"""Streamlit interface for the custom receipt processor."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from receipt_processor import ReceiptProcessor, ReceiptProcessingError  # noqa: E402


st.set_page_config(
    page_title="Receipt Processor",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Receipt Processor")
st.caption("Upload a receipt image or scanned PDF to extract structured purchase data.")

with st.sidebar:
    st.header("OCR settings")
    language = st.selectbox(
        "Document language",
        options=("eng", "spa", "fra", "deu"),
        format_func={
            "eng": "English",
            "spa": "Spanish",
            "fra": "French",
            "deu": "German",
        }.get,
    )
    currency_choice = st.selectbox(
        "Currency",
        options=("Auto-detect", "USD", "EUR", "GBP", "CAD", "AUD"),
    )
    min_confidence = st.slider("Minimum OCR confidence", 0, 100, 55)
    preprocess = st.toggle("Enhance image", value=True)
    if preprocess:
        deskew = st.toggle("Deskew", value=True)
        denoise = st.toggle("Denoise", value=True)
        threshold = st.toggle("Black-and-white threshold", value=True)
    else:
        deskew = denoise = threshold = False

uploaded_file = st.file_uploader(
    "Receipt file",
    type=("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "pdf"),
    help="Supported formats: PNG, JPEG, TIFF, BMP, WebP, and scanned PDF.",
)

if uploaded_file and uploaded_file.type != "application/pdf":
    st.image(uploaded_file, caption=uploaded_file.name, width=520)

process_clicked = st.button(
    "Process receipt",
    type="primary",
    disabled=uploaded_file is None,
    use_container_width=True,
)

if process_clicked and uploaded_file is not None:
    suffix = Path(uploaded_file.name).suffix.lower()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(uploaded_file.getvalue())
            temporary_path = Path(temporary.name)

        processor = ReceiptProcessor(
            temporary_path,
            language=language,
            currency=None if currency_choice == "Auto-detect" else currency_choice,
            min_confidence=float(min_confidence),
            preprocess=preprocess,
            deskew=deskew,
            denoise=denoise,
            threshold=threshold,
        )
        with st.spinner("Reading and parsing the receipt…"):
            result = processor.parse()
        st.session_state["receipt_result"] = result
        st.session_state["receipt_source_name"] = uploaded_file.name
        st.success("Receipt processed.")
    except (OSError, ValueError, ReceiptProcessingError) as exc:
        st.error(f"The receipt could not be processed: {exc}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

result = st.session_state.get("receipt_result")
if result:
    st.divider()
    overview_tab, items_tab, json_tab, text_tab = st.tabs(
        ("Overview", "Items", "JSON", "OCR text")
    )

    with overview_tab:
        first, second, third, fourth = st.columns(4)
        first.metric("Vendor", result.get("vendor") or "Not found")
        second.metric("Date", result.get("date") or "Not found")
        currency = result.get("currency") or "—"
        total = result.get("total")
        third.metric("Total", f"{currency} {total}" if total else "Not found")
        confidence = result.get("ocr", {}).get("confidence")
        fourth.metric("OCR confidence", f"{confidence:.1f}%" if confidence is not None else "—")

        summary = pd.DataFrame(
            [
                {
                    "Subtotal": result.get("subtotal"),
                    "Tax": result.get("tax"),
                    "Tip": result.get("tip"),
                    "Discount": result.get("discount"),
                    "Total": result.get("total"),
                }
            ]
        )
        st.dataframe(summary, hide_index=True, use_container_width=True)
        if result.get("warnings"):
            st.warning("Review recommended: " + ", ".join(result["warnings"]))

    with items_tab:
        items = pd.DataFrame(result.get("items") or [])
        if items.empty:
            st.info("No line items were detected. You can correct the OCR text in the OCR text tab.")
        else:
            st.dataframe(items, hide_index=True, use_container_width=True)
            st.download_button(
                "Download items as CSV",
                data=items.to_csv(index=False).encode("utf-8"),
                file_name="receipt_items.csv",
                mime="text/csv",
            )

    with json_tab:
        json_data = json.dumps(result, ensure_ascii=False, indent=2)
        st.json(result)
        st.download_button(
            "Download receipt JSON",
            data=json_data.encode("utf-8"),
            file_name="receipt.json",
            mime="application/json",
        )

    with text_tab:
        corrected_text = st.text_area(
            "Extracted text",
            value=result.get("raw_text", ""),
            height=320,
            help="Correct any OCR mistakes, then parse the text again.",
        )
        if st.button("Re-parse corrected text"):
            corrected = ReceiptProcessor.parse_text(
                corrected_text,
                currency=None if currency_choice == "Auto-detect" else currency_choice,
            )
            corrected["ocr"] = result.get("ocr", {})
            st.session_state["receipt_result"] = corrected
            st.rerun()

st.caption("Uploaded files are processed temporarily and are not stored by this app.")
