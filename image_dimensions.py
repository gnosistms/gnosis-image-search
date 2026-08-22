"""Resolve maximum provided-image dimensions without retaining image files."""

from __future__ import annotations

import threading
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import ImageFile


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/126.0 Safari/537.36")
HEADER_LIMIT = 384 * 1024
MAX_CACHE = 2048
DIMENSION_CACHE: OrderedDict[str, tuple[int, int]] = OrderedDict()
CACHE_LOCK = threading.RLock()


def inspect_remote_dimensions(url: str) -> tuple[int, int]:
    if not url:
        return 0, 0
    with CACHE_LOCK:
        if url in DIMENSION_CACHE:
            DIMENSION_CACHE.move_to_end(url)
            return DIMENSION_CACHE[url]
    dimensions = (0, 0)
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Range": f"bytes=0-{HEADER_LIMIT - 1}",
        })
        parser = ImageFile.Parser()
        read = 0
        with urllib.request.urlopen(request, timeout=8) as response:
            while read < HEADER_LIMIT and parser.image is None:
                chunk = response.read(min(32 * 1024, HEADER_LIMIT - read))
                if not chunk:
                    break
                read += len(chunk)
                parser.feed(chunk)
        if parser.image is not None:
            dimensions = tuple(int(value) for value in parser.image.size)
    except Exception:
        pass
    with CACHE_LOCK:
        DIMENSION_CACHE[url] = dimensions
        DIMENSION_CACHE.move_to_end(url)
        while len(DIMENSION_CACHE) > MAX_CACHE:
            DIMENSION_CACHE.popitem(last=False)
    return dimensions


def resolve_result_dimensions(items: list[dict]) -> list[dict]:
    unresolved = [item for item in items
                  if not (int(item.get("width") or 0) and int(item.get("height") or 0))]
    if unresolved:
        with ThreadPoolExecutor(max_workers=min(8, len(unresolved))) as pool:
            futures = {
                pool.submit(inspect_remote_dimensions,
                            item.get("image_url") or item.get("thumb_url")): item
                for item in unresolved
            }
            for future in as_completed(futures):
                item = futures[future]
                item["width"], item["height"] = future.result()
    return items
