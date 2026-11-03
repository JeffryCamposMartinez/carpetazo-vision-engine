# 8. E2 · Normalización

Implementado con datos sintéticos (`app/recognition/normalization.py`, `normalization_compare.py`, `app/dataset_lab/normalization_eval.py`). **No se afirma que normalizar mejore el reconocimiento**: se deja un mecanismo para medirlo con fotos reales.

## Qué conserva y qué produce
- La **foto original nunca se modifica**: `NormalizationResult.original` es una vista de solo lectura y se guarda su SHA-256 (probado).
- `rectified_raw`: solo geometría (esquinas del detector → 440×614, carta tumbada a vertical; se conservan 0° y 180°).
- `normalized`: carta con cambios fotométricos; `variants()` entrega ambas (con su par 0°/180°) para compararlas con las mismas fotos.
- `transforms`: registro de cada paso con parámetros y efecto medido; `config.tag` (`n1-<hash>`) identifica la versión exacta. Los vectores que se indexen deben guardar esa etiqueta.

## Pasos (todos configurables)
| Paso | Qué hace | Por defecto |
|---|---|---|
| `corner_refinement` | Ajusta cada lado a una recta con los bordes cercanos y recalcula esquinas; se descarta si mueve >2,5 % del alto | **Desactivado** (medido: escenas limpias empeoran ≈1,5 px; con fondos complejos el error medio baja de 0,0227 a 0,0146 del alto) |
| `border_crop` | Recorta 2 % por lado (funda, canto, fondo) y reescala | Activo |
| `illumination_flatten` | Divide la luminancia L por su estimación de baja frecuencia (σ = 25 % del ancho), fuerza 0,5 | Activo |
| `highlight_compression` | Por encima de L = 235 la pendiente baja a 0,5. **No rellena** reflejos | Activo |

Color: solo se toca L de Lab; a y b quedan intactos (probado, también con un arcoíris tipo Holo). La máscara de brillo especular se devuelve solo como información. Los valores por defecto son hipótesis, no resultados.

## Comparar con fotos reales
`dataset.ps1 compare-normalization --split piloto-1 --on calibration --extractor grid|dinov2` (solo lectura): Top-1/Top-5 por variante, discordancias pareadas, McNemar exacto y bootstrap de la diferencia. Se decide con la ronda A; `--on test` exige `--confirm-test` y debe usarse una sola vez. Se niega con datos ficticios y con referencias sin verificar. Informe: `evaluation/reports/normalization-compare-<split>-<on>.json`; se etiqueta EXPLORATORIO por debajo de 150 consultas.

## Resultado sintético (sin valor como precisión)
120 consultas, 60 cartas, extractor `grid`: raw 1,00 / normalized 1,00, 0 discordancias. Con cartas sintéticas el problema está saturado; no dice nada sobre fotos reales. La comparación pareada se podrá interpretar cuando existan las rondas A y B.
