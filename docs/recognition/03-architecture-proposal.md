# 3. Propuesta de arquitectura para Carpetazo

## Principios
1. **Recuperación por embeddings**, no clasificación: agregar cartas = calcular su vector e insertarlo (sin reentrenar el extractor).
2. **Identidad de carta primero, impresión después.** El sistema devuelve siempre una carta (con alternativas) aunque no pueda
   decidir la impresión o la variante. La variante tiene su propia evaluación y su propio estado.
3. **Rechazo explícito**: `unknown` cuando la similitud es baja o hay otra *carta* igual de cerca (las variantes de la misma carta no
   penalizan el margen; ver `RefIndex.decide`).
4. **Tres estados de resultado**: `verified` (asociación aprobada por una persona), `probable` (propuesta del sistema),
   `unknown`. Nada de lo pendiente de revisión se usa como verdad de entrenamiento o evaluación.

## Pipeline

```
foto (teléfono) → calidad (nitidez/luz/movimiento) → detección de UNA carta → rectificación (homografía 440×614)
   → [orientación 0°/180°] → extractor de embeddings (congelado) → búsqueda top-K (pgvector)
   → decisión: known / unknown → candidatos de carta (+ alternativas)
   → (opcional) verificación por OCR del número/código de set → impresión → variante (metadatos / confirmación)
```

| Etapa | Ahora (prototipo) | Siguiente paso | Nota |
|---|---|---|---|
| Detección + homografía | Contornos clásicos (`rectify.py`) | Detector orientado entrenado, **con licencia comercial clara** (no copiar GPL/NC; evitar AGPL de Ultralytics sin licencia) | Es el cuello de botella en escenas difíciles (ver benchmark) |
| Extractor | `phash`, `grid` (líneas base) | **DINOv2-small (384-d)** y **SigLIP-base (768-d)** sin entrenar; si no alcanzan, ajuste fino ligero (ArcFace/proxy) con hard negatives | Probar con fotos reales antes de decidir |
| Índice | `RefIndex` en memoria | `card_embeddings` en PostgreSQL con **HNSW sobre `(embedding::vector(N))`** (probado en pgvector 0.8.7) | HNSW admite ≤ 2.000 dimensiones |
| Rechazo | puntuación + margen entre cartas, calibrado con validación | Calibración por juego/modelo con fotos reales | Los umbrales **no** son transferibles entre extractores |
| Impresión/variante | No resuelto | Metadatos (TCGdex `variants_detailed`, `foil`, sellos) + OCR de número/set + confirmación del usuario | La imagen del catálogo no distingue Cosmos/Cracked Ice ni Poké Ball/Master Ball |

## Encaje con el catálogo
- Un vector por **imagen de referencia aprobada** (`card_embeddings.printing_id`, y `variant_id` cuando la imagen sea de una variante),
  con `model_name`/`model_version`: se pueden conservar varios modelos a la vez y comparar sin migrar.
- Las referencias salen de `image_associations` (aprobadas), no de `image_mapping_proposals`.
- Un cambio de modelo = recalcular vectores (proceso por lotes reproducible); el catálogo no cambia.

## Recomendación de modelo (provisional, hasta tener fotos reales)
1. **Extractor inicial: DINOv2-small** (Apache-2.0, 22,1 M parámetros, ≈90 MB, 384-d): menor coste en CPU y memoria, y el candidato
   natural para recuperación de instancias sin entrenar. Comparar con **DINOv2-base** y **SigLIP-base** en el mismo benchmark.
2. **Ajuste fino solo si** el modelo congelado queda por debajo del objetivo en la evaluación real, y con ArcFace/proxy-loss más
   *hard negatives* (mismo Pokémon, otro set; arte reimpreso). No entrenar a ciegas.
3. **Detección aprendida** antes que mejorar embeddings: en el benchmark sintético los fallos de la escena difícil vienen de la
   detección/rectificación, no del vector.

## Cobertura de la petición «preentrenado frente a ajustado»
El prototipo y el benchmark admiten cualquier extractor. **Hoy solo se midieron las líneas base clásicas.** Ni DINOv2 ni SigLIP se
ejecutaron (requieren `torch`/`transformers` y descargar pesos, pendiente de tu autorización) y no hay ajuste fino todavía. El
protocolo para compararlos está en `05-benchmark.md`.
