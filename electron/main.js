const { app, BrowserWindow, dialog, ipcMain, Menu, shell, systemPreferences } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const path = require('node:path');
const { reconcileModelConfiguration } = require('./model-config');

const APP_NAME = 'Gnosis Images';
const APP_DATA_NAME = 'Gnosis Image Search';
// In development Electron otherwise uses the executable name ("Electron") for
// the macOS application menu. Set this before the app becomes ready so the menu
// bar and system-provided menu items use the product name in every build mode.
app.setPath('userData', path.join(app.getPath('appData'), APP_DATA_NAME));
app.setName(APP_NAME);
if (process.platform === 'darwin') {
  // NSSavePanel otherwise defaults to its collapsed two-row presentation.
  // Persist AppKit's expanded browser state for both legacy and current modes.
  systemPreferences.setUserDefault('NSNavPanelExpandedStateForSaveMode', 'boolean', true);
  systemPreferences.setUserDefault('NSNavPanelExpandedStateForSaveMode2', 'boolean', true);
}

function installApplicationMenu() {
  if (!['darwin', 'win32'].includes(process.platform)) return;
  const checkForUpdatesItem = {
    label: 'Check for Updates…',
    click: () => checkForUpdates({ notifyIfCurrent: true })
  };
  const template = process.platform === 'darwin'
    ? [
        {
          label: APP_NAME,
          submenu: [
            { role: 'about', label: `About ${APP_NAME}` },
            checkForUpdatesItem,
            { type: 'separator' },
            { role: 'services' },
            { type: 'separator' },
            { role: 'hide', label: `Hide ${APP_NAME}` },
            { role: 'hideOthers' },
            { role: 'unhide' },
            { type: 'separator' },
            { role: 'quit', label: `Quit ${APP_NAME}` }
          ]
        },
        { role: 'fileMenu' },
        { role: 'editMenu' },
        { role: 'viewMenu' },
        { role: 'windowMenu' }
      ]
    : [
        {
          label: 'File',
          submenu: [
            checkForUpdatesItem,
            { type: 'separator' },
            { role: 'quit' }
          ]
        },
        { role: 'editMenu' },
        { role: 'viewMenu' },
        { role: 'windowMenu' }
      ];
  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

let backend = null;
let mainWindow = null;
let updateProgressWindow = null;
let updateDownload = null;
let downloadedUpdate = null;
let quitting = false;
const UPDATE_OWNER = 'gnosistms';
const UPDATE_REPOSITORY = 'gnosis-image-search';
const APP_ICON_PATH = path.resolve(__dirname, '..', 'assets', 'icon.png');
let updateCheckInProgress = false;
let googleSearchWindow = null;
const googleMetadataById = new Map();
const GOOGLE_STAGE_BUCKETS = new Map([[4, 4], [9, 8], [16, 15], [25, 20]]);
const GOOGLE_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const IMAGE_DOWNLOAD_PREFERENCES_PATH = path.join(app.getPath('userData'), 'image-download.json');
let lastImageDownloadDirectory = (() => {
  try {
    const saved = JSON.parse(fs.readFileSync(IMAGE_DOWNLOAD_PREFERENCES_PATH, 'utf8'));
    if (saved.directory && fs.statSync(saved.directory).isDirectory()) return saved.directory;
  } catch (_) {}
  return app.getPath('downloads');
})();

function rememberImageDownloadDirectory(directory) {
  lastImageDownloadDirectory = directory;
  try {
    fs.mkdirSync(path.dirname(IMAGE_DOWNLOAD_PREFERENCES_PATH), { recursive: true });
    fs.writeFileSync(IMAGE_DOWNLOAD_PREFERENCES_PATH, JSON.stringify({ directory }));
  } catch (error) {
    console.warn(`Could not remember the image download folder: ${error.message}`);
  }
}

function googleStageCachePath(query, domains, requestedMp) {
  const identity = JSON.stringify({ version: 2, query: query.toLowerCase(), domains: [...domains].sort(), requestedMp });
  const digest = crypto.createHash('sha256').update(identity).digest('hex');
  return path.join(app.getPath('userData'), 'google-image-search-cache', `${digest}.json`);
}

function readGoogleStageCache(cachePath) {
  try {
    const value = JSON.parse(fs.readFileSync(cachePath, 'utf8'));
    if (Date.now() - Number(value.fetchedAt || 0) <= GOOGLE_CACHE_TTL_MS) return value;
  } catch (_) {}
  return null;
}

function writeGoogleStageCache(cachePath, value) {
  fs.mkdirSync(path.dirname(cachePath), { recursive: true });
  const temporary = `${cachePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value));
  fs.renameSync(temporary, cachePath);
}

function googleImagesUrl(query, domains, requestedMp) {
  const sites = domains.map(domain => `site:${domain}`);
  const siteQuery = sites.length === 1 ? sites[0] : `(${sites.join(' OR ')})`;
  const url = new URL('https://www.google.com/search');
  url.searchParams.set('tbm', 'isch');
  url.searchParams.set('q', `${query} ${siteQuery}`);
  url.searchParams.set('tbs', `isz:lt,islt:${GOOGLE_STAGE_BUCKETS.get(requestedMp)}mp`);
  url.searchParams.set('num', '200');
  url.searchParams.set('hl', 'en');
  url.searchParams.set('filter', '0');
  return url.href;
}

async function loadGoogleSearchPage(window, url) {
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await window.loadURL(url);
      return;
    } catch (error) {
      lastError = error;
      const transient = /ERR_TIMED_OUT|ERR_NETWORK_CHANGED|ERR_CONNECTION_RESET|\(-7\)/i.test(String(error));
      if (!transient || attempt === 2) break;
      window.webContents.stop();
      await new Promise(resolve => setTimeout(resolve, 1500 * (attempt + 1)));
    }
  }
  throw new Error(`Google Images could not finish loading after 3 attempts: ${lastError?.message || lastError}`);
}

async function googleNeedsVerification(window) {
  return window.webContents.executeJavaScript(`Boolean(
    location.pathname.startsWith('/sorry') ||
    document.querySelector('.g-recaptcha, iframe[src*="recaptcha"]') ||
    document.body.innerText.toLowerCase().includes('unusual traffic')
  )`, true);
}

async function waitForGoogleVerification(window) {
  if (!await googleNeedsVerification(window)) return;
  window.show();
  window.setAlwaysOnTop(true, 'floating');
  window.moveTop();
  window.focus();
  window.setTitle('Verify Google Images to continue');
  const started = Date.now();
  while (!window.isDestroyed() && Date.now() - started < 5 * 60 * 1000) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (!await googleNeedsVerification(window)) {
      window.setAlwaysOnTop(false);
      window.hide();
      return;
    }
  }
  if (!window.isDestroyed()) window.setAlwaysOnTop(false);
  throw new Error('Google verification was not completed.');
}

async function waitForVisibleGoogleResults(window) {
  window.show();
  window.setAlwaysOnTop(true, 'floating');
  window.moveTop();
  window.focus();
  window.setTitle('Google Images needs attention');
  const started = Date.now();
  while (!window.isDestroyed() && Date.now() - started < 5 * 60 * 1000) {
    const results = await window.webContents.executeJavaScript(`(${scrapeRenderedGoogleImages.toString()})()`, true);
    if (results.length) {
      window.setAlwaysOnTop(false);
      window.hide();
      return results;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (!window.isDestroyed()) window.setAlwaysOnTop(false);
  throw new Error('Google Images still had no readable results after the attention window was opened.');
}

function scrapeRenderedGoogleImages() {
  const output = [];
  const seen = new Set();
  const add = item => {
    const key = item.google_id || item.image_url;
    if (!key || seen.has(key) || !/^https?:/.test(item.image_url || '')) return;
    seen.add(key);
    output.push(item);
  };
  for (const anchor of document.querySelectorAll('a[href*="/imgres?"]')) {
    try {
      const url = new URL(anchor.href, location.origin);
      const image = anchor.querySelector('img');
      const imageUrl = url.searchParams.get('imgurl') || '';
      add({
        image_url: imageUrl,
        page_url: url.searchParams.get('imgrefurl') || '',
        width: Number(url.searchParams.get('w') || 0),
        height: Number(url.searchParams.get('h') || 0),
        google_id: url.searchParams.get('tbnid') || url.searchParams.get('docid') || imageUrl,
        thumb_url: image && /^https?:/.test(image.currentSrc || image.src) ? (image.currentSrc || image.src) : '',
        title: image ? (image.alt || '') : ''
      });
    } catch (_) {}
  }

  const metadata = new Map();
  const pattern = /"([^"]+)"\s*,\s*\["https:\/\/[^".]*\.gstatic\.com\/images[^"]*"[^[]*\["(https?:\\?\/\\?\/[^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)/g;
  const decode = value => value
    .replace(/\\u0026/gi, '&')
    .replace(/\\u003d/gi, '=')
    .replace(/\\x3d/gi, '=')
    .replace(/\\\//g, '/');
  for (const script of document.scripts) {
    const text = script.textContent || '';
    if (!text.includes('AF_initDataCallback')) continue;
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text))) {
      metadata.set(match[1], {
        image_url: decode(match[2]),
        width: Number(match[3] || 0),
        height: Number(match[4] || 0),
      });
    }
  }
  const cards = [...document.querySelectorAll('[data-id]')];
  for (const [dataId, item] of metadata) {
    const card = cards.find(element => element.getAttribute('data-id') === dataId);
    const image = card && card.querySelector('img');
    const links = card ? [...card.querySelectorAll('a[href]')] : [];
    const sourceLink = links.find(link => {
      try {
        const url = new URL(link.href, location.origin);
        return /^https?:$/.test(url.protocol) && !/(^|\.)google\./.test(url.hostname);
      } catch (_) { return false; }
    });
    add({
      ...item,
      page_url: sourceLink ? sourceLink.href : '',
      google_id: dataId,
      thumb_url: image && /^https?:/.test(image.currentSrc || image.src) ? (image.currentSrc || image.src) : '',
      title: image ? (image.alt || '') : ''
    });
  }
  return output;
}

async function scrapeGoogleViewerCards(limit, knownItems = []) {
  const cards = [...document.querySelectorAll('[data-attrid="images universal"][data-docid][data-lpage]')]
    .slice(0, limit);
  const cardInfo = cards.map(card => ({
    card,
    googleId: card.getAttribute('data-docid') || '',
    pageUrl: card.getAttribute('data-lpage') || '',
    thumbnail: card.querySelector('[data-img-wrapper] img[alt]'),
    trigger: card.querySelector('[data-img-wrapper] [role="button"]')
  })).filter(item => item.googleId && item.pageUrl && item.thumbnail && item.trigger);
  const infoById = new Map(cardInfo.map(item => [item.googleId, item]));
  const resultsById = new Map();
  const knownById = new Map(knownItems.map(item => [item.google_id, item]));
  const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  for (const info of cardInfo) {
    const known = knownById.get(info.googleId);
    if (known && known.width && known.height) {
      resultsById.set(info.googleId, {
        ...known,
        page_url: info.pageUrl,
        thumb_url: info.thumbnail.currentSrc || info.thumbnail.src || known.thumb_url,
        title: info.thumbnail.alt || known.title || ''
      });
    }
  }
  const harvestLoadedViewers = () => {
    for (const viewer of document.querySelectorAll('[data-viewer-type][data-id]')) {
      const googleId = viewer.getAttribute('data-id') || '';
      const info = infoById.get(googleId);
      if (!info || resultsById.has(googleId)) continue;
      const dimensionText = [...viewer.querySelectorAll('.UWuvyf')]
        .map(element => element.textContent || '')
        .find(text => /[\d,]+\s*[×x]\s*[\d,]+/.test(text)) || '';
      const dimensions = dimensionText.match(/([\d,]+)\s*[×x]\s*([\d,]+)/);
      if (!dimensions) continue;
      const viewerImage = viewer.querySelector('img.sFlh5c.iPVvYb') || viewer.querySelector('img.sFlh5c');
      const imageUrl = viewerImage && /^https?:/.test(viewerImage.currentSrc || viewerImage.src)
        ? (viewerImage.currentSrc || viewerImage.src)
        : (info.thumbnail.currentSrc || info.thumbnail.src || '');
      resultsById.set(googleId, {
        image_url: imageUrl,
        page_url: info.pageUrl,
        width: Number(dimensions[1].replaceAll(',', '')),
        height: Number(dimensions[2].replaceAll(',', '')),
        google_id: googleId,
        thumb_url: info.thumbnail.currentSrc || info.thumbnail.src || imageUrl,
        title: info.thumbnail.alt || viewerImage?.alt || ''
      });
    }
  };
  harvestLoadedViewers();
  for (const info of cardInfo) {
    if (resultsById.has(info.googleId)) continue;
    info.trigger.click();
    for (let attempt = 0; attempt < 24; attempt += 1) {
      await delay(50);
      harvestLoadedViewers();
      if (resultsById.has(info.googleId)) break;
    }
  }
  return cardInfo.map(info => resultsById.get(info.googleId)).filter(Boolean);
}

async function extractGoogleImageResults(window, limit = 200, verificationRetries = 0, knownItems = []) {
  const collected = new Map();
  let previousCardCount = -1;
  let stableCardRounds = 0;
  while (previousCardCount < limit && stableCardRounds < 3) {
    if (await googleNeedsVerification(window)) await waitForGoogleVerification(window);
    const cardCount = await window.webContents.executeJavaScript(
      `document.querySelectorAll('[data-attrid="images universal"][data-docid][data-lpage]').length`,
      true,
    );
    stableCardRounds = cardCount === previousCardCount ? stableCardRounds + 1 : 0;
    previousCardCount = cardCount;
    if (cardCount >= limit) break;
    await window.webContents.executeJavaScript('window.scrollTo(0, document.body.scrollHeight)', true);
    await new Promise(resolve => setTimeout(resolve, 800));
  }
  const viewerResults = await window.webContents.executeJavaScript(
    `(${scrapeGoogleViewerCards.toString()})(${limit}, ${JSON.stringify(knownItems)})`,
    true,
  );
  for (const item of viewerResults) collected.set(item.google_id, item);

  let unchangedRounds = 0;
  while (collected.size < limit && unchangedRounds < 4) {
    if (await googleNeedsVerification(window)) {
      await waitForGoogleVerification(window);
      unchangedRounds = 0;
      continue;
    }
    const batch = await window.webContents.executeJavaScript(`(${scrapeRenderedGoogleImages.toString()})()`, true);
    const before = collected.size;
    for (const item of batch) {
      if (!collected.has(item.google_id)) collected.set(item.google_id, item);
      if (collected.size >= limit) break;
    }
    unchangedRounds = collected.size === before ? unchangedRounds + 1 : 0;
    if (collected.size >= limit) break;
    await window.webContents.executeJavaScript('window.scrollTo(0, document.body.scrollHeight)', true);
    await new Promise(resolve => setTimeout(resolve, 900));
    if (await googleNeedsVerification(window)) await waitForGoogleVerification(window);
  }
  if (!collected.size) {
    if (verificationRetries < 1 && await googleNeedsVerification(window)) {
      await waitForGoogleVerification(window);
      return extractGoogleImageResults(window, limit, verificationRetries + 1, knownItems);
    }
    const visibleResults = await waitForVisibleGoogleResults(window);
    for (const item of visibleResults) collected.set(item.google_id, item);
  }
  return [...collected.values()].slice(0, limit);
}

async function collectGoogleStage(_event, options = {}) {
  const query = String(options.query || '').trim();
  const requestedMp = Number(options.mp);
  const domains = Array.isArray(options.domains)
    ? [...new Set(options.domains.map(value => String(value).toLowerCase().trim()))]
    : [];
  if (!query || query.length > 240) throw new Error('Enter a search of 240 characters or fewer.');
  if (!GOOGLE_STAGE_BUCKETS.has(requestedMp)) throw new Error('Invalid megapixel stage.');
  if (!domains.length || domains.length > 30 || domains.some(domain => !/^[a-z0-9.-]+$/.test(domain))) {
    throw new Error('Invalid museum domain selection.');
  }
  const cachePath = googleStageCachePath(query, domains, requestedMp);
  const cached = readGoogleStageCache(cachePath);
  if (cached) {
    for (const item of cached.results || []) googleMetadataById.set(item.google_id, item);
    return { ...cached, cached: true };
  }

  if (!googleSearchWindow || googleSearchWindow.isDestroyed()) {
    googleSearchWindow = new BrowserWindow({
      title: 'Google Images metadata collection',
      width: 1180,
      height: 820,
      show: false,
      parent: mainWindow || undefined,
      webPreferences: { nodeIntegration: false, contextIsolation: true, partition: 'persist:gnosis-google-images' },
    });
    googleSearchWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
      return { action: 'deny' };
    });
    googleSearchWindow.on('closed', () => { googleSearchWindow = null; });
  }
  await loadGoogleSearchPage(googleSearchWindow, googleImagesUrl(query, domains, requestedMp));
  await waitForGoogleVerification(googleSearchWindow);
  await new Promise(resolve => setTimeout(resolve, 800));
  const results = await extractGoogleImageResults(googleSearchWindow, 200, 0, [...googleMetadataById.values()]);
  for (const item of results) googleMetadataById.set(item.google_id, item);
  const value = { results, fetchedAt: Date.now(), cached: false };
  writeGoogleStageCache(cachePath, value);
  return value;
}

ipcMain.handle('google-images:search-stage', collectGoogleStage);

ipcMain.handle('image:download-full-size', (event, options = {}) => {
  const url = String(options.url || '');
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('The full-sized image URL is invalid.');
  }
  const filename = path.basename(String(options.filename || 'image')) || 'image';
  const downloadSession = event.sender.session;
  return new Promise((resolve, reject) => {
    downloadSession.once('will-download', (_downloadEvent, item) => {
      item.setSaveDialogOptions({
        title: 'Save full sized image',
        defaultPath: path.join(lastImageDownloadDirectory, filename),
        properties: ['createDirectory', 'showOverwriteConfirmation'],
      });
      item.once('done', (_doneEvent, state) => {
        if (state === 'completed') {
          rememberImageDownloadDirectory(path.dirname(item.getSavePath()));
        }
        resolve({ state });
      });
    });
    try {
      event.sender.downloadURL(url);
    } catch (error) {
      reject(error);
    }
  });
});

function parseVersion(value) {
  return String(value).replace(/^v/, '').split(/[.-]/).slice(0, 3).map(part => Number(part) || 0);
}

function isNewerVersion(candidate, current) {
  const left = parseVersion(candidate);
  const right = parseVersion(current);
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index];
  }
  return false;
}

function latestRelease() {
  const options = {
    hostname: 'api.github.com',
    path: `/repos/${UPDATE_OWNER}/${UPDATE_REPOSITORY}/releases/latest`,
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': `${UPDATE_REPOSITORY}/${app.getVersion()}`
    }
  };
  return new Promise((resolve, reject) => {
    https.get(options, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => {
        if (response.statusCode === 404) resolve(null);
        else if (response.statusCode !== 200) reject(new Error(`GitHub returned ${response.statusCode}.`));
        else resolve(JSON.parse(body));
      });
    }).on('error', reject);
  });
}

function showMessageBox(options) {
  if (mainWindow && !mainWindow.isDestroyed()) return dialog.showMessageBox(mainWindow, options);
  return dialog.showMessageBox(options);
}

function compatibleUpdateAsset(release) {
  const assets = Array.isArray(release.assets) ? release.assets : [];
  const extensions = process.platform === 'darwin' ? ['.dmg', '.zip'] : ['.exe', '.msi'];
  const candidates = assets.filter(asset =>
    asset.browser_download_url && extensions.some(extension => asset.name.toLowerCase().endsWith(extension))
  );
  const architecture = process.arch === 'arm64' ? /arm64|aarch64/i : /x64|x86_64|amd64/i;
  return candidates.find(asset => architecture.test(asset.name)) || candidates[0] || null;
}

function updateWindowHtml(version) {
  const safeVersion = String(version).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  return `<!doctype html>
    <meta charset="utf-8">
    <style>
      :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #171714; color: #f5f1e8; }
      main { width: 380px; }
      h1 { margin: 0 0 8px; font-size: 18px; font-weight: 600; }
      p { margin: 0 0 20px; color: #bbb7ad; font-size: 13px; line-height: 1.45; }
      .track { height: 7px; overflow: hidden; border-radius: 999px; background: #34332e; }
      .bar { width: 0; height: 100%; border-radius: inherit; background: #d7b46a; transition: width 120ms linear; }
      .status { min-height: 18px; margin: 8px 0 20px; font-variant-numeric: tabular-nums; }
      .buttons { display: flex; justify-content: flex-end; gap: 9px; }
      a { padding: 7px 13px; border: 1px solid #555149; border-radius: 7px; color: #f5f1e8; text-decoration: none; font-size: 13px; }
      a.primary { border-color: #b89550; background: #9b7939; }
    </style>
    <main>
      <h1>Downloading Gnosis Images ${safeVersion}</h1>
      <p>You can keep using Gnosis Images while the update downloads.</p>
      <div class="track"><div class="bar" id="bar"></div></div>
      <p class="status" id="status">Starting download…</p>
      <div class="buttons"><a href="gnosis-update:cancel">Cancel</a><a class="primary" href="gnosis-update:background">Continue in background</a></div>
    </main>
    <script>
      window.setDownloadProgress = (percent, received, total) => {
        document.getElementById('bar').style.width = percent + '%';
        const format = bytes => bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.round(bytes / 1024) + ' KB';
        document.getElementById('status').textContent = total
          ? percent + '% — ' + format(received) + ' of ' + format(total)
          : format(received) + ' downloaded';
      };
    </script>`;
}

async function showUpdateProgressWindow(version) {
  if (updateProgressWindow && !updateProgressWindow.isDestroyed()) {
    updateProgressWindow.show();
    updateProgressWindow.focus();
    return;
  }
  updateProgressWindow = new BrowserWindow({
    title: 'Downloading update',
    width: 470,
    height: 245,
    show: false,
    resizable: false,
    minimizable: true,
    maximizable: false,
    autoHideMenuBar: true,
    backgroundColor: '#171714',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });
  updateProgressWindow.on('closed', () => { updateProgressWindow = null; });
  updateProgressWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('gnosis-update:')) return;
    event.preventDefault();
    if (url === 'gnosis-update:cancel') cancelUpdateDownload();
    else if (url === 'gnosis-update:background' && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
  await updateProgressWindow.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(updateWindowHtml(version))}`);
  if (updateProgressWindow && !updateProgressWindow.isDestroyed()) updateProgressWindow.show();
}

function closeUpdateProgressWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setProgressBar(-1);
  if (updateProgressWindow && !updateProgressWindow.isDestroyed()) updateProgressWindow.close();
  updateProgressWindow = null;
}

function reportUpdateProgress(received, total) {
  const percent = total ? Math.min(100, Math.round((received / total) * 100)) : 0;
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setProgressBar(total ? received / total : 2, total ? undefined : { mode: 'indeterminate' });
  if (updateProgressWindow && !updateProgressWindow.isDestroyed()) {
    updateProgressWindow.webContents.executeJavaScript(`window.setDownloadProgress(${percent}, ${received}, ${total})`).catch(() => {});
  }
}

function downloadFile(url, destination, state, redirectsRemaining = 5) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const client = parsedUrl.protocol === 'http:' ? http : https;
    const request = client.get(parsedUrl, response => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode) && response.headers.location) {
        response.resume();
        if (redirectsRemaining === 0) {
          reject(new Error('The update download redirected too many times.'));
          return;
        }
        downloadFile(new URL(response.headers.location, parsedUrl).href, destination, state, redirectsRemaining - 1).then(resolve, reject);
        return;
      }
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`The update server returned ${response.statusCode}.`));
        return;
      }
      const total = Number(response.headers['content-length']) || 0;
      let received = 0;
      let lastReport = 0;
      const file = fs.createWriteStream(destination);
      state.file = file;
      response.on('data', chunk => {
        received += chunk.length;
        if (Date.now() - lastReport >= 100 || received === total) {
          lastReport = Date.now();
          reportUpdateProgress(received, total);
        }
      });
      response.on('error', reject);
      file.on('error', reject);
      file.on('finish', () => file.close(() => resolve()));
      response.pipe(file);
    });
    state.request = request;
    request.on('error', reject);
  });
}

