#!/usr/bin/env python3
"""Create the packaged Europeana credential without printing secret data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "vendor"))

import keys  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    europeana_key = keys.get_key("europeana")
    if not europeana_key:
        parser.error(
            "Europeana key not found; set EUROPEANA_API_KEY or SEARCH_KEYS_FILE"
        )
    keys.write_encrypted({"europeana": europeana_key}, args.output_dir)
    print(f"Packaged encrypted Europeana credential in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
