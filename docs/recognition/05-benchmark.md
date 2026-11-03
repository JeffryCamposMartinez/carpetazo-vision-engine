# 5. Benchmark inicial (SINTÉTICO) y protocolo para el real

> **Esto NO es la precisión del sistema con fotos reales.** Las escenas se simulan a partir de las mismas imágenes de referencia que están
> en el índice (cota superior). No hay fotografías de teléfono todavía; mientras no existan, no se debe afirmar ninguna precisión.
> Sirve para: verificar el pipeline, comparar etapas y detectar cuellos de botella.

## Configuración (reproducible)
- Muestra: 401 imágenes de R2 en 6 estratos (`common` 100, `full_art_secret` 60, `old_sets` 60, `promos` 60, `pattern_variants` 61,
  `same_name_reprints` 60); semilla 7. Identidad = carpeta de set + número + nombre **según R2** (no las asociaciones pendientes).
- Partición por carta: **292 cartas (326 imágenes) en el índice** y **73 cartas «desconocidas»** (sin referencia), semilla 11.
- 200 consultas conocidas + 75 desconocidas por nivel (`easy`, `normal`, `hard`: inclinación, rotación hasta 180°, desenfoque, ruido,
  JPEG, reflejos). Umbral de rechazo calibrado en 80 + 75 escenas `normal` **distintas** (otra semilla) y aplicado a todas.
- Extractores medidos: `phash` (64-d) y `grid` (456-d). **DINOv2 y SigLIP no se ejecutaron** (requieren `torch`, `transformers` y
  descargar pesos: pendiente de autorización). **Tampoco hay modelo ajustado**, por lo que la comparación preentrenado/ajustado queda sin medir.

```powershell
python -m app.recognition_lab.cli sample            # selecciona y descarga (solo lectura) la muestra
python -m app.recognition_lab.cli audit             # auditoría de la muestra
python -m app.recognition_lab.cli benchmark --max-queries 200
```
Salida: `evaluation/reports/recognition-lab-benchmark.json` (y `recognition-r2-audit.json`).

## Resultados (consultas conocidas; los no detectados cuentan como fallo)

| Extractor | Nivel | Detección (esquinas ≤ 5 %) | Imagen exacta Top-1 / Top-5 | Identidad de carta Top-1 / Top-5 / Top-10 | Variante exacta dada la carta correcta |
|---|---|---|---|---|---|
| grid | easy | 99,5 % | 1,000 / 1,000 | 1,000 / 1,000 / 1,000 | 1,000 (n=29) |
| grid | normal | 98,5 % | 0,970 / 0,995 | 0,990 / 0,995 / 0,995 | 0,857 (n=28) |
| grid | hard | 63,5 % | 0,660 / 0,685 | 0,685 / 0,685 / 0,685 | 0,821 (n=28) |
| phash | easy | 99,5 % | 0,955 / 0,995 | 0,995 / 0,995 / 0,995 | 0,724 (n=29) |
| phash | normal | 98,5 % | 0,935 / 0,990 | 0,980 / 0,990 / 0,990 | 0,667 (n=27) |
| phash | hard | 63,5 % | 0,590 / 0,665 | 0,640 / 0,670 / 0,675 | 0,545 (n=22) |

Por estrato, identidad de carta Top-1 en `normal` / `hard` (grid): common 1,00 / 0,62 · full_art_secret 1,00 / 0,77 · old_sets 0,97 / 0,71 ·
promos 1,00 / 0,81 · **pattern_variants 0,97 / 0,74 (imagen exacta 0,83 / 0,61)** · **same_name_reprints 1,00 / 0,52**.

### Rechazo de cartas desconocidas
| Extractor (umbral, margen) | Nivel | Conocida correcta y aceptada | Conocida rechazada por error | **Desconocida aceptada (falso positivo)** |
|---|---|---|---|---|
| grid (0,904; 0,00) | easy / normal / hard | 0,990 / 0,945 / 0,425 | 0,010 / 0,055 / 0,540 | 0,053 / 0,053 / 0,000 |
| phash (0,625; 0,01) | easy / normal / hard | 0,995 / 0,970 / 0,535 | 0,005 / 0,030 / 0,425 | 0,093 / 0,080 / 0,053 |

