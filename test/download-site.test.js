const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function element() {
  const classes = new Set();
  return {
    attributes: {},
    classList: {
      add: value => classes.add(value),
      contains: value => classes.has(value),
      remove: value => classes.delete(value),
    },
    addEventListener(type, listener) {
      this.listeners ||= {};
      this.listeners[type] = listener;
    },
    removeAttribute(name) {
      delete this.attributes[name];
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

test('macOS download starts without navigating the top-level page', async () => {
  const button = element();
  const note = element();
  const help = { hidden: true };
  const fallback = element();
  let resetDownloadState;
  const elements = {
    '#download': button,
    '#download-fallback': fallback,
    '#download-help': help,
    '#platform-note': note,
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'docs', 'download.js'), 'utf8');

  vm.runInNewContext(source, {
    clearTimeout() {},
    document: { querySelector: selector => elements[selector] },
    fetch: async () => ({
      json: async () => ({
        assets: [{
          browser_download_url: 'https://example.test/Gnosis-Images-Update.dmg',
          name: 'Gnosis-Images-Update-1.0.0-arm64.dmg',
          size: 104857600,
        }, {
          browser_download_url: 'https://example.test/Gnosis.Images.dmg',
          name: 'Gnosis-Images-Full-Installer-1.0.0-arm64.dmg',
          size: 529224507,
        }],
        html_url: 'https://example.test/releases/v1',
        tag_name: 'v1.0.0',
      }),
      ok: true,
    }),
    navigator: { platform: 'MacIntel', userAgent: 'Mozilla/5.0 (Macintosh)' },
    setTimeout: callback => {
      resetDownloadState = callback;
      return 1;
    },
  });
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(button.href, 'https://example.test/Gnosis.Images.dmg');
  assert.equal(button.target, 'download-frame');
  assert.equal(button.textContent, 'Download for macOS');
  assert.equal(note.textContent, '1.0.0 · macOS · 505 MB');

  button.listeners.click({ preventDefault: () => assert.fail('first click should start the download') });
  assert.equal(button.textContent, 'Starting download…');
  assert.equal(button.attributes['aria-busy'], 'true');
  assert.match(note.textContent, /browser's Downloads list/);

  let prevented = false;
  button.listeners.click({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);

  resetDownloadState();
  assert.equal(button.textContent, 'Download again');
  assert.equal(help.hidden, false);
  assert.equal(button.attributes['aria-busy'], undefined);
});

test('Windows download selects Setup.exe instead of an update ZIP', async () => {
  const button = element();
  const note = element();
  const elements = {
    '#download': button,
    '#download-fallback': element(),
    '#download-help': { hidden: true },
    '#platform-note': note,
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'docs', 'download.js'), 'utf8');

  vm.runInNewContext(source, {
    clearTimeout() {},
    document: { querySelector: selector => elements[selector] },
    fetch: async () => ({
      json: async () => ({
        assets: [{
          browser_download_url: 'https://example.test/Gnosis-Images-Update.zip',
          name: 'Gnosis-Images-Update-1.0.0-x64.zip',
          size: 100,
        }, {
          browser_download_url: 'https://example.test/Gnosis-Images-Setup.exe',
          name: 'Gnosis-Images-Full-Installer-1.0.0-x64.exe',
          size: 209715200,
        }],
        html_url: 'https://example.test/releases/v1',
        tag_name: 'v1.0.0',
      }),
      ok: true,
    }),
    navigator: { platform: 'Win32', userAgent: 'Mozilla/5.0 (Windows NT 10.0)' },
    setTimeout: () => 1,
  });
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(button.href, 'https://example.test/Gnosis-Images-Setup.exe');
  assert.equal(button.textContent, 'Download for Windows');
  assert.equal(note.textContent, '1.0.0 · Windows · 200 MB');
});
