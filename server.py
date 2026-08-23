#!/usr/bin/env python3
"""Local unified image search over Automatic Illustrator source adapters."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import http.cookies
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping


HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
DATA_DIR = Path(os.environ.get("SEARCH_DATA_DIR") or HERE / "data").expanduser()
PROTOTYPE_DIR = HERE / "vendor"
if not (PROTOTYPE_DIR / "sources.py").exists():
    PROTOTYPE_DIR = HERE.parent / "prototype"
if not (PROTOTYPE_DIR / "sources.py").exists():
    PROTOTYPE_DIR = HERE.parent / "automatic-illustrator" / "prototype"
sys.path.insert(0, str(PROTOTYPE_DIR))

import sources  # noqa: E402
from additional_sources import (  # noqa: E402
    NGACatalog, getty, harvard, loc, mia, museum_commons, paris_musees,
    universal_comasonry, yale,
)
from gnosis_catalog import GnosisCatalog  # noqa: E402
from google_image_search import (  # noqa: E402
    GoogleImageSearchError, GoogleVerificationRequired, SOURCE_DOMAINS,
    search_google_stage,
)
from image_dimensions import resolve_result_dimensions  # noqa: E402
from ranker import rank_result_groups, rank_results  # noqa: E402
from semantic_embeddings import add_semantic_scores, image_similarity  # noqa: E402
from pamela_ranker import add_pamela_scores  # noqa: E402
from visual_similarity import likely_same_image, rank_similar  # noqa: E402
from beauty_tournament import TOURNAMENT  # noqa: E402


SOURCE_LABELS = OrderedDict([
    ("gnosis", "Gnosis VN"),
    ("universal_comasonry", "Universal Co-Masonry Galleries"),
    ("cleveland", "Cleveland Museum of Art"),
    ("met", "Metropolitan Museum of Art"),
    ("rijksmuseum", "Rijksmuseum"),
    ("aic", "Art Institute of Chicago"),
    ("getty", "J. Paul Getty Museum"),
    ("nga", "National Gallery of Art"),
    ("harvard", "Harvard Art Museums"),
    ("yale", "Yale LUX — Art Museums"),
    ("paris_musees", "Paris Musées"),
    ("mia", "Minneapolis Institute of Art"),
    ("loc", "Library of Congress"),
    ("smk", "Statens Museum for Kunst"),
    ("wellcome", "Wellcome Collection"),
    ("vam", "Victoria and Albert Museum"),
    ("commons", "Wikimedia Commons — Museum Collections"),
    ("europeana", "Europeana"),
])
DEFAULT_SELECTED = tuple(SOURCE_LABELS)
# Google Images indexes these collection sites well enough to return actual
# object pages.  Keep this separate from DEFAULT_SELECTED: the normal search
# should continue using every collection's official API/catalog adapter.
GOOGLE_IMAGE_SOURCES = frozenset((
    "universal_comasonry", "cleveland", "met", "aic", "nga", "harvard",
    "mia", "loc", "wellcome", "vam", "commons",
))
MAX_SESSIONS = 24
SESSION_TTL_SECONDS = 30 * 60
BATCH_SIZE = 10
PREVIEW_BATCH_SIZES = (1, 2, 4)
AGGREGATE_QUALITY_WINDOW = 50
SOURCE_CONCURRENCY = max(
    1, int(os.environ.get("SEARCH_SOURCE_CONCURRENCY", str(len(SOURCE_LABELS)))),
)
SOURCE_CONCURRENCY_PER_PROVIDER = max(
    1, int(os.environ.get("SEARCH_PROVIDER_CONCURRENCY", "2")),
)
SEARCH_BATCH_CACHE_TTL_SECONDS = max(
    0, int(os.environ.get("SEARCH_BATCH_CACHE_TTL", "300")),
)
SEARCH_BATCH_CACHE_SIZE = max(1, int(os.environ.get("SEARCH_BATCH_CACHE_SIZE", "256")))
RANK_AFTER_DIRTY_BATCHES = 4
SOURCE_BATCH_TIMEOUT_SECONDS = 120
AIC_PROXY_DELIVERIES = frozenset(("huggingface", "wayback"))
AIC_PROXY_HOSTS = frozenset(("datasets-server.huggingface.co", "web.archive.org"))
AIC_PROXY_MAX_BYTES = 16 * 1024 * 1024
AIC_PROXY_CONCURRENCY = threading.BoundedSemaphore(6)
AIC_PROXY_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
HARVARD_PROXY_HOST = "nrs.harvard.edu"
HARVARD_CDN_HOST = "images.harvardartmuseums.org"
HARVARD_PAGE_HOSTS = frozenset((
    "harvardartmuseums.org", "www.harvardartmuseums.org",
))
HARVARD_PROXY_MAX_BYTES = 32 * 1024 * 1024
HARVARD_PROXY_CONCURRENCY = threading.BoundedSemaphore(4)
HARVARD_COOKIE_LOCK = threading.Lock()
HARVARD_COOKIE_HEADER = ""
HARVARD_COOKIE_EXPIRES = 0.0
HIGH_RES_CACHE_DIR = DATA_DIR / "high-res-image-cache"
HIGH_RES_CACHE_LIMIT = 5
HIGH_RES_CACHE_MAX_BYTES = 64 * 1024 * 1024
HIGH_RES_CACHE_LOCK = threading.RLock()
HIGH_RES_CACHE_INFLIGHT: dict[str, threading.Event] = {}


def writable_data_file(relative_path: str) -> Path:
    """Return an app-writable data path, seeded from bundled data once."""
    destination = DATA_DIR / relative_path
    bundled = HERE / "data" / relative_path
    if destination != bundled and not destination.exists() and bundled.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, destination)
    return destination


GNOSIS_CATALOG = GnosisCatalog(writable_data_file("gnosis-media.json"))
NGA_CATALOG = NGACatalog(writable_data_file("nga-search.db"))
sources.CACHE = str(DATA_DIR / "api-cache" / "sources")
Path(sources.CACHE).mkdir(parents=True, exist_ok=True)
_GNOSIS_STANDARD_SEARCH = sources._gnosis_live
SOURCE_WORK_SEMAPHORE = threading.BoundedSemaphore(SOURCE_CONCURRENCY)
SOURCE_SEMAPHORES = {
    name: threading.BoundedSemaphore(SOURCE_CONCURRENCY_PER_PROVIDER)
    for name in SOURCE_LABELS
}
SEARCH_BATCH_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
SEARCH_BATCH_INFLIGHT: dict[tuple, threading.Event] = {}
SEARCH_BATCH_CACHE_LOCK = threading.RLock()


def query_wordform_variants(query: str) -> list[str]:
    """Produce a few conservative variants for WordPress's literal search."""
    words = query.split()
    variants = [query]
    for index, word in enumerate(words):
        folded = word.lower()
        replacements = []
        if len(folded) > 5 and folded.endswith("ing"):
            stem = word[:-3]
            replacements.extend((stem, stem + "e"))
            if len(stem) > 2 and stem[-1].lower() == stem[-2].lower():
                replacements.insert(0, stem[:-1])
        elif len(folded) > 4 and folded.endswith("ed"):
            stem = word[:-2]
            replacements.extend((stem, stem + "e"))
        for replacement in replacements:
            candidate_words = list(words)
            candidate_words[index] = replacement
            candidate = " ".join(candidate_words)
            if candidate not in variants:
                variants.append(candidate)
            if len(variants) == 4:
                return variants
    return variants


