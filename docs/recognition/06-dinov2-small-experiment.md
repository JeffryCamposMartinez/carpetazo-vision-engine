# 6. Experimento: DINOv2-small congelado (sin entrenamiento)

Fecha: 2026-11-03. Reproducible con `.\scripts\ml\setup-ml-env.ps1 -Run`. Datos crudos: `evaluation/reports/dinov2-small-experiment.json`.

> **Aviso principal.** No existen fotografías reales (la carpeta `Foto_Carpetazo` tiene 0 archivos). Todas las cifras de acierto vienen de
> **escenas sintéticas** generadas desde las mismas imágenes de referencia que están en el índice. **No son precisión real** y no deben citarse como tal.
> Además, **0 de las 401 imágenes de la muestra** tienen verificación documental (`data.json`): la identidad de cada imagen sale solo del
> nombre del objeto en R2, sin comprobar contra la carta física ni contra una asociación aprobada.

## 6.1 Modelo, pesos y licencias (cada componente por separado)

| Componente | Versión | Licencia | Fuente de la licencia |
|---|---|---|---|
| **Código DINOv2** (`facebookresearch/dinov2`) | — | **Apache-2.0** | API de licencias de GitHub + README («code and model weights are released under the Apache License 2.0») |
| **Pesos `facebook/dinov2-small`** | commit `ed25f3a31f01632728cabb09d1542f84ab7b0056` | **Apache-2.0** (permite uso comercial con atribución y aviso de licencia) | Ficha de Hugging Face + README del repositorio |
| Arquitectura | ViT-S/14, 22,06 M parámetros (medidos), `hidden_size` 384, parche 14, 12 capas, 6 cabezas | — | `config.json` |
| `transformers` | 5.22.1 | Apache-2.0 | metadatos del paquete |
| `torch` | 2.14.1+cu126 | BSD-3-Clause y otras permisivas (Apache-2.0, MIT, BSL-1.0) | metadatos del paquete |
| Bibliotecas CUDA/cuDNN incluidas en la rueda de PyTorch | CUDA 12.6 | **Licencia propietaria de NVIDIA** (no la cubre la licencia de PyTorch) | revisar el EULA de NVIDIA si algún día se redistribuye |
| `numpy` 2.5.4 · `opencv-python-headless` 5.0.0.93 · `pillow` 13.0.0 · `psutil` 7.2.2 | | BSD-3-Clause · Apache-2.0 · MIT-CMU · BSD-3-Clause | metadatos |
| `huggingface_hub` 2.4.0 · `safetensors` 0.8.0 · `tokenizers` 0.23.3 · `hf-xet` 1.8.0 | | Apache-2.0 | metadatos |
| `tqdm` 4.70.1 | | MPL-2.0 y MIT | metadatos |
| `psycopg` / `psycopg-binary` 3.3.7 | | **LGPL-3.0** (uso por enlace dinámico; no modificado) | metadatos |
| `pgvector` (Python) 0.5.1 | | MIT | metadatos |

Archivos de pesos descargados (solo estos, 84 MB en total): `model.safetensors` (84,2 MB, SHA-256
`ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1`) y `config.json` (SHA-256 `1809f83e3bdb1609a501a610ad4a742f4fd8ae44d72ca4aa0df52d1f2ac8628d`).
No se descargó DINOv2-base, SigLIP ni ningún otro modelo.

**Lo que esta tabla NO cubre:** los datos con los que Meta entrenó DINOv2 (LVD-142M) y las imágenes de cartas (derechos de The Pokémon Company / TCGplayer)
son asuntos aparte. La licencia Apache-2.0 del modelo no resuelve el derecho a usar nuestras imágenes de referencia en un producto comercial. Esto no es asesoría legal.

## 6.2 Entorno y GPU

- Entorno virtual **independiente**: `C:\Users\Jeffry\vision-ml-lab\venv` (4,4 GB; Python 3.13.5). El Python global y el `.venv` del proyecto no se modificaron. Congelado en `vision-ml-lab\requirements-ml.freeze.txt`.
- **GPU real del equipo: NVIDIA GeForce GTX 960, 2 GB de VRAM, capacidad de cómputo 5.2 (Maxwell), controlador 582.28 (CUDA 13.0).** Es la única GPU del sistema;
  **no hay una RTX 3070** en este PC (si la tienes en otro equipo, el experimento habría que repetirlo allí).
- Rueda elegida: `torch 2.14.1+cu126`. La matriz oficial de PyTorch 2.14 compila CUDA 12.6 desde Maxwell (sm_50), mientras que CUDA 13.x exige Turing (sm_75) o más. Arquitecturas de la
  rueda: sm_50, 60, 61, 70, 75, 80, 86, 90. Comprobado: `cuda.is_available()` = True y una multiplicación de matrices en GPU correcta. No se tocaron controladores.
