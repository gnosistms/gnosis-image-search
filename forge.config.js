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
    name: 'Gnosis Images',
    executableName: 'Gnosis Images',
    appBundleId: 'org.gnosis.image-search',
    appCategoryType: 'public.app-category.reference',
    icon: 'assets/icon',
    asar: true,
    osxSign: {
      identity: signingIdentity || '-',
      hardenedRuntime: Boolean(signingIdentity),
      ignore: filePath => !isSignableCode(filePath),
      ...(signingIdentity ? {
        entitlements: 'assets/entitlements.mac.plist',
        'entitlements-inherit': 'assets/entitlements.mac.plist'
      } : {
        // Keep local packages internally valid without pretending they are a
        // notarizable Developer ID release.
        identityValidation: false
      })
    },
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
        format: 'ULFO',
        title: 'Gnosis Images Installer',
        icon: 'assets/icon.icns',
        background: 'assets/dmg-background.png',
        iconSize: 112,
        contents: options => [
          {
            x: 186,
            y: 300,
            type: 'file',
            path: options.appPath,
            name: 'Gnosis Images.app'
          },
          { x: 472, y: 300, type: 'link', path: '/Applications' }
        ],
        additionalDMGOptions: {
          'background-color': '#f7f2ed',
          window: {
            size: { width: 658, height: 498 }
          }
        }
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
