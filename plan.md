# AI Job Hunter Agent

**Branch:** `feature/ai-job-hunter`
**Description:** A local Python script that reads your CV, uses Groq to generate relevant job titles, scrapes pt.indeed.com for recent listings, and exports them to a JSON file.

---

## Goal

Build an end-to-end automated job-hunting agent that runs entirely locally. Drop in your CV, run the script, and get a clean JSON file of fresh job leads — posted in the last 24 hours by default — ready to review and apply to.

---

## Tech Stack & Key Decisions

| Concern | Choice | Reason |
|---|---|---|
| PDF extraction | `pdfplumber` | Reliable text extraction, handles multi-column CV layouts cleanly |
| LLM | Groq API (`llama-3.3-70b-versatile`) | Fast, generous free tier, supports `response_format=json_object` |
| Scraper | `curl_cffi` with `impersonate="chrome124"` | Bypasses Indeed's TLS fingerprinting / Cloudflare without a headless browser |
| Indeed data source | `window.mosaic.providerData["mosaic-provider-jobcards"]` JSON blob | Proven embedded-JSON pattern — fully structured, no fragile HTML parsing |
| Date filter | `fromage` URL param (`1` = 24h, `7` = 7 days) | Native Indeed parameter — clean, reliable |
| Deduplication | `jobkey` field (Indeed's unique job ID) | Canonical dedup key that persists across keyword searches |
| Output | `job_leads.json` | Pretty-printed JSON, easy to inspect and process |
| Config | `.env` + `python-dotenv` | Keeps API key out of source code |

---

## Project File Structure

```
ai-job-hunter/
├── cv.pdf                  ← YOU place your CV here
├── job_hunter.py           ← Main script (all logic)
├── requirements.txt
├── .env                    ← GROQ_API_KEY=... (gitignored)
├── .env.example            ← Safe template to share/commit
├── .gitignore
└── output/
    └── job_leads.json      ← Generated on every run (overwritten)
```

---

## CONFIG Block (top of `job_hunter.py`)

The user-facing settings are isolated at the top of the script — no need to dig through the code to change them:

```python
# ── USER CONFIG ────────────────────────────────────────────
CV_PATH        = "cv.pdf"
LOCATION       = "Lisbon, Portugal"   # Change freely
DOMAIN         = "https://pt.indeed.com"
DATE_FILTER    = 1                    # 1 = last 24h | 7 = last week
MAX_PAGES      = 3                    # Pages per keyword (10 jobs/page)
OUTPUT_FILE    = "output/job_leads.json"
# ───────────────────────────────────────────────────────────
```

---

## Implementation Steps

### Step 1: Project Scaffold + CV Extraction
**Commit:** `feat: project setup, config, and PDF CV extraction`

**Files:**
- `requirements.txt`
- `.env.example`
- `.gitignore`
- `job_hunter.py` (CONFIG block + `extract_cv_text()` function)

**What:**
Create the project skeleton with all dependencies declared. Implement `extract_cv_text(pdf_path)` using `pdfplumber` — opens the CV PDF, iterates over all pages, and concatenates the extracted text. At startup, validate that `cv.pdf` exists and is non-empty; exit with a clear message if not.

**Dependencies to pin in `requirements.txt`:**
```
pdfplumber==0.11.4
groq==0.11.0
curl-cffi==0.7.4
python-dotenv==1.0.1
```

**Testing:**
Run `python job_hunter.py` — it should print the first 500 characters of extracted CV text and then exit (the other steps are not wired yet). If the file is missing it should print `[ERROR] cv.pdf not found. Please place your CV in the project directory.` and exit cleanly.

---

### Step 2: AI Keyword Generation via Groq
**Commit:** `feat: Groq integration to generate job title keywords from CV`

**Files:**
- `job_hunter.py` (add `generate_job_titles(cv_text)` function)
- `.env` (user adds their key here — gitignored)
- `.env.example` (`GROQ_API_KEY=your_key_here`)

**What:**
Send the extracted CV text to Groq using `response_format={"type": "json_object"}` to guarantee a parseable response. Parse the result and validate it's a list of 5–7 strings before continuing.

**Groq call specifics:**
- Model: `llama-3.3-70b-versatile`
- `response_format={"type": "json_object"}`
- Load `GROQ_API_KEY` from `.env` via `python-dotenv`

**Expected response shape:**
```json
{ "job_titles": ["Data Analyst", "Business Intelligence Engineer", "Product Analyst", ...] }
```

**System prompt:**
```
You are an expert career counselor with 20 years of experience.
Analyze the CV below and return a JSON object with a single key
"job_titles" containing an array of 5 to 7 strings. Each string
must be a realistic, searchable job title a recruiter would post
on Indeed. Return only valid JSON — no explanation, no markdown.
```

**Testing:**
Run the script — it should print a numbered list like:
```
[Groq] Generated 6 job titles:
  [1] Data Analyst
  [2] Business Intelligence Engineer
  [3] Product Analyst
  ...
```
If `GROQ_API_KEY` is missing from `.env`, exit with a clear error message.

---

### Step 3: Indeed Scraper
**Commit:** `feat: Indeed scraper using curl_cffi and mosaic JSON extraction`

**Files:**
- `job_hunter.py` (add `scrape_indeed(keyword)` and `parse_jobs_from_html(html, keyword)` functions)

**What:**
For each keyword from Step 2, scrape `pt.indeed.com` search results across up to `MAX_PAGES` pages (default 3, so 30 results/keyword).

**URL pattern:**
```
https://pt.indeed.com/jobs?q={keyword}&l={location}&fromage={DATE_FILTER}&start={offset}
```
- `fromage=1` → last 24 hours (default)
- `fromage=7` → last 7 days
- `start` increments by 10 per page

**Extraction logic — regex on the raw HTML:**
```python
import re, json

pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\})\s*;'
match = re.search(pattern, html, re.DOTALL)
blob = json.loads(match.group(1))
results = blob["metaData"]["mosaicProviderJobCardsModel"]["results"]
```

**Fields to extract per job:**
| Output field | Source in JSON |
|---|---|
| `title` | `job["displayTitle"]` |
| `company` | `job["company"]` |
| `location` | `job["formattedLocation"]` |
| `salary` | `job["salarySnippet"]["text"]` (or `"N/A"`) |
| `url` | `"https://pt.indeed.com/viewjob?jk=" + job["jobkey"]` |
| `job_key` | `job["jobkey"]` |
| `keyword` | the search term that surfaced it |
| `scraped_at` | `datetime.utcnow().isoformat()` |

**Anti-ban measures:**
- `curl_cffi` session with `impersonate="chrome124"`
- Randomised Chrome User-Agent version per request
- `time.sleep(random.uniform(3, 7))` between every page request
- Stop paginating early if a page returns 0 results (end of listings)

**Testing:**
Temporarily hardcode one keyword (e.g. `"Data Analyst"`) and run. Confirm job dicts are printed to stdout and that the `viewjob` URLs open valid postings in a browser. Confirm the `fromage=1` filter is working by checking that `postedAt` fields in the raw JSON say "Just posted" or "Today".

---

### Step 4: Deduplication + JSON Output
**Commit:** `feat: deduplication, JSON export, and full main() wiring`

**Files:**
- `job_hunter.py` (add `deduplicate(jobs)`, `save_json(jobs)`, wire up `main()`)

**What:**
Collect all job dicts from every keyword search. Deduplicate by `job_key` — keep only the first occurrence when the same posting surfaces under multiple keywords. Create `output/` directory if it doesn't exist. Write `job_leads.json` as a pretty-printed JSON array.

**Output JSON shape:**
```json
[
  {
    "title": "Data Analyst",
    "company": "Accenture Portugal",
    "location": "Lisboa",
    "salary": "N/A",
    "url": "https://pt.indeed.com/viewjob?jk=abc123",
    "job_key": "abc123",
    "keyword": "Data Analyst",
    "scraped_at": "2026-05-06T14:32:01"
  },
  ...
]
```

**Full `main()` flow:**
```
1. extract_cv_text()        →  cv_text
2. generate_job_titles()    →  keywords   (Groq)
3. for each keyword:
       scrape_indeed()      →  raw_jobs   (curl_cffi + mosaic JSON)
4. deduplicate()            →  unique_jobs
5. save_json()              →  output/job_leads.json
6. Print run summary
```

**Run summary printed to console:**
```
══════════════════════════════════════
 Job Hunter Complete
══════════════════════════════════════
 Keywords searched : 6
 Raw results       : 142
 After dedup       : 118 unique jobs
 Date filter       : Last 24 hours
 Location          : Lisbon, Portugal
 Output            : output/job_leads.json
══════════════════════════════════════
```

**Testing:**
Run the full script end-to-end with your real CV. Open `job_leads.json` and verify all fields are populated. Confirm there are no duplicate `job_key` values. Confirm all URLs open valid job postings. Try changing `DATE_FILTER = 7` in the CONFIG block and re-running — the result count should increase meaningfully.