function cancelUpdateDownload() {
  if (!updateDownload) return;
  updateDownload.cancelled = true;
  updateDownload.request?.destroy(new Error('Update download cancelled.'));
  updateDownload.file?.destroy();
  if (updateDownload.partialPath) fs.promises.unlink(updateDownload.partialPath).catch(() => {});
  updateDownload = null;
  closeUpdateProgressWindow();
}

async function askToInstallUpdate(update) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
  }
  const response = await showMessageBox({
    type: 'info',
    title: 'Install update',
    message: `Install Gnosis Images version ${update.version}?`,
    detail: process.platform === 'darwin'
      ? 'The macOS installer will open when you click Install.'
      : 'The installer will open when you click Install.',
    buttons: ['Later', 'Install'],
    defaultId: 1,
    cancelId: 0,
    noLink: true
  });
  if (response.response !== 1) return;
  const error = await shell.openPath(update.filePath);
  if (error) {
    await showMessageBox({
      type: 'error',
      title: 'Could not open installer',
      message: 'Gnosis Images could not open the downloaded installer.',
      detail: error,
      buttons: ['OK']
    });
  }
}

async function startUpdateDownload(release, asset) {
  const version = release.tag_name.replace(/^v/, '');
  const updateDirectory = path.join(app.getPath('temp'), 'gnosis-images-updates');
  await fs.promises.mkdir(updateDirectory, { recursive: true });
  const finalPath = path.join(updateDirectory, path.basename(asset.name));
  const partialPath = `${finalPath}.download`;
  await fs.promises.unlink(partialPath).catch(() => {});
  const state = { request: null, file: null, cancelled: false, partialPath, version };
  updateDownload = state;
  await showUpdateProgressWindow(version);
  try {
    await downloadFile(asset.browser_download_url, partialPath, state);
    if (state.cancelled) return;
    await fs.promises.unlink(finalPath).catch(() => {});
    await fs.promises.rename(partialPath, finalPath);
    downloadedUpdate = { version, filePath: finalPath };
    updateDownload = null;
    closeUpdateProgressWindow();
    await askToInstallUpdate(downloadedUpdate);
  } catch (error) {
    await fs.promises.unlink(partialPath).catch(() => {});
    if (state.cancelled) return;
    updateDownload = null;
    closeUpdateProgressWindow();
    console.error(`Update download failed: ${error.stack || error}`);
    await showMessageBox({
      type: 'error',
      title: 'Update download failed',
      message: 'Gnosis Images could not download the update.',
      detail: 'Check your internet connection and try again.',
      buttons: ['OK']
    });
  }
}

