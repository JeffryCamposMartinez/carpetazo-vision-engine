# 1. Investigación: arquitecturas y repositorios de referencia

Fecha de la consulta: 2026-11-03. Fuentes: README de cada repositorio, API de licencias de GitHub y fichas de Hugging Face.
**Todo lo de esta página son afirmaciones de esos documentos; no ejecuté ninguno de los cuatro proyectos.**

## 1.1 Los cuatro repositorios

| | TCG-AR (ULiège) | PTCG Scanner | Pokemon-Card-Identification | Real-Time Trading Card Recognizer |
|---|---|---|---|---|
| Enfoque | Detección + orientación + **ArcFace + vecino más cercano** | Detección YOLO-OBB + **OCR** + búsqueda de texto en catálogo | **Clasificación** (ResNet-18 con una clase por carta) | Visión clásica + **ArcFace (ResNet-50, 2048-d) + vecino más cercano** |
| Detector | Oriented R-CNN (OpenMMLab/mmrotate) | YOLO OBB (Ultralytics) | ninguno | Contornos OpenCV (ángulos, proporciones) |
| Embeddings | Sí (ArcFace; triplet como alternativa) | No | No | Sí (ArcFace; triplet descartado) |
| Agregar cartas sin reentrenar | **Sí** (según su README) | Sí (es texto) | **No** (cambia el nº de clases) | **Sí** |
| Datos | ≈20.360 cartas de Pokémon TCG API + escenas sintéticas + 14 cartas reales para evaluar | Roboflow (detección/ROI) + catálogo PkmnCards/TCGdex | Datasets de Kaggle | 20.479 cartas de pokemontcg.io; 20.426 imágenes (3,2 GB) |
| Resultado declarado | Real (14 cartas): Top-1 85,1 %, Top-5 96,2 % | Detector mAP50-95 ≈ 0,994; no publica acierto de extremo a extremo | 93,62 % **de entrenamiento** (no es medida de generalización) | pHash 95,7/76,3/37,7 % (fácil/normal/difícil, simulado); triplet 98,55 % sintético pero ≈0 % en fotos reales; ArcFace 85,1 % real (cita el paper de TCG-AR) |
| Licencia del **código** | **GPL-3.0** (confirmada por GitHub) | **CC BY-NC 4.0** (texto en LICENSE; GitHub: «Other») | **Ninguna** (la API de licencias devuelve 404) | README dice MIT, pero GitHub detecta «Other / NOASSERTION» y no pude leer el texto: **no verificada** |
| Licencia de **modelos/datos** | Sin licencia específica; datos de terceros | No clara (los pesos «no están en GitHub» / «irían a GitHub») | Remite a las páginas de Kaggle | Datos y pesos de terceros con sus propias condiciones (LEGAL.md no accesible) |
| Dependencias que molestan | CUDA 11.8/13, OpenMMLab, PySide6, Windows+NVIDIA | Ultralytics (AGPL), PyTorch | PyTorch, FastAPI | NVIDIA TAO, TensorRT, pycuda, **WSL2** para el modelo |

### Conclusión de licencias (decisión de ingeniería, no asesoría legal)
- **No copiar código, modelos ni datos de ninguno.** GPL-3.0 (copyleft), CC BY-NC (no comercial), sin licencia (todos los derechos
  reservados) y «no verificada» no son compatibles con un producto comercial propietario sin acuerdo escrito.
- **Sí usar las ideas** (no están protegidas): recuperación por embeddings, ArcFace/triplet, rectificación por homografía, escenas
  sintéticas, umbral de distancia + margen, hash perceptual como línea base.
- Para citar o contactar a los autores (p. ej. pedir una licencia para sus modelos) hay datos en cada README.
- Cuidado adicional con **Ultralytics (AGPL-3.0)**: no adoptar YOLO en el servicio sin licencia comercial.

## 1.2 Lecciones técnicas de los repositorios (declaradas por ellos)
1. **Recuperación gana a clasificación** cuando el catálogo crece: un clasificador de N clases exige datos de todas las cartas y
   reentrenar con cada set nuevo; con embeddings se agrega una carta calculando su vector.