- La GTX 960 ya tenía ≈ 880 MB ocupados por Windows; quedaban ≈ 1,6 GB libres.

## 6.3 Configuración del extractor (congelado)
`facebook/dinov2-small` en `eval`, sin gradientes, `float32`. Entrada: carta rectificada 440×614 → redimensionada a **224×308** (múltiplos de 14, conserva la
proporción 63:88, **sin recorte central**) → normalización de ImageNet. Embedding = **CLS + media de los parches (768-d)**, normalizado L2; similitud coseno.
Se probaron también «solo CLS» (384-d) y una entrada cuadrada 224×224. Las consultas se evalúan en 0° y 180° y se conserva la mejor.

## 6.4 Resultados (SINTÉTICOS; 200 consultas conocidas + 75 desconocidas por nivel; índice de 326 imágenes / 292 cartas; misma semilla y mismas consultas que las líneas base)

### Identidad de carta Top-1 / Top-5 / Top-10 e imagen exacta Top-1

| Extractor | easy carta T1/T5/T10 | normal carta T1/T5/T10 | hard carta T1/T5/T10 | exacta T1 easy / normal / hard |
|---|---|---|---|---|
| **DINOv2-s 768-d (224×308)** | 1,000 / 1,000 / 1,000 | 0,990 / 1,000 / 1,000 | 0,500 / 0,630 / 0,655 | 0,995 / 0,955 / 0,480 |
| DINOv2-s 384-d (solo CLS) | 1,000 / 1,000 / 1,000 | 0,990 / 0,995 / 1,000 | 0,500 / 0,635 / 0,640 | 0,985 / 0,955 / 0,460 |
| DINOv2-s 768-d (224×224) | 1,000 / 1,000 / 1,000 | 0,990 / 1,000 / 1,000 | 0,560 / 0,620 / 0,640 | 0,975 / 0,945 / 0,540 |
| Línea base `grid` (456-d) | 1,000 / 1,000 / 1,000 | 0,990 / 0,995 / 0,995 | 0,685 / 0,685 / 0,685 | 1,000 / 0,970 / 0,660 |
| Línea base `phash` (64-d) | 0,995 / 0,995 / 0,995 | 0,980 / 0,990 / 0,990 | 0,640 / 0,670 / 0,675 | 0,955 / 0,935 / 0,590 |

Con n = 200 el intervalo de Wilson del 95 % ronda ±7 puntos cerca del 50 % y ±2–3 cerca del 98 %.

### Impresión / variante (por separado)
Variante exacta dada la carta correcta en Top-1 (n ≈ 16–29 por nivel; muy poca muestra):

| Extractor | easy | normal | hard |
|---|---|---|---|
| DINOv2-s 768-d (224×308) | 0,966 | 0,759 | 0,750 (n=16) |
| `grid` | 1,000 | 0,857 | 0,821 |
| `phash` | 0,724 | 0,667 | 0,545 |

En el estrato `pattern_variants` (Poké Ball / Master Ball frente a la base), DINOv2-s en `normal` acierta la **carta** el 100 % pero la **imagen exacta** solo el 75,9 %
(`grid`: 96,6 % / 82,8 %). Ningún extractor distingue de forma fiable las variantes; la auditoría ya mostraba que casi no se diferencian en la imagen digital.

### Rechazo de cartas desconocidas (umbral calibrado en escenas `normal` distintas)
| Extractor (umbral, margen) | Nivel | Conocida correcta y aceptada | Conocida rechazada | **Desconocida aceptada** |
|---|---|---|---|---|
| DINOv2-s 768-d (0,823; 0,03) | easy / normal / hard | 0,980 / 0,885 / 0,120 | 0,020 / 0,115 / 0,845 | 0,147 / 0,067 / 0,013 |
| `grid` (0,904; 0,00) | easy / normal / hard | 0,990 / 0,945 / 0,425 | 0,010 / 0,055 / 0,540 | 0,053 / 0,053 / 0,000 |
| `phash` (0,625; 0,01) | easy / normal / hard | 0,995 / 0,970 / 0,535 | 0,005 / 0,030 / 0,425 | 0,093 / 0,080 / 0,053 |

