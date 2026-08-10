from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from functools import wraps
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar
from urllib.parse import quote_plus

import pdfplumber
import requests as std_requests
from bs4 import BeautifulSoup
from pyquery import PyQuery as pq
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv
from groq import Groq

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
RADIUS = 25
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
ITJOBS_LOCATION_ID = 14  # 14 = Lisboa. See itjobs.pt/api for other IDs
ITJOBS_LIMIT = 10  # Results per page
OUTPUT_FILE = "output/job_leads.json"
# Used when the source menu choice is blank or not in 1–9 (see _interactive_setup).
SOURCES = ["indeed", "linkedin", "itjobs", "netempregos", "sapo", "landingjobs", "techjobs", "expresso"]

# Use this to test one keyword in Step 3 without changing the rest of the flow.
DEBUG_KEYWORD_OVERRIDE: str | None = None
# ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an expert career counselor with 20 years of experience.\n"
    "Analyze the CV below and return a JSON object with a single key\n"
    '"job_titles" containing an array of 5 to 7 strings. Each string\n'
    "must be a realistic, searchable job title a recruiter would post\n"
    "on Indeed. Return only valid JSON — no explanation, no markdown."
)

MAX_GET_ATTEMPTS = 3
GET_RETRY_DELAYS_SECONDS = (2, 4, 8)
P = ParamSpec("P")
R = TypeVar("R")


