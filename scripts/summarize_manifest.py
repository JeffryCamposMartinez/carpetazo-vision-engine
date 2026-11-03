"""Resumen legible del manifiesto: python -m scripts.summarize_manifest"""

import json
from collections import Counter
from pathlib import Path

REPORTS = Path("evaluation/reports")
rows = json.loads((REPORTS / "r2-manifest.json").read_text(encoding="utf-8"))
sets = json.loads((REPORTS / "r2-sets-report.json").read_text(encoding="utf-8"))

for status in ("matched", "probable", "ambiguous", "unmatched"):
    group = [r for r in rows if r["status"] == status]
    print(f"\n== {status}: {len(group)}")
    print("  motivos:", Counter((r["method"] or r["reason"])[:70] for r in group).most_common(5))
    for r in group[:: max(1, len(group) // 3)][:3]:
        print("  -", r["object_key"].split("Pokemon_Images/")[-1].split("/")[0], "|", r["extracted_name"], r["number_raw"],
              "->", r["catalog_card_id"] or "-", "|", r["reason"][:90])  # fmt: skip

print("\n== sets: ", sets["counts"], sets["images_by_status"])
for key in ("probable", "unresolved", "ambiguous"):
    print(f"-- {key} (ejemplos)")
    for s in sets[key][:4]:
        near = s["candidates"][0] if s["candidates"] else {}
        print(
            f"  {s['folder']!r} ({s['images']} img) -> más cercano: {near.get('name')!r} {near.get('score')} {near.get('id')}"
        )
print("variantes/duplicados:", dict(list(sets["possible_duplicates_or_variants"].items())[:3]))
print("sets del catálogo sin carpeta:", len(sets["unmapped_catalog_sets"]))
