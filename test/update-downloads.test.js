const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { cleanupStaleUpdateDownloads, updateDownloadDirectory } = require('../electron/update-downloads');

test('startup cleanup removes completed and partial update downloads', async t => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-update-cleanup-'));
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }));
  const updateDirectory = updateDownloadDirectory(tempRoot);
  const nestedDirectory = path.join(updateDirectory, 'old');
  fs.mkdirSync(nestedDirectory, { recursive: true });
  fs.writeFileSync(path.join(updateDirectory, 'Gnosis-Images-Installer-0.1.13-x64.exe'), 'installer');
  fs.writeFileSync(path.join(nestedDirectory, 'installer.download'), 'partial');
  fs.writeFileSync(path.join(tempRoot, 'unrelated.txt'), 'keep');

  await cleanupStaleUpdateDownloads(tempRoot);

  assert.equal(fs.existsSync(updateDirectory), false);
  assert.equal(fs.readFileSync(path.join(tempRoot, 'unrelated.txt'), 'utf8'), 'keep');
});

test('startup cleanup succeeds when there is no update directory', async t => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'gnosis-update-cleanup-empty-'));
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }));

  await cleanupStaleUpdateDownloads(tempRoot);
  assert.equal(fs.existsSync(updateDownloadDirectory(tempRoot)), false);
});
