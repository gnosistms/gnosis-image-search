"""Typo-tolerant search over the enriched Gnosis VN media index."""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - the app environment includes RapidFuzz
    fuzz = process = None
    import difflib


TEXT_COLUMNS = (
    "title", "caption_vi", "gen_caption_vi", "alt", "wp_description",
    "filename", "description", "description_vi", "keywords", "figures", "style",
)
META_COLUMNS = (
    "id", "page_url", "image_url", "thumb_url", "width", "height",
)
DISPLAY_PRIORITY = (
    "description", "description_vi", "wp_description", "caption_vi",
    "gen_caption_vi", "alt", "keywords", "figures", "style", "filename",
)
FIELD_WEIGHTS = {
    "title": 1.12,
    "filename": 1.10,
    "alt": 1.06,
    "keywords": 1.05,
    "figures": 1.05,
}
STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on",
    "the", "to", "with", "image", "photo", "picture", "art", "artwork",
    "mot", "va", "cua", "cho", "trong", "tren", "voi", "hinh", "anh",
}

_cache_lock = threading.RLock()
_cache: dict[Path, tuple[int, list[dict]]] = {}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _tokens(value: object) -> list[str]:
    return [word for word in _fold(value).split()
            if len(word) > 1 and word not in STOPWORDS]


def _load_records(db_path: Path) -> list[dict]:
    mtime = db_path.stat().st_mtime_ns
    with _cache_lock:
        cached = _cache.get(db_path)
        if cached and cached[0] == mtime:
            return cached[1]

    columns = TEXT_COLUMNS + META_COLUMNS
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(f"SELECT {', '.join(columns)} FROM gnosis").fetchall()
    finally:
        connection.close()

    records = []
    for row in rows:
        record = dict(zip(columns, row))
        record["_fields"] = {
            name: (_fold(record.get(name)), _tokens(record.get(name)))
            for name in TEXT_COLUMNS if record.get(name)
        }
        records.append(record)
    with _cache_lock:
        _cache[db_path] = (mtime, records)
    return records


def prepare_record(record: dict) -> dict:
    prepared = dict(record)
    prepared["_fields"] = {
        name: (_fold(prepared.get(name)), _tokens(prepared.get(name)))
        for name in TEXT_COLUMNS if prepared.get(name)
    }
    return prepared


def _word_similarity(query_word: str, candidate_word: str) -> float:
    if query_word == candidate_word:
        return 1.0
    if len(query_word) >= 3 and (
        candidate_word.startswith(query_word) or query_word.startswith(candidate_word)
    ):
        shorter = min(len(query_word), len(candidate_word))
        longer = max(len(query_word), len(candidate_word))
        return 0.88 + 0.1 * (shorter / longer)
    if fuzz is not None:
        return fuzz.ratio(query_word, candidate_word) / 100.0
    return difflib.SequenceMatcher(None, query_word, candidate_word).ratio()


def _best_word_similarity(query_word: str, words: list[str]) -> float:
    if process is not None:
        match = process.extractOne(query_word, words, scorer=fuzz.ratio, score_cutoff=45)
        return (match[1] / 100.0) if match else 0.0
    return max((_word_similarity(query_word, word) for word in words), default=0.0)


def _field_score(
    query_phrase: str,
    query_words: list[str],
    field: tuple[str, list[str]],
    word_scores: dict[str, dict[str, float]] | None = None,
) -> float:
    folded, words = field
    if not words:
        return 0.0
    if query_phrase and query_phrase in folded:
        return 1.0
    if fuzz is not None:
        # RapidFuzz evaluates the complete token sets in compiled code. Besides
        # being typo tolerant, this keeps a live 1,000–2,000 item catalog well
        # below interactive latency even when descriptions are long.
        return fuzz.token_set_ratio(query_phrase, folded) / 100.0
    similarities = [
        max((word_scores[query_word].get(word, 0.0) for word in words), default=0.0)
        if word_scores is not None else _best_word_similarity(query_word, words)
        for query_word in query_words
    ]
    strong_coverage = sum(value >= 0.76 for value in similarities) / len(similarities)
    return (sum(similarities) / len(similarities)) * (0.62 + 0.38 * strong_coverage)


def _score(
    record: dict,
    query_phrase: str,
    query_words: list[str],
    word_scores: dict[str, dict[str, float]] | None = None,
) -> tuple[float, str]:
    best_score = 0.0
    best_field = ""
    all_words: list[str] = []
    for name, field in record["_fields"].items():
        all_words.extend(field[1])
        score = _field_score(query_phrase, query_words, field, word_scores) \
            * FIELD_WEIGHTS.get(name, 1.0)
        if score > best_score:
            best_score, best_field = score, name

    # A multi-word query often spans a caption, description, and keyword list.
    if all_words:
        combined = _field_score(
            query_phrase, query_words, ("", all_words), word_scores
        ) * 0.98
        if combined > best_score:
            best_score, best_field = combined, "combined metadata"
    return min(best_score, 1.0), best_field


def search_records(records: list[dict], query: str, limit: int) -> list[dict]:
    """Return source-adapter records ranked by approximate metadata match."""
    if limit < 1:
        return []
    query_phrase = _fold(query)
    query_words = _tokens(query)
    if not query_words:
        return []

    prepared_records = [
        record if "_fields" in record else prepare_record(record) for record in records
    ]
    word_scores = None
    if fuzz is None:
        # The app's lean virtual environment may not include RapidFuzz. Compute
        # each query-word/vocabulary-word comparison once instead of repeating
        # it across every field and record.
        vocabulary = {
            word for record in prepared_records
            for _, words in record["_fields"].values() for word in words
        }
        word_scores = {
            query_word: {
                word: _word_similarity(query_word, word) for word in vocabulary
            }
            for query_word in query_words
        }

    scored = []
    for record in prepared_records:
        score, matched_field = _score(record, query_phrase, query_words, word_scores)
        # This threshold accepts a one-character typo in ordinary content words
        # while avoiding weak, coincidental matches in a small library.
        if score >= 0.72:
            scored.append((score, int(record.get("id") or 0), matched_field, record))
    scored.sort(key=lambda item: (-item[0], item[1]))

    results = []
    for score, _, matched_field, record in scored[:limit]:
        description = next((str(record.get(name) or "").strip()
                            for name in DISPLAY_PRIORITY if record.get(name)), "")
        results.append({
            "source": "gnosis",
            "source_id": str(record.get("id") or ""),
            "title": str(record.get("title") or record.get("filename") or "Untitled"),
            "artist": "",
            "date": "",
            "medium": description[:500],
            "license": "house (gnosisvn.org)",
            "page_url": str(record.get("page_url") or ""),
            "image_url": str(record.get("image_url") or ""),
            "thumb_url": str(record.get("thumb_url") or record.get("image_url") or ""),
            "width": int(record.get("width") or 0),
            "height": int(record.get("height") or 0),
            "fuzzy_score": round(score, 4),
            "fuzzy_field": matched_field,
        })
    return results


def search(db_path: str | Path, query: str, limit: int) -> list[dict]:
    """Search an older SQLite index (kept for Automatic Illustrator users)."""
    path = Path(db_path)
    if not path.is_file():
        return []
    return search_records(_load_records(path), query, limit)
