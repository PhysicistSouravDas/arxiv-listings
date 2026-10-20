#!/usr/bin/env python3
"""Download today's new arXiv listings (with abstracts) and save them as JSON.

Plain Python, standard library only. Runs in GitHub Actions; no API keys.

Sources, per category
---------------------
1. The category's "new" page (primary), tried at several addresses:
   https://arxiv.org/list/<cat>/new, https://export.arxiv.org/list/<cat>/new,
   a unique never-cached address, and .../new?skip=0&show=2000.
   Updated at each announcement (20:00 US Eastern = 05:30 IST in summer
   time, 06:30 IST in winter time), with full abstracts. arXiv's cache can
   keep serving yesterday's copy of one address for hours (seen on
   20 Oct 2026), so the newest copy wins.
2. https://rss.arxiv.org/rss/<cat>                     (fallback)
   Only used if the HTML page cannot be fetched or parsed. NOTE: arXiv
   regenerates RSS at midnight US Eastern (~09:30-10:30 IST), so in the
   early IST morning it usually still holds the previous listing and will
   be marked stale. That is the honest outcome, not a bug.

Output
------
latest.json                       written every run (unless today's good copy exists)
archive/<listing_date>.json       written only when status is "ok" or "partial"

latest.json schema
------------------
{
  "listing_date":   "2026-10-19",          # arXiv's announcement label, newest good category
  "expected_date":  "2026-10-19",          # UTC date of this run = the label expected today
  "status":         "ok" | "partial" | "stale" | "empty" | "failed",
  "fetched_at_utc": "...",
  "categories":     {"astro-ph.CO": {"source": "html", "http": 200, "date": "2026-10-19",
                                     "new": 24, "cross": 13, "fresh": true}, ...},
  "counts":         {"new": 120, "cross": 80, "total": 200},
  "papers": [
    {"id": "2610.16496", "title": "...", "authors": ["..."], "abstract": "...",
     "comments": "15 pages, 5 figures", "categories": ["astro-ph.CO", "gr-qc"],
     "primary_category": "astro-ph.CO", "announce_type": "new",
     "listed_in": ["astro-ph.CO", "gr-qc"],
     "abs_url": "...", "html_url": "...", "pdf_url": "..."}
  ]
}
status: ok = every category fresh (dated today); partial = some fresh, some not;
stale = none fresh; empty = fresh but no new papers; failed = nothing could be fetched.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

CATEGORIES = [
    "astro-ph.CO",
    "gr-qc",
    "astro-ph.HE",
    "astro-ph.GA",
    "astro-ph.SR",
    "astro-ph.IM",
]
KEEP_TYPES = {"new", "cross"}          # skip replacements
# The same listing page at several addresses. On 20 Oct 2026 GitHub's runners
# kept receiving Monday's cached copy of ".../new?skip=0&show=2000" until at
# least 09:30 IST, while a browser opening ".../new" saw Tuesday's listing.
# Each category tries these in order and stops at the first copy dated today;
# otherwise the newest copy is kept. "tried" in latest.json shows what each gave.
HTML_URLS = [
    "https://arxiv.org/list/{cat}/new",                                   # what a browser opens
    "https://export.arxiv.org/list/{cat}/new",                            # arXiv's host for scripts
    "https://arxiv.org/list/{cat}/new?skip=0&show=2000&nocache={stamp}",  # unique: cannot be cached
    "https://arxiv.org/list/{cat}/new?skip=0&show=2000",                  # the address used until v5
]
FEED_URL = "https://rss.arxiv.org/rss/{cat}"
USER_AGENT = "arxiv-listings/1.1 (+https://github.com/PhysicistSouravDas/arxiv-listings)"
SPACING_S = 5                          # pause between requests
RETRY_WAITS_S = [30, 90, 180]          # backoff after a failed request

ROOT = pathlib.Path(__file__).resolve().parent
LATEST = ROOT / "latest.json"
ARCHIVE = ROOT / "archive"

ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
CAT_CODE_RE = re.compile(r"\(([a-z][a-z\-]*(?:\.[A-Za-z\-]+)?)\)")


# ----------------------------------------------------------------- helpers
def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_tags(fragment: str) -> str:
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")))


def fetch(url: str, retry: bool = True) -> tuple[int, bytes]:
    """GET with retries and backoff (one attempt if retry=False). Returns (http_status, body)."""
    last_err: Exception | None = None
    for attempt, wait in enumerate([0] + (RETRY_WAITS_S if retry else [])):
        if wait:
            print(f"  retry {attempt} in {wait}s ({last_err})", flush=True)
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Cache-Control": "no-cache",   # ask caches for a fresh copy
                "Pragma": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (429, 500, 502, 503, 504):
                return e.code, b""
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
    print(f"  giving up on {url}: {last_err}", flush=True)
    return 0, b""


def paper(pid: str, title: str, authors: list[str], abstract: str, comments: str,
          cats: list[str], ann: str, listed: str) -> dict:
    return {
        "id": pid,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "comments": comments,
        "categories": cats,
        "primary_category": cats[0] if cats else listed,
        "announce_type": ann,
        "listed_in": [listed],
        "abs_url": f"https://arxiv.org/abs/{pid}",
        "html_url": f"https://arxiv.org/html/{pid}",
        "pdf_url": f"https://arxiv.org/pdf/{pid}",
    }


def split_authors(text: str) -> list[str]:
    return [a for a in (clean(x) for x in re.split(r",|\band\b", text)) if a]


# ------------------------------------------------------------- HTML parser
DATE_PATTERNS = [
    # "Showing new listings for Monday, 19 October 2026"
    r"new listings for\s+(?:[A-Za-z]+,\s*)?(\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
    # older layout: "New submissions for Mon, 19 Oct 26"
    r"New submissions for\s+(?:[A-Za-z]+,\s*)?(\d{1,2}\s+[A-Za-z]+\s+\d{2,4})",
]


def parse_listing_date(text: str) -> str | None:
    for pat in DATE_PATTERNS:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        raw = clean(m.group(1))
        for fmt in ("%d %B %Y", "%d %b %Y", "%d %b %y", "%d %B %y"):
            try:
                return dt.datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def div_text(dd: str, cls: str) -> str:
    m = re.search(r"<div\s+class\s*=\s*['\"][^'\"]*\b%s\b[^'\"]*['\"][^>]*>(.*?)</div>" % re.escape(cls),
                  dd, re.S | re.I)
    return m.group(1) if m else ""


def drop_descriptor(text: str) -> str:
    return re.sub(r"^(Title|Authors|Comments|Subjects|Journal-ref)\s*:\s*", "", text, flags=re.I)


def parse_html(page: bytes, cat: str) -> tuple[str | None, list[dict]]:
    """Parse https://arxiv.org/list/<cat>/new. Returns (listing date, papers incl. type)."""
    text = page.decode("utf-8", errors="replace")
    date = parse_listing_date(strip_tags(text))

    heads = list(re.finditer(r"<h3[^>]*>(.*?)</h3>", text, re.S | re.I))
    papers: list[dict] = []
    for i, h in enumerate(heads):
        label = strip_tags(h.group(1)).lower()
        if "replacement" in label:
            ann = "replace"
        elif "cross" in label:
            ann = "cross"
        elif "new submission" in label:
            ann = "new"
        else:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        section = text[h.end():end]
        for m in re.finditer(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", section, re.S | re.I):
            dt_html, dd = m.group(1), m.group(2)
            idm = re.search(r"/abs/(\d{4}\.\d{4,5})", dt_html) or ID_RE.search(strip_tags(dt_html))
            if not idm:
                continue
            title = drop_descriptor(strip_tags(div_text(dd, "list-title")))
            authors = split_authors(drop_descriptor(strip_tags(div_text(dd, "list-authors"))))
            comments = drop_descriptor(strip_tags(div_text(dd, "list-comments")))
            subjects = strip_tags(div_text(dd, "list-subjects"))
            cats = CAT_CODE_RE.findall(subjects)
            am = re.search(r"<p[^>]*class\s*=\s*['\"][^'\"]*mathjax[^'\"]*['\"][^>]*>(.*?)</p>", dd, re.S | re.I) \
                or re.search(r"<p[^>]*>(.*?)</p>", dd, re.S | re.I)
            abstract = strip_tags(am.group(1)) if am else ""
            papers.append(paper(idm.group(1), title, authors, abstract, comments, cats, ann, cat))
    return date, papers


# -------------------------------------------------------------- RSS parser
def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml_bytes: bytes, cat: str) -> tuple[str | None, list[dict]]:
    """Parse one arXiv RSS feed (fallback). Returns (date, papers incl. type)."""
    root = ET.fromstring(xml_bytes)
    channel = next((el for el in root.iter() if local(el.tag) == "channel"), root)
    pub = next((clean(el.text) for el in channel if local(el.tag) == "pubDate"), None)
    papers = []
    for item in (el for el in channel if local(el.tag) == "item"):
        f: dict[str, list[str]] = {}
        for child in item:
            f.setdefault(local(child.tag), []).append(child.text or "")
        desc = (f.get("description") or [""])[0]
        guid = clean((f.get("guid") or [""])[0])
        link = clean((f.get("link") or [""])[0])
        m = ID_RE.search(guid) or ID_RE.search(desc) or ID_RE.search(link)
        if not m:
            continue
        ann = clean((f.get("announce_type") or [""])[0]).lower()
        if not ann:
            t = re.search(r"Announce Type:\s*([\w-]+)", desc, re.I)
            ann = t.group(1).lower() if t else "unknown"
        a = re.search(r"Abstract:\s*(.*)", desc, re.S)
        cats = [clean(c) for c in f.get("category") or [] if clean(c)]
        papers.append(paper(m.group(1), clean((f.get("title") or [""])[0]),
                            split_authors(" , ".join(f.get("creator") or [])),
                            clean(a.group(1) if a else desc), "", cats, ann, cat))
        if not pub and f.get("pubDate"):
            pub = clean(f["pubDate"][0])
    date = None
    if pub:
        try:
            date = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            pass
    return date, papers


