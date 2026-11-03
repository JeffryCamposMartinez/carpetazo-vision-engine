# 4. Diseño del dataset de referencia, entrenamiento y evaluación

## Tres conjuntos separados
| Conjunto | Contenido | Uso | Reglas |
|---|---|---|---|
| **A. Referencias** | Imágenes digitales de cartas, **con asociación aprobada** a una impresión (y variante si aplica) | Índice de búsqueda; origen de las escenas sintéticas | Solo `image_associations` aprobadas. Procedencia y licencia registradas |
| **B. Entrenamiento** | Escenas sintéticas (de A) + fotos reales de entrenamiento | Ajuste fino (si hace falta) y calibración | Nunca contiene cartas ni sesiones del conjunto C |
| **C. Evaluación** | Fotos reales de teléfono, tomadas aparte | Métricas finales; no se toca para ajustar | Sesiones y escenas distintas de B; congelado por versión |

## Evitar fuga de datos
- La unidad de partición es la **sesión de captura** (misma escena, misma tanda de fotos, mismo teléfono y fondo): todas las fotos de una
  sesión van al mismo conjunto. Nunca repartir fotos de una misma captura entre B y C.
- Para medir cartas **nunca vistas**, reservar además un subconjunto de cartas (`card_id`) que no aparece en B en absoluto.
- Las referencias (A) de las cartas de C **sí** están en el índice (es el caso de uso real); las cartas «desconocidas» de C no tienen referencia.
- Reimpresiones con arte idéntico se agrupan en la misma partición (grupo de arte = `art_group_id`) para no inflar resultados.
- Calibrar umbrales con un subconjunto de validación distinto de C.

## Esquema de anotación (una fila por foto)
```json
{
  "image_id": "uuid",
  "session_id": "uuid",                // unidad de partición
  "source": "phone_capture | synthetic | catalog",
  "device": "iPhone 14 / Pixel 8 ...",
  "file_sha256": "...",
  "labels": {
    "card_id": "uuid | null",          // null = carta fuera del catálogo
    "printing_id": "uuid | null",
    "variant_id": "uuid | null",
    "language": "en | fr | ... | unknown",
    "label_confidence": "verified | probable | unknown",
    "annotated_by": "persona", "annotated_at": "2026-..."
  },
  "capture": {
    "background": "madera | mantel | alfombrilla | blanco | negro | mano | funda",
    "lighting": "natural | cálida | fría | flash | sombra",
    "glare": "none | leve | fuerte", "sleeve": true,
    "angle_deg": 0, "distance": "cerca | media | lejos", "blur": "none | leve | fuerte"
  },
  "kind": "common | full_art | holo | reverse_holo | promo | old | pattern_poke_ball | pattern_master_ball | similar_art | unknown_card | not_a_card",
  "art_group_id": "uuid"
}
```
`label_confidence = verified` exige comprobación humana contra la carta física. **Solo `verified` cuenta como verdad.**

## Tamaño mínimo recomendado de la evaluación real (C)
- ≥ 300 fotos, ≥ 150 cartas distintas, ≥ 3 teléfonos (iPhone Safari y Android Chrome), ≥ 5 fondos y ≥ 4 condiciones de luz.
- ≥ 30 cartas con variantes (holo/reverse/patrones) fotografiadas con y sin reflejo; ≥ 40 cartas ausentes del índice; ≥ 20 objetos
  que no son cartas.
- Con 300 fotos, un Top-1 del 95 % tiene un intervalo de Wilson al 95 % de ≈ ±2,5 puntos; con 100 fotos sería ≈ ±4,5. Reportar siempre el intervalo.

## Captura y etiquetado
Herramienta mínima (formulario web móvil o carpeta con hoja de cálculo): el operador elige la carta del catálogo (o «desconocida»), toma 3–5
fotos y completa el bloque `capture`. Cada sesión se registra antes de fotografiar. Almacenamiento fuera de Git; consentimiento
explícito si alguna foto proviene de usuarios (política de privacidad).

## Procedencia y licencia (obligatorio antes de entrenar)
Registrar por imagen: origen, términos y fecha de revisión. Las imágenes de R2 provienen de CDN de TCGplayer y **no se han
revisado**; no entrenar ni distribuir derivados hasta confirmarlo. Las escenas sintéticas heredan las condiciones de su imagen de origen.
