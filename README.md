# Gnosis Image Search

A desktop museum-image search application with local SigLIP inference and a
PAMELA-derived visual criterion ranker. It searches selected public collections
in parallel and orders results by image size × the learned criterion score.

## Why it is a desktop app

The Electron interface supervises a bundled Python/PyTorch search engine. On
first launch, the app downloads and verifies the separately published SigLIP
model package into a user-owned cache before starting the backend. Comparison
preferences, indexes, and embeddings are stored alongside that cache outside
the replaceable application data. Routine application updates are therefore
model-free and do not download an unchanged checkpoint again. Managed model
profiles can still deliberately replace a checkpoint and remove its retired
app-owned files without touching shared Hugging Face data.

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

### Exact phrase searches

Use the **Exact phrases** control to treat the entire unquoted query as one
phrase, or put double quotation marks around each required phrase. For example,
`"guardian of the threshold"` requires those words together and in that order;
`"text one" "text two"` requires both phrases but allows them in either order
and in different metadata fields. Matches ignore capitalization, punctuation,
and whitespace. Provider results are normalized and verified locally before
they are shown. Unquoted searches with the control off retain the established
broad-search behavior.

Wrap a query in double quotation marks to require all its words, consecutively
and in their original order, in Cleveland Museum of Art metadata—for example,
`"tea service"`. Capitalization, punctuation, and whitespace are ignored, but
word boundaries are preserved and intervening words do not match. Cleveland's
API is still used to generate candidates; the adapter paginates the candidates
and verifies the normalized phrase locally because the upstream API does not
preserve Azure quoted-query semantics. Exact searches also request Cleveland's
`smart_parts` view so multipart works are represented by their cover records.
Unquoted searches retain the broader provider search behavior.

### Provider match evidence and document filtering

The image detail panel keeps the collection's narrative description separate
from a bounded **Why this matched** excerpt. The excerpt is selected from the
same provider metadata used by the adapter—including controlled subjects,
object histories, alternate titles, captions, and notes—so useful matching text
is not discarded during result normalization. If a provider returns a hit but
does not expose any matching metadata, the panel says so explicitly.

Provider-native metadata also removes mechanically identifiable non-image
results before ranking: PDF deliveries, full-text multipage books, catalog-card
scans, accession-register media, and inherited unillustrated document pages.
For multi-view picture records, designated primary/front views are preferred
over reverse scans while retaining the matched asset as provenance.

Release builds are written under `out/full/` and `out/update/`. Windows uses a
single NSIS installer for both new installations and application updates.

## Distribution

The static download site is in `docs/` and is deployed through GitHub Pages. It
queries the latest GitHub Release and selects the platform's installer asset.

The application checks the latest public GitHub Release at startup. If a newer
version exists, it asks for permission before downloading the compatible
installer. The model and embedding cache remain separate and are preserved.
macOS updates require a Developer ID-signed and notarized build. New Windows
x64 installations use a guided NSIS `.exe` installer with visible progress, an
optional desktop shortcut, and a completion page. The same installer is used
for updates; Windows builds remain unsigned until a code-signing certificate is
configured. See
[RELEASING.md](RELEASING.md) for the provisioning and release procedure.

## Tests

```bash
python -m pytest -q
npm run test:model-config
node --check electron/main.js
node --check web/app.js
```
