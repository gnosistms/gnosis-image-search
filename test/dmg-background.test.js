const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const generator = fs.readFileSync(
  path.join(__dirname, '..', 'scripts', 'generate-dmg-background.py'),
  'utf8'
);

test('macOS installer banner renders its title and subtitle at 150 percent', () => {
  assert.match(generator, /BANNER_TEXT_SCALE = 1\.5/);
  assert.match(generator, /heading_font\(banner_text_size\(31, scale\)\)/);
  assert.match(generator, /font\(banner_text_size\(14, scale\)\)/);
});
