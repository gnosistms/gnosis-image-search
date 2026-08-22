"""On-demand visual similarity over only the current search result set."""

from __future__ import annotations

import io
import math
import threading
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed


try:
    import numpy as np
    from PIL import Image
except ImportError:  # the stdlib server still works; similarity falls back
    np = None
    Image = None


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/126.0 Safari/537.36")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_FEATURES = 512
# Perceptual fingerprints require live image downloads when they were not
# encountered while grouping alternate versions.  Restrict those downloads to
# a semantically nominated pool so a detail-panel request cannot spend several
# minutes waiting on every result in a large search.
MAX_PERCEPTUAL_CANDIDATES = 24
FEATURE_CACHE: OrderedDict[str, dict | None] = OrderedDict()
FEATURE_LOCK = threading.RLock()


def _normalize(vector):
    norm = float(np.linalg.norm(vector)) or 1.0
    return vector.astype("float32") / norm


def compute_feature(image) -> dict:
    """Compact color/structure fingerprint; no source image is retained."""
    image = image.convert("RGB")
    original_width, original_height = image.size
    thumb = image.copy()
    thumb.thumbnail((128, 128))
    rgb = np.asarray(thumb, dtype="float32")
    hist_parts = []
    for channel in range(3):
        hist, _ = np.histogram(rgb[:, :, channel], bins=16, range=(0, 256))
        hist_parts.append(hist.astype("float32"))
    color = _normalize(np.concatenate(hist_parts))

    gray_image = image.convert("L").resize((8, 8))
    gray = _normalize(np.asarray(gray_image, dtype="float32").reshape(-1) + 1.0)
    dhash_image = np.asarray(image.convert("L").resize((9, 8)), dtype="float32")
    dhash = (dhash_image[:, 1:] > dhash_image[:, :-1]).reshape(-1)
    aspect = math.log(max(original_width / max(original_height, 1), 0.05))
    return {"color": color, "gray": gray, "dhash": dhash, "aspect": aspect}


def feature_similarity(first: dict, second: dict) -> float:
    color = float(first["color"] @ second["color"])
    gray = float(first["gray"] @ second["gray"])
    dhash = float((first["dhash"] == second["dhash"]).mean())
    aspect = math.exp(-abs(float(first["aspect"]) - float(second["aspect"])))
    return 0.52 * color + 0.18 * gray + 0.22 * dhash + 0.08 * aspect


def _download_feature(url: str) -> dict | None:
    if not url or Image is None or np is None:
        return None
    with FEATURE_LOCK:
        if url in FEATURE_CACHE:
            FEATURE_CACHE.move_to_end(url)
            return FEATURE_CACHE[url]
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds similarity size limit")
        feature = compute_feature(Image.open(io.BytesIO(data)))
    except Exception:
        feature = None
    with FEATURE_LOCK:
        FEATURE_CACHE[url] = feature
        FEATURE_CACHE.move_to_end(url)
        while len(FEATURE_CACHE) > MAX_FEATURES:
            FEATURE_CACHE.popitem(last=False)
    return feature


def _item_feature(item: dict) -> dict | None:
    for field in ("thumb_url", "image_url"):
        feature = _download_feature(str(item.get(field) or ""))
        if feature is not None:
            return feature
    return None


def _metadata_fallback(first: dict, second: dict) -> float:
    first_words = set((first.get("title", "") + " " + first.get("medium", "")).lower().split())
    second_words = set((second.get("title", "") + " " + second.get("medium", "")).lower().split())
    union = first_words | second_words
    lexical = len(first_words & second_words) / len(union) if union else 0.0
    first_ratio = (first.get("width") or 1) / max(first.get("height") or 1, 1)
    second_ratio = (second.get("width") or 1) / max(second.get("height") or 1, 1)
    aspect = math.exp(-abs(math.log(max(first_ratio, .05)) - math.log(max(second_ratio, .05))))
    return 0.7 * lexical + 0.3 * aspect


def result_feature_similarity(first: dict, second: dict) -> float | None:
    first_feature = _item_feature(first)
    second_feature = _item_feature(second)
    if first_feature is None or second_feature is None:
        return None
    return feature_similarity(first_feature, second_feature)


def likely_same_image(first: dict, second: dict, semantic_similarity: float | None) -> bool:
    """Conservative family match for alternate scans, encodes, and upscales."""
    if semantic_similarity is None or semantic_similarity < 0.90:
        return False
    first_ratio = (first.get("width") or 1) / max(first.get("height") or 1, 1)
    second_ratio = (second.get("width") or 1) / max(second.get("height") or 1, 1)
    ratio_distance = abs(math.log(max(first_ratio, .05) / max(second_ratio, .05)))
    if ratio_distance > 0.20:
        return False
    perceptual = result_feature_similarity(first, second)
    if perceptual is None:
        return semantic_similarity >= 0.992 and ratio_distance < 0.08
    if semantic_similarity >= 0.98:
        # Alternate scans can substantially shift contrast and color while
        # leaving SigLIP's composition-level embedding nearly unchanged.
        return perceptual >= 0.70
    if semantic_similarity >= 0.965:
        return perceptual >= 0.86
    if semantic_similarity >= 0.955:
        return perceptual >= 0.92
    # Clean redraws and meaningful crops can move the semantic vector more
    # than an ordinary rescan. Admit them only when the coarse structure and
    # proportions are exceptionally close.
    return perceptual >= 0.955 and ratio_distance < 0.08


def rank_similar(results: list[dict], target_id: str, limit: int = 12) -> list[dict]:
    target = next(item for item in results if item["id"] == target_id)
    candidates = [item for item in results if item["id"] != target_id]
    preliminary = {}
    embedding_scores = {}
    for item in candidates:
        try:
            from semantic_embeddings import image_similarity
            embedding_similarity = image_similarity(target, item)
        except Exception:
            embedding_similarity = None
        embedding_scores[item["id"]] = embedding_similarity
        preliminary[item["id"]] = (
            embedding_similarity
            if embedding_similarity is not None
            else _metadata_fallback(target, item)
        )

    # Search ranking order is not a useful proxy for visual similarity.  Use
    # cached image embeddings (and metadata only as a fallback) to choose the
    # bounded set that receives the slower perceptual comparison.
    perceptual_ids = {
        item["id"] for item in sorted(
            candidates,
            key=lambda item: (
                -preliminary[item["id"]], -item.get("rank_score", 0),
            ),
        )[:MAX_PERCEPTUAL_CANDIDATES]
    }
    target_feature = _item_feature(target)

    features = {}
    if target_feature is not None:
        perceptual_candidates = [
            item for item in candidates if item["id"] in perceptual_ids
        ]
        with ThreadPoolExecutor(
            max_workers=min(10, max(len(perceptual_candidates), 1)),
        ) as pool:
            futures = {
                pool.submit(_item_feature, item): item["id"]
                for item in perceptual_candidates
            }
            for future in as_completed(futures):
                try:
                    features[futures[future]] = future.result()
                except Exception:
                    features[futures[future]] = None

    ranked = []
    for item in candidates:
        feature = features.get(item["id"])
        similarity = (feature_similarity(target_feature, feature)
                      if target_feature is not None and feature is not None
                      else _metadata_fallback(target, item))
        embedding_similarity = embedding_scores[item["id"]]
        if embedding_similarity is not None:
            similarity = 0.72 * embedding_similarity + 0.28 * similarity
        candidate = dict(item)
        candidate["similarity"] = round(float(similarity), 4)
        ranked.append(candidate)
    ranked.sort(key=lambda item: (-item["similarity"], -item.get("rank_score", 0)))
    return ranked[:limit]
