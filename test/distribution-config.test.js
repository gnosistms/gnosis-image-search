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

test('full distribution includes the staged model in a separate output tree', () => {
  const config = distributionConfig('full', 'arm64');
  assert.equal(config.buildIdentifier, 'full');
  assert.deepEqual(config.packagerConfig.extraResource, ['build/backend', 'build/bundled-models']);
  const dmg = config.makers.find(maker => maker.name === '@electron-forge/maker-dmg');
  assert.match(dmg.config.name, /Gnosis-Images-Full-Installer-.*-arm64/);
  const zip = config.makers.find(maker => maker.name === '@electron-forge/maker-zip');
  assert.deepEqual(zip.platforms, ['win32']);
});

test('update distribution cannot contain the staged model', () => {
  const config = distributionConfig('update', 'x64');
  assert.equal(config.buildIdentifier, 'update');
  assert.deepEqual(config.packagerConfig.extraResource, ['build/backend']);
  assert.equal(config.packagerConfig.extraResource.includes('build/bundled-models'), false);
  const zip = config.makers.find(maker => maker.name === '@electron-forge/maker-zip');
  assert.deepEqual(zip.platforms, ['win32']);
});

test('packager excludes development data using absolute paths', () => {
  const config = distributionConfig('update', 'x64');
  const ignored = filePath => config.packagerConfig.ignore.some(pattern => pattern.test(filePath));
  assert.equal(ignored('/workspace/gnosis/data/pamela/PAMELA.zip'), true);
  assert.equal(ignored('C:\\workspace\\gnosis\\data\\pamela\\PAMELA.zip'), true);
  assert.equal(ignored('/workspace/gnosis/deploy/output.zip'), true);
  assert.equal(ignored('/workspace/gnosis/electron/main.js'), false);
});
