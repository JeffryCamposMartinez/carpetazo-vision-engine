# 9. E4 · Índice de referencias (con los identificadores de Carpetazo)

Implementado con datos sintéticos. **Hoy el índice real tiene 0 referencias**: no hay ninguna referencia verificada a mano ni se ha leído R2. Nada de aquí se usa todavía en `/v1/scan`.
La fuente de verdad del catálogo es **Carpetazo.cl** (ver `docs/catalog-mirror.md`); el índice apunta a su espejo local por `(category_id, product_id)`.

## Tres niveles, en tablas separadas
| Nivel | Tabla | Qué es | Puede producir identidad confirmada |
|---|---|---|---|
| **Verificada** | `verified_references` | Imagen revisada manualmente y asociada a un `productId` de Carpetazo (`verified_by/at/note` obligatorios, FK RESTRICT al espejo) | **Sí (la única)** |
| **Candidata** | `candidate_references` | Imagen vinculada por Carpetazo (`claimed_category_id/claimed_product_id`) pero **no revisada** (`status = unreviewed`) | No: solo experimentos de búsqueda |
| **Ambigua** | `ambiguous_references` | Imagen o asociación con dudas (`ambiguity_reason` obligatorio; vínculo afirmado opcional y completo) | No |

No hay claves foráneas entre niveles ni función que mueva una fila de uno a otro (hay una prueba estructural): pasar a verificada exige registrarla de nuevo como verificada, con su verificador. Una imagen vive en un solo nivel (se rechaza duplicarla en otro).

## Embeddings: dimensión fija, por modelo, por nivel
`<nivel>_embeddings_<modelo>` con `vector(768)` (DINOv2-small, versión `ed25f3a-clsmean-224x308-p1`) y `vector(456)` (grid): 3 niveles × 2 modelos = 6 tablas. Un vector con otra dimensión lo rechaza la base de datos. Cada vector guarda la etiqueta de normalización (`raw` o `n1-<hash>`).
Los embeddings cuelgan de la referencia (no del espejo) y el espejo nunca borra: **una sincronización del catálogo no pierde referencias ni embeddings** (probado: se modifica y se retira el producto en Carpetazo y todo sigue ahí; el producto retirado queda marcado, no borrado, y no admite referencias nuevas).

## Variantes
`verified_references.variant` es siempre `unknown` y `variant_key` es `null`: en Carpetazo no existe una entidad de variante y no se inventa. `finish` y `pattern` son descriptores leídos en la carta física al verificar, no una variante identificable. Ver `docs/recognition/10-e6-resolucion-y-variantes.md`.

## Importación controlada (`python -m app.recognition.index.cli`)
- `import-verified --database-url <lab> --dataset datasets/real-photos --by Nombre [--extractors grid dinov2] [--normalization raw|default] [--dry-run]`.
- Solo importa cartas del piloto con: carta física verificada, referencia verificada a mano, **vínculo `categoria_id_carpetazo` + `product_id_carpetazo`**, archivo local con SHA-256 igual al registrado, producto presente y vigente en el espejo y número coincidente. Cada carta omitida se informa con su motivo. `--dry-run` no escribe ni calcula embeddings. Idempotente.
- Candidatas y ambiguas: `import_candidates` / `import_ambiguous` desde archivos locales, con el vínculo afirmado existente en el espejo; se rechaza cualquier elemento que traiga identidad o verificación.
- **Guardia:** exige `--database-url` explícito y solo acepta SQLite o PostgreSQL en `127.0.0.1:5498`. Nada lee ni escribe R2.

## Migraciones (solo laboratorio)
`0005` (índice inicial) → `0006` (espejo del catálogo) → `0007` (ids de Carpetazo, tercer nivel). `0007` exige que las tablas de `0005` estén vacías (nunca hubo datos reales) y se detiene sin tocar nada si no lo están.

## Exacta frente a pgvector (SINTÉTICO, laboratorio 5498, 28.000 vectores × 768, 200 consultas, k = 10)
| Búsqueda | Top-1 igual a NumPy | Recall@10 | Latencia p50 / p95 |
|---|---|---|---|
| NumPy exacta en memoria (86 MB) | referencia | referencia | 7,6 / 8,8 ms |
| pgvector exacta (`<=>`, recorrido secuencial) | 100 % | 100 % | 78 / 86 ms |
| pgvector HNSW `ef_search=40` (m=16, ef_construction=64) | 35,5 % | 30,8 % | 2,1 / 3,5 ms |
| pgvector HNSW `ef_search=100` | 59,5 % | 54,3 % | 3,2 / 4,5 ms |
Construcción HNSW 56 s, 115 MB. Informe: `evaluation/reports/e4-pgvector-vs-exact.json`.

**Lectura honesta:** vectores sintéticos de 768 dimensiones con mucho ruido (caso difícil para HNSW); con embeddings reales el recall puede ser otro. Para ≤ 28.000 vectores la **búsqueda exacta en memoria es exacta, más rápida que pgvector y no necesita extensión**: `reference_search_backend = exact` por defecto; pgvector es opcional y HNSW no se recomienda sin medirlo con vectores reales.