2. **El entrenamiento solo con escenas sintéticas no garantiza fotos reales**: el triplet de Real-Time llegó a 98,55 % sintético y ≈0 %
   real. Por eso necesitamos un conjunto de evaluación con fotos reales y separado.
3. **La referencia es la propia imagen del catálogo**: las cifras «sobre el mismo catálogo» (pHash 85 % con 2.000 cartas) son una
   cota superior, no generalización.
4. **El fondo importa**: Real-Time pide fondo oscuro y mate; TCG-AR resuelve la detección con un modelo entrenado (Oriented R-CNN).
5. Escala: 20.426 vectores de 2048 dimensiones se buscan por fuerza bruta en tiempo real; con pgvector HNSW sobra capacidad.

## 1.3 Técnicas

**Clasificación frente a recuperación.** Clasificación: una salida por carta; no extensible, no rechaza lo desconocido y obliga a
reentrenar. Recuperación: un extractor genera un vector por imagen y se busca el más cercano en un índice; permite agregar cartas,
devolver alternativas (Top-K) y rechazar por distancia/margen. **Recomendado: recuperación.**

**Aprendizaje métrico.** Se entrena el extractor para acercar fotos de la misma carta y alejar cartas distintas.
- *Triplet/contrastiva*: sensible a la selección de negativos; su mejora sintética puede no transferir (caso Real-Time).
- *ArcFace*: softmax con margen angular sobre clases = cartas; produce embeddings muy separables, pero cada clase necesita imágenes
  de entrenamiento y se reentrena para cartas nuevas **solo si se quiere que el extractor las conozca**; el índice sí admite cartas
  no vistas (generalización a clases abiertas es limitada: hay que medirla con cartas de evaluación nunca vistas).
- *Hard negatives*: cartas muy parecidas (mismo Pokémon, otro set; patrones foil) son lo que más mejora el modelo.

**Backbones.**
- *ResNet (18/50)* y *EfficientNet (B0)*: CNN pequeñas, muy usadas con ArcFace; requieren fine-tuning para este dominio.
- *DINOv2* (autosupervisado, Apache-2.0 según su ficha; small = 22,1 M parámetros, base = 86,6 M): buen punto de partida para
  recuperación de instancias **sin entrenar**; dimensión 384 (small) / 768 (base) por la arquitectura (no figura en la ficha).
- *SigLIP* (visión-lenguaje, Apache-2.0 según su ficha; base ≈ 0,2 B parámetros incluyendo la torre de texto): embeddings semánticos;
  puede ser menos fino para distinguir instancias casi idénticas, algo a medir.
- Elección de dimensión: HNSW de pgvector admite **hasta 2.000 dimensiones** (comprobado en la prueba real); 384–1.024 son cómodas.

**Búsqueda de vecinos.** Fuerza bruta (exacta; suficiente para ≈28.000 vectores) → HNSW/IVFFlat (pgvector, aproximada, escala).
En nuestro esquema la columna `card_embeddings.embedding` no tiene dimensión: el índice se crea con una expresión
`(embedding::vector(N))` (probado en pgvector 0.8.7).

**Escenas sintéticas.** Pegar la imagen digital sobre fondos con perspectiva, rotación, luz, reflejos, desenfoque, ruido y JPEG.
Útiles para entrenar y para pruebas de humo; **no sustituyen** a la evaluación con fotos reales (brecha de dominio).

## 1.4 Modelos de embedding candidatos para evaluar (sin entrenar)

| Modelo | Licencia (ficha) | Parámetros | Dimensión | Pesos (aprox.) |
|---|---|---|---|---|
| DINOv2-small | Apache-2.0 | 22,1 M | 384 (+CLS/parches: 768 si se concatenan) | ≈ 90 MB (22,1 M × 4 B, F32) |
| DINOv2-base | Apache-2.0 | 86,6 M | 768 (1.536 concatenado) | ≈ 350 MB |
| SigLIP base patch16-224 | Apache-2.0 | ≈ 0,2 B (visión + texto) | 768 (por la arquitectura) | ≈ 800 MB (solo F32, incluye texto) |

Los tamaños son estimaciones por número de parámetros; **no se descargó ningún modelo** (requiere autorización previa).
