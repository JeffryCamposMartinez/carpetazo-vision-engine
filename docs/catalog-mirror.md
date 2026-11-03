# Espejo local del catálogo de Carpetazo.cl

**Carpetazo.cl es la fuente de verdad** de juegos, ediciones, cartas e identificadores. Vision Engine no usa TCGdex ni accede a la base de datos de producción: lee la **API pública** existente (opción A) y guarda un espejo local de solo lectura. Alcance inicial: **Pokémon, categorías 3 (inglés) y 100 (español)**.

## Fuente: endpoints usados (únicos permitidos por el cliente)
| Endpoint (GET, públicos, sin autenticación) | Para qué |
|---|---|
| `/api/tcg/catalog/sets?categoryId=N` | Lista de ediciones (`groupId`, nombre, sigla, `publishedOn`, `modifiedOn`) |
| `/api/tcg/catalog/{categoryId}/{groupId}/cards` | Cartas de una edición (una petición por edición, sin paginación interna) |
Base por defecto: `https://api.carpetazo.cl/api` (la que usa el frontend). El cliente rechaza cualquier otra ruta, no sigue redirecciones, no descarga imágenes (ni de R2 ni de ningún host) y no escribe nada en Carpetazo.

## Correspondencia de identificadores
| Carpetazo | Espejo local | Notas |
|---|---|---|
| `categoryId` (juego) | `mirror_games.category_id` | 3 = Pokémon inglés, 100 = Pokémon español |
| `groupId` (edición) | `mirror_sets.(category_id, group_id)` | En la categoría 100 las ediciones inglesas conservan su `groupId`; las solo-español usan ids virtuales ≥ 9.000.000 |
| `productId` (impresión del catálogo) | `mirror_cards.(category_id, product_id)` | **Identidad de la impresión** (decisión: no hay entidad “impresión” aparte) |
| `categoryId` de la fila de la respuesta | `mirror_cards.source_category_id` | En la categoría 100 la API devuelve **productos ingleses (categoryId 3) vistos en español** y **cartas solo español (categoryId 100)**; de ahí `relation`: `catalog_product`, `spanish_view_of_english_product`, `spanish_only` (derivado, no inventado) |
| `extendedData` | `mirror_cards.extended_data` (tal cual) | Si algún día incluye `TcgplayerProductId`, se copia a `tcgplayer_product_id`; hoy **la API pública no lo expone** |
| `imageUrl` | `image_url`, `image_origin`, `image_key` | Solo trazabilidad: `carpetazo_r2`, `tcgplayer_cdn`, `tcgdex` (respaldo que entrega Carpetazo, no es fuente de identidad), `placeholder`, `other`, `none`. **Nunca se descarga** |

**Limitación importante:** el vínculo interno `attributes.TcgplayerProductId` (fila española ↔ producto inglés) vive en la base de Carpetazo y la API pública solo lo refleja de forma indirecta (producto inglés con imagen española). Si hace falta el vínculo explícito, será el argumento para la opción B (endpoint de exportación), que no se crea todavía.

## Cómo sincroniza
- **Paginada por edición:** 1 petición de ediciones por categoría + 1 por edición que haga falta.
- **Limitador:** pausa mínima de 1 s entre peticiones (≈ 900 por ventana de 15 min frente a 2000 del backend), uso de las cabeceras `RateLimit-*` si existen, `Retry-After` en 429 y reintentos con espera creciente.
- **Incremental:** una edición se vuelve a descargar solo si cambió su `modifiedOn`, nunca se descargó o su última descarga supera `--max-age-days` (7 por defecto); `--force` la fuerza.
- **Digest:** SHA-256 del contenido semántico de cada edición y carta; solo lo que cambió se actualiza y queda en `mirror_changes` con antes/después.
- **Idempotente:** repetir la sincronización no cambia nada (solo `last_seen_at`).
- **Reanudable:** cada edición se confirma en su propia transacción y se guarda en `cursor`; `--resume` continúa la última corrida incompleta sin repetir lo hecho. Límites `--max-requests` y `--max-minutes` cortan limpiamente (`interrupted`).
- **Sin borrados:** lo que desaparece de una descarga completa y no vacía se marca `retired_at`; si reaparece se limpia. Una lista vacía nunca retira nada.
- **Registro de cada corrida** (`mirror_sync_runs`): fecha, duración, solicitudes, reintentos, pausas, resultados por entidad y errores.
- **Seguridad:** solo SQLite o el laboratorio privado `127.0.0.1:5498`; sin valor por defecto de base de datos.

## Uso
```powershell
# 1) Dry-run: pide SOLO las 2 listas de ediciones y estima el resto; no escribe nada
.\.venv\Scripts\python.exe -m app.catalog_mirror.cli sync --dry-run --database-url postgresql+psycopg://vision@127.0.0.1:5498/vision_catalog_lab
# 2) Sincronizar (solo tras revisar el dry-run)
.\.venv\Scripts\python.exe -m app.catalog_mirror.cli sync --database-url postgresql+psycopg://vision@127.0.0.1:5498/vision_catalog_lab
# 3) Reanudar una corrida interrumpida
.\.venv\Scripts\python.exe -m app.catalog_mirror.cli sync --resume --database-url ...
.\.venv\Scripts\python.exe -m app.catalog_mirror.cli status --database-url ...
```

## Esquema (migración `0006`)
`mirror_sync_runs`, `mirror_games`, `mirror_sets` (PK `category_id, group_id`), `mirror_cards` (PK `category_id, product_id`; FK RESTRICT a juego y edición), `mirror_changes`. Ninguna clave foránea hace cascada: el espejo nunca borra por arrastre.
