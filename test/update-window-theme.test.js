const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');

function section(start, end) {
  return main.slice(main.indexOf(start), main.indexOf(end));
}

test('application update window uses the light cream and wine download theme', () => {
  const html = section('function updateWindowHtml', 'function modelWindowHtml');
  assert.match(html, /color-scheme: light/);
  assert.match(html, /background: #f3ede4/);
  assert.match(html, /background: #602f40/);
  assert.match(html, /background: #74364a/);
  assert.match(html, /<header class="titlebar">Downloading update<\/header>/);
  assert.doesNotMatch(html, /#171714|color-scheme: dark/);
});

test('application update window uses the themed macOS title bar', () => {
  const options = section('async function showUpdateProgressWindow', 'function closeUpdateProgressWindow');
  assert.match(options, /height: 270/);
  assert.match(options, /backgroundColor: '#f3ede4'/);
  assert.match(options, /titleBarStyle: process\.platform === 'darwin' \? 'hiddenInset' : 'default'/);
  assert.match(options, /trafficLightPosition: \{ x: 18, y: 8 \}/);
  assert.doesNotMatch(options, /backgroundColor: '#171714'/);
});
