# Ruff: lint y comprobación de formato. Con -Fix aplica correcciones y formatea.
# Uso: .\scripts\dev\lint.ps1 [-Fix]
param([switch]$Fix)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
if ($Fix) {
    Invoke-Project @("-m", "ruff", "check", ".", "--fix")
    Invoke-Project @("-m", "ruff", "format", ".")
} else {
    Invoke-Project @("-m", "ruff", "check", ".")
    Invoke-Project @("-m", "ruff", "format", "--check", ".")
}
