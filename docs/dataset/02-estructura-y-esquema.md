# 2. Estructura de carpetas y esquema de anotación

Todo vive en `datasets/real-photos/` (carpeta **ignorada por Git**; nunca se sube). Esquema versión 1.

## 2.1 Estructura de carpetas
```
datasets/real-photos/
├── dataset.json              # versión del esquema y fecha de creación
├── pilot.json                # (opcional) plan del piloto y carencias declaradas (pilot-gap)
├── inbox/                    # tú copias aquí las fotos nuevas (el importador NO las mueve ni las modifica)
├── originals/                # copias de solo lectura, una por foto: originals/<sesión>/<image_id>.jpg
├── manifest/
│   ├── cards.json            # registro de cartas físicas (pc-001, pc-002...)
│   ├── sessions.json         # sesiones de captura (cada una con su ronda A/B)
│   └── photos.json           # una entrada por foto
├── references/               # imágenes digitales de referencia verificadas (pc-001.webp...) descargadas con references-fetch
├── splits/                   # particiones calibración/prueba por sesión; en el piloto, por ronda (<nombre>.json)
└── exports/                  # exportaciones (copias) para análisis
```
Los **originales** se copian una vez, se marcan de solo lectura y se verifican con SHA-256. Ninguna herramienta los mueve, edita ni borra.

## 2.2 Registro de cartas físicas (`cards.json`)
| Campo | Tipo | Notas |
|---|---|---|
| `card_ref` | texto | Clave **local** (`pc-001`). No es un id del catálogo |
| `name`, `set_name`, `collector_number` | texto | Obligatorios: lo que se lee en la carta física |
| `language` | `unknown` o código (`en`, `es`, `fr`...) | |
| `finish` | `normal` · `holo` · `reverse_holo` · `full_art` · `promo` · `otro` · `desconocido` | |
| `pilot_slot` | 1–30 o `null` | Posición en la plantilla (idempotencia de la importación) |
| `categories` | lista de `comun` · `holo` · `reverse_holo` · `full_art` · `promo` · `similar_dificil` · `antigua` · `variante_patron` · `otra` | **Solapables**: una carta puede estar en varias. `otra` = sin asignar (el estado del piloto lo marca como pendiente) |
| `pattern` | `ninguno` · `pokeball` · `masterball` · `desconocido` | Obligatorio declararlo (`desconocido` bloquea el piloto) |
| `variant_group` | texto o `null` | Agrupa la carta base y sus patrones del mismo número |
| `similar_to`, `difficulty_note` | texto o `null` | Para `similar_dificil`: con qué se confunde y por qué (la nota es obligatoria) |
| `tcgdex_card_id` | texto o `null` | Id de TCGdex si una persona lo confirmó |
| **`card_id`, `printing_id`, `variant_id`** | **UUID del catálogo o `null`** | **Desconocido = `null`. Nunca se rellenan por suposición** |
| `reference_object_key` | texto o `null` | Objeto de R2 que una persona comparó con la carta física y aceptó como referencia |
| `reference_verified_by`, `reference_verified_at`, `reference_verification_note` | texto o `null` | **Verificación manual de la referencia digital** (quién, cuándo y qué se comparó). Sin ella la carta no entra en la galería de evaluación |
| `reference_sha256` | texto o `null` | Se completa con `references-fetch` |
| `verification_status` | `unverified` · `verified` | `verified` exige `verified_by`, `verified_at` y `verification_note` (verificación **manual de la carta física**) |

No se admiten dos cartas con el mismo nombre, set, número, idioma, acabado y patrón (la misma carta física no se registra dos veces). Los tres ids del catálogo son `null` o un UUID válido; una cadena vacía o inventada se rechaza.

### Sesiones (`sessions.json`)
`capture_session_id` (único), `date`, `device_model`, `place`, `operator`, `notes` y **`round`**: `A` (calibración), `B` (evaluación final) o `null`. Una sesión pertenece a una sola ronda; las fotos verificadas del piloto exigen una sesión con ronda.

## 2.3 Registro de fotos (`photos.json`)
Campos solicitados (todos presentes en cada foto):

| Campo | Valores / formato | Desconocido |
|---|---|---|
| `image_id` | `img_` + 12 hex (clave local) | — |
| `card_id`, `printing_id`, `variant_id` | UUID o `null`; **copiados desde la carta** al verificar (`sync`) | `null` |
| `capture_session_id` | id de una sesión de `sessions.json` | — |
| `device_model` | texto (de la sesión; si falta, del EXIF; si no, `desconocido`) | `desconocido` |
| `lighting_condition` | `natural_directa` · `natural_sombra` · `interior_calida` · `interior_fria` · `flash` · `mixta` · `desconocida` | `desconocida` |
| `camera_angle` | `frontal` · `inclinada_leve` (≤15°) · `inclinada_media` (15–30°) · `inclinada_fuerte` (>30°) · `cenital` · `desconocido` | `desconocido` |
| `background` | `neutro_claro` · `neutro_oscuro` · `madera` · `mantel_estampado` · `alfombrilla` · `mano` · `otro` · `desconocido` | `desconocido` |
| `sleeve_condition` | `sin_funda` · `funda_transparente` · `funda_mate` · `toploader` · `desconocida` | `desconocida` |
| `focus_quality` | `buena` · `aceptable` · `borrosa` · `desconocida` (estimada al importar; `focus_quality_source` = `auto`/`manual`) | `desconocida` |
| `verified_by` | nombre de la persona que revisó la foto | `null` hasta verificar |
| `verification_status` | `pending` · `verified` · `rejected` · `needs_review` | — |
| `notes` | texto libre | `null` |

Campos adicionales: `card_ref` (clave local de la carta), `glare` (`ninguno`/`leve`/`fuerte`/`desconocido`), `card_corners` (opcional: 4 puntos
`[x, y]` anotados a mano para medir la detección y la perspectiva), `file` (`path`, `sha256`, `bytes`, `format`, `width`, `height`, `original_name`, `dhash`, `exif`),
`quality` (`sharpness`, `brightness`, `megapixels`, `glare_fraction`, `flags`), `possible_duplicates`, `imported_at`, `imported_by`.

### Estados de verificación de una foto
`pending` (recién importada, sin revisar) → `verified` (una persona comprobó que la foto es de esa carta, nítida y sin duplicado) ·
`needs_review` (la herramienta detectó resolución baja, desenfoque, poca/mucha luz o un posible duplicado entre sesiones) · `rejected`.
Una foto solo puede ser `verified` si **su carta está `verified`**, tiene `verified_by`, no tiene banderas bloqueantes (salvo `--accept-quality`/`--accept-duplicates`, que quedan en el historial de comandos) y **no supera 5 fotos verificadas por carta y ronda** (la herramienta se niega a verificar una sexta).

### Banderas de calidad (`quality.flags`)
`resolucion_baja` (< 720 px de lado corto, bloquea) · `resolucion_justa` (< 1080 px) · `borrosa` · `muy_oscura` · `sobreexpuesta` · `posible_reflejo` (informativa) ·
`contiene_gps` (informativa) · `posible_duplicado_entre_sesiones` · `carta_sin_verificar`. Los umbrales son **provisionales** (ver `quality.py`) y se ajustarán con el piloto.

## 2.4 Ejemplo (FICTICIO)
Ver `docs/dataset/example/manifest.json` y `manifest.csv`: cada fila lleva `_ficticio: true`, ids `EJEMPLO-*` y el aviso `DATOS FICTICIOS DE EJEMPLO`.
