# Piloto de 30 cartas: cómo completar la plantilla

Archivos (en esta carpeta):
- `plantilla-30-cartas.csv`: **vacía**, 30 filas (solo `slot` y `categoria_sugerida`). Es la que se completa.
- `plantilla-30-cartas.EJEMPLO.csv`: 3 filas **FICTICIAS** (todas empiezan por «EJEMPLO») solo para ver el formato. **La herramienta se niega a importarlas** (`pilot-import-cards` las informa como error aunque sea `--dry-run`).
- `plantilla-sesiones.csv`: las dos rondas (A = calibración, B = evaluación final).

Revisión metodológica: `../06-revision-metodologica-piloto.md`. Protocolo de fotos: `../01-protocolo-de-captura.md`.
Abre los CSV con Excel (UTF-8 con BOM) y guárdalos como **CSV UTF-8**, con la fila de encabezados intacta.

## Reglas generales
- **30 cartas físicas en total**, una fila por carta. Las categorías se **solapan** (una carta puede ser `holo;promo;similar_dificil`).
- `categoria_sugerida` es solo una guía (5 por categoría). Lo que vale es la columna `categorias`.
- Una fila sin `nombre` y sin `sin_carta_motivo` se **ignora** (no es un error: puedes ir completando por partes y reimportar; la importación es idempotente por `slot`).
- Cada fila inválida se informa con su motivo y **no se importa nada de ella**; el resto sí. Con `--dry-run` se aplican exactamente las mismas validaciones sin escribir nada.

## Columnas de `plantilla-30-cartas.csv`
| Columna | ¿Obligatoria? | Qué poner |
|---|---|---|
| `slot` | ya viene | 1–30. No lo cambies ni lo repitas |
| `categoria_sugerida` | ya viene | Guía; no se importa |
| `nombre`, `set`, `numero` | **sí** (si hay carta) | Lo que lees en la carta física (número como `058/102`) |
| `idioma` | recomendada | Código: `es`, `en`, `ja`... (vacío = `unknown`) |
| `acabado` | **sí** | `normal`, `holo`, `reverse_holo`, `full_art`, `promo`, `otro` |
| `categorias` | **sí** | Una o varias separadas por `;`: `comun`, `holo`, `reverse_holo`, `full_art`, `promo`, `similar_dificil`, `antigua`, `variante_patron` (vacío = «sin asignar»: el estado del piloto lo marca pendiente) |
| `patron` | **sí** | `ninguno`, `pokeball` o `masterball` |
| `grupo_variante` | opcional | Mismo texto en la carta base y en sus versiones Poké Ball/Master Ball |
| `similar_a`, `motivo_dificultad` | `motivo_dificultad` **sí** si es `similar_dificil` | Con qué se confunde y por qué |
| `notas_estado` | opcional | Funda, rayones, dobleces |
| `carta_verificada_por`, `nota_verificacion` | **van juntas**, opcionales | Tu nombre y qué comprobaste (lista de abajo). Si no estás seguro, déjalas vacías: la carta queda sin verificar |
| `clave_referencia_r2`, `referencia_verificada_por`, `nota_referencia` | **van juntas**, opcionales; exigen carta verificada | Clave completa del objeto de R2 que **comparaste con la carta física**, quién y qué comparaste. Sin esto la carta no entra en la galería de evaluación |
| `id_tcgdex` | opcional | Solo si lo confirmaste |
| `card_id`, `printing_id`, `variant_id` | **déjalos vacíos** | Solo un UUID del catálogo ya confirmado por una persona; cualquier otra cosa se rechaza. Vacío = `null` |
| `sin_carta_motivo` | solo para carencias | Ver abajo |

## Valores permitidos (escríbelos exactamente así, en minúsculas y sin tildes)
| Columna | Valores |
|---|---|
| `acabado` | `normal`, `holo`, `reverse_holo`, `full_art`, `promo`, `otro` |
| `categorias` | `comun`, `holo`, `reverse_holo`, `full_art`, `promo`, `similar_dificil`, `antigua`, `variante_patron` (varias separadas por `;`, sin espacios obligatorios; ejemplo `holo;promo`) |
| `patron` | `ninguno`, `pokeball`, `masterball` |
| `idioma` | `es`, `en`, `ja`... (código de 2 letras) o vacío |
| `ronda` (sesiones) | `A` o `B` en mayúscula |
| `fecha` (sesiones) | `AAAA-MM-DD` con fecha real, p. ej. `2026-11-10`. Otros formatos (`10-11-2026`, `2026/11/10`, `2026-02-30`) se rechazan |
Cualquier otro valor se rechaza con su motivo; nada se corrige por tu cuenta.

