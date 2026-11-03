# 1. Protocolo de captura de fotografías (piloto)

> **Objetivo del piloto:** ≈ 300 fotografías de **30 cartas físicas verificadas** (≈ 10 por carta) tomadas con **teléfonos normales**.
> Es un piloto para probar el método y detectar problemas; **no** basta para afirmar que el sistema funciona con todo el catálogo Pokémon.
> No hace falta equipo profesional ni luz de estudio. Todo se queda en este PC (nada se sube a R2 ni a ningún servicio).

## 1.1 Qué necesitas
- Un teléfono (idealmente 2–3 modelos distintos entre todas las sesiones: iPhone con Safari y Android con Chrome son los objetivos).
- Las 30 cartas físicas, algunas **con funda** y otras sin ella (la misma carta se fotografía con y sin funda cuando sea posible).
- Fondos: hoja blanca o mantel claro, cartulina o tela negra/oscura, mesa de madera, un mantel o tela estampada, una alfombrilla de juego, tu mano.
- Una lámpara de escritorio (luz cálida) y, si la hay, luz natural de ventana. El flash del teléfono para algunas tomas.
- Una hoja de papel para anotar nombre, set y número de cada carta **antes** de fotografiarla.

## 1.2 Cuáles 30 cartas (cuota sugerida)
| Categoría | Cartas | Para qué sirve |
|---|---|---|
| Comunes / sin brillo | 6 | Caso base |
| Full art / secret rare | 4 | Mucho color, poco texto |
| Holo | 4 | Reflejos del brillo |
| Reverse holo | 4 | Foil sobre el marco, no sobre la ilustración |
| Promos | 3 | Numeración distinta |
| Antiguas (Base, Jungle, Fósil, e-Card...) | 3 | Marco antiguo, desgaste |
| Variantes de patrón (Poké Ball / Master Ball): **2 cartas, la base y cada patrón** | 4 | Probar si se distinguen las variantes |
| Mismo Pokémon, otro set / ilustración parecida | 2 | Confusión por arte parecido |
| **Total** | **30** | |

Incluye al menos **5 cartas que luego retiraremos del índice** para probar el rechazo de cartas desconocidas (no las marques de forma especial: se eligen
al evaluar con `--holdout-cards`). Si una categoría no la tienes, sustitúyela y anótalo.

## 1.3 Antes de fotografiar: identificar y VERIFICAR cada carta
1. Mira la carta física y anota: **nombre, nombre/símbolo del set, número de colección (p. ej. 058/102), idioma, acabado (normal / holo / reverse holo / full art / promo)** y, si existe, el patrón (Poké Ball / Master Ball).
2. Regístrala: `.\scripts\dev\dataset.ps1 card-add --name ... --set ... --number ... --language es --finish holo --category holo`.
3. Compárala con una segunda fuente (la web del catálogo o la caja del producto). Solo si coincide: `.\scripts\dev\dataset.ps1 card-verify pc-001 --by TuNombre --note "comparada con la carta física: nombre, símbolo de set, número e idioma"`.
4. **Si no estás seguro de la impresión o la variante, déjalo sin verificar y NO inventes ids.** La carta puede fotografiarse, pero sus fotos no entrarán a la evaluación hasta que se verifique.
5. Los campos `card_id`, `printing_id` y `variant_id` (UUID del catálogo) se dejan **vacíos** mientras no exista una asociación revisada por una persona.

## 1.4 Sesiones de captura (clave para evitar fuga de datos)
Una **sesión** es una tanda continua en un mismo lugar, con el mismo fondo y la misma luz, un solo teléfono y un mismo día.
- Cada carta se fotografía en **al menos 2 sesiones independientes** (otro día o, como mínimo, otro lugar/fondo/luz, **volviendo a colocar la carta** y volviendo a enfocar).
- Las fotos de una ráfaga o de una misma colocación de la carta son de **la misma sesión**.
- **Nunca** copies, recortes, gires, filtres ni vuelvas a guardar una foto para «hacer otra»: se detectaría como duplicado y se marcaría.
- Crea la sesión antes: `.\scripts\dev\dataset.ps1 session-add --id s-2026-11-05-a --date 2026-11-05 --device "iPhone 14" --place "escritorio, ventana al este"`.

