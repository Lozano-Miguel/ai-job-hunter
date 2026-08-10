from __future__ import annotations

import json
import os
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler

VALID_STATUSES = {"not_applied", "applied", "interviewing", "rejected"}


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
JOB_LEADS_PATH = os.path.join(OUTPUT_DIR, "job_leads.json")


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def end_headers(self) -> None:
        if self.path.startswith("/job_leads.json"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/update-status":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            payload = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return

        job_key = str(payload.get("job_key") or "").strip()
        if not job_key or not re.match(r"^[\w\-]+$", job_key):
            self._send_json(400, {"ok": False, "error": "Invalid job_key"})
            return

        has_status = "status" in payload
        has_notes = "notes" in payload
        if not (has_status or has_notes):
            self._send_json(400, {"ok": False, "error": "No fields to update"})
            return
        status = str(payload.get("status") or "").strip() if has_status else None
        notes = str(payload.get("notes") or "") if has_notes else None
        if has_status and (not status or status not in VALID_STATUSES):
            self._send_json(400, {"ok": False, "error": "Invalid status"})
            return

        try:
            with open(JOB_LEADS_PATH, "r", encoding="utf-8") as f:
                jobs = json.load(f)
        except FileNotFoundError:
            self._send_json(404, {"ok": False, "error": "job_leads.json not found"})
            return
        except json.JSONDecodeError:
            self._send_json(500, {"ok": False, "error": "job_leads.json is invalid"})
            return

        if not isinstance(jobs, list):
            self._send_json(500, {"ok": False, "error": "job_leads.json must be a list"})
            return

        found = False
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("job_key") or "") == job_key:
                if has_status:
                    job["status"] = status
                if has_notes:
                    job["notes"] = notes
                found = True
                break

        if not found:
            self._send_json(404, {"ok": False, "error": "job_key not found"})
            return

        tmp_path = JOB_LEADS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, JOB_LEADS_PATH)

        self._send_json(200, {"ok": True})


def create_server() -> HTTPServer:
    HTTPServer.allow_reuse_address = True
    return HTTPServer(("localhost", 8000), DashboardHandler)


def main(server: HTTPServer | None = None) -> None:
    if server is None:
        server = create_server()
    print("[Dashboard] Running at http://localhost:8000/dashboard.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
