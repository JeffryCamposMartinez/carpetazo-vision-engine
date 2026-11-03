# Funciones comunes de los scripts de desarrollo (se cargan con: . "$PSScriptRoot\_common.ps1").
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Script:Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script:Python = Join-Path $Script:Root ".venv\Scripts\python.exe"

function Assert-Venv {
    if (-not (Test-Path $Script:Python)) {
        throw "No existe el entorno virtual. Ejecuta primero: .\scripts\dev\setup.ps1"
    }
}

function Invoke-Project([string[]]$Arguments) {
    # Ejecuta python del venv desde la raíz del proyecto y propaga el código de salida.
    Push-Location $Script:Root
    try {
        & $Script:Python @Arguments
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

function Find-PostgresBin {
    # Busca psql en PATH y en las carpetas habituales de EDB (la versión más alta primero).
    $cmd = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($cmd) { return Split-Path $cmd.Source }
    $dirs = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '\D', '0') } -Descending
    foreach ($d in $dirs) {
        if (Test-Path (Join-Path $d.FullName "bin\psql.exe")) { return (Join-Path $d.FullName "bin") }
    }
    return $null
}
