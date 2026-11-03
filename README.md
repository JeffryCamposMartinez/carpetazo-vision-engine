# Carpetazo Vision Engine

Servicio independiente de reconocimiento de cartas TCG para Carpetazo.cl (futuro `scanner.carpetazo.cl`).
Primer juego: Pokémon TCG. Procesamiento en servidor. Se desarrolla **directamente en Windows**: no hace falta Docker,
WSL2 ni virtualización.

## Estado

| Componente | Estado |
|---|---|
| API (FastAPI): sesiones, `POST /v1/scan` con validación y calidad de imagen, catálogo de solo lectura | Implementado |
| Modelo de catálogo (juego, set, carta, impresión, variantes, referencias externas) + migraciones 0001–0004 | Implementado |
| Importador TCGdex (idempotente, reanudable, sin imágenes) | Implementado; probado con TCGdex real en 2 sets |
| Inventario/matching/auditoría de imágenes de R2 (solo lectura) | Implementado |
| Propuestas de asociación R2 → carta (separadas de las aprobadas) | Implementado |
| Laboratorio de reconocimiento (`app/recognition_lab`): detección clásica, líneas base, índice, benchmark **sintético** | Experimental, aislado de la API |
| Sistema de captura y anotación de fotografías reales (`app/dataset_lab`, 100 % local; `docs/dataset/`) | Implementado; **sin fotos reales todavía** |
| Detección aprendida, embeddings profundos (DINOv2/SigLIP), OCR, cámara continua | **Pendiente** |

Mientras no exista reconocimiento, `/v1/scan` devuelve `unknown` o `poor_quality` con `recognition_available: false`.

## Requisitos

- Windows con **Python 3.12+** (python.org).
- **PostgreSQL nativo** con la extensión **pgvector** para migraciones e integración: `docs/postgres-windows.md` (§3.2: copia privada con pgvector 0.8.7, validada, `scripts\dev\pg-lab.ps1`).
  Sin PostgreSQL puedes desarrollar y ejecutar las pruebas unitarias (SQLite).

## Inicio rápido (PowerShell, desde la raíz del proyecto)

```powershell
.\scripts\dev\setup.ps1              # venv + dependencias + .env (desde .env.example)
notepad .env                          # secretos y VISION_DATABASE_URL
.\scripts\dev\create-dev-db.ps1       # (una vez) usuario y bases de desarrollo en tu PostgreSQL local
.\scripts\dev\verify-postgres.ps1     # ¿hay PostgreSQL, pgvector y migraciones?
.\scripts\dev\migrate.ps1             # alembic upgrade head
.\scripts\dev\start-api.ps1           # http://127.0.0.1:8001  (docs en /docs)
.\scripts\dev\test.ps1                # pytest
.\scripts\dev\lint.ps1                # ruff (-Fix para corregir)
```

Si PowerShell bloquea los scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

| Script | Qué hace |
|---|---|
| `setup.ps1` | Crea `.venv`, instala `requirements-dev.txt`, crea `.env` si falta |
| `start-api.ps1 [-Port]` | Uvicorn con recarga en `127.0.0.1` |
| `test.ps1 [-Postgres]` | Pruebas; `-Postgres` ejecuta solo las de PostgreSQL real |
| `lint.ps1 [-Fix]` | Ruff (lint + formato) |
| `migrate.ps1 [-Action up\|down\|current\|history] [-Revision]` | Alembic; muestra el destino, confirma los `down` y rechaza servidores no locales sin `-AllowRemote` |
| `verify-postgres.ps1 [-ExpectMigrated]` | Diagnóstico de PostgreSQL, pgvector, migraciones, tablas e índices (solo lectura) |
| `create-dev-db.ps1` | Crea usuario y bases exclusivas de desarrollo/pruebas |
| `dataset.ps1 <comando>` | Herramienta local del dataset de fotos reales (import, review, validate, split, export, backup, evaluate; piloto de 30 cartas: pilot-import-cards, pilot-import-sessions, pilot-gap, pilot-status) |

## Pruebas

- `.\scripts\dev\test.ps1`: pruebas unitarias y de API (SQLite en memoria). Las de PostgreSQL se **omiten** si no defines
  `VISION_TEST_POSTGRES_URL`.
- SQLite **no** valida migraciones de PostgreSQL ni pgvector. Para eso: `docs/postgres-windows.md` (§7) o el VPS
  (`docs/hostinger-integration.md`).

## CLI

```powershell
python -m app.cli catalog-import --language en --set base1 --dry-run   # importador TCGdex (docs/catalog-import-runbook.md)
python -m app.cli images-inventory | images-manifest | images-alias-audit | images-sets-review   # R2, solo lectura
python -m app.cli images-load-proposals                                # propuestas de imágenes (no asocia nada)
python -m app.cli storage-diagnose                                     # lista el bucket (solo lectura)
```

`images-apply` / `images-revert` existen pero solo deben usarse con alias confirmados por una persona.

## API

- `GET /health`, `GET /v1/version`
- `POST /v1/sessions` (cabecera `X-Service-Key`, la usa el backend de Carpetazo) → `session_id`, `token`, `expires_at`
- `POST /v1/scan` (`Authorization: Bearer <token>`, multipart `file`, `Idempotency-Key` opcional). Errores: 401, 403, 409, 413, 415, 422, 429, 503
- `GET /v1/scan/{scan_id}` (solo la sesión dueña; vive 5 min en memoria)
- `GET /v1/catalog/search | sets | cards/{id} | printings/{id}` (`X-Service-Key` o token de sesión)

## Variables de entorno

Ver `.env.example` (con comentarios). Las obligatorias: `VISION_SECRET_KEY`, `VISION_SERVICE_API_KEYS`,
`VISION_DATABASE_URL`. Las credenciales de R2 y de cualquier servicio son siempre de **desarrollo** y salen del entorno.

## Documentación

| Archivo | Contenido |
|---|---|
| `docs/postgres-windows.md` | Instalar PostgreSQL y pgvector en Windows, base de desarrollo, migraciones, verificación |
| `docs/hostinger-integration.md` | Guía (aún no ejecutada) para probar con pgvector real en el VPS sin tocar producción |
| `docs/catalog-model.md` | Modelo del catálogo, variantes, idiomas, identidad de cartas, decisiones pendientes |
| `docs/catalog-import-runbook.md` | Importador TCGdex: comandos, garantías y recuperación |
| `docs/recognition/` | Investigación de reconocimiento visual: comparativa de repositorios y licencias, auditoría de R2, arquitectura, diseño de dataset, benchmark sintético y experimento con DINOv2-small (`06-…`; entorno ML aparte: `scripts\ml\setup-ml-env.ps1`) |
| `docs/dataset/` | Dataset de fotografías reales: protocolo de captura, esquema, herramienta, protocolo de evaluación sin fuga y cómo repetir el experimento |
| `evaluation/set_aliases.schema.json` | Esquema de alias de sets y de asociaciones por objeto |

## Docker (opcional)

`Dockerfile` y `docker-compose.yml` se conservan solo como referencia para despliegues futuros (p. ej. Coolify). **Nada del
desarrollo local los necesita.** Despliegue en producción: aún no realizado.

## Seguridad

Existe: autenticación de servicio, tokens con expiración, validación de formato por firma real, límites de tamaño y píxeles,
rate limit, idempotencia, usuario no root en la imagen opcional, scripts que rechazan destinos no locales sin confirmación.
Falta: TLS (lo daría Coolify), rate limit por IP, política de retención, revisión de seguridad completa.
**No está listo para producción.**
