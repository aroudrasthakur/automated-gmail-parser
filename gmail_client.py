"""Gmail API client: OAuth2 auth + swelist email search/fetch."""

import base64
import os
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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


def search_message_ids(service, days):
    """Message IDs from swelist in the last `days` days."""
    after = int(time.time()) - days * 86400
    query = f'from:{SENDER} after:{after} "{SUBJECT_CONTAINS}"'
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
