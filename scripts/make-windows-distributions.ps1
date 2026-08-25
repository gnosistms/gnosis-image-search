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
$Version = (Get-Content (Join-Path $ProjectDir 'package.json') | ConvertFrom-Json).version
$FullInstaller = Get-ChildItem (Join-Path $ProjectDir 'out\full\make\squirrel.windows\x64\*.exe') | Select-Object -First 1
if (-not $FullInstaller) { throw 'Forge did not produce the full Windows Setup.exe installer.' }
$ExpectedInstallerName = "Gnosis-Images-Full-Installer-$Version-x64.exe"
if ($FullInstaller.Name -ne $ExpectedInstallerName) {
  throw "Unexpected Windows installer name: $($FullInstaller.Name)"
}
$env:GNOSIS_DISTRIBUTION = 'update'
& npx electron-forge make --platform=win32 --arch=x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$UpdateZip = Get-ChildItem (Join-Path $ProjectDir 'out\update\make\zip\win32\x64\*.zip') | Select-Object -First 1
if (-not $UpdateZip) { throw 'Forge did not produce the Windows update ZIP.' }
$UpdateZipName = "Gnosis-Images-Update-$Version-x64.zip"
Move-Item -Force $UpdateZip.FullName (Join-Path $UpdateZip.DirectoryName $UpdateZipName)