def search_gnosis(query: str, need: int, cue=None) -> list[dict]:
    """Merge current WordPress/FTS hits with typo-tolerant content hits."""
    try:
        standard = _GNOSIS_STANDARD_SEARCH(query, need)
    except Exception:
        # The local content index remains useful when WordPress is temporarily
        # unreachable; source-level error handling still protects other faults.
        standard = []
    fuzzy = GNOSIS_CATALOG.search(query, max(need * 3, 36))
    live_variants = []
    # Word-form expansion is only a bootstrap fallback while the app-owned
    # catalog is empty or has no approximate match; avoid redundant REST calls
    # during ordinary searches.
    if not fuzzy:
        for variant in query_wordform_variants(query)[1:]:
            try:
                live_variants.extend(sources._gnosis_live(variant, need))
            except Exception:
                continue
    merged = []
    seen = set()
    for item in standard + live_variants + fuzzy:
        source_id = str(item.get("source_id") or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        merged.append(item)
    return merged[:max(need * 2, need)]


# Keep the shared source package untouched; this app opts into richer Gnosis
# retrieval while every other consumer retains the established adapter.
sources.ADAPTERS["gnosis"] = search_gnosis
sources.ADAPTERS["getty"] = getty
sources.ADAPTERS["nga"] = NGA_CATALOG.search
sources.ADAPTERS["harvard"] = harvard
sources.ADAPTERS["yale"] = yale
sources.ADAPTERS["paris_musees"] = paris_musees
sources.ADAPTERS["mia"] = mia
sources.ADAPTERS["loc"] = loc
sources.ADAPTERS["commons"] = museum_commons
sources.ADAPTERS["universal_comasonry"] = universal_comasonry


class InputError(ValueError):
    pass


class ImageProxyError(RuntimeError):
    pass


class SearchCancelled(RuntimeError):
    pass


def validate_query(value: str) -> str:
    query = " ".join((value or "").split())
    if not query:
        raise InputError("Enter one or more search terms.")
    if len(query) > 240:
        raise InputError("Search terms must be 240 characters or fewer.")
    return query


def validate_limit(value: str | int | None) -> int:
    try:
        limit = 12 if value is None or value == "" else int(value)
    except (TypeError, ValueError) as exc:
        raise InputError("Result limit must be a number.") from exc
    if not 1 <= limit <= 24:
        raise InputError("Result limit must be between 1 and 24.")
    return limit


def parse_sources(value: str | None) -> list[str]:
    requested = [part.strip() for part in (value or "").split(",") if part.strip()]
    if not requested:
        return list(DEFAULT_SELECTED)
    unknown = [name for name in requested if name not in SOURCE_LABELS]
    if unknown:
        raise InputError(f"Unknown source: {unknown[0]}")
    return list(dict.fromkeys(requested))


def result_id(item: dict) -> str:
    identity = "|".join([
        str(item.get("source") or ""),
        str(item.get("source_id") or ""),
        str(item.get("image_url") or ""),
    ])
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]


