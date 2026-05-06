# AI Job Hunter

Local Python CLI that:

- Extracts text from `cv.pdf`
- Uses Groq to generate 5–7 job-title keywords
- Scrapes job listings from `pt.indeed.com`
- Deduplicates results by `job_key`
- Writes `output/job_leads.json`

## Requirements

- Python 3.10+

## Setup

Create and activate a virtualenv, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a `.env` file (do not commit it):

```bash
GROQ_API_KEY=your_key_here
```

Place your CV as `cv.pdf` in the repo root.

## Run

```bash
python job_hunter.py
```

On success, the script prints a run summary and creates:

- `output/job_leads.json`

## Output format

`output/job_leads.json` is a JSON array of objects with keys:

- `title`
- `company`
- `location`
- `salary`
- `url`
- `job_key`
- `keyword`
- `scraped_at`

## Notes

- Scraping uses `curl_cffi` with Chrome impersonation. If Indeed blocks requests (e.g., HTTP 403), try again later or reduce request rate.
