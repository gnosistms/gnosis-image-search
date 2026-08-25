const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('Electron enables and themes the native Windows title-bar overlay', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  const preload = fs.readFileSync(path.join(__dirname, '..', 'electron', 'preload.js'), 'utf8');
  const renderer = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');

  assert.match(main, /process\.platform === 'win32' \? 'hidden' : 'default'/);
  assert.match(main, /titleBarOverlay: \{ color: '#4c2835', symbolColor: '#ffffff', height: 36 \}/);
  assert.match(main, /mainWindow\.setTitleBarOverlay/);
  assert.match(main, /platform=\$\{process\.platform\}/);
  assert.match(preload, /setTitleBarTheme: theme => ipcRenderer\.send\('window:title-bar-theme', theme\)/);
  assert.match(renderer, /classList\.add\(`desktop-\$\{desktopPlatform\}`\)/);
  assert.match(renderer, /setTitleBarTheme\?\.\('search'\)/);
});