## 1.5 Las 10 tomas por carta (5 en cada una de dos sesiones)
| # | Sesión | Toma | Luz | Ángulo | Fondo | Funda |
|---|---|---|---|---|---|---|
| A1 | A | Frontal, carta centrada y entera | natural directa | frontal | neutro claro | sin funda |
| A2 | A | Frontal | interior cálida (lámpara) | frontal | madera | sin funda |
| A3 | A | Inclinada ~25° (si es holo/reverse: busca que se vea el brillo) | natural a la sombra | inclinada_media | neutro oscuro | sin funda |
| A4 | A | Con funda, reflejo leve | natural | frontal | neutro claro | funda transparente |
| A5 | A | Sostenida con la mano | cualquiera | frontal | mano | sin funda |
| B1 | B | Frontal | interior fría o flash | frontal | mantel estampado | funda transparente |
| B2 | B | Inclinada ~40° | interior cálida | inclinada_fuerte | alfombrilla | sin funda |
| B3 | B | **Encuadre imperfecto**: carta cerca del borde del encuadre y girada ~15° (sin cortar esquinas) | natural | frontal | madera | sin funda |
| B4 | B | **Reflejo fuerte** con funda (que se vea el foil/brillo) | mixta | inclinada_leve | neutro oscuro | funda transparente |
| B5 | B | Vista casi cenital, lejos (la carta ocupa ~40 % de la altura) | mixta | cenital | madera | sin funda |

Puedes variar el plan, pero **anota siempre las condiciones reales** (el campo vale más que la intención). Para cartas ya enfundadas, haz A1–A3 con la funda.

## 1.6 Reglas de cámara
- Modo foto normal. **Sin** zoom digital, sin HDR agresivo, sin filtros, sin modo retrato ni «mejora» automática.
- Toca la carta en la pantalla para enfocar y espera un instante. La carta debe ocupar entre el 40 % y el 80 % de la altura de la imagen.
- Formato **JPEG** (iPhone: Ajustes › Cámara › Formatos › *Más compatible*). El HEIC **no se admite**.
- No recortes ni edites. Conserva los nombres originales (`IMG_1234.jpg`).
- Desactiva la ubicación de la cámara si puedes (las fotos pueden llevar GPS en el EXIF; el dataset lo detecta y avisa).
- Evita tapar las esquinas con los dedos (salvo la toma de la mano), y evita tu sombra sobre la carta (salvo que lo anotes).

## 1.7 Guía visual
```
ENCUADRE CORRECTO                 ENCUADRE IMPERFECTO (toma B3)       NO HACER
┌─────────────────────┐           ┌─────────────────────┐             ┌─────────────────────┐
│                     │           │ ╱‾‾‾‾╲              │             │   ┌──────┐          │
│     ┌─────────┐     │           │╱      ╲  carta       │             │   │ carta│ ← cortada│
│     │         │     │           ││ carta │  girada      │             │   │      │          │
│     │  CARTA  │     │           │╲      ╱  ~15°        │             │ ──┘──────┘          │
│     │         │     │           │ ╲____╱  cerca del    │             │  esquinas fuera     │
│     └─────────┘     │           │         borde        │             └─────────────────────┘
│  40–80 % del alto   │           └─────────────────────┘
└─────────────────────┘

ÁNGULOS (vista lateral)                         LUZ Y REFLEJOS
 frontal        inclinada_leve  inclinada_media   inclinada_fuerte
 0°             ~10–15°         ~25°              ~40°           ventana ☀  →  evita que el reflejo caiga en el centro
 ┃              ╱               ╱                 ╱              (salvo toma B4, donde se busca a propósito)
 ┃ teléfono     ╱               ╱                 ╱              no pongas el teléfono entre la luz y la carta
```

## 1.8 Lista de comprobación (imprimible)
**Antes de empezar**
- [ ] La carta está identificada y anotada (nombre, set, número, idioma, acabado, patrón). ¿Verificada con dos fuentes? Si no, queda sin verificar.
- [ ] Sesión creada (`session-add`) con teléfono, lugar y fecha.
- [ ] Lente limpia, ubicación de la cámara desactivada, formato JPEG.

**Durante cada toma**
- [ ] La carta se ve entera (salvo la nota de la toma) y nítida; esquinas visibles.
- [ ] Anotada la luz, el ángulo, el fondo y la funda reales.
- [ ] Si es holo/reverse: al menos 2 tomas con el brillo visible.

**Al terminar la sesión**
- [ ] Copiar las fotos a `datasets/real-photos/inbox/<sesion>/<carta>/` (sin renombrar ni editar).
- [ ] Importar con `dataset.ps1 import` y revisar las fotos marcadas.
- [ ] Cada carta tiene fotos en 2 sesiones distintas.

## 1.9 Qué NO hacer
- No inventes la impresión ni la variante; no copies ids de otro lugar.
- No uses capturas de pantalla, fotos de imágenes de internet ni fotos editadas.
- No subas las fotos a R2, a la nube ni a chats. Se quedan en este equipo (respaldo local: ver `03-herramienta-y-respaldo.md`).
- No fotografíes a personas ni documentos junto a las cartas.
