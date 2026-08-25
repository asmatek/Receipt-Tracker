from receipt_processor.tracker import (
    TrackerStore, duplicate_key, merchant_similarity, money, normalize_email, normalize_merchant_name,
)


def test_normalize_email():
    assert normalize_email("  Owner@Example.COM ") == "owner@example.com"


def test_money_is_financially_stable():
    assert money("1,234.567") == 1234.57
    assert money("") is None


def test_duplicate_key_normalizes_vendor():
    first = duplicate_key("Corner Market, LLC", "2026-08-21", "12.18")
    second = duplicate_key("corner market llc", "2026-08-21", 12.18)
    assert first == second


def test_merchant_normalization_ignores_common_suffixes():
    assert normalize_merchant_name("Corner Market, LLC") == "cornermarket"
    assert normalize_merchant_name("CORNER-MARKET") == "cornermarket"


def test_merchant_similarity_spots_ocr_variations():
    assert merchant_similarity("Whole Foods Market", "Whole F00ds Market") > 0.85


def test_clean_expense_handles_refunds_and_approval():
    clean = TrackerStore._clean_expense({
        "vendor": "Corner Market", "expense_date": "2026-08-21", "total": 12.50,
        "document_kind": "refund", "business_id": "business-id", "category": "Refunds & Credits",
        "business_purpose": "Returned supplies", "review_status": "approved",
    })
    assert clean["total"] == -12.50
    assert clean["review_status"] == "approved"
    assert clean["approved_at"]


def test_possible_duplicate_cannot_be_auto_approved():
    clean = TrackerStore._clean_expense({
        "vendor": "Corner Market", "expense_date": "2026-08-21", "total": 12.50,
        "business_id": "business-id", "category": "Office Supplies",
        "business_purpose": "Supplies", "review_status": "approved", "possible_duplicate": True,
    })
    assert clean["review_status"] == "needs_review"
