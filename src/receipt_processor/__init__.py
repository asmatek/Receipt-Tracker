"""Receipt OCR and structured parsing."""

from .core import ReceiptProcessor, ReceiptProcessingError
from .tracker import TrackerError, TrackerStore

__all__ = ["ReceiptProcessor", "ReceiptProcessingError", "TrackerError", "TrackerStore"]
__version__ = "2.0.0"
