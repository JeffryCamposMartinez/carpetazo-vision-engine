# Importador TCGdex: ejecución y recuperación

El importador solo lee de la API pública de TCGdex y escribe en `VISION_DATABASE_URL`. **No descarga imágenes** y nunca
borra filas. No se invoca al arrancar la API.

## Comandos

```powershell
# 1. Ensayo: hace todo dentro de transacciones y las revierte (no escribe nada)
python -m app.cli catalog-import --language en --set base1 --set sv10.5b --dry-run

# 2. Importación limitada (pruebas): 12 cartas por set
python -m app.cli catalog-import --language en --set base1 --set sv10.5b --max-cards 12

# 3. Catálogo completo en inglés (≈ 24.000 peticiones; planifica 1–2 horas; haz antes un pg_dump)
python -m app.cli catalog-import --language en

# 4. Reanudar una importación interrumpida (mismos parámetros)
python -m app.cli catalog-import --language en --resume
```

Otros idiomas: `--language fr --language es` (repetible). `en` se procesa primero, así el nombre canónico de cada carta
queda en inglés. Cada idioma tiene su propio conjunto de sets y cartas.

Salida: un JSON por línea en `stderr` (eventos `import_start`, `set_done`, `import_error`, `import_end`) y los reportes
`evaluation/reports/tcgdex-import-report.json` y `.md` (entidades creadas/actualizadas/iguales/omitidas, errores,
duplicados, referencias externas, campos ignorados y desconocidos).

## Variables

| Variable | Para qué |
|---|---|
| `VISION_DATABASE_URL` | Base de desarrollo (obligatoria) |
| `VISION_TCGDEX_BASE_URL` | Por defecto `https://api.tcgdex.net/v2` |
| `VISION_PROVIDER_MIN_INTERVAL_SECONDS` | Pausa mínima entre peticiones (0.1 s por defecto) |
| `VISION_PROVIDER_TIMEOUT_SECONDS`, `VISION_PROVIDER_MAX_RETRIES` | Timeout (15 s) y reintentos (3) ante 429/5xx/red |
| `VISION_PROVIDER_CONTACT` | Se añade al User-Agent |

## Garantías

- **Idempotente:** repetir el comando no duplica nada (se comprobó contra TCGdex real y PostgreSQL nativo: segunda
  ejecución = todo `unchanged`, 0 referencias nuevas).
- **Transacciones:** una por set (los datos y la marca `done` se confirman juntos); cada carta va en un SAVEPOINT, así que
  una carta que falla no arrastra al resto del set. Un fallo de persistencia del set revierte ese set y sigue con los demás.
- **Errores por entidad:** tabla `import_errors` (etapa `fetch`, `validate` o `persist`) y reporte. Un set con errores de
  carta queda `failed` en `import_run_items` y `--resume` lo reintenta.
- **Campos desconocidos:** se guardan en `metadata.unmapped` y se cuentan; `pricing` se ignora a propósito y se cuenta.
- **Lo que el proveedor deje de enviar no se borra**: se informa en `variants_not_in_provider`.

## Recuperación

| Situación | Qué hacer |
|---|---|
| Ctrl+C / corte de red | El estado queda `interrupted`; repite el comando con `--resume`. |
| Errores de carta (`completed_with_errors`) | Revisa `import_errors` o el reporte; corrige la causa y usa `--resume` (solo repite los sets `failed`). |
| Datos incorrectos | Restaura el respaldo: `pg_restore -c -d vision_dev vision_dev.dump`. No hay borrado automático. |
| Cambió el esquema | `alembic upgrade head` antes de importar. |

Consultas útiles:

```sql
SELECT status, started_at, finished_at FROM import_runs ORDER BY started_at DESC LIMIT 5;
SELECT set_external_id, language, status, error FROM import_run_items WHERE status <> 'done';
SELECT entity_type, external_id, stage, message FROM import_errors ORDER BY created_at DESC LIMIT 20;
```

## Propuestas de imágenes de R2 (separadas de las asociaciones)

```powershell
python -m app.cli images-load-proposals     # lee r2-manifest.json y r2-object-mapping-proposals.json
python scripts\dev\inspect_catalog.py       # conteos por resultado
```

Escribe solo `image_mapping_proposals`. **No** ejecuta `images-apply` ni toca R2.
