"""Museum/archive search adapters. Every adapter returns normalized candidates:
{source, source_id, title, artist, date, medium, license, page_url,
 image_url, thumb_url, width, height}
All requests are disk-cached and polite. Unconfigured/failing sources return [].
"""
import base64, concurrent.futures, hashlib, io, json, os, threading, time
import urllib.request, urllib.parse

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

# ---------------- Cleveland Museum of Art ----------------

def cleveland(query, need, cue=None):
    d = _get_json(f"https://openaccess-api.clevelandart.org/api/artworks/"
                  f"?q={_q(query)}&has_image=1&limit={need * 2}", "cleveland")
    out = []
    for a in (d or {}).get("data", []):
        try:
            img = a.get("images", {}) or {}
            web, full = img.get("web") or {}, img.get("print") or img.get("web") or {}
            if not web.get("url"):
                continue
            out.append({"source": "cleveland", "source_id": str(a["id"]),
                        "title": a.get("title", ""),
                        "artist": (a.get("creators") or [{}])[0].get("description", ""),
                        "date": a.get("creation_date", ""), "medium": a.get("type", ""),
                        "license": "CC0" if a.get("share_license_status") == "CC0" else "unknown",
                        "page_url": a.get("url", ""), "image_url": full.get("url", web["url"]),
                        "thumb_url": web["url"],
                        "width": int(full.get("width") or web.get("width") or 0),
                        "height": int(full.get("height") or web.get("height") or 0)})
        except Exception:
            continue
    return out

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

def aic(query, need, cue=None):
    d = _get_json(f"https://api.artic.edu/api/v1/artworks/search?q={_q(query)}"
                  f"&limit={need * 2}&fields=id,title,artist_display,date_display,"
                  f"medium_display,image_id,is_public_domain,thumbnail", "aic")
    if _aic_search_is_fallback(d):
        return []
    artworks = [a for a in (d or {}).get("data", [])
                if a.get("image_id") and a.get("is_public_domain")][:need]
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
            image_url = f"{iiif}/full/1686,/0/default.jpg"
            thumb_url = f"{iiif}/full/843,/0/default.jpg"
            delivery = "aic"
            preview_width = preview_height = 0
        page_url = f"https://www.artic.edu/artworks/{a['id']}"
        out.append({"source": "aic", "source_id": str(a["id"]),
                    "title": a.get("title", ""), "artist": a.get("artist_display", ""),
                    "date": a.get("date_display", ""), "medium": a.get("medium_display", ""),
                    "license": "PD/CC0",
                    "page_url": page_url,
                    "image_url": image_url,
                    "thumb_url": thumb_url,
                    "placeholder_url": thumbnail.get("lqip", ""),
                    "image_delivery": delivery,
                    "full_resolution_url": page_url,
                    "preview_width": preview_width,
                    "preview_height": preview_height,
                    # These are AIC's native dimensions, not the 843 px mirror.
                    "width": native_width, "height": native_height})
    return out

# ---------------- Statens Museum for Kunst ----------------

def smk(query, need, cue=None):
    d = _get_json(f"https://api.smk.dk/api/v1/art/search/?keys={_q(query)}"
                  f"&filters=%5Bhas_image%3Atrue%5D&rows={need * 2}", "smk")
    out = []
    for a in (d or {}).get("items", []):
        try:
            if not a.get("image_native") and not a.get("image_thumbnail"):
                continue
            titles = a.get("titles") or [{}]
            prod = (a.get("production") or [{}])[0]
            out.append({"source": "smk", "source_id": a.get("object_number", ""),
                        "title": titles[0].get("title", ""),
                        "artist": prod.get("creator", "") or ", ".join(a.get("artist") or []),
                        "date": ((a.get("production_date") or [{}])[0] or {}).get("period", ""),
                        "medium": ", ".join(a.get("techniques") or []),
                        "license": "CC0" if a.get("public_domain") else "unknown",
                        "page_url": a.get("frontend_url", ""),
                        "image_url": a.get("image_native") or a.get("image_thumbnail"),
                        "thumb_url": a.get("image_thumbnail") or a.get("image_native"),
                        "width": a.get("image_width") or 0, "height": a.get("image_height") or 0})
        except Exception:
            continue
    return out

