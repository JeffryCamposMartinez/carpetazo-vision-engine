# Modelo del catálogo

Migraciones 0001–0004. Las anteriores no se editaron; la 0004 solo agrega.

## Entidades

| Entidad | Tabla | Qué es |
|---|---|---|
| Game | `games` | Juego TCG (`slug` único). |
| Set | `sets` | Una expansión **tal como la cataloga un idioma**: único por (juego, id del proveedor, idioma). TCGdex devuelve conjuntos y conteos distintos por idioma, por eso no se unifican. |
| Card | `cards` | Identidad lógica **según el proveedor**: una por id de carta de TCGdex (p. ej. `base1-58`), igual en todos los idiomas. `identity_basis = provider_card_id`. |
| CardPrinting | `card_printings` | Aparición de una carta en un set: único por (carta, set). Lleva `language` (idioma de la impresión declarado por el proveedor) y `catalog_language` (idioma del catálogo de origen). |
| PrintingVariant | `printing_variants` | Variante física o visual de una impresión (varias por impresión). |
| ExternalReference | `external_references` | Ids externos (TCGdex, TCGplayer, Cardmarket) de cualquier entidad. |
| CardEquivalence | `card_equivalences` | Relación **opcional y revisable** entre dos `Card` (reimpresión / misma carta). Nunca se llena sola. |
| CatalogSource | `catalog_sources` | Procedencia: una por set e idioma, con licencia del dato y atribución. |

## Identidad de cartas (sin inventar una global)

TCGdex identifica una carta por set + número. Una reimpresión de Charizard en otro set tiene otro id, y TCGdex no da un
identificador global entre reimpresiones. Por eso **cada id de TCGdex es una `Card`** y las reimpresiones no se fusionan
automáticamente. Cuando haya evidencia (mismo nombre, mismo `dexId`, revisión humana) se registra una fila en
`card_equivalences` con `status = proposed` → `confirmed`/`rejected`, evidencia y revisor.

## Variantes

`printing_variants` guarda por variante: `variant_type`, `variant_subtype`, `foil` (patrón: `pokeball`, `masterball`),
`stamp` (sellos ordenados y unidos con `+`), `size`, `language` (solo si está verificado; `NULL` = sin verificar),
`source` y `external_id`. Los atributos vacíos son `''`, no `NULL`.

La unicidad es `(printing_id, source, external_id)`: TCGdex da un `variantId` por variante. Un índice único sobre
(tipo, subtipo, sello) **sería incorrecto**: p. ej. `sv10.5b-024` tiene tres variantes `reverse` que solo se distinguen por
`foil` (ninguna, `pokeball`, `masterball`), y `base1-1` tiene `holo/shadowless` con y sin sello `1st-edition`. Si el proveedor
no da `variantId`, el `external_id` es una clave derivada de los atributos. Si solo hay banderas (`variants: {holo: true}`),
se crean variantes gruesas con `source = tcgdex_flags`.

No se asume que una combinación exista: solo se guardan las que el proveedor envía.

## Idiomas

Tres cosas distintas:

1. **Idioma del catálogo**: `sets.language`, `card_printings.catalog_language`.
2. **Idioma de la impresión**: `card_printings.language`.
3. **Idioma de una imagen**: `image_associations.image_language`, `card_images.language`,
   `image_mapping_proposals.image_language`. Vale `unknown` hasta que se verifique. Las imágenes de R2 **no** se asumen en inglés.

## Referencias externas

Exactamente una de las columnas `game_id/set_id/card_id/printing_id/variant_id` está informada (CHECK). La unicidad es por
entidad: un mismo id externo puede apuntar a varias entidades (p. ej. un `productId` de TCGplayer compartido por la variante
normal y la reverse, o por dos variantes Shadowless). El importador lo informa como `id_externo_compartido`.
Alcances (`scope`): ids de carta de TCGdex = `''` (válidos en todos los idiomas); ids de impresión, set y variante = idioma.

## Imágenes: propuestas separadas de asociaciones aprobadas

- `image_mapping_proposals`: lo que el código **propone** para cada objeto de R2 (`unique_candidate`, `multiple_candidates`,
  `no_candidate`) con candidatos, banderas y evidencia. Se carga con `images-load-proposals`. No decide nada.
- `image_associations`: lo **aprobado** por una persona (`images-apply`), con `variant_id` opcional e `image_language`.

Banderas: `unknown_language`, `variant_needed` (calificadores como Prerelease o Master Ball Pattern: no deben ir a la
impresión base), `duplicate_target` / `duplicate_across_objects` (varios objetos para la misma carta), `probable_match`,
`set_alias_not_confirmed`, `set_unresolved`, `folder_not_single_set` (carpetas agrupadas o kits que mezclan sets),
`card_not_in_catalog`. Un alias de carpeta nunca fija la impresión de todas sus imágenes.

## Cambios respecto al modelo anterior (0001–0003)

- `card_printings.variant` (texto único) queda sin uso; las variantes pasan a `printing_variants`.
- `card_printings`: + `catalog_language`, + único `(card_id, set_id)`. La migración se niega si hay duplicados.
- `cards`: + `identity_basis`. `card_images` e `image_associations`: + idioma; `image_associations`: + `variant_id`.
- Tablas nuevas: `printing_variants`, `external_references`, `card_equivalences`, `import_errors`, `image_mapping_proposals`.

## Limitaciones y decisiones pendientes

- Falta definir cuándo (y quién) confirma una `CardEquivalence`.
- Los idiomas distintos del inglés están soportados por el diseño y el importador, pero **solo se probaron con datos
  sintéticos** en las pruebas automáticas; no se importó ningún otro idioma real.
- El catálogo en inglés completo no se ha importado (ver `docs/catalog-import-runbook.md`).
- Los índices vectoriales (HNSW) no existen: dependen del modelo de embeddings que se elija.
- `pricing` de TCGdex se ignora a propósito (volátil); se cuenta en el reporte.
