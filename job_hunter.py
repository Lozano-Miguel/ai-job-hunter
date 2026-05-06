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