# ---------------- Wellcome Collection ----------------

def wellcome(query, need, cue=None):
    d = _get_json(f"https://api.wellcomecollection.org/catalogue/v2/images"
                  f"?query={_q(query)}&pageSize={min(need * 2, 100)}", "wellcome")
    out = []
    for a in (d or {}).get("results", []):
        try:
            iid = a["id"]
            src = a.get("source", {}) or {}
            thumb = (a.get("thumbnail") or {}).get("url", "")
            # thumbnail.url is the IIIF info.json (image NUMBER, not the
            # catalogue id) — strip it to get the IIIF base
            if thumb.endswith("/info.json"):
                base = thumb.rsplit("/info.json", 1)[0]
            elif "/full/" in thumb:
                base = thumb.split("/full/")[0]
            else:
                continue
            out.append({"source": "wellcome", "source_id": iid,
                        "title": src.get("title", ""), "artist": "", "date": "",
                        "medium": "", "license": "CC-BY/PD",
                        "page_url": f"https://wellcomecollection.org/works/{src.get('id', '')}/images?id={iid}",
                        "image_url": f"{base}/full/max/0/default.jpg",
                        "thumb_url": f"{base}/full/1024,/0/default.jpg",
                        "width": 0, "height": 0})
        except Exception:
            continue
    return out

# ---------------- Victoria & Albert ----------------

def vam(query, need, cue=None):
    d = _get_json(f"https://api.vam.ac.uk/v2/objects/search?q={_q(query)}"
                  f"&images_exist=1&page_size={need * 2}", "vam")
    out = []
    for a in (d or {}).get("records", []):
        try:
            iiif = a.get("_images", {}).get("_iiif_image_base_url", "")
            if not iiif:
                continue
            out.append({"source": "vam", "source_id": a.get("systemNumber", ""),
                        "title": a.get("_primaryTitle", "") or a.get("objectType", ""),
                        "artist": (a.get("_primaryMaker") or {}).get("name", ""),
                        "date": a.get("_primaryDate", ""), "medium": a.get("objectType", ""),
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

def rijksmuseum(query, need, cue=None):
    out = []
    for param in ("description", "title"):
        d = _get_json("https://data.rijksmuseum.nl/search/collection?"
                      + urllib.parse.urlencode({param: query, "imageAvailable": "true"}),
                      "rijksmuseum", headers=_LD)
        for it in ((d or {}).get("orderedItems") or [])[:need * 3]:
            if len(out) >= need * 2:
                break
            try:
                o = _rijks_get(it["id"])
                if not o:
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
                            "source_id": objnum or it["id"],
                            "title": _rijks_en(o.get("identified_by")),
                            "artist": artist,
                            "date": _rijks_en((prod.get("timespan") or {}).get("identified_by")),
                            "medium": "", "license": lic,
                            "page_url": f"https://www.rijksmuseum.nl/en/collection/{objnum}"
                                        if objnum else it["id"],
                            "image_url": f"{base}/full/max/0/default.jpg",
                            "thumb_url": f"{base}/full/1024,/0/default.jpg",
                            "width": 0, "height": 0})
            except Exception:
                continue
        if out:
            break
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
            out.append({"source": "gnosis", "source_id": str(m["id"]),
                        "title": _html.unescape((m.get("title") or {}).get("rendered", "")),
                        "artist": "", "date": "",  # artwork date unknown; upload
                        # date would trip the art-age gate, so leave blank
                        "medium": " · ".join(filter(None, [english[:500], cap[:160]])),
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

def europeana(query, need, cue=None):
    import keys as _keys
    k = _keys.get_key("europeana")
    if not k:
        return []
    d = _get_json(
        "https://api.europeana.eu/record/v2/search.json?"
        + urllib.parse.urlencode(
            {"query": query, "rows": min(need * 2, 50),
             "media": "true", "qf": "TYPE:IMAGE",
             "reusability": "open", "profile": "rich"}),
        "europeana", headers={"X-Api-Key": k},
    )
    if _europeana_access_error_message(d):
        raise EuropeanaAccessError()
    out = []
    for a in (d or {}).get("items", []):
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
