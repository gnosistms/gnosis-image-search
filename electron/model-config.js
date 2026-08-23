const fs = require('node:fs');
const path = require('node:path');

const MODEL_CONFIG_SCHEMA_VERSION = 1;
const DEFAULT_ACTIVE_PROFILE = 'pamela-siglip2-base-v1';
const BUNDLED_MODELS_DIRECTORY = 'bundled-models';
const BUNDLED_MODEL_MANIFEST = 'gnosis-model-manifest.json';

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

function readJson(fileSystem, filePath) {
  try {
    return JSON.parse(fileSystem.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function manifestFilesExist(fileSystem, root, manifest) {
  if (!manifest || !Array.isArray(manifest.files) || manifest.files.length === 0) return false;
  return manifest.files.every(file => {
    if (!file || typeof file.path !== 'string' || !Number.isSafeInteger(file.size)) return false;
    const candidate = path.resolve(root, file.path);
    if (!isInsideDirectory(root, candidate)) return false;
    try {
      return fileSystem.statSync(candidate).isFile() && fileSystem.statSync(candidate).size === file.size;
    } catch {
      return false;
    }
  });
}

function sameManifest(left, right) {
  return Boolean(left && right && JSON.stringify(left) === JSON.stringify(right));
}

function copyModelTree(fileSystem, source, destination) {
  const details = fileSystem.lstatSync(source);
  if (details.isSymbolicLink()) {
    fileSystem.symlinkSync(fileSystem.readlinkSync(source), destination);
    return;
  }
  if (details.isDirectory()) {
    fileSystem.mkdirSync(destination, { recursive: true });
    for (const entry of fileSystem.readdirSync(source)) {
      copyModelTree(fileSystem, path.join(source, entry), path.join(destination, entry));
    }
    return;
  }
  // APFS can clone the 1.4 GB weights without duplicating their physical disk
  // blocks. Node falls back to an ordinary copy when cloning is unavailable.
  fileSystem.copyFileSync(source, destination, fs.constants.COPYFILE_FICLONE);
}

function seedBundledModel(userData, resourcesPath, options = {}) {
  const fileSystem = options.fileSystem || fs;
  const profileId = options.profileId || DEFAULT_ACTIVE_PROFILE;
  const modelsRoot = path.join(userData, 'models');
  const source = path.join(resourcesPath, BUNDLED_MODELS_DIRECTORY, profileId);
  const sourceManifestPath = path.join(source, BUNDLED_MODEL_MANIFEST);
  const manifest = readJson(fileSystem, sourceManifestPath);
  const target = path.join(modelsRoot, profileId);
  const targetManifestPath = path.join(target, BUNDLED_MODEL_MANIFEST);

  if (!manifest) return { status: 'not-bundled', target };
  if (manifest.profileId !== profileId || !manifestFilesExist(fileSystem, source, manifest)) {
    throw new Error(`The bundled ${profileId} model is incomplete.`);
  }

  const installedManifest = readJson(fileSystem, targetManifestPath);
  if (sameManifest(installedManifest, manifest) && manifestFilesExist(fileSystem, target, manifest)) {
    return { status: 'already-installed', target };
  }

  // Adopt a model downloaded by an earlier app version when its expected files
  // are already complete. This avoids copying the same 1.4 GB payload again.
  if (manifestFilesExist(fileSystem, target, manifest)) {
    fileSystem.writeFileSync(targetManifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    return { status: 'adopted-existing', target };
  }

  fileSystem.mkdirSync(modelsRoot, { recursive: true });
  const temporary = path.join(modelsRoot, `.${profileId}.installing-${process.pid}`);
  fileSystem.rmSync(temporary, { recursive: true, force: true });
  try {
    copyModelTree(fileSystem, source, temporary);
    if (!manifestFilesExist(fileSystem, temporary, manifest)) {
      throw new Error(`The copied ${profileId} model failed verification.`);
    }
    fileSystem.rmSync(target, { recursive: true, force: true });
    fileSystem.renameSync(temporary, target);
  } catch (error) {
    fileSystem.rmSync(temporary, { recursive: true, force: true });
    throw error;
  }
  return { status: 'installed', target };
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
  BUNDLED_MODEL_MANIFEST,
  BUNDLED_MODELS_DIRECTORY,
  DEFAULT_ACTIVE_PROFILE,
  MANAGED_MODEL_PROFILES,
  MODEL_CONFIG_SCHEMA_VERSION,
  RETIRED_MANAGED_PROFILES,
  isInsideDirectory,
  reconcileModelConfiguration,
  seedBundledModel
};
