"""Supabase-backed persistence for the Streamlit receipt tracker."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4


DEFAULT_CATEGORIES = [
    "Advertising", "Bank Fees", "Contract Labor", "Education", "Equipment",
    "Fuel", "Insurance", "Meals", "Office Supplies", "Professional Services",
    "Rent", "Repairs & Maintenance", "Software", "Travel", "Utilities",
    "Vehicle", "Other",
]


class TrackerError(RuntimeError):
    """Raised when persistent tracker operations fail."""


def normalize_email(value: str) -> str:
    return value.strip().lower()


def money(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value).replace(",", "")).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None


def duplicate_key(vendor: str, expense_date: Any, total: Any) -> str:
    vendor_key = re.sub(r"[^a-z0-9]", "", (vendor or "").lower())
    date_key = str(expense_date or "")[:10]
    total_key = f"{money(total) or 0:.2f}"
    return hashlib.sha256(f"{vendor_key}|{date_key}|{total_key}".encode()).hexdigest()


class TrackerStore:
    """Service-layer wrapper around Supabase tables, auth, and private storage."""

    def __init__(self, url: str, anon_key: str, service_role_key: str) -> None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise TrackerError("The supabase Python package is not installed") from exc
        self._create_client = create_client
        self._url = url
        self._anon_key = anon_key
        self.admin_client = create_client(url, service_role_key)

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        email = normalize_email(email)
        auth_client = self._create_client(self._url, self._anon_key)
        response = auth_client.auth.sign_in_with_password({"email": email, "password": password})
        user = response.user
        session = response.session
        if not user or not session:
            raise TrackerError("Sign-in did not return a valid session")
        allowed = self.get_allowed_user(email)
        if not allowed or not allowed.get("active"):
            auth_client.auth.sign_out()
            raise TrackerError("This email has not been invited or access was disabled")
        self.admin_client.table("allowed_users").update(
            {"last_login_at": datetime.now(timezone.utc).isoformat()}
        ).eq("email", email).execute()
        return {
            "id": str(user.id),
            "email": email,
            "role": allowed.get("role", "member"),
            "full_name": allowed.get("full_name"),
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        }

    def sign_up_invited(self, email: str, password: str, full_name: str) -> str:
        email = normalize_email(email)
        allowed = self.get_allowed_user(email)
        if not allowed or not allowed.get("active"):
            raise TrackerError("Ask the administrator to invite this email first")
        auth_client = self._create_client(self._url, self._anon_key)
        auth_client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"full_name": full_name.strip()}},
        })
        if full_name.strip():
            self.admin_client.table("allowed_users").update(
                {"full_name": full_name.strip()}
            ).eq("email", email).execute()
        return "Account created. Confirm your email if Supabase sends a confirmation message, then sign in."

    def get_allowed_user(self, email: str) -> Optional[dict[str, Any]]:
        rows = self.admin_client.table("allowed_users").select("*").eq(
            "email", normalize_email(email)
        ).limit(1).execute().data
        return rows[0] if rows else None

    def ensure_admin(self, email: str, full_name: str = "") -> None:
        email = normalize_email(email)
        self.admin_client.table("allowed_users").upsert({
            "email": email, "full_name": full_name or None, "role": "admin",
            "active": True, "invited_by": "system",
        }).execute()

    def invite_email(self, email: str, full_name: str, role: str, actor_email: str) -> None:
        email = normalize_email(email)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address")
        self.admin_client.table("allowed_users").upsert({
            "email": email,
            "full_name": full_name.strip() or None,
            "role": role,
            "active": True,
            "invited_by": normalize_email(actor_email),
        }).execute()

    def list_users(self) -> list[dict[str, Any]]:
        return self.admin_client.table("allowed_users").select("*").order("invited_at").execute().data or []

    def set_user_access(self, email: str, active: bool) -> None:
        self.admin_client.table("allowed_users").update({"active": active}).eq(
            "email", normalize_email(email)
        ).execute()

    def list_businesses(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = self.admin_client.table("businesses").select("*").order("name")
        if active_only:
            query = query.eq("active", True)
        return query.execute().data or []

    def add_business(self, name: str, actor_email: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Business name is required")
        self.admin_client.table("businesses").insert({
            "name": name, "created_by": actor_email
        }).execute()

    def update_business(self, business_id: str, name: str, active: bool) -> None:
        self.admin_client.table("businesses").update({
            "name": name.strip(), "active": active,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", business_id).execute()

    def list_categories(self) -> list[str]:
        rows = self.admin_client.table("categories").select("name").eq("active", True).order("name").execute().data or []
        return [row["name"] for row in rows] or DEFAULT_CATEGORIES

    def add_category(self, name: str) -> None:
        name = name.strip()
        if name:
            self.admin_client.table("categories").upsert({"name": name, "active": True}).execute()

    def find_duplicates(self, key: str, receipt_sha256: Optional[str] = None, exclude_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = self.admin_client.table("expenses").select("id,vendor,expense_date,total,receipt_name").eq("duplicate_key", key)
        rows = query.execute().data or []
        if receipt_sha256:
            hash_rows = self.admin_client.table("expenses").select("id,vendor,expense_date,total,receipt_name").eq(
                "receipt_sha256", receipt_sha256
            ).execute().data or []
            rows.extend(hash_rows)
        unique = {row["id"]: row for row in rows if row.get("id") != exclude_id}
        return list(unique.values())

    def upload_receipt(self, content: bytes, filename: str, mime_type: str, user_id: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
        path = f"{user_id}/{date.today().isoformat()}/{uuid4()}-{safe_name}"
        self.admin_client.storage.from_("receipts").upload(
            path=path,
            file=content,
            file_options={"content-type": mime_type or "application/octet-stream", "upsert": "false"},
        )
        return path, digest

    def receipt_signed_url(self, path: str, expires: int = 900) -> Optional[str]:
        if not path:
            return None
        response = self.admin_client.storage.from_("receipts").create_signed_url(path, expires)
        if isinstance(response, dict):
            return response.get("signedURL") or response.get("signedUrl") or response.get("signed_url")
        return getattr(response, "signed_url", None)

    def download_receipt(self, path: str) -> bytes:
        return self.admin_client.storage.from_("receipts").download(path)

    def delete_receipt(self, path: str) -> None:
        if path:
            self.admin_client.storage.from_("receipts").remove([path])

    def create_expense(self, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        payload = self._clean_expense(payload)
        payload.update({"created_by": actor["id"], "created_by_email": actor["email"]})
        created = self.admin_client.table("expenses").insert(payload).execute().data[0]
        self._audit(created["id"], "create", actor, None, created)
        return created

    def update_expense(self, expense_id: str, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        before = self.get_expense(expense_id)
        clean = self._clean_expense(payload)
        clean["updated_at"] = datetime.now(timezone.utc).isoformat()
        updated = self.admin_client.table("expenses").update(clean).eq("id", expense_id).execute().data[0]
        self._audit(expense_id, "update", actor, before, updated)
        return updated

    def delete_expenses(self, ids: Iterable[str], actor: dict[str, Any]) -> int:
        count = 0
        for expense_id in ids:
            before = self.get_expense(expense_id)
            if not before:
                continue
            self._audit(expense_id, "delete", actor, before, None)
            self.admin_client.table("expenses").delete().eq("id", expense_id).execute()
            self.delete_receipt(before.get("receipt_path") or "")
            count += 1
        return count

    def get_expense(self, expense_id: str) -> Optional[dict[str, Any]]:
        rows = self.admin_client.table("expenses").select("*,businesses(name)").eq("id", expense_id).limit(1).execute().data
        return rows[0] if rows else None

    def list_expenses(self) -> list[dict[str, Any]]:
        return self.admin_client.table("expenses").select("*,businesses(name)").order(
            "expense_date", desc=True
        ).order("created_at", desc=True).execute().data or []

    def audit_history(self, expense_id: str) -> list[dict[str, Any]]:
        return self.admin_client.table("audit_log").select("*").eq("expense_id", expense_id).order(
            "created_at", desc=True
        ).execute().data or []

    def _audit(self, expense_id: str, action: str, actor: dict[str, Any], before: Any, after: Any) -> None:
        self.admin_client.table("audit_log").insert({
            "expense_id": expense_id,
            "action": action,
            "actor_id": actor.get("id"),
            "actor_email": actor.get("email"),
            "before_data": before,
            "after_data": after,
        }).execute()

    @staticmethod
    def _clean_expense(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "vendor", "expense_date", "subtotal", "tax", "tip", "discount",
            "total", "currency", "usd_total", "exchange_rate", "category",
            "business_id", "business_purpose", "client_name", "project_name",
            "payment_method", "notes", "items", "raw_text", "receipt_path",
            "receipt_name", "receipt_mime", "receipt_sha256", "duplicate_key",
            "possible_duplicate", "duplicate_of", "missing_fields", "status",
        }
        clean = {key: value for key, value in payload.items() if key in allowed}
        for key in ("subtotal", "tax", "tip", "discount", "total", "usd_total", "exchange_rate"):
            clean[key] = money(clean.get(key))
        clean["total"] = clean.get("total") or 0.0
        clean["duplicate_key"] = duplicate_key(clean.get("vendor", ""), clean.get("expense_date"), clean["total"])
        missing = [field for field in ("vendor", "expense_date", "total", "business_id", "category", "business_purpose") if not clean.get(field)]
        clean["missing_fields"] = missing
        clean["status"] = "needs_review" if missing or clean.get("possible_duplicate") else "complete"
        return clean
