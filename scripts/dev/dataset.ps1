# Atajo para la herramienta local del dataset de fotografías reales (todo ocurre en datasets\real-photos, sin red).
# Uso: .\scripts\dev\dataset.ps1 <comando> [opciones]      p. ej.:  .\scripts\dev\dataset.ps1 validate
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
Invoke-Project (@("-m", "app.dataset_lab.cli") + $Rest)
