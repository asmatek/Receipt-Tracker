"""Receipt OCR and structured parsing.

Imports are intentionally lazy so an optional component cannot prevent the
Streamlit application from displaying a useful startup diagnostic.
"""

from importlib import import_module
from typing import Any

__version__ = "4.0.1"

_EXPORTS = {
    "ReceiptProcessor": (".core", "ReceiptProcessor"),
    "ReceiptProcessingError": (".core", "ReceiptProcessingError"),
    "TrackerError": (".tracker", "TrackerError"),
    "TrackerStore": (".tracker", "TrackerStore"),
    "merchant_similarity": (".tracker", "merchant_similarity"),
    "normalize_merchant_name": (".tracker", "normalize_merchant_name"),
    "hamming_hex": (".workflow", "hamming_hex"),
    "image_perceptual_hash": (".workflow", "image_perceptual_hash"),
    "is_reimbursement_eligible": (".workflow", "is_reimbursement_eligible"),
    "jaccard_similarity": (".workflow", "jaccard_similarity"),
    "signed_total": (".workflow", "signed_total"),
    "text_fingerprint": (".workflow", "text_fingerprint"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
