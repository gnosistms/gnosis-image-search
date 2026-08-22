# Model profiles

The application stores model weights outside the application bundle. On macOS,
the default Hugging Face cache is under `~/.cache/huggingface`, so replacing or
updating `Gnosis Image Search.app` does not redownload the active checkpoint.

At first launch the app creates:

`~/Library/Application Support/Gnosis Image Search/model-config.json`

Its default profile uses `google/siglip2-large-patch16-256`. To test another
checkpoint, add a profile and change `activeProfile`. A ranking profile must
also supply a compatible learned axis (`axisModel`) and compatible reference
embedding archive (`referenceEmbeddings`); vectors trained in one embedding
space cannot be applied to another model.

Set `cacheDirectory` to place a profile's downloaded Hugging Face files in a
dedicated folder. Leaving it `null` uses the standard Hugging Face cache.
