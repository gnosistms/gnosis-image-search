"""Shared lightweight query-token and synonym handling."""

from __future__ import annotations

import re
import unicodedata


STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on",
    "the", "to", "with", "image", "art", "artwork", "painting",
}
SYNONYM_GROUPS = (
    frozenset({
        "escape", "escaping", "escaped", "liberation", "deliverance",
        "release", "released", "releases", "rescue", "rescued", "free",
        "freed",
    }),
    frozenset({
        "prison", "prisoner", "imprisoned", "jail", "cell", "captivity",
    }),
    frozenset({"wisdom", "sophia"}),
    frozenset({"engraving", "engraved", "etching", "etched", "print"}),
    frozenset({"teaching", "teach", "teacher", "instructing", "instruction"}),
)
SYNONYMS = {word: group for group in SYNONYM_GROUPS for word in group}


def fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def tokens(text: str) -> set[str]:
    return {
        token for token in fold(text).split()
        if len(token) > 1 and token not in STOPWORDS
    }


def token_matches(query_token: str, field_tokens: set[str]) -> bool:
    return bool(SYNONYMS.get(query_token, {query_token}) & field_tokens)


def matched_query_tokens(
    query_tokens: set[str],
    field_tokens: set[str],
) -> set[str]:
    return {
        token for token in query_tokens if token_matches(token, field_tokens)
    }


def coverage(query_tokens: set[str], field_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(matched_query_tokens(query_tokens, field_tokens)) / len(query_tokens)


def concepts(text: str) -> set[frozenset[str]]:
    """Collapse synonymous tokens so one idea is counted only once."""
    return {
        SYNONYMS.get(token, frozenset({token}))
        for token in tokens(text)
    }