### Rendimiento (CPU, un solo hilo de Python)
| Extractor | Detección p50 / p95 | Rectificar + embeber + buscar p50 / p95 | Matriz del índice (326 imágenes) | Extrapolado a 28.000 imágenes |
|---|---|---|---|---|
| grid (456-d) | 2,8 / 3,7 ms | 4,0 / 5,3 ms | 0,6 MB | ≈ 49 MB |
| phash (64-d) | 2,9 / 3,7 ms | 2,2 / 2,9 ms | 0,08 MB | ≈ 7 MB |

Memoria máxima de Python al construir el índice: ≈ 1,9 MB (medida con `tracemalloc`; no incluye OpenCV/Pillow ni un modelo profundo).

## Qué se puede concluir y qué no
- **El cuello de botella en escenas difíciles es la detección/rectificación** (solo 63,5 % de esquinas dentro del 5 %): con la carta bien
  encuadrada (`easy`, `normal`) las dos líneas base clásicas superan el 93 % de identidad. No se debe invertir en el extractor antes de
  tener un detector robusto.
- **Distinguir la carta es mucho más fácil que distinguir la variante**: la identidad de carta Top-1 es 0,97–0,99 en `normal`, la variante exacta
  0,67–0,86. Coincide con la auditoría (variantes casi idénticas, algunas son el mismo archivo). La variante necesita su propia evaluación
  con fotos reales y probablemente metadatos o confirmación.
- **Las reimpresiones y el arte repetido bajan el acierto en `hard`** (0,52 en `same_name_reprints`).
- **El rechazo de desconocidas con una sola puntuación es insuficiente**: 5–9 % de falsos positivos a costa de rechazar 3–6 % de las
  conocidas en `normal`; en `hard` el sistema rechaza más de lo que acierta.
- **No se probó la escala**: el índice tiene 326 imágenes, no 28.000. Con 28.000 habrá más vecinos parecidos y la confusión aumenta.
- **No hay categorías holo / reverse holo** reales: no se pueden inferir del nombre de carpeta ni se ven en la imagen digital.
- Los números son de **una** ejecución con una semilla; sin intervalos de confianza.

## Protocolo para la evaluación REAL (pendiente)
1. Reunir el conjunto C (ver `04-dataset-design.md`): ≥ 300 fotos, ≥ 150 cartas, ≥ 3 teléfonos, categorías: comunes, full art, holo, reverse
   holo, promos, antiguas, Poké Ball/Master Ball, ilustraciones parecidas, cartas ausentes, objetos que no son cartas.
2. Medir por categoría y condición: Top-1/Top-5/Recall@K de **carta**, y por separado de **impresión** y **variante**; falsos positivos y
   falsos rechazos al umbral calibrado en validación; latencia p50/p95 y memoria en el dispositivo/servidor objetivo; intervalos de Wilson.
3. Extractores a comparar con el **mismo** pipeline: DINOv2-small, DINOv2-base, SigLIP-base (preentrenados) y, si procede, el mejor ajustado con
   ArcFace/proxy + hard negatives. Reportar la ganancia del ajuste sobre el preentrenado con las mismas fotos de C.
4. Variantes: evaluar aparte con fotos bajo luz que haga visible el foil; si no se distinguen, resolver por metadatos/confirmación.

## Pendiente de tu autorización
- Instalar `torch` y `transformers` (CPU) y descargar los pesos: DINOv2-small ≈ 90 MB, DINOv2-base ≈ 350 MB, SigLIP-base ≈ 800 MB (estimaciones por
  nº de parámetros, licencia Apache-2.0 según sus fichas).
- Fotografiar el conjunto C.
- Revisar los términos de uso de las imágenes de R2 antes de entrenar.
