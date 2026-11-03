# 6. Revisión metodológica del piloto de 30 cartas (antes de modificar código)

Fecha: 2026-11-04. Se revisó el sistema implementado (`app/dataset_lab/`, documentos 01–05) frente a las precisiones del piloto:
30 cartas físicas en total; 5 comunes, 5 holo, 5 reverse holo, 5 full art, 5 promos y 5 similares/difíciles (categorías **solapables**); variantes
Poké Ball/Master Ball si se consiguen; 2 sesiones de 5 fotos por carta (A = calibración, B = evaluación final); sin mezcla ni duplicados entre conjuntos;
DINOv2-small congelado; referencia digital verificada a mano; carencias registrables sin inventar ni sustituir.

## 6.1 Qué ya permitía el sistema
- Separar por **sesión** con asignación manual (`split --assign sesión=calibration|test`) y detectar cuasi-duplicados de la misma carta entre conjuntos.
- Duplicados exactos globales por SHA-256, originales de solo lectura, identidades `null` si no están verificadas.
- Evaluar sin entrenar (extractores congelados) y negarse a calcular métricas con datos ficticios.

## 6.2 Problemas encontrados (y decisión)

| # | Problema | Por qué importa | Decisión |
|---|---|---|---|
| 1 | **La cuota anterior era por categorías excluyentes** (6 + 4 + 4 + 4 + 3 + 3 + 4 + 2) y la carta tenía **una sola** `category` | Contradice «30 cartas en total», categorías solapables y las 6 categorías nuevas | Cambiar a `categories` (lista, solapable). El piloto exige ≥ 5 cartas **por categoría contando solapes**, no 5 cartas exclusivas |
| 2 | **Faltaba el concepto de ronda.** Una «sesión» podía ser cualquiera y `split` asignaba sesiones al azar por hash | Sin una regla explícita, una sesión de la ronda A podía caer en prueba. El requisito es A = calibración y B = evaluación **para todas las cartas** | Añadir `round` (`A`/`B`) a cada sesión y `split --by-round` (A→calibración, B→prueba), con comprobaciones: toda sesión del piloto tiene ronda y ninguna sesión mezcla rondas |
| 3 | **Las 5 tomas de la ronda A eran más fáciles que las de la B** (A: frontales y luz normal; B: inclinación fuerte, reflejo fuerte, encuadre imperfecto) | Se calibraría el umbral con fotos fáciles y se probaría con fotos difíciles: los falsos rechazos de la prueba reflejarían el **desajuste de distribución**, no el modelo | **Misma receta de 5 tomas en ambas rondas** (mismas condiciones, carta recolocada y otro momento). La dificultad queda repartida igual |
| 4 | **La definición de sesión chocaba con las tomas variadas.** El protocolo decía «mismo fondo y luz» y a la vez pedía 5 tomas con luces y fondos distintos | Ambigüedad sobre qué agrupa a las fotos correlacionadas | Redefinir: la **ronda** es la ocasión de captura (día, lugar, teléfono, operador); dentro de una ronda puede haber varias condiciones. Lo que se prohíbe es compartir tomas entre rondas |
| 5 | **El rechazo de desconocidas usaba las mismas cartas «retiradas» en calibración y en prueba** (`--holdout-cards`) | El umbral se ajustaría con la puntuación de esas mismas cartas que luego se prueban (fuga a nivel de carta) | Dos conjuntos **disjuntos**: cartas desconocidas de calibración (se usan solo sus fotos A) y cartas desconocidas de prueba (solo sus fotos B). Ninguna carta retirada se usa en ambos conjuntos. Las fotos sobrantes de esas cartas se excluyen |
| 6 | **Pocas cartas desconocidas para acotar los falsos positivos.** Con 5 cartas × 5 fotos = 25 fotos desconocidas en prueba, 0 falsos aceptados da una cota superior del 95 % de ≈ 13 % | La cifra de falsos positivos sería poco informativa | Se mantiene la decisión de usar 5 + 5 cartas retiradas (deja 20 conocidas) y el informe **declara la cota**. Para una cota de ≈ 9 % hacen falta ≥ 40 fotos desconocidas (8 cartas) y quedarían solo 14 conocidas: es una decisión tuya (flags `--holdout-calibration`/`--holdout-test`) |
| 7 | **La referencia digital no tenía verificación manual registrada** (solo una clave de R2) | Requisito 8: debe verificarse a mano; hoy podría usarse una referencia nunca revisada | Añadir `reference_verified_by/at/note`; `card-reference` los exige; la evaluación **ignora** cartas sin referencia verificada |
| 8 | **Las variantes de patrón no tenían representación** (solo texto libre) | Poké Ball/Master Ball comparten número y nombre con la carta base | Campos `pattern` (`ninguno`/`pokeball`/`masterball`/`desconocido`) y `variant_group` (agrupa base y patrones del mismo número). Cada carta física cuenta como una de las 30; se recomienda como máximo 2 grupos |
| 9 | **Las «similares/difíciles» no estaban definidas** | Sin un criterio, la categoría sería subjetiva e irreproducible | Campo `similar_to` (otra carta registrada o texto) y nota obligatoria de por qué es difícil (mismo Pokémon en otro set, arte casi igual, marco antiguo, texto pequeño...) |
| 10 | **No se podía declarar una carencia.** El validador solo mostraba «faltan» | Requisito 9: registrar la carencia sin inventar datos ni forzar sustitución | Archivo `pilot.json` con `declared_gaps` (categoría, motivo, quién y cuándo). Una carencia **no declarada** impide dar el piloto por completo; una **declarada** se informa pero no bloquea |
| 11 | **No había validador de «piloto completo»** (30 cartas × 10 fotos) | Requisito del entregable | Nuevo `pilot-status`: exige 30 cartas verificadas, referencia verificada, patrón y categorías explícitos, **5 fotos verificadas en la ronda A y 5 en la B por carta**, partición por ronda sin fuga, cobertura por categoría (o carencia declarada) y ninguna condición `desconocida` |
| 12 | **Riesgo residual que no se puede eliminar:** las mismas cartas físicas aparecen en A y B | Mide reconocimiento de las *mismas cartas* en otra ocasión, no generalización a cartas nuevas | Se documenta. La generalización a cartas nunca vistas se mide con las **cartas desconocidas** (que sí lo son), no con las conocidas. No se afirma más allá del piloto |
| 13 | **Calibrar solo 2 números** (umbral y margen) con 5 fotos × 20 cartas | Es ajuste de umbrales, no entrenamiento; pero con poca muestra es inestable | Se permite (los pesos de DINOv2 permanecen congelados). El informe indica el tamaño de la calibración y no interpreta cambios pequeños |

## 6.3 Sobre «ninguna foto mezclada ni duplicada entre conjuntos»
Ya se cubre con tres controles que se mantienen: (1) SHA-256 único en todo el dataset (el mismo archivo no se puede importar dos veces), (2) cuasi-duplicados por hash perceptual
de la misma carta entre sesiones distintas → `needs_review` y bloqueo de la partición/exportación, (3) la evaluación verifica que ninguna sesión esté en los dos conjuntos.
Lo nuevo: la partición **por ronda** y la exigencia de que ninguna sesión mezcle rondas.
