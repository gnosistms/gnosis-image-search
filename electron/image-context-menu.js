const COPYABLE_IMAGE_PROTOCOLS = new Set(['http:', 'https:', 'data:', 'blob:']);

function normalizedImageUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return COPYABLE_IMAGE_PROTOCOLS.has(url.protocol) ? url.href : '';
  } catch (_error) {
    return '';
  }
}

function imageContextMenuTemplate({ imageUrl, x, y, webContents, clipboard, shell, saveImage }) {
  const url = normalizedImageUrl(imageUrl);
  if (!url) return [];

  const canOpenExternally = /^https?:/i.test(url);
  const template = [];
  if (canOpenExternally) {
    template.push({
      label: 'Open Image in Browser',
      click: () => shell.openExternal(url),
    });
  }
  template.push({
    label: 'Save Image As…',
    click: () => saveImage(url),
  });
  template.push({ type: 'separator' });
  template.push({
    label: 'Copy Image',
    click: () => webContents.copyImageAt(x, y),
  });
  template.push({
    label: 'Copy Image URL',
    click: () => clipboard.writeText(url),
  });
  return template;
}

module.exports = { imageContextMenuTemplate, normalizedImageUrl };
