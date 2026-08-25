"""Multi-user Streamlit receipt and business-expense tracker."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import zipfile
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from receipt_processor import (  # noqa: E402
    ReceiptProcessingError, ReceiptProcessor, TrackerStore, image_perceptual_hash,
    signed_total, text_fingerprint,
)
from receipt_processor.tracker import duplicate_key, money  # noqa: E402


st.set_page_config(page_title="Receipt Tracker", page_icon="🧾", layout="wide")

st.markdown(
    """
    <style>
    :root { --brand:#31b7a4; --ink:#18263b; --muted:#667085; --surface:#ffffff; }
    .stApp { background: #f6f7fb; color: var(--ink); }
    [data-testid="stSidebar"] { background: #23324a; }
    [data-testid="stSidebar"] * { color: #f9fafb; }
    [data-testid="stSidebar"] [role="radiogroup"] label {
        padding:.62rem .72rem; border-radius:.7rem; margin:.12rem 0;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:#31435f; box-shadow:inset 3px 0 #31b7a4; }
    div[data-testid="stMetric"] {
        background:var(--surface); border:1px solid #e7e9f0; border-radius:16px;
        padding:1rem 1.1rem; box-shadow:0 4px 16px rgba(16,24,40,.04);
    }
    div[data-testid="stExpander"], div[data-testid="stForm"] {
        background:var(--surface); border:1px solid #e7e9f0; border-radius:16px;
        box-shadow:0 4px 16px rgba(16,24,40,.035);
    }
    .status-pill { display:inline-block; padding:.25rem .6rem; border-radius:999px;
        font-size:.78rem; font-weight:700; background:#e8faf6; color:#087f70; }
    .status-pill.warn { background:#fff7ed; color:#c2410c; }
    .status-pill.done { background:#ecfdf3; color:#027a48; }
    .queue-title { font-size:1.08rem; font-weight:750; margin-bottom:.1rem; }
    .muted { color:var(--muted); font-size:.88rem; }
    .block-container { padding-top:2rem; max-width:1500px; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
        border-radius:10px; background:var(--brand); border-color:var(--brand);
    }
    @media (max-width: 760px) { .block-container { padding:.9rem 1rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)

CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN"]
PAYMENT_METHODS = ["Business card", "Personal card", "Cash", "Bank transfer", "Other"]
DOCUMENT_KINDS = ["receipt", "invoice", "credit", "refund", "statement", "email", "non_receipt", "unknown"]
REVIEW_STATUSES = ["needs_review", "approved", "rejected"]


def secret_value(section: str, key: str, default: Any = None) -> Any:
    try:
        return st.secrets[section].get(key, default)
    except (KeyError, AttributeError, FileNotFoundError):
        return default


@st.cache_resource(show_spinner=False)
def get_store(url: str, anon_key: str, service_key: str) -> TrackerStore:
    return TrackerStore(url, anon_key, service_key)


def configured_store() -> Optional[TrackerStore]:
    url = secret_value("supabase", "url")
    anon = secret_value("supabase", "anon_key")
    service = secret_value("supabase", "service_role_key")
    if not all((url, anon, service)):
        return None
    return get_store(str(url), str(anon), str(service))


def admin_emails() -> set[str]:
    raw = secret_value("app", "admin_emails", [])
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def parse_date(value: Any, fallback: Optional[date] = None) -> date:
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return fallback or date.today()


def classify_category(vendor: str, raw_text: str, categories: list[str]) -> str:
    source = f"{vendor} {raw_text}".lower()
    rules = [
        ("Fuel", ("shell", "chevron", "exxon", "mobil", "fuel", "gasoline", "valero")),
        ("Restaurants & Meals", ("restaurant", "cafe", "coffee", "grill", "kitchen", "doordash", "uber eats")),
        ("Hotels & Lodging", ("hotel", "booking", "marriott", "hilton", "airbnb")),
        ("Airfare", ("airlines", "airways", "flight", "delta", "united air")),
        ("Transportation", ("uber", "lyft", "taxi", "amtrak", "transit")),
        ("Parking & Tolls", ("parking", "toll", "garage", "meter")),
        ("Software & Subscriptions", ("software", "subscription", "microsoft", "google cloud", "openai", "adobe")),
        ("Office Supplies", ("office depot", "staples", "printer", "paper", "ink")),
        ("Utilities", ("electric", "water", "internet", "utility", "phone")),
        ("Shipping & Postage", ("fedex", "ups", "usps", "dhl", "shipping", "postage")),
        ("Medical", ("pharmacy", "clinic", "medical", "dental", "cvs", "walgreens")),
    ]
    for category, words in rules:
        if category in categories and any(word in source for word in words):
            return category
    return "Other" if "Other" in categories else categories[0]


def setup_required() -> None:
    st.title("🧾 Receipt Tracker setup")
    st.error("Supabase is not connected yet.")
    st.markdown(
        """
Complete these three steps:

1. Create a Supabase project.
2. Run `supabase_setup.sql` in **Supabase → SQL Editor**.
3. Add the project credentials in **Streamlit → Manage app → Settings → Secrets**.

Use this format:
"""
    )
    st.code(
        '[supabase]\nurl = "https://YOUR-PROJECT.supabase.co"\n'
        'anon_key = "YOUR-ANON-KEY"\nservice_role_key = "YOUR-SERVICE-ROLE-KEY"\n\n'
        '[app]\nadmin_emails = ["your-email@example.com"]\n',
        language="toml",
    )
    st.warning("Never add the service-role key to GitHub. Put it only in Streamlit Secrets.")
    st.stop()


def auth_screen(store: TrackerStore) -> None:
    st.title("🧾 Receipt Tracker")
    st.caption("Secure receipt storage and business-expense reporting")
    _, center, _ = st.columns([1, 1.3, 1])
    with center:
        login_tab, account_tab = st.tabs(["Sign in", "Create invited account"])
        with login_tab:
            with st.form("login_form"):
                email = st.text_input("Email", autocomplete="email")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
            if submitted:
                try:
                    st.session_state.user = store.sign_in(email, password)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Sign-in failed: {exc}")
        with account_tab:
            st.info("An administrator must add your email before you can create an account.")
            with st.form("signup_form"):
                full_name = st.text_input("Full name")
                new_email = st.text_input("Invited email", autocomplete="email")
                new_password = st.text_input("Create password", type="password", autocomplete="new-password")
                confirm = st.text_input("Confirm password", type="password", autocomplete="new-password")
                create = st.form_submit_button("Create account", use_container_width=True)
            if create:
                if len(new_password) < 8:
                    st.error("Use a password with at least 8 characters.")
                elif new_password != confirm:
                    st.error("Passwords do not match.")
                else:
                    try:
                        st.success(store.sign_up_invited(new_email, new_password, full_name))
                    except Exception as exc:
                        st.error(f"Account creation failed: {exc}")
    st.stop()


def expense_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    flattened = []
    for row in rows:
        record = dict(row)
        business = record.pop("businesses", None) or {}
        record["business"] = business.get("name", "Unassigned") if isinstance(business, dict) else "Unassigned"
        flattened.append(record)
    frame = pd.DataFrame(flattened)
    if not frame.empty:
        frame["expense_date"] = pd.to_datetime(frame["expense_date"], errors="coerce")
        for column in ("subtotal", "tax", "tip", "fees", "discount", "total", "usd_total", "mileage_miles"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        defaults = {
            "review_status": "needs_review", "reimbursement_status": "not_reimbursed",
            "possible_duplicate": False, "is_personal": False, "reimbursable": True,
            "document_kind": "receipt", "tax": 0.0, "fees": 0.0,
        }
        for column, default in defaults.items():
            if column not in frame:
                frame[column] = default
            else:
                frame[column] = frame[column].fillna(default)
    return frame


def dashboard_page(store: TrackerStore) -> None:
    st.header("Dashboard")
    frame = expense_frame(store.list_expenses())
    if frame.empty:
        st.info("No expenses yet. Use **Receipt inbox** to upload your first receipt.")
        return
    duplicate_mask = frame["possible_duplicate"].fillna(False).astype(bool)
    active = frame[~frame["review_status"].eq("rejected") & ~duplicate_mask]
    approved = active[active["review_status"].eq("approved")]
    reimbursable = approved[approved["reimbursable"] & ~approved["is_personal"]]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Approved expenses", f"${approved['usd_total'].sum():,.2f}")
    m2.metric("Awaiting review", f"${active[active['review_status'].eq('needs_review')]['usd_total'].sum():,.2f}")
    m3.metric("Outstanding", f"${reimbursable[~reimbursable['reimbursement_status'].eq('reimbursed')]['usd_total'].sum():,.2f}")
    m4.metric("Reimbursed", f"${reimbursable[reimbursable['reimbursement_status'].eq('reimbursed')]['usd_total'].sum():,.2f}")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Receipts", f"{len(frame):,}")
    q2.metric("Possible duplicates", int(frame["possible_duplicate"].sum()))
    q3.metric("Tax total", f"${approved['tax'].sum():,.2f}")
    q4.metric("Refunds & credits", f"${frame[frame['document_kind'].isin(['refund','credit'])]['usd_total'].sum():,.2f}")
    left, right = st.columns(2)
    with left:
        st.subheader("Monthly expenses")
        monthly = approved.dropna(subset=["expense_date"]).copy()
        monthly["month"] = monthly["expense_date"].dt.to_period("M").astype(str)
        st.bar_chart(monthly.groupby("month", as_index=False)["usd_total"].sum().set_index("month"), y="usd_total", color="#23324A")
    with right:
        st.subheader("By category")
        category = approved.groupby("category", dropna=False, as_index=False)["usd_total"].sum().sort_values("usd_total", ascending=False)
        category["category"] = category["category"].fillna("Uncategorized")
        st.bar_chart(category.set_index("category"), y="usd_total", horizontal=True, color="#31B7A4")
    st.subheader("Recent expenses")
    recent = frame.sort_values("expense_date", ascending=False).head(10)
    st.dataframe(
        recent[["expense_date", "vendor", "business", "category", "usd_total", "review_status", "reimbursement_status"]],
        hide_index=True, use_container_width=True,
        column_config={"expense_date": st.column_config.DateColumn("Date"), "usd_total": st.column_config.NumberColumn("USD total", format="$%.2f")},
    )


def extract_uploaded_receipt(uploaded: Any, language: str) -> dict[str, Any]:
    suffix = Path(uploaded.name).suffix.lower()
    raw = uploaded.getvalue()
    if suffix == ".eml":
        message = BytesParser(policy=policy.default).parsebytes(raw)
        text_parts: list[str] = []
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                    try:
                        text_parts.append(part.get_content())
                    except Exception:
                        pass
        else:
            try:
                text_parts.append(message.get_content())
            except Exception:
                pass
        parsed = ReceiptProcessor.parse_text("\n".join(text_parts))
        parsed.update({
            "document_kind": "email", "email_from": str(message.get("from", ""))[:500],
            "email_subject": str(message.get("subject", ""))[:500],
            "email_received_at": str(message.get("date", ""))[:200],
        })
        return parsed
    if suffix in {".txt", ".md", ".csv", ".html"}:
        return ReceiptProcessor.parse_text(raw.decode("utf-8", errors="replace"))
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(raw)
            temporary_path = Path(temporary.name)
        processor = ReceiptProcessor(
            temporary_path, language=language, min_confidence=45,
            preprocess=True, deskew=True, denoise=True, threshold=True,
        )
        return processor.parse()
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def queue_status(item: dict[str, Any]) -> tuple[str, str]:
    if item.get("status") == "saved":
        return "Saved", "done"
    if item.get("duplicates"):
        return "Possible duplicate", "warn"
    if item.get("error"):
        return "Needs attention", "warn"
    return "Ready to review", ""


def add_expense_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Receipt inbox")
    st.caption("Upload a batch, let OCR organize it, then review and save each receipt.")
    businesses = store.list_businesses()
    categories = store.list_categories()
    if not businesses:
        st.warning("Create at least one business on the **Businesses** page before saving an expense.")
    uploaded_files = st.file_uploader(
        "Drop receipt images or PDFs here",
        type=["png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "pdf", "eml", "txt", "md", "csv", "html"],
        accept_multiple_files=True,
        help="Select several files at once. Originals are preserved in private Supabase storage.",
    )
    c1, c2 = st.columns([2, 1])
    with c1:
        language = st.selectbox("Receipt language", ["eng", "spa", "fra", "deu"], format_func={"eng": "English", "spa": "Spanish", "fra": "French", "deu": "German"}.get)
    with c2:
        extract = st.button(
            f"Process {len(uploaded_files or [])} receipt(s)", type="primary",
            disabled=not uploaded_files, use_container_width=True,
        )
    if extract and uploaded_files:
        batch: list[dict[str, Any]] = []
        batch_keys: set[str] = set()
        progress = st.progress(0, text="Preparing receipts…")
        for index, uploaded in enumerate(uploaded_files):
            raw = uploaded.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            item: dict[str, Any] = {
                "id": f"{digest[:12]}-{index}", "file_bytes": raw, "file_name": uploaded.name,
                "mime_type": uploaded.type or "application/octet-stream", "sha256": digest,
                "perceptual_hash": image_perceptual_hash(raw) if (uploaded.type or "").startswith("image/") else "",
                "status": "ready", "parsed": {}, "duplicates": [], "error": None,
            }
            progress.progress((index + .2) / len(uploaded_files), text=f"Reading {uploaded.name}…")
            try:
                item["parsed"] = extract_uploaded_receipt(uploaded, language)
                parsed = item["parsed"]
                field_key = duplicate_key(parsed.get("vendor", ""), parsed.get("date"), parsed.get("total"))
                same_batch = digest in batch_keys or (field_key in batch_keys and bool(parsed.get("vendor")))
                item["duplicates"] = store.find_possible_duplicates(
                    parsed.get("vendor", ""), parsed.get("date"), parsed.get("total"), digest,
                    text_sha256=text_fingerprint(parsed.get("raw_text", "")),
                    receipt_number=parsed.get("receipt_number", ""), raw_text=parsed.get("raw_text", ""),
                    currency=parsed.get("currency", "USD"), perceptual_hash_value=item["perceptual_hash"],
                )
                if same_batch:
                    item["duplicates"].append({
                        "id": "current-batch", "vendor": parsed.get("vendor"),
                        "expense_date": parsed.get("date"), "total": parsed.get("total"),
                        "receipt_name": "Another file in this upload",
                    })
                batch_keys.update((digest, field_key))
            except (ReceiptProcessingError, OSError, ValueError) as exc:
                item["error"] = str(exc)
            batch.append(item)
            progress.progress((index + 1) / len(uploaded_files), text=f"Processed {index + 1} of {len(uploaded_files)}")
        st.session_state.receipt_batch = batch
        progress.empty()
        st.success(f"{len(batch)} receipt(s) are ready for review.")

    batch = st.session_state.get("receipt_batch", [])
    if not batch:
        st.info("Upload one or more receipts, then select **Process receipts**.")
        return

    ready_count = sum(item.get("status") != "saved" for item in batch)
    duplicate_count = sum(bool(item.get("duplicates")) for item in batch if item.get("status") != "saved")
    saved_count = sum(item.get("status") == "saved" for item in batch)
    a, b, c = st.columns(3)
    a.metric("In inbox", ready_count)
    b.metric("Possible duplicates", duplicate_count)
    c.metric("Saved", saved_count)
    if st.button("Clear receipt inbox", disabled=not batch):
        st.session_state.pop("receipt_batch", None)
        st.rerun()

    labels = []
    for index, item in enumerate(batch, 1):
        parsed_item = item.get("parsed", {})
        label, _ = queue_status(item)
        labels.append(f"{index}. {parsed_item.get('vendor') or item['file_name']} · {label}")
    selected_label = st.selectbox("Receipt to review", labels)
    selected_index = labels.index(selected_label)
    draft = batch[selected_index]
    parsed = draft.get("parsed", {})
    status_text, status_class = queue_status(draft)
    st.markdown(
        f'<div class="queue-title">{draft["file_name"]}</div>'
        f'<span class="status-pill {status_class}">{status_text}</span>',
        unsafe_allow_html=True,
    )
    if draft.get("status") == "saved":
        st.success("This receipt has already been saved. Choose another receipt from the inbox.")
        return
    if draft.get("error"):
        st.warning(f"OCR could not fully read this file: {draft['error']}. You can still enter the details manually.")
    if draft.get("duplicates"):
        st.warning("This receipt may already exist. Check the matches before saving.")
        st.dataframe(pd.DataFrame(draft["duplicates"]), hide_index=True, use_container_width=True)
    if parsed.get("warnings"):
        st.warning("Extraction checks: " + ", ".join(str(item).replace("_", " ") for item in parsed["warnings"]))
    ocr_mean = (parsed.get("ocr") or {}).get("confidence")
    if ocr_mean is not None and ocr_mean < 60:
        st.warning(f"Low OCR confidence ({ocr_mean:.0f}%). Carefully confirm highlighted critical fields before approval.")

    business_options = {row["name"]: row["id"] for row in businesses}
    default_category = classify_category(parsed.get("vendor", ""), parsed.get("raw_text", ""), categories)
    left_preview, right_form = st.columns([.8, 1.35], gap="large")
    with left_preview:
        if draft.get("mime_type", "").startswith("image/"):
            st.image(draft["file_bytes"], caption=draft["file_name"], use_container_width=True)
        else:
            st.info("PDF receipt attached. Review the extracted details on the right.")
    with right_form, st.form(f"expense_form_{draft['id']}", clear_on_submit=False):
        st.subheader("Review details")
        a, b, c = st.columns(3)
        vendor = a.text_input("Merchant", value=parsed.get("vendor") or "", help="Editable. The merchant is created automatically when this receipt is saved.")
        expense_date = b.date_input("Date", value=parse_date(parsed.get("date")))
        currency = c.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(parsed.get("currency")) if parsed.get("currency") in CURRENCIES else 0)
        date_confirmed = st.checkbox("I confirm the transaction date", value=bool(parsed.get("date")), help="Required when OCR could not reliably find a date.")
        a, b, c = st.columns(3)
        transaction_time = a.text_input("Time (optional)", value=parsed.get("time") or "")
        document_kind = b.selectbox("Document type", DOCUMENT_KINDS, index=DOCUMENT_KINDS.index(parsed.get("document_kind")) if parsed.get("document_kind") in DOCUMENT_KINDS else 0)
        receipt_number = c.text_input("Receipt / invoice number", value=parsed.get("receipt_number") or "")
        a, b, c, d, e, f = st.columns(6)
        subtotal = a.number_input("Subtotal", min_value=0.0, value=money(parsed.get("subtotal")) or 0.0, step=0.01)
        tax = b.number_input("Tax", min_value=0.0, value=money(parsed.get("tax")) or 0.0, step=0.01)
        tip = c.number_input("Tip", min_value=0.0, value=money(parsed.get("tip")) or 0.0, step=0.01)
        fees = d.number_input("Fees", min_value=0.0, value=money(parsed.get("fees")) or 0.0, step=0.01)
        discount = e.number_input("Discount", min_value=0.0, value=money(parsed.get("discount")) or 0.0, step=0.01)
        total = f.number_input("Total", min_value=0.0, value=abs(money(parsed.get("total")) or 0.0), step=0.01)
        total_confirmed = st.checkbox("I confirm the final total", value=money(parsed.get("total")) is not None, help="Required when OCR could not reliably find a total.")
        exchange_rate = 1.0
        if currency != "USD":
            exchange_rate = st.number_input(f"{currency} to USD exchange rate", min_value=0.000001, value=1.0, format="%.6f")
        final_total = signed_total(total, document_kind)
        usd_total = final_total * exchange_rate
        a, b = st.columns(2)
        business_name = a.selectbox("Business", list(business_options) or ["Create a business first"])
        category = b.selectbox("Category", categories, index=categories.index(default_category) if default_category in categories else 0)
        business_purpose = st.text_input("Business purpose", placeholder="Why was this expense necessary?")
        a, b, c = st.columns(3)
        client_name = a.text_input("Client (optional)")
        project_name = b.text_input("Project (optional)")
        payment_method = c.selectbox("Payment method", PAYMENT_METHODS)
        a, b, c = st.columns(3)
        card_last4 = a.text_input("Card last four", value=parsed.get("card_last4") or "", max_chars=4)
        location = b.text_input("Location", value=parsed.get("location") or "")
        department = c.text_input("Department / cost center")
        a, b = st.columns(2)
        tags_text = a.text_input("Tags", placeholder="travel, client-billable")
        attendees_text = b.text_input("Attendees", placeholder="Names separated by commas")
        a, b, c = st.columns(3)
        mileage_miles = a.number_input("Mileage", min_value=0.0, value=0.0, step=0.1)
        is_personal = b.checkbox("Personal expense")
        reimbursable = c.checkbox("Reimbursable", value=True)
        st.caption("Line items")
        item_rows = parsed.get("items") or []
        item_frame = pd.DataFrame(item_rows, columns=["description", "quantity", "unit_price", "total"])
        edited_items = st.data_editor(item_frame, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"items_{draft['id']}")
        notes = st.text_area("Notes")
        raw_text = st.text_area("OCR text", value=parsed.get("raw_text", ""), height=140)
        duplicate_reviewed = st.checkbox(
            "I reviewed the possible duplicate and want to save this receipt",
            disabled=not bool(draft.get("duplicates")), value=not bool(draft.get("duplicates")),
        )
        review_status = st.selectbox(
            "Review decision", REVIEW_STATUSES,
            index=0 if draft.get("duplicates") or not parsed.get("vendor") or not parsed.get("date") or not parsed.get("total") else 1,
            format_func=lambda value: value.replace("_", " ").title(),
        )
        save = st.form_submit_button("Save receipt", type="primary", use_container_width=True, disabled=not businesses)
    if save:
        text_sha = text_fingerprint(raw_text)
        saved_date = expense_date.isoformat() if date_confirmed else None
        saved_total = final_total if total_confirmed else 0.0
        usd_total = saved_total * exchange_rate
        duplicates = store.find_possible_duplicates(
            vendor, saved_date, saved_total, draft.get("sha256"), text_sha256=text_sha,
            receipt_number=receipt_number, raw_text=raw_text, currency=currency,
            perceptual_hash_value=draft.get("perceptual_hash", ""),
        )
        if duplicates and not duplicate_reviewed:
            draft["duplicates"] = duplicates
            st.warning("The receipt will be saved in Needs Review and excluded from approved totals until you make a duplicate decision.")
        receipt_path = None
        try:
            if draft.get("file_bytes"):
                receipt_path, receipt_hash = store.upload_receipt(draft["file_bytes"], draft["file_name"], draft["mime_type"], user["id"])
            else:
                receipt_hash = None
            payload = {
                "vendor": vendor.strip(), "expense_date": saved_date, "subtotal": subtotal,
                "transaction_time": transaction_time.strip(), "tax": tax, "tip": tip, "fees": fees,
                "discount": discount, "total": saved_total, "currency": currency, "document_kind": document_kind,
                "exchange_rate": exchange_rate, "usd_total": usd_total, "business_id": business_options[business_name],
                "category": category, "business_purpose": business_purpose.strip(), "client_name": client_name.strip(),
                "project_name": project_name.strip(), "payment_method": payment_method, "card_last4": card_last4.strip(),
                "receipt_number": receipt_number.strip(), "location": location.strip(), "department": department.strip(),
                "tags": [item.strip() for item in tags_text.split(",") if item.strip()],
                "attendees": [item.strip() for item in attendees_text.split(",") if item.strip()],
                "mileage_miles": mileage_miles, "is_personal": is_personal, "reimbursable": reimbursable,
                "review_status": review_status, "notes": notes.strip(),
                "items": edited_items.where(pd.notna(edited_items), None).to_dict("records"),
                "confidence": {"ocr_mean": (parsed.get("ocr") or {}).get("confidence")},
                "field_sources": {
                    "merchant": "extracted" if vendor.strip() == (parsed.get("vendor") or "").strip() else "manual",
                    "date": "extracted" if date_confirmed and parsed.get("date") else "manual" if date_confirmed else "unknown",
                    "total": "extracted" if total_confirmed and parsed.get("total") else "manual" if total_confirmed else "unknown",
                    "category": "rule_or_suggestion",
                },
                "raw_text": raw_text, "receipt_path": receipt_path,
                "receipt_name": draft.get("file_name"), "receipt_mime": draft.get("mime_type"),
                "receipt_sha256": receipt_hash, "text_sha256": text_sha,
                "perceptual_hash": draft.get("perceptual_hash", ""),
                "possible_duplicate": bool(duplicates) and not duplicate_reviewed, "duplicate_of": None,
                "duplicate_candidate_id": duplicates[0].get("id") if duplicates and not duplicate_reviewed and duplicates[0].get("id") != "current-batch" else None,
                "duplicate_reasons": duplicates[0].get("duplicate_reasons", []) if duplicates else [],
                "duplicate_score": duplicates[0].get("duplicate_score", 0) if duplicates else 0,
                "email_from": parsed.get("email_from"), "email_subject": parsed.get("email_subject"),
                "email_received_at": parsed.get("email_received_at"),
            }
            created = store.create_expense(payload, user)
            if duplicates and duplicate_reviewed and duplicates[0].get("id") != "current-batch":
                store.record_duplicate_decision(
                    created["id"], duplicates[0]["id"], "keep_both",
                    duplicates[0].get("duplicate_reasons", []), user,
                )
            draft["status"] = "saved"
            draft["expense_id"] = created["id"]
            st.success(f"Expense saved: {vendor or 'Unnamed merchant'} — ${usd_total:,.2f}")
            st.session_state.selected_expense_id = created["id"]
            st.rerun()
        except Exception as exc:
            if receipt_path:
                store.delete_receipt(receipt_path)
            st.error(f"Could not save the expense: {exc}")


def filtered_expenses(frame: pd.DataFrame, businesses: list[str], categories: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    with st.expander("Search and filters", expanded=True):
        a, b, c = st.columns(3)
        search = a.text_input("Search merchant, purpose, client, project, tags, or receipt number")
        selected_businesses = b.multiselect("Business", businesses)
        selected_categories = c.multiselect("Category", categories)
        a, b, c = st.columns(3)
        start = a.date_input("From", value=(date.today() - timedelta(days=365)), key="expense_filter_start")
        end = b.date_input("To", value=date.today(), key="expense_filter_end")
        review_statuses = c.multiselect("Review status", REVIEW_STATUSES, format_func=lambda x: x.replace("_", " ").title())
        a, b, c = st.columns(3)
        reimbursement_statuses = a.multiselect("Reimbursement", ["not_reimbursed", "in_batch", "reimbursed"], format_func=lambda x: x.replace("_", " ").title())
        payment_methods = b.multiselect("Payment method", PAYMENT_METHODS)
        duplicates_only = c.checkbox("Possible duplicates only")
        a, b, c = st.columns(3)
        minimum_amount = a.number_input("Minimum amount", min_value=0.0, value=0.0, step=10.0)
        maximum_amount = b.number_input("Maximum amount (0 = no limit)", min_value=0.0, value=0.0, step=10.0)
        file_types = c.multiselect("File type", sorted(frame.get("receipt_mime", pd.Series(dtype=str)).dropna().unique()))
    result = frame[(frame["expense_date"].dt.date >= start) & (frame["expense_date"].dt.date <= end)]
    if search:
        columns = [column for column in ("vendor", "business_purpose", "client_name", "project_name", "department", "receipt_number", "tags") if column in result]
        mask = result[columns].fillna("").astype(str).apply(lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
        result = result[mask]
    if selected_businesses:
        result = result[result["business"].isin(selected_businesses)]
    if selected_categories:
        result = result[result["category"].isin(selected_categories)]
    if review_statuses:
        result = result[result["review_status"].isin(review_statuses)]
    if reimbursement_statuses:
        result = result[result["reimbursement_status"].isin(reimbursement_statuses)]
    if payment_methods and "payment_method" in result:
        result = result[result["payment_method"].isin(payment_methods)]
    if duplicates_only:
        result = result[result["possible_duplicate"]]
    result = result[result["usd_total"].abs() >= minimum_amount]
    if maximum_amount > 0:
        result = result[result["usd_total"].abs() <= maximum_amount]
    if file_types and "receipt_mime" in result:
        result = result[result["receipt_mime"].isin(file_types)]
    return result


def expenses_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Expenses")
    frame = expense_frame(store.list_expenses())
    if frame.empty:
        st.info("No expenses have been saved.")
        return
    filtered = filtered_expenses(frame, sorted(frame["business"].dropna().unique()), sorted(frame["category"].dropna().unique()))
    st.caption(f"Showing {len(filtered)} of {len(frame)} expenses")
    st.dataframe(
        filtered[["expense_date", "vendor", "business", "category", "usd_total", "review_status", "reimbursement_status", "possible_duplicate"]],
        hide_index=True, use_container_width=True,
        column_config={"expense_date": st.column_config.DateColumn("Date"), "usd_total": st.column_config.NumberColumn("USD total", format="$%.2f"), "possible_duplicate": st.column_config.CheckboxColumn("Duplicate?")},
    )
    label_to_id = {
        f"{row['expense_date'].date() if pd.notna(row['expense_date']) else 'No date'} — {row.get('vendor') or 'No merchant'} — ${row.get('usd_total', 0):,.2f} — {str(row['id'])[:8]}": row["id"]
        for _, row in filtered.iterrows()
    }
    selected_labels = st.multiselect("Select expenses for batch actions", list(label_to_id))
    selected_ids = [label_to_id[label] for label in selected_labels]
    a, b, c = st.columns(3)
    if a.button("Approve selected", disabled=not selected_ids, type="primary", use_container_width=True):
        store.set_review_status(selected_ids, "approved", user)
        st.success(f"Approved {len(selected_ids)} expense(s).")
        st.rerun()
    if b.button("Reject selected", disabled=not selected_ids, use_container_width=True):
        store.set_review_status(selected_ids, "rejected", user)
        st.success(f"Rejected {len(selected_ids)} expense(s).")
        st.rerun()
    if c.button("Move to recycle bin", disabled=not selected_ids, use_container_width=True):
        try:
            deleted = store.delete_expenses(selected_ids, user)
            st.success(f"Moved {deleted} expense(s) to the recycle bin. Receipt files were preserved.")
            st.rerun()
        except Exception as exc:
            st.error(f"Deletion failed: {exc}")
    st.divider()
    st.subheader("Expense details")
    if not label_to_id:
        return
    chosen = st.selectbox("Open an expense", list(label_to_id))
    expense = store.get_expense(label_to_id[chosen])
    if expense:
        render_expense_detail(store, user, expense)


def duplicates_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Duplicate review")
    rows = [row for row in store.list_expenses() if row.get("possible_duplicate")]
    if not rows:
        st.success("No unresolved duplicate warnings.")
        return
    labels = {
        f"{row.get('vendor') or 'Unknown'} · {row.get('expense_date') or 'No date'} · ${money(row.get('total')) or 0:,.2f} · {str(row['id'])[:8]}": row
        for row in rows
    }
    selected = labels[st.selectbox("Warning to review", list(labels))]
    candidate = store.get_expense(selected.get("duplicate_candidate_id")) if selected.get("duplicate_candidate_id") else None
    left, right = st.columns(2)
    for column, title, record in ((left, "New receipt", selected), (right, "Possible match", candidate)):
        with column:
            st.subheader(title)
            if not record:
                st.info("The matching record is unavailable. You can keep the new receipt.")
                continue
            st.metric("Total", f"{record.get('currency', 'USD')} {money(record.get('total')) or 0:,.2f}")
            st.write(f"**Merchant:** {record.get('vendor') or 'Missing'}")
            st.write(f"**Date:** {record.get('expense_date') or 'Missing'}")
            st.write(f"**Receipt #:** {record.get('receipt_number') or 'Not extracted'}")
            if record.get("receipt_path") and record.get("receipt_mime", "").startswith("image/"):
                url = store.receipt_signed_url(record["receipt_path"])
                if url:
                    st.image(url, use_container_width=True)
    reasons = selected.get("duplicate_reasons") or ["Similar receipt data"]
    st.warning("Why it was flagged: " + "; ".join(reasons))
    a, b = st.columns(2)
    if a.button("Keep both", type="primary", use_container_width=True):
        match_id = selected.get("duplicate_candidate_id") or selected["id"]
        store.record_duplicate_decision(selected["id"], match_id, "keep_both", reasons, user)
        st.rerun()
    if b.button("Mark new receipt as duplicate", disabled=not candidate, use_container_width=True):
        store.record_duplicate_decision(selected["id"], candidate["id"], "marked_duplicate", reasons, user)
        st.rerun()


def recycle_bin_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Recycle bin")
    deleted = [row for row in store.list_expenses(True) if row.get("deleted_at")]
    if not deleted:
        st.info("The recycle bin is empty.")
        return
    labels = {
        f"{row.get('vendor') or 'Unknown'} · {row.get('expense_date') or 'No date'} · {str(row['id'])[:8]}": row["id"]
        for row in deleted
    }
    chosen = st.multiselect("Select deleted expenses", list(labels))
    ids = [labels[label] for label in chosen]
    a, b = st.columns(2)
    if a.button("Restore selected", disabled=not ids, type="primary", use_container_width=True):
        store.restore_expenses(ids, user)
        st.rerun()
    confirm = b.checkbox("Permanently delete records and receipt files")
    if b.button("Delete permanently", disabled=not ids or not confirm, use_container_width=True):
        store.permanently_delete_expenses(ids)
        st.rerun()


def batch_csv(rows: list[dict[str, Any]]) -> bytes:
    frame = expense_frame(rows)
    columns = [
        "expense_date", "vendor", "category", "business_purpose", "subtotal", "tax", "tip",
        "fees", "total", "currency", "usd_total", "project_name", "client_name", "payment_method",
        "receipt_number", "review_status", "reimbursement_status",
    ]
    return frame[[column for column in columns if column in frame]].to_csv(index=False).encode("utf-8")


def batch_pdf(batch: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    import fitz
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    y = 48
    page.insert_text((40, y), "Reimbursement Report", fontsize=18, color=(.12, .18, .28))
    y += 24
    for line in (
        f"Batch: {batch.get('name', '')}",
        f"Period: {batch.get('from_date') or '—'} to {batch.get('to_date') or '—'}",
        f"Status: {str(batch.get('status', 'draft')).upper()}",
        f"Receipts: {len(rows)}",
        f"Grand total: ${sum(money(row.get('usd_total')) or 0 for row in rows):,.2f}",
    ):
        page.insert_text((40, y), line, fontsize=10)
        y += 15
    y += 12
    page.insert_text((40, y), "Date       Merchant                         Category                 USD total", fontsize=9)
    y += 14
    for row in rows:
        if y > 750:
            page = document.new_page(width=612, height=792)
            y = 48
        merchant = (row.get("vendor") or "Unknown")[:30]
        category = (row.get("category") or "Uncategorized")[:22]
        line = f"{str(row.get('expense_date') or '')[:10]:10} {merchant:32} {category:24} ${money(row.get('usd_total')) or 0:,.2f}"
        page.insert_text((40, y), line, fontsize=8, fontname="courier")
        y += 12
    return document.tobytes()


def batch_zip(store: TrackerStore, batch: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in batch.get("name", "batch"))
        archive.writestr(f"{safe_name}-report.pdf", batch_pdf(batch, rows))
        archive.writestr(f"{safe_name}-expenses.csv", batch_csv(rows))
        for row in rows:
            if row.get("receipt_path"):
                try:
                    archive.writestr(f"receipts/{str(row.get('expense_date') or 'undated')[:10]}-{row.get('receipt_name') or row['id']}", store.download_receipt(row["receipt_path"]))
                except Exception:
                    pass
    return output.getvalue()


def reimbursements_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Reimbursements")
    expenses = store.list_expenses()
    eligible = [
        row for row in expenses
        if row.get("review_status") == "approved" and row.get("reimbursable", True)
        and not row.get("is_personal") and not row.get("duplicate_of") and not row.get("possible_duplicate")
        and row.get("reimbursement_status", "not_reimbursed") == "not_reimbursed"
    ]
    with st.expander("Create reimbursement batch", expanded=not bool(store.list_reimbursement_batches())):
        with st.form("create_reimbursement"):
            a, b, c = st.columns(3)
            name = a.text_input("Batch name", placeholder="August 2026 expenses")
            from_date = b.date_input("From", value=date.today().replace(day=1))
            to_date = c.date_input("To", value=date.today())
            notes = st.text_area("Notes")
            choices = {
                f"{row.get('expense_date')} · {row.get('vendor')} · ${money(row.get('usd_total')) or 0:,.2f}": row["id"]
                for row in eligible if from_date <= parse_date(row.get("expense_date")) <= to_date
            }
            selected = st.multiselect("Approved expenses", list(choices))
            create = st.form_submit_button("Create batch", type="primary", disabled=not selected)
        if create:
            try:
                store.create_reimbursement_batch(name, from_date, to_date, notes, [choices[label] for label in selected], user)
                st.success("Reimbursement batch created.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not create batch: {exc}")
    batches = store.list_reimbursement_batches()
    if not batches:
        st.info("No reimbursement batches yet. Approve eligible business expenses first.")
        return
    labels = {f"{row['name']} · {str(row['status']).title()} · {str(row['id'])[:8]}": row for row in batches}
    batch = labels[st.selectbox("Open batch", list(labels))]
    rows = store.reimbursement_batch_expenses(batch["id"])
    st.metric("Batch total", f"${sum(money(row.get('usd_total')) or 0 for row in rows):,.2f}")
    st.dataframe(expense_frame(rows)[["expense_date", "vendor", "category", "usd_total", "reimbursement_status"]], hide_index=True, use_container_width=True)
    status = batch.get("status", "draft")
    if status == "draft" and rows:
        with st.expander("Remove expenses from this draft"):
            remove_choices = {
                f"{row.get('expense_date')} · {row.get('vendor')} · ${money(row.get('usd_total')) or 0:,.2f}": row["id"]
                for row in rows
            }
            remove_labels = st.multiselect("Expenses to remove", list(remove_choices))
            if st.button("Remove from batch", disabled=not remove_labels):
                store.remove_expenses_from_batch(batch["id"], [remove_choices[label] for label in remove_labels])
                st.rerun()
    a, b, c = st.columns(3)
    if a.button("Mark submitted", disabled=status != "draft", type="primary", use_container_width=True):
        store.set_reimbursement_batch_status(batch["id"], "submitted")
        st.rerun()
    if b.button("Mark paid", disabled=status not in {"draft", "submitted"}, use_container_width=True):
        store.set_reimbursement_batch_status(batch["id"], "paid")
        st.rerun()
    if c.button("Cancel batch", disabled=status in {"paid", "cancelled"}, use_container_width=True):
        store.set_reimbursement_batch_status(batch["id"], "cancelled")
        st.rerun()
    a, b, c = st.columns(3)
    a.download_button("Download CSV", batch_csv(rows), file_name=f"{batch['name']}-expenses.csv", mime="text/csv", use_container_width=True)
    b.download_button("Download PDF", batch_pdf(batch, rows), file_name=f"{batch['name']}-report.pdf", mime="application/pdf", use_container_width=True)
    if c.button("Prepare ZIP with receipts", use_container_width=True):
        st.session_state[f"batch_zip_{batch['id']}"] = batch_zip(store, batch, rows)
    if st.session_state.get(f"batch_zip_{batch['id']}"):
        st.download_button("Download report + receipts ZIP", st.session_state[f"batch_zip_{batch['id']}"], file_name=f"{batch['name']}-complete.zip", mime="application/zip")


def rules_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Categories & rules")
    st.caption("Create rules such as “all purchases from Shell are Fuel.” Rules apply automatically when a receipt is saved.")
    with st.form("new_merchant_rule", clear_on_submit=True):
        a, b = st.columns(2)
        match_text = a.text_input("Merchant name contains")
        category = b.selectbox("Assign category", store.list_categories())
        a, b = st.columns(2)
        project = a.text_input("Default project (optional)")
        tags = b.text_input("Default tags", placeholder="travel, billable")
        save = st.form_submit_button("Save rule", type="primary")
    if save:
        store.save_merchant_rule(match_text, category, project, tags.split(","), user["email"])
        st.rerun()
    rules = store.list_merchant_rules()
    if rules:
        st.dataframe(pd.DataFrame(rules)[["match_text", "category", "project", "tags", "active"]], hide_index=True, use_container_width=True)
        selected = st.selectbox("Change rule", [row["match_text"] for row in rules])
        rule = next(row for row in rules if row["match_text"] == selected)
        if st.button("Disable rule" if rule.get("active") else "Enable rule"):
            store.set_merchant_rule_active(rule["id"], not rule.get("active"))
            st.rerun()


def render_expense_detail(store: TrackerStore, user: dict[str, Any], expense: dict[str, Any]) -> None:
    businesses = store.list_businesses(False)
    business_map = {row["name"]: row["id"] for row in businesses}
    business_current = (expense.get("businesses") or {}).get("name", "")
    categories = store.list_categories()
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown(f"### {expense.get('vendor') or 'Unnamed merchant'}")
        if expense.get("receipt_path"):
            url = store.receipt_signed_url(expense["receipt_path"])
            if expense.get("receipt_mime", "").startswith("image/") and url:
                st.image(url, caption=expense.get("receipt_name"), use_container_width=True)
            elif url:
                st.link_button("Open receipt PDF", url, use_container_width=True)
            try:
                content = store.download_receipt(expense["receipt_path"])
                st.download_button("Download original receipt", content, file_name=expense.get("receipt_name") or "receipt", mime=expense.get("receipt_mime") or "application/octet-stream", use_container_width=True)
            except Exception:
                st.caption("Receipt download is temporarily unavailable.")
        else:
            st.info("No receipt file is attached to this record.")
    with right:
        with st.form(f"edit_{expense['id']}"):
            a, b, c = st.columns(3)
            vendor = a.text_input("Merchant", value=expense.get("vendor") or "")
            expense_date = b.date_input("Date", value=parse_date(expense.get("expense_date")), key=f"date_{expense['id']}")
            currency = c.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(expense.get("currency")) if expense.get("currency") in CURRENCIES else 0, key=f"currency_{expense['id']}")
            a, b, c = st.columns(3)
            kind_current = expense.get("document_kind") or "receipt"
            document_kind = a.selectbox("Document type", DOCUMENT_KINDS, index=DOCUMENT_KINDS.index(kind_current) if kind_current in DOCUMENT_KINDS else 0)
            transaction_time = b.text_input("Time", value=expense.get("transaction_time") or "")
            receipt_number = c.text_input("Receipt / invoice number", value=expense.get("receipt_number") or "")
            a, b, c, d, e, f = st.columns(6)
            subtotal = a.number_input("Subtotal", min_value=0.0, value=money(expense.get("subtotal")) or 0.0, step=0.01, key=f"sub_{expense['id']}")
            tax = b.number_input("Tax", min_value=0.0, value=money(expense.get("tax")) or 0.0, step=0.01, key=f"tax_{expense['id']}")
            tip = c.number_input("Tip", min_value=0.0, value=money(expense.get("tip")) or 0.0, step=0.01, key=f"tip_{expense['id']}")
            fees = d.number_input("Fees", min_value=0.0, value=money(expense.get("fees")) or 0.0, step=0.01, key=f"fees_{expense['id']}")
            discount = e.number_input("Discount", min_value=0.0, value=money(expense.get("discount")) or 0.0, step=0.01, key=f"discount_{expense['id']}")
            total = f.number_input("Total", min_value=0.0, value=abs(money(expense.get("total")) or 0.0), step=0.01, key=f"total_{expense['id']}")
            exchange_rate = st.number_input("Exchange rate to USD", min_value=0.000001, value=float(expense.get("exchange_rate") or 1), format="%.6f") if currency != "USD" else 1.0
            a, b = st.columns(2)
            business_names = list(business_map)
            business_name = a.selectbox("Business", business_names, index=business_names.index(business_current) if business_current in business_names else 0)
            current_category = expense.get("category") or categories[0]
            category = b.selectbox("Category", categories, index=categories.index(current_category) if current_category in categories else 0)
            purpose = st.text_input("Business purpose", value=expense.get("business_purpose") or "")
            a, b, c = st.columns(3)
            client = a.text_input("Client", value=expense.get("client_name") or "")
            project = b.text_input("Project", value=expense.get("project_name") or "")
            payment = c.selectbox("Payment method", PAYMENT_METHODS, index=PAYMENT_METHODS.index(expense.get("payment_method")) if expense.get("payment_method") in PAYMENT_METHODS else 0)
            a, b, c = st.columns(3)
            card_last4 = a.text_input("Card last four", value=expense.get("card_last4") or "", max_chars=4)
            location = b.text_input("Location", value=expense.get("location") or "")
            department = c.text_input("Department / cost center", value=expense.get("department") or "")
            a, b = st.columns(2)
            tags_text = a.text_input("Tags", value=", ".join(expense.get("tags") or []))
            attendees_text = b.text_input("Attendees", value=", ".join(expense.get("attendees") or []))
            a, b, c = st.columns(3)
            mileage = a.number_input("Mileage", min_value=0.0, value=float(expense.get("mileage_miles") or 0), step=0.1)
            is_personal = b.checkbox("Personal expense", value=expense.get("is_personal", False))
            reimbursable = c.checkbox("Reimbursable", value=expense.get("reimbursable", True))
            st.caption("Line items")
            current_items = pd.DataFrame(expense.get("items") or [], columns=["description", "quantity", "unit_price", "total"])
            edited_items = st.data_editor(current_items, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"edit_items_{expense['id']}")
            notes = st.text_area("Notes", value=expense.get("notes") or "")
            save = st.form_submit_button("Save changes", type="primary", use_container_width=True)
        if save:
            try:
                final_total = signed_total(total, document_kind)
                matches = store.find_possible_duplicates(
                    vendor, expense_date, final_total, expense.get("receipt_sha256"), expense["id"],
                    expense.get("text_sha256"), receipt_number, expense.get("raw_text") or "", currency,
                )
                store.update_expense(expense["id"], {
                    "vendor": vendor, "expense_date": expense_date.isoformat(), "transaction_time": transaction_time,
                    "currency": currency, "document_kind": document_kind, "subtotal": subtotal,
                    "tax": tax, "tip": tip, "fees": fees, "discount": discount, "total": final_total,
                    "exchange_rate": exchange_rate, "usd_total": final_total * exchange_rate,
                    "business_id": business_map[business_name], "category": category, "business_purpose": purpose,
                    "client_name": client, "project_name": project, "payment_method": payment,
                    "card_last4": card_last4, "receipt_number": receipt_number, "location": location,
                    "department": department, "tags": [x.strip() for x in tags_text.split(",") if x.strip()],
                    "attendees": [x.strip() for x in attendees_text.split(",") if x.strip()],
                    "mileage_miles": mileage, "is_personal": is_personal, "reimbursable": reimbursable,
                    "items": edited_items.where(pd.notna(edited_items), None).to_dict("records"),
                    "field_sources": {**(expense.get("field_sources") or {}), "merchant": "manual", "date": "manual", "total": "manual", "category": "manual"},
                    "notes": notes, "review_status": expense.get("review_status", "needs_review"),
                    "possible_duplicate": bool(matches), "duplicate_of": expense.get("duplicate_of"),
                    "duplicate_reasons": matches[0].get("duplicate_reasons", []) if matches else [],
                    "duplicate_score": matches[0].get("duplicate_score", 0) if matches else 0,
                }, user)
                st.success("Expense updated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Update failed: {exc}")
        st.caption(f"Review: **{str(expense.get('review_status', 'needs_review')).replace('_', ' ').title()}** · Reimbursement: **{str(expense.get('reimbursement_status', 'not_reimbursed')).replace('_', ' ').title()}**")
        r1, r2, r3 = st.columns(3)
        if r1.button("Approve", type="primary", use_container_width=True, key=f"approve_{expense['id']}"):
            store.set_review_status([expense["id"]], "approved", user)
            st.rerun()
        if r2.button("Needs review", use_container_width=True, key=f"review_{expense['id']}"):
            store.set_review_status([expense["id"]], "needs_review", user)
            st.rerun()
        if r3.button("Reject", use_container_width=True, key=f"reject_{expense['id']}"):
            store.set_review_status([expense["id"]], "rejected", user)
            st.rerun()
    with st.expander("Audit history"):
        history = store.audit_history(expense["id"])
        if history:
            st.dataframe(pd.DataFrame(history)[["created_at", "action", "actor_email"]], hide_index=True, use_container_width=True)
        else:
            st.caption("No audit events available.")


def reports_page(store: TrackerStore) -> None:
    st.header("Reports")
    frame = expense_frame(store.list_expenses())
    if frame.empty:
        st.info("No expenses are available for reporting.")
        return
    frame = frame[
        frame["review_status"].eq("approved")
        & ~frame["possible_duplicate"].fillna(False).astype(bool)
    ]
    if frame.empty:
        st.info("Approve receipts to include them in financial reports. Rejected and unresolved duplicate records are excluded.")
        return
    a, b, c = st.columns(3)
    period = a.selectbox("Period", ["Monthly", "Quarterly", "Yearly"])
    group = b.selectbox("Group by", ["Category", "Business", "Project", "Client", "Payment method", "Department"])
    years = sorted(frame["expense_date"].dropna().dt.year.unique(), reverse=True)
    year = c.selectbox("Year", years)
    report = frame[frame["expense_date"].dt.year == year].copy()
    if period == "Monthly":
        report["period"] = report["expense_date"].dt.strftime("%Y-%m")
    elif period == "Quarterly":
        report["period"] = report["expense_date"].dt.to_period("Q").astype(str)
    else:
        report["period"] = report["expense_date"].dt.year.astype(str)
    group_column = {
        "Category": "category", "Business": "business", "Project": "project_name", "Client": "client_name",
        "Payment method": "payment_method", "Department": "department",
    }[group]
    if group_column not in report:
        report[group_column] = "Unassigned"
    report[group_column] = report[group_column].fillna("Unassigned").replace("", "Unassigned")
    summary = report.groupby(["period", group_column], as_index=False).agg(amount=("usd_total", "sum"), receipts=("id", "count"))
    a, b, c = st.columns(3)
    a.metric(f"{year} approved total", f"${report['usd_total'].sum():,.2f}")
    b.metric("Tax", f"${report['tax'].sum():,.2f}")
    c.metric("Refunds & credits", f"${report[report['document_kind'].isin(['refund','credit'])]['usd_total'].sum():,.2f}")
    st.bar_chart(summary.pivot(index="period", columns=group_column, values="amount").fillna(0), use_container_width=True)
    st.dataframe(summary, hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("USD amount", format="$%.2f")})
    columns = [
        "expense_date", "vendor", "business", "category", "business_purpose", "client_name",
        "project_name", "department", "payment_method", "receipt_number", "document_kind",
        "subtotal", "tax", "tip", "fees", "currency", "total", "usd_total", "review_status", "reimbursement_status",
    ]
    columns = [column for column in columns if column in report]
    st.download_button("Download detailed report CSV", report[columns].to_csv(index=False).encode("utf-8"), file_name=f"expense-report-{year}.csv", mime="text/csv")


def businesses_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Businesses")
    with st.form("new_business", clear_on_submit=True):
        name = st.text_input("New business name")
        add = st.form_submit_button("Add business", type="primary")
    if add:
        try:
            store.add_business(name, user["email"])
            st.success("Business added.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not add business: {exc}")
    for business in store.list_businesses(False):
        with st.expander(business["name"]):
            with st.form(f"business_{business['id']}"):
                updated_name = st.text_input("Name", value=business["name"])
                active = st.checkbox("Active", value=business.get("active", True))
                save = st.form_submit_button("Save")
            if save:
                try:
                    store.update_business(business["id"], updated_name, active)
                    st.success("Business updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not update business: {exc}")


def merchants_page(store: TrackerStore) -> None:
    st.header("Merchants")
    st.caption("Merchants are created automatically from reviewed receipts. Rename one here to update its linked expenses.")
    merchants = store.list_merchants(False)
    if not merchants:
        st.info("No merchants yet. Save a receipt and its merchant will appear here.")
        return
    active_count = sum(bool(row.get("active")) for row in merchants)
    a, b = st.columns(2)
    a.metric("Merchant records", len(merchants))
    b.metric("Active", active_count)
    merchant_map = {row["canonical_name"]: row for row in merchants}
    selected_name = st.selectbox("Select merchant", list(merchant_map))
    selected = merchant_map[selected_name]
    with st.form(f"merchant_{selected['id']}"):
        new_name = st.text_input("Merchant name", value=selected["canonical_name"])
        active = st.checkbox("Active", value=selected.get("active", True))
        aliases = ", ".join(selected.get("aliases") or [])
        st.caption(f"Recognized names: {aliases or 'None yet'}")
        save = st.form_submit_button("Save merchant", type="primary")
    if save:
        try:
            store.update_merchant(selected["id"], new_name, active)
            st.success("Merchant and linked expenses updated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not update merchant: {exc}")
    with st.expander("Merge duplicate merchant records"):
        st.caption("Move all expenses from one merchant into another, then deactivate the old record.")
        with st.form("merge_merchants"):
            source_name = st.selectbox("Merge from", list(merchant_map))
            target_name = st.selectbox("Merge into", list(merchant_map), index=min(1, len(merchant_map) - 1))
            merge = st.form_submit_button("Merge merchants", disabled=len(merchant_map) < 2)
        if merge:
            try:
                store.merge_merchants(merchant_map[source_name]["id"], merchant_map[target_name]["id"])
                st.success(f"Merged {source_name} into {target_name}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not merge merchants: {exc}")


def team_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Team access")
    st.caption("Add an email here first. The invited person can then open the app and create an account with that exact email.")
    with st.form("invite_user", clear_on_submit=True):
        a, b, c = st.columns([2, 2, 1])
        email = a.text_input("Email")
        full_name = b.text_input("Name")
        role = c.selectbox("Role", ["member", "admin"])
        invite = st.form_submit_button("Allow this email", type="primary")
    if invite:
        try:
            store.invite_email(email, full_name, role, user["email"])
            st.success(f"{email.strip().lower()} can now create an account. Send them the Streamlit app link.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not add user: {exc}")
    users = store.list_users()
    if users:
        st.dataframe(pd.DataFrame(users)[["email", "full_name", "role", "active", "invited_at", "last_login_at"]], hide_index=True, use_container_width=True)
        choices = [row["email"] for row in users if row["email"] != user["email"]]
        if choices:
            selected = st.selectbox("Change user access", choices)
            current = next(row for row in users if row["email"] == selected)
            action = "Disable access" if current["active"] else "Restore access"
            if st.button(action):
                store.set_user_access(selected, not current["active"])
                st.success(f"Access updated for {selected}.")
                st.rerun()


def full_backup_zip(store: TrackerStore) -> bytes:
    payload = store.backup_payload()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("receipt-tracker-backup.json", json.dumps(payload, indent=2, default=str))
        for expense in payload["tables"].get("expenses", []):
            if expense.get("receipt_path"):
                try:
                    name = Path(expense.get("receipt_name") or "receipt").name
                    archive.writestr(f"attachments/{expense['id']}/{name}", store.download_receipt(expense["receipt_path"]))
                except Exception:
                    pass
    return output.getvalue()


def restore_backup_zip(store: TrackerStore, content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        payload = json.loads(archive.read("receipt-tracker-backup.json"))
        store.restore_backup_payload(payload)
        for expense in payload["tables"].get("expenses", []):
            if not expense.get("receipt_path"):
                continue
            name = Path(expense.get("receipt_name") or "receipt").name
            archive_name = f"attachments/{expense['id']}/{name}"
            if archive_name in archive.namelist():
                store.restore_receipt_at_path(expense["receipt_path"], archive.read(archive_name), expense.get("receipt_mime") or "application/octet-stream")


def settings_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Settings")
    st.write(f"Signed in as **{user['email']}**")
    st.write(f"Role: **{user.get('role', 'member')}**")
    st.subheader("Backup and restore")
    st.caption("The portable ZIP contains structured records plus original receipt attachments. Keep it somewhere secure.")
    if st.button("Prepare full backup"):
        with st.spinner("Collecting records and receipt files…"):
            st.session_state.full_backup = full_backup_zip(store)
    if st.session_state.get("full_backup"):
        st.download_button(
            "Download full backup ZIP", st.session_state.full_backup,
            file_name=f"receipt-tracker-backup-{date.today().isoformat()}.zip", mime="application/zip",
        )
    if user.get("role") == "admin":
        with st.expander("Restore a backup"):
            backup_file = st.file_uploader("Receipt Tracker backup ZIP", type=["zip"], key="restore_backup")
            confirm_restore = st.checkbox("I understand matching records will be updated from this backup")
            if st.button("Restore backup", disabled=backup_file is None or not confirm_restore):
                try:
                    restore_backup_zip(store, backup_file.getvalue())
                    st.success("Backup restored.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Restore failed: {exc}")
    st.subheader("Expense categories")
    st.write(", ".join(store.list_categories()))
    if user.get("role") == "admin":
        with st.form("new_category", clear_on_submit=True):
            category = st.text_input("Add category")
            add = st.form_submit_button("Add")
        if add:
            try:
                store.add_category(category)
                st.success("Category added.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not add category: {exc}")


def main() -> None:
    store = configured_store()
    if store is None:
        setup_required()
    for email in admin_emails():
        try:
            store.ensure_admin(email)
        except Exception as exc:
            st.error(f"Supabase setup is incomplete: {exc}")
            st.info("Run `supabase_setup.sql` in the Supabase SQL Editor, then reboot the app.")
            st.stop()
    if "user" not in st.session_state:
        auth_screen(store)
    user = st.session_state.user
    allowed = store.get_allowed_user(user["email"])
    if not allowed or not allowed.get("active"):
        st.session_state.pop("user", None)
        st.error("Your access is no longer active.")
        st.stop()
    user["role"] = allowed.get("role", "member")
    with st.sidebar:
        st.title("🧾 Receipt Tracker")
        st.caption(user.get("full_name") or user["email"])
        pages = [
            "Dashboard", "Receipt inbox", "Expenses", "Duplicate review", "Reimbursements",
            "Reports", "Categories & rules", "Merchants", "Businesses", "Recycle bin", "Settings",
        ]
        if user.get("role") == "admin":
            pages.insert(-1, "Team")
        page = st.radio("Navigation", pages, label_visibility="collapsed")
        st.divider()
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    try:
        if page == "Dashboard":
            dashboard_page(store)
        elif page == "Receipt inbox":
            add_expense_page(store, user)
        elif page == "Expenses":
            expenses_page(store, user)
        elif page == "Duplicate review":
            duplicates_page(store, user)
        elif page == "Reimbursements":
            reimbursements_page(store, user)
        elif page == "Reports":
            reports_page(store)
        elif page == "Businesses":
            businesses_page(store, user)
        elif page == "Merchants":
            merchants_page(store)
        elif page == "Categories & rules":
            rules_page(store, user)
        elif page == "Recycle bin":
            recycle_bin_page(store, user)
        elif page == "Team":
            team_page(store, user)
        else:
            settings_page(store, user)
    except Exception as exc:
        st.error(f"The app could not complete this action: {exc}")


if __name__ == "__main__":
    main()
