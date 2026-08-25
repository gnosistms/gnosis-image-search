const assert = require('node:assert/strict');
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
  const squirrel = config.makers.find(maker => maker.name === '@electron-forge/maker-squirrel');
  assert.deepEqual(squirrel.platforms, ['win32']);
  assert.match(squirrel.config.setupExe, /Gnosis-Images-Full-Installer-.*-arm64\.exe/);
});

test('update distribution is also model-free', () => {
  const config = distributionConfig('update', 'x64');
  assert.equal(config.buildIdentifier, 'update');
  assert.deepEqual(config.packagerConfig.extraResource, ['build/backend/b']);
  assert.equal(config.packagerConfig.extraResource.includes('build/bundled-models'), false);
  const zip = config.makers.find(maker => maker.name === '@electron-forge/maker-zip');
  assert.deepEqual(zip.platforms, ['win32']);
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
