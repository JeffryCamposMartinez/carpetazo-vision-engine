"""Índice de referencias por similitud coseno (fuerza bruta en memoria). Agregar cartas = agregar vectores."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Decision:
    status: str  # known | unknown
    top_id: str | None
    top_score: float
    margin: float  # ventaja sobre el mejor candidato de OTRA carta (las variantes de la misma carta no restan)


class RefIndex:
    def __init__(self, dim: int):
        self.dim = dim
        self.ids: list[str] = []
        self.card_of: dict[str, str] = {}
        self._matrix = np.zeros((0, dim), np.float32)

    def add(self, items: list[tuple[str, str, np.ndarray]]) -> None:
        """items = (id de imagen, id de carta, vector normalizado). No reentrena nada."""
        for _, _, v in items:
            if v.shape != (self.dim,):
                raise ValueError(f"dimensión {v.shape} incompatible con el índice ({self.dim},)")
        self.ids += [i for i, _, _ in items]
        self.card_of.update({i: c for i, c, _ in items})
        self._matrix = np.vstack([self._matrix, np.stack([v for _, _, v in items]).astype(np.float32)])

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def memory_bytes(self) -> int:
        return int(self._matrix.nbytes)

    def search(self, query: np.ndarray, k: int = 10) -> list[tuple[str, float]]:
        sims = self._matrix @ query.astype(np.float32)
        top = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in top]

    def decide(self, results: list[tuple[str, float]], min_score: float, min_margin: float) -> Decision:
        """Acepta solo si el mejor candidato supera el umbral y se distingue de las demás CARTAS."""
        top_id, top_score = results[0]
        other = [s for i, s in results[1:] if self.card_of[i] != self.card_of[top_id]]
        margin = top_score - (other[0] if other else 0.0)
        ok = top_score >= min_score and margin >= min_margin
        return Decision("known" if ok else "unknown", top_id if ok else None, top_score, margin)
