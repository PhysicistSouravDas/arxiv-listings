"""Offline tests of the HTML (primary) and RSS (fallback) parsers.

Run:  python -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fetch_arxiv as fa  # noqa: E402

HTML_SAMPLE = """<!DOCTYPE html><html><body><div id='dlpage'>
<h1>Cosmology and Nongalactic Astrophysics</h1>
<div class='paging'>Total of 3 entries</div>
<h3>Showing new listings for Thursday, 15 October 2026</h3>
<dl id='articles'>
<h3>New submissions (showing 1 of 1 entries)</h3>
<dt>
  <a name='item1'>[1]</a>
  <a href ="/abs/2610.16496" title="Abstract" id="2610.16496">arXiv:2610.16496</a>
  [<a href="/pdf/2610.16496" title="Download PDF">pdf</a>, <a href="https://arxiv.org/html/2610.16496v1" title="View HTML">html</a>]
</dt>
<dd>
  <div class='meta'>
    <div class='list-title mathjax'><span class='descriptor'>Title:</span>
      Sensitivity of calibration-free DESI DR2 BAO--supernova constraints to a distance-duality tilt
    </div>
    <div class='list-authors'><a href="https://arxiv.org/a/fay_s_1">St&#233;phane Fay</a>, <a href="#">Mikel Artola</a>, <a href="#">Leandros Perivolaropoulos</a></div>
    <div class='list-comments mathjax'><span class='descriptor'>Comments:</span>
      15 pages, 5 figures
    </div>
    <div class='list-subjects'><span class='descriptor'>Subjects:</span>
      <span class="primary-subject">Cosmology and Nongalactic Astrophysics (astro-ph.CO)</span>; General Relativity and Quantum Cosmology (gr-qc)
    </div>
    <p class='mathjax'>
      We test whether a violation of the cosmic distance duality relation $\\eta(z)$ weakens the preference.
    </p>
  </div>
</dd>
</dl>
<dl id='articles'>
<h3>Cross submissions (showing 1 of 1 entries)</h3>
<dt><a name='item2'>[2]</a> <a href ="/abs/2610.16574" title="Abstract" id="2610.16574">arXiv:2610.16574</a> (cross-list from stat.ML) [<a href="/pdf/2610.16574">pdf</a>]</dt>
<dd><div class='meta'>
  <div class='list-title mathjax'><span class='descriptor'>Title:</span> NFDiag: High-dimensional MCMC Convergence Diagnosis with Normalizing Flows</div>
  <div class='list-authors'><a href="#">Kutay Nazl&#305;</a>, <a href="#">Elena Sellentin</a></div>
  <div class='list-subjects'><span class='descriptor'>Subjects:</span> <span class="primary-subject">Machine Learning (stat.ML)</span>; Cosmology and Nongalactic Astrophysics (astro-ph.CO)</div>
  <p class='mathjax'>A convergence diagnostic.</p>
</div></dd>
</dl>
<dl id='articles'>
<h3>Replacement submissions (showing 1 of 1 entries)</h3>
<dt><a name='item3'>[3]</a> <a href ="/abs/2601.00001" title="Abstract" id="2601.00001">arXiv:2601.00001</a> (replaced)</dt>
<dd><div class='meta'><div class='list-title mathjax'><span class='descriptor'>Title:</span> Old</div>
<div class='list-authors'><a href="#">A. Author</a></div>
<div class='list-subjects'><span class='descriptor'>Subjects:</span> <span class="primary-subject">Cosmology and Nongalactic Astrophysics (astro-ph.CO)</span></div>
<p class='mathjax'>Old.</p></div></dd>
</dl>
</div></body></html>""".encode()

RSS_SAMPLE = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <title>astro-ph.CO updates on arXiv.org</title>
    <pubDate>Thu, 15 Oct 2026 00:00:00 -0400</pubDate>
    <item>
      <title>A paper</title>
      <link>https://arxiv.org/abs/2610.16496</link>
      <description>arXiv:2610.16496v1 Announce Type: new
Abstract: We test whether   a violation holds.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.16496v1</guid>
      <category>astro-ph.CO</category>
      <category>gr-qc</category>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>A. One, B. Two</dc:creator>
    </item>
  </channel>
</rss>"""


