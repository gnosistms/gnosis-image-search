#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${BACKEND_PYTHON:-$PROJECT_DIR/../automatic-illustrator/prototype/venv/bin/python}"
PYINSTALLER_DIR="$PROJECT_DIR/.packaging-python"
OUTPUT_DIR="$PROJECT_DIR/build/backend"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.backend-venv/bin/python"
  python3 -m venv "$PROJECT_DIR/.backend-venv"
  "$PYTHON_BIN" -m pip install --quiet -r "$PROJECT_DIR/requirements-backend.txt"
fi

if ! PYTHONPATH="$PYINSTALLER_DIR" "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --quiet --target "$PYINSTALLER_DIR" 'pyinstaller==6.22.2'
fi
if ! PYTHONPATH="$PYINSTALLER_DIR" "$PYTHON_BIN" -c 'import cryptography' >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --quiet --target "$PYINSTALLER_DIR" 'cryptography==46.0.5'
fi
rm -rf "$OUTPUT_DIR" "$PROJECT_DIR/build/pyinstaller" "$PROJECT_DIR/build/gnosis-search-engine.spec"
mkdir -p "$OUTPUT_DIR"

PYINSTALLER_CONFIG_DIR="$PROJECT_DIR/build/pyinstaller-config" PYTHONPATH="$PYINSTALLER_DIR" "$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name gnosis-search-engine \
  --distpath "$OUTPUT_DIR" \
  --workpath "$PROJECT_DIR/build/pyinstaller" \
  --specpath "$PROJECT_DIR/build" \
  --additional-hooks-dir "$PROJECT_DIR/hooks" \
  --paths "$PROJECT_DIR/vendor" \
  --hidden-import transformers.models.siglip.configuration_siglip \
  --hidden-import transformers.models.siglip.image_processing_siglip \
  --hidden-import transformers.models.siglip.image_processing_pil_siglip \
  --hidden-import transformers.models.siglip.modeling_siglip \
  --hidden-import transformers.models.siglip.processing_siglip \
  --hidden-import transformers.models.siglip.tokenization_siglip \
  --hidden-import transformers.models.clip.configuration_clip \
  --hidden-import transformers.models.clip.image_processing_clip \
  --hidden-import transformers.models.clip.modeling_clip \
  --hidden-import transformers.models.clip.processing_clip \
  --hidden-import transformers.models.clip.tokenization_clip \
  --hidden-import cryptography.hazmat.primitives.ciphers.aead \
  --hidden-import keys \
  --hidden-import requests \
  --exclude-module cv2 \
  --exclude-module datasets \
  --exclude-module llvmlite \
  --exclude-module matplotlib \
  --exclude-module numba \
  --exclude-module pandas \
  --exclude-module pyarrow \
  --exclude-module pytest \
  --exclude-module scipy \
  --exclude-module timm \
  --exclude-module tkinter \
  --exclude-module torchvision \
  --add-data "$PROJECT_DIR/web:web" \
  --add-data "$PROJECT_DIR/data/beauty-tournament/axis-ranking-model-siglip2-base-patch16-256.npz:./data/beauty-tournament" \
  --add-data "$PROJECT_DIR/data/pamela/siglip2-base-patch16-256.npz:./data/pamela" \
  --add-data "$PROJECT_DIR/data/gnosis-media.json:./data" \
  --add-data "$PROJECT_DIR/data/nga-search.db:./data" \
  "$PROJECT_DIR/server.py"

PYTHONPATH="$PYINSTALLER_DIR" "$PYTHON_BIN" \
  "$PROJECT_DIR/scripts/package-europeana-key.py" \
  "$OUTPUT_DIR/gnosis-search-engine/credentials"
