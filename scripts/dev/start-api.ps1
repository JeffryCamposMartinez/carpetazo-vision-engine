# Inicia la API en http://127.0.0.1:8001 (documentación en /docs si VISION_ENVIRONMENT=development).
# Uso: .\scripts\dev\start-api.ps1 [-Port 8001]
param([int]$Port = 8001)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
if (-not (Test-Path (Join-Path $Script:Root ".env"))) { throw "Falta .env: ejecuta .\scripts\dev\setup.ps1" }
Invoke-Project @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $Port, "--reload")
