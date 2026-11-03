# Laboratorio PostgreSQL + pgvector aislado (copia privada de PostgreSQL, clúster propio, puerto 5498).
# No toca la instalación ni el servicio de PostgreSQL de Windows. No requiere administrador, Docker ni WSL.
# Uso: .\scripts\dev\pg-lab.ps1 -Action status|start|stop|test|remove
param([ValidateSet("status", "start", "stop", "test", "remove")][string]$Action = "status")
$ErrorActionPreference = "Stop"

$Lab = Join-Path $env:USERPROFILE "vision-pg-lab"
$Bin = Join-Path $Lab "pgroot\bin"
$Data = Join-Path $Lab "data"
$Port = 5498
$TestUrl = "postgresql+psycopg://vision@127.0.0.1:$Port/vision_pgvector_test"

function Test-Lab { Test-Path (Join-Path $Bin "pg_ctl.exe") }
function Test-Up { & (Join-Path $Bin "pg_isready.exe") -h 127.0.0.1 -p $Port *> $null; $LASTEXITCODE -eq 0 }
if (-not (Test-Lab) -and $Action -ne "remove") {
    throw "No existe el laboratorio en $Lab. Sigue docs\postgres-windows.md (sección 'Copia privada con pgvector')."
}

switch ($Action) {
    "status" {
        "Laboratorio: $Lab"
        "Clúster en 127.0.0.1:$Port : " + $(if (Test-Up) { "ACTIVO" } else { "detenido" })
        if (Test-Up) {
            & (Join-Path $Bin "psql.exe") -h 127.0.0.1 -p $Port -U vision -d postgres -tAc "select version()"
            & (Join-Path $Bin "psql.exe") -h 127.0.0.1 -p $Port -U vision -d postgres -tAc "select 'pgvector disponible: ' || default_version from pg_available_extensions where name = 'vector'"
        }
    }
    "start" {
        if (Test-Up) { "Ya está activo."; return }
        Start-Process -FilePath (Join-Path $Bin "pg_ctl.exe") -ArgumentList @("start", "-D", $Data, "-l", (Join-Path $Lab "log.txt")) -WindowStyle Hidden
        foreach ($i in 1..30) { Start-Sleep 1; if (Test-Up) { "Activo en 127.0.0.1:$Port"; return } }
        throw "No arrancó en 30 s. Revisa $Lab\log.txt"
    }
    "stop" {
        if (-not (Test-Up)) { "Ya está detenido."; return }
        & (Join-Path $Bin "pg_ctl.exe") stop -D $Data -m fast | Out-Null
        "Detenido."
    }
    "test" {
        if (-not (Test-Up)) { throw "Inicia el laboratorio primero: .\scripts\dev\pg-lab.ps1 -Action start" }
        $env:VISION_TEST_POSTGRES_URL = $TestUrl
        Remove-Item Env:VISION_TEST_ALLOW_VECTOR_STUB -ErrorAction SilentlyContinue   # nunca con relleno
        & (Join-Path $PSScriptRoot "test.ps1") -Postgres
    }
    "remove" {
        # Elimina SOLO esta carpeta (clúster, copia privada, código fuente y evidencias). No toca nada más.
        $full = [IO.Path]::GetFullPath($Lab)
        if ($full -ne [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "vision-pg-lab"))) { throw "Ruta inesperada: $full" }
        if ((Test-Path $Bin) -and (Test-Up)) { & (Join-Path $Bin "pg_ctl.exe") stop -D $Data -m fast | Out-Null }
        if ((Read-Host "Se borrará $full por completo. Escribe SI para continuar") -ne "SI") { throw "Cancelado." }
        Remove-Item -Recurse -Force $full
        "Eliminado: $full"
    }
}
