# Prepara el entorno: crea .venv (Python 3.12+), instala dependencias y crea .env si no existe.
# Uso: .\scripts\dev\setup.ps1
. "$PSScriptRoot\_common.ps1"
Push-Location $Script:Root
try {
    if (-not (Test-Path $Script:Python)) {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if (-not $py) { throw "Python no está en el PATH. Instala Python 3.12 o superior desde python.org." }
        $v = [version](& python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
        if ($v -lt [version]"3.12") { throw "Se necesita Python 3.12 o superior (encontrado $v)." }
        Write-Host "Creando entorno virtual con Python $v ..."
        & python -m venv .venv
    }
    Write-Host "Instalando dependencias ..."
    & $Script:Python -m pip install --quiet --upgrade pip
    & $Script:Python -m pip install --quiet -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de dependencias." }

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Se creó .env desde .env.example: edítalo (secretos y VISION_DATABASE_URL) antes de iniciar la API."
    }
    Write-Host "Listo. Siguientes pasos: .\scripts\dev\verify-postgres.ps1  y  .\scripts\dev\test.ps1"
} finally {
    Pop-Location
}