### Recursos medidos
| | DINOv2-small | `grid` |
|---|---|---|
| Carga del modelo | 8,9 s (84 MB de pesos) | — |
| Un embedding, GPU GTX 960 | **p50 21,4 ms · p95 24,0 ms** (100 ejecuciones) | — |
| Un embedding, CPU (8 hilos) | p50 73,0 ms · p95 79,8 ms (30 ejecuciones) | — |
| Consulta completa (2 orientaciones + búsqueda), GPU | p50 55,9 ms · p95 57,2 ms (+ detección 2,7 ms) | 4,1 ms (+ 2,9 ms) |
| RAM del proceso | 686 MB tras importar torch → 805 MB con el modelo → pico 1.900 MB (incluye imágenes y OpenCV) | — |
| VRAM | PyTorch: pico asignado **269 MB** (reservado 306 MB); `nvidia-smi`: de 1.028 a 1.307 MB usados de 2.048 | — |
| Índice en memoria (326 imágenes) | 978 KB (768-d) → ≈ 84 MB para 28.000 imágenes | 581 KB → ≈ 49 MB |

### pgvector (clúster privado `vision-pg-lab`, pgvector 0.8.7, base `vision_ml_lab`, tabla temporal ya eliminada)
326 vectores de 768 dimensiones, 100 consultas: la búsqueda exacta de pgvector coincide con NumPy en el **100 %** del Top-1; HNSW (`m=16`, `ef_construction=64`) coincide
con la exacta en el 100 % del Top-1 y tiene **recall@10 = 1,0**; latencia p50 de 1,70 ms (exacta) y 1,64 ms (HNSW) en una tabla tan pequeña (sin diferencia apreciable); construcción del índice 48 ms, 1,3 MB.
Esto valida la integración técnica, **no** el comportamiento con 28.000 vectores.

## 6.5 Lectura honesta de los resultados
1. **DINOv2-small no supera a las líneas base clásicas en este benchmark** (ni en identidad de carta ni en rechazo), y en `hard` queda claramente por debajo (0,50 frente a 0,685).
   Este benchmark favorece a las características de píxel porque cada consulta es una versión deformada de la imagen que está en el índice; es una prueba de humo, **no una prueba de que el modelo profundo sea peor o mejor con fotos reales**.
2. En `hard` pesa más la **detección/rectificación** (solo el 63,5 % de las esquinas dentro del 5 %): ningún extractor puede arreglar un recorte equivocado.
3. La **forma de preprocesar** cambia poco (aspecto 224×308 vs cuadrado, CLS vs CLS+media); las diferencias están dentro del ruido estadístico.
4. **Variantes**: la identidad de carta es fácil; la variante no, con cualquier extractor.
5. **Rechazo**: con una sola puntuación el DINOv2-small acepta entre 6,7 % y 14,7 % de cartas desconocidas (niveles `normal` y `easy`) al umbral calibrado, y rechaza por error el 11,5 % de las conocidas en `normal`; hace falta un mecanismo mejor (OCR, margen por carta, verificación geométrica).

## 6.6 Limitaciones
- Sin fotos reales (0 archivos), sin verificación documental de identidad (0/401), una sola semilla, sin intervalos por estrato (n pequeños), índice de 326 imágenes (no 28.000).
- No se probó holo / reverse holo (no se pueden inferir de la imagen digital ni del nombre), ni fundas, fondos reales, desenfoque de movimiento ni dedos tapando bordes.
- GPU de 2 GB con 880 MB ya ocupados: sirve para este modelo, pero limita lotes grandes y modelos mayores; la comparación con una RTX 3070 no se hizo.
- El preprocesado propio (OpenCV `INTER_AREA`) no es idéntico al `AutoImageProcessor` de Hugging Face (bicúbico de PIL).
- No hay comparación «preentrenado frente a ajustado»: no se entrenó nada (por instrucción).

## 6.7 Recomendación: ¿vale la pena continuar con DINOv2-small?
**Sí, como candidato a evaluar con fotos reales — no como decisión ya tomada.**
- A favor: licencia Apache-2.0 (código y pesos), 84 MB, 22 M parámetros, 21 ms por embedding en una GTX 960 (73 ms en CPU), 270 MB de VRAM, 768 dimensiones indexables con HNSW,
  y el flujo con pgvector funciona de punta a punta.
- En contra / sin evidencia: este experimento **no demuestra ninguna ventaja** sobre un descriptor clásico; su valor esperado (robustez a iluminación, reflejos y fondos reales) solo se puede
  comprobar con fotografías de teléfono.
- **Siguiente paso que más valor aporta (no avanzo sin tu OK):** reunir el conjunto C de fotos reales (≥ 300) y, en paralelo, un detector de carta robusto. Con eso medir DINOv2-small frente a
  `grid`/`phash` y, solo entonces, decidir si compensa probar DINOv2-base o SigLIP o considerar un ajuste fino. **No recomiendo entrenar ni integrar en producción antes de eso.**
