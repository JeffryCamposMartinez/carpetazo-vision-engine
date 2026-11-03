# 2. Auditoría del catálogo de imágenes de R2 (solo lectura)

Bucket `carpetazo-imagenes`, prefijo `Carpetazo.cl/Pokemon/Pokemon_DB/`. Fuente de los números: `evaluation/reports/r2-inventory.json`
(listado completo, sin descargar) y una **muestra estratificada de 401 imágenes (35,5 MB, solo GET)** en
`datasets/lab-cache/r2-sample/` (ignorada por Git). Reproducible: `python -m app.recognition_lab.cli sample` / `audit`.
No se escribió ni borró nada en R2.

## Qué se midió
| Pregunta | Resultado |
|---|---|
| Imágenes de cartas utilizables | **27.608** (`Pokemon_Images/<set>/<carta>/<carta>.webp`). Otros 8.156 objetos son productos sellados y miniaturas (excluidos) |
| Formato | 401/401 WebP, RGB, **sin canal alfa** (sin esquinas transparentes) |
| Resolución | Mediana **717 × 1000 px**; mín. 200 × 275; máx. 1000 × 1000. **31 de 401 (7,7 %)** miden menos de 600 px de alto |
| Cartas enteras o recortes | 400/401 tienen proporción de carta (0,716 ± 0,03). La excepción (`Zoroark BREAK`) es una carta horizontal legítima, no un recorte. Sin evidencia de recortes en la muestra |
| Nitidez | Varianza del laplaciano: mediana 6.907; percentil 5 = 1.510 (sin imágenes desenfocadas relevantes) |
| Duplicados exactos (ETag, **todo** el inventario) | 109 grupos / 274 imágenes (≈1 %); 47 dentro de una carpeta y 62 entre carpetas; el mayor grupo tiene 12 |
| Causa de los duplicados | Etiquetas distintas con **el mismo archivo**: p. ej. `Luxio (Cosmos Holo)` y `Luxio (Cracked Ice Holo)` son la misma imagen; o la misma carta en `Black and White` y `Deck Exclusives` |
| Cuasi-duplicados entre cartas distintas (muestra) | 6 pares de 401: reimpresiones con **el mismo arte** (`Prize Pack Series Cards` frente al set original; `Beach Court (167-198)` en ambos) |
| Variantes visuales (Poké Ball / Master Ball frente a la base) | Distancia dHash mediana **5 bits de 64** (máx. 29): casi idénticas a simple vista |
| Idioma | **Desconocido**: los `data.json` proceden de tcgcsv/TCGplayer (inglés), pero no está verificado para las 27.608 |
| Asociaciones con TCGdex | **Ninguna aprobada.** El manifiesto tiene 5.126 `matched`, 6.104 `probable`, 452 `ambiguous` y 15.926 `unmatched`, todas pendientes de revisión: **no se usan como verdad** |

## Implicaciones
1. **Resolución suficiente** para embeddings de 224–518 px; las 31 imágenes pequeñas se pueden marcar y reescalar o excluir de la
   evaluación.
2. **«Una imagen = una impresión» es falso**: hay imágenes compartidas por etiquetas distintas y variantes que solo se diferencian por
   el nombre de carpeta. Las variantes de foil (Cosmos, Cracked Ice, patrones) **no se distinguen visualmente en la imagen del
   catálogo**; el reconocimiento por imagen devolverá la carta, y la variante saldrá de metadatos, de una foto con luz o de la
   confirmación del usuario.
3. **Las reimpresiones con arte idéntico** (Prize Pack) no se pueden separar por apariencia del arte; hace falta el número/símbolo
   del set (OCR o zona del borde) o devolver varias impresiones como candidatas.
4. **Licencia de las imágenes**: proceden de CDN de TCGplayer (campo `imageUrl` de los `data.json`). Antes de entrenar con ellas o de
   redistribuir vectores derivados en un producto comercial hay que revisar sus condiciones y las del IP de The Pokémon Company.
   Este informe **no** las ha verificado.
5. La muestra de 401 imágenes no representa los 27.608 por igual (está estratificada); sirve para auditoría, no para estimar
   proporciones exactas de todo el catálogo.
