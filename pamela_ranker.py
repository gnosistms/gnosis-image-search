"""Apply the refreshed PAMELA tournament criterion vector to image results."""

from __future__ import annotations

from pathlib import Path
import os

import numpy as np

from semantic_embeddings import image_vectors


HERE = Path(__file__).resolve().parent
MODEL_PATH = Path(os.environ.get("SEARCH_AXIS_MODEL") or
                  HERE / "data" / "beauty-tournament" /
                  "axis-ranking-model-siglip2-base-patch16-256.npz")
PAMELA_EMBEDDINGS = Path(os.environ.get("SEARCH_PAMELA_EMBEDDINGS") or
                         HERE / "data" / "pamela" /
                         "siglip2-base-patch16-256.npz")
_VECTOR = None
_BOOK_ARTIFACT_AXIS = None
_CENTER = 0.0
_SCALE = 1.0

# Calibrated against museum-search images. Scores below this point are usually
# covers, bindings, spines, or mostly-text title pages; the short transition
# preserves illustrated manuscript leaves and prints.
BOOK_ARTIFACT_MIDPOINT = -0.0466
BOOK_ARTIFACT_TEMPERATURE = 0.0113


def _load() -> bool:
    global _VECTOR, _BOOK_ARTIFACT_AXIS, _CENTER, _SCALE
    if _VECTOR is not None:
        return True
    try:
        learned = np.load(MODEL_PATH, allow_pickle=False)
        vector = np.asarray(learned["combined_vector"], dtype="float32")
        axis_names = np.asarray(learned["axis_names"]).tolist()
        axis_index = axis_names.index("artwork_over_book_artifact")
        _BOOK_ARTIFACT_AXIS = np.asarray(
            learned["axes"][axis_index], dtype="float32",
        )
        archive = np.load(PAMELA_EMBEDDINGS, allow_pickle=False)
        reference = np.asarray(archive["vectors"], dtype="float32") @ vector
        _VECTOR = vector
        _CENTER = float(np.median(reference))
        # A robust scale keeps a few extreme images from flattening the useful range.
        _SCALE = max(float(np.quantile(reference, .75) - np.quantile(reference, .25)) / 1.349, 1e-6)
        return True
    except (OSError, KeyError, ValueError):
        return False


def _book_artifact_retention(axis_score: float) -> float:
    """Return a soft 0..1 artwork-retention gate for book-like surfaces."""
    z = max(-8.0, min(
        8.0, (axis_score - BOOK_ARTIFACT_MIDPOINT) / BOOK_ARTIFACT_TEMPERATURE,
    ))
    return float(1.0 / (1.0 + np.exp(-z)))


def add_pamela_scores(items: list[dict]) -> None:
    if not items or not _load():
        return
    vectors = image_vectors(items)
    for item in items:
        vector = vectors.get(item.get("id"))
        if vector is None or vector.shape != _VECTOR.shape:
            continue
        raw = float(vector @ _VECTOR)
        z = max(-8.0, min(8.0, (raw - _CENTER) / _SCALE))
        base_score = float(1.0 / (1.0 + np.exp(-z)))
        artifact_axis_score = float(vector @ _BOOK_ARTIFACT_AXIS)
        retention = _book_artifact_retention(artifact_axis_score)
        item["pamela_raw_score"] = round(raw, 6)
        item["pamela_base_score"] = round(base_score, 6)
        item["book_artifact_axis_score"] = round(artifact_axis_score, 6)
        item["artwork_retention"] = round(retention, 6)
        item["pamela_score"] = round(base_score * retention, 6)
