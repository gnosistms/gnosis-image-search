"""Museum/archive search adapters. Every adapter returns normalized candidates:
{source, source_id, title, artist, date, medium, license, page_url,
 image_url, thumb_url, width, height, description}
All requests are disk-cached and polite. Unconfigured/failing sources return [].
"""
import base64, concurrent.futures, hashlib, html, io, json, os, re, threading, time, unicodedata
import urllib.request, urllib.parse

from relevance_terms import concepts

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(
    os.environ.get("SEARCH_DATA_DIR") or BASE,
    "api_cache",
)
os.makedirs(CACHE, exist_ok=True)
UA = {"User-Agent": "IllustrationTool/0.2 (personal research; sirhans@gmail.com)"}
DELAY = 0.4
_last_call = {}


class EuropeanaAccessError(RuntimeError):
    """Europeana rejected the shared application credential."""

    def __init__(self, status=0):
        self.status = int(status or 0)
        super().__init__("Europeana API key access was limited or blocked.")


def _europeana_access_error_message(payload):
    if not isinstance(payload, dict) or payload.get("success") is not False:
        return ""
    message = str(payload.get("error") or "").strip()
    folded = message.casefold()
    access_markers = (
        "api key", "apikey", "authentication", "usage limit", "rate limit",
        "too many request", "quota", "suspend", "revok", "block", "ip address",
    )
    return message if any(marker in folded for marker in access_markers) else ""

def _get_json(url, source, ttl_ok=True, headers=None, timeout=45, attempts=3):
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    cpath = os.path.join(CACHE, f"{source}_{key}.json")
    if ttl_ok and os.path.exists(cpath):
        try:
            return json.load(open(cpath))
        except Exception:
            pass
    # WordPress/Jetpack currently returns an empty media-search payload to
    # urllib while returning the correct JSON to a conventional HTTP client.
    # Keep this narrow: only uncached Gnosis media searches use requests.
    if source == "gnosis" and not ttl_ok:
        import requests
        wait = DELAY - (time.time() - _last_call.get(source, 0))
        if wait > 0:
            time.sleep(wait)
        for attempt in range(attempts):
            try:
                response = requests.get(url, headers={**UA, **(headers or {})},
                                        timeout=timeout)
                if response.status_code in (429, 500, 502, 503):
                    time.sleep(5 * (attempt + 1))
                    continue
                response.raise_for_status()
                data = response.json()
                _last_call[source] = time.time()
                json.dump(data, open(cpath, "w"))
                return data
            except Exception:
                time.sleep(2 * (attempt + 1))
        return None
    wait = DELAY - (time.time() - _last_call.get(source, 0))
    if wait > 0:
        time.sleep(wait)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            _last_call[source] = time.time()
            json.dump(data, open(cpath, "w"))
            return data
        except urllib.error.HTTPError as e:
            if source == "europeana" and e.code in (401, 403, 429):
                # Europeana documents 429 for an application usage limit and
                # 401 for rejected authentication. A key limited, suspended,
                # or revoked because of shared-app usage therefore belongs to
                # the same actionable credential warning in the UI.
                raise EuropeanaAccessError(e.code) from e
            if e.code in (429, 500, 502, 503):
                time.sleep(5 * (attempt + 1)); continue
            return None
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None

def _q(s):
    return urllib.parse.quote(str(s))


def _description_text(value):
    """Return one clean human-readable description from API scalar variants."""
    if isinstance(value, dict):
        ordered = []
        for key, item in value.items():
            language = str(key).replace("_", "-").casefold()
            priority = 0 if language == "en" or language.startswith("en-") else (
                1 if language in {"def", "default", "und"} else 2
            )
            ordered.append((priority, item))
        value = [item for _priority, item in sorted(ordered, key=lambda row: row[0])]
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _description_text(item)
            if text:
                return text
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())

# ---------------- Cleveland Museum of Art ----------------

_CLEVELAND_EXACT_TEXT_FIELDS = (
    "title", "alternate_titles", "tombstone", "creators", "culture",
    "technique", "support_materials", "type", "collection", "department",
    "description", "artlens_description", "early_education_description",
    "did_you_know", "inscriptions", "provenance", "citations",
    "exhibitions", "exhibition_history", "catalogue_raisonne",
    "related_works", "creditline", "measurements", "find_spot",
    "state_of_the_work", "edition_of_the_work", "impression",
    "conservation_statement",
)


def _cleveland_phrase_query(query, exact_phrase=None):
    """Return the upstream query and an optional locally verified phrase."""
    value = str(query or "").strip()
    quote_pairs = {'"': '"', "“": "”", "„": "“"}
    quoted = len(value) >= 2 and quote_pairs.get(value[0]) == value[-1]
    if exact_phrase is None:
        exact_phrase = quoted
    if exact_phrase and quoted:
        value = value[1:-1].strip()
    return value, value if exact_phrase and value else ""


