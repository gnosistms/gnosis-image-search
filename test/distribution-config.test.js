const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const configPath = require.resolve('../forge.config');

function distributionConfig(distribution, arch) {
  const previousDistribution = process.env.GNOSIS_DISTRIBUTION;
  const previousArch = process.env.GNOSIS_TARGET_ARCH;
  process.env.GNOSIS_DISTRIBUTION = distribution;
  process.env.GNOSIS_TARGET_ARCH = arch;
  delete require.cache[configPath];
  const config = require(configPath);
  if (previousDistribution === undefined) delete process.env.GNOSIS_DISTRIBUTION;
  else process.env.GNOSIS_DISTRIBUTION = previousDistribution;
  if (previousArch === undefined) delete process.env.GNOSIS_TARGET_ARCH;
  else process.env.GNOSIS_TARGET_ARCH = previousArch;
  delete require.cache[configPath];
  return config;
}

test('full distribution is model-free and uses a separate output tree', () => {
  const config = distributionConfig('full', 'arm64');
  assert.equal(config.buildIdentifier, 'full');
  assert.deepEqual(config.packagerConfig.extraResource, ['build/backend/b']);
  assert.equal(config.packagerConfig.extraResource.includes('build/bundled-models'), false);
  const dmg = config.makers.find(maker => maker.name === '@electron-forge/maker-dmg');
  assert.match(dmg.config.name, /Gnosis-Images-Full-Installer-.*-arm64/);
  const zip = config.makers.find(maker => maker.name === '@electron-forge/maker-zip');
  assert.deepEqual(zip.platforms, ['darwin']);
  assert.equal(config.makers.some(maker => maker.name === '@electron-forge/maker-squirrel'), false);
});

test('update distribution is also model-free', () => {
  const config = distributionConfig('update', 'x64');
  assert.equal(config.buildIdentifier, 'update');
  assert.deepEqual(config.packagerConfig.extraResource, ['build/backend/b']);
  assert.equal(config.packagerConfig.extraResource.includes('build/bundled-models'), false);
  const zip = config.makers.find(maker => maker.name === '@electron-forge/maker-zip');
  assert.deepEqual(zip.platforms, ['darwin']);
  assert.equal(config.makers.some(maker => maker.name === '@electron-forge/maker-squirrel'), false);
});

test('packager excludes development data using absolute paths', () => {
  const config = distributionConfig('update', 'x64');
  const ignored = filePath => config.packagerConfig.ignore.some(pattern => pattern.test(filePath));
  assert.equal(ignored('/workspace/gnosis/data/pamela/PAMELA.zip'), true);
  assert.equal(ignored('C:\\workspace\\gnosis\\data\\pamela\\PAMELA.zip'), true);
  assert.equal(ignored('/workspace/gnosis/deploy/output.zip'), true);
  assert.equal(ignored('/workspace/gnosis/out/full/Gnosis Images.app'), true);
  assert.equal(ignored('C:\\workspace\\gnosis\\out\\full\\Gnosis Images.exe'), true);
  assert.equal(ignored('/workspace/gnosis/electron/main.js'), false);
});

test('Windows NSIS installer uses a guided finish page and one shared artifact', () => {
  const builderConfig = fs.readFileSync(path.join(__dirname, '..', 'electron-builder.yml'), 'utf8');
  const finishPage = fs.readFileSync(path.join(__dirname, '..', 'installer', 'nsis-finish.nsh'), 'utf8');
  const installerScript = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'make-windows-installer.ps1'), 'utf8');
  const releaseWorkflow = fs.readFileSync(path.join(__dirname, '..', '.github', 'workflows', 'release.yml'), 'utf8');
  assert.match(builderConfig, /oneClick: false/);
  assert.match(builderConfig, /artifactName: Gnosis-Images-Installer-/);
  assert.match(builderConfig, /createDesktopShortcut: false/);
  assert.match(installerScript, /--publish never/);
  assert.match(releaseWorkflow, /--publish never/);
  assert.match(finishPage, /Gnosis Images has been installed/);
  assert.match(finishPage, /Create a desktop shortcut/);
  assert.match(finishPage, /Run Gnosis Images/);
});
