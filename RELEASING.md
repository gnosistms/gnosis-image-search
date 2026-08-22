# macOS release setup

Public direct-download builds require Apple Developer ID signing and
notarization. A Mac App Store provisioning profile is not required.

1. Enroll the release owner in the Apple Developer Program.
2. In Xcode, open **Settings → Accounts**, add the Apple ID, select the team,
   choose **Manage Certificates**, and create a **Developer ID Application**
   certificate.
3. Create an app-specific password at appleid.apple.com.
4. Export the certificate and private key from Keychain Access as a password-
   protected `.p12`, then base64-encode it.
5. Add repository secrets named `APPLE_CERTIFICATE_P12`,
   `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and
   `APPLE_TEAM_ID`. Unsigned macOS builds cannot use automatic updates.
6. Bump `package.json`, commit, and push a matching tag such as `v0.2.0`.
7. Inspect the draft GitHub Release, download and test its DMG, then publish it.

The app checks GitHub Releases at startup. It prompts before downloading an
update, and installs only after the user accepts. Model weights and the local
embedding database live under user-owned cache/application-support directories
and remain untouched by application updates.
