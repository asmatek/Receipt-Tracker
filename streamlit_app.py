"""Multi-user Streamlit receipt and business-expense tracker."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from receipt_processor import ReceiptProcessingError, ReceiptProcessor, TrackerStore  # noqa: E402
from receipt_processor.tracker import duplicate_key, money  # noqa: E402


st.set_page_config(page_title="Receipt Tracker", page_icon="🧾", layout="wide")

CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN"]
PAYMENT_METHODS = ["Business card", "Personal card", "Cash", "Bank transfer", "Other"]


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
        ("Meals", ("restaurant", "cafe", "coffee", "grill", "kitchen", "doordash", "uber eats")),
        ("Travel", ("hotel", "airlines", "airways", "booking", "marriott", "hilton")),
        ("Software", ("software", "subscription", "microsoft", "google cloud", "openai", "adobe")),
        ("Office Supplies", ("office depot", "staples", "printer", "paper", "ink")),
        ("Vehicle", ("parking", "toll", "auto parts", "car wash")),
        ("Utilities", ("electric", "water", "internet", "utility", "phone")),
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
        for column in ("subtotal", "tax", "tip", "discount", "total", "usd_total"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame


def dashboard_page(store: TrackerStore) -> None:
    st.header("Dashboard")
    frame = expense_frame(store.list_expenses())
    if frame.empty:
        st.info("No expenses yet. Use **Add expense** to upload your first receipt.")
        return
    today = pd.Timestamp.today()
    month = frame[(frame["expense_date"].dt.year == today.year) & (frame["expense_date"].dt.month == today.month)]
    year = frame[frame["expense_date"].dt.year == today.year]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("This month", f"${month['usd_total'].sum():,.2f}")
    m2.metric("This year", f"${year['usd_total'].sum():,.2f}")
    m3.metric("Receipts", f"{len(frame):,}")
    m4.metric("Needs review", int((frame["status"] == "needs_review").sum()))
    left, right = st.columns(2)
    with left:
        st.subheader("Monthly expenses")
        monthly = frame.dropna(subset=["expense_date"]).copy()
        monthly["month"] = monthly["expense_date"].dt.to_period("M").astype(str)
        st.bar_chart(monthly.groupby("month", as_index=False)["usd_total"].sum().set_index("month"), y="usd_total", color="#176B87")
    with right:
        st.subheader("By category")
        category = frame.groupby("category", dropna=False, as_index=False)["usd_total"].sum().sort_values("usd_total", ascending=False)
        category["category"] = category["category"].fillna("Uncategorized")
        st.bar_chart(category.set_index("category"), y="usd_total", horizontal=True, color="#E07A5F")
    st.subheader("Recent expenses")
    recent = frame.sort_values("expense_date", ascending=False).head(10)
    st.dataframe(
        recent[["expense_date", "vendor", "business", "category", "usd_total", "status"]],
        hide_index=True, use_container_width=True,
        column_config={"expense_date": st.column_config.DateColumn("Date"), "usd_total": st.column_config.NumberColumn("USD total", format="$%.2f")},
    )


def extract_uploaded_receipt(uploaded: Any, language: str) -> dict[str, Any]:
    suffix = Path(uploaded.name).suffix.lower()
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(uploaded.getvalue())
            temporary_path = Path(temporary.name)
        processor = ReceiptProcessor(
            temporary_path, language=language, min_confidence=45,
            preprocess=True, deskew=True, denoise=True, threshold=True,
        )
        return processor.parse()
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def add_expense_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Add expense")
    businesses = store.list_businesses()
    categories = store.list_categories()
    if not businesses:
        st.warning("Create at least one business on the **Businesses** page before saving an expense.")
    uploaded = st.file_uploader(
        "Photograph or upload a receipt",
        type=["png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "pdf"],
        help="The original file will be preserved in private Supabase storage.",
    )
    c1, c2 = st.columns([2, 1])
    with c1:
        language = st.selectbox("Receipt language", ["eng", "spa", "fra", "deu"], format_func={"eng": "English", "spa": "Spanish", "fra": "French", "deu": "German"}.get)
    with c2:
        extract = st.button("Extract receipt", type="primary", disabled=uploaded is None, use_container_width=True)
    if extract and uploaded:
        try:
            with st.spinner("Reading receipt…"):
                parsed_receipt = extract_uploaded_receipt(uploaded, language)
            raw = uploaded.getvalue()
            st.session_state.expense_draft = {
                "parsed": parsed_receipt, "file_bytes": raw, "file_name": uploaded.name,
                "mime_type": uploaded.type or "application/octet-stream", "sha256": hashlib.sha256(raw).hexdigest(),
            }
            st.success("Receipt extracted. Review the information below before saving.")
        except (ReceiptProcessingError, OSError, ValueError) as exc:
            st.error(f"Receipt extraction failed: {exc}")
    draft = st.session_state.get("expense_draft", {})
    parsed = draft.get("parsed", {})
    if not draft:
        st.info("Upload a receipt and click **Extract receipt**, or enter the expense manually below.")
    business_options = {row["name"]: row["id"] for row in businesses}
    default_category = classify_category(parsed.get("vendor", ""), parsed.get("raw_text", ""), categories)
    with st.form("expense_form", clear_on_submit=False):
        st.subheader("Review expense")
        a, b, c = st.columns(3)
        vendor = a.text_input("Merchant", value=parsed.get("vendor") or "")
        expense_date = b.date_input("Date", value=parse_date(parsed.get("date")))
        currency = c.selectbox("Currency", CURRENCIES, index=CURRENCIES.index(parsed.get("currency")) if parsed.get("currency") in CURRENCIES else 0)
        a, b, c, d, e = st.columns(5)
        subtotal = a.number_input("Subtotal", min_value=0.0, value=money(parsed.get("subtotal")) or 0.0, step=0.01)
        tax = b.number_input("Tax", min_value=0.0, value=money(parsed.get("tax")) or 0.0, step=0.01)
        tip = c.number_input("Tip", min_value=0.0, value=money(parsed.get("tip")) or 0.0, step=0.01)
        discount = d.number_input("Discount", min_value=0.0, value=money(parsed.get("discount")) or 0.0, step=0.01)
        total = e.number_input("Total", min_value=0.0, value=money(parsed.get("total")) or 0.0, step=0.01)
        exchange_rate = 1.0
        if currency != "USD":
            exchange_rate = st.number_input(f"{currency} to USD exchange rate", min_value=0.000001, value=1.0, format="%.6f")
        usd_total = total * exchange_rate
        a, b = st.columns(2)
        business_name = a.selectbox("Business", list(business_options) or ["Create a business first"])
        category = b.selectbox("Category", categories, index=categories.index(default_category) if default_category in categories else 0)
        business_purpose = st.text_input("Business purpose", placeholder="Why was this expense necessary?")
        a, b, c = st.columns(3)
        client_name = a.text_input("Client (optional)")
        project_name = b.text_input("Project (optional)")
        payment_method = c.selectbox("Payment method", PAYMENT_METHODS)
        notes = st.text_area("Notes")
        raw_text = st.text_area("OCR text", value=parsed.get("raw_text", ""), height=140)
        duplicate_reviewed = st.checkbox("Save even if a possible duplicate is detected")
        save = st.form_submit_button("Save expense", type="primary", use_container_width=True, disabled=not businesses)
    if save:
        key = duplicate_key(vendor, expense_date, total)
        duplicates = store.find_duplicates(key, draft.get("sha256"))
        if duplicates and not duplicate_reviewed:
            st.error("Possible duplicate found. Review the matching receipt below, then select the duplicate checkbox if this is a separate purchase.")
            st.dataframe(pd.DataFrame(duplicates), hide_index=True, use_container_width=True)
            return
        receipt_path = None
        try:
            if draft.get("file_bytes"):
                receipt_path, receipt_hash = store.upload_receipt(draft["file_bytes"], draft["file_name"], draft["mime_type"], user["id"])
            else:
                receipt_hash = None
            payload = {
                "vendor": vendor.strip(), "expense_date": expense_date.isoformat(), "subtotal": subtotal,
                "tax": tax, "tip": tip, "discount": discount, "total": total, "currency": currency,
                "exchange_rate": exchange_rate, "usd_total": usd_total, "business_id": business_options[business_name],
                "category": category, "business_purpose": business_purpose.strip(), "client_name": client_name.strip(),
                "project_name": project_name.strip(), "payment_method": payment_method, "notes": notes.strip(),
                "items": parsed.get("items") or [], "raw_text": raw_text, "receipt_path": receipt_path,
                "receipt_name": draft.get("file_name"), "receipt_mime": draft.get("mime_type"),
                "receipt_sha256": receipt_hash, "possible_duplicate": bool(duplicates),
                "duplicate_of": duplicates[0]["id"] if duplicates else None,
            }
            created = store.create_expense(payload, user)
            st.session_state.pop("expense_draft", None)
            st.success(f"Expense saved: {vendor or 'Unnamed merchant'} — ${usd_total:,.2f}")
            st.session_state.selected_expense_id = created["id"]
        except Exception as exc:
            if receipt_path:
                store.delete_receipt(receipt_path)
            st.error(f"Could not save the expense: {exc}")


def filtered_expenses(frame: pd.DataFrame, businesses: list[str], categories: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    with st.expander("Search and filters", expanded=True):
        a, b, c = st.columns(3)
        search = a.text_input("Search merchant, purpose, client, or project")
        selected_businesses = b.multiselect("Business", businesses)
        selected_categories = c.multiselect("Category", categories)
        a, b, c = st.columns(3)
        start = a.date_input("From", value=(date.today() - timedelta(days=365)), key="expense_filter_start")
        end = b.date_input("To", value=date.today(), key="expense_filter_end")
        review_only = c.checkbox("Needs review only")
    result = frame[(frame["expense_date"].dt.date >= start) & (frame["expense_date"].dt.date <= end)]
    if search:
        columns = [column for column in ("vendor", "business_purpose", "client_name", "project_name") if column in result]
        mask = result[columns].fillna("").astype(str).apply(lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
        result = result[mask]
    if selected_businesses:
        result = result[result["business"].isin(selected_businesses)]
    if selected_categories:
        result = result[result["category"].isin(selected_categories)]
    if review_only:
        result = result[result["status"] == "needs_review"]
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
        filtered[["expense_date", "vendor", "business", "category", "usd_total", "status", "possible_duplicate"]],
        hide_index=True, use_container_width=True,
        column_config={"expense_date": st.column_config.DateColumn("Date"), "usd_total": st.column_config.NumberColumn("USD total", format="$%.2f"), "possible_duplicate": st.column_config.CheckboxColumn("Duplicate?")},
    )
    label_to_id = {
        f"{row['expense_date'].date() if pd.notna(row['expense_date']) else 'No date'} — {row.get('vendor') or 'No merchant'} — ${row.get('usd_total', 0):,.2f} — {str(row['id'])[:8]}": row["id"]
        for _, row in filtered.iterrows()
    }
    selected_labels = st.multiselect("Select expenses for batch actions", list(label_to_id))
    selected_ids = [label_to_id[label] for label in selected_labels]
    a, b = st.columns([1, 3])
    confirm_delete = b.checkbox("I understand selected expenses and their receipt files will be permanently deleted")
    if a.button("Delete selected", disabled=not selected_ids or not confirm_delete):
        try:
            deleted = store.delete_expenses(selected_ids, user)
            st.success(f"Deleted {deleted} expense(s).")
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
            a, b, c, d, e = st.columns(5)
            subtotal = a.number_input("Subtotal", min_value=0.0, value=money(expense.get("subtotal")) or 0.0, step=0.01, key=f"sub_{expense['id']}")
            tax = b.number_input("Tax", min_value=0.0, value=money(expense.get("tax")) or 0.0, step=0.01, key=f"tax_{expense['id']}")
            tip = c.number_input("Tip", min_value=0.0, value=money(expense.get("tip")) or 0.0, step=0.01, key=f"tip_{expense['id']}")
            discount = d.number_input("Discount", min_value=0.0, value=money(expense.get("discount")) or 0.0, step=0.01, key=f"discount_{expense['id']}")
            total = e.number_input("Total", min_value=0.0, value=money(expense.get("total")) or 0.0, step=0.01, key=f"total_{expense['id']}")
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
            notes = st.text_area("Notes", value=expense.get("notes") or "")
            save = st.form_submit_button("Save changes", type="primary", use_container_width=True)
        if save:
            try:
                matches = store.find_duplicates(duplicate_key(vendor, expense_date, total), expense.get("receipt_sha256"), expense["id"])
                store.update_expense(expense["id"], {
                    "vendor": vendor, "expense_date": expense_date.isoformat(), "currency": currency,
                    "subtotal": subtotal, "tax": tax, "tip": tip, "discount": discount, "total": total,
                    "exchange_rate": exchange_rate, "usd_total": total * exchange_rate,
                    "business_id": business_map[business_name], "category": category, "business_purpose": purpose,
                    "client_name": client, "project_name": project, "payment_method": payment, "notes": notes,
                    "possible_duplicate": bool(matches), "duplicate_of": matches[0]["id"] if matches else None,
                }, user)
                st.success("Expense updated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Update failed: {exc}")
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
    a, b, c = st.columns(3)
    period = a.selectbox("Period", ["Monthly", "Quarterly", "Yearly"])
    group = b.selectbox("Group by", ["Category", "Business", "Project", "Client"])
    years = sorted(frame["expense_date"].dropna().dt.year.unique(), reverse=True)
    year = c.selectbox("Year", years)
    report = frame[frame["expense_date"].dt.year == year].copy()
    if period == "Monthly":
        report["period"] = report["expense_date"].dt.strftime("%Y-%m")
    elif period == "Quarterly":
        report["period"] = report["expense_date"].dt.to_period("Q").astype(str)
    else:
        report["period"] = report["expense_date"].dt.year.astype(str)
    group_column = {"Category": "category", "Business": "business", "Project": "project_name", "Client": "client_name"}[group]
    report[group_column] = report[group_column].fillna("Unassigned").replace("", "Unassigned")
    summary = report.groupby(["period", group_column], as_index=False).agg(amount=("usd_total", "sum"), receipts=("id", "count"))
    st.metric(f"{year} total", f"${report['usd_total'].sum():,.2f}")
    st.bar_chart(summary.pivot(index="period", columns=group_column, values="amount").fillna(0), use_container_width=True)
    st.dataframe(summary, hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("USD amount", format="$%.2f")})
    columns = ["expense_date", "vendor", "business", "category", "business_purpose", "client_name", "project_name", "currency", "total", "usd_total"]
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


def settings_page(store: TrackerStore, user: dict[str, Any]) -> None:
    st.header("Settings")
    st.write(f"Signed in as **{user['email']}**")
    st.write(f"Role: **{user.get('role', 'member')}**")
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
        pages = ["Dashboard", "Add expense", "Expenses", "Reports", "Businesses", "Settings"]
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
        elif page == "Add expense":
            add_expense_page(store, user)
        elif page == "Expenses":
            expenses_page(store, user)
        elif page == "Reports":
            reports_page(store)
        elif page == "Businesses":
            businesses_page(store, user)
        elif page == "Team":
            team_page(store, user)
        else:
            settings_page(store, user)
    except Exception as exc:
        st.error(f"The app could not complete this action: {exc}")


if __name__ == "__main__":
    main()
