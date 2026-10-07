import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import pamela_ranker
import semantic_embeddings


class ProductionModelArtifactTests(unittest.TestCase):
    def test_base_checkpoint_and_ranker_artifacts_share_embedding_space(self):
        self.assertEqual(
            semantic_embeddings.MODEL_NAME,
            "google/siglip2-base-patch16-256",
        )
        reference = np.load(pamela_ranker.PAMELA_EMBEDDINGS, allow_pickle=False)
        learned = np.load(pamela_ranker.MODEL_PATH, allow_pickle=False)

        self.assertEqual(reference["model"].item(), semantic_embeddings.MODEL_NAME)
        self.assertEqual(learned["embedding_model"].item(), semantic_embeddings.MODEL_NAME)
        self.assertEqual(reference["vectors"].shape, (5077, 768))
        self.assertEqual(learned["combined_vector"].shape, (768,))
        self.assertEqual(learned["axes"].shape[1], 768)



class InstalledModelFolderTests(unittest.TestCase):
    def test_hub_checkpoint_names_are_not_checked_as_folders(self):
        with mock.patch.object(semantic_embeddings, "MODEL_SOURCE", "google/siglip2-base-patch16-256"):
            self.assertEqual(semantic_embeddings._missing_model_files(), [])

    def test_missing_installed_files_make_the_backend_exit_for_redownload(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in semantic_embeddings.REQUIRED_MODEL_FILES:
                if name != "model.safetensors":
                    (Path(folder) / name).write_text("{}")
            with mock.patch.object(semantic_embeddings, "MODEL_SOURCE", folder), \
                    mock.patch.object(semantic_embeddings, "_MODEL", None), \
                    mock.patch.object(semantic_embeddings, "_MODEL_UNAVAILABLE", False), \
                    mock.patch.object(semantic_embeddings.os, "_exit", side_effect=SystemExit) as exit_now, \
                    mock.patch("sys.stderr"):
                self.assertEqual(semantic_embeddings._missing_model_files(), ["model.safetensors"])
                with self.assertRaises(SystemExit):
                    semantic_embeddings._load_model()
            exit_now.assert_called_once_with(semantic_embeddings.MODEL_MISSING_EXIT_CODE)


if __name__ == "__main__":
    unittest.main()
