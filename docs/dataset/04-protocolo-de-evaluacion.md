# 4. Protocolo de evaluación sin fuga de datos

> **Regla de oro:** no se publica ni se cita ninguna métrica «real» hasta que existan fotografías reales verificadas. Hoy hay **0**. El código de evaluación está
> probado solo con datos sintéticos de prueba y se niega a calcular métricas sobre datos marcados como ficticios.

## 4.1 Tres conjuntos, tres funciones
| Conjunto | Qué es | Dónde está | Se usa para |
|---|---|---|---|
| **Referencias** | Imágenes **digitales** de las cartas (p. ej. del catálogo en R2), una por carta registrada, **aceptadas por una persona** tras compararlas con la carta física | `references/pc-XXX.*` (campo `reference_object_key` de la carta) | Construir la galería de búsqueda |
| **Consultas de calibración** | Fotos reales `verified` de las sesiones asignadas a *calibración* | `splits/<nombre>.json` | Elegir **solo** el umbral de rechazo (y el margen) |
| **Consultas de prueba** | Fotos reales `verified` de las sesiones asignadas a *prueba* | ídem | Medir. Se tocan una vez por configuración congelada |

## 4.2 Reglas contra la fuga (obligatorias)
1. **La unidad de partición es la sesión de captura.** Todas las fotos de una sesión van al mismo conjunto (`split` lo garantiza y `run_evaluation` lo vuelve a comprobar).
2. **Ninguna foto se deriva de otra.** Los cuasi-duplicados de la misma carta entre sesiones distintas se detectan al importar (`posible_duplicado_entre_sesiones`) y en `split`
   (`leaks.blocking`); `export` y la evaluación se niegan a continuar si hay una fuga bloqueante.
3. **Referencias y fotos son de dominios distintos y de orígenes distintos:** la referencia nunca sale de una foto de consulta, y las fotos no entran en la galería.
4. **Los umbrales se calibran con calibración y se evalúan en prueba.** Está prohibido ajustar umbrales, extractor, preprocesado o parámetros de detección mirando los resultados de prueba.
   Si cambia algo, se vuelve a congelar y se anota la versión.
5. **La verdad de referencia solo es lo verificado por una persona:** cartas `verified`, fotos `verified`, referencias aceptadas a mano. Las asociaciones pendientes de R2 (manifiesto, propuestas)
   **no** son verdad. Las imágenes de R2 que no sean referencias aceptadas solo pueden usarse como **distractores**, y su identidad no se usa.
6. **Cartas desconocidas:** se simulan retirando de la galería la referencia de algunas cartas verificadas (`--holdout-cards N`, elegidas por semilla). Sus fotos deben ser rechazadas.
7. **Sin entrenamiento ni ajuste fino.** Los extractores se usan congelados. Esta etapa solo mide.

## 4.3 Qué se mide, por separado
| # | Tarea | Definición | Observaciones |
|---|---|---|---|
| 1 | **Identidad de carta** | Acierto si la referencia de la carta correcta está entre los K primeros resultados: **Top-1, Top-5, Top-10**; **Recall@K** = fracción de las referencias de esa carta recuperadas en el top-K (con una referencia por carta coincide con Top-K) | Las fotos sin carta detectada cuentan como fallo |
| 2 | **Identidad de impresión** | Igual, pero la referencia debe tener el mismo `printing_id` que la foto | Solo cartas con `printing_id` verificado en foto **y** referencia. Si no hay ninguna: «sin datos» |
| 3 | **Identidad de variante** | Igual con `variant_id` (Holo, Reverse Holo, Poké Ball, Master Ball...) | Ídem. Se informa aparte de la carta |
| 4 | **Rechazo de desconocidas** | `unknown_falsely_accepted` (falsos positivos), `known_falsely_rejected`, `known_correct_and_accepted` al umbral calibrado | Necesita ≥ 40 fotos de cartas desconocidas para acotar el falso positivo (con 0/40 la cota superior del 95 % es ≈ 9 %) |
| 5 | **Detección y perspectiva** | `detected_rate`; con `card_corners` anotadas: error mediano de esquinas relativo al alto de la carta y % ≤ 5 % | Anotar esquinas en ≥ 50 fotos de prueba para que la cifra signifique algo |
| + | **Latencia** | p50 y p95 por etapa (detección; rectificar + embeber + buscar) | Memoria RAM/VRAM: medir con el procedimiento de `docs/recognition/06` |
| + | **Desglose** | Top-1 por iluminación, fondo, funda y ángulo, con intervalo de Wilson al 95 % | No se interpreta ninguna celda con n < 30 |

## 4.4 Informe y límites de afirmación
- Cada informe declara: partición, semilla, huella SHA-256 de `photos.json`, número de fotos/cartas/sesiones, cartas retiradas, distractores y extractores.
- **EXPLORATORIO** si hay < 150 fotos de prueba o < 20 cartas de prueba, o si los datos están marcados como ficticios. Un informe exploratorio **no se cita como precisión**.
- Se informan intervalos de Wilson. Las comparaciones entre extractores se hacen sobre **las mismas consultas** (pareadas).
- Se ejecuta primero sin distractores y luego con distractores (p. ej. 300 imágenes de R2 de otras cartas) para ver el efecto del tamaño de la galería.
  Las reimpresiones con el mismo arte pueden aparecer como distractores «correctos»; eso es un límite conocido y se informa.

## 4.5 Comparación experimental (misma tubería, mismas consultas)
| Extractor | Qué es | Entorno |
|---|---|---|
| `phash` | Hash perceptual DCT de 64 bits | proyecto |
| `grid` | Color por celdas + orientaciones de gradiente (456-d) | proyecto |
| `dinov2` | DINOv2-small congelado (`facebook/dinov2-small`, 768-d) | entorno ML aparte (`vision-ml-lab`) |

```powershell
.\scripts\dev\dataset.ps1 references-fetch                       # lee de R2 SOLO las referencias aceptadas a mano
.\scripts\dev\dataset.ps1 split --name piloto-1
.\scripts\dev\dataset.ps1 evaluate --split piloto-1 --extractor phash grid --holdout-cards 5
.\scripts\ml\eval-real-photos.ps1 -Split piloto-1 -HoldoutCards 5  # incluye dinov2 (GPU/CPU del entorno ML)
```
Opcional: guardar los embeddings de la galería en el PostgreSQL privado `vision-pg-lab` (pgvector) para comparar la búsqueda exacta y HNSW; no es necesario para el piloto.
