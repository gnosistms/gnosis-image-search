"""Image-source adapters owned by the interactive search app."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


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


def _reusable_commons_license(value: str) -> bool:
    folded = str(value or "").lower().replace("-", " ")
    return any(mark in folded for mark in (
        "public domain", "cc0", "cc by", "pdm",
    ))


def parse_museum_commons_response(data: dict, need: int) -> list[dict]:
    """Parse Commons files already constrained to collection + depicts claims."""
    out = []
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        try:
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            metadata = info.get("extmetadata") or {}
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
                "date": _metadata_value(metadata, "DateTimeOriginal"),
                "medium": _metadata_value(metadata, "ObjectName")
                    or _metadata_value(metadata, "Credit")[:160],
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
        sources._get_json(url, "commons_museum") or {}, need
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
                "thumb_url": str(manifest.get("thumb") or "")
                    or f"{image_base}/full/!600,600/0/default.jpg",
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
            out.append({
                "source": "nga", "source_id": str(row["objectid"]),
                "title": str(row["title"] or "Untitled"),
                "artist": str(row["artist"] or ""),
                "date": str(row["displaydate"] or ""),
                "medium": "; ".join(filter(None, (
                    str(row["classification"] or ""), str(row["medium"] or "")
                ))),
                "license": "NGA Open Access",
                "page_url": f'https://www.nga.gov/artworks/{row["objectid"]}',
                "image_url": f"{iiif}/full/full/0/default.jpg",
                "thumb_url": str(row["thumburl"] or "")
                    or f"{iiif}/full/!1024,1024/0/default.jpg",
                "width": _as_int(row["width"]), "height": _as_int(row["height"]),
                "description": str(row["description"] or ""),
            })
        return out
