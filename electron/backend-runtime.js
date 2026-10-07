const path = require('node:path');

// The backend exits with this code when the installed model files are gone.
// Keep it in sync with MODEL_MISSING_EXIT_CODE in semantic_embeddings.py.
const MODEL_MISSING_EXIT_CODE = 78;

const BUNDLED_CA_RELATIVE_PATH = path.join(
  'b',
  '_internal',
  'certifi',
  'cacert.pem'
);

function bundledCertificatePath(resourcesPath) {
  return path.join(resourcesPath, BUNDLED_CA_RELATIVE_PATH);
}

function packagedBackendExecutable(resourcesPath, platform = process.platform) {
  return path.join(
    resourcesPath,
    'b',
    platform === 'win32' ? 'gnosis-search-engine.exe' : 'gnosis-search-engine'
  );
}

function backendEnvironment(options) {
  const {
    baseEnvironment = process.env,
    isPackaged = false,
    resourcesPath = '',
    dataDirectory,
    activeModel = {}
  } = options;
  const environment = {
    ...baseEnvironment,
    SEARCH_DATA_DIR: dataDirectory,
    SEARCH_MODEL_KIND: activeModel.modelKind || 'siglip',
    SEARCH_MODEL_NAME: activeModel.checkpoint || 'google/siglip2-base-patch16-256',
    ...(activeModel.modelSource ? { SEARCH_MODEL_SOURCE: activeModel.modelSource } : {}),
    // The app installs the verified model package before starting the
    // backend, so the backend must never fetch weights from Hugging Face.
    SEARCH_MODEL_ALLOW_DOWNLOAD: '0',
    ...(activeModel.cacheDirectory ? { SEARCH_MODEL_CACHE_DIR: activeModel.cacheDirectory } : {}),
    ...(activeModel.axisModel ? { SEARCH_AXIS_MODEL: activeModel.axisModel } : {}),
    ...(activeModel.referenceEmbeddings ? { SEARCH_PAMELA_EMBEDDINGS: activeModel.referenceEmbeddings } : {}),
    PYTHONUNBUFFERED: '1'
  };
  if (isPackaged) {
    const certificatePath = bundledCertificatePath(resourcesPath);
    // A frozen Python runtime cannot reliably discover the macOS trust store
    // on every machine. Use the CA bundle PyInstaller already ships with
    // Certifi for both urllib/ssl and Requests instead of disabling TLS checks.
    environment.SSL_CERT_FILE = certificatePath;
    environment.REQUESTS_CA_BUNDLE = certificatePath;
  }
  return environment;
}

module.exports = {
  BUNDLED_CA_RELATIVE_PATH,
  MODEL_MISSING_EXIT_CODE,
  backendEnvironment,
  bundledCertificatePath,
  packagedBackendExecutable
};
