#!/usr/bin/env python3
"""Build the platform-neutral image ranking model release asset."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROFILE_ID = "pamela-siglip2-base-v1"
SOURCE = PROJECT / "build" / "bundled-models" / PROFILE_ID
OUTPUT = PROJECT / "out" / "model"
MAGIC = b"GNOSISMODEL1\n"


def sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    package = json.loads((PROJECT / "package.json").read_text(encoding="utf-8"))
    manifest_path = SOURCE / "gnosis-model-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("profileId") != PROFILE_ID:
        raise RuntimeError("The staged model manifest has the wrong profile id")

    paths = sorted(
        candidate for candidate in SOURCE.rglob("*")
        if candidate.is_file() and candidate != manifest_path
    )
    entries = []
    offset = 0
    for candidate in paths:
        size = candidate.stat().st_size
        entries.append({
            "path": candidate.relative_to(SOURCE).as_posix(),
            "size": size,
            "sha256": sha256(candidate),
            "offset": offset,
        })
        offset += size

    header = json.dumps({
        "schemaVersion": 1,
        "manifest": manifest,
        "files": entries,
    }, separators=(",", ":")).encode("utf-8")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / f"Gnosis-Images-Image-Ranking-Model-{package['version']}.gnosis-model"
    with destination.open("wb") as output:
        output.write(MAGIC)
        output.write(struct.pack(">Q", len(header)))
        output.write(header)
        for candidate in paths:
            with candidate.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    print(f"Packaged {destination.name} ({destination.stat().st_size / 2**30:.2f} GiB)")


if __name__ == "__main__":
    main()
