# Evaluación con fotografías reales incluyendo DINOv2-small congelado (entorno ML aparte; usa GPU si está disponible).
# Requiere: dataset con fotos verificadas, una partición creada (dataset.ps1 split) y las referencias traídas (references-fetch).
# Uso: .\scripts\ml\eval-real-photos.ps1 -Split piloto-1 [-HoldoutCards 5] [-Distractors 0]
param([Parameter(Mandatory = $true)][string]$Split, [int]$HoldoutCards = 0, [int]$Distractors = 0, [switch]$AllowFictitious)
$ErrorActionPreference = "Stop"
$Lab = Join-Path $env:USERPROFILE "vision-ml-lab"
$Py = Join-Path $Lab "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Falta el entorno ML. Ejecuta primero: .\scripts\ml\setup-ml-env.ps1" }
$env:HF_HOME = Join-Path $Lab "hf-home"   # pesos ya descargados: no se descarga nada nuevo
$env:HF_HUB_OFFLINE = "1"
$env:PYTHONIOENCODING = "utf-8"
Push-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
try {
    $args_ = @("-m", "app.dataset_lab.cli", "evaluate", "--split", $Split, "--extractor", "phash", "grid", "dinov2",
               "--holdout-cards", $HoldoutCards, "--distractors", $Distractors)
    if ($AllowFictitious) { $args_ += "--allow-fictitious" }
    & $Py @args_
} finally {
    Pop-Location
}
