"""Image-source adapters owned by the interactive search app."""

from __future__ import annotations

import csv
import concurrent.futures
import hashlib
import html
from html.parser import HTMLParser
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - packaged builds include RapidFuzz
    fuzz = None
    import difflib


NGA_ARCHIVE_URL = (
    "https://codeload.github.com/NationalGalleryOfArt/"
    "opendata/zip/refs/heads/main"
)
NGA_REFRESH_SECONDS = 7 * 24 * 60 * 60
NGA_OBJECTS_SUFFIX = "/data/objects.csv"
NGA_IMAGES_SUFFIX = "/data/published_images.csv"
_LOC_IMAGE_SIZE = re.compile(r"#h=(\d+)&w=(\d+)$")
_RASTER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_HTML_TAG = re.compile(r"<[^>]+>")
_COMMONS_SEARCH_STOPWORDS = frozenset((
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "of",
    "on", "the", "to", "with", "image", "images", "photo", "photograph",
    "picture", "art", "artwork", "painting", "object", "work",
))
_COMMONS_METADATA_FIELDS = (
    "ObjectName", "ImageDescription", "Description", "Categories",
    "DepictedPeople", "DepictedPlace", "Credit", "Artist",
)
GETTY_SEARCH_URL = "https://www.getty.edu/art/collection/api/search"
GETTY_COLLECTION_URL = "https://www.getty.edu/art/collection"
GETTY_IIIF_IMAGE_URL = "https://media.getty.edu/iiif/image"
GETTY_CC0_LICENSES = frozenset((
    "http://creativecommons.org/publicdomain/zero/1.0",
    "https://creativecommons.org/publicdomain/zero/1.0",
))
YALE_SEARCH_URL = "https://lux.collections.yale.edu/api/search/item"
YALE_MUSEUM_NAMES = (
    "Yale University Art Gallery",
    "Yale Center for British Art",
)
PARIS_MUSEES_GRAPHQL_URL = (
    "https://apicollections.parismusees.paris.fr/graphql"
)
MIA_SEARCH_URL = "https://search.artsmia.org"
MIA_IMAGE_CDN_URL = "https://img.artsmia.org/web_objects_cache"
UNIVERSAL_COMASONRY_BASE_URL = "https://www.universalfreemasonry.org"
UNIVERSAL_COMASONRY_GALLERIES_URL = (
    UNIVERSAL_COMASONRY_BASE_URL + "/en/masonic-galleries"
)
UNIVERSAL_COMASONRY_CATALOG_TTL = 7 * 24 * 60 * 60
_UNIVERSAL_COMASONRY_CATALOG_LOCK = threading.Lock()
_UNIVERSAL_COMASONRY_REFRESH_LOCK = threading.Lock()
_UNIVERSAL_COMASONRY_REFRESH_THREAD: threading.Thread | None = None


def _nga_artwork_url(object_id: str, title: str) -> str:
    """Build the NGA's current ID-and-title artwork route."""
    normalized = unicodedata.normalize("NFKD", str(title or ""))
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")
    suffix = f"-{slug}" if slug else ""
    return f"https://www.nga.gov/artworks/{object_id}{suffix}"


class _UniversalComasonryGalleryParser(HTMLParser):
    """Extract gallery links and their images from Universal Co-Masonry HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_href = ""
        self.records: list[dict] = []

    def handle_starttag(self, tag: str, attrs):
        values = {str(name).lower(): str(value or "") for name, value in attrs}
        if tag.lower() == "a":
            href = values.get("href", "").replace("\\", "/")
            self.current_href = href if href.startswith("/en/gallery/") else ""
            return
        if tag.lower() != "img" or not self.current_href:
            return
        source = values.get("src", "").replace("\\", "/")
        if "/gallery_images/" not in source.lower():
            return
        self.records.append({
            "page_path": self.current_href,
            "image_path": source,
            "title": html.unescape(values.get("alt", "")).strip(),
        })

    def handle_endtag(self, tag: str):
        if tag.lower() == "a":
            self.current_href = ""


class _UniversalComasonryDetailParser(HTMLParser):
    """Extract the image-specific description without indexing page chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_description = False
        self.description_span_depth = 0
        self.description: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        values = {str(name).lower(): str(value or "") for name, value in attrs}
        if (
            tag.lower() == "span"
            and values.get("id", "").casefold() == "galleryimagedescription"
        ):
            self.in_description = True
            self.description_span_depth = 1
        elif self.in_description and tag.lower() == "span":
            self.description_span_depth += 1

    def handle_data(self, data: str):
        if self.in_description:
            self.description.append(data)

    def handle_endtag(self, tag: str):
        if self.in_description and tag.lower() == "span":
            self.description_span_depth -= 1
            if self.description_span_depth <= 0:
                self.in_description = False


