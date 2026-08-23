const test = require('node:test');
const assert = require('node:assert/strict');
const { mergeStage, normalizeStageResults, rankResults } = require('../web/google-ranking.js');

function item(id, sizeScore, pixelCount = 10_000_000) {
  return { id, image_url: `https://images.test/${id}.jpg`, size_score: sizeScore, pixel_count: pixelCount };
}

test('later-stage discoveries append to the sizeless order', () => {
  const compiled = [];
  mergeStage(compiled, [item('a', 22), item('b', 23)], 4);
  mergeStage(compiled, [item('b', 23), item('c', 24)], 9);
  assert.deepEqual(compiled.map(value => value.id), ['a', 'b', 'c']);
  assert.deepEqual(compiled.map(value => value.sizeless_rank), [1, 2, 3]);
  assert.equal(compiled[2].discovered_stage, 9);
  assert.deepEqual(compiled[1].stages, [4, 9]);
});

test('size-adjusted order multiplies rank points by log size score', () => {
  const compiled = [];
  mergeStage(compiled, [item('small-first', 20), item('large-second', 30)], 4);
  assert.equal(compiled[0].final_score, 40);
  assert.equal(compiled[1].final_score, 30);
  assert.deepEqual(rankResults(compiled, 'final').map(value => value.id), ['small-first', 'large-second']);
  compiled[1].size_score = 50;
  mergeStage(compiled, [], 9);
  assert.deepEqual(rankResults(compiled, 'final').map(value => value.id), ['large-second', 'small-first']);
});

test('a duplicate with better metadata keeps its original sizeless position', () => {
  const compiled = [];
  mergeStage(compiled, [item('a', 22, 8_000_000)], 4);
  mergeStage(compiled, [item('a', 25, 32_000_000)], 16);
  assert.equal(compiled.length, 1);
  assert.equal(compiled[0].sizeless_rank, 1);
  assert.equal(compiled[0].pixel_count, 32_000_000);
  assert.deepEqual(compiled[0].stages, [4, 16]);
});

test('browser metadata normalization enforces exact megapixels and selected domains', () => {
  const sources = [
    { id: 'getty', label: 'Getty', google_domains: ['getty.edu'] },
    { id: 'met', label: 'Met', google_domains: ['metmuseum.org'] },
  ];
  const raw = [
    { google_id: 'one', page_url: 'https://www.getty.edu/art/one', image_url: 'https://media.getty.edu/one.jpg', width: 5000, height: 4000 },
    { google_id: 'two', page_url: 'https://www.metmuseum.org/art/two', image_url: 'https://images.metmuseum.org/two.jpg', width: 2000, height: 2000 },
  ];
  const normalized = normalizeStageResults(raw, sources, ['getty', 'met'], 9);
  assert.deepEqual(normalized.map(value => value.source), ['getty']);
  assert.equal(normalized[0].pixel_count, 20_000_000);
});
