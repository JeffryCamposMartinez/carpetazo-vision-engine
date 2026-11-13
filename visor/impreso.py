# -*- coding: utf-8 -*-
"""Lectura del número impreso de una carta, por juego. Cada TCG imprime su identificador en otro lugar y con otro formato:
no hay una expresión regular universal. Portado del visor del navegador (Visor2: src/engine/games/adapters.js y common.js).

- REGIONS: zona (x, y, ancho, alto, de 0 a 1 sobre la carta de frente) donde va el número de cada juego.
- parse_ocr(juego, texto) -> (número canónico, sigla de edición) o (None, None).
- constrain_to_candidates: corrige una lectura dudosa SOLO hacia números de los candidatos visuales (nunca inventa uno).
Las zonas vienen del visor del navegador y están por validar con fotos reales (el laboratorio sirve para eso).
"""
import re

LANG_CODES = {"EN", "ES", "FR", "DE", "IT", "PT", "JP", "JA", "KO", "ZH", "SP"}
GAME_OF_CAT = {3: "pokemon", 100: "pokemon", 85: "pokemon", 1: "magic", 2: "yugioh", 68: "onepiece", 89: "riftbound", 71: "lorcana", 63: "digimon", 99: "myl"}
REGIONS = {
    "pokemon": [(0.02, 0.90, 0.46, 0.09), (0.52, 0.90, 0.46, 0.09)],
    "magic": [(0.03, 0.925, 0.55, 0.07)],
    "yugioh": [(0.50, 0.722, 0.47, 0.055)],
    "onepiece": [(0.45, 0.90, 0.52, 0.09)],
    "riftbound": [(0.03, 0.925, 0.60, 0.07)],
    "lorcana": [(0.02, 0.925, 0.60, 0.07)],
    "digimon": [(0.55, 0.02, 0.43, 0.08), (0.50, 0.90, 0.48, 0.09)],
}

TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
CONFUSABLE = {"O0", "0O", "I1", "1I", "L1", "1L", "S5", "5S", "B8", "8B", "Z2", "2Z", "G6", "6G", "D0", "0D", "Q0", "0Q", "I|", "|I", "1|", "|1"}


def up(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").upper())


def fix_digits(s: str) -> str:
    """Confusiones típicas del OCR (O→0, I→1, S→5…): solo donde el formato exige dígitos."""
    return "".join(TO_DIGIT.get(c.upper(), c) for c in str(s))


def trim_zeros(token: str) -> str:
    """«008» → «8»; conserva prefijo y sufijo («TG05» → «TG5»)."""
    return re.sub(r"^([A-Z]*)0*(\d+)([A-Z★*]*)$", lambda m: m.group(1) + m.group(2) + m.group(3), str(token).upper())


def ocr_distance(a: str, b: str) -> float:
    """Levenshtein donde cambiar caracteres que el OCR confunde cuesta 0,3 en vez de 1."""
    m, n = len(a), len(b)
    if not m or not n:
        return float(max(m, n))
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i]
        for j in range(1, n + 1):
            sub = 0 if a[i - 1] == b[j - 1] else 0.3 if a[i - 1] + b[j - 1] in CONFUSABLE else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub))
        prev = cur
    return prev[n]


def compare_canon(a, b) -> str:
    """'exact' | 'partial' (coincide la parte principal, p. ej. sin el total) | 'none' | 'unknown'."""
    if not a or not b:
        return "unknown"
    if a == b:
        return "exact"
    a0, b0 = a.split("/")[0], b.split("/")[0]
    return "partial" if a0 == b0 and (("/" in a) != ("/" in b)) else "none"


def constrain_to_candidates(read, candidates, max_distance: float = 1.0):
    """Elige entre los números de los candidatos el que menos difiere del leído; solo si es único y cercano. -> (valor, distancia) | None."""
    if not read:
        return None
    best, tie = None, False
    for c in {x for x in candidates if x}:
        d = ocr_distance(read, c)
        if best is None or d < best[1] - 1e-9:
            best, tie = (c, d), False
        elif abs(d - best[1]) < 1e-9:
            tie = True
    return best if best and not tie and best[1] <= max_distance else None


