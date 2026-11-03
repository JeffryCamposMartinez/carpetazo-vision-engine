# Crea el usuario y las bases EXCLUSIVAS de desarrollo y de pruebas en un PostgreSQL local, e intenta activar pgvector.
# Pide por consola la contraseña del superusuario y la del usuario nuevo; no guarda ni imprime contraseñas.
# Uso: .\scripts\dev\create-dev-db.ps1 [-AdminUser postgres] [-Port 5432] [-DbUser vision_dev] [-DbName vision_dev]
param(
    [string]$AdminUser = "postgres",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5432,
    [string]$DbUser = "vision_dev",
    [string]$DbName = "vision_dev"
)
. "$PSScriptRoot\_common.ps1"
if ($HostName -notin @("127.0.0.1", "localhost")) { throw "Este script solo crea bases en el servidor local." }
foreach ($n in @($DbUser, $DbName)) { if ($n -notmatch '^[a-z][a-z0-9_]{2,40}$') { throw "Nombre no válido: $n" } }
$bin = Find-PostgresBin
if (-not $bin) { throw "No se encontró psql. Instala PostgreSQL (docs\postgres-windows.md)." }
$psql = Join-Path $bin "psql.exe"

function Read-Secret([string]$prompt) {
    $s = Read-Host $prompt -AsSecureString
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
$adminPw = Read-Secret "Contraseña del superusuario '$AdminUser'"
$userPw = Read-Secret "Contraseña NUEVA para '$DbUser' (se usará en VISION_DATABASE_URL)"
if ($userPw -match "['\\]") { throw "Evita comillas simples y barras invertidas en la contraseña." }

$env:PGPASSWORD = $adminPw
function Run-Sql([string]$db, [string]$sql) { & $psql -h $HostName -p $Port -U $AdminUser -d $db -v ON_ERROR_STOP=1 -tAc $sql }
try {
    if (-not (Run-Sql "postgres" "SELECT 1 FROM pg_roles WHERE rolname = '$DbUser'")) {
        Run-Sql "postgres" "CREATE ROLE $DbUser LOGIN PASSWORD '$userPw'" | Out-Null
        Write-Host "Usuario $DbUser creado."
    } else { Write-Host "El usuario $DbUser ya existe (no se cambió su contraseña)." }
    foreach ($db in @($DbName, "$($DbName -replace '_dev$','')_test")) {
        if (-not (Run-Sql "postgres" "SELECT 1 FROM pg_database WHERE datname = '$db'")) {
            Run-Sql "postgres" "CREATE DATABASE $db OWNER $DbUser" | Out-Null
            Write-Host "Base $db creada."
        } else { Write-Host "La base $db ya existe." }
    }
    $avail = Run-Sql $DbName "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
    if ($avail) {
        Run-Sql $DbName "CREATE EXTENSION IF NOT EXISTS vector" | Out-Null
        Run-Sql "$($DbName -replace '_dev$','')_test" "CREATE EXTENSION IF NOT EXISTS vector" | Out-Null
        Write-Host "Extensión vector activada en ambas bases."
    } else {
        Write-Warning "pgvector NO está instalado en este servidor: las migraciones fallarán hasta instalarlo (docs\postgres-windows.md)."
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "`nEn .env usa:  VISION_DATABASE_URL=postgresql+psycopg://${DbUser}:<tu contraseña>@127.0.0.1:${Port}/${DbName}"
