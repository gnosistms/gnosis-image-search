"""Official Harvard image access shared by previews, scoring, and comparison."""
import http.cookies
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from search_runtime import check_work, network_timeout, pause, count

class ImageProxyError(RuntimeError):
    pass

AIC_PROXY_UA = "GnosisImages/0.1 (museum image search)"
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
    with urllib.request.urlopen(request, timeout=network_timeout(30)) as response:
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
    while not HARVARD_COOKIE_LOCK.acquire(timeout=.1):
        check_work()
    try:
        check_work()
        if (not force and HARVARD_COOKIE_HEADER
                and time.time() < HARVARD_COOKIE_EXPIRES):
            return HARVARD_COOKIE_HEADER
        return _refresh_harvard_cookie(page_url)
    finally:
        HARVARD_COOKIE_LOCK.release()


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


def fetch_harvard_preview(
    item: dict, detail: bool = False, attempts: int = 4, cookie_provider=None,
) -> tuple[bytes, str]:
    """Fetch a Harvard CDN image, recovering from transient rate limits."""
    cookie_provider = cookie_provider or harvard_cookie
    source_url = str(item.get("image_url" if detail else "thumb_url") or "")
    cdn_url = harvard_cdn_url(source_url)
    page_url = str(item.get("page_url") or "")
    page = urllib.parse.urlparse(page_url)
    if page.scheme != "https" or page.hostname not in HARVARD_PAGE_HOSTS:
        raise ImageProxyError("The Harvard object page is not allowed.")
    last_error = None
    while not HARVARD_PROXY_CONCURRENCY.acquire(timeout=.1):
        check_work()
    try:
        for attempt in range(attempts):
            check_work()
            try:
                request = urllib.request.Request(cdn_url, headers={
                    "User-Agent": AIC_PROXY_UA,
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
                    # Refresh once after the first image failure. Further
                    # retries reuse that fresh cookie instead of adding more
                    # load to Harvard's object page while it is rate-limiting.
                    "Cookie": cookie_provider(page_url, force=attempt == 1),
                    "Referer": page_url,
                })
                count("image_downloads")
                with urllib.request.urlopen(request, timeout=network_timeout(30)) as response:
                    data = response.read(HARVARD_PROXY_MAX_BYTES + 1)
                count("image_bytes", len(data))
                check_work()
                if len(data) > HARVARD_PROXY_MAX_BYTES:
                    raise ImageProxyError("The Harvard preview exceeds the size limit.")
                return data, image_mime_type(data)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError,
                    ImageProxyError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    pause(min(0.5 * (2 ** attempt), 2.0))
    finally:
        HARVARD_PROXY_CONCURRENCY.release()
    raise ImageProxyError("The Harvard preview is unavailable.") from last_error