def _cleveland_normalize_phrase(value):
    text = unicodedata.normalize("NFC", html.unescape(str(value or "")))
    # Exactness is token-based: punctuation and whitespace may vary, but every
    # query word must remain adjacent and in the original order. ``[^\W_]`` is
    # Unicode-aware like ``\w`` while treating underscore as punctuation.
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _cleveland_text_values(value):
    """Yield individual metadata strings without joining unrelated fields."""
    if isinstance(value, str):
        yield re.sub(r"<[^>]+>", " ", value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _cleveland_text_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _cleveland_text_values(item)


def _cleveland_has_exact_phrase(artwork, phrase):
    needle = _cleveland_normalize_phrase(phrase)
    if not needle:
        return True
    for field in _CLEVELAND_EXACT_TEXT_FIELDS:
        for value in _cleveland_text_values(artwork.get(field)):
            if needle in _cleveland_normalize_phrase(value):
                return True
    return False


def _cleveland_candidates(query, exact_phrase="", smart_parts=False):
    """Fetch every candidate page needed for deterministic phrase filtering."""
    page_size = 1000 if exact_phrase else 0
    skip = 0
    candidates = []
    while True:
        limit = page_size or 1
        params = {"q": query, "has_image": 1, "limit": limit, "skip": skip}
        if smart_parts:
            params["smart_parts"] = 1
        data = _get_json(
            "https://openaccess-api.clevelandart.org/api/artworks/?"
            + urllib.parse.urlencode(params),
            "cleveland",
        )
        rows = list((data or {}).get("data") or [])
        candidates.extend(rows)
        if not exact_phrase:
            break
        total = int(((data or {}).get("info") or {}).get("total") or 0)
        skip += len(rows)
        if not rows or len(rows) < page_size or (total and skip >= total):
            break
    return candidates


def cleveland(query, need, cue=None, *, exact_phrase=None, smart_parts=None):
    """Search CMA; quoted queries receive deterministic exact-phrase semantics."""
    if need <= 0:
        return []
    candidate_query, phrase = _cleveland_phrase_query(query, exact_phrase)
    if not candidate_query:
        return []
    if smart_parts is None:
        smart_parts = bool(phrase)
    if phrase:
        candidates = _cleveland_candidates(candidate_query, phrase, smart_parts)
        candidates = [
            artwork for artwork in candidates
            if _cleveland_has_exact_phrase(artwork, phrase)
        ]
    else:
        params = {
            "q": candidate_query, "has_image": 1, "limit": max(need * 2, 1),
        }
        if smart_parts:
            params["smart_parts"] = 1
        d = _get_json(
            "https://openaccess-api.clevelandart.org/api/artworks/?"
            + urllib.parse.urlencode(params),
            "cleveland",
        )
        candidates = list((d or {}).get("data") or [])
    out = []
    for a in candidates:
        try:
            img = a.get("images", {}) or {}
            web, full = img.get("web") or {}, img.get("print") or img.get("web") or {}
            if not web.get("url"):
                continue
            out.append({"source": "cleveland", "source_id": str(a["id"]),
                        "title": a.get("title", ""),
                        "artist": (a.get("creators") or [{}])[0].get("description", ""),
                        "date": a.get("creation_date", ""), "medium": a.get("type", ""),
                        "description": _description_text(
                            a.get("description") or a.get("artlens_description")
                            or a.get("early_education_description")
                            or a.get("did_you_know")
                        ),
                        "search_text": {
                            field.replace("_", " "): a.get(field)
                            for field in _CLEVELAND_EXACT_TEXT_FIELDS
                            if a.get(field)
                        },
                        "license": "CC0" if a.get("share_license_status") == "CC0" else "unknown",
                        "page_url": a.get("url", ""), "image_url": full.get("url", web["url"]),
                        "thumb_url": web["url"],
                        "width": int(full.get("width") or web.get("width") or 0),
                        "height": int(full.get("height") or web.get("height") or 0)})
        except Exception:
            continue
    return out[:max(need * 2, need)]

# ---------------- Metropolitan Museum ----------------

def met(query, need, cue=None):
    d = _get_json(f"https://collectionapi.metmuseum.org/public/collection/v1/search"
                  f"?q={_q(query)}&hasImages=true&title=true", "met")
    ids = ((d or {}).get("objectIDs") or [])
    ids = ids[:need * 4]
    out = []
    for oid in ids:
        if len(out) >= need * 2:
            break
        o = _get_json(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}", "met")
        if not o or not o.get("primaryImage") or not o.get("isPublicDomain"):
            continue
        out.append({"source": "met", "source_id": str(oid),
                    "title": o.get("title", ""), "artist": o.get("artistDisplayName", ""),
                    "date": o.get("objectDate", ""), "medium": o.get("medium", ""),
                    "license": "PD/CC0", "page_url": o.get("objectURL", ""),
                    "image_url": o["primaryImage"],
                    "thumb_url": o.get("primaryImageSmall") or o["primaryImage"],
                    "width": 0, "height": 0})
    return out

# ---------------- Art Institute of Chicago ----------------

def _aic_commons_images(artwork_ids):
    """Return exact Commons mirrors joined through Wikidata ARTIC IDs."""
    ids = [str(value) for value in artwork_ids if str(value)]
    if not ids:
        return {}
    values = " ".join(json.dumps(value) for value in ids)
    query = ("SELECT ?articId ?image WHERE { VALUES ?articId { " + values
             + " } ?item wdt:P4610 ?articId ; wdt:P18 ?image . }")
    data = _get_json("https://query.wikidata.org/sparql?" + urllib.parse.urlencode(
        {"query": query, "format": "json"}), "aic_wikidata",
        headers={"Accept": "application/sparql-results+json"})
    mirrors = {}
    for binding in (((data or {}).get("results") or {}).get("bindings") or []):
        artwork_id = ((binding.get("articId") or {}).get("value") or "")
        image = ((binding.get("image") or {}).get("value") or "")
        if artwork_id and image:
            mirrors.setdefault(artwork_id, image.replace("http://", "https://", 1))
    return mirrors


def _image_colorfulness(data):
    """Return a 0..1 saturation score, or None when pixels cannot be read."""
    try:
        from PIL import Image, ImageStat
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((160, 160))
            saturation = ImageStat.Stat(image.convert("HSV")).mean[1] / 255.0
        return round(float(saturation), 6)
    except Exception:
        return None


def _data_image_colorfulness(data_url):
    try:
        encoded = data_url.split(",", 1)[1]
        return _image_colorfulness(base64.b64decode(encoded))
    except Exception:
        return None


def _commons_quality_thumbnail(image_url):
    """Use a small Commons derivative instead of downloading the original."""
    parsed = urllib.parse.urlparse(image_url)
    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    if not filename:
        return image_url
    return ("https://commons.wikimedia.org/wiki/Special:Redirect/file/"
            f"{urllib.parse.quote(filename)}?width=160")


def _remote_image_colorfulness(image_url):
    """Inspect and cache a tiny remote image derivative."""
    key = hashlib.sha1(image_url.encode()).hexdigest()[:20]
    cpath = os.path.join(CACHE, f"aic_color_{key}.json")
    try:
        if os.path.exists(cpath):
            cached = json.load(open(cpath))
            value = cached.get("colorfulness")
            return float(value) if value is not None else None
    except Exception:
        pass
    value = None
    try:
        import requests
        response = requests.get(_commons_quality_thumbnail(image_url), headers=UA,
                                timeout=20)
        response.raise_for_status()
        # The quality check needs only a derivative and must not pull full files.
        if len(response.content) <= 2 * 1024 * 1024:
            value = _image_colorfulness(response.content)
    except Exception:
        value = None
    try:
        json.dump({"colorfulness": value}, open(cpath, "w"))
    except Exception:
        pass
    return value


def _aic_commons_is_current_quality(artwork, image_url):
    """Reject a monochrome Commons reproduction when AIC now shows color."""
    aic_color = _data_image_colorfulness(
        ((artwork.get("thumbnail") or {}).get("lqip") or "")
    )
    if aic_color is None or aic_color < 0.14:
        return True
    commons_color = _remote_image_colorfulness(image_url)
    if commons_color is None:
        return True  # A failed quality probe must not make an image disappear.
    return not (commons_color < 0.075 and aic_color - commons_color >= 0.10)


def _filter_aic_commons_quality(artworks, commons):
    """Apply Commons quality checks concurrently and preserve input mapping."""
    matched = [(a, commons.get(str(a.get("id")))) for a in artworks]
    matched = [(a, url) for a, url in matched if url]
    if not matched:
        return dict(commons)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(matched))) as pool:
        accepted = pool.map(lambda pair: _aic_commons_is_current_quality(*pair), matched)
    rejected = {str(a.get("id")) for (a, _), keep in zip(matched, accepted) if not keep}
    return {artwork_id: url for artwork_id, url in commons.items()
            if artwork_id not in rejected}


# This 791 KB lookup maps ARTIC IDs to Hugging Face Dataset Viewer rows. It was
# generated from parquet ID columns only (not image data), avoiding both the
# 18.6 GB archive download and per-search binary-search request bursts.
_HF_AIC_ROWS_PATH = os.path.join(BASE, "aic_hf_rows.json")
_HF_AIC_ROW_COUNT = 64019
_HF_AIC_CACHE_TTL = 20 * 60
AIC_SEARCH_FALLBACK_MAX_SCORE = 0.001
AIC_SEARCH_MIN_CANDIDATES = 20
AIC_SEARCH_CANDIDATE_MULTIPLIER = 8
AIC_SEARCH_MAX_CANDIDATES = 100
_hf_aic_images = {}
_hf_aic_row_map = None
_hf_aic_lock = threading.RLock()


def _hf_aic_rows(offset, length=1):
    """Read a small sorted row window; signed image URLs stay memory-only."""
    offset = max(0, min(int(offset), _HF_AIC_ROW_COUNT - 1))
    length = max(1, min(int(length), 100, _HF_AIC_ROW_COUNT - offset))
    import requests
    rows = []
    for attempt in range(3):
        response = requests.get(
            "https://datasets-server.huggingface.co/rows",
            params={"dataset": "links-ads/artic-dataset", "config": "default",
                    "split": "train", "offset": offset, "length": length},
            headers=UA, timeout=30,
        )
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
        rows = (response.json() or {}).get("rows") or []
        break
    return rows


