# PostgreSQL nativo y pgvector en Windows

Nada de esto requiere Docker, WSL2 ni virtualización.

## 1. Instalar PostgreSQL

1. Descarga el instalador de EDB desde <https://www.postgresql.org/download/windows/> (cualquier versión soportada;
   este proyecto se probó con **18.6**).
2. Instala el servidor y las herramientas de línea de comandos. Anota la contraseña del superusuario `postgres` y el puerto
   (por defecto 5432). Si ya tienes PostgreSQL instalado para otro proyecto, **reutiliza el servidor pero no sus bases**:
   crea una base exclusiva (paso 2).
3. Comprueba que está activo: `Get-Service *postgres*` (estado `Running`).

## 2. Base y usuario exclusivos de desarrollo

Con el script (pide las contraseñas por consola, no las guarda):

```powershell
.\scripts\dev\create-dev-db.ps1            # crea el usuario vision_dev y las bases vision_dev y vision_test
```

Equivalente manual con `psql` (como superusuario):

```sql
CREATE ROLE vision_dev LOGIN PASSWORD '<elige una>';
CREATE DATABASE vision_dev  OWNER vision_dev;
CREATE DATABASE vision_test OWNER vision_dev;   -- solo para pruebas: se borra su esquema en cada prueba
```

## 3. pgvector

El proyecto **necesita** la extensión `vector` (tabla `card_embeddings`). El instalador de EDB **no** la incluye.

### 3.1 Comprobar si está

```powershell
.\scripts\dev\verify-postgres.ps1
```

Muestra si existe `share\extension\vector.control` en la instalación y, con conexión, si el servidor la ofrece
(`pg_available_extensions`). En el PC donde se desarrolló este proyecto **no estaba instalada** (PostgreSQL 18.6).

### 3.2 Recomendado y validado: copia privada con pgvector (sin administrador, sin tocar tu PostgreSQL)

Probado en este equipo: PostgreSQL 18.6 (EDB) + **pgvector 0.8.7** compilado con Visual Studio 2022 (MSVC 14.39) sobre una copia
de `bin`, `lib`, `share` e `include`. La instalación original, su servicio y sus datos no se modifican (se comparó una
instantánea SHA-256 de 3.141 archivos antes y después: idéntica). Todo vive en `C:\Users\<tú>\vision-pg-lab`:

| Ruta | Contenido |
|---|---|
| `vision-pg-lab\src\pgvector` | Clon oficial de `https://github.com/pgvector/pgvector.git`, etiqueta `v0.8.7` (commit `f37c13f68b57d2c3472b2214fbcff699d6d34876`, verificado) |
| `vision-pg-lab\pgroot` | Copia privada de PostgreSQL 18.6 (≈160 MB) con pgvector instalado dentro |
| `vision-pg-lab\data` | Clúster propio (`127.0.0.1:5498`, usuario `vision`, autenticación `trust` solo en loopback) |
| `vision-pg-lab\_evidence` | Instantáneas antes/después de la instalación original |

Pasos (PowerShell, **sin** administrador; ajusta las rutas si tu PostgreSQL no es la 18):

```powershell
$lab = "$env:USERPROFILE\vision-pg-lab"; New-Item -ItemType Directory -Force "$lab\src" | Out-Null
git clone --branch v0.8.7 --depth 1 https://github.com/pgvector/pgvector.git "$lab\src\pgvector"
git -C "$lab\src\pgvector" rev-parse HEAD        # debe ser f37c13f68b57d2c3472b2214fbcff699d6d34876

# Copia (solo lectura del original)
foreach ($d in "bin","lib","share","include") { robocopy "C:\Program Files\PostgreSQL\18\$d" "$lab\pgroot\$d" /E /NFL /NDL /NJH /NJS /NP | Out-Null }

# Compilar e instalar DENTRO de la copia (vswhere debe estar en el PATH del proceso para vcvars64.bat)
$vc = "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"   # o BuildTools
$vsw = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer"
cmd /c "set `"PATH=%PATH%;$vsw`" && call `"$vc`" && set `"PGROOT=$lab\pgroot`" && cd /d `"$lab\src\pgvector`" && nmake /F Makefile.win && nmake /F Makefile.win install"

