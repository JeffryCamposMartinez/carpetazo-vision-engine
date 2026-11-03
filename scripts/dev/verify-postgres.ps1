# Verifica PostgreSQL nativo, pgvector, migraciones, tablas e índices (solo lectura).
# Uso: .\scripts\dev\verify-postgres.ps1 [-ExpectMigrated]
param([switch]$ExpectMigrated)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
$bin = Find-PostgresBin
if ($bin) {
    Write-Host "Binarios de PostgreSQL: $bin"
    $ext = Join-Path (Split-Path $bin) "share\extension\vector.control"
    Write-Host ("pgvector instalado en esa instalación: " + $(if (Test-Path $ext) { "SÍ ($ext)" } else { "NO (ver docs\postgres-windows.md, sección pgvector)" }))
} else {
    Write-Host "psql no está en el PATH ni en C:\Program Files\PostgreSQL (puede ser una instalación en otra ruta)."
}
$flags = if ($ExpectMigrated) { @("--expect-head") } else { @() }
Invoke-Project (@("scripts\dev\pg_check.py") + $flags)
