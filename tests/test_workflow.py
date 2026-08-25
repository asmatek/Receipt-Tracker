from receipt_processor.workflow import (
    hamming_hex, image_perceptual_hash, is_reimbursement_eligible, jaccard_similarity,
    signed_total, text_fingerprint,
)
from PIL import Image
import io


def test_refunds_are_negative():
    assert signed_total("21.50", "refund") == -21.50
    assert signed_total("21.50", "receipt") == 21.50


def test_text_fingerprint_normalizes_spacing_and_punctuation():
    assert text_fingerprint("TOTAL: 12.34") == text_fingerprint(" total  12.34 ")


def test_jaccard_similarity_spots_nearly_same_ocr():
    assert jaccard_similarity("Corner Market total 12.34 tax 1.00", "corner market tax 1.00 total 12.34") == 1


def test_reimbursement_eligibility_is_strict():
    base = {"review_status": "approved", "reimbursable": True, "is_personal": False, "reimbursement_status": "not_reimbursed"}
    assert is_reimbursement_eligible(base)
    assert not is_reimbursement_eligible({**base, "review_status": "needs_review"})
    assert not is_reimbursement_eligible({**base, "duplicate_of": "some-id"})


def test_perceptual_hash_is_stable_for_same_image():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
    first = image_perceptual_hash(buffer.getvalue())
    assert first and hamming_hex(first, first) == 0
