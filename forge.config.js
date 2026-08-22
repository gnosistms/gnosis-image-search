const fs = require('node:fs');
const path = require('node:path');

const signingIdentity = process.env.APPLE_SIGNING_IDENTITY;
const notarizationEnabled = Boolean(
  process.env.APPLE_NOTARIZATION_KEY_PATH &&
  process.env.APPLE_API_KEY &&
  process.env.APPLE_API_ISSUER
);

const MACH_O_MAGICS = new Set([
  0xfeedface, 0xcefaedfe, 0xfeedfacf, 0xcffaedfe, 0xcafebabe, 0xbebafeca
]);

function isSignableCode(filePath) {
  if (['.app', '.framework'].includes(path.extname(filePath))) return true;
  try {
    if (!fs.statSync(filePath).isFile()) return false;
    const descriptor = fs.openSync(filePath, 'r');
    const bytes = Buffer.allocUnsafe(4);
    try {
      if (fs.readSync(descriptor, bytes, 0, 4, 0) !== 4) return false;
    } finally {
      fs.closeSync(descriptor);
    }
    return MACH_O_MAGICS.has(bytes.readUInt32BE(0));
  } catch {
    return false;
  }
}

module.exports = {
  packagerConfig: {
    name: 'Gnosis Image Search',
    executableName: 'Gnosis Image Search',
    appBundleId: 'org.gnosis.image-search',
    appCategoryType: 'public.app-category.reference',
    icon: 'assets/icon',
    asar: true,
    ...(signingIdentity ? {
      osxSign: {
        identity: signingIdentity,
        hardenedRuntime: true,
        entitlements: 'assets/entitlements.mac.plist',
        'entitlements-inherit': 'assets/entitlements.mac.plist',
        ignore: filePath => !isSignableCode(filePath)
      }
    } : {}),
    ...(notarizationEnabled ? {
      osxNotarize: {
        appleApiKey: process.env.APPLE_NOTARIZATION_KEY_PATH,
        appleApiKeyId: process.env.APPLE_API_KEY,
        appleApiIssuer: process.env.APPLE_API_ISSUER
      }
    } : {}),
    ignore: [
      /^\/\.packaging-python($|\/)/,
      /^\/assets\/icon\.iconset($|\/)/,
      /^\/build($|\/)/,
      /^\/data($|\/)/,
      /^\/deploy($|\/)/,
      /^\/docs($|\/)/,
      /^\/vendor($|\/)/,
      /^\/web($|\/)/
    ],
    extraResource: ['build/backend']
  },
  makers: [
    {
      name: '@electron-forge/maker-dmg',
      config: {
        format: 'ULFO'
      }
    },
    {
      name: '@electron-forge/maker-zip',
      platforms: ['darwin']
    }
  ],
  publishers: [
    {
      name: '@electron-forge/publisher-github',
      config: {
        repository: {
          owner: 'gnosistms',
          name: 'gnosis-image-search'
        },
        draft: true,
        prerelease: false
      }
    }
  ]
};
