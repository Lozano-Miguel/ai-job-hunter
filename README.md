# AI Job Hunter

A local Python CLI that reads your CV, generates job-search keywords with Groq, scrapes multiple Portuguese job sources, and stores results in a single JSON file used by the dashboard.

## Features

- CV text extraction from `cv.pdf`
- AI keyword generation (5-7 job titles) via Groq
- Multi-source scraping:
  - Indeed
  - LinkedIn
  - ITJobs
  - Net-Empregos
  - Sapo
- Request resilience for scraper `GET` calls:
  - retries on timeout/connection errors
  - retries on HTTP `5xx`
  - exponential backoff (`2s`, `4s`, `8s`, up to 3 attempts)
- Session priming before scraping (Indeed and LinkedIn)
- Deduplication in two passes:
  - by `job_key`
  - by normalized `title + company`, keeping the most complete entry
- Incremental persistence to `output/job_leads.json` with tracking fields:
  - `applied`
  - `status` (`not_applied`, `applied`, `interviewing`, `rejected`)
  - `notes`
- Built-in dashboard mode with local status/notes updates

## Requirements

- Python `3.10+`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root:

```bash
GROQ_API_KEY=your_groq_key
ITJOBS_API_KEY=your_itjobs_key
```

Notes:
- `GROQ_API_KEY` is required for AI keyword generation.
- `ITJOBS_API_KEY` is required only if you include ITJobs as a source.

Place your CV as `cv.pdf` in the project root.

## Usage

### Default run

```bash
python job_hunter.py
```

The CLI prompts you for:
- sources
- location
- date filter
- radius
- max pages
- optional extra context for the AI

### Reuse previous keywords (skip Groq)

```bash
python job_hunter.py --no-groq
```

This reuses keywords found in the existing `output/job_leads.json`.

### Open dashboard mode

```bash
python job_hunter.py --dashboard
```

Starts a local server and opens:
- `http://localhost:8000/dashboard.html`

## Output

Results are written to:
- `output/job_leads.json`

Each item includes fields such as:
- `title`, `company`, `location`, `salary`, `url`
- `job_key`, `keyword`, `source`, `scraped_at`
- `applied`, `status`, `notes`

## Project files

- `job_hunter.py` - main CLI, scraping, deduplication, persistence
- `dashboard_server.py` - local HTTP server for dashboard and updates
- `output/dashboard.html` - UI for filtering and tracking job applications
