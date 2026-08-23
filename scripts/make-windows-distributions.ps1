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
$FullZip = Get-ChildItem (Join-Path $ProjectDir 'out\full\make\zip\win32\x64\*.zip') | Select-Object -First 1
if (-not $FullZip) { throw 'Forge did not produce the full Windows ZIP.' }
$Version = (Get-Content (Join-Path $ProjectDir 'package.json') | ConvertFrom-Json).version
$FullZipName = "Gnosis-Images-Full-Installer-$Version-x64.zip"
Move-Item -Force $FullZip.FullName (Join-Path $FullZip.DirectoryName $FullZipName)
$env:GNOSIS_DISTRIBUTION = 'update'
& npx electron-forge make --platform=win32 --arch=x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
