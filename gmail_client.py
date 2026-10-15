"""Gmail API client: OAuth2 auth + swelist email search/fetch."""

import base64
import logging
import os
import time
from datetime import date, datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import chicago

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

SENDER = "noreply@swelist.com"
SUBJECT_CONTAINS = "new jobs posted today"


def load_credentials():
    """Return valid Credentials from token.json, or None if auth is needed."""
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds)
    if creds and creds.valid:
        return creds
    return None


def save_credentials(creds):
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


def is_authenticated():
    return load_credentials() is not None


def get_service():
    creds = load_credentials()
    if not creds:
        raise RuntimeError("not_authenticated")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _list_ids(service, query):
    log.info("gmail search: %s", query)
    ids = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids


def is_jobs_digest(subject):
    """SweList daily *jobs* email (not internships)."""
    s = (subject or "").lower()
    return "jobs posted today" in s and "internship" not in s


def _keep_in_window(service, ids, after, before):
    if not ids:
        return []
    start_day = datetime.fromtimestamp(after, chicago.CHICAGO).date()
    end_day = datetime.fromtimestamp((before or after + 1) - 1, chicago.CHICAGO).date()
    kept = []
    for mid in ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=mid, format="metadata", metadataHeaders=["Subject"])
            .execute()
        )
        ts = int(msg.get("internalDate", 0)) // 1000
        subject = _header(msg.get("payload", {}).get("headers", []), "Subject")
        if not is_jobs_digest(subject):
            continue
        chi_day = datetime.fromtimestamp(ts, chicago.CHICAGO).date()
        utc_day = datetime.fromtimestamp(ts, timezone.utc).date()
        if start_day <= chi_day <= end_day or start_day <= utc_day <= end_day:
            kept.append(mid)
            log.info("keep %s %s %s", mid, chi_day, subject)
    log.info("gmail search kept %d/%d", len(kept), len(ids))
    return kept


def _search_query(service, after: int, before: int | None = None):
    """Find SweList digest emails in a time window without brittle Gmail date phrases."""
    age_days = max(1, int((time.time() - after) / 86400) + 2)
    query = f"from:swelist.com newer_than:{age_days}d"
    ids = _list_ids(service, query)
    return _keep_in_window(service, ids, after, before)


def search_message_ids(service, days, skip_recent_days=0):
    bounds = chicago.range_utc_bounds(days, skip_recent_days)
    if not bounds:
        return []
    after, before = bounds
    return _search_query(service, after, before)


def search_message_ids_for_day(service, day: date):
    after, before = chicago.day_utc_bounds(day)
    return _search_query(service, after, before)


def search_message_ids_between(service, after: int, before: int):
    return _search_query(service, after, before)


def _header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(part):
    data = (part.get("body") or {}).get("data") or ""
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")


def _find_html(payload):
    """Return (html, text) bodies walking the MIME tree."""
    html = text = ""
    mime = payload.get("mimeType", "")
    if mime == "text/html":
        html = _decode_body(payload)
    elif mime == "text/plain":
        text = _decode_body(payload)
    for part in payload.get("parts", []) or []:
        h, t = _find_html(part)
        html = html or h
        text = text or t
    return html, text


def get_message(service, message_id):
    """Return {id, subject, sender, internal_ts, html}."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject = _header(headers, "Subject")
    sender = _header(headers, "From")
    internal_ts = int(msg.get("internalDate", 0)) // 1000
    html, text = _find_html(payload)
    if not html and text:
        html = "<pre>" + text + "</pre>"
    return {
        "id": message_id,
        "thread_id": msg.get("threadId", ""),
        "subject": subject,
        "sender": sender,
        "internal_ts": internal_ts,
        "html": html,
    }
