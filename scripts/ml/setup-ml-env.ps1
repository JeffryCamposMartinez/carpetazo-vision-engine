# Entorno ML INDEPENDIENTE (no toca el Python global ni el .venv del proyecto) para el experimento DINOv2-small.
# Ubicación: %USERPROFILE%\vision-ml-lab\venv  (pesos en %USERPROFILE%\vision-ml-lab\hf-home)
# Uso: .\scripts\ml\setup-ml-env.ps1            instala (si falta)      |   .\scripts\ml\setup-ml-env.ps1 -Run   ejecuta el experimento
param([switch]$Run)
$ErrorActionPreference = "Stop"
$Lab = Join-Path $env:USERPROFILE "vision-ml-lab"
$Py = Join-Path $Lab "venv\Scripts\python.exe"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:HF_HOME = Join-Path $Lab "hf-home"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Py)) {
    New-Item -ItemType Directory -Force $Lab | Out-Null
    python -m venv (Join-Path $Lab "venv")
}
if (-not (& $Py -c "import torch" 2>$null; $?)) {
    # CUDA 12.6: la matriz oficial de PyTorch 2.14 la compila desde Maxwell (sm_50), válida para la GTX 960 (sm_52).
    & $Py -m pip install "torch==2.14.1" --index-url https://download.pytorch.org/whl/cu126
    & $Py -m pip install transformers numpy pillow opencv-python-headless psutil "psycopg[binary]" pgvector
}
& $Py -m pip freeze | Set-Content (Join-Path $Lab "requirements-ml.freeze.txt")

if ($Run) {
    # Pesos: solo facebook/dinov2-small (se descargan la primera vez en HF_HOME).
    & $Py (Join-Path $Root "scripts\ml\dinov2_experiment.py")
}