def normalize_result(item: dict, provider_rank: int = 0) -> dict:
    source_name = str(item.get("source") or "")
    image_delivery = str(item.get("image_delivery") or "")
    image_url = str(item.get("image_url") or "")
    page_url = str(item.get("page_url") or "")
    full_resolution_url = str(item.get("full_resolution_url") or "")
    # Non-Commons AIC deliveries are medium-sized mirrors. The museum's
    # artwork page is the supported route to the native reproduction.
    # Every other current adapter exposes its downloadable original directly.
    requires_source_visit = bool(item.get("requires_source_visit")) or (
        source_name == "aic"
        and bool(image_delivery)
        and image_delivery != "commons"
    )
    # The preview is a doorway to the collection record, where the complete
    # description and rights context live. Keep the direct original as a
    # separate capability so the desktop app can offer an explicit download.
    preview_click_url = page_url or full_resolution_url or image_url
    download_url = "" if requires_source_visit else image_url
    normalized = {
        "source": source_name,
        "source_label": SOURCE_LABELS.get(source_name, source_name.title()),
        "source_id": str(item.get("source_id") or ""),
        "title": str(item.get("title") or "Untitled"),
        "artist": str(item.get("artist") or ""),
        "date": str(item.get("date") or ""),
        "medium": str(item.get("medium") or ""),
        "license": str(item.get("license") or "unknown"),
        "rights_status": str(item.get("rights_status") or ""),
        "rights_basis": str(item.get("rights_basis") or ""),
        "rights_evidence": copy.deepcopy(item.get("rights_evidence") or {}),
        "attribution": str(item.get("attribution") or ""),
        "page_url": page_url,
        "image_url": image_url,
        "thumb_url": str(item.get("thumb_url") or item.get("image_url") or ""),
        "placeholder_url": str(item.get("placeholder_url") or ""),
        "image_delivery": image_delivery,
        "full_resolution_url": full_resolution_url,
        "download_url": download_url,
        "preview_click_url": preview_click_url,
        "preview_click_action": "visit_website",
        "preview_width": int(item.get("preview_width") or 0),
        "preview_height": int(item.get("preview_height") or 0),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
        "provider_rank": int(provider_rank),
    }
    normalized["id"] = result_id(normalized)
    supplied_description = str(item.get("description") or "").strip() or " · ".join(
        filter(None, [
            normalized["artist"], normalized["date"], normalized["medium"],
        ])
    )
    normalized["description"] = supplied_description or (
        f'An image titled “{normalized["title"]}” in the '
        f'{normalized["source_label"]} collection.'
    )
    return normalized


