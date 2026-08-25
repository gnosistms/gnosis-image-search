$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$env:GNOSIS_TARGET_ARCH = 'x64'
$env:GNOSIS_DISTRIBUTION = 'full'

Push-Location $ProjectDir
try {
  & npx electron-forge package --platform=win32 --arch=x64
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $PackagedApp = Join-Path $ProjectDir 'out\full\Gnosis Images-win32-x64'
  if (-not (Test-Path (Join-Path $PackagedApp 'Gnosis Images.exe'))) {
    throw "Forge did not produce the packaged Windows application at $PackagedApp."
  }

  & npx electron-builder --win nsis --x64 --prepackaged $PackagedApp
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $Version = (Get-Content (Join-Path $ProjectDir 'package.json') | ConvertFrom-Json).version
  $ExpectedName = "Gnosis-Images-Installer-$Version-x64.exe"
  $Installer = Join-Path $ProjectDir "out\full\make\$ExpectedName"
  if (-not (Test-Path $Installer)) {
    throw "electron-builder did not produce the expected NSIS installer: $ExpectedName"
  }
}
finally {
  Pop-Location
}
