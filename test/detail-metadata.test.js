const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('detail panel displays artist and date independently of description', () => {
  const markup = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');

  assert.match(markup, /id="detail-artist"/);
  assert.match(markup, /id="detail-date"/);
  assert.match(markup, /id="detail-match-context"/);
  assert.match(markup, />Why this matched</);
  assert.match(script, /detailArtist\.textContent = item\.artist \|\| ''/);
  assert.match(script, /detailDate\.textContent = item\.date \|\| ''/);
  assert.match(script, /detailDescription\.textContent = item\.description/);
  assert.match(script, /detailMatchContext\.textContent = hasEvidence/);
});
