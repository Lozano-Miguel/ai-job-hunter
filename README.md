# AI Job Hunter

A Python CLI that reads your CV, generates job-search keywords with AI, scrapes 8 Portuguese job sources, and stores results in a JSON file powering a local dashboard.

## Features

- **CV parsing** — extracts text from `cv.pdf` with pdfplumber
- **AI keyword generation** — Groq (Llama 3.3 70B) generates 5-7 job title keywords from your CV
- **8 job sources**:
  - Indeed (Safari-impersonated sessions)
  - LinkedIn (Chrome-impersonated sessions)
  - ITJobs (official API)
  - Net-Empregos (HTML scraping)
  - Sapo Emprego (JSON component extraction)
  - Landing.jobs (public REST API)
  - TechJobs.pt (hidden API)
  - Expresso Emprego (HTML scraping)
- **Modern CLI** — ASCII art banner, clear screens between steps, multi-select source picker
- **Unified date filtering** — `posted_at` field across all sources with configurable range (1/3/7/14 days)
- **Deduplication** in two passes:
  - by `job_key` (source-prefixed identifier)
  - by normalized `title + company`, keeping the most complete entry
- **Incremental persistence** — merges new results into `output/job_leads.json`, preserving status and notes
- **Dashboard** — local single-page UI for filtering, status tracking, and notes
- **Retry resilience** — exponential backoff (3 attempts) on timeout, connection errors, and HTTP 5xx

## Requirements

- Python 3.10+

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_key
ITJOBS_API_KEY=your_itjobs_key
```

- `GROQ_API_KEY` is required for AI keyword generation.
- `ITJOBS_API_KEY` is required only if you include ITJobs as a source.

Place your CV as `cv.pdf` in the project root.

## Usage

### Full run

```bash
python job_hunter.py
```

The CLI walks you through 4 steps:
1. **Sources** — toggle which sources to scrape (multi-select)
2. **Location & Filters** — city, date range, radius, max pages
3. **AI Keywords** — optional extra context before Groq generates job titles
4. **Scraping** — runs all selected sources with progress output

### Reuse previous keywords

```bash
python job_hunter.py --no-groq
```

Skips the Groq API call and reuses keywords from the existing `output/job_leads.json`.

### Dashboard only

```bash
python job_hunter.py --dashboard
```

Opens a local server at `http://localhost:8000/dashboard.html` for reviewing and tracking jobs.

## Output

Results are saved to `output/job_leads.json`. Each job record includes:

| Field | Description |
|-------|-------------|
| `title` | Job title |
| `company` | Company name |
| `location` | Job location |
| `salary` | Salary info (when available) |
| `url` | Link to the original listing |
| `job_key` | Source-prefixed unique ID (e.g. `li_`, `itjobs_`, `ne_`) |
| `keyword` | Search keyword that found this job |
| `source` | Source name |
| `posted_at` | Publication date (YYYY-MM-DD) |
| `scraped_at` | When the job was scraped |
| `status` | `not_applied` / `applied` / `interviewing` / `rejected` |
| `notes` | User notes (editable in dashboard) |

## Project Structure

```
job_hunter.py          # Main CLI: CV parsing, Groq, scrapers, dedup, JSON persistence
dashboard_server.py    # Local HTTP server + POST /update-status endpoint
output/dashboard.html  # Single-page dashboard UI (vanilla JS)
output/job_leads.json  # Scraped job data (gitignored)
cv.pdf                 # Your CV (gitignored)
.env                   # API keys (gitignored)
requirements.txt       # Python dependencies
```
