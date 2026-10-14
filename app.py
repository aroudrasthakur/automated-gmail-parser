"""Flask app: Gmail auth, scan orchestration, results API."""

import logging
import os
import threading

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from google_auth_oauthlib.flow import Flow

import db
import gmail_client
import scraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Scan worker state (single-user local app)
_scan_lock = threading.Lock()
_scan = {
    "running": False,
    "error": None,
    "stage": "idle",
    "emails_found": 0,
    "emails_done": 0,
    "jobs_found": 0,
    "jobs_done": 0,
    "current": "",
}


def _reset_scan():
    _scan.update(
        running=True, error=None, stage="searching",
        emails_found=0, emails_done=0, jobs_found=0, jobs_done=0, current="",
    )


def run_scan(days):
    _reset_scan()
    try:
        service = gmail_client.get_service()
        ids = gmail_client.search_message_ids(service, days)
        _scan["emails_found"] = len(ids)
        todo = [mid for mid in ids if not db.email_scanned(mid)]
        log.info("scan: %d emails matched, %d new", len(ids), len(todo))

        for mid in todo:
            _scan["stage"] = "reading_email"
            _scan["current"] = mid
            msg = gmail_client.get_message(service, mid)
            if gmail_client.SUBJECT_CONTAINS not in (msg["subject"] or "").lower():
                db.record_email(mid, msg["thread_id"], msg["subject"], msg["sender"], msg["internal_ts"])
                _scan["emails_done"] += 1
                continue

            jobs = scraper.extract_jobs_from_email(msg["html"])
            new_jobs = [j for j in jobs if not db.job_scanned(j["url"])]
            _scan["jobs_found"] += len(new_jobs)
            log.info("email %s: %d links, %d new", mid, len(jobs), len(new_jobs))

            _scan["stage"] = "scraping_jobs"
            urls = [j["url"] for j in new_jobs]
            by_url = {j["url"]: j for j in new_jobs}
            for url, info in scraper.fetch_jobs(urls).items():
                meta = by_url[url]
                company = info["company"] or meta["company"] or "Unknown"
                title = info["title"] or meta["title"]
                qualifies = (not info["no_h1b_tag"]) and info["in_usa"] and not info["error"]
                db.record_job(
                    url=url,
                    company=company,
                    title=title,
                    email_id=mid,
                    email_date=msg["internal_ts"],
                    sponsors_h1b=None if info["sponsors_h1b"] is None else int(info["sponsors_h1b"]),
                    no_h1b_tag=int(info["no_h1b_tag"]),
                    location_text=info["location_text"],
                    in_usa=int(info["in_usa"]),
                    qualifies=int(qualifies),
                    error=info["error"],
                )
                _scan["jobs_done"] += 1
                _scan["current"] = company

            db.record_email(mid, msg["thread_id"], msg["subject"], msg["sender"], msg["internal_ts"])
            _scan["emails_done"] += 1

        _scan["stage"] = "done"
    except Exception as e:  # noqa: BLE001 - surface anything to the UI
        log.exception("scan failed")
        _scan["error"] = str(e)
        _scan["stage"] = "error"
    finally:
        _scan["running"] = False


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    return jsonify(
        {
            "authenticated": gmail_client.is_authenticated(),
            "credentials_present": os.path.exists(gmail_client.CREDENTIALS_FILE),
            "stats": db.stats(),
        }
    )


@app.get("/auth")
def auth():
    if not os.path.exists(gmail_client.CREDENTIALS_FILE):
        return (
            "credentials.json not found. Create OAuth credentials in Google Cloud "
            "Console and place credentials.json next to app.py (see README).",
            400,
        )
    flow = Flow.from_client_secrets_file(
        gmail_client.CREDENTIALS_FILE,
        scopes=gmail_client.SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["oauth_state"] = state
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.get("/oauth2callback")
def oauth2callback():
    code_verifier = session.pop("oauth_code_verifier", None)
    if not code_verifier:
        return "OAuth session expired; start again from /auth.", 400
    flow = Flow.from_client_secrets_file(
        gmail_client.CREDENTIALS_FILE,
        scopes=gmail_client.SCOPES,
        state=session.pop("oauth_state", None),
        redirect_uri=url_for("oauth2callback", _external=True),
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(authorization_response=request.url)
    gmail_client.save_credentials(flow.credentials)
    return redirect(url_for("index"))


@app.get("/disconnect")
def disconnect():
    if os.path.exists(gmail_client.TOKEN_FILE):
        os.remove(gmail_client.TOKEN_FILE)
    return redirect(url_for("index"))


@app.post("/api/scan")
def api_scan():
    body = request.get_json(silent=True) or {}
    days = max(1, min(int(body.get("days", 1)), 60))
    if not gmail_client.is_authenticated():
        return jsonify({"error": "not_authenticated"}), 401
    with _scan_lock:
        if _scan["running"]:
            return jsonify({"error": "scan already running"}), 409
        thread = threading.Thread(target=run_scan, args=(days,), daemon=True)
        thread.start()
    return jsonify({"started": True, "days": days})


@app.get("/api/scan_status")
def api_scan_status():
    return jsonify(dict(_scan))


@app.get("/api/results")
def api_results():
    qualifying_only = request.args.get("all") != "1"
    return jsonify({"results": db.list_results(qualifying_only), "stats": db.stats()})


if __name__ == "__main__":
    db.init_db()
    # Loopback OAuth over http://localhost is fine; do not use in production.
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
