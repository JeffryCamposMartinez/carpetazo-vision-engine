# ruff: noqa: E501
"""E6: resolución de identidad. Convierte coincidencias del índice (E5) en identidades del catálogo de Carpetazo, SIN decidir aceptación ni confianza (eso es E7).

Reglas:
- La identidad de la impresión es el `productId` de Carpetazo (junto con su `categoryId`); la edición es `groupId`. Edición y variante no se confunden.
- Solo una referencia VERIFICADA vinculada a un producto vigente del espejo produce `confirmable=True`. Candidatas y ambiguas nunca son confirmables, sea cual sea la similitud.
- La variante es siempre `unknown`: Carpetazo no tiene entidad de variante y una coincidencia visual no prueba la variante. `reference_hints` solo informa lo que se
  anotó al verificar la carta física de referencia (acabado/patrón); no es una afirmación sobre la foto consultada.
- Si no hay información suficiente se devuelve una identificación `incomplete` o `unknown`; nada se inventa. Solo lectura: no escribe en ninguna base.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database.mirror_models import MirrorCard, MirrorSet
from app.database.reference_models import AmbiguousReference, CandidateReference, VerifiedReference
from app.recognition.index.store import Match

STATUSES = ("resolved", "incomplete", "unverified_candidate", "ambiguous", "unknown")


@dataclass(frozen=True)
class ResolvedIdentity:
    status: str  # resolved | incomplete | unverified_candidate | ambiguous | unknown
    confirmable: bool  # True solo para referencia verificada + producto vigente del espejo
    tier: str | None
    reference_id: str | None
    similarity: float | None
    category_id: int | None = None
    product_id: int | None = None
    name: str | None = None
    number: str | None = None
    rarity: str | None = None
    set: dict | None = None  # {group_id, name, abbreviation}: la EDICIÓN (no la variante)
    game_name: str | None = None
    language: str | None = None
    catalog_relation: str | None = None  # catalog_product | spanish_view_of_english_product | spanish_only
    variant: str = "unknown"
    variant_evidence: None = None
    reference_hints: dict | None = None  # lo anotado al verificar la carta de referencia; NO es la variante de la foto consultada
    catalog: dict | None = None  # {last_seen_at, retired, digest}
    reasons: tuple[str, ...] = field(default_factory=tuple)


def unknown(reason: str) -> ResolvedIdentity:
    return ResolvedIdentity("unknown", False, None, None, None, reasons=(reason,))


def _product(session: Session, category_id: int | None, product_id: int | None):
    if category_id is None or product_id is None:
        return None, None
    card = session.get(MirrorCard, (category_id, product_id))
    return card, (session.get(MirrorSet, (card.category_id, card.group_id)) if card else None)


def resolve_match(session: Session, match: Match) -> ResolvedIdentity:
    tier = match.tier
    if tier == "verified":
        ref = session.get(VerifiedReference, match.reference_id)
        cat, prod = (ref.category_id, ref.product_id) if ref else (None, None)
    elif tier == "candidate":
        ref = session.get(CandidateReference, match.reference_id)
        cat, prod = (ref.claimed_category_id, ref.claimed_product_id) if ref else (None, None)
    elif tier == "ambiguous":
        ref = session.get(AmbiguousReference, match.reference_id)
        cat, prod = (ref.claimed_category_id, ref.claimed_product_id) if ref else (None, None)
    else:
        raise ValueError(f"nivel desconocido: {tier!r}")
    rid = str(match.reference_id)
    if ref is None:
        return ResolvedIdentity("incomplete", False, tier, rid, match.similarity, reasons=("la referencia ya no existe en el índice",))
    card, set_ = _product(session, cat, prod)
    reasons: list[str] = []
    base = dict(tier=tier, reference_id=rid, similarity=match.similarity)
    if card is None:
        reasons.append("el producto no está en el espejo del catálogo de Carpetazo" if cat is not None else "la referencia no tiene un producto de Carpetazo asociado")
        return ResolvedIdentity("incomplete" if tier == "verified" else ("ambiguous" if tier == "ambiguous" else "unverified_candidate"), False, category_id=cat, product_id=prod, reasons=tuple(reasons), **base)
    data = dict(
        category_id=card.category_id, product_id=card.product_id, name=card.name, number=card.number, rarity=card.rarity, game_name=card.game_name, language=card.language, catalog_relation=card.relation,
        set={"group_id": card.group_id, "name": set_.name, "abbreviation": set_.abbreviation} if set_ else {"group_id": card.group_id, "name": None, "abbreviation": None},
        catalog={"last_seen_at": str(card.last_seen_at), "retired": card.retired_at is not None, "digest": card.digest},
    )  # fmt: skip
    if tier == "verified":
        data["reference_hints"] = {"finish": ref.finish, "pattern": ref.pattern, "verified_by": ref.verified_by, "note": "descriptores de la carta de referencia; la variante de la foto consultada no se afirma"}
        if card.retired_at is not None:
            return ResolvedIdentity("incomplete", False, reasons=("el producto fue retirado del catálogo de Carpetazo",), **base, **data)
        return ResolvedIdentity("resolved", True, reasons=("referencia verificada vinculada a un producto vigente de Carpetazo", "variante desconocida: sin evidencia ni entidad de variante"), **base, **data)
    if tier == "candidate":
        return ResolvedIdentity("unverified_candidate", False, reasons=("vínculo afirmado por Carpetazo, no revisado por una persona: nunca confirma una identidad",), **base, **data)
    return ResolvedIdentity("ambiguous", False, reasons=(f"asociación ambigua: {ref.ambiguity_reason}", "nunca confirma una identidad"), **base, **data)


def resolve_matches(session: Session, matches: list[Match]) -> list[ResolvedIdentity]:
    """Resuelve y conserva el orden; si varios resultados caen en el mismo producto y nivel se queda el de mayor similitud."""
    if not matches:
        return [unknown("el índice no devolvió ninguna coincidencia")]
    out, seen = [], set()
    for m in matches:
        r = resolve_match(session, m)
        key = (r.tier, r.category_id, r.product_id) if r.product_id is not None else (r.tier, r.reference_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
