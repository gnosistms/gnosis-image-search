const assert = require('node:assert/strict');
const test = require('node:test');

const { segments } = require('../web/search-term-highlight');

test('highlights every whole search term without changing displayed text', () => {
  const text = 'A red Rose surrounds the CROSS; another rose appears below.';
  const result = segments(text, 'rose cross');

  assert.equal(result.map(part => part.text).join(''), text);
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['Rose', 'CROSS', 'rose'],
  );
});

test('matches search terms accent-insensitively but preserves original spelling', () => {
  const result = segments('José painted the figure.', 'Jose');
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['José'],
  );
});

test('highlights an inflected match only when the provider opts into stemming', () => {
  const text = 'His arms crossed before anyone rose.';
  const exactResult = segments(text, 'rose cross');
  const providerResult = segments(text, 'rose cross', 'english_stem');

  assert.deepEqual(
    exactResult.filter(part => part.highlighted).map(part => part.text),
    ['rose'],
  );
  assert.equal(providerResult.map(part => part.text).join(''), text);
  assert.deepEqual(
    providerResult.filter(part => part.highlighted).map(part => part.text),
    ['cross', 'rose'],
  );
});

test('provider stemming does not turn arbitrary prefixes into evidence', () => {
  const result = segments('A rosette beside a rose; a crossover is shown.', 'rose cross', 'english_stem');
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['rose'],
  );
});

test('provider stemming also links an inflected query to its visible base form', () => {
  const result = segments('A rune is carved in stone.', 'runes', 'english_stem');
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['rune'],
  );
});

test('does not treat provider markup as HTML or match inside a word', () => {
  const text = '<img src=x onerror=alert(1)> A lacrosse player held a rose.';
  const result = segments(text, 'rose');

  assert.equal(result.map(part => part.text).join(''), text);
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['rose'],
  );
});

test('does not expand short query words into prefixes', () => {
  const result = segments('A figure appears in an arch.', 'a in');
  assert.deepEqual(
    result.filter(part => part.highlighted).map(part => part.text),
    ['A', 'in'],
  );
});
