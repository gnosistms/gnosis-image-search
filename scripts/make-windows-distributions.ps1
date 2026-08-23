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
$env:GNOSIS_DISTRIBUTION = 'update'
& npx electron-forge make --platform=win32 --arch=x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
