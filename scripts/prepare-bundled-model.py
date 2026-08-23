#!/usr/bin/env python3
"""Stage the default SigLIP checkpoint for its separate release package."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT = Path(__file__).resolve().parent.parent
PROFILE_ID = "pamela-siglip2-base-v1"
CHECKPOINT = "google/siglip2-base-patch16-256"
CHECKPOINT_REVISION = "3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab"
OUTPUT = PROJECT / "build" / "bundled-models" / PROFILE_ID
SNAPSHOT = OUTPUT / "snapshot"
RUNTIME_FILES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def main() -> None:
    shutil.rmtree(OUTPUT, ignore_errors=True)
    OUTPUT.mkdir(parents=True)
    snapshot = Path(snapshot_download(
        CHECKPOINT,
        revision=CHECKPOINT_REVISION,
        local_dir=SNAPSHOT,
        allow_patterns=[*RUNTIME_FILES, "LICENSE*", "README*"],
    ))
    shutil.rmtree(SNAPSHOT / ".cache", ignore_errors=True)
    files = []
    for name in RUNTIME_FILES:
        model_file = snapshot / name
        if not model_file.is_file():
            raise RuntimeError(f"Downloaded checkpoint is missing {name}")
        files.append({
            "path": model_file.relative_to(OUTPUT).as_posix(),
            "size": model_file.stat().st_size,
        })
    manifest = {
        "schemaVersion": 1,
        "profileId": PROFILE_ID,
        "checkpoint": CHECKPOINT,
        "revision": CHECKPOINT_REVISION,
        "files": files,
    }
    (OUTPUT / "gnosis-model-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Staged {CHECKPOINT} ({sum(item['size'] for item in files) / 2**30:.2f} GiB)")


if __name__ == "__main__":
    main()
