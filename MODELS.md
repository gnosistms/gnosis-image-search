# Model profiles

The application stores model weights outside the application bundle in its own
application-support directory. Replacing or updating `Gnosis Image Search.app`
therefore does not redownload an unchanged checkpoint, while retired weights can
be removed without touching another application's Hugging Face cache.

At first launch the app creates:

`~/Library/Application Support/Gnosis Image Search/model-config.json`

The default checkpoint is cached below:

`~/Library/Application Support/Gnosis Image Search/models/`

Its managed profile uses `google/siglip2-base-patch16-256`. User-created
profiles are preserved when the app refreshes its managed profiles. A future
app release can add a replacement, make it active, and retire the old managed
profile. On first launch, configuration is switched first and only the retired
directory inside this app-owned `models` directory is deleted. External and
shared cache paths are never recursively removed.

The previous `pamela-siglip2-large-v1` profile is retired. Upgrading switches
the managed default to `pamela-siglip2-base-v1` and removes only the old
app-owned Large checkpoint cache after the new configuration is written.

Profiles accept `modelKind: "siglip"` or `modelKind: "clip"`. A ranking profile
must also supply a compatible learned axis (`axisModel`) and compatible
reference embedding archive (`referenceEmbeddings`); vectors trained in one
embedding space cannot be applied to another model.

## Replacement-model handoff

The model-evaluation work must provide all of the following before a managed
profile is changed:

- stable profile id and exact Hugging Face checkpoint
- `modelKind` (`clip` or `siglip`) and embedding dimension
- compatible PAMELA/reference embeddings and learned ranking-axis file
- ranking and semantic-relevance validation metrics against the current model
- approximate clean-download size and supported hardware

The release implementing the replacement then adds the new profile and lists
the previous managed profile in `RETIRED_MANAGED_PROFILES`. The normal prompted
application update performs the migration; model files remain external to the
application package.