# Clúster propio
& "$lab\pgroot\bin\initdb.exe" -D "$lab\data" -U vision -A trust -E UTF8 --locale=C
Add-Content "$lab\data\postgresql.conf" "`nport = 5498`nlisten_addresses = '127.0.0.1'`nmax_connections = 20`nshared_buffers = 128MB"
.\scripts\dev\pg-lab.ps1 -Action start
& "$lab\pgroot\bin\psql.exe" -h 127.0.0.1 -p 5498 -U vision -d postgres -c "create database vision_pgvector_test"
```

Día a día:

```powershell
.\scripts\dev\pg-lab.ps1 -Action start     # | stop | status
.\scripts\dev\pg-lab.ps1 -Action test      # suite de PostgreSQL con pgvector REAL (sin relleno)
.\scripts\dev\pg-lab.ps1 -Action remove    # borra por completo vision-pg-lab (pide confirmación)
```

Para usarlo como base de desarrollo, crea en el laboratorio una base propia (`create database vision_dev;`) y apunta
`VISION_DATABASE_URL=postgresql+psycopg://vision@127.0.0.1:5498/vision_dev`. Las migraciones crean la extensión solas.

Limitaciones: el clúster no es un servicio de Windows (arráncalo con `pg-lab.ps1`), hay que repetir la copia y la compilación
si actualizas PostgreSQL, y es un entorno de desarrollo: no uses `trust` fuera de loopback.

### 3.2b Alternativa: instalar en tu PostgreSQL (requiere administrador)

Con un terminal elevado: `set "PGROOT=C:\Program Files\PostgreSQL\18"` y los mismos `nmake /F Makefile.win` +
`nmake /F Makefile.win install`. Modifica la instalación original (reversible con `nmake /F Makefile.win uninstall`); no hace
falta reiniciar el servicio. Compila desde un «x64 Native Tools Command Prompt» como administrador.

### 3.3 Si no puedes compilar pgvector en tu PC

- Sigue desarrollando: las pruebas unitarias (SQLite) y toda la API funcionan sin PostgreSQL.
- Las migraciones y las pruebas que dependen de pgvector se ejecutan en el VPS: ver `docs/hostinger-integration.md`.
- Las pruebas de integración pueden correr en modo parcial con `VISION_TEST_ALLOW_VECTOR_STUB=1`
  (valida lo relacional; **no** valida pgvector ni la migración 0001 tal cual).

## 4. Configurar `.env`

```powershell
.\scripts\dev\setup.ps1      # crea .env desde .env.example si no existe
```

Edita `.env`:

```
VISION_DATABASE_URL=postgresql+psycopg://vision_dev:<contraseña>@127.0.0.1:5432/vision_dev
```

## 5. Migraciones

```powershell
.\scripts\dev\migrate.ps1                       # alembic upgrade head
.\scripts\dev\migrate.ps1 -Action current       # versión aplicada
.\scripts\dev\migrate.ps1 -Action down -Revision -1     # revierte una (pide confirmación)
.\scripts\dev\migrate.ps1 -Action down -Revision 0003   # vuelve a una revisión concreta
```

Las migraciones **no se editan una vez aplicadas**: se agrega una nueva. La 0004 no destruye datos y se niega a correr si
hay dos impresiones de la misma carta en el mismo set (pide fusionarlas a mano).

## 6. Verificar

```powershell
.\scripts\dev\verify-postgres.ps1 -ExpectMigrated
```

Comprueba: conexión, versión, extensión `vector` (disponible y activada), versión de Alembic, las 15 tablas y los índices
clave. Sale con código 1 y explica qué falta si algo no está.

## 7. Pruebas de integración con PostgreSQL

```powershell
$env:VISION_TEST_POSTGRES_URL = "postgresql+psycopg://vision_dev:<contraseña>@127.0.0.1:5432/vision_test"
.\scripts\dev\test.ps1 -Postgres
```

- La base **debe llamarse `*_test`**: cada prueba hace `DROP SCHEMA public CASCADE`.
- Sin `VISION_TEST_POSTGRES_URL` esas pruebas se omiten (y se ve en el resumen de pytest).
- Si el servidor no tiene pgvector, también se omiten, salvo con `VISION_TEST_ALLOW_VECTOR_STUB=1` (modo parcial, solo relacional).
- Con el laboratorio (§3.2): `.\scripts\dev\pg-lab.ps1 -Action test` ejecuta las 14 pruebas con pgvector real.

Qué cubren: migración desde vacío, restricciones únicas y CHECK, integridad referencial, índices, rollback (`downgrade`
a 0003 y a la base, y vuelta a `head`), rechazo de la 0004 con datos ambiguos y relleno de `catalog_language`, importador
idempotente y rollback por carta.
