#!/usr/bin/env python3
"""Stage the default SigLIP checkpoint for its separate release package."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from huggingface_hub import snapshot_download
from safetensors.numpy import load_file, save_file


PROJECT = Path(__file__).resolve().parent.parent
PROFILE_ID = "pamela-siglip2-base-v2"
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

# The published checkpoint stores 32-bit weights. Half precision halves the
# download; Apple GPUs already run the model in float16, and the CPU path
# converts the weights back to float32 when it loads them.
STORED_DTYPE = "float16"


def store_half_precision(model_file: Path) -> None:
    """Rewrite 32-bit weights as float16 while keeping the file format."""
    tensors = {
        name: tensor.astype(np.float16) if tensor.dtype == np.float32 else tensor
        for name, tensor in load_file(model_file).items()
    }
    temporary = model_file.with_name(model_file.name + ".tmp")
    # Transformers only accepts safetensors files that declare their format.
    save_file(tensors, temporary, metadata={"format": "pt"})
    temporary.replace(model_file)


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
    store_half_precision(snapshot / "model.safetensors")
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
        "dtype": STORED_DTYPE,
        "files": files,
    }
    (OUTPUT / "gnosis-model-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Staged {CHECKPOINT} ({sum(item['size'] for item in files) / 2**30:.2f} GiB)")


if __name__ == "__main__":
    main()
