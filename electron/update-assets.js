function compatibleUpdateAsset(release, platform = process.platform, arch = process.arch) {
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  const extensions = platform === 'darwin' ? ['.dmg'] : platform === 'win32' ? ['.zip'] : [];
  const architecture = arch === 'arm64' ? /arm64|aarch64/i : /x64|x86_64|amd64/i;
  const candidates = assets.filter(asset => {
    const name = String(asset?.name || '');
    return asset?.browser_download_url
      && /(?:^|[-_. ])update(?:[-_. ]|$)/i.test(name)
      && extensions.some(extension => name.toLowerCase().endsWith(extension));
  });
  return candidates.find(asset => architecture.test(asset.name)) || candidates[0] || null;
}

function compatibleModelAsset(release) {
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  return assets.find(asset => asset?.browser_download_url
    && /^Gnosis-Images-Image-Ranking-Model-.*\.gnosis-model$/i.test(String(asset.name || ''))) || null;
}

module.exports = { compatibleModelAsset, compatibleUpdateAsset };
