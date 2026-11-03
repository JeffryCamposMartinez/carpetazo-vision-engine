"""E3: extractores de embeddings versionados, con DINOv2-small en un proceso aparte y respaldo `grid`."""

from app.recognition.embedding.base import EmbeddingResult, EmbeddingSpec, Extractor, ExtractorUnavailable
from app.recognition.embedding.service import EmbeddingService

__all__ = ["EmbeddingResult", "EmbeddingService", "EmbeddingSpec", "Extractor", "ExtractorUnavailable"]
