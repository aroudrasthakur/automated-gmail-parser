"""SQLite storage for scanned emails and job postings."""

import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("GMAIL_PARSER_DB", os.path.join(os.path.dirname(__file__), "data", "jobs.db"))


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS emails (
                message_id   TEXT PRIMARY KEY,
                thread_id    TEXT,
                subject      TEXT,
                sender       TEXT,
                internal_ts  INTEGER,
                scanned_at   INTEGER
            );

            CREATE TABLE IF NOT EXISTS jobs (
                url            TEXT PRIMARY KEY,
                company        TEXT,
                title          TEXT,
                email_id       TEXT,
                email_date     INTEGER,
                sponsors_h1b   INTEGER,          -- NULL unknown, 1 yes, 0 no
                no_h1b_tag     INTEGER,          -- 1 if the "No H1B Sponsorship" tag is shown
                location_text  TEXT,
                in_usa         INTEGER,
                qualifies      INTEGER,          -- no_h1b_tag == 0 AND in_usa == 1
                error          TEXT,
                scanned_at     INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_qualifies ON jobs(qualifies);
            """
        )


def email_scanned(message_id):
    with get_db() as db:
        row = db.execute("SELECT 1 FROM emails WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None


def record_email(message_id, thread_id, subject, sender, internal_ts):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO emails (message_id, thread_id, subject, sender, internal_ts, scanned_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, thread_id, subject, sender, internal_ts, int(time.time())),
        )


def job_scanned(url):
    """True only when the job was scraped successfully (error rows are retried)."""
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM jobs WHERE url = ? AND error IS NULL", (url,)
        ).fetchone()
        return row is not None


def record_job(url, company, title, email_id, email_date, sponsors_h1b, no_h1b_tag,
               location_text, in_usa, qualifies, error=None):
    """Insert or refresh a job. On conflict the original email_id/email_date is kept
    (first-seen email) while the scrape fields are refreshed."""
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (url, company, title, email_id, email_date, sponsors_h1b, "
            "no_h1b_tag, location_text, in_usa, qualifies, error, scanned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET "
            "company=excluded.company, title=excluded.title, sponsors_h1b=excluded.sponsors_h1b, "
            "no_h1b_tag=excluded.no_h1b_tag, location_text=excluded.location_text, "
            "in_usa=excluded.in_usa, qualifies=excluded.qualifies, error=excluded.error, "
            "scanned_at=excluded.scanned_at",
            (url, company, title, email_id, email_date, sponsors_h1b, no_h1b_tag,
             location_text, in_usa, qualifies, error, int(time.time())),
        )


def list_results(qualifying_only=True):
    with get_db() as db:
        q = "SELECT * FROM jobs"
        if qualifying_only:
            q += " WHERE qualifies = 1"
        q += " ORDER BY email_date DESC, company"
        return [dict(r) for r in db.execute(q).fetchall()]


def stats():
    with get_db() as db:
        emails = db.execute("SELECT COUNT(*) c FROM emails").fetchone()["c"]
        jobs = db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        qual = db.execute("SELECT COUNT(*) c FROM jobs WHERE qualifies = 1").fetchone()["c"]
        return {"emails": emails, "jobs": jobs, "qualifying": qual}
