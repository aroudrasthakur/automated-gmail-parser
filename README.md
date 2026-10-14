# SweList Job Scanner

Scans your Gmail for `noreply@swelist.com` **"New Jobs Posted Today"** emails, opens every
Simplify job link inside them, and lists the postings that:

- do **not** show the **"No H1B Sponsorship"** tag, and
- have a **location in the USA**.

Results are stored in SQLite (`data/jobs.db`) so re-scans are incremental — already-scanned
emails and job pages are skipped.

## Stack

Python · Flask · SQLite · Gmail API (read-only OAuth) · BeautifulSoup + `__NEXT_DATA__` JSON
scraping. No headless browser or LLM needed: Simplify pages embed all tag data in
`__NEXT_DATA__` (`jobPosting.sponsors_h1b` / `jobPosting.job.company.sponsors_h1b`,
`jobPosting.locations[]`).

## Setup

### 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create Google OAuth credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project.
2. **APIs & Services → Library** → enable **Gmail API**.
3. **APIs & Services → OAuth consent screen** → External → fill in the basics, add yourself
   as a **test user**.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - **Web application**, name it anything, and add authorized redirect URI:
     `http://localhost:5000/oauth2callback`
   - Download the JSON and save it as `credentials.json` next to `app.py`.

   > A **Desktop app** client also works — Google allows loopback redirects for it. Or skip
   > the web flow entirely and run `python auth.py`, which does the local-server flow and
   > writes `token.json` directly.

### 3. Run

```bash
python app.py          # serves http://localhost:5000
```

1. Open http://localhost:5000 → **Connect Gmail** → approve read-only access
   (token saved to `token.json`, gitignored).
2. Pick a day range (default 1 = last 24 h) → **Scan**.
3. Results table shows **company · title · location · email date · link**.
   Check "show all scraped jobs" to see filtered-out postings too.

## How matching works

For each `simplify.jobs/p/<id>` page the scraper reads `__NEXT_DATA__`:

| Field | Source |
|---|---|
| No-H1B tag | `jobPosting.sponsors_h1b` (job override) else `jobPosting.job.company.sponsors_h1b`; `false` ⇒ badge shown ⇒ filtered out. `null`/`true` ⇒ kept. The rendered badge text is checked too. |
| USA location | any entry of `jobPosting.locations[]` whose `country`/`value` resolves to the US (incl. "North America"/"Remote, USA"). |

## Notes

- Gmail scope used: `gmail.readonly` — the app never modifies your mailbox.
- Duplicate job links across emails are deduped; the earliest email date is kept.
- Jobs that fail to scrape are stored with an error and retried on the next scan.
- DB path override: `GMAIL_PARSER_DB=/path/to/db.sqlite`. Port override: `PORT=8000`
  (then register `http://localhost:8000/oauth2callback` as the redirect URI).
