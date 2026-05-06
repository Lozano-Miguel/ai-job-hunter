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