def _hf_aic_image(artwork_id):
    """Return the medium preview for one exact ARTIC ID, when mirrored."""
    target = int(artwork_id)
    now = time.time()
    with _hf_aic_lock:
        cached = _hf_aic_images.get(target)
        if cached and now - cached[0] < _HF_AIC_CACHE_TTL:
            return cached[1]

    found = None
    global _hf_aic_row_map
    with _hf_aic_lock:
        if _hf_aic_row_map is None:
            try:
                with open(_HF_AIC_ROWS_PATH, encoding="utf-8") as row_map_file:
                    _hf_aic_row_map = json.load(row_map_file)
            except Exception:
                _hf_aic_row_map = {}
        row_index = _hf_aic_row_map.get(str(target))
    if row_index is not None:
        try:
            rows = _hf_aic_rows(row_index, 1)
            if rows and int((rows[0].get("row") or {}).get("id") or -1) == target:
                image = (rows[0].get("row") or {}).get("image") or {}
                if image.get("src"):
                    found = {"src": image["src"],
                             "width": int(image.get("width") or 0),
                             "height": int(image.get("height") or 0)}
        except Exception:
            # One mirror failure must not discard other AIC or Commons results.
            found = None

    with _hf_aic_lock:
        _hf_aic_images[target] = (now, found)
        if len(_hf_aic_images) > 1024:
            expired = [key for key, value in _hf_aic_images.items()
                       if now - value[0] >= _HF_AIC_CACHE_TTL]
            for key in expired:
                _hf_aic_images.pop(key, None)
            while len(_hf_aic_images) > 1024:
                _hf_aic_images.pop(next(iter(_hf_aic_images)))
    return found


def _hf_aic_images_for(artwork_ids):
    ids = [str(value) for value in artwork_ids if str(value)]
    if not ids:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
        previews = pool.map(_hf_aic_image, ids)
    return {artwork_id: preview for artwork_id, preview in zip(ids, previews)
            if preview}


def _wayback_aic_image(image_id):
    """Return an archived 1686 px AIC IIIF image when the live host is blocked."""
    original = ("https://www.artic.edu/iiif/2/"
                f"{image_id}/full/1686,/0/default.jpg")
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({
        "url": original,
        "output": "json",
        "filter": ["statuscode:200", "mimetype:image/jpeg"],
        "fl": "timestamp,original",
        "collapse": "digest",
    }, doseq=True)
    # Wayback is a last-resort preview service. Never let its CDX index hold up
    # an otherwise useful AIC batch for the general request timeout.
    data = _get_json(url, "aic_wayback", timeout=8, attempts=1)
    rows = data[1:] if isinstance(data, list) and data else []
    if not rows:
        return None
    timestamp, captured_url = rows[-1][:2]
    return {"src": f"https://web.archive.org/web/{timestamp}id_/{captured_url}",
            "width": 1686, "height": 0}


def _wayback_aic_images_for(artworks):
    items = [(str(a.get("id")), str(a.get("image_id") or "")) for a in artworks
             if a.get("id") and a.get("image_id")]
    if not items:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
        previews = pool.map(lambda pair: _wayback_aic_image(pair[1]), items)
    return {artwork_id: preview for (artwork_id, _), preview in zip(items, previews)
            if preview}


def _aic_search_is_fallback(data):
    """Detect AIC's near-zero-score match-all response for unmatched text."""
    records = (data or {}).get("data") or []
    scores = [record.get("_score") for record in records]
    # Older fixtures or a future API response may omit _score. In that case,
    # preserve the response instead of guessing that it is irrelevant.
    if not scores or any(not isinstance(score, (int, float)) for score in scores):
        return False
    return max(scores) < AIC_SEARCH_FALLBACK_MAX_SCORE


def _aic_relevant_search_records(data):
    """Remove match-all padding while retaining genuine or unscored results."""
    records = (data or {}).get("data") or []
    return [
        record for record in records
        if not isinstance(record.get("_score"), (int, float))
        or record["_score"] >= AIC_SEARCH_FALLBACK_MAX_SCORE
    ]


def _aic_search_text(record):
    """Return useful object metadata without AIC's noisy publication history."""
    thumbnail = record.get("thumbnail") or {}
    values = [
        record.get("title"), record.get("artist_display"),
        record.get("description"), record.get("short_description"),
        thumbnail.get("alt_text"),
    ]
    values.extend(record.get("subject_titles") or [])
    values.extend(record.get("term_titles") or [])
    return " ".join(_description_text(value) for value in values if value)


def _aic_match_metadata(record, query):
    """Return concise hidden controlled metadata that explains an AIC hit."""
    query_concepts = concepts(query)
    evidence = []
    seen = set()
    for label, values in (
        ("Subject term", record.get("subject_titles") or []),
        ("Index term", record.get("term_titles") or []),
    ):
        for value in values:
            text = _description_text(value)
            if text and text not in seen and query_concepts & concepts(text):
                evidence.append(text)
                seen.add(text)
                break
        if evidence:
            break
    return evidence


def _aic_display_description(record, query):
    """Compatibility view used by study/report code."""
    evidence = [f'Subject term — “{text}”' for text in _aic_match_metadata(record, query)]
    thumbnail = record.get("thumbnail") or {}
    narrative = _description_text(
        record.get("description") or record.get("short_description")
        or thumbnail.get("alt_text")
    )
    return "\n\n".join([*evidence, *([narrative] if narrative else [])])


def _aic_narrative_description(record):
    thumbnail = record.get("thumbnail") or {}
    return _description_text(
        record.get("description") or record.get("short_description")
        or thumbnail.get("alt_text")
    )


def _aic_has_sufficient_concept_coverage(query, record):
    """Reject clear one-token AIC false positives from multi-concept queries."""
    query_concepts = concepts(query)
    if len(query_concepts) <= 1:
        return True
    matched = query_concepts & concepts(_aic_search_text(record))
    if len(query_concepts) == 2:
        return len(matched) == 2
    return len(matched) >= 2 and len(matched) * 3 >= len(query_concepts) * 2


def aic(query, need, cue=None):
    candidate_limit = min(
        AIC_SEARCH_MAX_CANDIDATES,
        max(AIC_SEARCH_MIN_CANDIDATES, need * AIC_SEARCH_CANDIDATE_MULTIPLIER),
    )
    d = _get_json(f"https://api.artic.edu/api/v1/artworks/search?q={_q(query)}"
                  f"&limit={candidate_limit}&fields=id,title,artist_display,date_display,"
                  f"medium_display,image_id,is_public_domain,thumbnail,description,"
                  f"short_description,subject_titles,term_titles", "aic")
    if _aic_search_is_fallback(d):
        return []
    artworks = [
        a for a in _aic_relevant_search_records(d)
        if a.get("image_id") and a.get("is_public_domain")
        and _aic_has_sufficient_concept_coverage(query, a)
    ][:need]
    artwork_ids = [a.get("id") for a in artworks]
    # These are independent remote indexes. Start both immediately so latency
    # is the slower lookup rather than the sum of two sequential lookups.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        commons_future = pool.submit(_aic_commons_images, artwork_ids)
        hf_future = pool.submit(_hf_aic_images_for, artwork_ids)
        commons = _filter_aic_commons_quality(artworks, commons_future.result())
        hf_previews = hf_future.result()
    wayback_previews = _wayback_aic_images_for(
        a for a in artworks
        if str(a.get("id")) not in commons
        and str(a.get("id")) not in hf_previews
    )
    out = []
    for a in artworks:
        iiif_base = (d or {}).get("config", {}).get("iiif_url") or "https://www.artic.edu/iiif/2"
        iiif = f"{iiif_base.rstrip('/')}/{a['image_id']}"
        thumbnail = a.get("thumbnail") or {}
        native_width = int(thumbnail.get("width") or 0)
        native_height = int(thumbnail.get("height") or 0)
        mirror = commons.get(str(a["id"]), "")
        hf_preview = hf_previews.get(str(a["id"]))
        wayback_preview = wayback_previews.get(str(a["id"]))
        if mirror:
            image_url = mirror
            thumb_url = f"{mirror}{'&' if '?' in mirror else '?'}width=843"
            delivery = "commons"
            preview_width = preview_height = 0
        elif hf_preview:
            image_url = thumb_url = hf_preview["src"]
            delivery = "huggingface"
            preview_width = hf_preview["width"]
            preview_height = hf_preview["height"]
        elif wayback_preview:
            image_url = thumb_url = wayback_preview["src"]
            delivery = "wayback"
            preview_width = wayback_preview["width"]
            preview_height = wayback_preview["height"]
        else:
            # AIC rejects width-only requests that would upscale a small
            # original. A confined box returns the native image instead.
            image_url = f"{iiif}/full/!1686,1686/0/default.jpg"
            thumb_url = f"{iiif}/full/!843,843/0/default.jpg"
            delivery = "aic"
            preview_width = preview_height = 0
        page_url = f"https://www.artic.edu/artworks/{a['id']}"
        out.append({"source": "aic", "source_id": str(a["id"]),
                    "title": a.get("title", ""), "artist": a.get("artist_display", ""),
                    "date": a.get("date_display", ""), "medium": a.get("medium_display", ""),
                    "description": _aic_narrative_description(a),
                    "search_text": {
                        "Controlled term": _aic_match_metadata(a, query),
                        "Provider metadata": _aic_search_text(a),
                    },
                    "license": "PD/CC0",
                    "page_url": page_url,
                    "image_url": image_url,
                    "thumb_url": thumb_url,
                    "fallback_image_url": f"{iiif}/full/!843,843/0/default.jpg",
                    "placeholder_url": thumbnail.get("lqip", ""),
                    "image_delivery": delivery,
                    "full_resolution_url": page_url,
                    "preview_width": preview_width,
                    "preview_height": preview_height,
                    # These are AIC's native dimensions, not the 843 px mirror.
                    "width": native_width, "height": native_height})
    return out