def search_one(
    source_name: str,
    query: str,
    limit: int,
    adapters: Mapping[str, Callable] | None = None,
) -> dict:
    started = time.monotonic()
    try:
        adapter = (adapters or sources.ADAPTERS)[source_name]
        raw = adapter(query, limit)
        results = [normalize_result(item, rank) for rank, item in enumerate(raw[:limit])]
        return {
            "source": source_name,
            "label": SOURCE_LABELS[source_name],
            "results": results,
            "count": len(results),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": "",
        }
    except Exception as exc:
        group = {
            "source": source_name,
            "label": SOURCE_LABELS[source_name],
            "results": [],
            "count": 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
        if isinstance(exc, sources.EuropeanaAccessError):
            group["alert"] = {
                "code": "europeana_key_access",
                "status": exc.status,
            }
        return group


def search_batch(
    source_name: str,
    query: str,
    offset: int,
    batch_size: int = BATCH_SIZE,
    adapters: Mapping[str, Callable] | None = None,
    cancelled: Callable[[], bool] | None = None,
    resolve_dimensions: bool = True,
) -> dict:
    """Fetch a growing provider window and return only its next slice."""
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise SearchCancelled("Search was superseded by a newer request.")

    def acquire(semaphore: threading.BoundedSemaphore):
        while not semaphore.acquire(timeout=0.1):
            check_cancelled()

    cache_key = None
    cache_owner = False
    cache_event = None
    if adapters is None and SEARCH_BATCH_CACHE_TTL_SECONDS:
        cache_key = (source_name, query.casefold(), offset, batch_size)
        while True:
            check_cancelled()
            now = time.monotonic()
            with SEARCH_BATCH_CACHE_LOCK:
                cached = SEARCH_BATCH_CACHE.get(cache_key)
                if cached and now - cached[0] <= SEARCH_BATCH_CACHE_TTL_SECONDS:
                    SEARCH_BATCH_CACHE.move_to_end(cache_key)
                    return copy.deepcopy(cached[1])
                if cached:
                    SEARCH_BATCH_CACHE.pop(cache_key, None)
                cache_event = SEARCH_BATCH_INFLIGHT.get(cache_key)
                if cache_event is None:
                    cache_event = threading.Event()
                    SEARCH_BATCH_INFLIGHT[cache_key] = cache_event
                    cache_owner = True
                    break
            cache_event.wait(0.1)

    started = time.monotonic()
    global_acquired = False
    provider_acquired = False
    try:
        check_cancelled()
        if adapters is None:
            acquire(SOURCE_WORK_SEMAPHORE)
            global_acquired = True
            acquire(SOURCE_SEMAPHORES[source_name])
            provider_acquired = True
            check_cancelled()
        adapter = (adapters or sources.ADAPTERS)[source_name]
        requested = offset + batch_size
        raw = adapter(query, requested)
        check_cancelled()
        window = list(raw[offset:requested])
        if resolve_dimensions:
            window = resolve_result_dimensions(window)
        check_cancelled()
        group = {
            "source": source_name,
            "label": SOURCE_LABELS[source_name],
            "results": [normalize_result(item, offset + rank)
                        for rank, item in enumerate(window)],
            "count": len(window),
            "offset": offset,
            "exhausted": len(window) < batch_size,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": "",
        }
        if cache_key is not None:
            with SEARCH_BATCH_CACHE_LOCK:
                SEARCH_BATCH_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(group))
                SEARCH_BATCH_CACHE.move_to_end(cache_key)
                while len(SEARCH_BATCH_CACHE) > SEARCH_BATCH_CACHE_SIZE:
                    SEARCH_BATCH_CACHE.popitem(last=False)
        return group
    except SearchCancelled:
        raise
    except Exception as exc:
        group = {
            "source": source_name,
            "label": SOURCE_LABELS[source_name],
            "results": [],
            "count": 0,
            "offset": offset,
            "exhausted": True,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
        if isinstance(exc, sources.EuropeanaAccessError):
            group["alert"] = {
                "code": "europeana_key_access",
                "status": exc.status,
            }
        return group
    finally:
        if provider_acquired:
            SOURCE_SEMAPHORES[source_name].release()
        if global_acquired:
            SOURCE_WORK_SEMAPHORE.release()
        if cache_key is not None and cache_owner:
            with SEARCH_BATCH_CACHE_LOCK:
                SEARCH_BATCH_INFLIGHT.pop(cache_key, None)
                cache_event.set()


def score_search_results(query: str, items: list[dict]) -> list[dict]:
    """Enrich a copy of visible results without delaying their first display."""
    enriched = resolve_result_dimensions(copy.deepcopy(items))
    add_semantic_scores(query, enriched)
    add_pamela_scores(enriched)
    for item in enriched:
        item["pamela_rerank"] = True
    return enriched


def stream_search_round(
    session: "SearchSession",
    batch_search: Callable = search_batch,
):
    """Stream independent per-collection pipelines plus background enrichment."""
    search_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(session.selected_sources),
    )
    enrichment_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(4, len(session.selected_sources)),
    )
    work = {}

    def submit_search(source_name: str):
        policy = session.continuation_policy()[source_name]
        if not policy["continue"] or session.source_cancelled(source_name):
            return
        state = session.source_states[source_name]
        batch_size = (
            PREVIEW_BATCH_SIZES[state["rounds"]]
            if state["rounds"] < len(PREVIEW_BATCH_SIZES)
            else BATCH_SIZE
        )
        future = search_executor.submit(
            batch_search,
            source_name,
            session.query,
            policy["fetched"],
            batch_size,
            cancelled=lambda name=source_name: session.source_cancelled(name),
            resolve_dimensions=False,
        )
        work[future] = {
            "kind": "search",
            "source": source_name,
            "offset": policy["fetched"],
            "started": time.monotonic(),
        }

    def submit_enrichment(items: list[dict]):
        if not items:
            return
        future = enrichment_executor.submit(score_search_results, session.query, items)
        work[future] = {"kind": "enrichment", "started": time.monotonic()}

    for source_name in session.selected_sources:
        submit_search(source_name)

    try:
        while work and not session.cancelled:
            now = time.monotonic()
            search_deadlines = [
                details["started"] + SOURCE_BATCH_TIMEOUT_SECONDS
                for details in work.values() if details["kind"] == "search"
            ]
            timeout = max(0.0, min(search_deadlines) - now) if search_deadlines else None
            completed, _pending = concurrent.futures.wait(
                set(work),
                timeout=timeout,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not completed:
                overdue = [
                    future for future, details in work.items()
                    if details["kind"] == "search"
                    and now - details["started"] >= SOURCE_BATCH_TIMEOUT_SECONDS
                ]
                for future in overdue:
                    details = work.pop(future)
                    source_name = details["source"]
                    group = {
                        "source": source_name,
                        "results": [],
                        "count": 0,
                        "offset": details["offset"],
                        "exhausted": True,
                        "error": (
                            f"TimeoutError: collection search exceeded "
                            f"{SOURCE_BATCH_TIMEOUT_SECONDS} seconds"
                        ),
                    }
                    snapshot = session.merge_batch(
                        group, coalesce_ranking=False, score_results=False,
                    )
                    session.cancel_source(source_name)
                    future.cancel()
                    yield {"type": "snapshot", "source": source_name, "snapshot": snapshot}
                continue

            for future in completed:
                details = work.pop(future)
                if details["kind"] == "enrichment":
                    try:
                        scored_items = future.result()
                    except Exception:
                        continue
                    if not session.cancelled:
                        yield {
                            "type": "rerank",
                            "snapshot": session.merge_scored_results(scored_items),
                        }
                    continue

                source_name = details["source"]
                try:
                    group = future.result()
                    snapshot = session.merge_batch(
                        group, coalesce_ranking=False, score_results=False,
                    )
                except SearchCancelled:
                    continue
                except Exception as exc:
                    group = {
                        "source": source_name,
                        "results": [],
                        "count": 0,
                        "offset": details["offset"],
                        "exhausted": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    snapshot = session.merge_batch(
                        group, coalesce_ranking=False, score_results=False,
                    )

                # Start this collection's next retrieval before yielding or
                # beginning its slower dimension/model enrichment.
                submit_search(source_name)
                submit_enrichment(group["results"])
                yield {"type": "snapshot", "source": source_name, "snapshot": snapshot}

        if not session.cancelled:
            yield {"type": "complete", "snapshot": session.snapshot(force_rank=True)}
    finally:
        search_executor.shutdown(wait=False, cancel_futures=True)
        enrichment_executor.shutdown(wait=False, cancel_futures=True)


class SearchSession:
    def __init__(self, query: str, selected_sources: list[str]):
        self.id = uuid.uuid4().hex
        self.query = query
        self.selected_sources = tuple(selected_sources)
        self.ranking_mode = "pamela"
        self.created_at = time.time()
        self.results: dict[str, dict] = {}
        self.all_results: dict[str, dict] = {}
        self.families: dict[str, list[dict]] = {}
        self.family_by_id: dict[str, str] = {}
        self.source_errors: dict[str, str] = {}
        self.source_alerts: dict[str, dict] = {}
        self.source_states = {
            name: {"fetched": 0, "last_ids": [], "exhausted": False,
                   "stop_reason": "", "rounds": 0, "last_batch_count": 0}
            for name in selected_sources
        }
        self.revision = 0
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.source_cancel_events = {
            name: threading.Event() for name in selected_sources
        }
        self.rank_dirty = False
        self.dirty_batches = 0

    def cancel(self):
        self.cancel_event.set()

    def cancel_source(self, source_name: str):
        self.source_cancel_events[source_name].set()

    def source_cancelled(self, source_name: str) -> bool:
        return self.cancelled or self.source_cancel_events[source_name].is_set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _rerank_locked(self, force: bool = False):
        if not self.rank_dirty:
            return
        if not force and self.revision and self.dirty_batches < RANK_AFTER_DIRTY_BATCHES:
            return
        ranked, families = rank_result_groups(
            self.query, list(self.all_results.values()),
            same_image=lambda first, second: likely_same_image(
                first, second, image_similarity(first, second)
            ),
        )
        self.results = {item["id"]: item for item in ranked}
        self.families = families
        self.family_by_id = {
            item["id"]: representative_id
            for representative_id, members in families.items() for item in members
        }
        self.rank_dirty = False
        self.dirty_batches = 0
        self.revision += 1

    def merge_batch(
        self,
        group: dict,
        coalesce_ranking: bool = False,
        score_results: bool = True,
    ) -> dict:
        source_name = group["source"]
        if self.source_cancelled(source_name):
            raise SearchCancelled("Search was superseded by a newer request.")
        with self.lock:
            state = self.source_states[source_name]
            if int(group.get("offset") or 0) != state["fetched"]:
                return self.snapshot()
        incoming = list(group["results"])
        if score_results:
            incoming = score_search_results(self.query, incoming)
        if self.source_cancelled(source_name):
            raise SearchCancelled("Search was superseded by a newer request.")
        with self.lock:
            state = self.source_states[source_name]
            if int(group.get("offset") or 0) != state["fetched"]:
                return self.snapshot()
            for item in incoming:
                self.all_results[item["id"]] = item
            state["last_ids"] = [item["id"] for item in incoming]
            state["last_batch_count"] = int(group.get("count") or 0)
            state["fetched"] += state["last_batch_count"]
            state["rounds"] += 1
            state["exhausted"] = bool(group.get("exhausted"))
            if group.get("error"):
                self.source_errors[group["source"]] = group["error"]
                state["stop_reason"] = "source unavailable"
            if group.get("alert"):
                self.source_alerts[group["source"]] = dict(group["alert"])
            if incoming:
                self.rank_dirty = True
                self.dirty_batches += 1
            self._rerank_locked(force=not coalesce_ranking)
            return self.snapshot()

    def merge_scored_results(self, scored_items: list[dict]) -> dict:
        """Replace visible metadata records with enriched versions and rerank."""
        with self.lock:
            changed = False
            for item in scored_items:
                item_id = item.get("id")
                if item_id in self.all_results:
                    self.all_results[item_id] = item
                    changed = True
            if changed:
                self.rank_dirty = True
                self.dirty_batches += 1
                self._rerank_locked(force=True)
            return self.snapshot()

    def item(self, item_id: str) -> dict | None:
        with self.lock:
            return self.all_results.get(item_id) or self.results.get(item_id)

    def family(self, item_id: str) -> list[dict]:
        with self.lock:
            representative_id = self.family_by_id.get(item_id, item_id)
            fallback = self.item(item_id)
            return list(self.families.get(
                representative_id, [fallback] if fallback else [],
            ))

    def related(self, item_id: str, limit: int = 12) -> dict:
        with self.lock:
            target = self.item(item_id)
            if target is None:
                raise InputError("Image is not part of this search.")
            family = self.family(item_id)
            family_ids = {item["id"] for item in family}
            comparison_items = [target] + [
                item for item in self.results.values() if item["id"] not in family_ids
            ]
        similar = rank_similar(comparison_items, item_id, limit)
        alternates = sorted(
            (dict(item) for item in family if item["id"] != item_id),
            key=lambda item: (
                int(item.get("width") or 0) * int(item.get("height") or 0),
                min(int(item.get("width") or 0), int(item.get("height") or 0)),
            ),
            reverse=True,
        )
        return {"id": item_id, "alternates": alternates, "results": similar}

    def continuation_policy(self) -> dict:
        with self.lock:
            positions = {item_id: index + 1 for index, item_id in enumerate(self.results)}
            policy = {}
            for name, state in self.source_states.items():
                latest_representatives = {
                    self.family_by_id.get(item_id, item_id) for item_id in state["last_ids"]
                }
                latest_positions = [positions[item_id] for item_id in latest_representatives
                                    if item_id in positions]
                top_hits = sum(position <= AGGREGATE_QUALITY_WINDOW
                               for position in latest_positions)
                if state["rounds"] == 0 and not state["stop_reason"]:
                    should_continue, reason = True, "awaiting first batch"
                elif state["stop_reason"]:
                    should_continue, reason = False, state["stop_reason"]
                elif state["exhausted"]:
                    should_continue, reason = False, "source exhausted"
                elif (state["rounds"] <= len(PREVIEW_BATCH_SIZES)
                      and state["last_batch_count"]
                      == PREVIEW_BATCH_SIZES[state["rounds"] - 1]):
                    should_continue, reason = True, "progressive preview batch complete"
                elif top_hits == 0:
                    should_continue, reason = False, "latest batch produced no aggregate top-50 result"
                else:
                    should_continue, reason = True, (
                        f"{top_hits} of latest {state['last_batch_count']} "
                        "rank in aggregate top 50"
                    )
                policy[name] = {
                    "continue": should_continue,
                    "reason": reason,
                    "fetched": state["fetched"],
                    "top_50_hits": top_hits,
                    "rounds": state["rounds"],
                }
            return policy

    def snapshot(self, force_rank: bool = False) -> dict:
        with self.lock:
            self._rerank_locked(force=force_rank)
            return {
                "session_id": self.id,
                "query": self.query,
                "ranking_mode": self.ranking_mode,
                "revision": self.revision,
                "results": list(self.results.values()),
                "source_errors": dict(self.source_errors),
                "source_alerts": copy.deepcopy(self.source_alerts),
                "selected_count": len(self.selected_sources),
                "source_policy": self.continuation_policy(),
            }


SESSIONS: OrderedDict[str, SearchSession] = OrderedDict()
SESSIONS_LOCK = threading.RLock()


def create_session(
    query: str,
    selected_sources: list[str],
) -> SearchSession:
    now = time.time()
    with SESSIONS_LOCK:
        expired = [sid for sid, session in SESSIONS.items()
                   if now - session.created_at > SESSION_TTL_SECONDS]
        for sid in expired:
            SESSIONS.pop(sid, None)
        while len(SESSIONS) >= MAX_SESSIONS:
            SESSIONS.popitem(last=False)
        session = SearchSession(query, selected_sources)
        SESSIONS[session.id] = session
        return session


def get_session(session_id: str) -> SearchSession:
    with SESSIONS_LOCK:
        session = SESSIONS.get(session_id)
        if not session:
            raise InputError("Search session has expired. Please search again.")
        SESSIONS.move_to_end(session_id)
        return session


def get_aic_proxy_item(session_id: str, item_id: str) -> dict:
    """Resolve an approved mirror URL without exposing an open web proxy."""
    session = get_session(session_id)
    with session.lock:
        item = session.item(item_id)
    if not item:
        raise InputError("Image is not part of this search.")
    if (item.get("source") != "aic"
            or item.get("image_delivery") not in AIC_PROXY_DELIVERIES):
        raise InputError("That image does not use the AIC preview proxy.")
    image_url = str(item.get("image_url") or "")
    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme != "https" or parsed.hostname not in AIC_PROXY_HOSTS:
        raise InputError("The AIC preview host is not allowed.")
    return item


def get_harvard_proxy_item(session_id: str, item_id: str) -> dict:
    """Resolve an approved Harvard image without exposing an open proxy."""
    session = get_session(session_id)
    with session.lock:
        item = session.item(item_id)
    if not item or item.get("source") != "harvard":
        raise InputError("Image is not part of this Harvard search.")
    page = urllib.parse.urlparse(str(item.get("page_url") or ""))
    if page.scheme != "https" or page.hostname not in HARVARD_PAGE_HOSTS:
        raise InputError("The Harvard object page is not allowed.")
    for field in ("image_url", "thumb_url"):
        image = urllib.parse.urlparse(str(item.get(field) or ""))
        if image.scheme != "https" or image.hostname != HARVARD_PROXY_HOST:
            raise InputError("The Harvard image host is not allowed.")
    return item


def get_cacheable_detail_item(session_id: str, item_id: str) -> dict:
    """Resolve an image whose downloadable original is directly available."""
    session = get_session(session_id)
    with session.lock:
        item = session.item(item_id)
    if not item:
        raise InputError("Image is not part of this search.")
    if not item.get("download_url"):
        raise InputError("The source website is required for the full image.")
    image_url = str(item.get("image_url") or "")
    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InputError("The full image URL is invalid.")
    return item


def harvard_cdn_url(image_url: str) -> str:
    """Translate Harvard's API IIIF URL to its signed official image CDN."""
    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme != "https" or parsed.hostname != HARVARD_PROXY_HOST:
        raise ImageProxyError("The Harvard image host is not allowed.")
    base, marker, operation = parsed.path.partition("/full/")
    if (not marker or not base.lower().startswith("/urn-3:huam:")
            or base.endswith(":IMAGE")):
        raise ImageProxyError("The Harvard image URL is invalid.")
    path = f"{base}:IMAGE/full/{operation}"
    return urllib.parse.urlunparse((
        "https", HARVARD_CDN_HOST, path, "", parsed.query, "",
    ))


def _refresh_harvard_cookie(page_url: str) -> str:
    global HARVARD_COOKIE_HEADER, HARVARD_COOKIE_EXPIRES
    request = urllib.request.Request(page_url, headers={
        "User-Agent": AIC_PROXY_UA,
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        set_cookies = response.headers.get_all("Set-Cookie") or []
    values = {}
    for header in set_cookies:
        cookie = http.cookies.SimpleCookie()
        cookie.load(header)
        for name in (
            "CloudFront-Policy", "CloudFront-Signature", "CloudFront-Key-Pair-Id",
        ):
            if name in cookie:
                values[name] = cookie[name].value
    if len(values) != 3:
        raise ImageProxyError("Harvard did not grant temporary image access.")
    HARVARD_COOKIE_HEADER = "; ".join(
        f"{name}={values[name]}" for name in (
            "CloudFront-Policy", "CloudFront-Signature", "CloudFront-Key-Pair-Id",
        )
    )
    HARVARD_COOKIE_EXPIRES = time.time() + 50 * 60
    return HARVARD_COOKIE_HEADER


def harvard_cookie(page_url: str, force: bool = False) -> str:
    with HARVARD_COOKIE_LOCK:
        if (not force and HARVARD_COOKIE_HEADER
                and time.time() < HARVARD_COOKIE_EXPIRES):
            return HARVARD_COOKIE_HEADER
        return _refresh_harvard_cookie(page_url)


def image_mime_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageProxyError("The mirror did not return a supported image.")


IMAGE_CACHE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _high_res_cache_key(image_url: str) -> str:
    return hashlib.sha256(image_url.encode("utf-8")).hexdigest()


def _read_high_res_cache_locked(cache_key: str) -> tuple[bytes, str] | None:
    HIGH_RES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for content_type, extension in IMAGE_CACHE_EXTENSIONS.items():
        path = HIGH_RES_CACHE_DIR / f"{cache_key}{extension}"
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
            # Cache hits count as recent use, so frequently revisited images
            # survive when a sixth full-resolution image is downloaded.
            path.touch()
            return data, content_type
        except OSError:
            continue
    return None


def _write_high_res_cache_locked(
    cache_key: str,
    data: bytes,
    content_type: str,
) -> None:
    extension = IMAGE_CACHE_EXTENSIONS[content_type]
    HIGH_RES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    destination = HIGH_RES_CACHE_DIR / f"{cache_key}{extension}"
    temporary = HIGH_RES_CACHE_DIR / f".{cache_key}-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    cached_images = sorted(
        (
            path for path in HIGH_RES_CACHE_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_CACHE_EXTENSIONS.values()
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in cached_images[HIGH_RES_CACHE_LIMIT:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass


def fetch_direct_image(image_url: str) -> tuple[bytes, str]:
    """Download and validate a bounded browser-displayable image."""
    try:
        request = urllib.request.Request(image_url, headers={
            "User-Agent": AIC_PROXY_UA,
            "Accept": "image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8",
        })
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(HIGH_RES_CACHE_MAX_BYTES + 1)
        if len(data) > HIGH_RES_CACHE_MAX_BYTES:
            raise ImageProxyError("The full image exceeds the cache size limit.")
        return data, image_mime_type(data)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise ImageProxyError("The full image is unavailable.") from exc


def cached_high_res_image(
    item: dict,
    downloader: Callable[[dict], tuple[bytes, str]] | None = None,
) -> tuple[bytes, str, bool]:
    """Return a cached full image, coalescing concurrent cache misses."""
    image_url = str(item.get("image_url") or "")
    cache_key = _high_res_cache_key(image_url)
    while True:
        with HIGH_RES_CACHE_LOCK:
            cached = _read_high_res_cache_locked(cache_key)
            if cached:
                return cached[0], cached[1], True
            event = HIGH_RES_CACHE_INFLIGHT.get(cache_key)
            if event is None:
                event = threading.Event()
                HIGH_RES_CACHE_INFLIGHT[cache_key] = event
                break
        event.wait()

    try:
        if downloader:
            data, content_type = downloader(item)
        elif item.get("source") == "harvard":
            data, content_type = fetch_harvard_preview(item, detail=True)
        else:
            data, content_type = fetch_direct_image(image_url)
        if content_type not in IMAGE_CACHE_EXTENSIONS:
            raise ImageProxyError("The full image format is not supported.")
        with HIGH_RES_CACHE_LOCK:
            _write_high_res_cache_locked(cache_key, data, content_type)
        return data, content_type, False
    finally:
        with HIGH_RES_CACHE_LOCK:
            HIGH_RES_CACHE_INFLIGHT.pop(cache_key, None)
            event.set()


def fetch_harvard_preview(item: dict, detail: bool = False) -> tuple[bytes, str]:
    """Stream an official Harvard CDN image using short-lived access cookies."""
    source_url = str(item.get("image_url" if detail else "thumb_url") or "")
    cdn_url = harvard_cdn_url(source_url)
    page_url = str(item.get("page_url") or "")
    last_error = None
    with HARVARD_PROXY_CONCURRENCY:
        for attempt in range(2):
            try:
                request = urllib.request.Request(cdn_url, headers={
                    "User-Agent": AIC_PROXY_UA,
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
                    "Cookie": harvard_cookie(page_url, force=attempt > 0),
                    "Referer": page_url,
                })
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(HARVARD_PROXY_MAX_BYTES + 1)
                if len(data) > HARVARD_PROXY_MAX_BYTES:
                    raise ImageProxyError("The Harvard preview exceeds the size limit.")
                return data, image_mime_type(data)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError,
                    ImageProxyError) as exc:
                last_error = exc
    raise ImageProxyError("The Harvard preview is unavailable.") from last_error


def fetch_aic_preview(image_url: str, attempts: int = 3) -> tuple[bytes, str]:
    """Fetch a bounded preview with retries and return a browser-safe MIME."""
    last_error = None
    with AIC_PROXY_CONCURRENCY:
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(image_url, headers={
                    "User-Agent": AIC_PROXY_UA,
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
                })
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(AIC_PROXY_MAX_BYTES + 1)
                if len(data) > AIC_PROXY_MAX_BYTES:
                    raise ImageProxyError("The AIC preview exceeds the size limit.")
                return data, image_mime_type(data)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError,
                    ImageProxyError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
    raise ImageProxyError("The AIC preview mirror is unavailable.") from last_error


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "GnosisUnifiedImageSearch/0.2"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        cache_control: str = "no-store",
        extra_headers: Mapping[str, str] | None = None,
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, status: int, value: dict | list):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def send_ndjson(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            for event in events:
                line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()

    def serve_static(self, name: str):
        path = WEB_DIR / name
        if not path.is_file():
            self.send_json(404, {"error": "Not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(200, path.read_bytes(), f"{content_type}; charset=utf-8")

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise InputError("Invalid request length.") from exc
        if length > 64 * 1024:
            raise InputError("Request is too large.")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise InputError("Invalid JSON.") from exc
        if not isinstance(value, dict):
            raise InputError("Expected a JSON object.")
        return value

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/":
                self.serve_static("index.html")
            elif url.path == "/beauty":
                self.serve_static("beauty.html")
            elif url.path == "/google-helper":
                self.serve_static("google-helper.html")
            elif url.path == "/saint-peter-ranked":
                self.serve_static("saint-peter-ranked.html")
            elif url.path in (
                "/app.js", "/styles.css", "/beauty.js", "/beauty.css",
                "/google-helper.js", "/google-ranking.js", "/google-helper.css",
                "/gnosis-caduceus.svg",
            ):
                self.serve_static(url.path[1:])
            elif url.path == "/favicon.ico":
                self.serve_static("gnosis-caduceus.svg")
            elif url.path == "/health":
                self.send_json(200, {"ok": True, "sessions": len(SESSIONS)})
            elif url.path == "/api/sources":
                self.send_json(200, {
                    "sources": [
                        {
                            "id": name,
                            "label": label,
                            "default": name in DEFAULT_SELECTED,
                            "google_domains": list(SOURCE_DOMAINS.get(name, ())),
                            "google_images": name in GOOGLE_IMAGE_SOURCES,
                        }
                        for name, label in SOURCE_LABELS.items()
                    ],
                })
            elif url.path == "/api/google-images/stage":
                query = validate_query((params.get("q") or [""])[0])
                selected = parse_sources((params.get("sources") or [""])[0])
                try:
                    requested_mp = int((params.get("mp") or ["0"])[0])
                except ValueError as exc:
                    raise InputError("Invalid megapixel stage.") from exc
                if requested_mp not in (4, 9, 16, 25):
                    raise InputError("Megapixel stage must be 4, 9, 16, or 25.")
                self.send_json(200, search_google_stage(
                    query,
                    selected,
                    requested_mp,
                    dict(SOURCE_LABELS),
                    DATA_DIR / "google-image-search-cache",
                ))
            elif url.path == "/api/beauty/state":
                if TOURNAMENT is None:
                    raise InputError("The optional tournament image pack is not installed.")
                self.send_json(200, TOURNAMENT.snapshot())
            elif url.path == "/api/beauty/image":
                if TOURNAMENT is None:
                    raise InputError("The optional tournament image pack is not installed.")
                item_id = (params.get("id") or [""])[0]
                try:
                    body, content_type = TOURNAMENT.image_bytes(item_id)
                except ValueError as exc:
                    raise InputError(str(exc)) from exc
                self.send_bytes(200, body, content_type, "private, max-age=86400")
            elif url.path == "/api/search/start":
                query = validate_query((params.get("q") or [""])[0])
                selected = parse_sources((params.get("sources") or [""])[0])
                session = create_session(query, selected)
                self.send_json(200, session.snapshot())
            elif url.path == "/api/search/source":
                session = get_session((params.get("session") or [""])[0])
                if session.cancelled:
                    raise SearchCancelled("Search was superseded by a newer request.")
                source_name = (params.get("source") or [""])[0]
                if source_name not in session.selected_sources:
                    raise InputError("That collection is not selected for this search.")
                try:
                    offset = int((params.get("offset") or ["0"])[0])
                except ValueError as exc:
                    raise InputError("Invalid source offset.") from exc
                if offset < 0:
                    raise InputError("Invalid source offset.")
                group = search_batch(
                    source_name, session.query, offset,
                    cancelled=lambda: session.source_cancelled(source_name),
                )
                self.send_json(200, session.merge_batch(group, coalesce_ranking=True))
            elif url.path == "/api/search/stream":
                session = get_session((params.get("session") or [""])[0])
                if session.cancelled:
                    raise SearchCancelled("Search was superseded by a newer request.")
                self.send_ndjson(stream_search_round(session))
            elif url.path == "/api/search/policy":
                session = get_session((params.get("session") or [""])[0])
                self.send_json(200, session.snapshot(force_rank=True))
            elif url.path == "/api/search/cancel":
                session = get_session((params.get("session") or [""])[0])
                session.cancel()
                self.send_json(200, {"cancelled": True})
            elif url.path == "/api/search/source/cancel":
                session = get_session((params.get("session") or [""])[0])
                source_name = (params.get("source") or [""])[0]
                if source_name not in session.selected_sources:
                    raise InputError("That collection is not selected for this search.")
                session.cancel_source(source_name)
                self.send_json(200, {"cancelled": True, "source": source_name})
            elif url.path == "/api/image/aic":
                item = get_aic_proxy_item(
                    (params.get("session") or [""])[0],
                    (params.get("id") or [""])[0],
                )
                body, content_type = fetch_aic_preview(item["image_url"])
                self.send_bytes(
                    200, body, content_type,
                    cache_control="private, max-age=900",
                )
            elif url.path == "/api/image/harvard":
                item = get_harvard_proxy_item(
                    (params.get("session") or [""])[0],
                    (params.get("id") or [""])[0],
                )
                detail = (params.get("detail") or [""])[0] == "1"
                body, content_type = fetch_harvard_preview(item, detail=detail)
                self.send_bytes(
                    200, body, content_type,
                    cache_control="private, max-age=900",
                )
            elif url.path == "/api/image/detail":
                item = get_cacheable_detail_item(
                    (params.get("session") or [""])[0],
                    (params.get("id") or [""])[0],
                )
                body, content_type, cache_hit = cached_high_res_image(item)
                self.send_bytes(
                    200, body, content_type,
                    cache_control="private, max-age=900",
                    extra_headers={"X-Image-Cache": "HIT" if cache_hit else "MISS"},
                )
            elif url.path == "/api/similar":
                session = get_session((params.get("session") or [""])[0])
                item_id = (params.get("id") or [""])[0]
                limit = min(validate_limit((params.get("limit") or ["12"])[0]), 16)
                self.send_json(200, session.related(item_id, limit))
            else:
                self.send_json(404, {"error": "Not found"})
        except InputError as exc:
            self.send_json(400, {"error": str(exc)})
        except ImageProxyError as exc:
            self.send_json(502, {"error": str(exc)})
        except GoogleVerificationRequired as exc:
            self.send_json(429, {"error": str(exc), "code": "google_verification"})
        except GoogleImageSearchError as exc:
            self.send_json(502, {"error": str(exc), "code": "google_images"})
        except SearchCancelled as exc:
            self.send_json(409, {"error": str(exc)})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/api/beauty/vote":
                if TOURNAMENT is None:
                    raise InputError("The optional tournament image pack is not installed.")
                value = self.read_json()
                try:
                    snapshot = TOURNAMENT.vote(
                        str(value.get("left_id") or ""),
                        str(value.get("right_id") or ""),
                        str(value.get("choice") or ""),
                    )
                except ValueError as exc:
                    raise InputError(str(exc)) from exc
                self.send_json(200, snapshot)
            elif url.path == "/api/beauty/undo":
                if TOURNAMENT is None:
                    raise InputError("The optional tournament image pack is not installed.")
                self.send_json(200, TOURNAMENT.undo())
            else:
                self.send_json(404, {"error": "Not found"})
        except InputError as exc:
            self.send_json(400, {"error": str(exc)})


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with a hard cap on live request handlers."""

    daemon_threads = True
    request_queue_size = 128

    def __init__(self, server_address, handler_class, max_workers: int = 64):
        self._request_slots = threading.BoundedSemaphore(max(1, max_workers))
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        self._request_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--max-request-workers", type=int, default=64)
    args = parser.parse_args()
    NGA_CATALOG.start()
    server = BoundedThreadingHTTPServer(
        (args.host, args.port), SearchHandler, args.max_request_workers,
    )
    print(f"Unified image search: http://{args.host}:{args.port}")
    print("All selected collections feed one relevance-ranked gallery.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
