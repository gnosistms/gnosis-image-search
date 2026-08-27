const assert = require('node:assert/strict');
const test = require('node:test');

const { compatibleModelAsset, compatibleUpdateAsset } = require('../electron/update-assets');

const asset = name => ({ name, browser_download_url: `https://example.test/${name}` });

test('macOS updater selects the model-free update DMG', () => {
  const release = { assets: [
    asset('Gnosis-Images-Full-Installer-1.2.0-arm64.dmg'),
    asset('Gnosis-Images-Update-1.2.0-x64.dmg'),
    asset('Gnosis-Images-Update-1.2.0-arm64.dmg'),
  ] };
  assert.equal(
    compatibleUpdateAsset(release, 'darwin', 'arm64').name,
    'Gnosis-Images-Update-1.2.0-arm64.dmg'
  );
  assert.equal(
    compatibleUpdateAsset(release, 'darwin', 'x64').name,
    'Gnosis-Images-Update-1.2.0-x64.dmg'
  );
});

test('macOS updater never falls back to the other CPU architecture', () => {
  const release = { assets: [asset('Gnosis-Images-Update-1.2.0-arm64.dmg')] };
  assert.equal(compatibleUpdateAsset(release, 'darwin', 'x64'), null);
});

test('updater never falls back to a full installer', () => {
  const release = { assets: [asset('Gnosis-Images-Full-Installer-1.2.0-arm64.dmg')] };
  assert.equal(compatibleUpdateAsset(release, 'darwin', 'arm64'), null);
});

test('Windows updater reuses the model-free NSIS installer', () => {
  const release = { assets: [
    asset('Gnosis-Images-Installer-1.2.0-x64.exe'),
    asset('Gnosis-Images-Update-1.2.0-x64.zip'),
  ] };
  assert.equal(
    compatibleUpdateAsset(release, 'win32', 'x64').name,
    'Gnosis-Images-Installer-1.2.0-x64.exe'
  );
});

test('model downloader selects only the platform-neutral model package', () => {
  const release = { assets: [
    asset('Gnosis-Images-Update-1.2.0-arm64.dmg'),
    asset('Gnosis-Images-Image-Ranking-Model-1.2.0.gnosis-model'),
  ] };
  assert.equal(
    compatibleModelAsset(release).name,
    'Gnosis-Images-Image-Ranking-Model-1.2.0.gnosis-model'
  );
  assert.equal(compatibleModelAsset({ assets: [asset('model.zip')] }), null);
});
