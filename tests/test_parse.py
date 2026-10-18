"""Offline test of the RSS parser against a sample in arXiv's RSS format.

Run:  python -m unittest discover -s tests
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fetch_arxiv as fa  # noqa: E402

SAMPLE = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
  <channel>
    <title>astro-ph.CO updates on arXiv.org</title>
    <link>http://rss.arxiv.org/rss/astro-ph.CO</link>
    <pubDate>Thu, 15 Oct 2026 00:00:00 -0400</pubDate>
    <item>
      <title>Sensitivity of calibration-free DESI DR2 BAO--supernova constraints to a distance-duality tilt</title>
      <link>https://arxiv.org/abs/2610.16496</link>
      <description>arXiv:2610.16496v1 Announce Type: new
Abstract: We test whether   a violation of the cosmic distance duality relation weakens the preference.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.16496v1</guid>
      <category>astro-ph.CO</category>
      <category>gr-qc</category>
      <pubDate>Thu, 15 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>St\xc3\xa9phane Fay, Mikel Artola, Leandros Perivolaropoulos</dc:creator>
    </item>
    <item>
      <title>A replaced paper</title>
      <link>https://arxiv.org/abs/2601.00001</link>
      <description>arXiv:2601.00001v3 Announce Type: replace
Abstract: Old.</description>
      <category>astro-ph.CO</category>
      <arxiv:announce_type>replace</arxiv:announce_type>
      <dc:creator>A. Author</dc:creator>
    </item>
  </channel>
</rss>"""


class ParseTest(unittest.TestCase):
    def test_parse(self):
        pub, papers = fa.parse_feed(SAMPLE, "astro-ph.CO")
        self.assertEqual(fa.pub_to_date(pub), "2026-10-15")
        self.assertEqual(len(papers), 2)
        p = papers[0]
        self.assertEqual(p["id"], "2610.16496")
        self.assertEqual(p["version"], "v1")
        self.assertEqual(p["announce_type"], "new")
        self.assertEqual(p["authors"], ["Stéphane Fay", "Mikel Artola", "Leandros Perivolaropoulos"])
        self.assertEqual(p["categories"], ["astro-ph.CO", "gr-qc"])
        self.assertTrue(p["abstract"].startswith("We test whether a violation"))
        self.assertEqual(papers[1]["announce_type"], "replace")


if __name__ == "__main__":
    unittest.main()