class _UniversalComasonryIndexParser(HTMLParser):
    """Extract the 36 gallery overview links and their human-readable names."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_href = ""
        self.current_title: list[str] = []
        self.in_heading = False
        self.galleries: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        values = {str(name).lower(): str(value or "") for name, value in attrs}
        if tag.lower() == "a":
            href = values.get("href", "").replace("\\", "/").rstrip("/")
            parts = [part for part in href.split("/") if part]
            self.current_href = href if parts[:2] == ["en", "gallery"] and len(parts) == 3 else ""
            self.current_title = []
        elif tag.lower() == "h6" and self.current_href:
            self.in_heading = True

    def handle_data(self, data: str):
        if self.in_heading:
            self.current_title.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "h6":
            self.in_heading = False
        elif tag.lower() == "a":
            if self.current_href:
                title = " ".join("".join(self.current_title).split())
                if title:
                    self.galleries.append((self.current_href, title))
            self.current_href = ""
            self.current_title = []
            self.in_heading = False


def parse_universal_comasonry_gallery_index(document: str) -> list[tuple[str, str]]:
    parser = _UniversalComasonryIndexParser()
    parser.feed(document or "")
    return list(dict.fromkeys(parser.galleries))


def parse_universal_comasonry_gallery_page(
    document: str, gallery_title: str = ""
) -> list[dict]:
    """Convert one native gallery page into shared image result records."""
    parser = _UniversalComasonryGalleryParser()
    parser.feed(document or "")
    results = []
    seen = set()
    for record in parser.records:
        image_url = urllib.parse.urljoin(
            UNIVERSAL_COMASONRY_BASE_URL + "/", record["image_path"]
        )
        page_url = urllib.parse.urljoin(
            UNIVERSAL_COMASONRY_BASE_URL + "/", record["page_path"]
        )
        source_id = Path(urllib.parse.urlparse(image_url).path).stem
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        results.append({
            "source": "universal_comasonry", "source_id": source_id,
            "title": record["title"] or "Untitled",
            "artist": "", "date": "", "medium": gallery_title,
            "license": "Check Universal Co-Masonry copyright and reuse terms",
            "page_url": page_url, "image_url": image_url,
            "thumb_url": image_url, "width": 0, "height": 0,
            "requires_source_visit": True,
        })
    return results


def parse_universal_comasonry_detail_page(document: str) -> str:
    """Return the searchable description attached to one gallery image."""
    parser = _UniversalComasonryDetailParser()
    parser.feed(document or "")
    return " ".join("".join(parser.description).split())


def _universal_comasonry_fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "GnosisImages/1.0 (personal research image search)",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _universal_comasonry_catalog_path() -> Path:
    import sources
    return Path(sources.CACHE) / "universal_comasonry_catalog.json"


def _read_universal_comasonry_catalog(
    path: Path, *, allow_stale: bool = False
) -> list[dict] | None:
    try:
        if (
            not allow_stale
            and time.time() - path.stat().st_mtime > UNIVERSAL_COMASONRY_CATALOG_TTL
        ):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return None
        if any(not isinstance(item, dict) for item in payload):
            return None
        return payload
    except (OSError, ValueError, TypeError):
        return None


def _universal_comasonry_catalog_needs_refresh(
    path: Path, records: list[dict]
) -> tuple[bool, bool]:
    """Return (needs_refresh, rebuild_index) for a usable disk catalog."""
    try:
        stale = time.time() - path.stat().st_mtime > UNIVERSAL_COMASONRY_CATALOG_TTL
    except OSError:
        stale = True
    missing_descriptions = any("search_text" not in item for item in records)
    return stale or missing_descriptions, stale


def _write_universal_comasonry_catalog(path: Path, records: list[dict]) -> None:
    if not records:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass


def _build_universal_comasonry_catalog(*, enrich: bool = True) -> list[dict]:
    index = _universal_comasonry_fetch_text(UNIVERSAL_COMASONRY_GALLERIES_URL)
    galleries = parse_universal_comasonry_gallery_index(index)

    def fetch_gallery(gallery: tuple[str, str]) -> list[dict]:
        path, title = gallery
        document = _universal_comasonry_fetch_text(
            urllib.parse.urljoin(UNIVERSAL_COMASONRY_BASE_URL + "/", path)
        )
        return parse_universal_comasonry_gallery_page(document, title)

    records = []
    # The source has no search API. Fetch its small set of overview pages in
    # parallel once, then reuse the disk catalog for a week.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_gallery, gallery) for gallery in galleries]
        for future in concurrent.futures.as_completed(futures):
            try:
                records.extend(future.result())
            except Exception:
                continue

    records.sort(key=lambda item: (item["medium"].casefold(), item["title"].casefold()))
    return _enrich_universal_comasonry_catalog(records) if enrich else records


def _enrich_universal_comasonry_catalog(records: list[dict]) -> list[dict]:
    def fetch_description(item: dict) -> dict:
        enriched = dict(item)
        try:
            document = _universal_comasonry_fetch_text(item["page_url"])
            description = parse_universal_comasonry_detail_page(document)
            enriched["search_text"] = description
            enriched["description"] = description
        except Exception:
            enriched["search_text"] = ""
            enriched["description"] = ""
        return enriched

    # Descriptive captions live only on the individual image pages. Fetch them
    # once while building the weekly cache so subject/name searches can find
    # images whose thumbnail titles use different terminology.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        records = list(executor.map(fetch_description, records))
    records.sort(key=lambda item: (item["medium"].casefold(), item["title"].casefold()))
    return records


def _refresh_universal_comasonry_catalog(
    path: Path, records: list[dict], rebuild_index: bool
) -> None:
    global _UNIVERSAL_COMASONRY_REFRESH_THREAD
    try:
        with _UNIVERSAL_COMASONRY_CATALOG_LOCK:
            if rebuild_index:
                records = _build_universal_comasonry_catalog(enrich=False)
            records = _enrich_universal_comasonry_catalog(records)
            _write_universal_comasonry_catalog(path, records)
    except Exception:
        # The existing catalog remains usable when an offline refresh fails.
        pass
    finally:
        with _UNIVERSAL_COMASONRY_REFRESH_LOCK:
            _UNIVERSAL_COMASONRY_REFRESH_THREAD = None


def _schedule_universal_comasonry_refresh(
    path: Path, records: list[dict], *, rebuild_index: bool
) -> None:
    """Refresh a usable catalog in the background without delaying search."""
    global _UNIVERSAL_COMASONRY_REFRESH_THREAD
    with _UNIVERSAL_COMASONRY_REFRESH_LOCK:
        if (
            _UNIVERSAL_COMASONRY_REFRESH_THREAD is not None
            and _UNIVERSAL_COMASONRY_REFRESH_THREAD.is_alive()
        ):
            return
        thread = threading.Thread(
            target=_refresh_universal_comasonry_catalog,
            args=(path, [dict(item) for item in records], rebuild_index),
            name="universal-comasonry-catalog-refresh",
            daemon=True,
        )
        _UNIVERSAL_COMASONRY_REFRESH_THREAD = thread
        thread.start()


def _load_universal_comasonry_catalog() -> list[dict]:
    path = _universal_comasonry_catalog_path()
    cached = _read_universal_comasonry_catalog(path, allow_stale=True)
    if cached is not None:
        needs_refresh, rebuild_index = _universal_comasonry_catalog_needs_refresh(
            path, cached
        )
        if needs_refresh:
            _schedule_universal_comasonry_refresh(
                path, cached, rebuild_index=rebuild_index
            )
        return cached
    with _UNIVERSAL_COMASONRY_CATALOG_LOCK:
        cached = _read_universal_comasonry_catalog(path, allow_stale=True)
        if cached is not None:
            return cached
        # A brand-new installation only needs the 36 overview pages before it
        # can search. The much larger detail-page crawl is completed in the
        # background and atomically replaces this immediately usable catalog.
        records = _build_universal_comasonry_catalog(enrich=False)
        _write_universal_comasonry_catalog(path, records)
    _schedule_universal_comasonry_refresh(path, records, rebuild_index=False)
    return records


def _universal_comasonry_match_score(query: str, item: dict) -> tuple[float, str]:
    phrase = " ".join(query.casefold().split())
    haystack = " ".join((
        str(item.get("title") or ""), str(item.get("medium") or ""),
        str(item.get("page_url") or "").replace("-", " "),
        str(item.get("search_text") or ""),
    )).casefold()
    tokens = [token for token in re.findall(r"[\w']+", phrase) if len(token) > 1]
    matched = sum(token in haystack for token in tokens)
    if phrase and phrase in haystack:
        return 100.0 + len(tokens), str(item.get("title") or "").casefold()
    if not tokens or not matched:
        return 0.0, str(item.get("title") or "").casefold()
    return matched / len(tokens), str(item.get("title") or "").casefold()


def universal_comasonry(query: str, need: int, cue=None) -> list[dict]:
    """Search Universal Co-Masonry's native art-gallery catalog."""
    ranked = []
    for item in _load_universal_comasonry_catalog():
        score, title = _universal_comasonry_match_score(query, item)
        if score:
            ranked.append((score, title, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in ranked[:max(need * 2, need)]]


def _clean_url(value: str) -> str:
    value = str(value or "").strip()
    return "https://" + value[7:] if value.startswith("http://") else value


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metadata_value(metadata: dict, name: str) -> str:
    value = str((metadata.get(name) or {}).get("value") or "")
    return html.unescape(_HTML_TAG.sub("", value)).strip()


def _commons_artwork_date(metadata: dict) -> str:
    value = _metadata_value(metadata, "DateTimeOriginal")
    # Commons templates sometimes append their QuickStatements import payload
    # to the human-readable date in extmetadata.
    return re.sub(r"\s*date\s+QS:.*$", "", value, flags=re.IGNORECASE).strip()


def _commons_search_tokens(value: object) -> list[str]:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return [
        token for token in re.findall(r"[a-z0-9]+", folded.casefold())
        if len(token) > 1 and token not in _COMMONS_SEARCH_STOPWORDS
    ]


def _commons_stem(token: str) -> str:
    """Fold common English inflections without a language-model dependency."""
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        return base[:-1] if len(base) > 2 and base[-1:] == base[-2:-1] else base
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        return base[:-1] if len(base) > 2 and base[-1:] == base[-2:-1] else base
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _commons_word_matches(query_word: str, metadata_word: str) -> bool:
    if query_word == metadata_word:
        return True
    if _commons_stem(query_word) == _commons_stem(metadata_word):
        return True
    # Match Elasticsearch AUTO-style typo tolerance: no edits for very short
    # words, roughly one for ordinary words, and two only for longer words.
    cutoff = 84 if max(len(query_word), len(metadata_word)) >= 7 else 86
    if min(len(query_word), len(metadata_word)) < 4:
        return False
    if fuzz is not None:
        return fuzz.ratio(query_word, metadata_word) >= cutoff
    return difflib.SequenceMatcher(None, query_word, metadata_word).ratio() >= cutoff / 100


def commons_metadata_is_relevant(query: str, metadata_text: str) -> bool:
    """Require conservative fuzzy query coverage in Commons metadata."""
    query_words = list(dict.fromkeys(_commons_search_tokens(query)))
    metadata_words = list(dict.fromkeys(_commons_search_tokens(metadata_text)))
    if not query_words or not metadata_words:
        return False
    matched = sum(
        any(_commons_word_matches(query_word, word) for word in metadata_words)
        for query_word in query_words
    )
    # A single subject/name must occur. Longer natural-language searches may
    # omit function words and tolerate one unmatched concept, but a lone match
    # such as "Peter" is insufficient for "Peter escapes from prison".
    required = 1 if len(query_words) <= 2 else max(2, (len(query_words) + 1) // 2)
    return matched >= required


def _commons_metadata_text(page: dict, info: dict) -> str:
    metadata = info.get("extmetadata") or {}
    return " ".join(filter(None, (
        str(page.get("title") or "").removeprefix("File:").replace("_", " "),
        *(_metadata_value(metadata, name) for name in _COMMONS_METADATA_FIELDS),
    )))


def _reusable_commons_license(value: str) -> bool:
    folded = str(value or "").lower().replace("-", " ")
    return any(mark in folded for mark in (
        "public domain", "cc0", "cc by", "pdm",
    ))


def parse_museum_commons_response(
    data: dict, need: int, query: str | None = None
) -> list[dict]:
    """Parse Commons files already constrained to collection + depicts claims."""
    out = []
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        try:
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            metadata = info.get("extmetadata") or {}
            metadata_text = _commons_metadata_text(page, info)
            if query is not None and not commons_metadata_is_relevant(query, metadata_text):
                continue
            license_name = _metadata_value(metadata, "LicenseShortName")
            if not _reusable_commons_license(license_name):
                continue
            image_url = _clean_url(info.get("url") or "")
            if not image_url:
                continue
            out.append({
                "source": "commons",
                "source_id": str(page.get("title") or ""),
                "title": str(page.get("title") or "Untitled").removeprefix("File:"),
                "artist": _metadata_value(metadata, "Artist")[:160],
                "date": _commons_artwork_date(metadata),
                "medium": _metadata_value(metadata, "ObjectName")
                    or _metadata_value(metadata, "Credit")[:160],
                "description": (
                    _metadata_value(metadata, "ImageDescription")
                    or _metadata_value(metadata, "Description")
                )[:500],
                "license": license_name,
                "page_url": _clean_url(info.get("descriptionurl") or ""),
                "image_url": image_url,
                "thumb_url": _clean_url(info.get("thumburl") or image_url),
                "width": _as_int(info.get("width")),
                "height": _as_int(info.get("height")),
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def museum_commons(query: str, need: int, cue=None) -> list[dict]:
    """Search only structured, reusable Commons collection objects."""
    import sources
    structured_query = (
        f"{query} filetype:bitmap "
        "haswbstatement:P195 haswbstatement:P180"
    )
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": structured_query,
        "gsrnamespace": "6", "gsrlimit": str(min(max(need * 5, 30), 50)),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1024", "format": "json",
    })
    return parse_museum_commons_response(
        sources._get_json(url, "commons_museum") or {}, need, query
    )


def _is_getty_cc0(value: str) -> bool:
    return str(value or "").strip().rstrip("/") in GETTY_CC0_LICENSES


def parse_getty_response(data: dict, need: int) -> list[dict]:
    """Convert Getty's ranked Open Content search records into shared results."""
    out = []
    for record in (data or {}).get("data", []):
        try:
            manifest = record.get("manifest") or {}
            image_id = str(manifest.get("thumbUuid") or "").strip()
            # Getty's Open Content filter is the first gate; independently
            # validate the returned representative image's machine-readable
            # license before exposing its deterministic IIIF URL.
            if not image_id or not _is_getty_cc0(manifest.get("license")):
                continue
            producers = record.get("producers") or []
            artist = "; ".join(filter(None, (
                str(producer.get("primary_name") or "").strip()
                for producer in producers[:3]
            )))
            source_id = str(record.get("id") or "").rsplit("/", 1)[-1]
            slug = str(record.get("slug_with_path") or "").strip()
            image_base = f"{GETTY_IIIF_IMAGE_URL}/{image_id}"
            out.append({
                "source": "getty", "source_id": source_id,
                "title": str(record.get("primary_name") or "Untitled"),
                "artist": artist, "date": str(record.get("date_created") or ""),
                "medium": "; ".join(str(value) for value in
                                    (record.get("culture") or [])[:2]),
                "license": "CC0",
                "page_url": GETTY_COLLECTION_URL + slug if slug else "",
                "image_url": f"{image_base}/full/max/0/default.jpg",
                # Getty's manifest thumbnail is often only large enough for a
                # 1x card. Request a bounded IIIF derivative that remains crisp
                # at the gallery's maximum width on a 2x display.
                "thumb_url": f"{image_base}/full/!1200,1200/0/default.jpg",
                "width": 0, "height": 0,
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def getty(query: str, need: int, cue=None) -> list[dict]:
    """Search Getty's website index for independently verified CC0 images."""
    import sources
    url = GETTY_SEARCH_URL + "?" + urllib.parse.urlencode({
        "q": query, "from": 0, "size": min(max(need * 2, 20), 100),
        "open_content": "true", "images": "true", "is_standalone": "true",
    })
    return parse_getty_response(sources._get_json(url, "getty") or {}, need)


def _loc_image_candidate(value: str) -> tuple[int, int, str] | None:
    value = _clean_url(value)
    match = _LOC_IMAGE_SIZE.search(value)
    bare = value.split("#", 1)[0]
    if not bare.lower().endswith(_RASTER_SUFFIXES):
        return None
    height, width = (int(part) for part in match.groups()) if match else (0, 0)
    return width, height, bare


def parse_loc_response(data: dict, need: int) -> list[dict]:
    """Convert a loc.gov photo-search response into shared result records."""
    out = []
    for record in (data or {}).get("results", []):
        try:
            if record.get("access_restricted") or not record.get("digitized"):
                continue
            images = [candidate for candidate in (
                _loc_image_candidate(url) for url in record.get("image_url") or []
            ) if candidate]
            if not images:
                continue
            width, height, image_url = max(images, key=lambda item: item[0] * item[1])
            _, _, thumb_url = min(
                images, key=lambda item: abs(max(item[0], item[1]) - 1024)
            )
            item = record.get("item") or {}
            rights = str(
                item.get("rights_advisory") or item.get("rights_information") or ""
            ).strip()
            rights_folded = rights.lower()
            if "public domain" in rights_folded:
                license_name = "Public domain"
            elif "no known restrictions" in rights_folded:
                license_name = "No known restrictions"
            else:
                license_name = rights or "Check Library of Congress rights"
            contributors = item.get("contributors") or record.get("contributor") or []
            medium = item.get("medium") or item.get("mediums") or []
            if isinstance(medium, list):
                medium = "; ".join(str(value) for value in medium[:2])
            source_id = str(item.get("id") or record.get("id") or "")
            page_url = _clean_url(record.get("url") or record.get("id") or "")
            out.append({
                "source": "loc", "source_id": source_id,
                "title": str(record.get("title") or item.get("title") or "Untitled"),
                "artist": str(contributors[0] if contributors else ""),
                "date": str(item.get("date") or record.get("date") or ""),
                "medium": str(medium or ""), "license": license_name,
                "page_url": page_url, "image_url": image_url,
                "thumb_url": thumb_url, "width": width, "height": height,
                # Search responses generally expose a derivative; the item
                # page is the reliable route to every downloadable file.
                "requires_source_visit": True,
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def loc(query: str, need: int, cue=None) -> list[dict]:
    import sources
    url = "https://www.loc.gov/photos/?" + urllib.parse.urlencode({
        "q": query, "fo": "json", "c": min(max(need * 2, 20), 100),
        "fa": "online-format:image",
    })
    return parse_loc_response(sources._get_json(url, "loc") or {}, need)


def _harvard_base_image_url(value: str) -> str:
    # ``_dynmc`` is part of Harvard's IIIF service identifier, not a filename
    # suffix.  Harvard's manifests retain it when appending IIIF operations.
    return _clean_url(value).rstrip("/")


def parse_harvard_response(data: dict, need: int) -> list[dict]:
    """Convert Harvard Art Museums object records into shared results."""
    out = []
    for record in (data or {}).get("records", []):
        try:
            if _as_int(record.get("imagepermissionlevel")) != 0:
                continue
            images = sorted(
                record.get("images") or [],
                key=lambda image: _as_int(image.get("displayorder")) or 9999,
            )
            image = next((value for value in images if value.get("baseimageurl")), {})
            base = _harvard_base_image_url(
                image.get("baseimageurl") or record.get("primaryimageurl") or ""
            )
            if not base:
                continue
            people = record.get("people") or []
            artist = next((
                person.get("displayname") or person.get("name")
                for person in people
                if "artist" in str(person.get("role") or "").lower()
            ), "") or next((
                person.get("displayname") or person.get("name") for person in people
            ), "")
            copyright_name = str(
                image.get("copyright") or record.get("copyright") or ""
            ).strip()
            out.append({
                "source": "harvard",
                "source_id": str(record.get("objectid") or record.get("id") or ""),
                "title": str(record.get("title") or "Untitled"),
                "artist": str(artist or ""), "date": str(record.get("dated") or ""),
                "medium": str(record.get("medium") or record.get("technique")
                              or record.get("classification") or ""),
                "license": copyright_name or "Rights not stated",
                "page_url": _clean_url(record.get("url") or ""),
                "image_url": f"{base}/full/full/0/default.jpg",
                "thumb_url": f"{base}/full/!1024,1024/0/default.jpg",
                "width": _as_int(image.get("width")),
                "height": _as_int(image.get("height")),
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def _harvard_key() -> str:
    configured = os.environ.get("HARVARD_ART_MUSEUMS_API_KEY", "").strip()
    if configured:
        return configured
    try:
        import keys
        return str(keys.get_key("harvard")
                   or keys.get_key("harvard_art_museums") or "").strip()
    except (ImportError, OSError):
        return ""


def harvard(query: str, need: int, cue=None) -> list[dict]:
    import sources
    api_key = _harvard_key()
    if not api_key:
        return []
    fields = ",".join((
        "objectid", "title", "people", "dated", "classification", "technique",
        "medium", "primaryimageurl", "images", "url", "copyright",
        "imagepermissionlevel",
    ))
    url = "https://api.harvardartmuseums.org/object?" + urllib.parse.urlencode({
        "apikey": api_key, "keyword": query, "hasimage": 1,
        "q": "imagepermissionlevel:0", "size": min(max(need * 2, 20), 100),
        "fields": fields,
    })
    return parse_harvard_response(sources._get_json(url, "harvard") or {}, need)


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_entity_name(values) -> str:
    for value in values or []:
        entity = value.get("entity") if isinstance(value, dict) else None
        entity = entity or (value if isinstance(value, dict) else {})
        name = entity.get("name") or entity.get("_label")
        if name:
            return str(name)
    return ""


def _linked_art_image(record: dict) -> tuple[str, str, int, int]:
    """Return full image, thumbnail, width and height from Linked Art/IIIF."""
    direct = []
    services = []
    for representation in record.get("representation") or []:
        for node in _walk_dicts(representation):
            width, height = _as_int(node.get("width")), _as_int(node.get("height"))
            for access in node.get("access_point") or []:
                url = _clean_url((access or {}).get("id") or "")
                if not url:
                    continue
                if node.get("type") == "DigitalService" or "iiif" in url.lower():
                    services.append((url.rstrip("/"), width, height))
                elif str(node.get("format") or "").startswith("image/"):
                    direct.append((url, width, height))
    if services:
        base, width, height = services[0]
        return (f"{base}/full/max/0/default.jpg",
                f"{base}/full/!1024,1024/0/default.jpg", width, height)
    if direct:
        url, width, height = max(direct, key=lambda item: item[1] * item[2])
        return url, url, width, height
    return "", "", 0, 0


def parse_yale_record(record: dict) -> dict | None:
    """Convert a reusable YUAG/YCBA Linked Art object into a result."""
    serialized = json.dumps(record, ensure_ascii=False).lower()
    if not any(name.lower() in serialized for name in YALE_MUSEUM_NAMES):
        return None
    reusable_markers = (
        "public domain", "creativecommons.org/publicdomain", "cc0",
        "creativecommons.org/licenses/by/", "rightsstatements.org/vocab/noc",
    )
    if not any(marker in serialized for marker in reusable_markers):
        return None
    image_url, thumb_url, width, height = _linked_art_image(record)
    if not image_url:
        return None
    production = record.get("produced_by") or {}
    artist = ""
    for activity in production.get("part") or [production]:
        artist = _first_entity_name(activity.get("carried_out_by") or [])
        if artist:
            break
    timespan = production.get("timespan") or {}
    date = str(timespan.get("_label") or "")
    medium = _first_entity_name(record.get("classified_as") or [])
    data_url = _clean_url(record.get("id") or "")
    page_url = data_url.replace("/data/", "/view/", 1)
    return {
        "source": "yale", "source_id": data_url.rsplit("/", 1)[-1],
        "title": str(record.get("_label") or "Untitled"),
        "artist": artist, "date": date, "medium": medium,
        "license": "Reusable / public domain",
        "page_url": page_url, "image_url": image_url,
        "thumb_url": thumb_url, "width": width, "height": height,
    }


def yale(query: str, need: int, cue=None) -> list[dict]:
    """Search only the two Yale art museums and retain reusable images."""
    import sources
    museum_filter = {"OR": [
        {"memberOf": {"name": name}} for name in YALE_MUSEUM_NAMES
    ]}
    search = {"AND": [
        {"hasDigitalImage": 1}, {"text": query}, museum_filter,
    ]}
    url = YALE_SEARCH_URL + "?" + urllib.parse.urlencode({
        "q": json.dumps(search, separators=(",", ":")),
    })
    data = sources._get_json(url, "yale") or {}
    out = []
    for reference in data.get("orderedItems") or []:
        record_url = _clean_url((reference or {}).get("id") or "")
        if not record_url:
            continue
        item = parse_yale_record(sources._get_json(record_url, "yale") or {})
        if item:
            out.append(item)
        if len(out) >= need * 2:
            break
    return out


def parse_mia_response(data: dict, need: int) -> list[dict]:
    """Keep only Mia public-domain works with a valid public image."""
    out = []
    hits = ((data or {}).get("hits") or {}).get("hits") or []
    for hit in hits:
        try:
            record = hit.get("_source") or {}
            if (record.get("rights_type") != "Public Domain"
                    or record.get("image") != "valid"
                    or _as_int(record.get("public_access")) != 1):
                continue
            source_id = str(record.get("id") or hit.get("_id") or "")
            if not source_id:
                continue
            cache_location = str(record.get("Cache_Location") or "").strip()
            rendition = str(record.get("Primary_RenditionNumber") or "").strip()
            if not cache_location or not rendition:
                continue
            cache_path = "/".join(
                urllib.parse.quote(part, safe="")
                for part in cache_location.replace("\\", "/").split("/")
                if part and part not in (".", "..")
            )
            rendition_base = re.sub(r"\.jpg$", "", rendition, flags=re.IGNORECASE)
            rendition_base = urllib.parse.quote(rendition_base, safe="")
            if not cache_path or not rendition_base:
                continue
            image_base = f"{MIA_IMAGE_CDN_URL}/{cache_path}/{rendition_base}"
            image_url = f"{image_base}_full.jpg"
            thumb_url = f"{image_base}_800.jpg"
            out.append({
                "source": "mia", "source_id": source_id,
                "title": str(record.get("title") or "Untitled"),
                "artist": str(record.get("artist") or ""),
                "date": str(record.get("dated") or ""),
                "medium": str(record.get("medium") or record.get("classification") or ""),
                "description": str(record.get("text") or ""),
                "license": "Public Domain",
                "page_url": f"https://collections.artsmia.org/art/{source_id}",
                "image_url": image_url, "thumb_url": thumb_url,
                "width": _as_int(record.get("image_width")),
                "height": _as_int(record.get("image_height")),
                "preview_width": 800,
                "requires_source_visit": True,
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def mia(query: str, need: int, cue=None) -> list[dict]:
    import sources
    encoded = urllib.parse.quote(query, safe="")
    url = f"{MIA_SEARCH_URL}/{encoded}?size={min(max(need * 5, 50), 200)}"
    return parse_mia_response(sources._get_json(url, "mia") or {}, need)


def _paris_musees_key() -> str:
    configured = (os.environ.get("PARIS_MUSEES_API_TOKEN", "").strip()
                  or os.environ.get("PARIS_MUSEES_API_KEY", "").strip())
    if configured:
        return configured
    try:
        import keys
        return str(keys.get_key("paris_musees")
                   or keys.get_key("paris") or "").strip()
    except (ImportError, OSError):
        return ""


def _post_json_cached(url: str, payload: dict, headers: dict, source: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha1(url.encode("utf-8") + body).hexdigest()[:20]
    data_dir = Path(os.environ.get("SEARCH_DATA_DIR") or Path(__file__).resolve().parent / "data")
    cache_dir = data_dir / "api-cache"
    cache_path = cache_dir / f"{source}_{digest}.json"
    try:
        with cache_path.open() as stream:
            return json.load(stream)
    except (OSError, ValueError):
        pass
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "User-Agent": "GnosisInteractiveImageSearch/1.0",
            "Content-Type": "application/json", **headers,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("errors"):
            cache_dir.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w") as stream:
                json.dump(data, stream)
        return data
    except (OSError, ValueError, urllib.error.HTTPError):
        return {}


def _drupal_original_image(value: str) -> str:
    """Turn a Drupal styled derivative into its public original URL."""
    url = _clean_url(value)
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/styles/[^/]+/public/", "/", parsed.path, count=1)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def parse_paris_musees_response(data: dict, need: int) -> list[dict]:
    out = []
    entities = (((data or {}).get("data") or {}).get("nodeQuery") or {}).get("entities") or []
    for record in entities:
        try:
            visual = next((value.get("entity") or {}
                           for value in record.get("fieldVisuelsPrincipals") or []
                           if (value.get("entity") or {}).get("vignette")), {})
            thumb_url = _clean_url(visual.get("vignette") or "")
            if not thumb_url:
                continue
            source_id = str(record.get("entityId") or record.get("entityUuid") or "")
            date = record.get("fieldDateProduction") or {}
            date_text = date.get("sort") if isinstance(date, dict) else date
            artist = _first_entity_name(record.get("fieldOeuvreAuteurs") or [])
            medium = _first_entity_name(record.get("fieldMateriauxTechnique") or [])
            out.append({
                "source": "paris_musees", "source_id": source_id,
                "title": str(record.get("title") or "Untitled"),
                "artist": artist, "date": str(date_text or ""),
                "medium": medium, "license": "CC0",
                "page_url": _clean_url(record.get("absolutePath") or ""),
                "image_url": _drupal_original_image(thumb_url),
                "thumb_url": thumb_url, "width": 0, "height": 0,
            })
        except (KeyError, TypeError, ValueError):
            continue
        if len(out) >= need * 2:
            break
    return out


def paris_musees(query: str, need: int, cue=None) -> list[dict]:
    token = _paris_musees_key()
    if not token:
        return []
    # The public API exposes only copyright-free visuals.  LIKE provides the
    # broad title/author/subject behavior expected by the unified search UI.
    safe_query = query.replace("\\", "\\\\").replace('"', '\\"')
    limit = min(max(need * 3, 30), 100)
    graphql = f'''{{
      nodeQuery(limit: {limit}, filter: {{conditions: [
        {{field: "type", value: "oeuvre"}},
        {{field: "title", value: "{safe_query}", operator: LIKE}}
      ]}}) {{
        entities {{ entityId entityUuid
          ... on NodeOeuvre {{
            title absolutePath
            fieldVisuelsPrincipals {{ entity {{ vignette }} }}
            fieldOeuvreAuteurs {{ entity {{ name }} }}
            fieldDateProduction {{ sort }}
            fieldMateriauxTechnique {{ entity {{ name }} }}
          }}
        }}
      }}
    }}'''
    data = _post_json_cached(
        PARIS_MUSEES_GRAPHQL_URL, {"query": graphql},
        {"auth-token": token}, "paris_musees",
    )
    return parse_paris_musees_response(data, need)


class NGACatalog:
    """Compact FTS index built from the NGA's official open-data release."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self._refresh_lock = threading.Lock()
        self._started = False

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self.refresh_if_needed, daemon=True).start()

    def _is_fresh(self) -> bool:
        try:
            if time.time() - self.database_path.stat().st_mtime >= NGA_REFRESH_SECONDS:
                return False
            with sqlite3.connect(self.database_path) as connection:
                connection.execute("SELECT 1 FROM works LIMIT 1").fetchone()
            return True
        except (OSError, sqlite3.Error):
            return False

    def refresh_if_needed(self):
        if self._is_fresh() or not self._refresh_lock.acquire(blocking=False):
            return
        archive_path = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix="nga-open-data-", suffix=".zip",
                dir=self.database_path.parent, delete=False,
            ) as archive:
                archive_path = Path(archive.name)
                request = urllib.request.Request(
                    NGA_ARCHIVE_URL,
                    headers={"User-Agent": "GnosisInteractiveImageSearch/1.0"},
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    while chunk := response.read(1024 * 1024):
                        archive.write(chunk)
            self.build_from_archive(archive_path)
        except Exception as exc:
            print(f"NGA catalog refresh failed: {type(exc).__name__}: {exc}")
        finally:
            if archive_path:
                archive_path.unlink(missing_ok=True)
            self._refresh_lock.release()

    @staticmethod
    def _archive_member(archive: zipfile.ZipFile, suffix: str) -> str:
        return next(name for name in archive.namelist() if name.endswith(suffix))

    def build_from_archive(self, archive_path: str | Path):
        temp_path = self.database_path.with_suffix(".db.tmp")
        temp_path.unlink(missing_ok=True)
        images = {}
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open(self._archive_member(archive, NGA_IMAGES_SUFFIX)) as raw:
                reader = csv.DictReader(io.TextIOWrapper(
                    raw, encoding="utf-8-sig", newline=""
                ))
                for row in reader:
                    object_id = str(row.get("depictstmsobjectid") or "")
                    if (not object_id or row.get("openaccess") != "1"
                            or not row.get("iiifurl")):
                        continue
                    current = images.get(object_id)
                    priority = (
                        0 if str(row.get("viewtype") or "").lower() == "primary" else 1,
                        _as_int(row.get("sequence")),
                    )
                    if current is None or priority < current[0]:
                        images[object_id] = (priority, row)

            connection = sqlite3.connect(temp_path)
            try:
                connection.executescript("""
                    PRAGMA journal_mode=OFF;
                    PRAGMA synchronous=OFF;
                    CREATE TABLE works (
                        objectid TEXT PRIMARY KEY,
                        title TEXT, artist TEXT, displaydate TEXT, medium TEXT,
                        classification TEXT, description TEXT, iiifurl TEXT,
                        thumburl TEXT, width INTEGER, height INTEGER
                    );
                    CREATE VIRTUAL TABLE works_fts USING fts5(
                        objectid UNINDEXED, title, artist, displaydate, medium,
                        classification, description,
                        content='works', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    );
                """)
                rows = []
                with archive.open(self._archive_member(archive, NGA_OBJECTS_SUFFIX)) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(
                        raw, encoding="utf-8-sig", newline=""
                    ))
                    for obj in reader:
                        object_id = str(obj.get("objectid") or "")
                        image_entry = images.get(object_id)
                        if not image_entry:
                            continue
                        image = image_entry[1]
                        rows.append((
                            object_id, str(obj.get("title") or "Untitled"),
                            str(obj.get("attribution") or ""),
                            str(obj.get("displaydate") or ""),
                            str(obj.get("medium") or ""),
                            str(obj.get("classification") or ""),
                            str(image.get("assistivetext") or ""),
                            str(image.get("iiifurl") or ""),
                            str(image.get("iiifthumburl") or ""),
                            _as_int(image.get("width")), _as_int(image.get("height")),
                        ))
                        if len(rows) >= 1000:
                            self._insert_rows(connection, rows)
                            rows.clear()
                self._insert_rows(connection, rows)
                connection.commit()
                connection.execute("INSERT INTO works_fts(works_fts) VALUES('rebuild')")
                connection.execute("INSERT INTO works_fts(works_fts) VALUES('optimize')")
                connection.commit()
            finally:
                connection.close()
        temp_path.replace(self.database_path)

    @staticmethod
    def _insert_rows(connection: sqlite3.Connection, rows: list[tuple]):
        if rows:
            connection.executemany("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = re.findall(r"[\w]+", str(query or ""), flags=re.UNICODE)
        return " AND ".join(f'"{term}"*' for term in terms if len(term) > 1)

    def search(self, query: str, need: int, cue=None) -> list[dict]:
        if not self.database_path.exists():
            self.start()
            raise RuntimeError("NGA catalog is preparing its first local index")
        expression = self._fts_query(query)
        if not expression:
            return []
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""
                SELECT works.* FROM works_fts
                JOIN works ON works.rowid = works_fts.rowid
                WHERE works_fts MATCH ? ORDER BY bm25(works_fts) LIMIT ?
            """, (expression, max(need * 2, 20))).fetchall()
        out = []
        for row in rows:
            iiif = str(row["iiifurl"] or "").rstrip("/")
            title = str(row["title"] or "Untitled")
            out.append({
                "source": "nga", "source_id": str(row["objectid"]),
                "title": title,
                "artist": str(row["artist"] or ""),
                "date": str(row["displaydate"] or ""),
                "medium": "; ".join(filter(None, (
                    str(row["classification"] or ""), str(row["medium"] or "")
                ))),
                "license": "NGA Open Access",
                "page_url": _nga_artwork_url(str(row["objectid"]), title),
                "image_url": f"{iiif}/full/full/0/default.jpg",
                # NGA's published iiifthumburl is only 200 px wide. That is
                # visibly soft in our 520 px tiles (and worse on HiDPI
                # displays), so request a bounded preview from the same IIIF
                # service instead of upscaling the catalog thumbnail.
                "thumb_url": f"{iiif}/full/!1024,1024/0/default.jpg",
                "width": _as_int(row["width"]), "height": _as_int(row["height"]),
                "description": str(row["description"] or ""),
            })
        return out
