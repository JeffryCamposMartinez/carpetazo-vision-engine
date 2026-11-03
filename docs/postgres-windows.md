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

### 3.2 Instalarla (compilando, según las instrucciones oficiales de pgvector para Windows)

Requisitos: **Visual Studio Build Tools** con la carga de trabajo «Desarrollo para el escritorio con C++» y Git.
Abre «x64 Native Tools Command Prompt for VS» **como administrador** y ejecuta (ajusta la ruta de PostgreSQL):

```bat
set "PGROOT=C:\Program Files\PostgreSQL\18"
cd %TEMP%
git clone --branch <ultima-version> https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

- Usa la **última etiqueta** de <https://github.com/pgvector/pgvector/releases> y confirma en sus notas que es compatible
  con tu versión de PostgreSQL (las versiones nuevas de PostgreSQL suelen requerir un pgvector reciente).
- Reinicia el servicio si la extensión no aparece: `Restart-Service postgresql-x64-18`.
- Activa la extensión en cada base, como superusuario: `CREATE EXTENSION vector;`
  (el script `create-dev-db.ps1` lo hace si la encuentra).

### 3.3 Si no puedes instalar pgvector en tu PC

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
- Si el servidor no tiene pgvector, también se omiten, salvo con `VISION_TEST_ALLOW_VECTOR_STUB=1` (modo parcial).

Qué cubren: migración desde vacío, restricciones únicas y CHECK, integridad referencial, índices, rollback (`downgrade`
a 0003 y a la base, y vuelta a `head`), rechazo de la 0004 con datos ambiguos y relleno de `catalog_language`, importador
idempotente y rollback por carta.
