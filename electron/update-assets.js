function compatibleUpdateAsset(release, platform = process.platform, arch = process.arch) {
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  const extensions = platform === 'darwin' ? ['.dmg'] : platform === 'win32' ? ['.exe'] : [];
  const architecture = arch === 'arm64' ? /arm64|aarch64/i : /x64|x86_64|amd64/i;
  const candidates = assets.filter(asset => {
    const name = String(asset?.name || '');
    const intendedPackage = platform === 'win32'
      ? /(?:^|[-_. ])installer(?:[-_. ]|$)/i.test(name)
      : /(?:^|[-_. ])update(?:[-_. ]|$)/i.test(name);
    return asset?.browser_download_url
      && intendedPackage
      && extensions.some(extension => name.toLowerCase().endsWith(extension));
  });
  // Never offer an installer for the other CPU architecture. Rosetta only
  // translates Intel apps on Apple Silicon, not Apple Silicon apps on Intel.
  return candidates.find(asset => architecture.test(asset.name)) || null;
}

function compatibleModelAsset(release) {
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  return assets.find(asset => asset?.browser_download_url
    && /^Gnosis-Images-Image-Ranking-Model-.*\.gnosis-model$/i.test(String(asset.name || ''))) || null;
}

module.exports = { compatibleModelAsset, compatibleUpdateAsset };
