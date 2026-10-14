"""Parse swelist emails for Simplify job links, then scrape each job page.

A Simplify job page embeds its data in Next.js __NEXT_DATA__ JSON:
  props.pageProps.jobPosting.sponsors_h1b        job-level override (often null)
  props.pageProps.jobPosting.job.company         {name, sponsors_h1b}
  props.pageProps.jobPosting.locations[]         {value, country}
The "No H1B Sponsorship" badge is rendered iff the effective sponsors_h1b
(job override, else company flag) is false. No JS rendering or LLM needed.
"""

import json
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

log = logging.getLogger(__name__)

SIMPLIFY_JOB_RE = re.compile(r"https?://simplify\.jobs/p/([0-9a-fA-F-]{32,36})[^\"'\s<>]*")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
NO_H1B_BADGE_RE = re.compile(r"(does not provide h1b sponsorship|no h1b sponsorship)", re.I)

US_COUNTRIES = {"united states", "usa", "us", "u.s.", "u.s.a.", "united states of america"}
US_REGIONS = {"north america", "us remote", "usa remote", "remote, usa", "remote - usa"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

_session = requests.Session()
_session.headers.update(HEADERS)


def canonical_job_url(url):
    """Normalize a simplify.jobs job URL to https://simplify.jobs/p/<uuid> for dedup."""
    m = SIMPLIFY_JOB_RE.search(url)
    if not m:
        return None
    return f"https://simplify.jobs/p/{m.group(1).lower()}"


def simplify_url_from_href(href):
    """Extract a simplify.jobs URL from an anchor href (direct or embedded in a
    tracking/redirect URL as an encoded parameter)."""
    if not href:
        return None
    decoded = urllib.parse.unquote(href)
    # Direct link
    direct = canonical_job_url(decoded)
    if direct:
        return direct
    # Embedded link, e.g. https://track.swelist.com/click?u=https%3A//simplify.jobs/p/...
    m = SIMPLIFY_JOB_RE.search(decoded)
    if m:
        return f"https://simplify.jobs/p/{m.group(1).lower()}"
    return None


def resolve_redirect(url, timeout=15):
    """Follow redirects on a tracking URL; return the final simplify URL if it lands there."""
    try:
        resp = _session.head(url, allow_redirects=True, timeout=timeout)
        final = resp.url
        if resp.status_code >= 400:
            resp = _session.get(url, allow_redirects=True, timeout=timeout, stream=True)
            final = resp.url
        return simplify_url_from_href(final)
    except requests.RequestException as e:
        log.debug("redirect resolve failed for %s: %s", url, e)
        return None


def _company_before_anchor(anchor):
    """Company name preceding the anchor in the same block, e.g. '<b>Acme:</b> <a>Eng</a>'.

    Falls back to the previous sibling block's text if it ends with a colon.
    """
    parent = anchor.parent
    if not parent:
        return ""
    pieces = []
    for sib in anchor.previous_siblings:
        txt = sib.get_text(" ", strip=True) if isinstance(sib, Tag) else str(sib)
        pieces.insert(0, txt)
    text = " ".join(p for p in pieces if p).strip()
    if text:
        return text.rstrip(":").strip()
    # Previous block-level sibling (e.g. <p><b>Acme:</b></p><p><a>…</a></p>)
    prev = parent.find_previous_sibling()
    hops = 0
    while prev is not None and hops < 3:
        txt = prev.get_text(" ", strip=True)
        if txt:
            if txt.rstrip().endswith(":"):
                return txt.rstrip().rstrip(":").strip()
            break
        prev = prev.find_previous_sibling()
        hops += 1
    return ""


def _looks_like_job_line(anchor):
    """Heuristic: anchor sitting in a 'Company: link' line or list item."""
    return bool(_company_before_anchor(anchor))


def extract_jobs_from_email(html, resolve_links=True):
    """Return [{company, title, url}] for every Simplify job link in the email."""
    soup = BeautifulSoup(html or "", "html.parser")
    jobs = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "#")):
            continue
        url = simplify_url_from_href(href)
        if url is None and resolve_links and _looks_like_job_line(a):
            resolved = resolve_redirect(href)
            if resolved:
                url = resolved
        if url is None or url in seen:
            continue
        seen.add(url)
        company = _company_before_anchor(a)
        title = a.get_text(" ", strip=True)
        jobs.append({"company": company, "title": title, "url": url})
    return jobs


def _locations_usa(locations):
    """True if any job location is in the USA."""
    for loc in locations or []:
        country = (loc.get("country") or "").strip().lower()
        value = (loc.get("value") or "").strip().lower()
        if country in US_COUNTRIES:
            return True
        if value in US_REGIONS or value in US_COUNTRIES:
            return True
        if re.search(r"\b(usa|u\.s\.a?|united states)\b", value):
            return True
    return False


def fetch_job(url, timeout=25):
    """Scrape one simplify.jobs page. Returns a dict of extracted fields."""
    out = {
        "title": None,
        "company": None,
        "sponsors_h1b": None,
        "no_h1b_tag": False,
        "location_text": "",
        "in_usa": False,
        "error": None,
    }
    try:
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        out["error"] = f"fetch failed: {e}"
        return out

    m = NEXT_DATA_RE.search(html)
    if not m:
        out["error"] = "no __NEXT_DATA__ on page"
        # still record badge text if rendered
        out["no_h1b_tag"] = bool(NO_H1B_BADGE_RE.search(html))
        return out

    try:
        data = json.loads(m.group(1))
        posting = data["props"]["pageProps"]["jobPosting"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        out["error"] = f"unexpected page JSON: {e}"
        out["no_h1b_tag"] = bool(NO_H1B_BADGE_RE.search(html))
        return out

    company = ((posting.get("job") or {}).get("company") or {})
    job_flag = posting.get("sponsors_h1b")
    eff_flag = job_flag if job_flag is not None else company.get("sponsors_h1b")
    badge_rendered = bool(NO_H1B_BADGE_RE.search(html))

    locations = posting.get("locations") or []
    loc_text = "; ".join(l.get("value", "") for l in locations if l.get("value"))

    out.update(
        {
            "title": posting.get("title"),
            "company": company.get("name"),
            "sponsors_h1b": eff_flag,
            "no_h1b_tag": (eff_flag is False) or badge_rendered,
            "location_text": loc_text,
            "in_usa": _locations_usa(locations),
        }
    )
    return out


def fetch_jobs(urls, workers=8):
    """Fetch many job pages concurrently. Returns {url: fetch_job_result}."""
    results = {}
    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, res in zip(urls, pool.map(fetch_job, urls)):
            results[url] = res
    return results
