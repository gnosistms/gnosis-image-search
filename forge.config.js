const signingEnabled = Boolean(process.env.APPLE_TEAM_ID);

module.exports = {
  packagerConfig: {
    name: 'Gnosis Image Search',
    executableName: 'Gnosis Image Search',
    appBundleId: 'org.gnosis.image-search',
    appCategoryType: 'public.app-category.reference',
    icon: 'assets/icon',
    asar: true,
    ...(signingEnabled ? {
      osxSign: {
        identity: 'Developer ID Application',
        hardenedRuntime: true,
        entitlements: 'assets/entitlements.mac.plist',
        'entitlements-inherit': 'assets/entitlements.mac.plist'
      },
      osxNotarize: {
        appleId: process.env.APPLE_ID,
        appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
        teamId: process.env.APPLE_TEAM_ID
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
