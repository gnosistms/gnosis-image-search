#!/usr/bin/env python3
"""Package configured collection credentials without printing secret data."""

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
    providers = {"europeana": europeana_key}
    harvard_key = keys.get_key("harvard") or keys.get_key("harvard_art_museums")
    if harvard_key:
        providers["harvard"] = harvard_key
    keys.write_encrypted(providers, args.output_dir)
    print(f"Packaged encrypted credentials for {', '.join(providers)} in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
