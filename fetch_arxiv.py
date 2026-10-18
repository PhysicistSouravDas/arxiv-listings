#!/usr/bin/env python3
"""Download today's new arXiv listings (with abstracts) and save them as JSON.

Plain Python, standard library only. Runs in GitHub Actions; no API keys.

Output
------
latest.json                       always rewritten unless today's good copy exists
archive/<listing_date>.json       written only when status == "ok"

latest.json schema
------------------
{
  "listing_date":   "2026-10-15",          # arXiv's announcement label (feed pubDate)
  "expected_date":  "2026-10-15",          # date this run expected (UTC date of the run)
  "status":         "ok" | "stale" | "empty" | "partial",
  "fetched_at_utc": "2026-10-15T01:05:31Z",
  "categories":     {"astro-ph.CO": {"http": 200, "items": 37, "pubDate": "..."}, ...},
  "counts":         {"new": 120, "cross": 80, "total": 200},
  "papers": [
    {"id": "2610.16496", "version": "v1", "title": "...", "authors": ["..."],
     "abstract": "...", "categories": ["astro-ph.CO", "gr-qc"],
     "primary_category": "astro-ph.CO", "announce_type": "new",
     "listed_in": ["astro-ph.CO", "gr-qc"],
     "abs_url": "https://arxiv.org/abs/2610.16496",
     "html_url": "https://arxiv.org/html/2610.16496",
     "pdf_url": "https://arxiv.org/pdf/2610.16496"}
  ]
}
"""
from __future__ import annotations

import datetime as dt
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
KEEP_TYPES = {"new", "cross"}          # skip "replace" / "replace-cross"
FEED_URL = "https://rss.arxiv.org/rss/{cat}"
USER_AGENT = "arxiv-listings/1.0 (+https://github.com/PhysicistSouravDas/arxiv-listings)"
SPACING_S = 5                          # pause between category requests
RETRY_WAITS_S = [30, 90, 180]          # backoff after a failed request

ROOT = pathlib.Path(__file__).resolve().parent
LATEST = ROOT / "latest.json"
ARCHIVE = ROOT / "archive"

ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
TYPE_RE = re.compile(r"Announce Type:\s*([\w-]+)", re.I)
ABSTRACT_RE = re.compile(r"Abstract:\s*(.*)", re.S)


def local(tag: str) -> str:
    """Strip an XML namespace: '{ns}creator' -> 'creator'."""
    return tag.rsplit("}", 1)[-1]


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch(url: str) -> tuple[int, bytes]:
    """GET with retries and backoff. Returns (http_status, body)."""
    last_err: Exception | None = None
    for attempt, wait in enumerate([0] + RETRY_WAITS_S):
        if wait:
            print(f"  retry {attempt} in {wait}s ({last_err})", flush=True)
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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


def parse_feed(xml_bytes: bytes, cat: str) -> tuple[str | None, list[dict]]:
    """Parse one arXiv RSS feed. Returns (channel pubDate string, papers)."""
    root = ET.fromstring(xml_bytes)
    channel = next((el for el in root.iter() if local(el.tag) == "channel"), root)
    pub = next((clean(el.text) for el in channel if local(el.tag) == "pubDate"), None)

    papers = []
    for item in (el for el in channel if local(el.tag) == "item"):
        f: dict[str, list[str]] = {}
        for child in item:
            f.setdefault(local(child.tag), []).append(child.text or "")
        desc = (f.get("description") or [""])[0]
        link = clean((f.get("link") or [""])[0])
        guid = clean((f.get("guid") or [""])[0])
        m = ID_RE.search(guid) or ID_RE.search(desc) or ID_RE.search(link)
        if not m:
            continue
        ann = clean((f.get("announce_type") or [""])[0]).lower()
        if not ann:
            t = TYPE_RE.search(desc)
            ann = t.group(1).lower() if t else "unknown"
        a = ABSTRACT_RE.search(desc)
        abstract = clean(a.group(1) if a else desc)
        creators = " , ".join(f.get("creator") or [])
        authors = [clean(x) for x in re.split(r",|\band\b", creators) if clean(x)]
        cats = [clean(c) for c in f.get("category") or [] if clean(c)]
        pid = m.group(1)
        papers.append({
            "id": pid,
            "version": m.group(2) or "",
            "title": clean((f.get("title") or [""])[0]),
            "authors": authors,
            "abstract": abstract,
            "categories": cats,
            "primary_category": cats[0] if cats else cat,
            "announce_type": ann,
            "listed_in": [cat],
            "abs_url": f"https://arxiv.org/abs/{pid}",
            "html_url": f"https://arxiv.org/html/{pid}",
            "pdf_url": f"https://arxiv.org/pdf/{pid}",
        })
        if not pub and f.get("pubDate"):
            pub = clean(f["pubDate"][0])   # fall back to the item's own date
    return pub, papers


def pub_to_date(pub: str | None) -> str | None:
    if not pub:
        return None
    try:
        return parsedate_to_datetime(pub).date().isoformat()
    except (TypeError, ValueError):
        return None


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    expected = now.date().isoformat()   # the 01:05/01:25 UTC run expects today's label

    if LATEST.exists():
        try:
            old = json.loads(LATEST.read_text())
            if old.get("listing_date") == expected and old.get("status") == "ok":
                print(f"latest.json already holds a good {expected} listing; nothing to do.")
                return 0
        except json.JSONDecodeError:
            pass

    merged: dict[str, dict] = {}
    cat_info: dict[str, dict] = {}
    dates: set[str] = set()
    for i, cat in enumerate(CATEGORIES):
        if i:
            time.sleep(SPACING_S)
        print(f"Fetching {cat} ...", flush=True)
        code, body = fetch(FEED_URL.format(cat=cat))
        info = {"http": code, "items": 0, "pubDate": None}
        if code == 200 and body:
            try:
                pub, papers = parse_feed(body, cat)
            except ET.ParseError as e:
                print(f"  parse error: {e}", flush=True)
                pub, papers = None, []
            info["pubDate"] = pub
            d = pub_to_date(pub)
            if d:
                dates.add(d)
            kept = [p for p in papers if p["announce_type"] in KEEP_TYPES]
            info["items"] = len(kept)
            for p in kept:
                if p["id"] in merged:
                    merged[p["id"]]["listed_in"].append(cat)
                else:
                    merged[p["id"]] = p
        cat_info[cat] = info
        print(f"  http={code} items={info['items']} pubDate={info['pubDate']}", flush=True)

    ok_cats = [c for c, v in cat_info.items() if v["http"] == 200]
    listing_date = max(dates) if dates else None
    if not ok_cats:
        print("All category requests failed.", file=sys.stderr)
        status = "failed"
    elif not merged:
        status = "empty"
    elif listing_date != expected:
        status = "stale"
    elif len(ok_cats) < len(CATEGORIES):
        status = "partial"
    else:
        status = "ok"

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
            if old.get("listing_date") == expected and old.get("status") in ("ok", "partial"):
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