# ---------------- Statens Museum for Kunst ----------------

_SMK_RUNE_FORMS = {
    "rune", "runes", "runic", "runer", "runerne", "runesten", "runestone",
    "runestones",
}


def _smk_runes_hit_is_supported(record, enrichments=()):
    """Reject the known SMK ``runes`` -> OCR ``RUN`` stem collision."""
    catalog_values = []
    for field in (
        "titles", "title", "description", "content_description", "subjects",
        "inscriptions", "notes",
    ):
        catalog_values.extend(_wellcome_text_values(record.get(field)))
    enrichment_values = []
    for enrichment in enrichments or ():
        enrichment_values.extend(_wellcome_text_values(enrichment.get("data")))
    tokens = {
        token
        for value in (*catalog_values, *enrichment_values)
        for token in _wellcome_search_tokens(value)
    }
    return bool(tokens & _SMK_RUNE_FORMS)


def smk(query, need, cue=None):
    verify_runes = _wellcome_search_tokens(query) == ["runes"]
    rows = need * 6 if verify_runes else need * 2
    d = _get_json(f"https://api.smk.dk/api/v1/art/search/?keys={_q(query)}"
                  f"&filters=%5Bhas_image%3Atrue%5D&rows={rows}", "smk")
    records = list((d or {}).get("items", []))
    enrichments = {}
    if verify_runes:
        unresolved = [
            str(record.get("object_number") or "") for record in records
            if record.get("object_number")
            and not _smk_runes_hit_is_supported(record)
        ]

        def fetch_enrichment(object_number):
            url = (
                "https://enrichment.api.smk.dk/api/enrichment/"
                + urllib.parse.quote(object_number, safe="")
            )
            return _get_json(url, "smk_enrichment") or []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(6, len(unresolved) or 1)
        ) as pool:
            enrichments = dict(zip(unresolved, pool.map(fetch_enrichment, unresolved)))
    out = []
    for a in records:
        try:
            if not a.get("image_native") and not a.get("image_thumbnail"):
                continue
            object_number = str(a.get("object_number") or "")
            if verify_runes and not _smk_runes_hit_is_supported(
                a, enrichments.get(object_number, ())
            ):
                continue
            titles = a.get("titles") or [{}]
            prod = (a.get("production") or [{}])[0]
            out.append({"source": "smk", "source_id": object_number,
                        "title": titles[0].get("title", ""),
                        "artist": prod.get("creator", "") or ", ".join(a.get("artist") or []),
                        "date": ((a.get("production_date") or [{}])[0] or {}).get("period", ""),
                        "medium": ", ".join(a.get("techniques") or []),
                        "description": _description_text(
                            a.get("description") or a.get("content_description")
                        ),
                        "search_text": {
                            field.replace("_", " "): a.get(field)
                            for field in (
                                "titles", "description", "content_description",
                                "subjects", "inscriptions", "notes",
                            ) if a.get(field)
                        },
                        "license": "CC0" if a.get("public_domain") else "unknown",
                        "page_url": a.get("frontend_url", ""),
                        "image_url": a.get("image_native") or a.get("image_thumbnail"),
                        "thumb_url": a.get("image_thumbnail") or a.get("image_native"),
                        "width": a.get("image_width") or 0, "height": a.get("image_height") or 0})
        except Exception:
            continue
    return out[:need]

# ---------------- Wellcome Collection ----------------

def _wellcome_work_metadata(work):
    contributors = work.get("contributors") or []
    primary = [value for value in contributors if value.get("primary")]
    ordered = primary + [value for value in contributors if value not in primary]
    artist = "; ".join(dict.fromkeys(
        str((value.get("agent") or {}).get("label") or "").strip()
        for value in ordered
        if (value.get("agent") or {}).get("label")
    ))
    dates = []
    for event in work.get("production") or []:
        dates.extend(
            str(value.get("label") or "").strip()
            for value in event.get("dates") or []
            if value.get("label")
        )
    date = "; ".join(dict.fromkeys(dates))
    medium = str(work.get("physicalDescription") or "").strip()
    if not medium:
        medium = str((work.get("workType") or {}).get("label") or "").strip()
    return artist, date, medium


def _wellcome_text_values(value):
    if isinstance(value, str):
        value = _description_text(value)
        if value:
            yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in {"id", "type", "url", "identifiers", "identifierType"}:
                continue
            yield from _wellcome_text_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _wellcome_text_values(child)


def _wellcome_search_text(work, source):
    """Expose the metadata fields covered by Wellcome's full-text search."""
    values = []
    for record in (source, work):
        for field in (
            "title", "alternativeTitles", "description", "physicalDescription",
            "contributors", "production", "subjects", "genres", "notes",
            "lettering", "edition", "languages",
        ):
            values.extend(_wellcome_text_values(record.get(field)))
    return list(dict.fromkeys(values))


def _wellcome_search_tokens(value):
    """Return accent-folded word tokens for local provider-hit verification."""
    folded = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", folded.casefold())


def _wellcome_safe_noun_forms(token):
    """Keep ordinary singular/plural variants without broad stemming.

    Wellcome's English analyzer can reduce ``runes`` all the way to ``run``.
    These deliberately modest rules accept ``rune``/``runes`` while refusing
    that semantic collision.  They are only used to verify one-word queries.
    """
    forms = {token}
    if token.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")
    elif token.endswith(("ches", "shes", "xes", "zes", "oes")):
        forms.add(token[:-2])
    elif token.endswith("sses"):
        forms.add(token[:-2])
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")):
        forms.add(token[:-1])
    elif token.endswith("y") and len(token) > 2 and token[-2] not in "aeiou":
        forms.add(token[:-1] + "ies")
    elif token.endswith(("ch", "sh", "x", "z", "o")):
        forms.add(token + "es")
    else:
        forms.add(token + "s")
    return forms


def _wellcome_single_term_hit_is_supported(query, search_text):
    """Reject Wellcome hits supported only by an over-broad English stem."""
    query_tokens = _wellcome_search_tokens(query)
    if len(query_tokens) != 1 or len(query_tokens[0]) < 4:
        return True
    accepted = _wellcome_safe_noun_forms(query_tokens[0])
    metadata_tokens = {
        token
        for value in search_text
        for token in _wellcome_search_tokens(value)
    }
    return bool(accepted & metadata_tokens)


