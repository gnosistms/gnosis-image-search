$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = $env:BACKEND_PYTHON
if (-not $PythonBin) {
  $PythonBin = Join-Path $ProjectDir '.backend-venv\Scripts\python.exe'
}
if (-not (Test-Path $PythonBin)) {
  py -3.12 -m venv (Join-Path $ProjectDir '.backend-venv')
  & $PythonBin -m pip install --quiet -r (Join-Path $ProjectDir 'requirements-backend.txt')
}

& $PythonBin -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
  & $PythonBin -m pip install --quiet 'pyinstaller==6.22.2'
}

$OutputDir = Join-Path $ProjectDir 'build\backend'
$WorkDir = Join-Path $ProjectDir 'build\pyinstaller'
$SpecFile = Join-Path $ProjectDir 'build\gnosis-search-engine.spec'
if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
if (Test-Path $SpecFile) { Remove-Item -Force $SpecFile }
New-Item -ItemType Directory -Force $OutputDir | Out-Null

$PyInstallerArgs = @(
  '--noconfirm',
  '--clean',
  '--onedir',
  '--name', 'gnosis-search-engine',
  '--distpath', $OutputDir,
  '--workpath', $WorkDir,
  '--specpath', (Join-Path $ProjectDir 'build'),
  '--additional-hooks-dir', (Join-Path $ProjectDir 'hooks'),
  '--paths', (Join-Path $ProjectDir 'vendor'),
  '--hidden-import', 'transformers.models.siglip.configuration_siglip',
  '--hidden-import', 'transformers.models.siglip.image_processing_siglip',
  '--hidden-import', 'transformers.models.siglip.image_processing_pil_siglip',
  '--hidden-import', 'transformers.models.siglip.modeling_siglip',
  '--hidden-import', 'transformers.models.siglip.processing_siglip',
  '--hidden-import', 'transformers.models.siglip.tokenization_siglip',
  '--hidden-import', 'transformers.models.clip.configuration_clip',
  '--hidden-import', 'transformers.models.clip.image_processing_clip',
  '--hidden-import', 'transformers.models.clip.modeling_clip',
  '--hidden-import', 'transformers.models.clip.processing_clip',
  '--hidden-import', 'transformers.models.clip.tokenization_clip',
  '--hidden-import', 'cryptography.hazmat.primitives.ciphers.aead',
  '--hidden-import', 'keys',
  '--hidden-import', 'requests',
  '--exclude-module', 'cv2',
  '--exclude-module', 'datasets',
  '--exclude-module', 'llvmlite',
  '--exclude-module', 'matplotlib',
  '--exclude-module', 'numba',
  '--exclude-module', 'pandas',
  '--exclude-module', 'pyarrow',
  '--exclude-module', 'pytest',
  '--exclude-module', 'scipy',
  '--exclude-module', 'timm',
  '--exclude-module', 'tkinter',
  '--exclude-module', 'torchvision',
  '--add-data', "$(Join-Path $ProjectDir 'web');web",
  '--add-data', "$(Join-Path $ProjectDir 'data\beauty-tournament\axis-ranking-model-siglip2-base-patch16-256.npz');data/beauty-tournament",
  '--add-data', "$(Join-Path $ProjectDir 'data\pamela\siglip2-base-patch16-256.npz');data/pamela",
  '--add-data', "$(Join-Path $ProjectDir 'data\gnosis-media.json');data",
  '--add-data', "$(Join-Path $ProjectDir 'data\nga-search.db');data",
  '--add-data', "$(Join-Path $ProjectDir 'vendor\aic_hf_rows.json');.",
  (Join-Path $ProjectDir 'server.py')
)
& $PythonBin -m PyInstaller @PyInstallerArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$CaBundle = Join-Path $OutputDir 'gnosis-search-engine\_internal\certifi\cacert.pem'
if (-not (Test-Path $CaBundle -PathType Leaf)) {
  throw "Packaged backend is missing its Certifi CA bundle: $CaBundle"
}

& $PythonBin (Join-Path $ProjectDir 'scripts\package-europeana-key.py') (Join-Path $OutputDir 'gnosis-search-engine\credentials')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
