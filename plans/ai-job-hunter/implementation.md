# AI Job Hunter Agent
## Goal

Implement a local Python CLI that extracts text from your `cv.pdf`, uses the Groq API to generate relevant job-title keywords, scrapes recent listings from `pt.indeed.com`, deduplicates results, and writes them to `output/job_leads.json`.

## Prerequisites

- [ ] **Be on the correct branch**: `feature/ai-job-hunter`
  - [ ] If the branch exists: `git switch feature/ai-job-hunter`
  - [ ] If the branch does not exist: `git switch -c feature/ai-job-hunter`
- [ ] **Python requirement**: Python **3.10+** (required by `curl-cffi`)
- [ ] **Create a virtualenv** (recommended):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

---

## Step-by-Step Instructions

## Step 1: Project Scaffold + CV Extraction

- [ ] Create `requirements.txt` with pinned dependencies. Copy and paste:

```txt
pdfplumber==0.11.4
groq==0.11.0
curl-cffi==0.7.4
python-dotenv==1.0.1
```

- [ ] Create `.env.example`. Copy and paste:

```bash
GROQ_API_KEY=your_key_here
```

- [ ] Create `.gitignore`. Copy and paste:

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd
.Python

# Virtual environments
.venv/
venv/
ENV/
env/

# Environment files / secrets
.env

# Outputs
output/

# OS / editor noise
.DS_Store
.idea/
.vscode/
```

- [ ] Create `job_hunter.py` with the config block and CV extraction logic. Copy and paste:

```python
from __future__ import annotations

import os
import sys

import pdfplumber

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
OUTPUT_FILE = "output/job_leads.json"
# ───────────────────────────────────────────────────────────


def extract_cv_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        parts: list[str] = []
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


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

    print(cv_text[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Install dependencies:

```bash
python -m pip install -r requirements.txt
```

- [ ] Place your CV at `cv.pdf` (in the repo root).
- [ ] Run the script:

```bash
python job_hunter.py
```

## Step 1 Verification Checklist

- [ ] `python job_hunter.py` prints the first ~500 characters of extracted CV text and exits.
- [ ] If `cv.pdf` is missing, the script prints exactly:
  - [ ] `[ERROR] cv.pdf not found. Please place your CV in the project directory.`
- [ ] No Python tracebacks during normal run.

## Step 1 STOP & COMMIT

STOP & COMMIT: Agent must stop here and wait for the user to test, stage, and commit the change.

---

## Step 2: AI Keyword Generation via Groq

- [ ] Create your local `.env` (DO NOT COMMIT) with your Groq key:
  - [ ] File: `.env`
  - [ ] Content: `GROQ_API_KEY=...`

- [ ] Update `job_hunter.py` to load env vars and generate job-title keywords via Groq (Structured Outputs with JSON Schema). Copy and paste the **entire file** below into `job_hunter.py`:

```python
from __future__ import annotations

import json
import os
import sys
from typing import Any

import pdfplumber
from dotenv import load_dotenv
from groq import Groq

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
OUTPUT_FILE = "output/job_leads.json"
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

    # Groq Structured Outputs (official docs): response_format type json_schema
    # We use a minimal schema and validate again in Python.
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": cv_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "job_titles",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {
                        "job_titles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 5,
                            "maxItems": 7,
                        }
                    },
                    "required": ["job_titles"],
                    "additionalProperties": False,
                },
            },
        },
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run:

```bash
python job_hunter.py
```

## Step 2 Verification Checklist

- [ ] If `GROQ_API_KEY` is missing, the script exits with:
  - [ ] `[ERROR] GROQ_API_KEY missing. Add it to your .env file as GROQ_API_KEY=...`
- [ ] With a valid key, output includes:
  - [ ] `[Groq] Generated N job titles:` where \(5 \le N \le 7\)
  - [ ] A numbered list of job titles
- [ ] No Python tracebacks during a normal run.

## Step 2 STOP & COMMIT

STOP & COMMIT: Agent must stop here and wait for the user to test, stage, and commit the change.

---

## Step 3: Indeed Scraper (curl_cffi + mosaic JSON extraction)

- [ ] Update `job_hunter.py` to add:
  - [ ] `scrape_indeed(keyword)`
  - [ ] `parse_jobs_from_html(html, keyword)`
  - [ ] Anti-ban: Chrome impersonation, randomized UA, sleep jitter
  - [ ] A debug override to test one keyword without changing code later

Copy and paste the **entire file** below into `job_hunter.py`:

```python
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
from curl_cffi import requests
from dotenv import load_dotenv
from groq import Groq

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
OUTPUT_FILE = "output/job_leads.json"

# Set this to a string (e.g. "Data Analyst") to test scraping a single keyword.
# Set to None to use Groq-generated keywords.
DEBUG_KEYWORD_OVERRIDE: str | None = None
# ───────────────────────────────────────────────────────────


SYSTEM_PROMPT = (
    "You are an expert career counselor with 20 years of experience.\n"
    "Analyze the CV below and return a JSON object with a single key\n"
    '"job_titles" containing an array of 5 to 7 strings. Each string\n'
    "must be a realistic, searchable job title a recruiter would post\n"
    "on Indeed. Return only valid JSON — no explanation, no markdown."
)


MOSAIC_REGEX = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\})\s*;',
    re.DOTALL,
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
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "job_titles",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {
                        "job_titles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 5,
                            "maxItems": 7,
                        }
                    },
                    "required": ["job_titles"],
                    "additionalProperties": False,
                },
            },
        },
        temperature=0.2,
    )

    content = (response.choices[0].message.content or "").strip()
    payload = json.loads(content)
    return _validate_job_titles(payload.get("job_titles"))


