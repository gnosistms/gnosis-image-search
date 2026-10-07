import contextlib
import unittest
from unittest.mock import patch

import numpy as np

import semantic_embeddings


class FakeTorch:
    @staticmethod
    def inference_mode():
        return contextlib.nullcontext()


class FakeTensor:
    def __init__(self, array):
        self.array = np.asarray(array, dtype="float32")

    def to(self, _device):
        return self

    def float(self):
        return self

    def norm(self, dim, keepdim):
        return FakeTensor(np.linalg.norm(self.array, axis=dim, keepdims=keepdim))

    def clamp_min(self, value):
        return FakeTensor(np.maximum(self.array, value))

    def __itruediv__(self, other):
        self.array = self.array / other.array
        return self

    def __getitem__(self, index):
        return FakeTensor(self.array[index])

    def cpu(self):
        return self

    def numpy(self):
        return self.array


class FakeModel:
    def get_text_features(self, **_inputs):
        return FakeTensor([[3.0, 4.0]])


class TextVectorTests(unittest.TestCase):
    def test_siglip_text_is_padded_to_64_tokens(self):
        # transformers 4.x does not pad SigLIP 2 text at all without an
        # explicit max_length, which skews query vectors on Intel Macs.
        calls = []

        def processor(**kwargs):
            calls.append(kwargs)
            return {"input_ids": FakeTensor([[1, 2, 3]])}

        with patch.multiple(semantic_embeddings, _MODEL=FakeModel(), _PROCESSOR=processor,
                            _TORCH=FakeTorch(), TEXT_MAX_LENGTH=64), \
                patch.object(semantic_embeddings, "_TEXT_CACHE", semantic_embeddings.OrderedDict()):
            vector = semantic_embeddings.text_vector("Anubis")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["padding"], "max_length")
        self.assertEqual(calls[0]["max_length"], 64)
        self.assertTrue(calls[0]["truncation"])
        np.testing.assert_allclose(vector, [.6, .8])

    def test_text_max_length_matches_siglip_model_kind(self):
        if semantic_embeddings.MODEL_KIND == "siglip":
            self.assertEqual(semantic_embeddings.TEXT_MAX_LENGTH, 64)

    def test_installed_siglip_processor_pads_short_queries_to_64_tokens(self):
        if semantic_embeddings.MODEL_KIND != "siglip":
            self.skipTest("Not using SigLIP")
        try:
            from transformers.models.siglip.processing_siglip import SiglipProcessor
            processor = SiglipProcessor.from_pretrained(
                semantic_embeddings.MODEL_SOURCE, local_files_only=True,
                **({"cache_dir": semantic_embeddings.MODEL_CACHE_DIR}
                   if semantic_embeddings.MODEL_CACHE_DIR else {}),
            )
        except Exception as exc:
            self.skipTest(f"SigLIP processor unavailable: {type(exc).__name__}")
        for query in ("Anubis", "Saint Peter liberated from prison by an angel"):
            inputs = processor(
                text=[query], padding="max_length", truncation=True,
                max_length=semantic_embeddings.TEXT_MAX_LENGTH, return_tensors="np",
            )
            self.assertEqual(inputs["input_ids"].shape, (1, 64), query)


if __name__ == "__main__":
    unittest.main()
