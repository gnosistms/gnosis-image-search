const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');

test('exact phrase controls restart an active query immediately', () => {
  const handlerStart = script.indexOf('function exactPhrasesChanged()');
  const nextFunction = script.indexOf('\nfunction ', handlerStart + 1);
  const handler = script.slice(handlerStart, nextFunction);

  assert.match(handler, /setExactPhrases\(!exactPhrasesRequested\)/);
  assert.match(handler, /if \(activeSearchQuery\) runSearch\(activeSearchQuery\)/);
  assert.doesNotMatch(handler, /if \(currentSession\)/);
  assert.match(
    script,
    /exactPhrases\.addEventListener\('click', exactPhrasesChanged\)/,
  );
  assert.match(
    script,
    /heroExactPhrases\.addEventListener\('click', exactPhrasesChanged\)/,
  );
});

test('replacement search records the selected exact policy before starting', () => {
  const searchStart = script.indexOf('async function runSearch(query)');
  const searchEnd = script.indexOf('\nfunction ', searchStart + 1);
  const search = script.slice(searchStart, searchEnd);

  const recordsPolicy = search.indexOf('activeSearchExact = exactPhrasesRequested');
  const startsRequest = search.indexOf('/api/search/start?');
  assert.ok(recordsPolicy >= 0);
  assert.ok(startsRequest > recordsPolicy);
});

test('a deferred exact change starts when a provider is selected again', () => {
  const handlerStart = script.indexOf('function providerSelectionChanged()');
  const nextFunction = script.indexOf('\nasync function runSearch', handlerStart);
  const handler = script.slice(handlerStart, nextFunction);

  const deferredRestart = handler.indexOf(
    'exactPhrasesRequested !== activeSearchExact',
  );
  const noSessionReturn = handler.indexOf('if (!currentSession) return');
  assert.ok(deferredRestart >= 0);
  assert.ok(noSessionReturn > deferredRestart);
  assert.match(
    handler,
    /if \(selected\.length && activeSearchQuery[\s\S]*?runSearch\(activeSearchQuery\);[\s\S]*?return;/,
  );
});