class HtmlTest(unittest.TestCase):
    def test_parse_html(self):
        date, papers = fa.parse_html(HTML_SAMPLE, "astro-ph.CO")
        self.assertEqual(date, "2026-10-15")
        self.assertEqual([p["announce_type"] for p in papers], ["new", "cross", "replace"])
        p = papers[0]
        self.assertEqual(p["id"], "2610.16496")
        self.assertTrue(p["title"].startswith("Sensitivity of calibration-free DESI DR2"))
        self.assertEqual(p["authors"], ["Stéphane Fay", "Mikel Artola", "Leandros Perivolaropoulos"])
        self.assertEqual(p["comments"], "15 pages, 5 figures")
        self.assertEqual(p["categories"], ["astro-ph.CO", "gr-qc"])
        self.assertIn("$\\eta(z)$", p["abstract"])
        c = papers[1]
        self.assertEqual(c["id"], "2610.16574")
        self.assertEqual(c["primary_category"], "stat.ML")
        self.assertEqual(c["authors"], ["Kutay Nazlı", "Elena Sellentin"])

    def test_old_date_layout(self):
        self.assertEqual(fa.parse_listing_date("New submissions for Mon, 19 Oct 26"), "2026-10-19")


class RssTest(unittest.TestCase):
    def test_parse_feed(self):
        date, papers = fa.parse_feed(RSS_SAMPLE, "astro-ph.CO")
        self.assertEqual(date, "2026-10-15")
        self.assertEqual(papers[0]["id"], "2610.16496")
        self.assertEqual(papers[0]["announce_type"], "new")
        self.assertEqual(papers[0]["abstract"], "We test whether a violation holds.")
        self.assertEqual(papers[0]["categories"], ["astro-ph.CO", "gr-qc"])


class StaleCacheTest(unittest.TestCase):
    """20 Oct 2026: one address served Monday's cached page, a browser saw Tuesday's."""

    def setUp(self):
        self._fetch, self._sleep = fa.fetch, fa.time.sleep
        fa.time.sleep = lambda s: None
        monday = HTML_SAMPLE.replace(b"Thursday, 15 October 2026", b"Monday, 19 October 2026")
        tuesday = HTML_SAMPLE.replace(b"Thursday, 15 October 2026", b"Tuesday, 20 October 2026")
        self.calls = []

        def fake_fetch(url, retry=True):
            self.calls.append(url)
            return 200, (tuesday if url.startswith("https://export.arxiv.org") else monday)
        fa.fetch = fake_fetch

    def tearDown(self):
        fa.fetch, fa.time.sleep = self._fetch, self._sleep

    def test_newest_copy_wins(self):
        info, kept = fa.get_category("astro-ph.CO", "2026-10-20")
        self.assertEqual(info["date"], "2026-10-20")
        self.assertEqual(info["variant"], 1)               # export.arxiv.org
        self.assertEqual(len(self.calls), 2)               # stopped once today's copy arrived
        self.assertEqual([t["date"] for t in info["tried"]], ["2026-10-19", "2026-10-20"])
        self.assertEqual(len(kept), 2)                     # new + cross, replacement dropped

    def test_all_stale_keeps_newest(self):
        fa.fetch = lambda url, retry=True: (self.calls.append(url) or (200, HTML_SAMPLE))
        info, _ = fa.get_category("astro-ph.CO", "2026-10-20")
        self.assertEqual(info["date"], "2026-10-15")
        self.assertEqual(len(self.calls), len(fa.HTML_URLS))


if __name__ == "__main__":
    unittest.main()

