# 9. E4 · Índice de referencias

Implementado con datos sintéticos. **Hoy el índice real tiene 0 referencias**: no hay ninguna referencia verificada a mano ni se ha leído R2 (no autorizado). Nada de aquí se usa todavía en `/v1/scan`.

## Modelo de datos (migración `0005`, solo laboratorio)
| Tabla | Qué guarda | Regla |
|---|---|---|
| `verified_references` | Identidad VERIFICADA por una persona: nombre, set, número, idioma, acabado, patrón (lo leído en la carta física), `verified_by/at/note` obligatorios, ids de catálogo opcionales (FK; `null` hasta una asociación revisada), SHA-256 de la imagen, clave de R2 solo como trazabilidad, `license_status = unknown` | CHECK: verificador y nota no vacíos |
| `candidate_references` | Imágenes probables o ambiguas. **Sin columnas de identidad ni de verificación**; `claimed_label` es solo lo que dice el origen (no es verdad) | CHECK: estado `probable`/`ambiguous` |
| `verified_embeddings_<modelo>` / `candidate_embeddings_<modelo>` | Un vector por (referencia, normalización), en una tabla por modelo/versión con **dimensión fija** (`vector(768)` DINOv2-small, `vector(456)` grid) | Un vector de otra dimensión lo rechaza la propia base de datos |
| `reference_import_runs` | Quién importó, de qué origen y cuántas filas | |

Separación: no hay claves foráneas entre candidatas y verificadas, ni función que mueva una fila de un nivel a otro (hay una prueba que lo comprueba). Una candidata solo sirve de distractor; pasar a verificada exige registrarla como referencia verificada con su verificador. Una imagen ya verificada no se duplica como candidata.

## Importación controlada (`python -m app.recognition.index.cli`)
- `import-verified --database-url <lab> --dataset datasets/real-photos --by Nombre [--extractors grid dinov2] [--normalization raw|default] [--dry-run]`.
- Solo importa cartas del piloto con: carta física verificada, referencia verificada a mano, archivo local de la referencia ya existente con SHA-256 igual al registrado y sin ser de ejemplo. Cada carta omitida se informa con su motivo. `--dry-run` no escribe ni calcula embeddings. Idempotente.
- Las candidatas se importan con `import_candidates` desde archivos locales; se rechaza cualquier elemento con identidad o verificación.
- **Guardia:** exige `--database-url` explícito y solo acepta SQLite o PostgreSQL en `127.0.0.1:5498` (laboratorio). El PostgreSQL original o cualquier otro servidor se rechazan (código 2).
- Nada lee ni escribe R2; `references-fetch` (lectura de R2) sigue sin ejecutarse hasta que lo autorices.

## Exacta frente a pgvector (SINTÉTICO, laboratorio 5498, 28.000 vectores × 768, 200 consultas, k = 10)
| Búsqueda | Top-1 igual a NumPy | Recall@10 | Latencia p50 / p95 |
|---|---|---|---|
| NumPy exacta en memoria (86 MB) | referencia | referencia | 7,6 / 8,8 ms |
| pgvector exacta (`<=>`, recorrido secuencial) | 100 % | 100 % | 78 / 86 ms |
| pgvector HNSW `ef_search=40` (m=16, ef_construction=64) | 35,5 % | 30,8 % | 2,1 / 3,5 ms |
| pgvector HNSW `ef_search=100` | 59,5 % | 54,3 % | 3,2 / 4,5 ms |
Construcción HNSW 56 s, 115 MB. Informe: `evaluation/reports/e4-pgvector-vs-exact.json`.

**Lectura honesta:** los vectores son sintéticos de 768 dimensiones con mucho ruido, un caso difícil para HNSW; con embeddings reales el recall puede ser distinto y no se debe extrapolar. Aun así, para el volumen previsto (≤ 28.000) la **búsqueda exacta en memoria es exacta, más rápida que pgvector y no necesita extensión**. Decisión provisional: `reference_search_backend = exact` (por defecto); pgvector queda como persistencia/consulta opcional y HNSW **no se recomienda** salvo que crezca mucho el índice y se mida con vectores reales.
