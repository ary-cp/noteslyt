"""PDF -> per-page markdown -> page-tagged chunks -> vectors (see pipeline.py)."""

from .pipeline import IngestionError, IngestionPipeline

__all__ = ["IngestionError", "IngestionPipeline"]
