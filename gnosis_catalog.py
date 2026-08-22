"""Current WordPress media catalog owned by the interactive search tool."""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gnosis_fuzzy import prepare_record, search_records


API = "https://gnosisvn.org/wp-json/wp/v2/media"
USER_AGENT = "gnosis-interactive-image-search/1.0"
REFRESH_SECONDS = 30 * 60
MEDIA_FIELDS = (
    "id,modified_gmt,link,title,caption,alt_text,description,filename,"
    "source_url,mime_type,media_details,jetpack_videopress"
)


def _text(value) -> str:
    rendered = value.get("rendered", "") if isinstance(value, dict) else str(value or "")
    rendered = re.sub(r"<[^>]+>", " ", rendered)
    return " ".join(html.unescape(rendered).split())


def wordpress_record(item: dict) -> dict:
    details = item.get("media_details") or {}
    sizes = details.get("sizes") or {}
    thumb = next((sizes[name].get("source_url", "") for name in
                  ("1536x1536", "medium_large", "large", "medium")
                  if isinstance(sizes.get(name), dict) and sizes[name].get("source_url")), "")
    rich_description = str((item.get("jetpack_videopress") or {}).get("description") or "")
    return {
        "id": int(item.get("id") or 0),
        "title": _text(item.get("title")),
        "caption_vi": _text(item.get("caption")),
        "gen_caption_vi": "",
        "alt": str(item.get("alt_text") or "").strip(),
        "wp_description": _text(item.get("description")),
        "filename": str(item.get("filename") or Path(
            urllib.parse.urlparse(str(item.get("source_url") or "")).path
        ).name),
        "description": rich_description,
        "description_vi": "",
        "keywords": rich_description,
        "figures": "",
        "style": "",
        "page_url": str(item.get("link") or ""),
        "image_url": str(item.get("source_url") or ""),
        "thumb_url": thumb or str(item.get("source_url") or ""),
        "width": int(details.get("width") or 0),
        "height": int(details.get("height") or 0),
        "modified": str(item.get("modified_gmt") or item.get("modified") or ""),
    }


class GnosisCatalog:
    def __init__(self, cache_path: str | Path, refresh_seconds: int = REFRESH_SECONDS):
        self.cache_path = Path(cache_path)
        self.refresh_seconds = refresh_seconds
        self.records: list[dict] = []
        self.updated_at = 0.0
        self.latest_modified = ""
        self.remote_total = 0
        self.last_refresh_mode = "cache"
        self.last_error = ""
        self.lock = threading.RLock()
        self._refreshing = False
        self._load_cache()

    def _load_cache(self):
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            records = payload.get("records", payload) if isinstance(payload, dict) else payload
            prepared = [prepare_record(record) for record in records]
            with self.lock:
                self.records = prepared
                self.updated_at = float(payload.get("updated_at", 0)) \
                    if isinstance(payload, dict) else self.cache_path.stat().st_mtime
                self.latest_modified = max(
                    (str(record.get("modified") or "") for record in records), default=""
                )
                self.remote_total = len(records)
        except (OSError, ValueError, TypeError):
            pass

    def _request(self, params: dict) -> tuple[list[dict], int, int]:
        query = urllib.parse.urlencode({**params, "_cb": int(time.time())})
        request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.loads(response.read().decode("utf-8"))
            total = int(response.headers.get("X-WP-Total") or len(batch))
            total_pages = int(response.headers.get("X-WP-TotalPages") or 1)
        return batch, total, total_pages

    def _probe(self) -> tuple[str, int]:
        batch, total, _ = self._request({
            "media_type": "image", "per_page": 1,
            "orderby": "modified", "order": "desc",
            "_fields": "id,modified_gmt",
        })
        latest = str((batch[0] if batch else {}).get("modified_gmt") or "")
        return latest, total

    def _fetch_records(self, modified_after: str = "") -> list[dict]:
        records = []
        page, total_pages = 1, 1
        while page <= total_pages:
            params = {
                "media_type": "image", "per_page": 100, "page": page,
                "orderby": "modified", "order": "asc", "_fields": MEDIA_FIELDS,
            }
            if modified_after:
                params["modified_after"] = modified_after
            batch, _, total_pages = self._request(params)
            records.extend(wordpress_record(item) for item in batch
                           if str(item.get("mime_type") or "").startswith("image/"))
            page += 1
        return records

    @staticmethod
    def _overlap_timestamp(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (parsed - timedelta(seconds=2)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
        except (TypeError, ValueError):
            return value

    def _save(self, records: list[dict], latest_modified: str, total: int, mode: str):
        updated_at = time.time()
        payload = {"updated_at": updated_at, "records": records}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.cache_path)
        prepared = [prepare_record(record) for record in records]
        with self.lock:
            self.records = prepared
            self.updated_at = updated_at
            self.latest_modified = latest_modified
            self.remote_total = total
            self.last_refresh_mode = mode
            self.last_error = ""

    def refresh(self) -> int:
        latest_modified, remote_total = self._probe()
        with self.lock:
            cached = [
                {key: value for key, value in record.items() if key != "_fields"}
                for record in self.records
            ]
            cached_latest = self.latest_modified

        if cached and len(cached) == remote_total and cached_latest == latest_modified:
            with self.lock:
                self.updated_at = time.time()
                self.remote_total = remote_total
                self.last_refresh_mode = "unchanged"
                self.last_error = ""
            return len(cached)

        if cached and cached_latest:
            changed = self._fetch_records(self._overlap_timestamp(cached_latest))
            merged = {int(record["id"]): record for record in cached}
            merged.update({int(record["id"]): record for record in changed})
            if len(merged) == remote_total:
                records = list(merged.values())
                self._save(records, latest_modified, remote_total, "incremental")
                return len(records)

        # A count mismatch means a deletion (possibly paired with an upload),
        # or an incomplete old cache. A full reconciliation is rare but safe.
        records = self._fetch_records()
        reconciled_latest = max(
            (str(record.get("modified") or "") for record in records),
            default=latest_modified,
        )
        self._save(records, reconciled_latest, len(records), "full")
        return len(records)

    def _refresh_in_background(self):
        try:
            self.refresh()
        except Exception as exc:
            with self.lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                self._refreshing = False

    def refresh_if_stale(self) -> bool:
        """Start one refresh after an actual search, never from an idle timer."""
        with self.lock:
            fresh = self.updated_at and time.time() - self.updated_at < self.refresh_seconds
            if fresh or self._refreshing:
                return False
            self._refreshing = True
        threading.Thread(target=self._refresh_in_background, name="gnosis-catalog-refresh",
                         daemon=True).start()
        return True

    def search(self, query: str, limit: int) -> list[dict]:
        self.refresh_if_stale()
        with self.lock:
            records = list(self.records)
        return search_records(records, query, limit)

    def status(self) -> dict:
        with self.lock:
            return {
                "records": len(self.records),
                "updated_at": self.updated_at,
                "latest_modified": self.latest_modified,
                "remote_total": self.remote_total,
                "last_refresh_mode": self.last_refresh_mode,
                "last_error": self.last_error,
                "refreshing": self._refreshing,
            }
