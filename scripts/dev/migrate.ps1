# Migraciones Alembic contra VISION_DATABASE_URL (de .env). Muestra el destino sin contraseña y pide confirmación
# para downgrade o para servidores que no sean locales.
# Uso: .\scripts\dev\migrate.ps1 [-Action up|down|current|history] [-Revision head|-1|0003|base] [-AllowRemote]
param(
    [ValidateSet("up", "down", "current", "history")][string]$Action = "up",
    [string]$Revision = "",
    [switch]$AllowRemote
)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
$target = (& $Script:Python -c "from app.core.config import get_settings; from sqlalchemy.engine import make_url; print(make_url(get_settings().database_url).render_as_string(hide_password=True))" 2>&1)
if ($LASTEXITCODE -ne 0) { throw "No se pudo leer VISION_DATABASE_URL. ¿Existe .env?`n$target" }
Write-Host "Destino: $target"
$isLocal = $target -match "@(127\.0\.0\.1|localhost|\[::1\])[:/]"
if (-not $isLocal -and -not $AllowRemote) { throw "El destino no es local. Si es la base de DESARROLLO del VPS, repite con -AllowRemote." }

switch ($Action) {
    "up"      { Invoke-Project @("-m", "alembic", "upgrade", $(if ($Revision) { $Revision } else { "head" })) }
    "down"    {
        if (-not $Revision) { throw "Indica -Revision (por ejemplo -1, 0003 o base)." }
        if ((Read-Host "Revertir a '$Revision' en $target. Escribe SI para continuar") -ne "SI") { throw "Cancelado." }
        Invoke-Project @("-m", "alembic", "downgrade", $Revision)
    }
    "current" { Invoke-Project @("-m", "alembic", "current") }
    "history" { Invoke-Project @("-m", "alembic", "history", "--verbose") }
}
