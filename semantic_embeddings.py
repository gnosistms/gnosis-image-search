"""Optional SigLIP 2 embeddings shared by ranking and image comparison."""

from __future__ import annotations

import io
import os
import sqlite3
import threading
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image


MODEL_KIND = os.environ.get("SEARCH_MODEL_KIND", "siglip").strip().lower()
MODEL_NAME = os.environ.get("SEARCH_MODEL_NAME", "google/siglip2-base-patch16-256")
MODEL_SOURCE = os.environ.get("SEARCH_MODEL_SOURCE") or MODEL_NAME
MODEL_CACHE_DIR = os.environ.get("SEARCH_MODEL_CACHE_DIR") or None
MODEL_ALLOW_DOWNLOAD = os.environ.get("SEARCH_MODEL_ALLOW_DOWNLOAD", "0") == "1"
MODEL_CACHE_KEY = f"{MODEL_KIND}:{MODEL_NAME}"
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SEARCH_DATA_DIR") or HERE / "data").expanduser()
CACHE_PATH = DATA_DIR / "image-embeddings.sqlite3"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/126.0 Safari/537.36")

_MODEL = None
_PROCESSOR = None
_TORCH = None
_DEVICE = "cpu"
_MODEL_UNAVAILABLE = False
_MODEL_LOCK = threading.RLock()
_CACHE_LOCK = threading.RLock()
_MEMORY_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_TEXT_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_MAX_MEMORY_VECTORS = 512


def _feature_tensor(value):
    """Normalize the different feature return types used by CLIP and SigLIP."""
    return value.pooler_output if hasattr(value, "pooler_output") else value


def _image_urls(item: dict) -> list[str]:
    """Return usable image candidates, preferring the smaller preview."""
    urls = []
    for field in ("thumb_url", "image_url"):
        url = str(item.get(field) or "")
        if url and url not in urls:
            urls.append(url)
    return urls


def _cached_item_vector(item: dict) -> np.ndarray | None:
    for url in _image_urls(item):
        vector = _database_vector(url)
        if vector is not None:
            return vector
    return None


def _load_model() -> bool:
    global _MODEL, _PROCESSOR, _TORCH, _DEVICE, _MODEL_UNAVAILABLE
    if _MODEL is not None:
        return True
    if _MODEL_UNAVAILABLE:
        return False
    with _MODEL_LOCK:
        if _MODEL is not None:
            return True
        try:
            import torch
            import transformers
            from transformers.utils import logging as transformers_logging
            if MODEL_KIND == "siglip":
                from transformers.models.siglip.modeling_siglip import SiglipModel
                from transformers.models.siglip.processing_siglip import SiglipProcessor
                model_class, processor_class = SiglipModel, SiglipProcessor
            elif MODEL_KIND == "clip":
                from transformers.models.clip.modeling_clip import CLIPModel
                from transformers.models.clip.processing_clip import CLIPProcessor
                model_class, processor_class = CLIPModel, CLIPProcessor
            else:
                raise ValueError(
                    f"Unsupported model kind {MODEL_KIND!r}; expected 'siglip' or 'clip'."
                )
            transformers_logging.set_verbosity_error()
            transformers_logging.disable_progress_bar()
            load_options = {"local_files_only": not MODEL_ALLOW_DOWNLOAD}
            if MODEL_CACHE_DIR:
                load_options["cache_dir"] = MODEL_CACHE_DIR
            processor = processor_class.from_pretrained(MODEL_SOURCE, **load_options)
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            dtype = torch.float16 if device == "mps" else torch.float32
            dtype_option = "dtype" if int(transformers.__version__.split(".", 1)[0]) >= 5 else "torch_dtype"
            model = model_class.from_pretrained(
                MODEL_SOURCE, **{dtype_option: dtype}, **load_options,
            ).to(device)
            model.eval()
            _MODEL, _PROCESSOR, _TORCH, _DEVICE = model, processor, torch, device
            return True
        except Exception as exc:
            __import__("traceback").print_exc()
            print(
                f"{MODEL_KIND.upper()} model unavailable ({type(exc).__name__}): {exc}",
                file=__import__("sys").stderr,
                flush=True,
            )
            _MODEL_UNAVAILABLE = True
            return False


def available() -> bool:
    return _load_model()


def _remember(url: str, vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype="float32")
    with _CACHE_LOCK:
        _MEMORY_CACHE[url] = vector
        _MEMORY_CACHE.move_to_end(url)
        while len(_MEMORY_CACHE) > _MAX_MEMORY_VECTORS:
            _MEMORY_CACHE.popitem(last=False)
    return vector


def _database_vector(url: str) -> np.ndarray | None:
    with _CACHE_LOCK:
        cached = _MEMORY_CACHE.get(url)
        if cached is not None:
            _MEMORY_CACHE.move_to_end(url)
            return cached
        try:
            connection = sqlite3.connect(CACHE_PATH)
            try:
                row = connection.execute(
                    "SELECT vector FROM embeddings WHERE url = ? AND model = ?",
                    (url, MODEL_CACHE_KEY),
                ).fetchone()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            row = None
    if not row:
        return None
    return _remember(url, np.frombuffer(row[0], dtype="float32").copy())


