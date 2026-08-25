const fs = require('node:fs');
const path = require('node:path');

const UPDATE_DOWNLOAD_DIRECTORY_NAME = 'gnosis-images-updates';

function updateDownloadDirectory(tempRoot) {
  return path.join(tempRoot, UPDATE_DOWNLOAD_DIRECTORY_NAME);
}

async function cleanupStaleUpdateDownloads(tempRoot, fileSystem = fs) {
  const directory = updateDownloadDirectory(tempRoot);
  await fileSystem.promises.rm(directory, {
    recursive: true,
    force: true,
    maxRetries: 3,
    retryDelay: 100,
  });
  return directory;
}

module.exports = { cleanupStaleUpdateDownloads, updateDownloadDirectory };
