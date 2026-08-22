import unittest

from pamela_ranker import _book_artifact_retention


class BookArtifactRetentionTests(unittest.TestCase):
    def test_strong_book_artifact_is_nearly_filtered(self):
        self.assertLess(_book_artifact_retention(-0.09), 0.02)

    def test_borderline_document_surface_is_softly_demoted(self):
        self.assertLess(_book_artifact_retention(-0.07), 0.06)

    def test_depicted_artwork_is_preserved(self):
        self.assertGreater(_book_artifact_retention(0.01), 0.97)


if __name__ == "__main__":
    unittest.main()
