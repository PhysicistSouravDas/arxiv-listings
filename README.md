# arxiv-listings

Daily snapshot of new arXiv submissions and cross-lists, with abstracts, for:
`astro-ph.CO`, `gr-qc`, `astro-ph.HE`, `astro-ph.GA`, `astro-ph.SR`, `astro-ph.IM`.

A GitHub Action runs `fetch_arxiv.py` (plain Python, standard library only, no API keys) on weekdays:

| Run | UTC | IST |
| --- | --- | --- |
| Main | 01:05 | 06:35 |
| Backup | 01:25 | 06:55 (does nothing if the main run already got a good listing) |

It reads arXiv's official RSS feeds (`https://rss.arxiv.org/rss/<category>`), one request per category, 5 s apart, retrying with backoff on errors.

**No commits are made.** The results are published as assets of a single GitHub Release with the tag `listings`, which the workflow creates on its first run. Each run replaces `latest.json` there and adds a dated copy. The repo's git history stays exactly as you pushed it.

## Output (Release "listings")

- `latest.json`: the most recent run. Check `status` and `listing_date` before using it.
  - `ok`: all six feeds were read, and the listing is dated today (UTC date of the run).
  - `partial`: today's listing, but at least one feed failed.
  - `stale`: arXiv still served an older listing.
  - `empty`: the feeds returned no new papers (e.g. an arXiv holiday).
- `YYYY-MM-DD.json`: a dated copy of every `ok`/`partial` listing.

Each paper has `id`, `version`, `title`, `authors`, `abstract`, `categories`, `primary_category`, `announce_type` (`new`/`cross`), `listed_in`, and `abs_url`/`html_url`/`pdf_url`. Replacements are skipped.

Download URLs (public, no login), as used by the morning brief:

```
https://github.com/PhysicistSouravDas/arxiv-listings/releases/download/listings/latest.json
https://github.com/PhysicistSouravDas/arxiv-listings/releases/download/listings/2026-10-19.json
```

From a terminal: `curl -sL <url> | jq '.status, .counts'`

## Setup (once)

1. Push this folder to the public repo `PhysicistSouravDas/arxiv-listings`.
2. **Actions** tab → "Fetch arXiv listings" → **Run workflow** to test. Outside 06:30–20:00 IST on a weekday, expect `stale` or `empty`; that's normal.
3. Only if the run fails with a permissions (403) error: **Settings → Actions → General → Workflow permissions →** "Read and write permissions" → Save.

## Local test

```sh
python -m unittest discover -s tests   # offline parser test
python fetch_arxiv.py                  # live run, writes latest.json locally (git-ignored)
```

## Notes

- GitHub can start scheduled runs 10–30 min late; the 06:55 backup covers that.
- GitHub pauses scheduled workflows in public repos after 60 days without repository activity. Because this workflow makes no commits, it re-enables itself on every run (`gh workflow enable`). If GitHub ever pauses it anyway, it emails you and one click re-enables it.
- One release can hold up to about 1000 assets (roughly 4 years of weekdays). Old dated files can be deleted from the release page at any time.
- Only arXiv metadata and abstracts are stored; full texts are not.

