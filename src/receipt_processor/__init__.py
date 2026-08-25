"""Receipt OCR and structured parsing."""

from .core import ReceiptProcessor, ReceiptProcessingError
from .tracker import TrackerError, TrackerStore, merchant_similarity, normalize_merchant_name
from .workflow import (
    hamming_hex, image_perceptual_hash, is_reimbursement_eligible, jaccard_similarity,
    signed_total, text_fingerprint,
)

__all__ = [
    "ReceiptProcessor", "ReceiptProcessingError", "TrackerError", "TrackerStore",
    "merchant_similarity", "normalize_merchant_name",
    "is_reimbursement_eligible", "jaccard_similarity", "signed_total", "text_fingerprint",
    "hamming_hex", "image_perceptual_hash",
]
__version__ = "4.0.0"
