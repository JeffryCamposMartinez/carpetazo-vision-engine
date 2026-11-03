# 10. E6 (preparación) · Resolución de identidad y variantes

Implementado con datos sintéticos en `app/recognition/resolve.py`. **No está conectado a `/v1/scan`** (E8) y no decide aceptación ni confianza (E7).

## Qué hace
Convierte coincidencias del índice (`Match` con su nivel) en identidades del catálogo de Carpetazo, solo lectura:
| Entrada | Resultado | ¿Confirmable? |
|---|---|---|
| Referencia **verificada** con producto vigente en el espejo | `resolved`: `category_id`, `product_id`, nombre, número, rareza, edición (`group_id`, nombre, sigla), idioma/relación del catálogo | **Sí (única vía)** |
| Verificada con producto retirado por Carpetazo | `incomplete` (razón: retirado) | No |
| Verificada cuyo producto no está en el espejo | `incomplete` (no se inventa nada) | No |
| **Candidata** (vínculo de Carpetazo sin revisar) | `unverified_candidate`, con lo que *afirma* Carpetazo | **Nunca**, sea cual sea la similitud |
| **Ambigua** | `ambiguous` + motivo | **Nunca** |
| Sin coincidencias | `unknown` | No |

## Identificadores y entidades
- **Impresión del catálogo = `productId`** (con su `categoryId`). **Edición = `groupId`**. No se confunden con la variante.
- En la categoría 100 (español) la misma `productId` inglesa puede aparecer como *vista en español* (`catalog_relation = spanish_view_of_english_product`) o como carta *solo español* (`spanish_only`); la relación se deriva del `categoryId` de origen que entrega la API (no se adivina).

## Variantes: cómo se contemplan sin inventarlas
Hoy `variant = "unknown"` siempre y `variant_evidence = None`. `reference_hints` (acabado y patrón anotados al verificar la carta física de la referencia) se devuelve solo como pista etiquetada; una coincidencia visual no prueba la variante de la foto consultada (los experimentos mostraron que ningún extractor las distingue de forma fiable).

**Cómo se podría incorporar una entidad de variante en el futuro (sin tocar todavía el backend de Carpetazo):**
1. **En Carpetazo (cuando se decida):** una tabla de variantes por producto, p. ej. `CatalogVariant(variantId, productId → CatalogCard, kind, source, verifiedBy/At)`, con `kind` en un vocabulario por juego: `normal`, `holo`, `reverse_holo`, `full_art`, `pokeball`, `masterball`, y las propias de otros TCG. La variante es un atributo de la impresión (`productId`), no una edición.
2. **En el espejo:** una tabla `mirror_variants (category_id, product_id, variant_key)` con las mismas reglas (ids originales, sin borrados, digest, retirada). El endpoint de exportación (opción B) la entregaría.
3. **En el índice:** `verified_references.variant_key` (ya existe, `null`) pasaría a apuntar a `mirror_variants`; `variant` dejaría de ser `unknown` solo si hay una referencia verificada **de esa variante** y una evidencia validada.
4. **Al consultar:** determinar la variante de una foto exige un clasificador propio evaluado con fotos reales (Holo y Reverse Holo cambian con el ángulo/luz; Poké Ball y Master Ball casi no se distinguen en la imagen digital). Mientras no exista, se devuelve `variant: unknown` y, a lo sumo, la lista de variantes del producto como candidatas.
5. Nada de esto crea identificadores falsos: sin entidad real en Carpetazo, no hay `variant_key`.

## Qué falta (E5/E7/E8)
Generar el embedding de la consulta y buscar (E5), decidir aceptar/rechazar con umbrales calibrados con fotos reales (E7) y exponerlo en `/v1/scan` con el contrato actual (E8). El resolutor ya devuelve lo que esas etapas necesitarán.
