$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = $env:BACKEND_PYTHON
if (-not $PythonBin) {
  $PythonBin = Join-Path $ProjectDir '.backend-venv\Scripts\python.exe'
}

& $PythonBin (Join-Path $PSScriptRoot 'prepare-bundled-model.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:GNOSIS_TARGET_ARCH = 'x64'
$env:GNOSIS_DISTRIBUTION = 'full'
& npx electron-forge make --platform=win32 --arch=x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$FullMsi = Get-ChildItem (Join-Path $ProjectDir 'out\full\make\wix\x64\*.msi') | Select-Object -First 1
if (-not $FullMsi) { throw 'WiX did not produce the full Windows MSI.' }
$Version = (Get-Content (Join-Path $ProjectDir 'package.json') | ConvertFrom-Json).version
$FullMsiName = "Gnosis-Images-Full-Installer-$Version-x64.msi"
Move-Item -Force $FullMsi.FullName (Join-Path $FullMsi.DirectoryName $FullMsiName)
$env:GNOSIS_DISTRIBUTION = 'update'
& npx electron-forge make --platform=win32 --arch=x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