## ¿Es obligatoria la referencia digital?
Se puede **registrar y verificar** una carta física sin referencia, y fotografiarla. Pero:
- **`pilot-status` no dará «PILOTO COMPLETO»** si alguna de las 30 cartas no tiene la referencia verificada a mano y traída (`references-fetch`).
- **En la evaluación, esa carta se excluye por completo** (sus fotos de A y B no se usan: no cuenta como conocida ni como desconocida) y el informe lo declara en `excluded_photos_without_verified_reference`. Así no se contamina el rechazo de desconocidas con cartas que simplemente no tenían galería.
- Una carta sin referencia **no sirve como carta desconocida** controlada: solo se retiran de la galería cartas que sí tienen referencia verificada.
Conclusión: para el piloto completo, las 30 necesitan referencia verificada; si alguna no puede tenerla, el piloto queda incompleto (o se decide uno menor, tú decides).

## Una carta que NO tienes: `pilot-gap` o `sin_carta_motivo` (sin inventar una carta)
Una carta que no tienes **no existe en el dataset**: no cuenta como capturada, ni como verificada, ni suma fotos. Solo se declara que a una categoría le falta cobertura. Si más adelante la consigues, rellena esa fila y reimporta (es idempotente por `slot`).
Dos formas equivalentes, ninguna crea una carta:
1. En el CSV: deja el `slot` sin `nombre` y escribe `sin_carta_motivo` (p. ej. «no tengo ninguna reverse holo»). Se declara la carencia para `categorias` de esa fila o, si está vacía, para su `categoria_sugerida`. Una fila no puede llevar `nombre` y `sin_carta_motivo` a la vez.
2. Suelta: `.\scripts\dev\dataset.ps1 pilot-gap --category reverse_holo --reason "no tengo ninguna" --by TuNombre`.

La carencia queda en `pilot.json` (motivo, quién, cuándo) y en el informe. **No sustituye el requisito de 30 cartas verificadas**: si no llegas a 30, el veredicto sigue siendo «PILOTO INCOMPLETO» (puedes compensar una categoría con más cartas de otras categorías; las categorías se solapan).

## Sesiones (`plantilla-sesiones.csv`)
Columnas: `session_id`, `ronda` (**A** o **B**, obligatoria), `fecha` (**obligatoria**, `AAAA-MM-DD`: no se inventa ninguna), `telefono`, `lugar`, `operador`, `notas`.
Puedes añadir filas (varias sesiones por ronda); una sesión no mezcla rondas. `session_id` de 3–41 caracteres (letras, números, `_`, `.`, `-`); un id repetido se omite o se informa.

## Orden de trabajo y comandos
Los comandos se ejecutan desde `C:\Users\Jeffry\Desktop\carpetazo-vision-engine`. Primero siempre `--dry-run`:
```powershell
.\scripts\dev\dataset.ps1 init
.\scripts\dev\dataset.ps1 pilot-import-cards --csv docs\dataset\piloto\plantilla-30-cartas.csv --by TuNombre --dry-run
.\scripts\dev\dataset.ps1 pilot-import-cards --csv docs\dataset\piloto\plantilla-30-cartas.csv --by TuNombre
.\scripts\dev\dataset.ps1 pilot-import-sessions --csv docs\dataset\piloto\plantilla-sesiones.csv --dry-run
.\scripts\dev\dataset.ps1 pilot-import-sessions --csv docs\dataset\piloto\plantilla-sesiones.csv
.\scripts\dev\dataset.ps1 pilot-status --md evaluation\reports\pilot-status.md
```
Después (con las fotos reales): `import` → `review` → `pilot-status` → `split --name piloto-1 --by-round` → `references-fetch` → `evaluate` (ver `../05-repetir-el-experimento.md`).

## Reglas de las dos rondas
- Cada carta: **exactamente 5 fotos verificadas en la ronda A y 5 en la B**, con **la misma receta de 5 tomas** en ambas (misma dificultad), carta recolocada y en otra ocasión. `review` se niega a verificar una sexta foto de la misma carta y ronda.
- Nunca copies, edites ni reutilices una toma; las fotos de A y B no se mezclan ni se repiten (la herramienta lo comprueba por SHA-256 y por cuasi-duplicados).
- `split --name piloto-1 --by-round` crea la partición (A = calibración, B = prueba).
- DINOv2-small permanece **congelado**: la calibración solo ajusta el umbral y el margen.

## Lista de comprobación de la carta física (marca antes de `carta_verificada_por`)
- [ ] Nombre leído en la carta coincide con la fila.
- [ ] Nombre o símbolo del set y número de colección (p. ej. 058/102) coinciden.
- [ ] Idioma y acabado (normal / holo / reverse / full art / promo) confirmados a la luz.
- [ ] Patrón (Poké Ball / Master Ball / ninguno) revisado a ojo con inclinación.
- [ ] Segunda fuente consultada (catálogo o caja) y coincide.
- [ ] Estado anotado (funda, rayones, dobleces) en `notas_estado`.
- [ ] Imagen digital de referencia comparada con la carta física (ilustración, número, símbolo, patrón) y su clave anotada en `clave_referencia_r2`.
- [ ] Si algo no coincide o hay dudas: **no verificar**.
