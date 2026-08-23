# Gnosis Image Search

A desktop museum-image search application with local SigLIP inference and a
PAMELA-derived visual criterion ranker. It searches selected public collections
in parallel and orders results by image size × the learned criterion score.

## Why it is a desktop app

The Electron interface supervises a bundled Python/PyTorch search engine. The
full installer includes the default SigLIP checkpoint and seeds it into a
user-owned cache before starting the backend, so first use never begins with a
silent multi-gigabyte download. Comparison preferences, indexes, and embeddings
are stored alongside that cache outside the replaceable application data.
Routine releases provide a separately named, model-free update installer and
therefore do not download an unchanged checkpoint again. Managed model profiles
can still deliberately replace a checkpoint and remove its retired app-owned
files without touching shared Hugging Face data.

On macOS the app creates its writable data under:

`~/Library/Application Support/Gnosis Image Search/`

On Windows it uses:

`%APPDATA%\Gnosis Image Search\`

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

Europeana credentials can be supplied through `EUROPEANA_API_KEY`, through a
JSON file selected by `SEARCH_KEYS_FILE`, or through `keys.json` in this project
or the neighboring Automatic Illustrator project. Release builds encrypt the
Europeana key with AES-GCM and place the encrypted payload and runtime
decryption key in separate files inside the packaged backend. This keeps the
key out of source control, request URLs, logs, and casual inspection; it is
application obfuscation rather than a secure secret store.

Release builds are written under `out/full/` and `out/update/`.

## Distribution

The static download site is in `docs/` and is deployed through GitHub Pages. It
queries the latest GitHub Release and selects only the `Full-Installer` asset
for macOS or Windows.

The application checks the latest public GitHub Release at startup. If a newer
version exists, it asks for permission before downloading and installing only
the smaller `Update` asset.
macOS updates require a Developer ID-signed and notarized build. New Windows
x64 installations use the full ZIP (extract it, then run `Gnosis Images.exe`).
Windows updates download a smaller model-free ZIP and reveal it in File Explorer
for manual replacement; Windows builds remain unsigned until a code-signing
certificate is configured. See
[RELEASING.md](RELEASING.md) for the provisioning and release procedure.

## Tests

```bash
python -m pytest -q
npm run test:model-config
node --check electron/main.js
node --check web/app.js
```
