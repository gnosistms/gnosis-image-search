const repository = 'https://api.github.com/repos/gnosistms/gnosis-image-search/releases/latest';
const button = document.querySelector('#download');
const note = document.querySelector('#platform-note');

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
    const asset = detected.extensions
      .map(extension => assets.find(item => item.name.toLowerCase().endsWith(extension)))
      .find(Boolean);
    if (asset) {
      button.href = asset.browser_download_url;
      button.textContent = `Download for ${detected.name}`;
      note.textContent = `${release.tag_name.replace(/^v/, '')} · ${detected.name} · ${Math.round(asset.size / 1048576)} MB`;
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

configureDownload();
