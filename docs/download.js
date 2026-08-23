const repository = 'https://api.github.com/repos/gnosistms/gnosis-image-search/releases/latest';
const button = document.querySelector('#download');
const note = document.querySelector('#platform-note');
const help = document.querySelector('#download-help');
const fallback = document.querySelector('#download-fallback');

let download = null;
let resetTimer = null;

function platform() {
  const value = `${navigator.userAgentData?.platform || ''} ${navigator.platform || ''} ${navigator.userAgent || ''}`.toLowerCase();
  if (value.includes('mac')) return { name: 'macOS', extensions: ['.dmg', 'darwin-arm64.zip', '.zip'] };
  if (value.includes('win')) return { name: 'Windows', extensions: ['.exe', '.msi'] };
  if (value.includes('linux')) return { name: 'Linux', extensions: ['.appimage', '.deb', '.rpm'] };
  return { name: 'your operating system', extensions: [] };
}

async function configureDownload() {
  const detected = platform();
  try {
    const response = await fetch(repository, { headers: { Accept: 'application/vnd.github+json' } });
    if (!response.ok) throw new Error(`GitHub returned ${response.status}`);
    const release = await response.json();
    const assets = release.assets || [];
    const installers = assets.filter(item => /(?:^|[-_. ])(?:full[-_. ]?)?installer(?:[-_. ]|$)/i.test(item.name));
    const asset = detected.extensions
      .map(extension => installers.find(item => item.name.toLowerCase().endsWith(extension)))
      .find(Boolean);
    if (asset) {
      download = {
        name: detected.name,
        size: `${Math.round(asset.size / 1048576)} MB`,
        url: asset.browser_download_url,
        version: release.tag_name.replace(/^v/, ''),
      };
      button.href = asset.browser_download_url;
      button.textContent = `Download for ${detected.name}`;
      button.target = 'download-frame';
      fallback.href = release.html_url;
      note.textContent = `${download.version} · ${download.name} · ${download.size}`;
    } else {
      button.href = release.html_url;
      button.textContent = 'View latest release';
      note.textContent = `A direct ${detected.name} download is not available yet.`;
    }
  } catch {
    button.textContent = 'View latest release';
    note.textContent = `Choose the ${detected.name} download on GitHub.`;
  }
}

button.addEventListener('click', event => {
  if (!download) return;
  if (button.classList.contains('is-downloading')) {
    event.preventDefault();
    return;
  }

  clearTimeout(resetTimer);
  help.hidden = true;
  button.classList.add('is-downloading');
  button.setAttribute('aria-busy', 'true');
  button.textContent = 'Starting download…';
  note.textContent = `${download.size} · Check your browser's Downloads list for progress.`;

  resetTimer = setTimeout(() => {
    button.classList.remove('is-downloading');
    button.removeAttribute('aria-busy');
    button.textContent = 'Download again';
    help.hidden = false;
  }, 2500);
});

configureDownload();
