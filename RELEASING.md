# Release setup

Public macOS direct-download builds require Apple Developer ID signing and
notarization. A Mac App Store provisioning profile is not required. Windows
x64 installers are built in parallel and are currently unsigned.

1. Enroll the release owner in the Apple Developer Program.
2. In Xcode, open **Settings → Accounts**, add the Apple ID, select the team,
   choose **Manage Certificates**, and create a **Developer ID Application**
   certificate.
3. Create an App Store Connect API key with App Manager access, or reuse the
   API key already used by Gnosis TMS.
4. Export the certificate and private key from Keychain Access as a password-
   protected `.p12`, then base64-encode it. The existing Gnosis TMS certificate
   can be reused because it belongs to the same Apple developer team.
5. Add the same repository secrets used by Gnosis TMS:
   `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_API_ISSUER`,
   `APPLE_API_KEY`, and `APPLE_API_KEY_CONTENT`. GitHub does not expose existing
   repository-secret values, so they must be copied from their original secure
   storage into this repository. Unsigned macOS builds cannot use automatic
   updates.
6. Bump `package.json`, commit, and push a matching tag such as `v0.2.0`.
7. Inspect both release artifacts on a clean machine:
   `Gnosis-Images-Full-Installer-*` must contain and seed the SigLIP checkpoint,
   while `Gnosis-Images-Update-*` must omit it and preserve the seeded cache.
   Also confirm that a remote collection search works without setting
   `SSL_CERT_FILE`; the app must use the Certifi CA bundle included in the
   packaged backend.
8. Test the Windows x64 full `.zip` (extract it, then run `Gnosis Images.exe`)
   and model-free update `.zip` on a clean Windows system. Windows builds are
   currently unsigned and may show a Microsoft
   Defender SmartScreen warning until a Windows code-signing certificate is
   configured.

The app checks GitHub Releases at startup. It prompts before downloading the
explicitly named, model-free `Update` artifact and installs only after the user
accepts. The public download page selects the `Full-Installer` artifact, which
includes the default model and seeds it into the user-owned Application Support
model cache before the backend starts. Model weights and the local embedding
database remain untouched by application updates. Never rename an update to
look like a full installer (or vice versa): asset selection intentionally does
not fall back between them.
