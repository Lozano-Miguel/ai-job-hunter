# AI Job Hunter

Python CLI that reads a CV, generates job-search keywords via Groq LLM, scrapes Portuguese job boards, and stores results in a JSON file powering a local dashboard.

## Stack

- Python 3.10+ (single-file CLI + dashboard server)
- Groq API (Llama 3.3 70B) for keyword generation
- curl_cffi for anti-bot scraping sessions (Indeed, LinkedIn, Net-Empregos, Sapo)
- requests for ITJobs API
- pdfplumber for CV text extraction
- Built-in http.server for the dashboard

## Project structure

```
job_hunter.py          # Main CLI: CV parsing, Groq call, scrapers, dedup, JSON persistence
dashboard_server.py    # Local HTTP server serving dashboard + POST /update-status endpoint
output/dashboard.html  # Single-page dashboard UI (vanilla JS, no build step)
output/job_leads.json  # Scraped job data (gitignored)
cv.pdf                 # User's CV (gitignored)
.env                   # API keys (gitignored)
```

## Key commands

```bash
# Full run (interactive prompts for sources, location, filters)
python job_hunter.py

# Reuse keywords from last run (skip Groq call)
python job_hunter.py --no-groq

# Open dashboard only
python job_hunter.py --dashboard
```

## Virtual environment

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Environment variables

- `GROQ_API_KEY` - Required for keyword generation
- `ITJOBS_API_KEY` - Required only when scraping ITJobs

## Data flow

1. Extract text from `cv.pdf`
2. Groq generates 5-7 job title keywords
3. For each keyword, scrape selected sources (Indeed, LinkedIn, ITJobs, Net-Empregos, Sapo)
4. Deduplicate by job_key then by normalized title+company
5. Merge into `output/job_leads.json` (incremental, preserves status/notes)
6. Dashboard reads JSON, supports status changes (not_applied/applied/interviewing/rejected) and notes

## Git

- Never commit as Co-Authored-By. Commits and pushes go under Miguel's name only.

## Conventions

- All scraping uses curl_cffi sessions with Chrome impersonation except ITJobs (standard requests POST)
- Retry decorator `retry_http_get` handles transient failures (3 attempts, exponential backoff)
- Job records use `job_key` for dedup identity (source-prefixed: `li_`, `itjobs_`, `ne_`, `sapo_`)
- JSON writes use atomic tmp+rename pattern
- Dashboard communicates with server via POST to `/update-status`
