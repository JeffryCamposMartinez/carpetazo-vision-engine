# Carpetazo Vision Engine

Servicio independiente de reconocimiento de cartas TCG para Carpetazo.cl (futuro `scanner.carpetazo.cl`).
Primer juego: Pokémon TCG, catálogo multilingüe. Procesamiento en servidor.

## Estado (Fase 0)

| Componente | Estado |
|---|---|
| FastAPI, config, Docker, Alembic (migración 0001) | Implementado |
| Sesiones (token firmado), autenticación de servicio | Implementado |
| `POST /v1/scan`: validación de imagen, calidad, rate limit, idempotencia | Implementado |
| `GET /v1/catalog/*` | Implementado (solo lectura) |
| Detección, rectificación, embeddings, retrieval, OCR, validación | **Pendiente** |
| Importador de catálogo, almacenamiento de imágenes | **Pendiente** (Fase 1) |
| Cámara continua (React) | **Pendiente** (Fase 5) |

Mientras no exista reconocimiento, `/v1/scan` devuelve `unknown` (o `poor_quality`) con
`recognition_available: false`. No hay resultados simulados.

No verificado todavía: la migración contra PostgreSQL real y la imagen Docker (el daemon de Docker no estaba
activo al construir esta fase). Los tests usan SQLite en memoria.

## Desarrollo local

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check .
```

Con base de datos (solo local):

```bash
cp .env.example .env        # completar los valores CAMBIAR
docker compose up -d db
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --port 8001
```

Docs interactivas en `/docs` solo con `VISION_ENVIRONMENT=development`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `VISION_SECRET_KEY` | Firma de tokens de sesión (obligatoria) |
| `VISION_SERVICE_API_KEYS` | Claves del backend de Carpetazo, separadas por coma (obligatoria) |
| `VISION_DATABASE_URL` | PostgreSQL con pgvector |
| `VISION_ENVIRONMENT` | `development` o `production` |
| `VISION_MAX_IMAGE_BYTES`, `..._MIN/MAX_IMAGE_SIDE`, `..._MAX_IMAGE_PIXELS` | Límites de imagen |
| `VISION_SCAN_RATE_LIMIT_PER_MINUTE`, `VISION_MAX_CONCURRENT_SCANS` | Rate limit por sesión y concurrencia |
| `VISION_MIN_SHARPNESS`, `VISION_MIN/MAX_BRIGHTNESS` | Umbrales de calidad, **provisionales** |

## API

- `GET /health`, `GET /v1/version`
- `POST /v1/sessions` — cabecera `X-Service-Key` (la usa el backend de Carpetazo, nunca el navegador).
  Devuelve `session_id`, `token`, `expires_at`.
- `POST /v1/scan` — `Authorization: Bearer <token>`, multipart `file` (+ `session_id` opcional),
  cabecera opcional `Idempotency-Key`. Errores: 401, 403, 409 (escaneo en curso), 413, 415, 422, 429, 503.
- `GET /v1/scan/{scan_id}` — solo la sesión dueña; los resultados viven 5 min en memoria.
- `GET /v1/catalog/search|sets|cards/{id}|printings/{id}` — `X-Service-Key` o token de sesión.

Respuesta de scan: `status` ∈ `confirmed|probable|ambiguous|unknown|poor_quality|not_a_card`;
`quality` trae valores medidos (`sharpness` = varianza del laplaciano, `lighting` = brillo medio);
`perspective` es `null` hasta que exista detector.

## Decisiones

- **Sesiones sin estado**: token HMAC con expiración; no hay tabla de sesiones. Sin revocación individual.
- **Estado de escaneo en memoria**, un solo worker. Para varios procesos habría que usar Redis.
- **`card_embeddings.embedding` sin dimensión**: se fija cuando el benchmark elija el modelo; entonces se
  crea el índice HNSW en una migración nueva.
- **Tablas solo por Alembic**; la app no crea tablas al iniciar.
- **Imágenes de referencia**: `license_status` en `catalog_sources` debe ser `permitted` para descargarlas.
- **Docker Compose es solo para desarrollo**; en Coolify se despliega el `Dockerfile` como recurso
  `Carpetazo Scanner` con su propia base de datos.

## Seguridad (lo que existe y lo que falta)

Existe: autenticación de servicio, tokens con expiración, validación de formato por firma real, límites de
tamaño/píxeles, rate limit, idempotencia, logs sin imágenes, usuario no root en Docker.
Falta: HTTPS/TLS (lo da Coolify), rate limit por IP, eliminación de temporales cuando haya almacenamiento,
política de retención, revisión de seguridad completa. No está listo para producción.

## Despliegue en Coolify (pendiente de ejecutar)

Nuevo recurso `Carpetazo Scanner` desde este repo (Dockerfile), dominio `scanner.carpetazo.cl`, health check
`/health`, puerto 8001, base PostgreSQL propia (imagen `pgvector/pgvector`) sin puerto público, límite de
memoria ~1 GB. Migrar con `alembic upgrade head` antes de iniciar. Rollback: redeploy de la imagen anterior;
las migraciones se revierten con `alembic downgrade -1`. Backup: `pg_dump` programado de la base del scanner.

## Próximas fases

1. Catálogo: comparar Pokémon TCG API y TCGdex (licencias, idiomas, límites) e implementar importador.
2. Evaluación: protocolo y herramientas para registrar fotos reales (`evaluation/`).
3. Detector, embeddings (benchmark DINOv2/SigLIP/otros), retrieval, OCR, calibración.
