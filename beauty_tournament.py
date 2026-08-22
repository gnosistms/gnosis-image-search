#!/usr/bin/env python3
"""Persistent pairwise beauty tournament over a local image pool."""

from __future__ import annotations

import csv
import json
import math
import os
import random
import threading
import zipfile
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SEARCH_DATA_DIR") or HERE / "data").expanduser()
POOL_CSV = HERE / "data" / "pamela" / "external-beauty-sample-scores.csv"
STATE_PATH = DATA_DIR / "beauty-tournament" / "ratings.json"
PAMELA_ZIP = HERE / "data" / "pamela" / "PAMELA.zip"
PAMELA_STATE_PATH = DATA_DIR / "beauty-tournament" / "pamela-ratings.json"
SAINT_PETER_POOL = HERE / "data" / "beauty-tournament" / "saint-peter-pool.csv"
SAINT_PETER_STATE_PATH = DATA_DIR / "beauty-tournament" / "saint-peter-ratings.json"
CHOICES = frozenset(("left", "right", "tie", "no_opinion"))


def load_csv_pool(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    pool = []
    for row in sorted(rows, key=lambda item: int(item["sample"])):
        image_path = Path(row["local_image"]).resolve()
        if not image_path.is_file():
            image_path = path.parent / "saint-peter-images" / image_path.name
        if not image_path.is_file():
            continue
        pool.append({
            "id": f"sample-{int(row['sample']):02d}",
            "title": row["title"],
            "source": row["source"],
            "path": str(image_path),
        })
    return pool


def load_pamela_pool(path: Path) -> list[dict]:
    """Load every unique PAMELA image without expanding the 2.1 GB archive."""
    with zipfile.ZipFile(path) as archive:
        metadata = {}
        annotation_names = [
            name for name in archive.namelist()
            if name.startswith("PAMELA/annotations/") and name.endswith(".json")
        ]
        for annotation_name in annotation_names:
            for row in json.loads(archive.read(annotation_name)):
                member = str(row.get("image_path") or "").removeprefix("./")
                if member and not member.startswith("PAMELA/"):
                    member = f"PAMELA/{member}"
                metadata.setdefault(member, row.get("image_metadata") or {})
        image_names = sorted(
            name for name in archive.namelist()
            if name.startswith("PAMELA/images/") and name.lower().endswith(".png")
        )
    pool = []
    for index, member in enumerate(image_names, 1):
        item_metadata = metadata.get(member, {})
        pool.append({
            "id": Path(member).stem,
            "title": f"PAMELA image {index:,}",
            "source": "PAMELA",
            "archive_path": str(path.resolve()),
            "archive_member": member,
            "metadata": item_metadata,
        })
    return pool


def load_pool(path: Path = PAMELA_ZIP) -> list[dict]:
    return load_csv_pool(path) if path.suffix.lower() == ".csv" else load_pamela_pool(path)


def fit_bradley_terry(ids: list[str], votes: list[dict]) -> dict[str, float]:
    """Fit strengths with the Bradley–Terry MM update and a weak neutral prior."""
    index = {item_id: i for i, item_id in enumerate(ids)}
    n = len(ids)
    wins = [0.5] * n
    meetings: Counter[tuple[int, int]] = Counter()
    for vote in votes:
        choice = vote.get("choice")
        if choice == "no_opinion":
            continue
        left = index.get(vote.get("left_id"))
        right = index.get(vote.get("right_id"))
        if left is None or right is None or left == right:
            continue
        if choice == "left":
            wins[left] += 1
        elif choice == "right":
            wins[right] += 1
        elif choice == "tie":
            wins[left] += 0.5
            wins[right] += 0.5
        else:
            continue
        pair = tuple(sorted((left, right)))
        meetings[pair] += 1

    strengths = [1.0] * n
    for _ in range(250):
        # One half-win in one virtual contest against a fixed neutral item.
        # This keeps disconnected images at neutral instead of letting their
        # unconstrained strengths drift during normalization.
        denominators = [1.0 / (value + 1.0) for value in strengths]
        for (left, right), count in meetings.items():
            term = count / max(strengths[left] + strengths[right], 1e-12)
            denominators[left] += term
            denominators[right] += term
        updated = [wins[i] / max(denominators[i], 1e-12) for i in range(n)]
        log_mean = sum(math.log(max(value, 1e-12)) for value in updated) / max(n, 1)
        scale = math.exp(log_mean)
        updated = [value / scale for value in updated]
        if max((abs(math.log(max(updated[i], 1e-12) / strengths[i])) for i in range(n)), default=0) < 1e-9:
            strengths = updated
            break
        strengths = updated
    return {item_id: math.log(max(strengths[index[item_id]], 1e-12)) for item_id in ids}


def choose_pair(ids: list[str], votes: list[dict], scores: dict[str, float]) -> tuple[str, str]:
    shown = Counter()
    compared = Counter()
    for vote in votes:
        left, right = vote.get("left_id"), vote.get("right_id")
        if left in scores and right in scores:
            shown[left] += 1
            shown[right] += 1
            compared[tuple(sorted((left, right)))] += 1

    rng = random.Random(7919 + len(votes) * 104729)
    minimum = min((shown[item_id] for item_id in ids), default=0)
    underexposed = [item_id for item_id in ids if shown[item_id] == minimum]
    left = rng.choice(underexposed)

    # Early comparisons spread exposure; later ones concentrate on close ranks.
    candidates = [item_id for item_id in ids if item_id != left]
    rng.shuffle(candidates)
    candidates.sort(key=lambda item_id: (
        compared[tuple(sorted((left, item_id)))],
        abs(scores[left] - scores[item_id]) if minimum >= 2 else shown[item_id],
        shown[item_id],
    ))
    right = candidates[0]
    return (left, right) if rng.random() < 0.5 else (right, left)


class BeautyTournament:
    def __init__(self, pool_path: Path = POOL_CSV, state_path: Path = STATE_PATH):
        self.pool = load_pool(pool_path)
        self.items = {item["id"]: item for item in self.pool}
        self.state_path = state_path
        self.lock = threading.RLock()
        self.votes = self._load_votes()

    def _load_votes(self) -> list[dict]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return value.get("votes", []) if isinstance(value, dict) else []

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "votes": self.votes}, indent=2), encoding="utf-8")
        temp.replace(self.state_path)

    def snapshot(self) -> dict:
        with self.lock:
            ids = list(self.items)
            scores = fit_bradley_terry(ids, self.votes)
            left, right = choose_pair(ids, self.votes, scores)
            counts = Counter(vote.get("choice") for vote in self.votes)
            ranking = sorted(self.pool, key=lambda item: scores[item["id"]], reverse=True)
            public = lambda item: {
                "id": item["id"], "title": item["title"], "source": item["source"],
                "image_url": f"/api/beauty/image?id={item['id']}",
            }
            return {
                "pair": [public(self.items[left]), public(self.items[right])],
                "total_images": len(ids),
                "votes": len(self.votes),
                "decisions": len(self.votes) - counts["no_opinion"],
                "ties": counts["tie"],
                "no_opinion": counts["no_opinion"],
                "ranking": [dict(public(item), score=scores[item["id"]]) for item in ranking[:100]],
                "ranking_shown": min(100, len(ranking)),
            }

    def vote(self, left_id: str, right_id: str, choice: str) -> dict:
        if left_id not in self.items or right_id not in self.items or left_id == right_id:
            raise ValueError("Invalid image pair.")
        if choice not in CHOICES:
            raise ValueError("Invalid comparison choice.")
        with self.lock:
            self.votes.append({"left_id": left_id, "right_id": right_id, "choice": choice})
            self._save()
            return self.snapshot()

    def undo(self) -> dict:
        with self.lock:
            if self.votes:
                self.votes.pop()
                self._save()
            return self.snapshot()

    def image_bytes(self, item_id: str) -> tuple[bytes, str]:
        item = self.items.get(item_id)
        if not item:
            raise ValueError("Unknown tournament image.")
        if item.get("archive_member"):
            with zipfile.ZipFile(item["archive_path"]) as archive:
                return archive.read(item["archive_member"]), "image/png"
        path = Path(item["path"])
        return path.read_bytes(), "image/jpeg"


try:
    TOURNAMENT = BeautyTournament(SAINT_PETER_POOL, SAINT_PETER_STATE_PATH)
except (OSError, ValueError, KeyError):
    # The desktop search package intentionally omits the optional 75 MB
    # tournament image pack. Search and PAMELA ranking remain fully available.
    TOURNAMENT = None
