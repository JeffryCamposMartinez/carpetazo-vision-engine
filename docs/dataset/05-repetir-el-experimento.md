# 5. Cómo repetir todo el experimento (de cero)

Requisitos: Windows, el proyecto preparado (`.\scripts\dev\setup.ps1`), el entorno ML opcional (`.\scripts\ml\setup-ml-env.ps1`, ya creado en este PC).
No se usa Docker, WSL2 ni virtualización. No se escribe en R2 en ningún paso.

## Paso a paso
1. **Preparar el dataset vacío**
   `.\scripts\dev\dataset.ps1 init`
2. **Elegir las 30 cartas** (cuota en `01-protocolo-de-captura.md` §1.2) y **registrarlas** con `card-add`. Comparar cada una con la carta física y una segunda fuente y marcarla con `card-verify`.
   Si una impresión o variante no está clara, déjala sin verificar.
3. **Elegir la referencia digital de cada carta verificada.** Busca la imagen en el inventario de R2 (`evaluation/reports/r2-inventory.json`), mírala junto a la carta física y,
   solo si es la misma impresión, regístrala: `dataset.ps1 card-reference pc-001 --key "Carpetazo.cl/Pokemon/Pokemon_DB/Pokemon_Images/<set>/<carta>/<carta>.webp"`. Es una decisión humana; no se usa el manifiesto automático.
4. **Fotografiar** siguiendo `01-protocolo-de-captura.md` (2 sesiones por carta, 5 tomas por sesión). Crear cada sesión antes con `session-add`.
5. **Copiar las fotos** a `datasets\real-photos\inbox\<sesión>\<pc-XXX>\` sin renombrar ni editar.
6. **Importar** (primero `--dry-run`, luego real) con las condiciones de la toma; usa `--sidecar` si las condiciones varían por foto.
7. **Revisar**: abre las fotos marcadas (`needs_review`/banderas), corrige condiciones y márcalas `verified` o `rejected` con `review`. No uses `--accept-quality` o `--accept-duplicates` sin mirar la foto.
8. **Validar**: `dataset.ps1 validate`. Criterios de aceptación del piloto: 0 errores; 30 cartas verificadas; ≥ 10 fotos verificadas por carta; ≥ 2 sesiones por carta;
   `unknown_conditions_in_verified` = 0; ≤ 10 % de fotos en `needs_review`.
9. **Respaldar**: `dataset.ps1 backup --dest D:\Respaldos`.
10. **Particionar por sesión**: `dataset.ps1 split --name piloto-1 --seed 7` (si dos sesiones son de la misma carta y casi idénticas, el comando avisa de la fuga y no se debe continuar).
11. **Traer las referencias** (lectura de R2): `dataset.ps1 references-fetch`.
12. **Congelar la configuración** (extractor, semilla, `--holdout-cards`, `--distractors`) y anotarla en un archivo de texto junto al informe.
13. **Evaluar**: `dataset.ps1 evaluate --split piloto-1 --extractor phash grid --holdout-cards 5` y `.\scripts\ml\eval-real-photos.ps1 -Split piloto-1 -HoldoutCards 5` (añade DINOv2-small).
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
| `split` pide 2 sesiones | Hace falta al menos una sesión de calibración y una de prueba con fotos verificadas |
| `evaluate` dice «FICTICIOS» | El dataset contiene datos de ejemplo/prueba; usa un dataset real |
| `dinov2 omitido` | Ejecuta desde el entorno ML (`eval-real-photos.ps1`); necesita `torch` y los pesos ya descargados |
