const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('portrait result tiles cannot flex into landscape crops', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'web', 'styles.css'), 'utf8');

  assert.match(script, /classList\.toggle\('portrait-image', boundedRatio < 1\)/);
  assert.match(script, /image\.naturalWidth \/ image\.naturalHeight/);
  assert.match(script, /Number\(tile\.dataset\.intrinsicRatio\) \|\| imageRatio\(item\)/);
  assert.match(
    styles,
    /\.image-tile\.portrait-image \{ max-width: max\(92px, calc\(var\(--ratio\) \* 178px\)\); \}/,
  );
});
