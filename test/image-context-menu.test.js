const assert = require('node:assert/strict');
const test = require('node:test');

const { imageContextMenuTemplate, normalizedImageUrl } = require('../electron/image-context-menu');

test('image context menu provides browser-style image actions', () => {
  const calls = [];
  const template = imageContextMenuTemplate({
    imageUrl: 'https://images.example/artwork.jpg',
    x: 24,
    y: 36,
    webContents: { copyImageAt: (...args) => calls.push(['copy-image', ...args]) },
    clipboard: { writeText: value => calls.push(['copy-url', value]) },
    shell: { openExternal: value => calls.push(['open', value]) },
    saveImage: value => calls.push(['save', value]),
  });

  assert.deepEqual(template.map(item => item.label || item.type), [
    'Open Image in Browser',
    'Save Image As…',
    'separator',
    'Copy Image',
    'Copy Image URL',
  ]);
  template[0].click();
  template[1].click();
  template[3].click();
  template[4].click();
  assert.deepEqual(calls, [
    ['open', 'https://images.example/artwork.jpg'],
    ['save', 'https://images.example/artwork.jpg'],
    ['copy-image', 24, 36],
    ['copy-url', 'https://images.example/artwork.jpg'],
  ]);
});

test('image URLs are limited to supported media protocols', () => {
  assert.equal(normalizedImageUrl('https://images.example/a b.jpg'), 'https://images.example/a%20b.jpg');
  assert.equal(normalizedImageUrl('javascript:alert(1)'), '');
  assert.equal(normalizedImageUrl('/relative/image.jpg'), '');
});
