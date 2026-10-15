"""SQLite storage for scanned emails and job postings."""

import os
import sqlite3
import time
from contextlib import contextmanager

import chicago

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

            CREATE TABLE IF NOT EXISTS app_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        _migrate_jobs(db)


def _migrate_jobs(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(jobs)").fetchall()}
    if "status" not in cols:
        db.execute("ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "applied_at" not in cols:
        db.execute("ALTER TABLE jobs ADD COLUMN applied_at INTEGER")
    if "starred" not in cols:
        db.execute("ALTER TABLE jobs ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")


def _meta_get(key, default=""):
    with get_db() as db:
        row = db.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def _meta_set(key, value):
    with get_db() as db:
        db.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_scan_coverage_days():
    raw = _meta_get("scan_coverage_days", "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def set_scan_coverage_days(days):
    _meta_set("scan_coverage_days", max(0, int(days)))


def get_scanned_days():
    raw = _meta_get("scanned_days", "")
    if not raw:
        return set()
    return {s for s in raw.split(",") if s}


def add_scanned_days(dates):
    current = get_scanned_days()
    for d in dates:
        if hasattr(d, "isoformat"):
            current.add(d.isoformat())
        else:
            current.add(str(d))
    _meta_set("scanned_days", ",".join(sorted(current)))


def is_day_scanned(day) -> bool:
    iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
    return iso in get_scanned_days()


def has_email_on_day(day):
    """True if any stored email landed on this calendar day (local timezone)."""
    after, before = chicago.day_utc_bounds(day)
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM emails WHERE internal_ts >= ? AND internal_ts < ?",
            (after, before),
        ).fetchone()
        return row is not None


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


def list_results(qualifying_only=True, job_status="active"):
    with get_db() as db:
        clauses = []
        if job_status == "active":
            clauses.append("status = 'active'")
        elif job_status == "applied":
            clauses.append("status = 'applied'")
        elif job_status == "starred":
            clauses.append("status = 'active'")
            clauses.append("starred = 1")
        if qualifying_only and job_status != "applied":
            clauses.append("qualifies = 1")
        q = "SELECT * FROM jobs"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        if job_status == "active":
            q += " ORDER BY starred DESC, email_date DESC, company"
        else:
            q += " ORDER BY email_date DESC, company"
        return [dict(r) for r in db.execute(q).fetchall()]


def delete_job(url):
    with get_db() as db:
        db.execute("DELETE FROM jobs WHERE url = ?", (url,))


def mark_job_applied(url):
    with get_db() as db:
        db.execute(
            "UPDATE jobs SET status = 'applied', applied_at = ?, starred = 0 WHERE url = ?",
            (int(time.time()), url),
        )


def set_job_starred(url, starred):
    with get_db() as db:
        cur = db.execute(
            "UPDATE jobs SET starred = ? WHERE url = ? AND status = 'active'",
            (1 if starred else 0, url),
        )
        return cur.rowcount > 0


def mark_job_active(url):
    with get_db() as db:
        db.execute(
            "UPDATE jobs SET status = 'active', applied_at = NULL WHERE url = ?",
            (url,),
        )


def stats():
    with get_db() as db:
        emails = db.execute("SELECT COUNT(*) c FROM emails").fetchone()["c"]
        jobs = db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        qual = db.execute(
            "SELECT COUNT(*) c FROM jobs WHERE qualifies = 1 AND status = 'active'"
        ).fetchone()["c"]
        applied = db.execute("SELECT COUNT(*) c FROM jobs WHERE status = 'applied'").fetchone()["c"]
        starred = db.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status = 'active' AND starred = 1 AND qualifies = 1"
        ).fetchone()["c"]
        return {
            "emails": emails,
            "jobs": jobs,
            "qualifying": qual,
            "applied": applied,
            "starred": starred,
            "scan_coverage_days": get_scan_coverage_days(),
            "scanned_days": sorted(get_scanned_days()),
        }
