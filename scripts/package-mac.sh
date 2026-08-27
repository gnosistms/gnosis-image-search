#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMMAND="${1:-make}"
MACHINE="$(uname -m)"

case "${GNOSIS_TARGET_ARCH:-$MACHINE}" in
  arm64|aarch64) TARGET_ARCH="arm64" ;;
  x64|x86_64|amd64) TARGET_ARCH="x64" ;;
  *)
    echo "Unsupported macOS architecture: ${GNOSIS_TARGET_ARCH:-$MACHINE}" >&2
    exit 1
    ;;
esac

case "$COMMAND" in
  package|make) ;;
  *)
    echo "Usage: $0 [package|make]" >&2
    exit 1
    ;;
esac

if [[ "$TARGET_ARCH" == "arm64" && "$MACHINE" != "arm64" ]] ||
   [[ "$TARGET_ARCH" == "x64" && "$MACHINE" != "x86_64" ]]; then
  echo "The native Python backend must be built on the target architecture ($TARGET_ARCH); runner is $MACHINE." >&2
  exit 1
fi

cd "$PROJECT_DIR"
npm run generate:dmg
npm run build:backend

if [[ "$COMMAND" == "package" ]]; then
  GNOSIS_TARGET_ARCH="$TARGET_ARCH" electron-forge package --platform=darwin --arch="$TARGET_ARCH"
else
  GNOSIS_TARGET_ARCH="$TARGET_ARCH" GNOSIS_DISTRIBUTION=full electron-forge make --platform=darwin --arch="$TARGET_ARCH"
  GNOSIS_TARGET_ARCH="$TARGET_ARCH" GNOSIS_DISTRIBUTION=update electron-forge make --platform=darwin --arch="$TARGET_ARCH"
fi
