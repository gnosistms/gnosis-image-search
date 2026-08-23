const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pipeline } = require('node:stream/promises');
const { Transform } = require('node:stream');

const { BUNDLED_MODEL_MANIFEST, DEFAULT_ACTIVE_PROFILE, isInsideDirectory } = require('./model-config');

const MODEL_PACKAGE_MAGIC = Buffer.from('GNOSISMODEL1\n', 'ascii');
const MODEL_PACKAGE_HEADER_BYTES = 8;
const MAX_HEADER_BYTES = 1024 * 1024;

function modelTarget(userData, profileId = DEFAULT_ACTIVE_PROFILE) {
  return path.join(userData, 'models', profileId);
}

function readManifest(fileSystem, target) {
  try {
    return JSON.parse(fileSystem.readFileSync(path.join(target, BUNDLED_MODEL_MANIFEST), 'utf8'));
  } catch {
    return null;
  }
}

function validRelativePath(root, relativePath) {
  if (typeof relativePath !== 'string' || !relativePath || relativePath.includes('\\')) return null;
  const candidate = path.resolve(root, relativePath);
  return isInsideDirectory(root, candidate) ? candidate : null;
}

function manifestFilesExist(fileSystem, target, manifest) {
  if (!manifest || !Array.isArray(manifest.files) || manifest.files.length === 0) return false;
  return manifest.files.every(file => {
    const candidate = file && validRelativePath(target, file.path);
    if (!candidate || !Number.isSafeInteger(file.size) || file.size < 0) return false;
    try {
      return fileSystem.statSync(candidate).isFile() && fileSystem.statSync(candidate).size === file.size;
    } catch {
      return false;
    }
  });
}

function installedModel(userData, profileId = DEFAULT_ACTIVE_PROFILE, options = {}) {
  const fileSystem = options.fileSystem || fs;
  const target = modelTarget(userData, profileId);
  const manifest = readManifest(fileSystem, target);
  return {
    installed: Boolean(manifest?.profileId === profileId && manifestFilesExist(fileSystem, target, manifest)),
    manifest,
    target
  };
}

function readPackageHeader(packagePath) {
  const descriptor = fs.openSync(packagePath, 'r');
  try {
    const prefixLength = MODEL_PACKAGE_MAGIC.length + MODEL_PACKAGE_HEADER_BYTES;
    const prefix = Buffer.alloc(prefixLength);
    if (fs.readSync(descriptor, prefix, 0, prefix.length, 0) !== prefix.length
      || !prefix.subarray(0, MODEL_PACKAGE_MAGIC.length).equals(MODEL_PACKAGE_MAGIC)) {
      throw new Error('The image ranking model package is invalid.');
    }
    const headerLength = Number(prefix.readBigUInt64BE(MODEL_PACKAGE_MAGIC.length));
    if (!Number.isSafeInteger(headerLength) || headerLength < 2 || headerLength > MAX_HEADER_BYTES) {
      throw new Error('The image ranking model package header is invalid.');
    }
    const bytes = Buffer.alloc(headerLength);
    if (fs.readSync(descriptor, bytes, 0, headerLength, prefixLength) !== headerLength) {
      throw new Error('The image ranking model package is incomplete.');
    }
    return { header: JSON.parse(bytes.toString('utf8')), payloadOffset: prefixLength + headerLength };
  } finally {
    fs.closeSync(descriptor);
  }
}

async function extractEntry(packagePath, payloadOffset, entry, destination) {
  await fs.promises.mkdir(path.dirname(destination), { recursive: true });
  if (entry.size === 0) {
    await fs.promises.writeFile(destination, '', { flag: 'wx' });
    if (crypto.createHash('sha256').update('').digest('hex') !== entry.sha256) {
      throw new Error(`The model file ${entry.path} failed verification.`);
    }
    return;
  }
  const hash = crypto.createHash('sha256');
  const verifier = new Transform({
    transform(chunk, _encoding, callback) {
      hash.update(chunk);
      callback(null, chunk);
    }
  });
  const start = payloadOffset + entry.offset;
  const input = fs.createReadStream(packagePath, { start, end: start + entry.size - 1 });
  await pipeline(input, verifier, fs.createWriteStream(destination, { flags: 'wx' }));
  if (hash.digest('hex') !== entry.sha256) throw new Error(`The model file ${entry.path} failed verification.`);
}

async function installModelPackage(packagePath, userData, options = {}) {
  const profileId = options.profileId || DEFAULT_ACTIVE_PROFILE;
  const { header, payloadOffset } = readPackageHeader(packagePath);
  if (header?.schemaVersion !== 1 || header?.manifest?.profileId !== profileId || !Array.isArray(header.files)) {
    throw new Error('The image ranking model package is not compatible with this version of Gnosis Images.');
  }
  const packageSize = (await fs.promises.stat(packagePath)).size;
  const target = modelTarget(userData, profileId);
  const modelsRoot = path.dirname(target);
  const temporary = path.join(modelsRoot, `.${profileId}.installing-${process.pid}`);
  await fs.promises.mkdir(modelsRoot, { recursive: true });
  await fs.promises.rm(temporary, { recursive: true, force: true });
  await fs.promises.mkdir(temporary);
  try {
    const seen = new Set();
    for (const entry of header.files) {
      const destination = entry && validRelativePath(temporary, entry.path);
      if (!destination || seen.has(entry.path) || !Number.isSafeInteger(entry.size) || entry.size < 0
        || !Number.isSafeInteger(entry.offset) || entry.offset < 0 || !/^[a-f0-9]{64}$/.test(entry.sha256 || '')
        || payloadOffset + entry.offset + entry.size > packageSize) {
        throw new Error('The image ranking model package contains an invalid file entry.');
      }
      seen.add(entry.path);
      await extractEntry(packagePath, payloadOffset, entry, destination);
    }
    await fs.promises.writeFile(
      path.join(temporary, BUNDLED_MODEL_MANIFEST),
      `${JSON.stringify(header.manifest, null, 2)}\n`,
      { flag: 'wx' }
    );
    if (!manifestFilesExist(fs, temporary, header.manifest)) {
      throw new Error('The installed image ranking model is incomplete.');
    }
    await fs.promises.rm(target, { recursive: true, force: true });
    await fs.promises.rename(temporary, target);
  } catch (error) {
    await fs.promises.rm(temporary, { recursive: true, force: true });
    throw error;
  }
  return { manifest: header.manifest, target };
}

module.exports = {
  MODEL_PACKAGE_MAGIC,
  installModelPackage,
  installedModel,
  modelTarget,
  readPackageHeader
};