# ---- formato de cada juego ----
def _pokemon_canon(raw):
    parts = up(raw).replace(" ", "").split("/")
    return "/".join(trim_zeros(p) for p in parts) if len(parts) <= 2 and all(re.fullmatch(r"[A-Z]{0,5}\d{1,3}[A-Z]?", p) for p in parts) else None


def _magic_canon(raw):
    m = re.fullmatch(r"(\d{1,4}[A-Z★]?)(?:/\d{1,4})?", up(raw).replace(" ", ""))
    return trim_zeros(m.group(1)) if m else None


def _yugioh_canon(raw):
    m = re.fullmatch(r"([A-Z0-9]{2,5})-([A-Z]{0,2})(\d{1,4})", up(raw).replace(" ", ""))
    return f"{m.group(1)}-{trim_zeros(m.group(3))}" if m else None


def _onepiece_canon(raw):
    m = re.search(r"(OP|ST|EB|PRB|P)-?(\d{0,2})-(\d{3})(?!\d)", up(raw).replace(" ", ""))
    return f"{m.group(1)}{m.group(2).zfill(2) if m.group(2) else ''}-{m.group(3)}" if m else None


def _riftbound_canon(raw):
    m = re.fullmatch(r"(\d{1,3}[A-Z*★]?)/(\d{1,3})", up(raw).replace(" ", ""))
    return f"{trim_zeros(m.group(1))}/{trim_zeros(m.group(2))}" if m else None


def _lorcana_canon(raw):
    parts = up(raw).replace(" ", "").split("/")
    if len(parts) == 1 and re.fullmatch(r"\d{1,3}", parts[0]):
        return trim_zeros(parts[0])
    if len(parts) == 2 and all(re.fullmatch(r"[A-Z]?\d{1,3}", p) for p in parts):
        return "/".join(trim_zeros(p) for p in parts)
    return None


def _digimon_canon(raw):
    m = re.fullmatch(r"(BT|ST|EX|RB|LM|P)-?(\d{0,2})-(\d{1,3})", up(raw).replace(" ", ""))
    return f"{m.group(1)}{trim_zeros(m.group(2)) if m.group(2) else ''}-{trim_zeros(m.group(3))}" if m else None


def _myl_canon(raw):
    t = up(raw).replace(" ", "")
    a, b = re.fullmatch(r"([A-Z]{2,8})-(\d{1,3})", t), re.fullmatch(r"(\d{1,3})/(\d{1,3})", t)
    return f"{a.group(1)}-{trim_zeros(a.group(2))}" if a else f"{trim_zeros(b.group(1))}/{trim_zeros(b.group(2))}" if b else None


CANON = {"pokemon": _pokemon_canon, "magic": _magic_canon, "yugioh": _yugioh_canon, "onepiece": _onepiece_canon, "riftbound": _riftbound_canon, "lorcana": _lorcana_canon, "digimon": _digimon_canon, "myl": _myl_canon}


def canon_number(game: str, raw):
    fn = CANON.get(game)
    return fn(raw) if fn and raw else None


def _digits_of_pokemon(p: str) -> str:
    return re.sub(r"^([A-Z]{0,4}?)([0-9OIS]{1,3})([A-Z]?)$", lambda m: m.group(1) + fix_digits(m.group(2)) + m.group(3), p)


