# Ejecuta las pruebas. Las de PostgreSQL se omiten salvo que VISION_TEST_POSTGRES_URL esté definida.
# Uso: .\scripts\dev\test.ps1 [-Postgres] [argumentos de pytest...]
#   -Postgres  exige VISION_TEST_POSTGRES_URL y ejecuta solo las pruebas marcadas `postgres`.
param([switch]$Postgres, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
$args_ = @("-m", "pytest", "-q")
if ($Postgres) {
    if (-not $env:VISION_TEST_POSTGRES_URL) {
        throw "Define VISION_TEST_POSTGRES_URL con una base *_test exclusiva (ver docs\postgres-windows.md)."
    }
    $args_ += @("-m", "postgres", "-rs")
}
Invoke-Project ($args_ + $Rest)
