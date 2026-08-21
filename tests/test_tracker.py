from receipt_processor.tracker import duplicate_key, money, normalize_email


def test_normalize_email():
    assert normalize_email("  Owner@Example.COM ") == "owner@example.com"


def test_money_is_financially_stable():
    assert money("1,234.567") == 1234.57
    assert money("") is None


def test_duplicate_key_normalizes_vendor():
    first = duplicate_key("Corner Market, LLC", "2026-08-21", "12.18")
    second = duplicate_key("corner market llc", "2026-08-21", 12.18)
    assert first == second
