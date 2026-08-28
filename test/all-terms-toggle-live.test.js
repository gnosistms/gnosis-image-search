const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const markup = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');

test('all terms controls stay synchronized and filter cached results locally', () => {
  const handlerStart = script.indexOf('function allTermsChanged()');
  const nextFunction = script.indexOf('\nfunction ', handlerStart + 1);
  const handler = script.slice(handlerStart, nextFunction);

  assert.match(handler, /setAllTerms\(!allTermsRequested\)/);
  assert.match(handler, /renderGallery\(resultsForSelectedSources\(latestSnapshotResults\)\)/);
  assert.doesNotMatch(handler, /runSearch/);
  assert.match(script, /allTerms\.addEventListener\('click', allTermsChanged\)/);
  assert.match(script, /heroAllTerms\.addEventListener\('click', allTermsChanged\)/);
});

test('search request does not send the local all-terms policy', () => {
  const searchStart = script.indexOf('async function runSearch(query)');
  const searchEnd = script.indexOf('\nfunction ', searchStart + 1);
  const search = script.slice(searchStart, searchEnd);

  assert.doesNotMatch(search, /activeSearchAllTerms/);
  assert.doesNotMatch(search, /&all=/);
});

test('both search surfaces explain all terms, exact phrases, and collections', () => {
  assert.equal((markup.match(/data-tooltip=/g) || []).length >= 6, true);
  assert.equal(
    (markup.match(/Only show results where all the search terms appear somewhere on the page\./g) || []).length,
    2,
  );
  assert.equal(
    (markup.match(/This requests the search providers to return only results that exactly match the entire search term\./g) || []).length,
    2,
  );
  assert.equal(
    (markup.match(/Select which image websites to search\. This can be adjusted in real time without restarting the search\./g) || []).length,
    2,
  );
});
