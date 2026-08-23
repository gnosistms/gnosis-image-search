"""Staged Google Images metadata retrieval for the interactive helper.

Google does not publish an API for Advanced Image Search result pages.  This
adapter deliberately keeps the page-format dependency small: one HTML parser,
one fetch function, and a persistent metadata cache.  The ranking algorithm
and UI do not depend on Google's markup.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


GOOGLE_IMAGE_URL = "https://www.google.com/search"
GOOGLE_RESULT_LIMIT = 200
GOOGLE_CACHE_TTL_SECONDS = 24 * 60 * 60
GOOGLE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)

# Google exposes only a few Advanced Search presets.  Retrieve from the next
# lower supported bucket, then enforce the requested threshold using Google's
# indexed width and height metadata.
GOOGLE_RETRIEVAL_MP = {4: 4, 9: 8, 16: 15, 25: 20}

SOURCE_DOMAINS = {
    "gnosis": ("gnosisvn.org",),
    "universal_comasonry": ("universalfreemasonry.org",),
    "cleveland": ("clevelandart.org",),
    "met": ("metmuseum.org",),
    "rijksmuseum": ("rijksmuseum.nl",),
    "aic": ("artic.edu",),
    "getty": ("getty.edu",),
    "nga": ("nga.gov",),
    "harvard": ("harvardartmuseums.org",),
    "yale": ("collections.yale.edu", "lux.collections.yale.edu"),
    "paris_musees": ("parismuseescollections.paris.fr",),
    "mia": ("artsmia.org",),
    "loc": ("loc.gov",),
    "smk": ("smk.dk",),
    "wellcome": ("wellcomecollection.org",),
    "vam": ("vam.ac.uk",),
    "commons": ("commons.wikimedia.org",),
    "europeana": ("europeana.eu",),
}


class GoogleImageSearchError(RuntimeError):
    """A user-facing Google collection failure."""


class GoogleVerificationRequired(GoogleImageSearchError):
    """Google returned an automated-traffic or CAPTCHA interstitial."""


def _attributes(attrs) -> dict[str, str]:
    return {str(name).lower(): str(value or "") for name, value in attrs}


class GoogleImageResultParser(HTMLParser):
    """Extract metadata carried by Google Image Search ``/imgres`` links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current: dict | None = None
        self.anchor_depth = 0
        self.results: list[dict] = []

    def handle_starttag(self, tag: str, attrs):
        values = _attributes(attrs)
        tag = tag.lower()
        if tag == "a":
            if self.current is not None:
                self.anchor_depth += 1
                return
            href = values.get("href", "")
            if "/imgres?" not in href:
                return
            parsed = urllib.parse.urlparse(href)
            query = urllib.parse.parse_qs(parsed.query)
            self.current = {
                "image_url": (query.get("imgurl") or [""])[0],
                "page_url": (query.get("imgrefurl") or [""])[0],
                "width": _positive_int((query.get("w") or [0])[0]),
                "height": _positive_int((query.get("h") or [0])[0]),
                "google_id": (query.get("tbnid") or query.get("docid") or [""])[0],
                "thumb_url": "",
                "title": "",
            }
            self.anchor_depth = 1
        elif tag == "img" and self.current is not None:
            source = values.get("src", "")
            if source.startswith(("http://", "https://")):
                self.current["thumb_url"] = source
            self.current["title"] = values.get("alt", "").strip()

    def handle_endtag(self, tag: str):
        if tag.lower() != "a" or self.current is None:
            return
        self.anchor_depth -= 1
        if self.anchor_depth > 0:
            return
        item, self.current = self.current, None
        if item["image_url"].startswith(("http://", "https://")):
            self.results.append(item)


def _positive_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def parse_google_image_results(document: str) -> list[dict]:
    folded = document.casefold()
    if "g-recaptcha" in folded or "unusual traffic" in folded:
        raise GoogleVerificationRequired(
            "Google asked for browser verification. Wait a little, then try again."
        )
    parser = GoogleImageResultParser()
    parser.feed(document)
    if not parser.results and "google" in folded:
        raise GoogleImageSearchError(
            "Google returned no readable image metadata. Its result format may have changed."
        )
    return parser.results


def domains_for_sources(source_names: list[str]) -> list[str]:
    domains: list[str] = []
    for source_name in source_names:
        for domain in SOURCE_DOMAINS.get(source_name, ()):
            if domain not in domains:
                domains.append(domain)
    return domains


