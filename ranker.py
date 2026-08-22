"""Deterministic cross-source relevance ranking for interactive search."""

from __future__ import annotations

import math
import re
import unicodedata
import urllib.parse


STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on",
    "the", "to", "with", "image", "art", "artwork", "painting",
}
GNOSIS_NEAR_TIE_BOOST = 0.22
SYNONYM_GROUPS = (
    {"escape", "escaping", "escaped", "liberation", "deliverance", "release",
     "released", "releases", "rescue", "rescued", "free", "freed"},
    {"prison", "prisoner", "imprisoned", "jail", "cell", "captivity"},
    {"wisdom", "sophia"},
    {"engraving", "engraved", "etching", "etched", "print"},
    {"teaching", "teach", "teacher", "instructing", "instruction"},
)
SYNONYMS = {word: group for group in SYNONYM_GROUPS for word in group}


def fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def tokens(text: str) -> set[str]:
    return {token for token in fold(text).split() if len(token) > 1 and token not in STOPWORDS}


def _token_matches(query_token: str, field_tokens: set[str]) -> bool:
    return bool((SYNONYMS.get(query_token, {query_token})) & field_tokens)


def _matched_query_tokens(query_tokens: set[str], field_tokens: set[str]) -> set[str]:
    return {token for token in query_tokens if _token_matches(token, field_tokens)}


def _coverage(query_tokens: set[str], field_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(_matched_query_tokens(query_tokens, field_tokens)) / len(query_tokens)


def _metadata_score_result(query: str, item: dict) -> tuple[float, str]:
    """Return the legacy text/metadata score when SigLIP cannot score an image."""
    q = fold(query)
    q_tokens = tokens(query)
    title = fold(item.get("title", ""))
    description = fold(" ".join(filter(None, [
        item.get("description", ""), item.get("medium", ""),
        item.get("artist", ""), item.get("date", ""),
    ])))
    title_tokens = tokens(title)
    desc_tokens = tokens(description)

    title_coverage = _coverage(q_tokens, title_tokens)
    desc_coverage = _coverage(q_tokens, desc_tokens)
    title_matches = _matched_query_tokens(q_tokens, title_tokens)
    matched = _matched_query_tokens(q_tokens, title_tokens | desc_tokens)
    score = 0.0
    reasons = []

    if q and q in title:
        score += 5.0
        reasons.append("exact title phrase")
    elif q and q in description:
        score += 2.4
        reasons.append("exact description phrase")

    score += 4.6 * title_coverage
    score += 2.8 * desc_coverage
    if title_coverage:
        precision = len(title_matches) / max(len(title_tokens), 1)
        score += 1.1 * precision
        reasons.append(f"{round(title_coverage * 100)}% title terms")
    if matched == q_tokens and q_tokens:
        score += 1.0
        reasons.append("all terms represented")

    provider_rank = max(int(item.get("provider_rank") or 0), 0)
    score += 1.55 / math.sqrt(provider_rank + 1)

    if item.get("source") == "gnosis":
        score += GNOSIS_NEAR_TIE_BOOST
        reasons.append("Gnosis VN near-tie boost")

    license_name = fold(item.get("license", ""))
    if any(mark in license_name for mark in ("cc0", "public domain", "pd cc0", "cc by")):
        score += 0.10

    return round(score, 5), ", ".join(reasons[:3]) or "provider relevance"


def resolution_relevance_score(item: dict) -> tuple[float, float, float] | None:
    """Return sqrt(pixel area), SigLIP relevance, and their product."""
    semantic_score = item.get("semantic_score")
    if not isinstance(semantic_score, (int, float)):
        return None
    width = max(int(item.get("width") or 0), 0)
    height = max(int(item.get("height") or 0), 0)
    size_score = math.sqrt(width * height) if width and height else 0.0
    relevance_score = max(0.0, min(float(semantic_score), 1.0))
    return size_score, relevance_score, size_score * relevance_score


def score_result(query: str, item: dict) -> tuple[float, str]:
    pamela_score = item.get("pamela_score")
    if item.get("pamela_rerank") and isinstance(pamela_score, (int, float)):
        width = max(int(item.get("width") or 0), 0)
        height = max(int(item.get("height") or 0), 0)
        size_score = math.sqrt(width * height) if width and height else 0.0
        beauty_score = max(0.0, min(float(pamela_score), 1.0))
        score = size_score * beauty_score
        return round(score, 5), (
            f"size {size_score:.2f} × PAMELA criteria {beauty_score:.3f}"
        )
    components = resolution_relevance_score(item)
    if components is None:
        score, reason = _metadata_score_result(query, item)
        reason = f"SigLIP unavailable; {reason}"
    else:
        size_score, relevance_score, score = components
        reason = (
            f"size {size_score:.2f} × "
            f"SigLIP relevance {relevance_score:.3f}"
        )
    return round(score, 5), reason


def _canonical_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                    parsed.path.rstrip("/"), "", ""))


