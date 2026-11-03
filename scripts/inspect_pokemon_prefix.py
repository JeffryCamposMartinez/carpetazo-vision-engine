"""Inspección de solo lectura del prefijo Pokémon en el bucket (python -m scripts.inspect_pokemon_prefix)."""

from collections import Counter

from app.core.config import get_settings
from app.storage.factory import get_storage

PREFIX = "Carpetazo.cl/Pokemon/"

st = get_storage(get_settings())
objs = list(st.list_objects(PREFIX))
print("objetos:", len(objs))
print("extensiones:", dict(Counter(o.key.rpartition(".")[2] for o in objs)))
print("carpetas nivel 3:", dict(Counter(o.key.split("/")[2] for o in objs)))
print("carpetas nivel 4:", dict(Counter(o.key.split("/")[3] for o in objs if o.key.count("/") > 3)))
sets = Counter(o.key.split("/")[4] for o in objs if "_Images/" in o.key and o.key.endswith(".webp"))
print("sets con imágenes:", len(sets), "imágenes:", sum(sets.values()))
print("primeros sets:", list(sets.items())[:8])
jsons = [o for o in objs if o.key.endswith("data.json")]
print("data.json:", len(jsons))
if jsons:
    print(jsons[0].key)
    print(st.get(jsons[0].key, 200_000).decode("utf-8")[:1800])
print("ejemplos webp:", [(o.key.split("/", 3)[3], o.size) for o in objs if o.key.endswith(".webp")][:4])
