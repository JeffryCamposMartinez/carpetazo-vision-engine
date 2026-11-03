# Piloto de 30 cartas: cómo completar la plantilla

Archivos (en esta carpeta): `plantilla-30-cartas.csv` (vacía, 30 filas), `plantilla-30-cartas.EJEMPLO.csv` (3 filas FICTICIAS para ver el formato), `plantilla-sesiones.csv`.
Revisión metodológica previa: `../06-revision-metodologica-piloto.md`.

## Cómo completar `plantilla-30-cartas.csv`
Una fila por carta física (30 en total). `slot` y `categoria_sugerida` ya vienen puestos (5 comunes, 5 holo, 5 reverse holo, 5 full art, 5 promos, 5 similares/difíciles): es solo una guía inicial.

| Columna | Qué poner |
|---|---|
| `nombre`, `set`, `numero`, `idioma`, `acabado` | Lo que lees en la carta física (`acabado`: normal, holo, reverse_holo, full_art, promo, otro) |
| `categorias` | Una o varias separadas por `;` (se solapan): comun, holo, reverse_holo, full_art, promo, similar_dificil, antigua, variante_patron |
| `patron` | **Obligatorio**: ninguno, pokeball o masterball |
| `grupo_variante` | Mismo texto en la carta base y en sus versiones Poké Ball/Master Ball (si las tienes) |
| `similar_a` / `motivo_dificultad` | Para similar_dificil: con qué se confunde y por qué |
| `carta_verificada_por` / `nota_verificacion` | Tu nombre y qué comprobaste (lista de abajo). Si no estás seguro, déjalo vacío |
| `clave_referencia_r2`, `referencia_verificada_por`, `nota_referencia` | Clave completa del objeto de R2 que comparaste con la carta física, quién y qué comparaste |
| `id_tcgdex`, `card_id`, `printing_id`, `variant_id` | **Déjalos vacíos** salvo que estén confirmados; nunca se inventan |
| `sin_carta_motivo` | Si no tienes carta para ese slot: déjala sin nombre y escribe el motivo. Se registra como **carencia**; no se sustituye ni se inventa nada |

Importar: `.\scripts\dev\dataset.ps1 pilot-import-cards --csv docs\dataset\piloto\plantilla-30-cartas.csv --by TuNombre --dry-run` y luego sin `--dry-run`.
Sesiones: rellena fecha, teléfono, lugar y operador de `plantilla-sesiones.csv` (ronda **A** = calibración, **B** = evaluación final; puedes añadir filas) y ejecuta `pilot-import-sessions --csv ...`.
Estado: `.\scripts\dev\dataset.ps1 pilot-status --md evaluation\reports\pilot-status.md` (dice PILOTO COMPLETO solo con 30 cartas, 5+5 fotos verificadas por carta, rondas y cobertura).
Declarar una carencia suelta: `pilot-gap --category reverse_holo --reason "..." --by TuNombre`.

## Reglas de las dos rondas
- Cada carta: **5 fotos en la ronda A y 5 en la B**, con **la misma receta de 5 tomas** en ambas (misma dificultad), carta recolocada y en otro momento.
- Nunca copies, edites ni reutilices una toma; las fotos de A y B no se mezclan ni se repiten (la herramienta lo comprueba).
- `split --name piloto-1 --by-round` crea la partición (A = calibración, B = prueba).

## Lista de comprobación de la carta física (marca antes de `carta_verificada_por`)
- [ ] Nombre leído en la carta coincide con la fila.
- [ ] Nombre o símbolo del set y número de colección (p. ej. 058/102) coinciden.
- [ ] Idioma y acabado (normal / holo / reverse / full art / promo) confirmados a la luz.
- [ ] Patrón (Poké Ball / Master Ball / ninguno) revisado a ojo con inclinación.
- [ ] Segunda fuente consultada (catálogo o caja) y coincide.
- [ ] Estado anotado (funda, rayones, dobleces) en `notas_estado`.
- [ ] Imagen digital de referencia comparada con la carta física (ilustración, número, símbolo, patrón) y su clave anotada.
- [ ] Si algo no coincide o hay dudas: **no verificar**.