def _wellcome_iiif_base(value):
    """Return the full-resolution IIIF image base for a Wellcome image URL."""
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("image", "thumbs"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                identifier = parts[index + 1]
                return urllib.parse.urlunsplit((
                    parsed.scheme, parsed.netloc, f"/image/{identifier}", "", "",
                ))
    if value.endswith("/info.json"):
        return value.rsplit("/info.json", 1)[0]
    if "/full/" in value:
        return value.split("/full/", 1)[0]
    return ""


def _wellcome_can_use_work_primary(work):
    """Only let image-like works replace the provider's matched bitmap.

    A digitized book's work thumbnail is commonly its cover or title page,
    even when Wellcome's image search matched a separate illustrated plate.
    ``Pictures`` is an authoritative provider type for image works (including
    postcards), where selecting the designated primary/front view is useful.
    Unknown and document-like types deliberately fail closed to the exact
    image-search hit.
    """
    work_type = work.get("workType") or {}
    return str(work_type.get("label") or "").strip().casefold() == "pictures"


def _wellcome_image_candidates(query, need, exact=False):
    """Follow image result pages so exact verification gets its full budget."""
    if need <= 0:
        return []
    results = []
    page = 1
    page_size = min(100, need)
    while len(results) < need:
        params = {
            "query": query,
            "pageSize": page_size,
            "page": page,
        }
        if exact:
            params["include"] = (
                "source.contributors,source.genres,source.subjects"
            )
        image_url = (
            "https://api.wellcomecollection.org/catalogue/v2/images?"
            + urllib.parse.urlencode(params)
        )
        data = _get_json(image_url, "wellcome") or {}
        page_results = list(data.get("results") or [])
        results.extend(page_results[:need - len(results)])
        if not page_results or not data.get("nextPage"):
            break
        page += 1
    return results


def wellcome(query, need, cue=None, *, exact_phrases=()):
    verify_single_term = len(_wellcome_search_tokens(query)) == 1 and not exact_phrases
    # Replace rejected stem-only hits without changing the caller's requested
    # result budget.  Exact searches already receive a larger candidate window
    # from server.search_batch and are verified there.
    candidate_need = min(400, max(need, need * 3)) if verify_single_term else need
    results = _wellcome_image_candidates(
        query, candidate_need, bool(exact_phrases)
    )
    work_ids = list(dict.fromkeys(
        str((image.get("source") or {}).get("id") or "")
        for image in results
        if (image.get("source") or {}).get("id")
    ))
    def fetch_work(work_id):
        url = (f"https://api.wellcomecollection.org/catalogue/v2/works/{work_id}?"
               + urllib.parse.urlencode({"include": "contributors,production"}))
        return _get_json(url, "wellcome_works") or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(work_ids) or 1)) as pool:
        work_records = pool.map(fetch_work, work_ids)
    work_by_id = dict(zip(work_ids, work_records))
    out = []
    for a in results:
        try:
            iid = a["id"]
            src = a.get("source", {}) or {}
            work_id = str(src.get("id") or "")
            work = work_by_id.get(work_id, {})
            artist, date, medium = _wellcome_work_metadata(work)
            search_text = _wellcome_search_text(work, src)
            if verify_single_term and not _wellcome_single_term_hit_is_supported(
                query, search_text
            ):
                continue
            thumb = (a.get("thumbnail") or {}).get("url", "")
            matched_base = _wellcome_iiif_base(thumb)
            # Image search indexes individual scans, so a postcard reverse can
            # match the metadata of its (highly relevant) parent picture.  Use
            # Wellcome's designated primary/front view for picture works while
            # retaining the matched image as provenance.  For books and other
            # document-like or unknown types, preserve the exact image hit so a
            # cover or title-page thumbnail cannot replace an illustrated plate.
            primary_base = ""
            if _wellcome_can_use_work_primary(work):
                primary_base = _wellcome_iiif_base(
                    (work.get("thumbnail") or {}).get("url", "")
                )
            base = primary_base or matched_base
            if not base:
                continue
            out.append({"source": "wellcome", "source_id": iid,
                        "work_id": work_id,
                        "is_primary_view": bool(primary_base),
                        "matched_image_url": (f"{matched_base}/full/max/0/default.jpg"
                                              if matched_base else ""),
                        "title": src.get("title", ""), "artist": artist,
                        "date": date, "medium": medium,
                        "description": _description_text(work.get("description")),
                        "search_text": search_text,
                        "license": str((((a.get("thumbnail") or {}).get("license")
                                         or {}).get("label") or "CC-BY/PD")),
                        "page_url": f"https://wellcomecollection.org/works/{work_id}",
                        "image_url": f"{base}/full/max/0/default.jpg",
                        "thumb_url": f"{base}/full/1024,/0/default.jpg",
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out[:need]

# ---------------- Victoria & Albert ----------------

def vam(query, need, cue=None):
    d = _get_json(f"https://api.vam.ac.uk/v2/objects/search?q={_q(query)}"
                  f"&images_exist=1&page_size={need * 2}", "vam")
    records = (d or {}).get("records", [])
    detail_count = min(need, len(records))
    system_numbers = [
        str(a.get("systemNumber") or "") for a in records[:detail_count]
    ]

    def fetch_record(system_number):
        if not system_number:
            return {}
        detail = _get_json(
            "https://api.vam.ac.uk/v2/object/" + urllib.parse.quote(system_number),
            "vam_objects",
        ) or {}
        return detail.get("record") or {}

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(6, len(system_numbers) or 1)
    ) as pool:
        details = list(pool.map(fetch_record, system_numbers))
    details.extend({} for _ in records[detail_count:])

    out = []
    for a, detail in zip(records, details):
        try:
            iiif = a.get("_images", {}).get("_iiif_image_base_url", "")
            if not iiif:
                continue
            narrative = _description_text(
                detail.get("summaryDescription")
                or detail.get("contentDescription")
                or detail.get("physicalDescription")
                or detail.get("briefDescription")
            )
            out.append({"source": "vam", "source_id": a.get("systemNumber", ""),
                        "title": a.get("_primaryTitle", "") or a.get("objectType", ""),
                        "artist": (a.get("_primaryMaker") or {}).get("name", ""),
                        "date": a.get("_primaryDate", ""), "medium": a.get("objectType", ""),
                        "description": narrative,
                        "search_text": {
                            "Description": narrative,
                            "Object history": detail.get("objectHistory"),
                            "Subjects": detail.get("subjects"),
                            "Categories": detail.get("categories"),
                        },
                        "license": "unknown",
                        "page_url": f"https://collections.vam.ac.uk/item/{a.get('systemNumber', '')}",
                        "image_url": f"{iiif}full/full/0/default.jpg",
                        "thumb_url": f"{iiif}full/843,/0/default.jpg",
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out

# ---------------- Wikimedia Commons (text + structured depicts) ----------------

def commons(query, need, cue=None, depicts_qid=None):
    gsr = f"haswbstatement:P180={depicts_qid}" if depicts_qid else f"{query} filetype:bitmap"
    d = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "generator": "search", "gsrsearch": gsr,
         "gsrnamespace": "6", "gsrlimit": str(min(need * 2, 50)),
         "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
         "iiurlwidth": "1024", "format": "json"}), "commons")
    out = []
    import re as _re
    for p in ((d or {}).get("query", {}) or {}).get("pages", {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        if not info or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        em = info.get("extmetadata") or {}
        def emv(k):
            return _re.sub(r"<[^>]+>", "", str((em.get(k) or {}).get("value", ""))).strip()
        out.append({"source": "commons", "source_id": p.get("title", ""),
                    "title": p.get("title", "").replace("File:", ""),
                    "artist": emv("Artist")[:80], "date": emv("DateTimeOriginal"),
                    "medium": "", "license": emv("LicenseShortName") or "unknown",
                    "page_url": info.get("descriptionurl", ""),
                    "image_url": info.get("url", ""),
                    "thumb_url": info.get("thumburl") or info.get("url", ""),
                    "width": info.get("width", 0), "height": info.get("height", 0)})
    return out

# ---------------- Web Gallery of Art (local FTS index) ----------------

def wga(query, need, cue=None):
    import sqlite3
    db = os.path.join(BASE, "wga.db")
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    safe = '"' + query.replace('"', "") + '"'
    try:
        rows = con.execute(
            "SELECT author,title,date,technique,location,url,form,type FROM wga "
            "WHERE wga MATCH ? LIMIT ?", (safe, need * 2)).fetchall()
        if not rows:  # loosen: AND of terms
            terms = " AND ".join(w for w in query.replace('"', "").split() if len(w) > 2)
            rows = con.execute(
                "SELECT author,title,date,technique,location,url,form,type FROM wga "
                "WHERE wga MATCH ? LIMIT ?", (terms, need * 2)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for author, title, date, tech, loc, url, form, typ in rows:
        img = url.replace("/html/", "/art/").replace(".html", ".jpg")
        out.append({"source": "wga", "source_id": url,
                    "title": title, "artist": author, "date": date,
                    "medium": f"{form}; {tech}"[:80], "license": "PD (WGA scan)",
                    "page_url": url, "image_url": img, "thumb_url": img,
                    "width": 0, "height": 0})
    return out

# ---------------- Rijksmuseum (keyless Linked Art API) ----------------
# Search returns LOD ids; each candidate resolves in three cached hops:
# object (title/artist/date/objnum) → VisualItem (license) → DigitalObject
# (IIIF image). data.rijksmuseum.nl serves JSON-LD only with Accept header.

_LD = {"Accept": "application/ld+json"}
_AAT_EN = "http://vocab.getty.edu/aat/300388277"

def _rijks_get(lod_id):
    return _get_json("https://data.rijksmuseum.nl/" + lod_id.rsplit("/", 1)[-1],
                     "rijksmuseum", headers=_LD)

def _rijks_en(entries):
    first = ""
    for n in entries or []:
        if n.get("type") != "Name" or not n.get("content"):
            continue
        if _AAT_EN in [l.get("id") for l in n.get("language") or []]:
            return n["content"]
        first = first or n["content"]
    return first

def _rijks_notation_en(obj):
    for n in obj.get("notation") or []:
        if n.get("@language") == "en":
            return n.get("@value", "")
    return ((obj.get("notation") or [{}])[0]).get("@value", "")


def _rijks_description(obj):
    """Extract an English display description from Rijks Linked Art text."""
    candidates = []

    def walk(value):
        if isinstance(value, dict):
            content = _description_text(value.get("content"))
            classifications = json.dumps(value.get("classified_as") or []).lower()
            if content and any(marker in classifications for marker in (
                "300435416",  # Linked Art Description
                "300048722",  # Rijks display/gallery text
            )):
                languages = json.dumps(value.get("language") or []).lower()
                candidates.append(("300388277" not in languages, content))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj.get("subject_of") or [])
    walk(obj.get("referred_to_by") or [])
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates[0][1] if candidates else ""


def _rijks_query_description(obj, query):
    """Return the field-local Rijks passage that actually caused the match."""
    phrase = _rijks_normalize_phrase(query)
    if not phrase:
        return ""
    candidates = []

    def walk(value, inherited_languages=()):
        if isinstance(value, dict):
            own_languages = tuple(
                item.get("id", "") for item in value.get("language") or []
                if isinstance(item, dict)
            )
            languages = own_languages or inherited_languages
            if value.get("type") == "LinguisticObject":
                content = _description_text(value.get("content"))
                normalized = _rijks_normalize_phrase(content)
                if content and f" {phrase} " in f" {normalized} ":
                    candidates.append((_AAT_EN not in languages, content))
            for child in value.values():
                walk(child, languages)
        elif isinstance(value, list):
            for child in value:
                walk(child, inherited_languages)

    walk(obj.get("subject_of") or [])
    walk(obj.get("referred_to_by") or [])
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates[0][1] if candidates else ""


def _rijks_normalize_phrase(value):
    """Normalize text for a contiguous, whole-token phrase comparison."""
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _rijks_phrase_matches(obj, query):
    """Match a query within one Rijks title/description, never across fields."""
    phrase = _rijks_normalize_phrase(query)
    if not phrase:
        return False

    texts = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") in ("Name", "LinguisticObject"):
                content = value.get("content")
                if content:
                    texts.append(content)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    # These are the Linked Art locations used for object titles and descriptive
    # text. Keep each scalar separate so adjacent fields cannot form a match.
    walk(obj.get("identified_by") or [])
    walk(obj.get("subject_of") or [])
    walk(obj.get("referred_to_by") or [])
    needle = f" {phrase} "
    return any(needle in f" {_rijks_normalize_phrase(text)} " for text in texts)


def _rijks_search_ids(param, query, limit):
    """Return up to limit unique search IDs, following Linked Art pages."""
    url = ("https://data.rijksmuseum.nl/search/collection?"
           + urllib.parse.urlencode({
               param: query,
               "imageAvailable": "true",
           }))
    out = []
    seen = set()
    while url and len(out) < limit:
        data = _get_json(url, "rijksmuseum", headers=_LD) or {}
        for item in data.get("orderedItems") or []:
            lod_id = item.get("id", "")
            if lod_id and lod_id not in seen:
                seen.add(lod_id)
                out.append(lod_id)
                if len(out) >= limit:
                    break
        next_url = ((data.get("next") or {}).get("id") or "")
        url = next_url if next_url and next_url != url else ""
    return out


def rijksmuseum(query, need, cue=None):
    out = []
    target = max(need * 2, need)
    # Search both fields instead of letting description false positives prevent
    # title retrieval. Four candidate windows per requested output provides
    # room for phrase filtering; _rijks_search_ids paginates for large windows.
    per_field_limit = max(need * 4, 20)
    candidate_ids = []
    seen_ids = set()
    field_ids = {
        param: _rijks_search_ids(param, query, per_field_limit)
        for param in ("title", "description")
    }
    # Interleave ranks so a long run of description false positives cannot
    # consume the verification budget before strong title matches are tried.
    for index in range(max((len(ids) for ids in field_ids.values()), default=0)):
        for param in ("title", "description"):
            ids = field_ids[param]
            if index >= len(ids):
                continue
            lod_id = ids[index]
            if lod_id not in seen_ids:
                seen_ids.add(lod_id)
                candidate_ids.append(lod_id)

    for lod_id in candidate_ids:
        if len(out) >= target:
            break
        try:
            o = _rijks_get(lod_id)
            if not o or not _rijks_phrase_matches(o, query):
                continue
            vi = _rijks_get((o.get("shows") or [{}])[0].get("id", ""))
            do = _rijks_get(((vi or {}).get("digitally_shown_by")
                             or [{}])[0].get("id", ""))
            iiif = ((do or {}).get("access_point") or [{}])[0].get("id", "")
            if "/full/" not in iiif:
                continue
            base = iiif.split("/full/")[0]
            lic = "unknown"
            for r in (vi.get("subject_to") or []):
                for c in r.get("classified_as") or []:
                    cid = c.get("id", "")
                    if "publicdomain/mark" in cid: lic = "PD"
                    elif "publicdomain/zero" in cid: lic = "CC0"
                    elif "licenses/by" in cid: lic = "CC-BY"
            prod = o.get("produced_by") or {}
            artist = ""
            for part in (prod.get("part") or [prod]):
                for p in part.get("carried_out_by") or []:
                    artist = artist or _rijks_notation_en(p)
            objnum = next((i["content"] for i in o.get("identified_by") or []
                           if i.get("type") == "Identifier" and i.get("content")), "")
            out.append({"source": "rijksmuseum",
                        "source_id": objnum or lod_id,
                        "title": _rijks_en(o.get("identified_by")),
                        "artist": artist,
                        "date": _rijks_en((prod.get("timespan") or {}).get("identified_by")),
                        "medium": "", "license": lic,
                        "description": (
                            _rijks_query_description(o, query)
                            or _rijks_description(o)
                        ),
                        "page_url": f"https://www.rijksmuseum.nl/en/collection/{objnum}"
                                    if objnum else lod_id,
                        "image_url": f"{base}/full/max/0/default.jpg",
                        "thumb_url": f"{base}/full/1024,/0/default.jpg",
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out

# ---------------- gnosisvn.org WordPress media library (house) ----------------
# The site's own curated library — already licensed, cropped, and styled for
# the corpus, so the unified ranker gives it a small near-tie boost. Since 2026-08-08 the
# primary search is a local FTS5 index (gnosis.db, built by gnosis_index.py
# from the crawl + LLM descriptions — see museum-search-adapters-plan); the
# live WP REST search tops up when the local index is short or absent.
# Filenames carry English artwork names, captions are Vietnamese.

_GNOSIS_SEL = ("SELECT id,title,caption_vi,gen_caption_vi,page_url,"
               "image_url,thumb_url,width,height FROM gnosis "
               "WHERE gnosis MATCH ? LIMIT ?")

def _gnosis_local(query, need):
    import sqlite3
    db = os.path.join(BASE, "gnosis.db")
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    safe = '"' + query.replace('"', "") + '"'
    try:
        rows = con.execute(_GNOSIS_SEL, (safe, need * 2)).fetchall()
        if not rows:  # loosen: AND of terms (the wga pattern)
            terms = " AND ".join(w for w in query.replace('"', "").split()
                                 if len(w) > 2)
            if terms:
                rows = con.execute(_GNOSIS_SEL, (terms, need * 2)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for mid, title, cap, gcap, page, img, thumb, w, h in rows:
        out.append({"source": "gnosis", "source_id": str(mid),
                    "title": title, "artist": "", "date": "",
                    "medium": (cap or gcap or "")[:120],
                    "license": "house (gnosisvn.org)",
                    "page_url": page, "image_url": img,
                    "thumb_url": thumb or img,
                    "width": w or 0, "height": h or 0})
    return out

def gnosis(query, need, cue=None):
    # WordPress is authoritative and includes uploads made since the last
    # local-index crawl. The FTS index then adds caption/description matches
    # that native WordPress search may not understand.
    out = _gnosis_live(query, need)
    seen = {c["source_id"] for c in out}
    out += [c for c in _gnosis_local(query, need)
            if c["source_id"] not in seen]
    return out

def _gnosis_live(query, need, cue=None):
    import html as _html, re as _re
    from gnosis_catalog import artwork_metadata
    verify_single_term = len(_wellcome_search_tokens(query)) == 1
    d = _get_json("https://gnosisvn.org/wp-json/wp/v2/media?"
                  + urllib.parse.urlencode({"search": query, "media_type": "image",
                                            "per_page": min(need * 2, 50)}),
                  "gnosis", ttl_ok=False)
    out = []
    for m in d if isinstance(d, list) else []:
        try:
            if not str(m.get("mime_type", "")).startswith("image/"):
                continue
            md = m.get("media_details", {}) or {}
            sizes = md.get("sizes") or {}
            thumb = next((sizes[s]["source_url"] for s in
                          ("1536x1536", "medium_large", "large")
                          if (sizes.get(s) or {}).get("source_url")), "")
            cap = _html.unescape(_re.sub(r"<[^>]+>", " ",
                                         (m.get("caption") or {}).get("rendered", "")))
            cap = _re.sub(r"\s*…?\s*More\s+\S.*$", "", " ".join(cap.split()))
            english = str((m.get("jetpack_videopress") or {}).get("description") or "")
            if not english:
                english = _html.unescape(_re.sub(
                    r"<[^>]+>", " ", (m.get("description") or {}).get("rendered", "")
                ))
            english = " ".join(english.split())
            title = _html.unescape((m.get("title") or {}).get("rendered", ""))
            if verify_single_term and not _wellcome_single_term_hit_is_supported(
                query, [title, english, cap]
            ):
                continue
            image_meta = (md.get("image_meta") or {})
            artist, artwork_date = artwork_metadata(
                title=title,
                description=english, caption=cap,
                credit=image_meta.get("credit", ""),
                metadata_caption=image_meta.get("caption", ""),
            )
            out.append({"source": "gnosis", "source_id": str(m["id"]),
                        "title": title,
                        "artist": artist, "date": artwork_date,
                        "medium": " · ".join(filter(None, [english[:500], cap[:160]])),
                        "description": " · ".join(filter(None, [english, cap])),
                        "license": "house (gnosisvn.org)",
                        "page_url": m.get("link", ""),
                        "image_url": m.get("source_url", ""),
                        "thumb_url": thumb or m.get("source_url", ""),
                        "width": md.get("width") or 0, "height": md.get("height") or 0})
        except Exception:
            continue
    return out

# ---------------- Europeana (aggregator, key) ----------------

_EU_LICENSE = [("publicdomain/mark", "PD"), ("publicdomain/zero", "CC0"),
               ("licenses/by-sa", "CC-BY-SA"), ("licenses/by-nc", "CC-BY-NC"),
               ("licenses/by", "CC-BY")]
_NHM_DATA_PROVIDER = "the trustees of the natural history museum, london"
_NHM_COLLECTION_RESOURCE = "05ff2255-c38a-40c9-b657-4ccb55ab2feb"
_NHM_MEDIA_ASSET = re.compile(
    r"^/media/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


def _europeana_nhm_asset_id(item):
    """Return the NHM media UUID only for the exact trusted provider route."""
    providers = {
        str(value or "").strip().casefold()
        for value in (item.get("dataProvider") or [])
    }
    if _NHM_DATA_PROVIDER not in providers:
        return ""
    image_url = str((item.get("edmIsShownBy") or [""])[0] or "")
    parsed = urllib.parse.urlsplit(image_url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != "data.nhm.ac.uk":
        return ""
    matched = _NHM_MEDIA_ASSET.fullmatch(parsed.path)
    return matched.group(1).casefold() if matched else ""


def _nhm_media_categories(asset_id):
    """Read the provider's authoritative category for one exact media asset."""
    data = _get_json(
        "https://data.nhm.ac.uk/api/3/action/datastore_search?"
        + urllib.parse.urlencode({
            "resource_id": _NHM_COLLECTION_RESOURCE,
            "q": asset_id,
            "limit": 5,
            "fields": "associatedMedia",
        }),
        "nhm_media",
        headers={"User-Agent": "Gnosis-Image-Search/0.2"},
        timeout=12,
        attempts=2,
    )
    categories = set()
    records = (((data or {}).get("result") or {}).get("records") or [])
    for record in records:
        for media in record.get("associatedMedia") or []:
            if str(media.get("assetID") or "").casefold() != asset_id:
                continue
            category = str(media.get("category") or "").strip().casefold()
            if category:
                categories.add(category)
    return categories


def _filter_europeana_nhm_registers(items):
    """Exclude NHM register scans while retaining specimen media and failures."""
    assets = {
        asset_id for item in items
        if (asset_id := _europeana_nhm_asset_id(item))
    }
    if not assets:
        return list(items)
    categories = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(3, len(assets)),
    ) as pool:
        futures = {
            pool.submit(_nhm_media_categories, asset_id): asset_id
            for asset_id in assets
        }
        for future in concurrent.futures.as_completed(futures):
            asset_id = futures[future]
            try:
                categories[asset_id] = future.result()
            except Exception:
                categories[asset_id] = set()
    return [
        item for item in items
        if categories.get(_europeana_nhm_asset_id(item)) != {"register"}
    ]


def _europeana_is_pdf_delivery(item):
    """Reject document files misclassified upstream as image deliveries."""
    image_url = str((item.get("edmIsShownBy") or [""])[0] or "")
    return urllib.parse.urlsplit(image_url).path.casefold().endswith(".pdf")


def europeana(query, need, cue=None):
    import keys as _keys
    k = _keys.get_key("europeana")
    if not k:
        return []
    d = _get_json(
        "https://api.europeana.eu/record/v2/search.json?"
        + urllib.parse.urlencode(
            {"query": query, "rows": min(max(need * 4, 12), 50),
             "media": "true", "qf": "TYPE:IMAGE",
             "reusability": "open", "profile": "rich"}),
        "europeana", headers={"X-Api-Key": k},
    )
    if _europeana_access_error_message(d):
        raise EuropeanaAccessError()
    out = []
    items = [
        item for item in (d or {}).get("items", [])
        if not _europeana_is_pdf_delivery(item)
    ]
    items = _filter_europeana_nhm_registers(items)
    for a in items:
        try:
            img = (a.get("edmIsShownBy") or [""])[0]
            if not img:
                continue
            rights = (a.get("rights") or [""])[0]
            lic = next((v for pat, v in _EU_LICENSE if pat in rights), "unknown")
            out.append({"source": "europeana", "source_id": a.get("id", ""),
                        "title": (a.get("title") or [""])[0],
                        "artist": (a.get("dcCreator") or [""])[0],
                        "date": str((a.get("year") or [""])[0]),
                        "medium": (a.get("dataProvider") or [""])[0],
                        "description": _description_text(
                            a.get("dcDescriptionLangAware")
                            or a.get("dcDescription")
                        ),
                        "search_text": {
                            "Description": (
                                a.get("dcDescriptionLangAware")
                                or a.get("dcDescription")
                            ),
                            "Subject": a.get("dcSubjectLangAware") or a.get("dcSubject"),
                            "Type": a.get("dcTypeLangAware") or a.get("dcType"),
                            "Alternative title": a.get("dctermsAlternative"),
                            "Provider": a.get("dataProvider"),
                        },
                        "license": lic,
                        "page_url": (a.get("edmIsShownAt") or [""])[0]
                                    or f'https://www.europeana.eu/item{a.get("id", "")}',
                        "image_url": img,
                        "thumb_url": (a.get("edmPreview") or [img])[0],
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out

# ---------------- Openverse (Tier C aggregator — keyless, CC-licensed) ----------------

_OV_LICENSE = {"pdm": "PD", "cc0": "CC0", "by": "CC-BY", "by-sa": "CC-BY-SA",
               "by-nc": "CC-BY-NC", "by-nd": "CC-BY-ND", "by-nc-sa": "CC-BY-NC-SA",
               "by-nc-nd": "CC-BY-NC-ND"}

def openverse(query, need, cue=None):
    d = _get_json("https://api.openverse.org/v1/images/?"
                  + urllib.parse.urlencode(
                      {"q": query, "page_size": min(need * 2, 20)}), "openverse")
    out = []
    for r in (d or {}).get("results", []):
        try:
            if not r.get("url"):
                continue
            out.append({"source": "openverse", "source_id": r.get("id", ""),
                        "title": r.get("title", ""),
                        "artist": r.get("creator", ""), "date": "",
                        "medium": r.get("source", ""),   # providing collection
                        "license": _OV_LICENSE.get(r.get("license", ""), "unknown"),
                        "page_url": r.get("foreign_landing_url", ""),
                        "image_url": r["url"],
                        "thumb_url": r.get("thumbnail") or r["url"],
                        "width": r.get("width") or 0, "height": r.get("height") or 0})
        except Exception:
            continue
    return out

# ---------------- Google Image Search (Tier D — general web, keyed) ----------------
# OFF by default: never in msearch's DEFAULT_ORDER; reachable only when a cue
# explicitly lists "google" in its prefer routing. Everything returned is
# license "unclear (google)" until a human confirms rights (master plan Tier D
# rule). Junk domains are pre-filtered; the depicts/watermark/art-age funnel
# does the rest.

_GOOGLE_BLOCK = ("pinterest.", "fineartamerica.", "alamy.", "gettyimages.",
                 "shutterstock.", "istockphoto.", "dreamstime.", "redbubble.",
                 "etsy.", "ebay.", "amazon.", "aliexpress.", "walmart.",
                 "society6.", "posterlounge.", "allposters.", "art.com",
                 "granger.", "posterazzi.", "bridgemanimages.", "akg-images.",
                 "mediastorehouse.", "meisterdrucke.", "artres.", "photos12.",
                 # AI-art hubs and wallpaper farms (user feedback 2026-08-08)
                 "deviantart.", "artstation.", "civitai.", "midjourney.",
                 "lexica.", "openart.", "nightcafe.", "playgroundai.",
                 "wallhaven.", "wallpaper", "peakpx.", "pxfuel.")

# web-search queries only: negative terms to pre-filter AI slop (museum
# adapters never need this — their corpora can't contain it)
_WEB_NEG = " -AI -midjourney -\"stable diffusion\" -wallpaper -deviantart"

def google(query, need, cue=None):
    import keys as _keys
    k, cx = _keys.get_key("google_cse"), _keys.get_key("google_cx")
    if not k or not cx:
        return []
    d = _get_json("https://www.googleapis.com/customsearch/v1?"
                  + urllib.parse.urlencode(
                      {"key": k, "cx": cx, "q": query, "searchType": "image",
                       "num": min(max(need * 2, 1), 10), "safe": "off"}), "google")
    out = []
    for it in (d or {}).get("items", []):
        try:
            link = it.get("link", "")
            host = (it.get("displayLink") or "").lower()
            if not link or any(b in host or b in link.lower() for b in _GOOGLE_BLOCK):
                continue
            im = it.get("image", {}) or {}
            out.append({"source": "google", "source_id": link,
                        "title": it.get("title", ""), "artist": "", "date": "",
                        "medium": host,          # source domain, for review context
                        "license": "unclear (google)",
                        "page_url": im.get("contextLink", link),
                        # thumbnailLink is ~150px — too small to score fairly;
                        # score on the full image like the WGA adapter does
                        "image_url": link, "thumb_url": link,
                        "width": im.get("width", 0), "height": im.get("height", 0)})
        except Exception:
            continue
    return out

# ---------------- Brave Image Search (Tier D — general web, keyed) ----------------
# Same Tier D rules as google: OFF by default, prefer-routing only,
# rights "unclear (web)" until human confirmation, junk-domain blocklist.

def brave(query, need, cue=None):
    import keys as _keys
    k = _keys.get_key("brave")
    if not k:
        return []
    d = _get_json("https://api.search.brave.com/res/v1/images/search?"
                  + urllib.parse.urlencode(
                      {"q": query + _WEB_NEG, "count": min(need * 2, 20),
                       "safesearch": "off"}),
                  "brave", headers={"X-Subscription-Token": k,
                                    "Accept": "application/json"})
    out = []
    for r in (d or {}).get("results", []):
        try:
            img = (r.get("properties") or {}).get("url", "")
            page = r.get("url", "")
            host = (r.get("source") or page).lower()
            if not img or any(b in host or b in img.lower() for b in _GOOGLE_BLOCK):
                continue
            out.append({"source": "brave", "source_id": img,
                        "title": r.get("title", ""), "artist": "", "date": "",
                        "medium": r.get("source", ""),  # hosting domain, for review
                        "license": "unclear (web)",
                        "page_url": page,
                        "image_url": img,
                        "thumb_url": (r.get("thumbnail") or {}).get("src") or img,
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out

ADAPTERS = {"gnosis": gnosis, "wga": wga, "cleveland": cleveland, "met": met,
            "rijksmuseum": rijksmuseum, "aic": aic,
            "smk": smk, "wellcome": wellcome, "vam": vam,
            "commons": commons, "europeana": europeana,
            "openverse": openverse, "google": google,
            "brave": brave}