def _store_vectors(vectors: dict[str, np.ndarray]) -> None:
    if not vectors:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        connection = sqlite3.connect(CACHE_PATH)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS embeddings ("
                "url TEXT NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL, "
                "PRIMARY KEY (url, model))"
            )
            connection.executemany(
                "INSERT OR REPLACE INTO embeddings (url, model, vector) VALUES (?, ?, ?)",
                [(url, MODEL_CACHE_KEY, vector.astype("float32").tobytes())
                 for url, vector in vectors.items()],
            )
            connection.commit()
        finally:
            connection.close()
    for url, vector in vectors.items():
        _remember(url, vector)


def _download_image(url: str) -> Image.Image | None:
    if not url or url.endswith(".test") or ".test/" in url:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.load()
        return image
    except Exception:
        return None


def _download_item_image(item: dict) -> Image.Image | None:
    # Jetpack/i0 thumbnails are occasionally unavailable even though the
    # corresponding first-party WordPress upload works normally.
    for url in _image_urls(item):
        image = _download_image(url)
        if image is not None:
            return image
    return None


def image_vectors(items: list[dict]) -> dict[str, np.ndarray]:
    """Return available normalized image vectors keyed by result id."""
    keyed_items = {item["id"]: item for item in items if item.get("id")}
    vectors = {
        item_id: vector
        for item_id, item in keyed_items.items()
        if (vector := _cached_item_vector(item)) is not None
    }
    missing = [item for item_id, item in keyed_items.items() if item_id not in vectors]
    if missing and _load_model():
        downloaded = {}
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            futures = {
                pool.submit(_download_item_image, item): item["id"]
                for item in missing
            }
            for future in as_completed(futures):
                image = future.result()
                if image is not None:
                    downloaded[futures[future]] = image
        if downloaded:
            ordered = list(downloaded)
            with _MODEL_LOCK, _TORCH.inference_mode():
                inputs = _PROCESSOR(
                    images=[downloaded[url] for url in ordered], return_tensors="pt",
                )
                inputs = {name: value.to(_DEVICE) for name, value in inputs.items()}
                encoded = _feature_tensor(_MODEL.get_image_features(**inputs)).float()
                encoded /= encoded.norm(dim=-1, keepdim=True).clamp_min(1e-12)
                arrays = encoded.cpu().numpy().astype("float32")
            fresh = dict(zip(ordered, arrays))
            aliases = {
                url: fresh[item_id]
                for item_id in ordered for url in _image_urls(keyed_items[item_id])
            }
            _store_vectors(aliases)
            vectors.update(fresh)
    return vectors


def text_vector(query: str) -> np.ndarray | None:
    query = " ".join(str(query or "").split())
    if not query or not _load_model():
        return None
    with _CACHE_LOCK:
        cached = _TEXT_CACHE.get(query)
        if cached is not None:
            _TEXT_CACHE.move_to_end(query)
            return cached
    with _MODEL_LOCK, _TORCH.inference_mode():
        inputs = _PROCESSOR(
            text=[query], padding="max_length", truncation=True, return_tensors="pt",
        )
        inputs = {name: value.to(_DEVICE) for name, value in inputs.items()}
        encoded = _feature_tensor(_MODEL.get_text_features(**inputs)).float()
        encoded /= encoded.norm(dim=-1, keepdim=True).clamp_min(1e-12)
        vector = encoded[0].cpu().numpy().astype("float32")
    with _CACHE_LOCK:
        _TEXT_CACHE[query] = vector
        while len(_TEXT_CACHE) > 64:
            _TEXT_CACHE.popitem(last=False)
    return vector


def add_semantic_scores(query: str, items: list[dict]) -> None:
    """Attach normalized CLIP relevance to items in place when available."""
    if not items:
        return
    query_embedding = text_vector(query)
    if query_embedding is None:
        return
    vectors = image_vectors(items)
    for item in items:
        vector = vectors.get(item.get("id"))
        if vector is None:
            continue
        cosine = float(query_embedding @ vector)
        item["semantic_similarity"] = round(cosine, 5)
        # SigLIP 2 Base cosine values are centered lower than original CLIP's.
        # The -0.05..0.22 calibration preserves the Large model's average
        # relevance scale on the held-out 512-query benchmark.
        item["semantic_score"] = round(max(0.0, min(1.0, (cosine + 0.05) / 0.27)), 5)


def image_similarity(first: dict, second: dict) -> float | None:
    """Cosine similarity for images already encoded during this search."""
    first_vector = _cached_item_vector(first)
    second_vector = _cached_item_vector(second)
    if first_vector is None or second_vector is None:
        return None
    return float(first_vector @ second_vector)