async function checkForUpdates({ notifyIfCurrent = false } = {}) {
  if (!app.isPackaged || !['darwin', 'win32'].includes(process.platform)) return;
  if (updateDownload) {
    await showUpdateProgressWindow(updateDownload.version);
    return;
  }
  if (downloadedUpdate) {
    await askToInstallUpdate(downloadedUpdate);
    return;
  }
  if (updateCheckInProgress) return;
  updateCheckInProgress = true;
  try {
    const release = await latestRelease();
    if (!release || !isNewerVersion(release.tag_name, app.getVersion())) {
      if (notifyIfCurrent) {
        await showMessageBox({
          type: 'info',
          title: 'No updates available',
          message: `${APP_NAME} is up to date.`,
          detail: `You are using version ${app.getVersion()}.`,
          buttons: ['OK']
        });
      }
      return;
    }
    const response = await showMessageBox({
      type: 'info',
      title: 'Update available',
      message: `Gnosis Images ${release.tag_name.replace(/^v/, '')} is available.`,
      detail: 'Would you like to download it now? Your image model and embedding cache are stored separately and will not be downloaded again.',
      buttons: ['Download update', 'Later'],
      defaultId: 0,
      cancelId: 1
    });
    if (response.response !== 0) return;
    const asset = compatibleUpdateAsset(release);
    if (!asset) {
      await showMessageBox({
        type: 'error',
        title: 'Update download unavailable',
        message: 'No compatible installer is available for this Mac or PC.',
        detail: 'Please try again later.',
        buttons: ['OK']
      });
      return;
    }
    await startUpdateDownload(release, asset);
  } catch (error) {
    console.error(`Update check failed: ${error.stack || error}`);
    if (notifyIfCurrent) {
      await showMessageBox({
        type: 'error',
        title: 'Could not check for updates',
        message: `${APP_NAME} could not check for updates.`,
        detail: 'Check your internet connection and try again.',
        buttons: ['OK']
      });
    }
  } finally {
    updateCheckInProgress = false;
  }
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function backendCommand(port) {
  const args = ['--host', '127.0.0.1', '--port', String(port)];
  if (app.isPackaged) {
    return {
      executable: path.join(process.resourcesPath, 'backend', 'gnosis-search-engine', 'gnosis-search-engine'),
      args
    };
  }
  return {
    executable: path.resolve(__dirname, '../../automatic-illustrator/prototype/venv/bin/python'),
    args: [path.resolve(__dirname, '..', 'server.py'), ...args]
  };
}

function waitUntilReady(port, child) {
  // First launch seeds the bundled museum indexes into Application Support
  // before the backend starts listening. Slow disks and mounted DMGs can make
  // that one-time copy exceed two minutes even though the backend is healthy.
  const deadline = Date.now() + 240000;
  return new Promise((resolve, reject) => {
    const probe = () => {
      if (child.exitCode !== null) {
        reject(new Error(`Search engine exited with code ${child.exitCode}.`));
        return;
      }
      const request = http.get(
        { hostname: '127.0.0.1', port, path: '/api/sources', timeout: 1000 },
        (response) => {
          response.resume();
          if (response.statusCode === 200) resolve();
          else retry();
        }
      );
      request.on('error', retry);
      request.on('timeout', () => request.destroy());
    };
    const retry = () => {
      if (Date.now() >= deadline) reject(new Error('The search engine did not start in time.'));
      else setTimeout(probe, 250);
    };
    probe();
  });
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  backend.kill('SIGTERM');
  const processToStop = backend;
  setTimeout(() => {
    if (processToStop.exitCode === null) processToStop.kill('SIGKILL');
  }, 3000).unref();
}

async function createApplication() {
  const port = await reservePort();
  const command = backendCommand(port);
  const dataDirectory = path.join(app.getPath('userData'), 'data');
  const { configPath, value: modelConfig } = reconcileModelConfiguration(app.getPath('userData'));
  const activeModel = modelConfig.profiles?.[modelConfig.activeProfile] || {};
  console.log(`Model configuration: ${configPath}`);
  backend = spawn(command.executable, command.args, {
    env: {
      ...process.env,
      SEARCH_DATA_DIR: dataDirectory,
      SEARCH_MODEL_KIND: activeModel.modelKind || 'siglip',
      SEARCH_MODEL_NAME: activeModel.checkpoint || 'google/siglip2-base-patch16-256',
      SEARCH_MODEL_ALLOW_DOWNLOAD: '1',
      ...(activeModel.cacheDirectory ? { SEARCH_MODEL_CACHE_DIR: activeModel.cacheDirectory } : {}),
      ...(activeModel.axisModel ? { SEARCH_AXIS_MODEL: activeModel.axisModel } : {}),
      ...(activeModel.referenceEmbeddings ? { SEARCH_PAMELA_EMBEDDINGS: activeModel.referenceEmbeddings } : {}),
      PYTHONUNBUFFERED: '1'
    },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  backend.stdout.on('data', (chunk) => console.log(`[search] ${chunk.toString().trimEnd()}`));
  backend.stderr.on('data', (chunk) => console.error(`[search] ${chunk.toString().trimEnd()}`));
  backend.on('exit', (code, signal) => {
    if (!quitting && mainWindow) {
      dialog.showErrorBox('Search engine stopped', `The local search engine stopped unexpectedly (${signal || code}).`);
    }
  });
  await waitUntilReady(port, backend);

  mainWindow = new BrowserWindow({
    title: APP_NAME,
    icon: APP_ICON_PATH,
    width: 1460,
    height: 980,
    minWidth: 900,
    minHeight: 650,
    show: false,
    backgroundColor: '#11110f',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 18, y: 19 },
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js')
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  await mainWindow.loadURL(`http://127.0.0.1:${port}/?desktop=1`);
  const googleBridgeReady = await mainWindow.webContents.executeJavaScript(
    "typeof window.gnosisGoogle?.searchStage === 'function'",
    true,
  );
  if (!googleBridgeReady) throw new Error('Google Images browser bridge did not load.');
  if (process.platform === 'darwin') setTimeout(checkForUpdates, 2500).unref();
}

const hasLock = app.requestSingleInstanceLock();
if (!hasLock) app.quit();
else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(() => {
    if (process.platform === 'darwin') app.dock.setIcon(APP_ICON_PATH);
    installApplicationMenu();
    return createApplication();
  }).catch((error) => {
    dialog.showErrorBox('Gnosis Images could not start', error.stack || String(error));
    app.quit();
  });
}

app.on('before-quit', () => {
  quitting = true;
  cancelUpdateDownload();
  stopBackend();
});

app.on('window-all-closed', () => app.quit());