# -------------------------------------------------------------------- main
def get_html(cat: str, expected: str, order: list[int], limit: int) -> tuple[dict, list[dict]]:
    """Try up to `limit` addresses of the category's "new" page, in `order`.
    Stops at the first copy dated `expected`; otherwise keeps the newest copy."""
    best = {"variant": None, "http": 0, "date": None, "papers": []}
    tried = []
    stamp = int(time.time())
    for n, k in enumerate(order[:limit]):
        if n:
            time.sleep(SPACING_S)
        url = HTML_URLS[k].format(cat=cat, stamp=stamp)
        code, body = fetch(url, retry=(n == 0))
        date, papers = parse_html(body, cat) if code == 200 and body else (None, [])
        tried.append({"url": url, "http": code, "date": date, "entries": len(papers)})
        print(f"  [{k}] {url} -> http={code} date={date} entries={len(papers)}", flush=True)
        if best["variant"] is None or (date or "", len(papers)) > (best["date"] or "", len(best["papers"])):
            best = {"variant": k, "http": code, "date": date, "papers": papers}
        if date == expected:
            break
    best["tried"] = tried
    return best, best.pop("papers")


def get_category(cat: str, expected: str, order: list[int] | None = None,
                 limit: int | None = None) -> tuple[dict, list[dict]]:
    """Try the HTML page's addresses, then RSS. Returns (info, kept papers)."""
    order = order if order is not None else list(range(len(HTML_URLS)))
    limit = limit if limit is not None else len(order)
    best, papers = get_html(cat, expected, order, limit)
    info = {"source": None, "http": best["http"], "date": None, "new": 0, "cross": 0,
            "variant": best["variant"], "tried": best["tried"]}
    date = best["date"]
    if best["http"] == 200 and (date or papers):
        info["source"] = "html"
    if not date or not papers:
        if info["source"] == "html" and date and not papers:
            pass   # a dated page with no entries: genuinely empty, keep it
        else:
            print(f"  HTML gave date={date} papers={len(papers)}; trying RSS", flush=True)
            time.sleep(SPACING_S)
            rcode, rbody = fetch(FEED_URL.format(cat=cat))
            if rcode == 200 and rbody:
                try:
                    date, papers = parse_feed(rbody, cat)
                    info.update(source="rss", http=rcode)
                except ET.ParseError as e:
                    print(f"  RSS parse error: {e}", flush=True)
    kept = [p for p in papers if p["announce_type"] in KEEP_TYPES]
    info["date"] = date
    info["new"] = sum(p["announce_type"] == "new" for p in kept)
    info["cross"] = sum(p["announce_type"] == "cross" for p in kept)
    return info, kept


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    expected = now.date().isoformat()   # runs at 01:05-01:45 UTC expect today's label

    if LATEST.exists():
        try:
            old = json.loads(LATEST.read_text())
            if old.get("listing_date") == expected and old.get("status") == "ok":
                print(f"latest.json already holds a good {expected} listing; nothing to do.")
                return 0
        except json.JSONDecodeError:
            pass

    cat_info: dict[str, dict] = {}
    per_cat: dict[str, list[dict]] = {}
    # The first category tries every address. Later ones start with the address
    # that worked; if none gave today's copy (e.g. before the announcement),
    # later categories try only one address, to keep requests to arXiv low.
    order = list(range(len(HTML_URLS)))
    limit = len(order)
    for i, cat in enumerate(CATEGORIES):
        if i:
            time.sleep(SPACING_S)
        print(f"Fetching {cat} ...", flush=True)
        info, kept = get_category(cat, expected, order, limit)
        info["fresh"] = info["date"] == expected
        if info["fresh"] and info["source"] == "html":
            order = [info["variant"]] + [k for k in order if k != info["variant"]]
            limit = len(order)
        elif i == 0:
            limit = 1
        cat_info[cat], per_cat[cat] = info, kept
        print(f"  source={info['source']} http={info['http']} date={info['date']} "
              f"new={info['new']} cross={info['cross']} fresh={info['fresh']}", flush=True)
        for p in kept[:2]:
            print(f"    e.g. {p['id']} [{p['announce_type']}] {p['title'][:80]}", flush=True)

    fetched = [c for c, v in cat_info.items() if v["source"]]
    fresh = [c for c, v in cat_info.items() if v["fresh"]]
    use = fresh or fetched                      # stale output still shows what arXiv served
    merged: dict[str, dict] = {}
    for cat in use:
        for p in per_cat[cat]:
            if p["id"] in merged:
                if cat not in merged[p["id"]]["listed_in"]:
                    merged[p["id"]]["listed_in"].append(cat)
            else:
                merged[p["id"]] = p

    if not fetched:
        status = "failed"
    elif not fresh:
        status = "stale"
    elif not merged:
        status = "empty"
    elif len(fresh) < len(CATEGORIES):
        status = "partial"
    else:
        status = "ok"
    dates = [cat_info[c]["date"] for c in use if cat_info[c]["date"]]
    listing_date = max(dates) if dates else None

    papers = sorted(merged.values(), key=lambda p: (p["announce_type"] != "new", p["id"]))
    out = {
        "listing_date": listing_date,
        "expected_date": expected,
        "status": status,
        "fetched_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categories": cat_info,
        "counts": {
            "new": sum(p["announce_type"] == "new" for p in papers),
            "cross": sum(p["announce_type"] == "cross" for p in papers),
            "total": len(papers),
        },
        "papers": papers,
    }

    # Never overwrite a good listing for today with a worse one.
    if LATEST.exists() and status != "ok":
        try:
            old = json.loads(LATEST.read_text())
            if old.get("listing_date") == expected and old.get("status") in ("ok", "partial") \
                    and not (status == "partial" and old.get("status") == "partial"
                             and len(fresh) > sum(v.get("fresh", False) for v in old.get("categories", {}).values())):
                print(f"Keeping existing {old['status']} copy; this run was {status}.")
                return 0
        except json.JSONDecodeError:
            pass

    LATEST.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    if status in ("ok", "partial") and listing_date:
        ARCHIVE.mkdir(exist_ok=True)
        (ARCHIVE / f"{listing_date}.json").write_text(
            json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"status={status} listing_date={listing_date} counts={out['counts']}")
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())

