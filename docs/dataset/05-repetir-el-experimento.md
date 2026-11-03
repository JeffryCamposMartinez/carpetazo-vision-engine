# 5. Cómo repetir todo el experimento (de cero)

Requisitos: Windows, el proyecto preparado (`.\scripts\dev\setup.ps1`), el entorno ML opcional (`.\scripts\ml\setup-ml-env.ps1`, ya creado en este PC).
No se usa Docker, WSL2 ni virtualización. No se escribe en R2 en ningún paso.

## Paso a paso
1. **Preparar el dataset vacío**
   `.\scripts\dev\dataset.ps1 init`
2. **Elegir las 30 cartas físicas en total** (objetivo por categoría solapable en `01-protocolo-de-captura.md` §1.2) y **registrarlas** con la plantilla `docs/dataset/piloto/plantilla-30-cartas.csv`
   (`pilot-import-cards --dry-run` primero). Comparar cada una con la carta física y una segunda fuente y marcarla con `card-verify` (o rellenando `carta_verificada_por`/`nota_verificacion`).
   Si una impresión o variante no está clara, déjala sin verificar. Los ids del catálogo quedan en `null`. Si falta una categoría: `pilot-gap` o una fila con `sin_carta_motivo` (nunca una carta inventada).
3. **Elegir la referencia digital de cada carta verificada.** Busca la imagen en el inventario de R2 (`evaluation/reports/r2-inventory.json`), mírala junto a la carta física y,
   solo si es la misma impresión, regístrala: `dataset.ps1 card-reference pc-001 --key "Carpetazo.cl/Pokemon/Pokemon_DB/Pokemon_Images/<set>/<carta>/<carta>.webp" --by TuNombre --note "ilustración, número y símbolo coinciden"`. Es una verificación humana; no se usa el manifiesto automático.
4. **Fotografiar** siguiendo `01-protocolo-de-captura.md`: dos rondas por carta, **A = calibración y B = evaluación final**, 5 tomas por ronda con **la misma receta** en ambas. Crear cada sesión antes (`pilot-import-sessions` o `session-add --round A|B`).
5. **Copiar las fotos** a `datasets\real-photos\inbox\<sesión>\<pc-XXX>\` sin renombrar ni editar.
6. **Importar** (primero `--dry-run`, luego real) con las condiciones de la toma; usa `--sidecar` si las condiciones varían por foto.
7. **Revisar**: abre las fotos marcadas (`needs_review`/banderas), corrige condiciones y márcalas `verified` o `rejected` con `review`. No uses `--accept-quality` o `--accept-duplicates` sin mirar la foto.
8. **Validar**: `dataset.ps1 validate` y `dataset.ps1 pilot-status --md evaluation\reports\pilot-status.md`. El piloto está **completo** solo si: 30 cartas registradas y verificadas; referencia verificada a mano en cada una;
   patrón y categorías declarados; **exactamente 5 fotos verificadas en la ronda A y 5 en la B por carta**; todas las sesiones con ronda; partición por ronda sin fugas; cada categoría con ≥ 5 cartas o con carencia declarada;
   0 condiciones `desconocido` en fotos verificadas; 0 errores de `validate`.
9. **Respaldar**: `dataset.ps1 backup --dest D:\Respaldos`.
10. **Particionar por ronda**: `dataset.ps1 split --name piloto-1 --by-round` (A → calibración, B → prueba; si dos fotos de rondas distintas son casi idénticas, el comando avisa de la fuga y no se debe continuar).
11. **Traer las referencias** (lectura de R2): `dataset.ps1 references-fetch`.
12. **Congelar la configuración** (extractor, semilla, `--holdout-calibration`, `--holdout-test`, `--distractors`) y anotarla en un archivo de texto junto al informe.
13. **Evaluar**: `dataset.ps1 evaluate --split piloto-1 --extractor phash grid --holdout-calibration 5 --holdout-test 5` y `.\scripts\ml\eval-real-photos.ps1 -Split piloto-1 -HoldoutCalibration 5 -HoldoutTest 5` (añade DINOv2-small congelado; no hay entrenamiento).
    Repetir con `--distractors 300` para ver el efecto del tamaño de la galería.
14. **Informar** con `evaluation/reports/real-photo-eval-piloto-1.md/.json`: citar la etiqueta (PILOTO o EXPLORATORIO), n, intervalos y limitaciones. No citar celdas con n < 30.
15. **Exportar** si hace falta analizar fuera de la herramienta: `dataset.ps1 export --out datasets\real-photos\exports\piloto-1 --split piloto-1`.

## Reproducibilidad
- Semillas explícitas (`split --seed`, `evaluate --seed`); la partición queda guardada en `splits/<nombre>.json`.
- El informe incluye la huella SHA-256 de `photos.json`, el número de fotos y las cartas retiradas.
- Los originales tienen SHA-256 verificado por `validate`; cualquier cambio en un original aparece como error.
- Las versiones de las dependencias del entorno ML están en `vision-ml-lab\requirements-ml.freeze.txt` (ver `docs/recognition/06`).

## Problemas frecuentes
| Síntoma | Causa y solución |
|---|---|
| `formato HEIC no admitido` | Cambia la cámara a JPEG («Más compatible» en iPhone) o convierte con la app de fotos sin recortar ni editar |
| Muchas fotos `needs_review` por `borrosa` | Mejor enfoque o más luz; los umbrales son provisionales: si la foto es buena a ojo, usa `review --accept-quality` y anótalo |
| `posible_duplicado_entre_sesiones` | Probablemente la misma toma en dos sesiones: elimina la repetida del lote (no del original ya importado: recházala con `review --status rejected`) |
| `split` pide 2 sesiones / rondas | Hace falta al menos una sesión de la ronda A y una de la B con fotos verificadas; todas las sesiones usadas deben tener ronda |
| `review` se niega a verificar una foto más | La carta ya tiene 5 fotos verificadas en esa ronda; rechaza la sobrante con `review --status rejected` |
| `pilot-import-cards` informa «EJEMPLO» | Es una fila de la plantilla de ejemplo (ficticia); no se importa. Usa `plantilla-30-cartas.csv` |
| `evaluate` dice «FICTICIOS» | El dataset contiene datos de ejemplo/prueba; usa un dataset real |
| `dinov2 omitido` | Ejecuta desde el entorno ML (`eval-real-photos.ps1`); necesita `torch` y los pesos ya descargados |
