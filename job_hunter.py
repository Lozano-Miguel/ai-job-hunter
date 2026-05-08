from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pdfplumber
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


DATE_FILTER_TO_LINKEDIN = {1: "r86400", 3: "r259200", 7: "r604800", 14: "r1209600"}


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
    print("  [1] Both Indeed + LinkedIn (default)")
    print("  [2] Indeed only")
    print("  [3] LinkedIn only")
    source_choice = input("Choice [1]: ").strip()
    sources_map: dict[str, list[str]] = {
        "1": ["indeed", "linkedin"],
        "2": ["indeed"],
        "3": ["linkedin"],
    }
    sources = sources_map.get(source_choice, ["indeed", "linkedin"])

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
    sources_label = ", ".join("Indeed" if s == "indeed" else "LinkedIn" for s in sources)
    print(f" Sources     : {sources_label}")
    print("─────────────────────────────────")

    if DEBUG_KEYWORD_OVERRIDE:
        titles = [DEBUG_KEYWORD_OVERRIDE]
    else:
        titles = generate_job_titles(cv_text)
        print(f"[Groq] Generated {len(titles)} job titles:")
        for i, t in enumerate(titles, start=1):
            print(f"  [{i}] {t}")

    all_jobs: list[dict[str, Any]] = []
    indeed_session = create_session() if "indeed" in sources else None
    linkedin_session = create_linkedin_session() if "linkedin" in sources else None

    if indeed_session:
        prime_session(indeed_session)

    for title in titles:
        if "indeed" in sources:
            print(f"[Indeed] Scraping: '{title}'")
            all_jobs.extend(scrape_indeed(title, indeed_session))
        if "linkedin" in sources:
            print(f"[LinkedIn] Scraping: '{title}'")
            all_jobs.extend(scrape_linkedin(title, linkedin_session))

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
    print(f" Output            : {output_path}")
    print("══════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
