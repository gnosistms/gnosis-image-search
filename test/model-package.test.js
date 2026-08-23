const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { DEFAULT_ACTIVE_PROFILE } = require('../electron/model-config');
const {
  MODEL_PACKAGE_MAGIC,
  installModelPackage,
  installedModel,
  readPackageHeader
} = require('../electron/model-package');

function temporaryDirectory(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function modelPackage(options = {}) {
  const directory = temporaryDirectory('gnosis-model-package-');
  const packagePath = path.join(directory, 'model.gnosis-model');
  const contents = Buffer.from(options.contents || 'model weights');
  const relativePath = 'snapshot/model.safetensors';
  const manifest = {
    schemaVersion: 1,
    profileId: DEFAULT_ACTIVE_PROFILE,
    checkpoint: 'example/model',
    revision: 'revision',
    files: [{ path: relativePath, size: contents.length }]
  };
  const header = Buffer.from(JSON.stringify({
    schemaVersion: 1,
    manifest,
    files: [{
      path: options.path || relativePath,
      size: contents.length,
      sha256: options.sha256 || crypto.createHash('sha256').update(contents).digest('hex'),
      offset: 0
    }]
  }));
  const length = Buffer.alloc(8);
  length.writeBigUInt64BE(BigInt(header.length));
  fs.writeFileSync(packagePath, Buffer.concat([MODEL_PACKAGE_MAGIC, length, header, contents]));
  return { contents, packagePath, relativePath };
}

test('installs and detects a verified model package', async () => {
  const userData = temporaryDirectory('gnosis-model-user-');
  const fixture = modelPackage();
  assert.equal(installedModel(userData).installed, false);
  const result = await installModelPackage(fixture.packagePath, userData);
  assert.deepEqual(fs.readFileSync(path.join(result.target, fixture.relativePath)), fixture.contents);
  assert.equal(installedModel(userData).installed, true);
  assert.equal(readPackageHeader(fixture.packagePath).header.manifest.profileId, DEFAULT_ACTIVE_PROFILE);
});

test('rejects a model package whose payload fails its checksum', async () => {
  const userData = temporaryDirectory('gnosis-model-user-');
  const fixture = modelPackage({ sha256: '0'.repeat(64) });
  await assert.rejects(installModelPackage(fixture.packagePath, userData), /failed verification/);
  assert.equal(installedModel(userData).installed, false);
});

test('rejects model package path traversal', async () => {
  const userData = temporaryDirectory('gnosis-model-user-');
  const fixture = modelPackage({ path: '../outside.bin' });
  await assert.rejects(installModelPackage(fixture.packagePath, userData), /invalid file entry/);
  assert.equal(fs.existsSync(path.join(userData, 'outside.bin')), false);
});
