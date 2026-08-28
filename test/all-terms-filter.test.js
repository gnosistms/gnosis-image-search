const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const filter = require(path.join(__dirname, '..', 'web', 'all-terms-filter.js'));

test('ignores common words and requires every meaningful term', () => {
  assert.deepEqual(
    filter.queryTerms('the rose of a cross and the sun'),
    ['rose', 'cross', 'sun'],
  );
  assert.equal(filter.matches(
    {page_text_terms: ['cross', 'rose', 'sun']},
    'the rose of a cross and the sun',
  ), true);
  assert.equal(filter.matches(
    {page_text_terms: ['cross', 'rose']},
    'the rose of a cross and the sun',
  ), false);
});

test('matches terms anywhere and does not require phrase order', () => {
  const result = {page_text_terms: ['cross', 'garden', 'rose']};
  assert.equal(filter.matches(result, 'rose cross'), true);
  assert.equal(filter.matches(result, 'cross rose'), true);
});

test('matches case and accents consistently with provider page terms', () => {
  assert.equal(filter.matches(
    {page_text_terms: ['cafe', 'rose']},
    'CAFÉ rose',
  ), true);
});