def retry_http_get(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        last_error: Exception | None = None
        for attempt in range(1, MAX_GET_ATTEMPTS + 1):
            try:
                response = func(*args, **kwargs)
                status_code = getattr(response, "status_code", None)
                if status_code is not None and 500 <= status_code < 600:
                    if attempt >= MAX_GET_ATTEMPTS:
                        return response
                    delay = GET_RETRY_DELAYS_SECONDS[min(attempt - 1, len(GET_RETRY_DELAYS_SECONDS) - 1)]
                    print(
                        f"[WARN] HTTP {status_code} on GET (attempt {attempt}/{MAX_GET_ATTEMPTS}). "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                return response
            except (std_requests.Timeout, std_requests.ConnectionError) as err:
                last_error = err
                if attempt >= MAX_GET_ATTEMPTS:
                    raise
                delay = GET_RETRY_DELAYS_SECONDS[min(attempt - 1, len(GET_RETRY_DELAYS_SECONDS) - 1)]
                print(
                    f"[WARN] GET failed with {type(err).__name__} "
                    f"(attempt {attempt}/{MAX_GET_ATTEMPTS}). Retrying in {delay}s..."
                )
                time.sleep(delay)
        if last_error:
            raise last_error
        return func(*args, **kwargs)

    return wrapper


@retry_http_get
def session_get_with_retry(session: cffi_requests.Session, url: str, **kwargs: Any) -> Any:
    return session.get(url, **kwargs)


@retry_http_get
def _itjobs_post_with_retry(params: dict[str, Any]) -> Any:
    return std_requests.post(
        "https://api.itjobs.pt/job/search.json",
        data=params,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )


def extract_cv_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        parts: list[str] = []
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _validate_job_titles(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("job_titles is not a list")

    titles: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("job_titles contains a non-string item")
        s = item.strip()
        if not s:
            continue
        titles.append(s)

    if not (5 <= len(titles) <= 7):
        raise ValueError(f"expected 5–7 job titles, got {len(titles)}")

    return titles


def generate_job_titles(cv_text: str) -> list[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[ERROR] GROQ_API_KEY missing. Add it to your .env file as GROQ_API_KEY=...")
        raise SystemExit(1)

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": cv_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = (response.choices[0].message.content or "").strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        print("[ERROR] Groq returned non-JSON output. Raw content:")
        print(content)
        raise SystemExit(1) from e

    try:
        titles = _validate_job_titles(payload.get("job_titles"))
    except Exception as e:
        print("[ERROR] Groq returned unexpected JSON shape. Raw JSON:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(1) from e

    return titles


def _random_chrome_user_agent() -> str:
    major = random.randint(122, 126)
    build = random.randint(0, 9999)
    patch = random.randint(0, 200)
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36"
    )


def build_search_url(keyword: str, start: int) -> str:
    encoded_keyword = quote_plus(keyword)
    encoded_location = quote_plus(LOCATION)
    return (
        f"{DOMAIN}/jobs?q={encoded_keyword}&l={encoded_location}"
        f"&fromage={DATE_FILTER}&radius={RADIUS}&start={start}"
    )


def create_linkedin_session() -> cffi_requests.Session:
    session = cffi_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "referer": "https://www.linkedin.com/jobs/search",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        }
    )
    return session


def _chrome_124_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


def _stable_key_from_url(url: str, prefix: str) -> str:
    """Stable dedup key: Python's hash() is salted per process (not suitable for job_key)."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def create_netempregos_session() -> cffi_requests.Session:
    session = cffi_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
            "User-Agent": _chrome_124_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.net-empregos.com/",
            "Connection": "keep-alive",
        }
    )
    return session


def create_sapo_session() -> cffi_requests.Session:
    session = cffi_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
            "User-Agent": _chrome_124_user_agent(),
            "Referer": "https://emprego.sapo.pt/",
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8",
        }
    )
    return session


DATE_FILTER_TO_LINKEDIN = {1: "r86400", 3: "r259200", 7: "r604800", 14: "r1209600"}

DATE_FILTER_TO_SAPO = {
    1: "ultimas-24-horas",
    3: "ultimas-24-horas",  # Sapo has no 3-day option, fallback to 24h
    7: "ultima-semana",
    14: "ultima-semana",  # Sapo has no 14-day option, fallback to week
}


def build_linkedin_url(keyword: str, start: int) -> str:
    encoded_keyword = quote_plus(keyword)
    encoded_location = quote_plus(LOCATION)
    f_tpr = DATE_FILTER_TO_LINKEDIN.get(DATE_FILTER, "r604800")
    return (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={encoded_keyword}&location={encoded_location}&start={start}&f_TPR={f_tpr}"
    )


def parse_jobs_from_linkedin_html(html: str, keyword: str) -> list[dict[str, Any]]:
    if not html or not html.strip():
        return []
    try:
        doc = pq(html)
    except Exception:
        return []
    jobs: list[dict[str, Any]] = []
    for item in doc("li").items():
        title = item.find(".base-search-card__title").text().strip()
        company = item.find(".base-search-card__subtitle").text().strip()
        location = item.find(".job-search-card__location").text().strip()
        link = item.find(".base-card__full-link").attr("href")
        if not title:
            continue
        clean_link = link.split("?")[0] if link else "N/A"
        job_key = clean_link.rstrip("/").split("-")[-1] if clean_link != "N/A" else None
        if not job_key:
            continue
        posted_at = item.find("time").attr("datetime") or ""
        jobs.append(
            {
                "title": title,
                "company": company or "N/A",
                "location": location or "N/A",
                "salary": "N/A",
                "url": clean_link,
                "job_key": f"li_{job_key}",
                "keyword": keyword,
                "source": "linkedin",
                "posted_at": posted_at,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return jobs


def parse_jobs_from_html(html: str, keyword: str) -> list[dict[str, Any]]:
    pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\})\s*;'
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        return []

    try:
        blob = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    results = (
        blob.get("metaData", {})
        .get("mosaicProviderJobCardsModel", {})
        .get("results", [])
    )

    jobs: list[dict[str, Any]] = []
    for job in results:
        job_key = job.get("jobkey")
        if not job_key:
            continue

        salary = "N/A"
        salary_snippet = job.get("salarySnippet") or {}
        if isinstance(salary_snippet, dict) and salary_snippet.get("text"):
            salary = str(salary_snippet["text"])

        jobs.append(
            {
                "title": job.get("displayTitle") or job.get("title") or "N/A",
                "company": job.get("company") or "N/A",
                "location": job.get("formattedLocation") or "N/A",
                "salary": salary,
                "url": f"{DOMAIN}/viewjob?jk={job_key}",
                "job_key": job_key,
                "keyword": keyword,
                "source": "indeed",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


def create_session() -> cffi_requests.Session:
    return cffi_requests.Session(impersonate="safari15_5")


def prime_session(session: cffi_requests.Session) -> None:
    session_get_with_retry(session, DOMAIN, timeout=30)
    time.sleep(random.uniform(2, 4))


def prime_linkedin_session(session: cffi_requests.Session) -> None:
    session_get_with_retry(session, "https://www.linkedin.com", timeout=30)
    time.sleep(random.uniform(2, 4))


def parse_jobs_from_netempregos_html(html: str, keyword: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    job_cards = soup.select(".job-item") or soup.select(".oferta")
    jobs: list[dict[str, Any]] = []

    for card in job_cards:
        # Title and URL
        title_elem = card.select_one("h2 a") or card.select_one("a")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        href = title_elem.get("href", "")
        url = f"https://www.net-empregos.com{href}" if href.startswith("/") else href

        # Job key from URL path — pattern is /123456/slug/
        parts = [p for p in href.split("/") if p]
        job_key = (
            f"ne_{parts[0]}"
            if parts and parts[0].isdigit()
            else _stable_key_from_url(url, "ne")
        )

        # Prefer explicit fields; avoid pipe-splitting the whole card (order is unreliable).
        company = "N/A"
        location = "N/A"
        salary = "N/A"
        for sel in (
            ".empresa",
            ".nome_empresa",
            ".company",
            "span.empresa",
            "[class*='empresa']",
        ):
            el = card.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if len(t) > 1:
                    company = t
                    break
        for sel in (".localidade", ".local", ".zona", "[class*='local']"):
            el = card.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if len(t) > 1:
                    location = t
                    break
        meta_items: list[str] = []
        if company == "N/A" or location == "N/A":
            for s in card.stripped_strings:
                piece = s.strip()
                if len(piece) <= 2 or piece == title:
                    continue
                if piece not in meta_items:
                    meta_items.append(piece)
            if company == "N/A" and len(meta_items) > 1:
                company = meta_items[1]
            if location == "N/A" and len(meta_items) > 2:
                location = meta_items[2]
        salary_pattern = re.compile(r"\d[\d.,]*\s*(€|eur\b)|€\s*\d", re.I)
        salary_gen = (m for m in card.stripped_strings if salary_pattern.search(m))
        salary = next(salary_gen, "N/A")

        posted_at = ""
        cal_icon = card.select_one(".flaticon-calendar")
        if cal_icon and cal_icon.parent:
            raw_date = cal_icon.parent.get_text(strip=True)
            date_match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", raw_date)
            if date_match:
                posted_at = f"{date_match.group(3)}-{date_match.group(2).zfill(2)}-{date_match.group(1).zfill(2)}"

        jobs.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "url": url,
                "job_key": job_key,
                "keyword": keyword,
                "source": "netempregos",
                "posted_at": posted_at,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


def scrape_indeed(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page_index in range(MAX_PAGES):
        start = page_index * 10
        url = build_search_url(keyword, start)

        resp = session_get_with_retry(session, url, timeout=30)
        if resp.status_code != 200:
            print(
                f"[ERROR] Indeed returned HTTP {resp.status_code} for keyword={keyword!r}, page={page_index + 1}"
            )
            break

        page_jobs = parse_jobs_from_html(resp.text, keyword)
        if not page_jobs:
            break

        jobs.extend(page_jobs)
        time.sleep(random.uniform(3, 7))

    return jobs


def scrape_linkedin(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page_index in range(MAX_PAGES):
        start = page_index * 25
        url = build_linkedin_url(keyword, start)
        r = session_get_with_retry(session, url, timeout=30)
        if r.status_code == 429:
            print(f"[WARN] LinkedIn rate limited on '{keyword}', stopping.")
            break
        if r.status_code != 200:
            print(f"[WARN] LinkedIn page {page_index + 1} for '{keyword}' returned {r.status_code}")
            break
        if not r.text.strip():
            print(f"[WARN] LinkedIn returned empty body for '{keyword}' page {page_index + 1}")
            print(f"       Status: {r.status_code} | URL: {url}")
            break
        page_jobs = parse_jobs_from_linkedin_html(r.text, keyword)
        if not page_jobs:
            break
        jobs.extend(page_jobs)
        time.sleep(random.uniform(3, 7))
    return jobs


def scrape_itjobs(keyword: str) -> list[dict[str, Any]]:
    api_key = os.getenv("ITJOBS_API_KEY")
    if not api_key:
        print("[ERROR] ITJOBS_API_KEY missing from .env")
        return []

    jobs: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "api_key": api_key,
            "q": keyword,
            "limit": ITJOBS_LIMIT,
            "page": page,
            "location": ITJOBS_LOCATION_ID,
        }
        r = _itjobs_post_with_retry(params)
        if r.status_code != 200:
            print(f"[WARN] ITJobs API error {r.status_code} for '{keyword}'")
            break

        try:
            data = r.json()
        except ValueError:
            print(f"[WARN] ITJobs returned invalid JSON for '{keyword}'")
            break

        results = data.get("results", [])
        if not results:
            break

        for job in results:
            published_str = job.get("publishedAt", "")
            job_id = job.get("id")
            slug = job.get("slug", "")
            salary = "N/A"
            if job.get("salaryMin") and job.get("salaryMax"):
                salary = f"{job['salaryMin']}–{job['salaryMax']} €/yr"
            elif job.get("salaryMin"):
                salary = f"From {job['salaryMin']} €/yr"

            locations = job.get("locations", [])
            location_str = (
                ", ".join(l.get("name", "") for l in locations if isinstance(l, dict))
                if locations
                else "N/A"
            )

            jobs.append(
                {
                    "title": (job.get("title") or "N/A").strip(),
                    "company": (job.get("company") or {}).get("name") or "N/A",
                    "location": location_str or "N/A",
                    "salary": salary,
                    "url": f"https://www.itjobs.pt/oferta/{job_id}/{slug}",
                    "job_key": f"itjobs_{job_id}",
                    "keyword": keyword,
                    "source": "itjobs",
                    "posted_at": published_str,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        time.sleep(random.uniform(1, 2))  # API is friendly but be polite

    return jobs


EN_TO_PT_CITY = {
    "lisbon": "Lisboa",
    "oporto": "Porto",
    "porto": "Porto",
    "leiria": "Leiria",
    "braga": "Braga",
    "coimbra": "Coimbra",
    "faro": "Faro",
    "setubal": "Setúbal",
    "aveiro": "Aveiro",
}


def scrape_netempregos(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    raw_city = LOCATION.split(",")[0].strip()  # Extract "Lisbon" from "Lisbon, Portugal"
    city = EN_TO_PT_CITY.get(raw_city.lower(), raw_city)

    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://www.net-empregos.com/pesquisa-empregos.asp"
            f"?chaves={quote_plus(keyword)}&cidade={quote_plus(city)}&page={page}"
        )
        r = session_get_with_retry(session, url, timeout=30)

        # curl_cffi sets r.url to EFFECTIVE_URL (final URL after redirects). Also check body
        # when the site returns 200 with an HTML login wall.
        combined_urls = " ".join(
            u
            for u in (
                getattr(r, "url", None),
                getattr(r, "redirect_url", None),
                getattr(getattr(r, "request", None), "url", None),
            )
            if u
        ).lower()
        snippet = (r.text or "")[:6000].lower()
        login_wall = (
            "login de candidato" in snippet
            or "/loginc.asp" in combined_urls
            or "loginc.asp" in combined_urls
        )
        if r.status_code != 200:
            print(f"[WARN] Net-Empregos blocked page {page} for '{keyword}' (status {r.status_code})")
            break

        page_jobs = parse_jobs_from_netempregos_html(r.text, keyword)
        if not page_jobs:
            if login_wall:
                print(f"[WARN] Net-Empregos login wall detected on page {page} for '{keyword}'")
            break

        jobs.extend(page_jobs)
        time.sleep(random.uniform(3, 6))

    return jobs


def parse_jobs_from_sapo_html(html: str, keyword: str) -> tuple[list[dict[str, Any]], int]:
    """Returns (jobs, total_pages)"""
    soup = BeautifulSoup(html, "html.parser")
    component = soup.find("search-results-component")

    if not component or not component.has_attr(":offers"):
        return [], 0

    try:
        jobs_raw = json.loads(component[":offers"])
    except (json.JSONDecodeError, KeyError):
        return [], 0

    # Get total pages from pagination attribute
    total_pages = 1
    if component.has_attr(":pagination"):
        try:
            pagination = json.loads(component[":pagination"])
            total = int(pagination.get("offers_total", 0))
            size = int(pagination.get("size", 9)) or 9
            total_pages = -(-total // size)  # ceiling division
        except (json.JSONDecodeError, ValueError):
            pass

    jobs: list[dict[str, Any]] = []
    for job in jobs_raw:
        link = job.get("link") or job.get("url") or "N/A"
        job_key = (
            _stable_key_from_url(link, "sapo") if link != "N/A" else "sapo_unknown"
        )

        salary = "N/A"
        sal_min = job.get("salary_min")
        sal_max = job.get("salary_max")
        if sal_min and sal_max:
            salary = f"{sal_min}–{sal_max} €"
        elif sal_min:
            salary = f"From {sal_min} €"

        jobs.append(
            {
                "title": job.get("offer_name") or "N/A",
                "company": job.get("company_name") or "N/A",
                "location": job.get("location") or "N/A",
                "salary": salary,
                "remote": job.get("remote_work", False),
                "url": link,
                "job_key": job_key,
                "keyword": keyword,
                "source": "sapo",
                "posted_at": job.get("publication_date") or "",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs, total_pages


def scrape_sapo(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    date_param = DATE_FILTER_TO_SAPO.get(DATE_FILTER, "ultima-semana")

    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://emprego.sapo.pt/offers"
            f"?pesquisa={quote_plus(keyword)}"
            f"&data-de-publicacao={date_param}"
            f"&pagina={page}&ordem=relevancia"
        )
        r = session_get_with_retry(session, url, timeout=30)
        if r.status_code != 200:
            print(f"[WARN] Sapo page {page} for '{keyword}' returned {r.status_code}")
            break

        page_jobs, total_pages = parse_jobs_from_sapo_html(r.text, keyword)
        if not page_jobs:
            break

        jobs.extend(page_jobs)

        # Stop early if we've reached the last real page
        if page >= total_pages:
            break

        time.sleep(random.uniform(2, 5))

    return jobs


LANDING_JOBS_API = "https://landing.jobs/api/v1/jobs"
LANDING_JOBS_LIMIT = 50


def scrape_landingjobs(keyword: str) -> list[dict[str, Any]]:
    keyword_lower = keyword.lower()
    keyword_words = keyword_lower.split()
    jobs: list[dict[str, Any]] = []
    offset = 0

    while True:
        r = std_requests.get(
            LANDING_JOBS_API,
            params={"limit": LANDING_JOBS_LIMIT, "offset": offset},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[WARN] Landing.jobs API returned {r.status_code}")
            break

        try:
            data = r.json()
        except ValueError:
            print("[WARN] Landing.jobs returned invalid JSON")
            break

        if not isinstance(data, list) or not data:
            break

        for job in data:
            title = (job.get("title") or "N/A").strip()
            tags = job.get("tags") or []
            haystack = f"{title} {' '.join(str(t) for t in tags)}".lower()
            if not all(w in haystack for w in keyword_words):
                continue

            locations = job.get("locations") or []
            location_str = (
                ", ".join(l.get("city", "") for l in locations if isinstance(l, dict))
                if locations
                else "N/A"
            )

            salary = "N/A"
            sal_low = job.get("gross_salary_low")
            sal_high = job.get("gross_salary_high")
            currency = job.get("currency_code", "EUR")
            if sal_low and sal_high:
                salary = f"{sal_low}–{sal_high} {currency}"
            elif sal_low:
                salary = f"From {sal_low} {currency}"

            job_id = job.get("id")
            job_url = job.get("url") or ""
            company = "N/A"
            url_parts = job_url.split("/at/")
            if len(url_parts) > 1:
                slug = url_parts[1].split("/")[0]
                company = slug.replace("-", " ").title()
            jobs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location_str or "N/A",
                    "salary": salary,
                    "url": job_url or f"https://landing.jobs/at/job/{job_id}",
                    "job_key": f"landing_{job_id}",
                    "keyword": keyword,
                    "source": "landingjobs",
                    "posted_at": job.get("published_at", ""),
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        if len(data) < LANDING_JOBS_LIMIT:
            break
        offset += LANDING_JOBS_LIMIT
        time.sleep(random.uniform(1, 2))

    return jobs


TECHJOBS_API = "https://api.techjobs.pt/api/jobs"
TECHJOBS_LIMIT = 50


def _techjobs_location() -> str:
    return LOCATION.split(",")[0].strip()


def scrape_techjobs(keyword: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    offset = 0

    while True:
        params: dict[str, Any] = {
            "q": keyword,
            "location": _techjobs_location(),
            "limit": TECHJOBS_LIMIT,
            "offset": offset,
        }
        r = std_requests.get(
            TECHJOBS_API,
            params=params,
            headers={
                "Accept": "application/json",
                "Referer": "https://techjobs.pt/",
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[WARN] TechJobs API returned {r.status_code}")
            break

        try:
            data = r.json()
        except ValueError:
            print("[WARN] TechJobs returned invalid JSON")
            break

        items = data.get("items", [])
        if not items:
            break

        for job in items:
            created_str = job.get("created_at", "")

            salary = "N/A"
            sal_min = job.get("salary_min")
            sal_max = job.get("salary_max")
            currency = job.get("salary_currency", "EUR")
            if sal_min and sal_max:
                salary = f"{sal_min}–{sal_max} {currency}"
            elif sal_min:
                salary = f"From {sal_min} {currency}"

            slug = job.get("slug", "")
            job_id = job.get("id", "")

            jobs.append(
                {
                    "title": (job.get("title") or "N/A").strip(),
                    "company": (job.get("company_name") or "N/A").strip(),
                    "location": job.get("location") or "N/A",
                    "salary": salary,
                    "url": f"https://techjobs.pt/jobs/{slug}" if slug else "N/A",
                    "job_key": f"techjobs_{job_id}",
                    "keyword": keyword,
                    "source": "techjobs",
                    "posted_at": created_str,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        if not data.get("has_more", False):
            break
        offset += TECHJOBS_LIMIT
        time.sleep(random.uniform(1, 2))

    return jobs


def create_expresso_session() -> cffi_requests.Session:
    session = cffi_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
            "User-Agent": _chrome_124_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8",
            "Referer": "https://expressoemprego.pt/",
        }
    )
    return session


def _expresso_location() -> str:
    raw_city = LOCATION.split(",")[0].strip()
    return EN_TO_PT_CITY.get(raw_city.lower(), raw_city).lower()


def parse_jobs_from_expresso_html(html: str, keyword: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.resultadosBox")
    jobs: list[dict[str, Any]] = []

    for card in cards:
        title_elem = card.select_one("h3 a")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        href = title_elem.get("href", "")
        url = f"https://expressoemprego.pt{href}" if href.startswith("/") else href

        company_elem = card.select_one("h4")
        company = company_elem.get_text(strip=True) if company_elem else "N/A"

        date_text = ""
        location = "N/A"
        date_span = card.select_one("span.px13.colorBlack")
        if date_span:
            raw = date_span.get_text(strip=True)
            parts = [p.strip() for p in raw.split("|")]
            if parts:
                date_text = parts[0]
            if len(parts) > 1 and parts[1]:
                location = parts[1]

        ref_id = href.rstrip("/").split("/")[-1] if href else ""
        job_key = f"expresso_{ref_id}" if ref_id.isdigit() else _stable_key_from_url(url, "expresso")

        posted_at = ""
        date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", date_text)
        if date_match:
            posted_at = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"

        jobs.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "salary": "N/A",
                "url": url,
                "job_key": job_key,
                "keyword": keyword,
                "source": "expresso",
                "posted_at": posted_at,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


def scrape_expresso(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    location = _expresso_location()
    slug_keyword = keyword.lower().replace(" ", "-")

    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://expressoemprego.pt/emprego/pesquisa/"
            f"{slug_keyword}/{location}?order=data&page={page}"
        )
        r = session_get_with_retry(session, url, timeout=30)
        if r.status_code != 200:
            print(f"[WARN] Expresso page {page} for '{keyword}' returned {r.status_code}")
            break

        page_jobs = parse_jobs_from_expresso_html(r.text, keyword)
        if not page_jobs:
            break

        jobs.extend(page_jobs)
        time.sleep(random.uniform(3, 6))

    return jobs


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), "ymd"),           # 2026-08-10, 2026-8-5
    (re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})"), "ymd"),          # 2026.08.10
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})"), "ymd"),            # 2026/08/10
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})"), "dmy"),            # 10-08-2026, 10-8-2026
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})"), "dmy"),          # 10.08.2026
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})"), "dmy"),            # 10/08/2026
]
SKIP_DATE_FILTER_SOURCES = {"landingjobs"}


def _parse_posted_date(raw: str) -> datetime | None:
    text = raw.strip().split("T")[0].split(" ")[0]
    if not text:
        return None
    for pattern, order in _DATE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if order == "ymd":
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def filter_by_date(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_FILTER)
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        source = str(job.get("source", ""))
        if source in SKIP_DATE_FILTER_SOURCES:
            filtered.append(job)
            continue
        posted_at = str(job.get("posted_at") or "")
        if not posted_at:
            filtered.append(job)
            continue
        posted_date = _parse_posted_date(posted_at)
        if posted_date is None or posted_date >= cutoff:
            filtered.append(job)
            continue
    return filtered


def deduplicate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _normalize_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"\s+", " ", text)

    def _is_present(value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.upper() != "N/A"

    def _completeness_score(job: dict[str, Any]) -> int:
        # Prefer entries that keep more useful searchable/tracking fields.
        fields = ("salary", "location", "url", "job_key", "remote")
        return sum(1 for field in fields if _is_present(job.get(field)))

    seen: set[str] = set()
    unique_by_key: list[dict[str, Any]] = []
    for job in jobs:
        key = str(job.get("job_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique_by_key.append(job)

    # Second pass: collapse equivalent "title + company" records, keeping the richest one.
    best_by_identity: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for job in unique_by_key:
        identity = f"{_normalize_text(job.get('title'))}::{_normalize_text(job.get('company'))}"
        current_best = best_by_identity.get(identity)
        if current_best is None:
            best_by_identity[identity] = job
            order.append(identity)
            continue

        current_score = _completeness_score(current_best)
        candidate_score = _completeness_score(job)
        if candidate_score > current_score:
            best_by_identity[identity] = job

    return [best_by_identity[i] for i in order]


def save_json(jobs: list[dict[str, Any]]) -> tuple[str, int, int, int]:
    out_path = Path(OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing_jobs: list[dict[str, Any]] = []
    existing_by_key: dict[str, dict[str, Any]] = {}
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing_jobs = [j for j in loaded if isinstance(j, dict)]
        except (json.JSONDecodeError, OSError):
            existing_jobs = []
        for job in existing_jobs:
            job_key = str(job.get("job_key") or "")
            if job_key:
                existing_by_key[job_key] = job

    merged_jobs = list(existing_jobs)
    new_count = 0
    skipped_count = 0
    # Status values: "not_applied" | "applied" | "interviewing" | "rejected"
    for job in jobs:
        job_key = str(job.get("job_key") or "")
        if not job_key:
            continue
        if job_key in existing_by_key:
            skipped_count += 1
            continue
        new_job = dict(job)
        new_job["status"] = "not_applied"
        new_job["notes"] = ""
        merged_jobs.append(new_job)
        existing_by_key[job_key] = new_job
        new_count += 1

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(merged_jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp_path, out_path)
    return str(out_path), new_count, skipped_count, len(merged_jobs)


BANNER = r"""
     ██╗ ██████╗ ██████╗       ██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗
     ██║██╔═══██╗██╔══██╗      ██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗
     ██║██║   ██║██████╔╝█████╗███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝
██   ██║██║   ██║██╔══██╗╚════╝██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗
╚█████╔╝╚██████╔╝██████╔╝      ██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║
 ╚════╝  ╚═════╝ ╚═════╝       ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝
"""


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _header(subtitle: str = "") -> None:
    _clear()
    print(BANNER)
    if subtitle:
        print(f"  {subtitle}")
        print()


def _date_filter_label() -> str:
    if int(DATE_FILTER) == 1:
        return "Last 24 hours"
    if int(DATE_FILTER) == 3:
        return "Last 3 days"
    if int(DATE_FILTER) == 7:
        return "Last 7 days"
    if int(DATE_FILTER) == 14:
        return "Last 14 days"
    return f"Last {DATE_FILTER} days"


def _interactive_setup() -> list[str]:
    global LOCATION, DATE_FILTER, RADIUS, MAX_PAGES

    # 0) Sources — multi-select toggle
    all_sources = [
        ("indeed", "Indeed"),
        ("linkedin", "LinkedIn"),
        ("itjobs", "ITJobs"),
        ("netempregos", "Net-Empregos"),
        ("sapo", "Sapo"),
        ("landingjobs", "Landing.jobs"),
        ("techjobs", "TechJobs"),
        ("expresso", "Expresso"),
    ]
    selected = set(key for key, _ in all_sources)

    def _print_sources() -> None:
        _header("Step 1/4 — Select Sources")
        print("  Toggle numbers to select/deselect, Enter to confirm:")
        print()
        for i, (key, label) in enumerate(all_sources, 1):
            mark = "x" if key in selected else " "
            print(f"    [{mark}] {i}. {label}")
        print()

    _print_sources()
    while True:
        choice = input("  > ").strip().lower()
        if not choice:
            break
        tokens = choice.replace(",", " ").split()
        changed = False
        for token in tokens:
            if not token.isdigit():
                continue
            idx = int(token) - 1
            if 0 <= idx < len(all_sources):
                key = all_sources[idx][0]
                if key in selected:
                    selected.discard(key)
                else:
                    selected.add(key)
                changed = True
        if changed:
            _print_sources()

    sources = [key for key, _ in all_sources if key in selected]
    if not sources:
        sources = [key for key, _ in all_sources]

    # 1) Location + Date + Radius + Pages
    _header("Step 2/4 — Location & Filters")
    loc = input(f"  Location [{LOCATION}]: ").strip()
    if loc:
        LOCATION = loc
    print()

    print("  Date filter:")
    print("    [1] Last 24 hours    [3] Last 7 days")
    print("    [2] Last 3 days      [4] Last 14 days")
    date_choice = input("  Choice [1]: ").strip()
    date_map: dict[str, int] = {"1": 1, "2": 3, "3": 7, "4": 14}
    DATE_FILTER = date_map.get(date_choice, 1)
    print()

    print("  Search radius:")
    print("    [1] 5 km    [3] 15 km    [5] 35 km")
    print("    [2] 10 km   [4] 25 km")
    radius_choice = input("  Choice [4]: ").strip()
    radius_map: dict[str, int] = {"1": 5, "2": 10, "3": 15, "4": 25, "5": 35}
    RADIUS = radius_map.get(radius_choice, 25)
    print()

    raw_pages = input(f"  Max pages per keyword [{MAX_PAGES}]: ").strip()
    if raw_pages:
        try:
            pages = int(raw_pages)
        except ValueError:
            pages = MAX_PAGES
        if 1 <= pages <= 10:
            MAX_PAGES = pages

    return sources


def main() -> int:
    if "--dashboard" in sys.argv:
        from dashboard_server import create_server, main as run_server

        try:
            server = create_server()
        except OSError as err:
            print(f"[ERROR] Could not start dashboard server on port 8000: {err}")
            return 1

        thread = threading.Thread(target=run_server, args=(server,), daemon=True)
        thread.start()
        webbrowser.open("http://localhost:8000/dashboard.html")
        print("[Dashboard] Open at http://localhost:8000/dashboard.html — press Ctrl+C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("\n[Dashboard] Closed.")
            sys.exit(0)

    if not os.path.exists(CV_PATH):
        print("[ERROR] cv.pdf not found. Please place your CV in the project directory.")
        return 1

    if os.path.getsize(CV_PATH) == 0:
        print("[ERROR] cv.pdf is empty. Please replace it with your CV.")
        return 1

    load_dotenv()

    sources = _interactive_setup()

    cv_text = extract_cv_text(CV_PATH)
    if not cv_text.strip():
        print("[ERROR] Could not extract any text from cv.pdf. Is it scanned? Try an OCR'd PDF.")
        return 1

    _source_names = {
        "indeed": "Indeed",
        "linkedin": "LinkedIn",
        "itjobs": "ITJobs",
        "netempregos": "Net-Empregos",
        "sapo": "Sapo",
        "landingjobs": "Landing.jobs",
        "techjobs": "TechJobs",
        "expresso": "Expresso",
    }
    sources_label = ", ".join(_source_names.get(s, s) for s in sources)

    if "--no-groq" not in sys.argv:
        _header("Step 3/4 — AI Keywords")
        print("  Your CV has been read. Anything to add before the AI generates job titles?")
        additional = input(
            '  (e.g. "I prefer remote roles", "I\'m a recent grad", or press Enter to skip): '
        ).strip()
        if additional:
            cv_text = cv_text + "\n\n--- ADDITIONAL CONTEXT ---\n" + additional

    if DEBUG_KEYWORD_OVERRIDE:
        titles = [DEBUG_KEYWORD_OVERRIDE]
    elif "--no-groq" in sys.argv:
        _header("Step 3/4 — Keywords (cached)")
        out_path = Path(OUTPUT_FILE)
        if not out_path.exists():
            print("[ERROR] No previous run found. Run without --no-groq first.")
            return 1
        try:
            previous_jobs = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("[ERROR] No previous run found. Run without --no-groq first.")
            return 1
        seen_keywords: set[str] = set()
        titles = []
        if isinstance(previous_jobs, list):
            for job in previous_jobs:
                if not isinstance(job, dict):
                    continue
                keyword = str(job.get("keyword") or "").strip()
                if keyword and keyword not in seen_keywords:
                    seen_keywords.add(keyword)
                    titles.append(keyword)
        if not titles:
            print("[ERROR] No previous run found. Run without --no-groq first.")
            return 1
        print(f"  Reusing {len(titles)} keywords from last run:")
        for i, t in enumerate(titles, start=1):
            print(f"    [{i}] {t}")
    else:
        titles = generate_job_titles(cv_text)
        print(f"  Generated {len(titles)} job titles:")
        for i, t in enumerate(titles, start=1):
            print(f"    [{i}] {t}")

    _header("Step 4/4 — Scraping")
    print("  ─────────────────────────────────────")
    print(f"   Location    : {LOCATION}")
    print(f"   Date filter : {_date_filter_label()}")
    print(f"   Radius      : {RADIUS} km")
    print(f"   Max pages   : {MAX_PAGES}")
    print(f"   Sources     : {sources_label}")
    print(f"   Keywords    : {len(titles)}")
    print("  ─────────────────────────────────────")
    print()

    all_jobs: list[dict[str, Any]] = []
    indeed_session = create_session() if "indeed" in sources else None
    linkedin_session = create_linkedin_session() if "linkedin" in sources else None
    netempregos_session = create_netempregos_session() if "netempregos" in sources else None
    sapo_session = create_sapo_session() if "sapo" in sources else None
    expresso_session = create_expresso_session() if "expresso" in sources else None

    if indeed_session:
        prime_session(indeed_session)
    if linkedin_session:
        prime_linkedin_session(linkedin_session)

    for title in titles:
        if "indeed" in sources:
            print(f"  ► [Indeed] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_indeed(title, indeed_session))
            except Exception as err:
                print(f"  ✗ [Indeed] failed for '{title}': {err}")
        if "linkedin" in sources:
            print(f"  ► [LinkedIn] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_linkedin(title, linkedin_session))
            except Exception as err:
                print(f"  ✗ [LinkedIn] failed for '{title}': {err}")
        if "itjobs" in sources:
            print(f"  ► [ITJobs] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_itjobs(title))
            except Exception as err:
                print(f"  ✗ [ITJobs] failed for '{title}': {err}")
        if "netempregos" in sources:
            print(f"  ► [Net-Empregos] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_netempregos(title, netempregos_session))
            except Exception as err:
                print(f"  ✗ [Net-Empregos] failed for '{title}': {err}")
        if "sapo" in sources:
            print(f"  ► [Sapo] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_sapo(title, sapo_session))
            except Exception as err:
                print(f"  ✗ [Sapo] failed for '{title}': {err}")
        if "landingjobs" in sources:
            print(f"  ► [Landing.jobs] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_landingjobs(title))
            except Exception as err:
                print(f"  ✗ [Landing.jobs] failed for '{title}': {err}")
        if "techjobs" in sources:
            print(f"  ► [TechJobs] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_techjobs(title))
            except Exception as err:
                print(f"  ✗ [TechJobs] failed for '{title}': {err}")
        if "expresso" in sources:
            print(f"  ► [Expresso] Scraping: '{title}'")
            try:
                all_jobs.extend(scrape_expresso(title, expresso_session))
            except Exception as err:
                print(f"  ✗ [Expresso] failed for '{title}': {err}")

        output_path, new_count, skipped_count, total_count = save_json(deduplicate(filter_by_date(all_jobs)))

    _header("Done!")
    print("  ═══════════════════════════════════════")
    print(f"   Keywords searched : {len(titles)}")
    print(f"   New jobs added    : {new_count}")
    print(f"   Already tracked   : {skipped_count}")
    print(f"   Total in file     : {total_count}")
    print("  ───────────────────────────────────────")
    print(f"   Location          : {LOCATION}")
    print(f"   Date filter       : {_date_filter_label()}")
    print(f"   Sources           : {sources_label}")
    print(f"   Output            : {output_path}")
    print("  ═══════════════════════════════════════")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
