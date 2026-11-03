# 1. Protocolo de captura de fotografías (piloto)

> **Objetivo del piloto:** **30 cartas físicas en total** (no 30 por categoría), **verificadas una a una**, con **10 fotografías verificadas por carta**
> (5 en la ronda A y 5 en la ronda B), tomadas con **teléfonos normales**: 300 fotos como máximo.
> Es un piloto para probar el método y detectar problemas; **no** basta para afirmar que el sistema funciona con todo el catálogo Pokémon.
> No hace falta equipo profesional ni luz de estudio. Todo se queda en este PC (nada se sube a R2 ni a ningún servicio).
> Las decisiones metodológicas están en `06-revision-metodologica-piloto.md`; si algo aquí parece contradecirlo, manda el 06.
> Las plantillas y la lista de comprobación de la carta física están en `docs/dataset/piloto/`.

## 1.1 Qué necesitas
- Un teléfono (idealmente 2–3 modelos distintos entre todas las sesiones: iPhone con Safari y Android con Chrome son los objetivos).
- Las 30 cartas físicas, algunas **con funda** y otras sin ella (la misma carta se fotografía con y sin funda según la receta de 1.5).
- Fondos: hoja blanca o mantel claro, cartulina o tela negra/oscura, mesa de madera, tu mano.
- Una lámpara de escritorio (luz cálida) y, si la hay, luz natural de ventana.
- Una hoja de papel para anotar nombre, set y número de cada carta **antes** de fotografiarla.

## 1.2 Cuáles 30 cartas (objetivo inicial, categorías solapables)
Son **30 cartas físicas distintas en total**. Cada categoría busca **al menos 5 cartas**, pero **una carta puede estar en varias categorías**
(una holo promo difícil de distinguir cuenta en `holo`, `promo` y `similar_dificil`), por eso la suma de categorías puede pasar de 30.

| Categoría (`categorias`) | Objetivo | Para qué sirve |
|---|---|---|
| `comun` (sin brillo) | ≥ 5 | Caso base |
| `holo` | ≥ 5 | Reflejos del brillo |
| `reverse_holo` | ≥ 5 | Foil sobre el marco, no sobre la ilustración |
| `full_art` | ≥ 5 | Mucho color, poco texto |
| `promo` | ≥ 5 | Numeración distinta |
| `similar_dificil` | ≥ 5 | Mismo Pokémon en otro set, arte casi igual, marco antiguo... (exige `motivo_dificultad`) |
| Opcionales: `antigua`, `variante_patron` | — | Marco antiguo; Poké Ball / Master Ball |

- **Poké Ball / Master Ball:** inclúyelas si las tienes. La carta base y cada patrón son cartas físicas distintas y comparten `grupo_variante` (máximo recomendado: 2 grupos).
  Declara siempre `patron` (`ninguno`, `pokeball` o `masterball`). No bloquean el piloto si no las tienes.
- **Cartas desconocidas:** al evaluar se retiran de la galería algunas cartas verificadas, en **grupos disjuntos** (unas para calibrar el umbral y otras distintas para la prueba, ver `04`). No se marcan de forma especial al fotografiar.
- **Si no tienes suficientes cartas de una categoría, no la inventes ni sustituyas a la fuerza:** regístrala como **carencia** (`pilot-gap` o una fila con `sin_carta_motivo`).
  La carencia queda visible en el informe. Una carencia de categoría no reduce el requisito de **30 cartas verificadas**: si no llegas a 30, `pilot-status` seguirá diciendo **PILOTO INCOMPLETO** y hacer un piloto menor es una decisión tuya que se documenta aparte.

## 1.3 Antes de fotografiar: identificar y VERIFICAR cada carta
Usa la plantilla `docs/dataset/piloto/plantilla-30-cartas.csv` y la lista de comprobación de `docs/dataset/piloto/instrucciones-y-lista-de-verificacion.md`.
1. Mira la carta física y anota: **nombre, nombre/símbolo del set, número de colección (p. ej. 058/102), idioma, acabado, patrón** (obligatorio) y las categorías.
2. Regístrala (por CSV con `pilot-import-cards`, o con `card-add --categories holo promo --pattern ninguno ...`). Queda **sin verificar**.
3. Compárala con una segunda fuente (la web del catálogo o la caja). Solo si coincide: `card-verify pc-001 --by TuNombre --note "..."` (la nota dice qué comprobaste).
4. **Verifica la referencia digital a mano:** con la carta física delante, abre la imagen de R2 y compara ilustración, número, símbolo de set y patrón. Solo entonces:
   `card-reference pc-001 --key <clave completa> --by TuNombre --note "..."`. Sin esto la carta **no** entra en la galería de evaluación.
5. **Si no estás seguro de la impresión o la variante, déjala sin verificar y NO inventes ids.** Sus fotos no entrarán a la evaluación hasta que se verifique.
6. Los campos `card_id`, `printing_id` y `variant_id` (UUID del catálogo) se dejan **vacíos (`null`)** mientras no exista una asociación revisada por una persona. No se copian de otro lugar.

## 1.4 Rondas de captura (clave para evitar fuga de datos)
Cada carta se fotografía en **dos rondas independientes de 5 fotos**:
- **Ronda A = calibración** (elegir el umbral de rechazo y el margen; los pesos de DINOv2-small siguen **congelados**).
- **Ronda B = evaluación final** (se mide una vez con la configuración congelada).

