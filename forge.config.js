const signingIdentity = process.env.APPLE_SIGNING_IDENTITY;
const notarizationEnabled = Boolean(
  process.env.APPLE_NOTARIZATION_KEY_PATH &&
  process.env.APPLE_API_KEY &&
  process.env.APPLE_API_ISSUER
);

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
        'entitlements-inherit': 'assets/entitlements.mac.plist'
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
