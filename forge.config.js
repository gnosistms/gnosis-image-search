const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const signingIdentity = process.env.APPLE_SIGNING_IDENTITY;
const notarizationEnabled = Boolean(
  process.env.APPLE_NOTARIZATION_KEY_PATH &&
  process.env.APPLE_API_KEY &&
  process.env.APPLE_API_ISSUER
);
const distribution = process.env.GNOSIS_DISTRIBUTION || '';
if (distribution && !['full', 'update'].includes(distribution)) {
  throw new Error(`Unsupported GNOSIS_DISTRIBUTION: ${distribution}`);
}
const packageVersion = require('./package.json').version;
const targetArch = process.env.GNOSIS_TARGET_ARCH || (process.platform === 'darwin' ? 'arm64' : 'x64');
const distributionLabel = distribution === 'full' ? 'Full-Installer' : 'Update';

function prepareApplicationsAlias() {
  const output = path.resolve('build/dmg-assets/Applications');
  execFileSync('swift', [
    path.resolve('scripts/generate-applications-alias.swift'),
    path.resolve('assets/applications-folder.png'),
    output
  ], { stdio: 'inherit' });
  return output;
}

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
  ...(distribution ? { buildIdentifier: distribution } : {}),
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
      /[\\/]\.packaging-python(?:[\\/]|$)/,
      /[\\/]assets[\\/]icon\.iconset(?:[\\/]|$)/,
      /[\\/]build(?:[\\/]|$)/,
      /[\\/]data(?:[\\/]|$)/,
      /[\\/]deploy(?:[\\/]|$)/,
      /[\\/]docs(?:[\\/]|$)/,
      /[\\/]out(?:[\\/]|$)/,
      /[\\/]vendor(?:[\\/]|$)/,
      /[\\/]web(?:[\\/]|$)/
    ],
    // Keep the packaged resource prefix short because Squirrel/NuGet still
    // applies Windows' legacy MAX_PATH limit to deeply nested PyTorch files.
    extraResource: ['build/backend/b']
  },
  makers: [
    {
      name: '@electron-forge/maker-dmg',
      config: {
        name: `Gnosis-Images-${distributionLabel}-${packageVersion}-${targetArch}`,
        format: 'ULFO',
        title: distribution === 'full' ? 'Gnosis Images Installer' : 'Gnosis Images Update',
        icon: 'assets/icon.icns',
        background: 'assets/dmg-background.png',
        iconSize: 112,
        contents: options => {
          const applicationsAlias = prepareApplicationsAlias();
          return [{
            x: 186,
            y: 300,
            type: 'file',
            path: options.appPath,
            name: 'Gnosis Images.app'
          }, {
            x: 472,
            y: 300,
            type: 'file',
            path: applicationsAlias,
            name: 'Applications'
          }];
        },
        additionalDMGOptions: {
          'background-color': '#f7f2ed',
          window: {
            size: { width: 658, height: 498 }
          }
        }
      }
    },
    ...(distribution === 'full' ? [{
      name: '@electron-forge/maker-squirrel',
      platforms: ['win32'],
      config: {
        name: 'GnosisImages',
        authors: 'Hans',
        description: 'Museum image search ranked by the PAMELA criterion model',
        setupExe: `Gnosis-Images-Full-Installer-${packageVersion}-${targetArch}.exe`,
        setupIcon: path.resolve('assets/icon.ico'),
        noMsi: true
      }
    }] : []),
    {
      name: '@electron-forge/maker-zip',
      platforms: distribution === 'update' ? ['win32'] : ['darwin']
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