def dedupe_keys(item: dict) -> list[tuple[str, ...]]:
    keys = []
    image = _canonical_url(item.get("image_url", ""))
    if image:
        keys.append(("image", image))
    title = fold(item.get("title", ""))
    artist = fold(item.get("artist", ""))
    if title and (artist or len(tokens(title)) >= 6):
        keys.append(("work", title, artist, fold(item.get("date", ""))))
    # Gnosis records for an original, rescan, and Topaz/upscaled derivative
    # often have different filenames but retain the same curated description.
    description = fold(item.get("description", ""))
    if item.get("source") == "gnosis" and len(description) >= 80:
        keys.append(("gnosis-description", description))
    return keys or [("id", str(item.get("id") or ""))]


def _resolution_key(item: dict) -> tuple:
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    return (bool(width and height), width * height, min(width, height),
            float(item.get("rank_score") or 0), -int(item.get("provider_rank") or 0))


def rank_result_groups(
    query: str,
    results: list[dict],
    same_image=None,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Rank image families, retaining every non-representative as an alternate."""
    scored = []
    for item in results:
        candidate = dict(item)
        candidate["rank_score"], candidate["rank_reason"] = score_result(query, candidate)
        components = resolution_relevance_score(candidate)
        if components is not None:
            candidate["size_score"] = round(components[0], 5)
            candidate["relevance_score"] = round(components[1], 5)
        scored.append(candidate)
    scored.sort(key=lambda item: (-item["rank_score"], item.get("provider_rank", 0),
                                  item.get("source_label", ""), item.get("title", "")))

    parents = list(range(len(scored)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first, second):
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    key_owner = {}
    for index, item in enumerate(scored):
        for key in dedupe_keys(item):
            if key in key_owner:
                union(index, key_owner[key])
            else:
                key_owner[key] = index

    if same_image is not None:
        for first in range(len(scored)):
            for second in range(first + 1, len(scored)):
                if find(first) != find(second) and same_image(scored[first], scored[second]):
                    union(first, second)

    grouped = {}
    for index, item in enumerate(scored):
        grouped.setdefault(find(index), []).append(item)

    representatives = []
    families = {}
    for members in grouped.values():
        members.sort(key=_resolution_key, reverse=True)
        representative = dict(members[0])
        representative["rank_score"] = max(item["rank_score"] for item in members)
        representative["duplicate_count"] = len(members) - 1
        representative["alternate_ids"] = [item["id"] for item in members[1:]]
        families[representative["id"]] = members
        representatives.append(representative)
    representatives.sort(key=lambda item: (-item["rank_score"], item.get("provider_rank", 0),
                                            item.get("source_label", ""), item.get("title", "")))
    return representatives, families


def rank_results(query: str, results: list[dict]) -> list[dict]:
    return rank_result_groups(query, results)[0]
