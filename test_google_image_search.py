import tempfile
import unittest
import urllib.parse
from pathlib import Path

import google_image_search as google


FIXTURE = """
<html><body>
<a href="/imgres?q=angel&amp;imgurl=https%3A%2F%2Fmedia.getty.edu%2Fangel.jpg&amp;imgrefurl=https%3A%2F%2Fwww.getty.edu%2Fart%2Fangel&amp;docid=page1&amp;tbnid=image1&amp;w=5000&amp;h=4000">
  <div><img alt="The Angel" src="https://encrypted-tbn0.gstatic.com/one.jpg"></div>
</a>
<a href="/imgres?q=angel&amp;imgurl=https%3A%2F%2Fimages.metmuseum.org%2Fsmall.jpg&amp;imgrefurl=https%3A%2F%2Fwww.metmuseum.org%2Fart%2Fcollection%2Fsearch%2F1&amp;tbnid=image2&amp;w=2000&amp;h=2000">
  <img alt="Small angel" src="https://encrypted-tbn0.gstatic.com/two.jpg">
</a>
</body></html>
"""


class GoogleImageSearchTests(unittest.TestCase):
    def test_parser_extracts_urls_dimensions_and_thumbnail(self):
        results = google.parse_google_image_results(FIXTURE)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "The Angel")
        self.assertEqual(results[0]["width"], 5000)
        self.assertEqual(results[0]["height"], 4000)
        self.assertEqual(results[0]["google_id"], "image1")
        self.assertIn("encrypted-tbn0", results[0]["thumb_url"])

    def test_parser_identifies_google_verification(self):
        with self.assertRaises(google.GoogleVerificationRequired):
            google.parse_google_image_results("<div class='g-recaptcha'>unusual traffic</div>")

    def test_custom_stages_use_lower_supported_retrieval_presets(self):
        url = google.google_stage_url("angel", ["getty", "met"], 16)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(params["tbs"], ["isz:lt,islt:15mp"])
        self.assertIn("site:getty.edu", params["q"][0])
        self.assertIn("site:metmuseum.org", params["q"][0])

    def test_normalization_enforces_actual_requested_megapixels(self):
        raw = google.parse_google_image_results(FIXTURE)
        results = google.normalize_stage_results(
            raw, ["getty", "met"], 9, {"getty": "Getty", "met": "Met"},
        )
        self.assertEqual([item["source"] for item in results], ["getty"])
        self.assertEqual(results[0]["pixel_count"], 20_000_000)

    def test_stage_results_are_cached(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            return FIXTURE

        with tempfile.TemporaryDirectory() as directory:
            kwargs = dict(
                query="angel", source_names=["getty", "met"], requested_mp=4,
                source_labels={"getty": "Getty", "met": "Met"},
                cache_dir=Path(directory), fetcher=fetcher,
            )
            first = google.search_google_stage(**kwargs)
            second = google.search_google_stage(**kwargs)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
