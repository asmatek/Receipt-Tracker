"""Deterministic accounting workflow helpers shared by the UI and persistence layer."""

from __future__ import annotations

import hashlib
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from PIL import Image


REFUND_KINDS = {"refund", "credit"}


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def text_fingerprint(value: str) -> str:
    normalized = normalized_text(value)[:4000]
    return hashlib.sha256(normalized.encode()).hexdigest() if normalized else ""


def image_perceptual_hash(content: bytes) -> str:
    """Return a compact average hash; visually similar receipt photos have nearby hashes."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            pixels = list(image.convert("L").resize((8, 8), Image.Resampling.LANCZOS).getdata())
    except Exception:
        return ""
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if value > average else "0" for value in pixels)
    return f"{int(bits, 2):016x}"


def hamming_hex(first: str, second: str) -> int:
    if not first or len(first) != len(second):
        return 10_000
    return (int(first, 16) ^ int(second, 16)).bit_count()


def jaccard_similarity(first: str, second: str) -> float:
    left, right = set(normalized_text(first).split()), set(normalized_text(second).split())
    return len(left & right) / len(left | right) if left and right else 0.0


def signed_total(total: Any, document_kind: str) -> float:
    try:
        amount = Decimal(str(total or 0).replace(",", ""))
    except (InvalidOperation, ValueError):
        amount = Decimal("0")
    if (document_kind or "").lower() in REFUND_KINDS and amount > 0:
        amount = -amount
    return float(amount.quantize(Decimal("0.01")))


def is_reimbursement_eligible(expense: dict[str, Any]) -> bool:
    return bool(
        not expense.get("deleted_at")
        and expense.get("review_status") == "approved"
        and expense.get("reimbursable", True)
        and not expense.get("is_personal", False)
        and not expense.get("duplicate_of")
        and not expense.get("possible_duplicate", False)
        and expense.get("reimbursement_status", "not_reimbursed") == "not_reimbursed"
    )


def reimbursement_total(expenses: Iterable[dict[str, Any]]) -> float:
    return float(sum(Decimal(str(row.get("usd_total") or row.get("total") or 0)) for row in expenses if is_reimbursement_eligible(row)))
