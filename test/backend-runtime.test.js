const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  MODEL_MISSING_EXIT_CODE,
  backendEnvironment,
  bundledCertificatePath,
  packagedBackendExecutable
} = require('../electron/backend-runtime');

test('packaged backend uses the CA bundle shipped with Certifi', () => {
  const resourcesPath = path.join(path.sep, 'Applications', 'Gnosis Images.app', 'Contents', 'Resources');
  const certificatePath = bundledCertificatePath(resourcesPath);
  const environment = backendEnvironment({
    baseEnvironment: {
      KEEP_ME: 'yes',
      SSL_CERT_FILE: '/incorrect/system/path.pem',
      REQUESTS_CA_BUNDLE: '/incorrect/requests/path.pem'
    },
    isPackaged: true,
    resourcesPath,
    dataDirectory: '/tmp/gnosis-data'
  });

  assert.equal(
    certificatePath,
    path.join(
      resourcesPath,
      'b',
      '_internal',
      'certifi',
      'cacert.pem'
    )
  );
  assert.equal(environment.SSL_CERT_FILE, certificatePath);
  assert.equal(environment.REQUESTS_CA_BUNDLE, certificatePath);
  assert.equal(environment.KEEP_ME, 'yes');
});

test('development backend leaves existing certificate settings unchanged', () => {
  const environment = backendEnvironment({
    baseEnvironment: {
      SSL_CERT_FILE: '/developer/certificate.pem',
      REQUESTS_CA_BUNDLE: '/developer/requests.pem'
    },
    isPackaged: false,
    dataDirectory: '/tmp/gnosis-data',
    activeModel: {
      modelKind: 'clip',
      checkpoint: 'example/model',
      modelSource: '/tmp/model-snapshot',
      cacheDirectory: '/tmp/model-cache'
    }
  });

  assert.equal(environment.SSL_CERT_FILE, '/developer/certificate.pem');
  assert.equal(environment.REQUESTS_CA_BUNDLE, '/developer/requests.pem');
  assert.equal(environment.SEARCH_MODEL_KIND, 'clip');
  assert.equal(environment.SEARCH_MODEL_NAME, 'example/model');
  assert.equal(environment.SEARCH_MODEL_SOURCE, '/tmp/model-snapshot');
  assert.equal(environment.SEARCH_MODEL_CACHE_DIR, '/tmp/model-cache');
});

test('backend never downloads model weights on its own', () => {
  const environment = backendEnvironment({
    baseEnvironment: { SEARCH_MODEL_ALLOW_DOWNLOAD: '1' },
    dataDirectory: '/tmp/gnosis-data',
    activeModel: { modelSource: '/tmp/model-snapshot' }
  });
  assert.equal(environment.SEARCH_MODEL_ALLOW_DOWNLOAD, '0');
});

test('backend and app agree on the missing-model exit code', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'semantic_embeddings.py'), 'utf8');
  assert.match(source, new RegExp(`^MODEL_MISSING_EXIT_CODE = ${MODEL_MISSING_EXIT_CODE}$`, 'm'));
});

test('packaged backend selects the native executable name', () => {
  const resourcesPath = path.join(path.sep, 'app', 'resources');
  assert.equal(
    packagedBackendExecutable(resourcesPath, 'darwin'),
    path.join(resourcesPath, 'b', 'gnosis-search-engine')
  );
  assert.equal(
    packagedBackendExecutable(resourcesPath, 'win32'),
    path.join(resourcesPath, 'b', 'gnosis-search-engine.exe')
  );
});