def _random_chrome_user_agent() -> str:
    # Keep the UA "Chrome-like" while varying versions slightly.
    major = random.randint(122, 126)
    build = random.randint(0, 9999)
    patch = random.randint(0, 200)
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36"
    )


def parse_jobs_from_html(html: str, keyword: str) -> list[dict[str, Any]]:
    match = MOSAIC_REGEX.search(html)
    if not match:
        return []

    blob = json.loads(match.group(1))
    results = blob["metaData"]["mosaicProviderJobCardsModel"]["results"]

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
                "title": str(job.get("displayTitle") or "").strip(),
                "company": str(job.get("company") or "").strip(),
                "location": str(job.get("formattedLocation") or "").strip(),
                "salary": salary,
                "url": f"{DOMAIN}/viewjob?jk={job_key}",
                "job_key": job_key,
                "keyword": keyword,
                "scraped_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
        )

    return jobs


def scrape_indeed(keyword: str) -> list[dict[str, Any]]:
    session = requests.Session()
    all_jobs: list[dict[str, Any]] = []

    for page in range(MAX_PAGES):
        offset = page * 10

        url = (
            f"{DOMAIN}/jobs"
            f"?q={quote_plus(keyword)}"
            f"&l={quote_plus(LOCATION)}"
            f"&fromage={int(DATE_FILTER)}"
            f"&start={offset}"
        )

        headers = {"User-Agent": _random_chrome_user_agent()}
        resp = session.get(url, headers=headers, impersonate="chrome124", timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Indeed returned HTTP {resp.status_code} for keyword={keyword!r}, page={page+1}")
            break

        jobs = parse_jobs_from_html(resp.text, keyword=keyword)
        if not jobs:
            # End pagination early if no results (or structure changed).
            break

        all_jobs.extend(jobs)

        # Anti-ban: sleep between page requests.
        time.sleep(random.uniform(3, 7))

    return all_jobs


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

    if DEBUG_KEYWORD_OVERRIDE:
        keywords = [DEBUG_KEYWORD_OVERRIDE]
    else:
        keywords = generate_job_titles(cv_text)
        print(f"[Groq] Generated {len(keywords)} job titles:")
        for i, t in enumerate(keywords, start=1):
            print(f"  [{i}] {t}")

    # Step 3 testing: scrape and print a few jobs to stdout.
    keyword = keywords[0]
    print(f"[Indeed] Scraping keyword: {keyword!r}")
    jobs = scrape_indeed(keyword)
    print(f"[Indeed] Found {len(jobs)} jobs for {keyword!r}")
    for job in jobs[:5]:
        print(json.dumps(job, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run a focused scrape test:
  - [ ] Edit `DEBUG_KEYWORD_OVERRIDE = "Data Analyst"` (keep everything else unchanged)
  - [ ] Run:

```bash
python job_hunter.py
```

## Step 3 Verification Checklist

- [ ] Output includes `[Indeed] Found N jobs` where \(N \ge 0\).
- [ ] The printed job JSON objects contain:
  - [ ] `url` values like `https://pt.indeed.com/viewjob?jk=...`
  - [ ] A non-empty `job_key`
- [ ] Open at least 2 printed `url`s in a browser and confirm they load valid postings.
- [ ] With `DATE_FILTER = 1`, results should look recent (e.g., “Today” / “Just posted” in Indeed UI).

## Step 3 STOP & COMMIT

STOP & COMMIT: Agent must stop here and wait for the user to test, stage, and commit the change.

---

## Step 4: Deduplication + JSON Output + Full `main()` Wiring

- [ ] Update `job_hunter.py` to:
  - [ ] Search all keywords (Groq-generated, unless `DEBUG_KEYWORD_OVERRIDE` is set)
  - [ ] Aggregate all jobs from all keywords
  - [ ] Deduplicate by `job_key` (keep the first occurrence)
  - [ ] Create `output/` if missing
  - [ ] Write pretty-printed JSON array to `output/job_leads.json`
  - [ ] Print final run summary banner

Copy and paste the **entire file** below into `job_hunter.py`:

```python
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pdfplumber
from curl_cffi import requests
from dotenv import load_dotenv
from groq import Groq

# ── USER CONFIG ────────────────────────────────────────────
CV_PATH = "cv.pdf"
LOCATION = "Lisbon, Portugal"  # Change freely
DOMAIN = "https://pt.indeed.com"
DATE_FILTER = 1  # 1 = last 24h | 7 = last week
MAX_PAGES = 3  # Pages per keyword (10 jobs/page)
OUTPUT_FILE = "output/job_leads.json"

# Optional debug override:
# - Set to "Data Analyst" to force a single keyword run
# - Set to None for normal Groq-generated keywords
DEBUG_KEYWORD_OVERRIDE: str | None = None
# ───────────────────────────────────────────────────────────


SYSTEM_PROMPT = (
    "You are an expert career counselor with 20 years of experience.\n"
    "Analyze the CV below and return a JSON object with a single key\n"
    '"job_titles" containing an array of 5 to 7 strings. Each string\n'
    "must be a realistic, searchable job title a recruiter would post\n"
    "on Indeed. Return only valid JSON — no explanation, no markdown."
)


MOSAIC_REGEX = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\})\s*;',
    re.DOTALL,
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
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "job_titles",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {
                        "job_titles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 5,
                            "maxItems": 7,
                        }
                    },
                    "required": ["job_titles"],
                    "additionalProperties": False,
                },
            },
        },
        temperature=0.2,
    )

    content = (response.choices[0].message.content or "").strip()
    payload = json.loads(content)
    return _validate_job_titles(payload.get("job_titles"))


def _random_chrome_user_agent() -> str:
    major = random.randint(122, 126)
    build = random.randint(0, 9999)
    patch = random.randint(0, 200)
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36"
    )


def parse_jobs_from_html(html: str, keyword: str) -> list[dict[str, Any]]:
    match = MOSAIC_REGEX.search(html)
    if not match:
        return []

    blob = json.loads(match.group(1))
    results = blob["metaData"]["mosaicProviderJobCardsModel"]["results"]

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
                "title": str(job.get("displayTitle") or "").strip(),
                "company": str(job.get("company") or "").strip(),
                "location": str(job.get("formattedLocation") or "").strip(),
                "salary": salary,
                "url": f"{DOMAIN}/viewjob?jk={job_key}",
                "job_key": job_key,
                "keyword": keyword,
                "scraped_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
        )

    return jobs


def scrape_indeed(keyword: str) -> list[dict[str, Any]]:
    session = requests.Session()
    all_jobs: list[dict[str, Any]] = []

    for page in range(MAX_PAGES):
        offset = page * 10
        url = (
            f"{DOMAIN}/jobs"
            f"?q={quote_plus(keyword)}"
            f"&l={quote_plus(LOCATION)}"
            f"&fromage={int(DATE_FILTER)}"
            f"&start={offset}"
        )

        headers = {"User-Agent": _random_chrome_user_agent()}
        resp = session.get(url, headers=headers, impersonate="chrome124", timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] Indeed returned HTTP {resp.status_code} for keyword={keyword!r}, page={page+1}")
            break

        jobs = parse_jobs_from_html(resp.text, keyword=keyword)
        if not jobs:
            break

        all_jobs.extend(jobs)
        time.sleep(random.uniform(3, 7))

    return all_jobs


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


def save_json(jobs: list[dict[str, Any]]) -> str:
    out_path = Path(OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(out_path)


def _date_filter_label() -> str:
    if int(DATE_FILTER) == 1:
        return "Last 24 hours"
    if int(DATE_FILTER) == 7:
        return "Last 7 days"
    return f"Last {DATE_FILTER} days"


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

    if DEBUG_KEYWORD_OVERRIDE:
        keywords = [DEBUG_KEYWORD_OVERRIDE]
    else:
        keywords = generate_job_titles(cv_text)

    all_jobs: list[dict[str, Any]] = []
    for keyword in keywords:
        print(f"[Indeed] Scraping: {keyword!r}")
        all_jobs.extend(scrape_indeed(keyword))

    unique_jobs = deduplicate(all_jobs)
    output_path = save_json(unique_jobs)

    print("══════════════════════════════════════")
    print(" Job Hunter Complete")
    print("══════════════════════════════════════")
    print(f" Keywords searched : {len(keywords)}")
    print(f" Raw results       : {len(all_jobs)}")
    print(f" After dedup       : {len(unique_jobs)} unique jobs")
    print(f" Date filter       : {_date_filter_label()}")
    print(f" Location          : {LOCATION}")
    print(f" Output            : {output_path}")
    print("══════════════════════════════════════")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run end-to-end:

```bash
python job_hunter.py
```

## Step 4 Verification Checklist

- [ ] The script creates `output/job_leads.json`.
- [ ] `output/job_leads.json` is valid JSON and is a list of objects.
- [ ] Every object includes these keys: `title`, `company`, `location`, `salary`, `url`, `job_key`, `keyword`, `scraped_at`.
- [ ] There are **no duplicate** `job_key` values.
- [ ] Open at least 5 `url` values and confirm they load.
- [ ] Change `DATE_FILTER = 7`, re-run, and confirm the job count increases meaningfully.

## Step 4 STOP & COMMIT

STOP & COMMIT: Agent must stop here and wait for the user to test, stage, and commit the change.

