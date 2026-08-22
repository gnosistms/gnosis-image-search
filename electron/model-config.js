const fs = require('node:fs');
const path = require('node:path');

const MODEL_CONFIG_SCHEMA_VERSION = 1;
const DEFAULT_ACTIVE_PROFILE = 'pamela-siglip2-base-v1';

const MANAGED_MODEL_PROFILES = {
  [DEFAULT_ACTIVE_PROFILE]: {
    source: 'bundled',
    modelKind: 'siglip',
    checkpoint: 'google/siglip2-base-patch16-256',
    cacheSubdirectory: DEFAULT_ACTIVE_PROFILE,
    axisModel: null,
    referenceEmbeddings: null
  }
};

// Future releases add replaced bundled profile ids here. On first launch after
// the update, the active profile is switched before its app-owned cache is
// removed. Never list a user-created profile here.
const RETIRED_MANAGED_PROFILES = [
  { id: 'pamela-siglip2-large-v1', cacheSubdirectory: 'pamela-siglip2-large-v1' }
];

function isInsideDirectory(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return Boolean(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function materializeProfile(profile, modelsRoot) {
  const value = { ...profile };
  if (value.source === 'bundled' && value.cacheSubdirectory) {
    value.cacheDirectory = path.join(modelsRoot, value.cacheSubdirectory);
  }
  delete value.cacheSubdirectory;
  return value;
}

function reconcileModelConfiguration(userData, options = {}) {
  const fileSystem = options.fileSystem || fs;
  const configPath = path.join(userData, 'model-config.json');
  const modelsRoot = path.join(userData, 'models');
  const managedProfiles = options.managedProfiles || MANAGED_MODEL_PROFILES;
  const retiredProfiles = options.retiredProfiles || RETIRED_MANAGED_PROFILES;
  const defaultActiveProfile = options.defaultActiveProfile || DEFAULT_ACTIVE_PROFILE;
  let saved = {};
  try {
    saved = JSON.parse(fileSystem.readFileSync(configPath, 'utf8'));
  } catch {
    saved = {};
  }

  const retiredIds = new Set(retiredProfiles.map(profile => profile.id));
  const profiles = {};
  for (const [id, profile] of Object.entries(saved.profiles || {})) {
    if (!Object.hasOwn(managedProfiles, id) && !retiredIds.has(id)) profiles[id] = profile;
  }
  for (const [id, profile] of Object.entries(managedProfiles)) {
    profiles[id] = materializeProfile(profile, modelsRoot);
  }

  let activeProfile = saved.activeProfile || defaultActiveProfile;
  if (retiredIds.has(activeProfile) || !profiles[activeProfile]) activeProfile = defaultActiveProfile;
  const value = {
    schemaVersion: MODEL_CONFIG_SCHEMA_VERSION,
    activeProfile,
    profiles
  };

  fileSystem.mkdirSync(modelsRoot, { recursive: true });
  fileSystem.writeFileSync(configPath, `${JSON.stringify(value, null, 2)}\n`);

  const deletedCaches = [];
  for (const retired of retiredProfiles) {
    const candidates = [
      retired.cacheSubdirectory && path.join(modelsRoot, retired.cacheSubdirectory),
      saved.profiles?.[retired.id]?.cacheDirectory
    ].filter(Boolean);
    for (const cachePath of new Set(candidates.map(candidate => path.resolve(candidate)))) {
      if (!isInsideDirectory(modelsRoot, cachePath)) continue;
      try {
        fileSystem.rmSync(cachePath, { recursive: true, force: true });
        deletedCaches.push(cachePath);
      } catch (error) {
        console.warn(`Could not remove retired model cache ${cachePath}: ${error.message}`);
      }
    }
  }
  return { configPath, modelsRoot, value, deletedCaches };
}

module.exports = {
  DEFAULT_ACTIVE_PROFILE,
  MANAGED_MODEL_PROFILES,
  MODEL_CONFIG_SCHEMA_VERSION,
  RETIRED_MANAGED_PROFILES,
  isInsideDirectory,
  reconcileModelConfiguration
};
