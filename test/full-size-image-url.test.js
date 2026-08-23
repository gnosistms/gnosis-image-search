const assert = require('node:assert/strict');
const test = require('node:test');

const {
  AVAILABLE_TOOLTIP,
  UNAVAILABLE_TOOLTIP,
  controlState,
  fullSizeImageUrl,
} = require('../web/full-size-image-url');

test('copy control uses the direct full-sized download URL', () => {
  const item = {
    image_url: 'https://images.example/preview.jpg',
    download_url: 'https://images.example/original image.tif',
  };

  assert.equal(fullSizeImageUrl(item), 'https://images.example/original%20image.tif');
  assert.deepEqual(controlState(item), {
    disabled: false,
    tooltip: AVAILABLE_TOOLTIP,
    url: 'https://images.example/original%20image.tif',
  });
});

test('copy control stays visible but disabled without a full-sized URL', () => {
  assert.deepEqual(controlState({ image_url: 'https://images.example/preview.jpg' }), {
    disabled: true,
    tooltip: UNAVAILABLE_TOOLTIP,
    url: '',
  });
});

test('copy control rejects non-web download URLs', () => {
  assert.equal(fullSizeImageUrl({ download_url: 'javascript:alert(1)' }), '');
  assert.equal(fullSizeImageUrl({ download_url: '/api/image/detail' }), '');
});
