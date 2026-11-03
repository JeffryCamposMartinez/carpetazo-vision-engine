# 7. E1 detección de cartas y E3 servicio de embeddings

Estado: implementado y probado **solo con datos sintéticos**. No está conectado a `/v1/scan` (contrato público sin cambios; eso es E8). No lee R2 ni indexa nada.

## E1 · `app/recognition/detection.py`
- `CardDetector.detect(bgr)` → `Detection(found, confidence, quad, metrics, reason, warnings)`; `found=False` equivale a `not_a_card`.
- Candidatos desde cuatro estrategias (Canny adaptativo, Otsu en ambas polaridades, distancia de color al fondo); puertas duras (proporción 63:88, rectangularidad, ángulos) y puntuación por área, proporción, ángulos y soporte de borde.
- `CardDetector.rectify` endereza a 440×614 y corrige la carta tumbada (90°). El 180° **no se decide aquí**: devuelve `upright` y `flipped` y la búsqueda elige la mejor.
- Métricas: área, proporción, rectangularidad, desviación de ángulos, sesgo de perspectiva, soporte de borde, carta tocando el borde, candidatos fuertes; y de la carta rectificada: nitidez, brillo, contraste, fracción de reflejo.
- **Umbral provisional** (`DEFAULT_MIN_CONFIDENCE = 0.62`) ajustado solo con escenas sintéticas; recalibrar con la ronda A. La confianza es una puntuación de forma, no una probabilidad.
- **Límite conocido:** una hoja rectangular con proporción de carta (A4) se detecta como carta; lo debe rechazar la similitud (E7). Escenas difíciles (rotación libre + perspectiva fuerte + JPEG 45): ≈ 70 % de esquinas dentro del 5 % en sintético.

## E3 · `app/recognition/embedding/`
- `Extractor` (interfaz), `EmbeddingSpec(model_name, model_version, dim)` y `EmbeddingResult`.
- `dinov2_worker.py` corre con `C:\Users\Jeffry\vision-ml-lab\venv` (la API no instala torch). Modelo congelado, sin red (`local_files_only`), protocolo binario por pipes.
- `Dinov2WorkerExtractor`: carga diferida, un cerrojo (una inferencia a la vez), descarga por inactividad (libera VRAM), tiempo límite, verificación de revisión de pesos y dimensión.
- GPU de 2 GB: `device=auto` usa CUDA solo si quedan ≥ 700 MB libres; si CUDA se queda sin memoria pasa a CPU; `cpu` forzable.
- `EmbeddingService`: primario DINOv2-small con respaldo `grid`; tras un fallo espera `embedding_retry_cooldown_seconds`; cada resultado declara `fallback_used`, el motivo y su `spec`.
- Versiones: `dinov2-small:ed25f3a-clsmean-224x308-p1` (768-d) y `grid:g1-8x11c-4x6x8g-p1` (456-d). Nunca se comparan vectores de especificaciones distintas.
- Ajustes (`VISION_EMBEDDING_*`): `PRIMARY`, `ML_PYTHON`, `ML_HOME`, `DEVICE`, `MIN_FREE_VRAM_MB`, `WORKER_IDLE_SECONDS`, `WORKER_STARTUP_TIMEOUT`, `REQUEST_TIMEOUT`, `RETRY_COOLDOWN_SECONDS`.

## Lo que NO prueba
Ninguna cifra de aquí es precisión real. En la prueba de humo con cartas sintéticas abstractas, DINOv2 dio similitud 0,925 (misma carta, otra escena) frente a 0,921 (cartas distintas): los valores absolutos se agrupan y no sirven de umbral; hay que calibrar con fotos reales.
