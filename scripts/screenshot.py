"""Capture real screenshots of the running app for the README.

Requires the API to be running locally (``uvicorn app.main:app``) and
Playwright Chromium installed (``pip install playwright`` then
``playwright install chromium``).

Produces:
  docs/screenshots/dashboard-list.png  — dashboard: metrics, form, queue, claims table
  docs/screenshots/dashboard-detail.png — dashboard: open claim detail + review actions
  docs/screenshots/swagger-ui.png    — the FastAPI /docs page
  docs/screenshots/triage-result.png — a real triaged claim (JSON view)
  docs/screenshots/metrics.png       — the live /metrics endpoint
"""

from __future__ import annotations

import json
import os
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(BASE_DIR, "docs", "screenshots")
API = os.environ.get("API_URL", "http://localhost:8000")


def _get_json(path: str) -> dict | list:
    with urllib.request.urlopen(API + path, timeout=10) as resp:
        return json.loads(resp.read())


def _launch_kwargs() -> dict:
    """Proxy config for sandboxed environments.

    In sandboxes with an egress proxy, Chromium needs the proxy passed
    explicitly (it does not read the shell's *_PROXY variables). The
    proxy URL comes from the environment — never hardcoded here.
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy_url:
        return {
            "proxy": {
                "server": proxy_url,
                "bypass": "localhost,127.0.0.1,::1",
            }
        }
    return {}


def main() -> None:
    from playwright.sync_api import sync_playwright

    os.makedirs(SHOT_DIR, exist_ok=True)

    claims = _get_json("/claims?status=auto_approved")
    if not claims:
        raise SystemExit("No auto-approved claim found — seed the API first.")
    claim_id = claims[0]["id"]
    print(f"Using claim {claim_id} for the triage screenshot.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_launch_kwargs())
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # --- Dashboard: claims list ---
        page.goto(f"{API}/", wait_until="networkidle")
        page.wait_for_selector("#claims-body tr[data-id]", timeout=15000)
        page.wait_for_timeout(1000)
        page.screenshot(
            path=os.path.join(SHOT_DIR, "dashboard-list.png"), full_page=True
        )
        print("Saved dashboard-list.png")

        # --- Dashboard: claim detail (click the first pending-review row) ---
        page.click("#queue-list li[data-id] >> nth=0")
        page.wait_for_selector("#detail-body .review-box", timeout=15000)
        page.wait_for_timeout(800)
        page.screenshot(
            path=os.path.join(SHOT_DIR, "dashboard-detail.png"), full_page=True
        )
        print("Saved dashboard-detail.png")

        page.goto(f"{API}/docs", wait_until="load")
        page.wait_for_selector(".swagger-ui", timeout=30000)
        page.wait_for_timeout(2000)
        page.screenshot(
            path=os.path.join(SHOT_DIR, "swagger-ui.png")
        )
        print("Saved swagger-ui.png")

        page.goto(f"{API}/claims/{claim_id}", wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(
            path=os.path.join(SHOT_DIR, "triage-result.png"), full_page=True
        )
        print("Saved triage-result.png")

        page.goto(f"{API}/metrics", wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(
            path=os.path.join(SHOT_DIR, "metrics.png"), full_page=True
        )
        print("Saved metrics.png")

        browser.close()


if __name__ == "__main__":
    main()