def parse_ocr(game: str, text: str):
    """Texto leído en la zona del número → (número canónico, sigla de edición)."""
    t = up(text)
    if game == "pokemon":
        number = None
        m = re.search(r"\b([A-Z]{0,4}[0-9OIS]{1,3}[A-Z]?)\s*[/|]\s*([A-Z]{0,4}[0-9OIS]{1,3})\b", t)
        if m:
            number = canon_number(game, f"{_digits_of_pokemon(m.group(1))}/{_digits_of_pokemon(m.group(2))}")
        if not number:
            p = re.search(r"\b(SWSH|SM|XY|BW|HGSS|SVP|SV|MEP|DP|NP)\s?-?\s?([0-9OIS]{1,3})\b", t)
            if p:
                number = canon_number(game, p.group(1) + fix_digits(p.group(2)))
        s = re.search(r"\b([A-Z]{2,4}\d?)\b(?=[^A-Z0-9]*(?:[A-Z]{2}\b)?[^A-Z0-9]*[0-9OIS]{1,3}\s*/)", t)
        code = s.group(1) if s and s.group(1) not in LANG_CODES else None
        return number, code
    if game == "magic":
        m = re.search(r"\b([0-9OIS]{1,4}[A-Z★]?)\s*(?:/\s*[0-9OIS]{1,4})?\s*[RUCMLS]?\b", t)
        c = re.search(r"\b([A-Z0-9]{3,4})\s*[•·*]\s*[A-Z]{2}\b", t)
        raw = fix_digits(m.group(1)) if m and re.search(r"\d", m.group(1)) else None  # una palabra como «IS» no es un número
        return (canon_number(game, raw) if raw and not re.fullmatch(r"(19|20)\d\d", raw) else None), (c.group(1) if c else None)
    if game == "yugioh":
        m = re.search(r"\b([A-Z0-9]{2,5})\s?-\s?([A-Z]{0,2})\s?([0-9OIS]{3,4})\b", t)
        return (canon_number(game, f"{m.group(1)}-{m.group(2)}{fix_digits(m.group(3))}") if m else None), (m.group(1) if m else None)
    if game in ("onepiece", "digimon"):
        pat = r"\b(OP|ST|EB|PRB|P)\s?-?\s?([0-9OIS]{0,2})\s?-\s?([0-9OIS]{3})\b" if game == "onepiece" else r"\b(BT|ST|EX|RB|LM|P)\s?-?\s?([0-9OIS]{0,2})\s?-\s?([0-9OIS]{1,3})\b"
        m = re.search(pat, t)
        return (canon_number(game, f"{m.group(1)}{fix_digits(m.group(2))}-{fix_digits(m.group(3))}") if m else None), (m.group(1) + fix_digits(m.group(2)) if m else None)
    if game == "riftbound":
        m = re.search(r"\b([0-9OIS]{1,3}[A-Z*★]?)\s*/\s*([0-9OIS]{1,3})\b", t)
        s = re.search(r"\b([A-Z]{3})\b[^A-Z0-9]{0,4}[0-9OIS]{1,3}\s*/", t)
        return (canon_number(game, f"{fix_digits(m.group(1))}/{fix_digits(m.group(2))}") if m else None), (s.group(1) if s else None)
    if game == "lorcana":
        m = re.search(r"\b([0-9OIS]{1,3})\s*/\s*([A-Z]?[0-9OIS]{1,3})\b", t)
        s = re.search(r"\b([0-9A-Z]{3,4})\s*[·•\-\s]\s*(?:EN|FR|DE|IT|JP|ES|PT)\b", t)
        num = canon_number(game, f"{fix_digits(m.group(1))}/{m.group(2) if m.group(2)[0] == 'P' else fix_digits(m.group(2))}") if m else None
        return num, (s.group(1) if s else None)
    if game == "myl":
        a = re.search(r"\b([A-Z]{2,8})\s?-\s?([0-9OIS]{1,3})\b", t)
        b = re.search(r"\b([0-9OIS]{1,3})\s*/\s*([0-9OIS]{1,3})\b", t)
        num = canon_number(game, f"{a.group(1)}-{fix_digits(a.group(2))}") if a else canon_number(game, f"{fix_digits(b.group(1))}/{fix_digits(b.group(2))}") if b else None
        return num, (a.group(1) if a else None)
    return None, None
