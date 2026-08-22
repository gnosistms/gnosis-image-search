const { app, autoUpdater, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const path = require('node:path');

let backend = null;
let mainWindow = null;
let quitting = false;
const UPDATE_OWNER = 'gnosistms';
const UPDATE_REPOSITORY = 'gnosis-image-search';

function modelConfiguration() {
  const configPath = path.join(app.getPath('userData'), 'model-config.json');
  const defaults = {
    activeProfile: 'pamela-siglip2-large-v1',
    profiles: {
      'pamela-siglip2-large-v1': {
        checkpoint: 'google/siglip2-large-patch16-256',
        cacheDirectory: null,
        axisModel: null,
        referenceEmbeddings: null
      }
    }
  };
  try {
    const saved = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    return { configPath, value: { ...defaults, ...saved } };
  } catch {
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, `${JSON.stringify(defaults, null, 2)}\n`);
    return { configPath, value: defaults };
  }
}

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

async function checkForUpdatesAtStartup() {
  if (!app.isPackaged || process.platform !== 'darwin') return;
  try {
    const release = await latestRelease();
    if (!release || !isNewerVersion(release.tag_name, app.getVersion())) return;
    const response = await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update available',
      message: `Gnosis Image Search ${release.tag_name.replace(/^v/, '')} is available.`,
      detail: 'Would you like to download and install it now? Your image model and embedding cache are stored separately and will not be downloaded again.',
      buttons: ['Update now', 'Later'],
      defaultId: 0,
      cancelId: 1
    });
    if (response.response !== 0) return;
    autoUpdater.setFeedURL({
      url: `https://update.electronjs.org/${UPDATE_OWNER}/${UPDATE_REPOSITORY}/${process.platform}/${app.getVersion()}`
    });
    autoUpdater.once('update-downloaded', () => {
      quitting = true;
      stopBackend();
      autoUpdater.quitAndInstall();
    });
    autoUpdater.checkForUpdates();
  } catch (error) {
    console.error(`Update check failed: ${error.stack || error}`);
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
  const deadline = Date.now() + 120000;
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
  const { configPath, value: modelConfig } = modelConfiguration();
  const activeModel = modelConfig.profiles?.[modelConfig.activeProfile] || {};
  console.log(`Model configuration: ${configPath}`);
  backend = spawn(command.executable, command.args, {
    env: {
      ...process.env,
      SEARCH_DATA_DIR: dataDirectory,
      SEARCH_MODEL_NAME: activeModel.checkpoint || 'google/siglip2-large-patch16-256',
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
    title: 'Gnosis Image Search',
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
      sandbox: true
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  await mainWindow.loadURL(`http://127.0.0.1:${port}/?desktop=1`);
  setTimeout(checkForUpdatesAtStartup, 2500).unref();
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
  app.whenReady().then(createApplication).catch((error) => {
    dialog.showErrorBox('Gnosis Image Search could not start', error.stack || String(error));
    app.quit();
  });
}

app.on('before-quit', () => {
  quitting = true;
  stopBackend();
});

app.on('window-all-closed', () => app.quit());