def google_site_query(query: str, source_names: list[str]) -> str:
    sites = [f"site:{domain}" for domain in domains_for_sources(source_names)]
    expression = sites[0] if len(sites) == 1 else "(" + " OR ".join(sites) + ")"
    return " ".join(filter(None, (query.strip(), expression)))


def google_stage_url(query: str, source_names: list[str], requested_mp: int) -> str:
    retrieval_mp = GOOGLE_RETRIEVAL_MP[requested_mp]
    return GOOGLE_IMAGE_URL + "?" + urllib.parse.urlencode({
        "q": google_site_query(query, source_names),
        "udm": "2",
        "tbs": f"isz:lt,islt:{retrieval_mp}mp",
        "num": str(GOOGLE_RESULT_LIMIT),
        "hl": "en",
        "filter": "0",
    })


def fetch_google_document(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": GOOGLE_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
        "Cookie": "CONSENT=PENDING+987",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GoogleImageSearchError(f"Google Images could not be reached: {exc}") from exc


def _host_matches(host: str, domain: str) -> bool:
    host, domain = host.casefold().strip("."), domain.casefold().strip(".")
    return host == domain or host.endswith("." + domain)


def source_for_result(item: dict, source_names: list[str]) -> str:
    for field in ("page_url", "image_url"):
        host = urllib.parse.urlparse(str(item.get(field) or "")).hostname or ""
        for source_name in source_names:
            if any(_host_matches(host, domain) for domain in SOURCE_DOMAINS[source_name]):
                return source_name
    return ""


def _canonical_image_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((
        parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, parsed.query, "",
    ))


def _result_id(item: dict) -> str:
    identity = item.get("google_id") or _canonical_image_url(item.get("image_url", ""))
    return hashlib.sha1(str(identity).encode("utf-8")).hexdigest()[:20]


def normalize_stage_results(
    raw: list[dict], source_names: list[str], requested_mp: int,
    source_labels: dict[str, str], limit: int = GOOGLE_RESULT_LIMIT,
) -> list[dict]:
    minimum_pixels = requested_mp * 1_000_000
    results, seen = [], set()
    for raw_rank, item in enumerate(raw):
        width = _positive_int(item.get("width"))
        height = _positive_int(item.get("height"))
        if width * height < minimum_pixels:
            continue
        source_name = source_for_result(item, source_names)
        if not source_name:
            continue
        normalized = dict(item)
        normalized.update({
            "id": _result_id(item),
            "source": source_name,
            "source_label": source_labels.get(source_name, source_name.title()),
            "google_rank": raw_rank,
            "stage_mp": requested_mp,
            "pixel_count": width * height,
            "megapixels": round(width * height / 1_000_000, 2),
            "size_score": round(math.log2(width * height), 5),
        })
        if normalized["id"] in seen:
            continue
        seen.add(normalized["id"])
        results.append(normalized)
        if len(results) >= limit:
            break
    return results


def _cache_key(query: str, source_names: list[str], requested_mp: int) -> str:
    payload = json.dumps({
        "query": query.casefold().strip(),
        "sources": sorted(source_names),
        "mp": requested_mp,
        "version": 1,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_cache(path: Path, ttl_seconds: int) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(value.get("fetched_at") or 0) <= ttl_seconds:
            return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _write_cache(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(value, destination, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def search_google_stage(
    query: str,
    source_names: list[str],
    requested_mp: int,
    source_labels: dict[str, str],
    cache_dir: Path,
    fetcher=fetch_google_document,
    ttl_seconds: int = GOOGLE_CACHE_TTL_SECONDS,
) -> dict:
    if requested_mp not in GOOGLE_RETRIEVAL_MP:
        raise ValueError("Unsupported Google Images size stage.")
    cache_path = cache_dir / (_cache_key(query, source_names, requested_mp) + ".json")
    cached = _read_cache(cache_path, ttl_seconds)
    if cached is not None:
        cached["cached"] = True
        return cached

    url = google_stage_url(query, source_names, requested_mp)
    document = fetcher(url)
    raw = parse_google_image_results(document)
    results = normalize_stage_results(
        raw, source_names, requested_mp, source_labels, GOOGLE_RESULT_LIMIT,
    )
    value = {
        "query": query,
        "stage_mp": requested_mp,
        "retrieval_mp": GOOGLE_RETRIEVAL_MP[requested_mp],
        "count": len(results),
        "results": results,
        "fetched_at": time.time(),
        "cached": False,
    }
    _write_cache(cache_path, value)
    return value
