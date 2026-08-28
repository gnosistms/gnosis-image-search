const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');

test('provider changes hide deselected results before waiting for the server', () => {
  const handlerStart = script.indexOf('function providerSelectionChanged()');
  const nextFunction = script.indexOf('\nasync function runSearch', handlerStart);
  const handler = script.slice(handlerStart, nextFunction);

  const localRender = handler.indexOf(
    'renderGallery(resultsForSelectedSources(latestSnapshotResults, selected))'
  );
  const serverQueue = handler.indexOf('sourceUpdateQueue =');
  assert.ok(localRender >= 0, 'selection handler should filter the cached snapshot');
  assert.ok(serverQueue > localRender, 'local filtering should happen before server work');
});

test('incoming snapshots cannot redisplay a provider that is now unchecked', () => {
  assert.match(
    script,
    /latestSnapshotResults = snapshot\.results;[\s\S]*?renderGallery\(resultsForSelectedSources\(latestSnapshotResults\)\)/,
  );
});

test('a completed empty provider clears the searching spinner', () => {
  assert.match(
    script,
    /const active = policies\.filter\(policy => policy\.continue\)\.length;[\s\S]*?setGallerySearching\(Boolean\(active && !currentResults\.length\)\)/,
  );
  assert.match(
    script,
    /else if \(!currentResults\.length\) \{\s*gallery\.innerHTML = '<p class="notice">No matching images found\.<\/p>';/,
  );
});
