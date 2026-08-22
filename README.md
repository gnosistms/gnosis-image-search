# Gnosis Image Search

A desktop museum-image search application with local SigLIP inference and a
PAMELA-derived visual criterion ranker. It searches selected public collections
in parallel and orders results by image size × the learned criterion score.

## Why it is a desktop app

The Electron interface supervises a bundled Python/PyTorch search engine. Model
weights, comparison preferences, indexes, and the embedding cache remain in
user-owned folders outside the application bundle. Updating the application
therefore does not download an unchanged checkpoint again. Managed model
weights live in an app-owned cache, so an application update can safely replace
a model and remove its retired files without touching shared Hugging Face data.

On macOS the app creates its writable data under:

`~/Library/Application Support/Gnosis Image Search/`

The active model profile is defined by `model-config.json` in that directory.
See [MODELS.md](MODELS.md) before changing checkpoints: the learned PAMELA axis
and its reference embeddings must use the same vector space.

## Development

Requirements: Node.js 22 and Python 3.12. Backend package versions are pinned in
`requirements-backend.txt`.

```bash
nvm use
npm install
python -m pip install -r requirements-backend.txt
npm start
```

The development shell uses the neighboring Automatic Illustrator virtual
environment when present. The release build creates a standalone backend with
PyInstaller:

```bash
npm run package:mac
```

The packaged application is written under `out/`.

## Distribution

The static download site is in `docs/` and is deployed through GitHub Pages. It
queries the latest GitHub Release and selects the appropriate asset for macOS,
Windows, or Linux.

The application checks the latest public GitHub Release at startup. If a newer
version exists, it asks for permission before downloading and installing it.
macOS updates require a Developer ID-signed and notarized build. See
[RELEASING.md](RELEASING.md) for the provisioning and release procedure.

## Tests

```bash
python -m pytest -q
npm run test:model-config
node --check electron/main.js
node --check web/app.js
```
