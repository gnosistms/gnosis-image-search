const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  BUNDLED_MODEL_MANIFEST,
  BUNDLED_MODELS_DIRECTORY,
  DEFAULT_ACTIVE_PROFILE,
  RETIRED_MANAGED_PROFILES,
  isInsideDirectory,
  reconcileModelConfiguration,
  seedBundledModel
} = require('../electron/model-config');

function temporaryUserData() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-model-config-'));
}

test('creates an app-owned cache for the bundled profile', () => {
  const userData = temporaryUserData();
  const result = reconcileModelConfiguration(userData);
  assert.equal(result.value.activeProfile, DEFAULT_ACTIVE_PROFILE);
  assert.equal(
    result.value.profiles[DEFAULT_ACTIVE_PROFILE].cacheDirectory,
    path.join(userData, 'models', DEFAULT_ACTIVE_PROFILE)
  );
});

test('preserves custom profiles while refreshing managed profiles', () => {
  const userData = temporaryUserData();
  fs.writeFileSync(path.join(userData, 'model-config.json'), JSON.stringify({
    activeProfile: 'my-model',
    profiles: {
      [DEFAULT_ACTIVE_PROFILE]: { checkpoint: 'stale/checkpoint' },
      'my-model': { source: 'custom', modelKind: 'clip', checkpoint: 'my/clip' }
    }
  }));
  const result = reconcileModelConfiguration(userData);
  assert.equal(result.value.activeProfile, 'my-model');
  assert.equal(result.value.profiles['my-model'].checkpoint, 'my/clip');
  assert.equal(
    result.value.profiles[DEFAULT_ACTIVE_PROFILE].checkpoint,
    'google/siglip2-base-patch16-256'
  );
});

test('migrates the retired Large profile to Base and removes its app-owned cache', () => {
  const userData = temporaryUserData();
  const oldProfile = RETIRED_MANAGED_PROFILES[0];
  const retiredCache = path.join(userData, 'models', oldProfile.cacheSubdirectory);
  fs.mkdirSync(retiredCache, { recursive: true });
  fs.writeFileSync(path.join(retiredCache, 'model.safetensors'), 'old');
  fs.writeFileSync(path.join(userData, 'model-config.json'), JSON.stringify({
    activeProfile: oldProfile.id,
    profiles: {
      [oldProfile.id]: {
        source: 'bundled',
        checkpoint: 'google/siglip2-large-patch16-256',
        cacheDirectory: retiredCache
      }
    }
  }));

  const result = reconcileModelConfiguration(userData);
  assert.equal(result.value.activeProfile, DEFAULT_ACTIVE_PROFILE);
  assert.equal(
    result.value.profiles[DEFAULT_ACTIVE_PROFILE].checkpoint,
    'google/siglip2-base-patch16-256'
  );
  assert.equal(fs.existsSync(retiredCache), false);
});

test('switches away from a retired profile and deletes only its private cache', () => {
  const userData = temporaryUserData();
  const retiredCache = path.join(userData, 'models', 'old-model');
  fs.mkdirSync(retiredCache, { recursive: true });
  fs.writeFileSync(path.join(retiredCache, 'weights.bin'), 'old');
  fs.writeFileSync(path.join(userData, 'model-config.json'), JSON.stringify({
    activeProfile: 'old-model',
    profiles: { 'old-model': { source: 'bundled', cacheDirectory: retiredCache } }
  }));
  const result = reconcileModelConfiguration(userData, {
    retiredProfiles: [{ id: 'old-model', cacheSubdirectory: 'old-model' }]
  });
  assert.equal(result.value.activeProfile, DEFAULT_ACTIVE_PROFILE);
  assert.equal(fs.existsSync(retiredCache), false);
});

test('never deletes a retired profile path outside the app models directory', () => {
  const userData = temporaryUserData();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-shared-cache-'));
  fs.writeFileSync(path.join(outside, 'keep.bin'), 'keep');
  fs.writeFileSync(path.join(userData, 'model-config.json'), JSON.stringify({
    activeProfile: 'old-model',
    profiles: { 'old-model': { source: 'bundled', cacheDirectory: outside } }
  }));
  reconcileModelConfiguration(userData, { retiredProfiles: [{ id: 'old-model' }] });
  assert.equal(fs.existsSync(path.join(outside, 'keep.bin')), true);
  assert.equal(isInsideDirectory(path.join(userData, 'models'), outside), false);
});

function bundledModelFixture() {
  const resources = fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-resources-'));
  const source = path.join(resources, BUNDLED_MODELS_DIRECTORY, DEFAULT_ACTIVE_PROFILE);
  const relativeModel = path.join('snapshot', 'model.safetensors');
  fs.mkdirSync(path.dirname(path.join(source, relativeModel)), { recursive: true });
  fs.writeFileSync(path.join(source, relativeModel), 'weights');
  const manifest = {
    schemaVersion: 1,
    profileId: DEFAULT_ACTIVE_PROFILE,
    checkpoint: 'google/siglip2-base-patch16-256',
    revision: 'revision',
    files: [{ path: relativeModel, size: 7 }]
  };
  fs.writeFileSync(path.join(source, BUNDLED_MODEL_MANIFEST), JSON.stringify(manifest));
  return { resources, manifest, relativeModel };
}

test('seeds the model bundled with a full installer into Application Support', () => {
  const userData = temporaryUserData();
  const fixture = bundledModelFixture();
  const result = seedBundledModel(userData, fixture.resources);
  assert.equal(result.status, 'installed');
  assert.equal(fs.readFileSync(path.join(result.target, fixture.relativeModel), 'utf8'), 'weights');
  assert.deepEqual(
    JSON.parse(fs.readFileSync(path.join(result.target, BUNDLED_MODEL_MANIFEST), 'utf8')),
    fixture.manifest
  );
  assert.equal(seedBundledModel(userData, fixture.resources).status, 'already-installed');
});

test('model-free update packages preserve the installed model', () => {
  const userData = temporaryUserData();
  const modelFile = path.join(userData, 'models', DEFAULT_ACTIVE_PROFILE, 'keep.bin');
  fs.mkdirSync(path.dirname(modelFile), { recursive: true });
  fs.writeFileSync(modelFile, 'keep');
  const emptyResources = fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-update-resources-'));
  assert.equal(seedBundledModel(userData, emptyResources).status, 'not-bundled');
  assert.equal(fs.readFileSync(modelFile, 'utf8'), 'keep');
});