Una **ronda** es una ocasión de captura distinta: otro día o, como mínimo, otra tanda con la carta **recolocada** y vuelta a enfocar. Dentro de una ronda las 5 tomas
varían luz, fondo y funda a propósito (ver 1.5). Cada ronda se registra como una o más **sesiones** con `session-add --round A` o `--round B`; una sesión no mezcla rondas.
- Las fotos de A y de B **nunca** se mezclan ni se repiten: no hay copias, recortes, giros, filtros ni «otra versión» de una toma. La herramienta lo comprueba
  (SHA-256 exacto y cuasi-duplicados entre rondas) y bloquea la partición si lo detecta.
- **Máximo 5 fotos verificadas por carta y ronda.** `review` se niega a verificar una sexta; las sobrantes se marcan `rejected`.
- Crea la sesión antes: `session-add --id piloto-A-1 --round A --date 2026-11-05 --device "iPhone 14" --place "escritorio, ventana al este"` (o importa `plantilla-sesiones.csv`).

## 1.5 Las 5 tomas de cada ronda (la misma receta en A y en B)
La ronda B **repite exactamente la misma receta** que la A. Si la B fuese más difícil, el umbral calibrado no serviría y se confundiría el desajuste de distribución con un fallo del modelo.

| Toma | Descripción | Luz | Ángulo | Fondo | Funda |
|---|---|---|---|---|---|
| T1 | Frontal, carta centrada y entera | natural directa | frontal | neutro claro | sin funda |
| T2 | Frontal | interior cálida (lámpara) | frontal | madera | sin funda |
| T3 | Inclinada ~25° (si es holo/reverse: busca que se vea el brillo) | natural a la sombra | inclinada_media | neutro oscuro | sin funda |
| T4 | Con funda, reflejo leve | natural | frontal | neutro claro | funda transparente |
| T5 | Sostenida con la mano | cualquiera | frontal | mano | sin funda |

Puedes variar el plan, pero **igual en ambas rondas**, y **anota siempre las condiciones reales** (el campo vale más que la intención). Para cartas ya enfundadas, haz T1–T3 con la funda.

## 1.6 Reglas de cámara
- Modo foto normal. **Sin** zoom digital, sin HDR agresivo, sin filtros, sin modo retrato ni «mejora» automática.
- Toca la carta en la pantalla para enfocar y espera un instante. La carta debe ocupar entre el 40 % y el 80 % de la altura de la imagen.
- Formato **JPEG** (iPhone: Ajustes › Cámara › Formatos › *Más compatible*). El HEIC **no se admite**.
- No recortes ni edites. Conserva los nombres originales (`IMG_1234.jpg`).
- Desactiva la ubicación de la cámara si puedes (las fotos pueden llevar GPS en el EXIF; el dataset lo detecta y avisa).
- Evita tapar las esquinas con los dedos (salvo la toma de la mano), y evita tu sombra sobre la carta (salvo que lo anotes).

## 1.7 Guía visual
```
ENCUADRE CORRECTO                                   NO HACER
┌─────────────────────┐                             ┌─────────────────────┐
│                     │                             │   ┌──────┐          │
│     ┌─────────┐     │                             │   │ carta│ ← cortada│
│     │         │     │                             │   │      │          │
│     │  CARTA  │     │                             │ ──┘──────┘          │
│     │         │     │                             │  esquinas fuera     │
│     └─────────┘     │                             └─────────────────────┘
│  40–80 % del alto   │
└─────────────────────┘

ÁNGULOS (vista lateral)                         LUZ Y REFLEJOS
 frontal        inclinada_leve  inclinada_media   inclinada_fuerte
 0°             ~10–15°         ~25°              ~40°           ventana ☀  →  evita que el reflejo caiga en el centro
 ┃              ╱               ╱                 ╱              (un reflejo leve con funda, toma T4, es aceptable)
 ┃ teléfono     ╱               ╱                 ╱              no pongas el teléfono entre la luz y la carta
```

## 1.8 Lista de comprobación (imprimible)
**Antes de empezar**
- [ ] La carta está identificada y anotada (nombre, set, número, idioma, acabado, patrón, categorías) y **verificada con la lista de `docs/dataset/piloto/`**. Si no, queda sin verificar.
- [ ] La referencia digital está comparada a mano con la carta física (o la fila queda sin referencia y la carta no entra en la evaluación).
- [ ] Sesión creada (`session-add --round A|B`) con teléfono, lugar y fecha.
- [ ] Lente limpia, ubicación de la cámara desactivada, formato JPEG.

**Durante cada toma**
- [ ] La carta se ve entera y nítida; esquinas visibles.
- [ ] Anotada la luz, el ángulo, el fondo y la funda reales.
- [ ] Si es holo/reverse: al menos 2 tomas con el brillo visible.

**Al terminar la ronda**
- [ ] Copiar las fotos a `datasets/real-photos/inbox/<sesion>/<carta>/` (sin renombrar ni editar).
- [ ] Importar con `dataset.ps1 import` (primero `--dry-run`) y revisar las fotos marcadas.
- [ ] Cada carta tiene exactamente 5 fotos verificadas en esta ronda.

## 1.9 Qué NO hacer
- No inventes la impresión ni la variante; no copies ids de otro lugar.
- No uses capturas de pantalla, fotos de imágenes de internet ni fotos editadas.
- No subas las fotos a R2, a la nube ni a chats. Se quedan en este equipo (respaldo local: ver `03-herramienta-y-respaldo.md`).
- No fotografíes a personas ni documentos junto a las cartas.
