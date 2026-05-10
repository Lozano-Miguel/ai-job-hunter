from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
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
SOURCES = ["indeed", "linkedin"]  # Toggle sources here

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
    load_dotenv()
    os.getenv("ITJOBS_API_KEY")  # loaded from .env for scrape_itjobs
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


def create_netempregos_session() -> cffi_requests.Session:
    session = cffi_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
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
    return cffi_requests.Session(impersonate="chrome124")


def prime_session(session: cffi_requests.Session) -> None:
    session.get(DOMAIN, timeout=30)
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
        job_key = f"ne_{parts[0]}" if parts and parts[0].isdigit() else f"ne_{abs(hash(url))}"

        # Company, location, salary — scraped from metadata text blocks
        meta_items = [
            t.strip()
            for t in card.get_text(separator="|").split("|")
            if t.strip() and len(t.strip()) > 2
        ]
        company = meta_items[1] if len(meta_items) > 1 else "N/A"
        location = meta_items[2] if len(meta_items) > 2 else "N/A"
        salary = next((m for m in meta_items if "€" in m or "eur" in m.lower()), "N/A")

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
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return jobs


def scrape_indeed(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page_index in range(MAX_PAGES):
        start = page_index * 10
        url = build_search_url(keyword, start)

        resp = session.get(url, timeout=30)
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
        r = session.get(url, timeout=30)
        if not r.text.strip():
            print(f"[WARN] LinkedIn returned empty body for '{keyword}' page {page_index + 1}")
            print(f"       Status: {r.status_code} | URL: {url}")
            break
        if r.status_code == 429:
            print(f"[WARN] LinkedIn rate limited on '{keyword}', stopping.")
            break
        if r.status_code != 200:
            print(f"[WARN] LinkedIn page {page_index + 1} for '{keyword}' returned {r.status_code}")
            break
        page_jobs = parse_jobs_from_linkedin_html(r.text, keyword)
        if not page_jobs:
            break
        jobs.extend(page_jobs)
        time.sleep(random.uniform(3, 7))
    return jobs


def scrape_itjobs(keyword: str) -> list[dict[str, Any]]:
    load_dotenv()
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
        r = std_requests.post(
            "https://api.itjobs.pt/job/search.json",
            data=params,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[WARN] ITJobs API error {r.status_code} for '{keyword}'")
            break

        data = r.json()
        results = data.get("results", [])
        if not results:
            break

        # Filter by date on our side since the API has no date param
        cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_FILTER)
        for job in results:
            published_str = job.get("publishedAt", "")
            try:
                published = datetime.strptime(published_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if published < cutoff:
                continue

            job_id = job.get("id")
            slug = job.get("slug", "")
            salary = "N/A"
            if job.get("salaryMin") and job.get("salaryMax"):
                salary = f"{job['salaryMin']}–{job['salaryMax']} €/yr"
            elif job.get("salaryMin"):
                salary = f"From {job['salaryMin']} €/yr"

            locations = job.get("locations", [])
            location_str = ", ".join(l["name"] for l in locations) if locations else "N/A"

            jobs.append(
                {
                    "title": job.get("title", "N/A").strip(),
                    "company": job.get("company", {}).get("name", "N/A"),
                    "location": location_str,
                    "salary": salary,
                    "url": f"https://www.itjobs.pt/oferta/{job_id}/{slug}",
                    "job_key": f"itjobs_{job_id}",
                    "keyword": keyword,
                    "source": "itjobs",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        time.sleep(random.uniform(1, 2))  # API is friendly but be polite

    return jobs


def scrape_netempregos(keyword: str, session: cffi_requests.Session) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    city = LOCATION.split(",")[0].strip()  # Extract "Lisbon" from "Lisbon, Portugal"

    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://www.net-empregos.com/pesquisa-empregos.asp"
            f"?chaves={quote_plus(keyword)}&cidade={quote_plus(city)}&page={page}"
        )
        r = session.get(url, timeout=30)

        # Redirect to login means bot was detected
        if "loginc.asp" in r.url or r.status_code != 200:
            print(f"[WARN] Net-Empregos blocked page {page} for '{keyword}' (status {r.status_code})")
            break

        page_jobs = parse_jobs_from_netempregos_html(r.text, keyword)
        if not page_jobs:
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
        job_key = f"sapo_{abs(hash(link))}"

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
        r = session.get(url, timeout=30)
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


def deduplicate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        key = str(job.get("job_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


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
        new_job["applied"] = False
        new_job["status"] = "not_applied"
        new_job["notes"] = ""
        merged_jobs.append(new_job)
        existing_by_key[job_key] = new_job
        new_count += 1

    out_path.write_text(
        json.dumps(merged_jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(out_path), new_count, skipped_count, len(merged_jobs)


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

    # 0) Sources
    print("Sources to scrape:")
    print("  [1] All sources")
    print("  [2] Indeed only")
    print("  [3] LinkedIn only")
    print("  [4] ITJobs only")
    print("  [5] Net-Empregos only")
    print("  [6] Sapo only")
    print("  [7] Indeed + ITJobs")
    print("  [8] LinkedIn + ITJobs")
    print("  [9] ITJobs + Net-Empregos + Sapo")
    source_choice = input("Choice [1]: ").strip()
    sources_map: dict[str, list[str]] = {
        "1": ["indeed", "linkedin", "itjobs", "netempregos", "sapo"],
        "2": ["indeed"],
        "3": ["linkedin"],
        "4": ["itjobs"],
        "5": ["netempregos"],
        "6": ["sapo"],
        "7": ["indeed", "itjobs"],
        "8": ["linkedin", "itjobs"],
        "9": ["itjobs", "netempregos", "sapo"],
    }
    sources = sources_map.get(source_choice, ["indeed", "linkedin", "itjobs", "netempregos", "sapo"])

    # 1) City / Location
    loc = input(f"Enter target city/location [{LOCATION}]: ").strip()
    if loc:
        LOCATION = loc

    # 2) Date filter
    print("Date filter:")
    print("  [1] Last 24 hours")
    print("  [2] Last 3 days")
    print("  [3] Last 7 days")
    print("  [4] Last 14 days")
    date_choice = input("Choice [1]: ").strip()
    date_map: dict[str, int] = {"1": 1, "2": 3, "3": 7, "4": 14}
    DATE_FILTER = date_map.get(date_choice, 1)

    # 3) Search radius
    print("Search radius:")
    print("  [1]  5 miles")
    print("  [2] 10 miles")
    print("  [3] 15 miles")
    print("  [4] 25 miles")
    print("  [5] 35 miles")
    radius_choice = input("Choice [4]: ").strip()
    radius_map: dict[str, int] = {"1": 5, "2": 10, "3": 15, "4": 25, "5": 35}
    RADIUS = radius_map.get(radius_choice, 25)

    # 4) Max pages per keyword
    raw_pages = input(f"Max pages per keyword (10 jobs/page) [{MAX_PAGES}]: ").strip()
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
        from dashboard_server import main as run_server

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(1)
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

    sources = _interactive_setup()

    cv_text = extract_cv_text(CV_PATH)
    if not cv_text.strip():
        print("[ERROR] Could not extract any text from cv.pdf. Is it scanned? Try an OCR'd PDF.")
        return 1

    # 5) Additional context for Groq (after CV is extracted, before Groq call)
    print("Your CV has been read. Anything to add before the AI generates job titles?")
    additional = input(
        '(e.g. "I prefer remote roles", "I\'m a recent grad", or press Enter to skip): '
    ).strip()
    if additional:
        cv_text = cv_text + "\n\n--- ADDITIONAL CONTEXT ---\n" + additional

    print("─────────────────────────────────")
    print(" Search Configuration")
    print("─────────────────────────────────")
    print(f" Location   : {LOCATION}")
    print(f" Date filter: {_date_filter_label()}")
    print(f" Radius     : {RADIUS} miles")
    print(f" Max pages  : {MAX_PAGES}")
    _source_names = {
        "indeed": "Indeed",
        "linkedin": "LinkedIn",
        "itjobs": "ITJobs",
        "netempregos": "Net-Empregos",
        "sapo": "Sapo",
    }
    sources_label = ", ".join(_source_names.get(s, s) for s in sources)
    print(f" Sources     : {sources_label}")
    print("─────────────────────────────────")

    if DEBUG_KEYWORD_OVERRIDE:
        titles = [DEBUG_KEYWORD_OVERRIDE]
    elif "--no-groq" in sys.argv:
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
        print(f"[Groq] Skipped — reusing {len(titles)} keywords from last run:")
        for i, t in enumerate(titles, start=1):
            print(f"  [{i}] {t}")
    else:
        titles = generate_job_titles(cv_text)
        print(f"[Groq] Generated {len(titles)} job titles:")
        for i, t in enumerate(titles, start=1):
            print(f"  [{i}] {t}")

    all_jobs: list[dict[str, Any]] = []
    indeed_session = create_session() if "indeed" in sources else None
    linkedin_session = create_linkedin_session() if "linkedin" in sources else None
    netempregos_session = create_netempregos_session() if "netempregos" in sources else None
    sapo_session = create_sapo_session() if "sapo" in sources else None

    if indeed_session:
        prime_session(indeed_session)

    for title in titles:
        if "indeed" in sources:
            print(f"[Indeed] Scraping: '{title}'")
            all_jobs.extend(scrape_indeed(title, indeed_session))
        if "linkedin" in sources:
            print(f"[LinkedIn] Scraping: '{title}'")
            all_jobs.extend(scrape_linkedin(title, linkedin_session))
        if "itjobs" in sources:
            print(f"[ITJobs] Scraping: '{title}'")
            all_jobs.extend(scrape_itjobs(title))
        if "netempregos" in sources:
            print(f"[Net-Empregos] Scraping: '{title}'")
            all_jobs.extend(scrape_netempregos(title, netempregos_session))
        if "sapo" in sources:
            print(f"[Sapo] Scraping: '{title}'")
            all_jobs.extend(scrape_sapo(title, sapo_session))

    unique_jobs = deduplicate(all_jobs)
    output_path, new_count, skipped_count, total_count = save_json(unique_jobs)

    print("══════════════════════════════════════")
    print(" Job Hunter Complete")
    print("══════════════════════════════════════")
    print(f" Keywords searched : {len(titles)}")
    print(f" New jobs added  : {new_count}")
    print(f" Already tracked : {skipped_count}")
    print(f" Total in file   : {total_count}")
    print(f" Date filter       : {_date_filter_label()}")
    print(f" Location          : {LOCATION}")
    print(f" Sources           : {sources_label}")
    print(f" Output            : {output_path}")
    print("══════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
