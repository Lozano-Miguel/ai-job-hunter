from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import pdfplumber
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv
from groq import Groq

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
OUTPUT_FILE = "output/job_leads.json"

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
        f"&fromage={DATE_FILTER}&start={start}"
    )


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


def main() -> int:
    if not os.path.exists(CV_PATH):
        print("[ERROR] cv.pdf not found. Please place your CV in the project directory.")
        return 1

    if os.path.getsize(CV_PATH) == 0:
        print("[ERROR] cv.pdf is empty. Please replace it with your CV.")
        return 1

    cv_text = extract_cv_text(CV_PATH)
    if not cv_text.strip():
        print("[ERROR] Could not extract any text from cv.pdf. Is it scanned? Try an OCR'd PDF.")
        return 1

    titles = generate_job_titles(cv_text)
    print(f"[Groq] Generated {len(titles)} job titles:")
    for i, t in enumerate(titles, start=1):
        print(f"  [{i}] {t}")

    if DEBUG_KEYWORD_OVERRIDE:
        titles = [DEBUG_KEYWORD_OVERRIDE]

    all_jobs: list[dict[str, Any]] = []
    session = create_session()
    prime_session(session)

    for title in titles:
        print(f"[INFO] Scraping Indeed for '{title}'...")
        keyword_jobs = scrape_indeed(title, session)
        print(f"[INFO] Found {len(keyword_jobs)} jobs for '{title}'.")
        for job in keyword_jobs[:3]:
            print(json.dumps(job, ensure_ascii=False, indent=2))
        all_jobs.extend(keyword_jobs)

    print(f"[RESULT] Scraped {len(all_jobs)} total jobs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
