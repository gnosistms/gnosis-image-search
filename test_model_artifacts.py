import unittest

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


if __name__ == "__main__":
    unittest.main()
