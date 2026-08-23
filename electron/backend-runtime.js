const path = require('node:path');

const BUNDLED_CA_RELATIVE_PATH = path.join(
  'backend',
  'gnosis-search-engine',
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
    'backend',
    'gnosis-search-engine',
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
    SEARCH_MODEL_ALLOW_DOWNLOAD: '1',
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
  backendEnvironment,
  bundledCertificatePath,
  packagedBackendExecutable
};
