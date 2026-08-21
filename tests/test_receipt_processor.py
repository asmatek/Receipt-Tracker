from receipt_processor import ReceiptProcessor


SAMPLE = """Corner Market
123 Main Street
08/19/2026 14:22
2 x 3.50 Sparkling Water 7.00
Fresh Bread 4.25
SUBTOTAL $11.25
TAX $0.93
TOTAL $12.18
VISA **** 1234
"""


def test_parses_core_receipt_fields():
    result = ReceiptProcessor.parse_text(SAMPLE)
    assert result["vendor"] == "Corner Market"
    assert result["date"] == "2026-08-19"
    assert result["currency"] == "USD"
    assert result["subtotal"] == "11.25"
    assert result["tax"] == "0.93"
    assert result["total"] == "12.18"
    assert result["warnings"] == []
    assert result["items"] == [
        {"description": "Sparkling Water", "quantity": "2", "unit_price": "3.50", "total": "7.00"},
        {"description": "Fresh Bread", "quantity": None, "unit_price": None, "total": "4.25"},
    ]


def test_currency_override_and_reconciliation_warning():
    result = ReceiptProcessor.parse_text("Shop\nItem 5.00\nSubtotal 5.00\nTax 1.00\nTotal 9.00", currency="cad")
    assert result["currency"] == "CAD"
    assert "totals_do_not_reconcile" in result["warnings"]


def test_text_only_public_api_does_not_require_ocr():
    result = ReceiptProcessor(currency="EUR").parse(text="Cafe\nCoffee 2.50\nTotal 2.50")
    assert result["total"] == "2.50"
    assert result["ocr"]["confidence"] is None
