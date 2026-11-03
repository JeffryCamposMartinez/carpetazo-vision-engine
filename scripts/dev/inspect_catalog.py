# ruff: noqa: E501
"""Resumen de solo lectura del catálogo importado y de las propuestas de imagen (VISION_DATABASE_URL)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.models import (  # noqa: E402
    Card,
    CardPrinting,
    ExternalReference,
    ImageAssociation,
    ImageMappingProposal,
    PrintingVariant,
)


def main() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as c:
        for model in (Card, CardPrinting, PrintingVariant, ExternalReference, ImageMappingProposal, ImageAssociation):
            print(f"{model.__tablename__:26} {c.scalar(select(func.count()).select_from(model))}")
        print(
            "\nVariantes por origen:",
            dict(c.execute(select(PrintingVariant.source, func.count()).group_by(PrintingVariant.source)).all()),
        )
        print(
            "Propuestas por resultado:",
            dict(
                c.execute(
                    select(ImageMappingProposal.outcome, func.count()).group_by(ImageMappingProposal.outcome)
                ).all()
            ),
        )
        print("\nEjemplo de variantes (impresión con patrones foil):")
        rows = c.execute(
            select(
                CardPrinting.external_id,
                PrintingVariant.variant_type,
                PrintingVariant.variant_subtype,
                PrintingVariant.foil,
                PrintingVariant.stamp,
            )
            .join(PrintingVariant, PrintingVariant.printing_id == CardPrinting.id)
            .where(CardPrinting.external_id.in_(["sv10.5b-024", "base1-1"]))
            .order_by(CardPrinting.external_id, PrintingVariant.variant_type, PrintingVariant.foil)
        )
        for r in rows:
            print("  ", tuple(r))


if __name__ == "__main__":
    main()
