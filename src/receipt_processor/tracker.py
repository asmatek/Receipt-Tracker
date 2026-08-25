"""Supabase-backed persistence for the Streamlit receipt tracker."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

from .workflow import hamming_hex, is_reimbursement_eligible, jaccard_similarity, signed_total, text_fingerprint


DEFAULT_CATEGORIES = [
    "Advertising & Marketing", "Airfare", "Bank Fees", "Contract Labor", "Education & Training",
    "Entertainment", "Equipment", "Fuel", "Hotels & Lodging", "Insurance", "Medical",
    "Parking & Tolls", "Restaurants & Meals", "Office Supplies", "Professional Services",
    "Rent", "Repairs & Maintenance", "Refunds & Credits", "Shipping & Postage",
    "Software & Subscriptions", "Taxes & Government Fees", "Transportation", "Utilities", "Other",
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


def normalize_merchant_name(value: str) -> str:
    """Return a stable key while preserving the editable display name separately."""
    value = (value or "").strip().lower()
    value = re.sub(r"\b(incorporated|corporation|company|limited|inc|corp|co|ltd|llc)\b", "", value)
    return re.sub(r"[^a-z0-9]", "", value)


def merchant_similarity(first: str, second: str) -> float:
    left, right = normalize_merchant_name(first), normalize_merchant_name(second)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


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

    def list_merchant_rules(self) -> list[dict[str, Any]]:
        return self.admin_client.table("merchant_rules").select("*").order("match_text").execute().data or []

    def save_merchant_rule(self, match_text: str, category: str, project: str, tags: list[str], actor_email: str) -> None:
        match_text = re.sub(r"\s+", " ", match_text.strip().lower())
        if not match_text:
            raise ValueError("Merchant match text is required")
        self.admin_client.table("merchant_rules").upsert({
            "match_text": match_text, "category": category or None, "project": project.strip() or None,
            "tags": sorted({tag.strip() for tag in tags if tag.strip()}), "active": True,
            "created_by": normalize_email(actor_email), "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="match_text").execute()

    def set_merchant_rule_active(self, rule_id: str, active: bool) -> None:
        self.admin_client.table("merchant_rules").update({"active": active}).eq("id", rule_id).execute()

    def matching_merchant_rule(self, merchant: str) -> Optional[dict[str, Any]]:
        candidate = (merchant or "").lower()
        return next((row for row in self.list_merchant_rules() if row.get("active") and row.get("match_text", "") in candidate), None)

    def list_merchants(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = self.admin_client.table("merchants").select("*").order("canonical_name")
        if active_only:
            query = query.eq("active", True)
        return query.execute().data or []

    def get_or_create_merchant(self, name: str, actor_email: str) -> Optional[dict[str, Any]]:
        display_name = re.sub(r"\s+", " ", (name or "").strip())
        normalized = normalize_merchant_name(display_name)
        if not normalized:
            return None
        rows = self.admin_client.table("merchants").select("*").eq(
            "normalized_name", normalized
        ).limit(1).execute().data or []
        if rows:
            return rows[0]
        try:
            return self.admin_client.table("merchants").insert({
                "canonical_name": display_name,
                "normalized_name": normalized,
                "aliases": [display_name],
                "created_by": normalize_email(actor_email),
            }).execute().data[0]
        except Exception:
            rows = self.admin_client.table("merchants").select("*").eq(
                "normalized_name", normalized
            ).limit(1).execute().data or []
            if rows:
                return rows[0]
            raise

    def update_merchant(self, merchant_id: str, canonical_name: str, active: bool = True) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", canonical_name.strip())
        if not name:
            raise ValueError("Merchant name is required")
        rows = self.admin_client.table("merchants").select("*").eq("id", merchant_id).limit(1).execute().data
        before = rows[0] if rows else {}
        aliases = list(before.get("aliases") or [])
        for alias in (before.get("canonical_name"), name):
            if alias and alias not in aliases:
                aliases.append(alias)
        updated = self.admin_client.table("merchants").update({
            "canonical_name": name,
            "normalized_name": normalize_merchant_name(name),
            "aliases": aliases,
            "active": active,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", merchant_id).execute().data[0]
        self.admin_client.table("expenses").update({"vendor": name}).eq("merchant_id", merchant_id).execute()
        return updated

    def merge_merchants(self, source_id: str, target_id: str) -> None:
        if source_id == target_id:
            raise ValueError("Choose two different merchants")
        targets = self.admin_client.table("merchants").select("canonical_name").eq("id", target_id).limit(1).execute().data
        if not targets:
            raise ValueError("Destination merchant was not found")
        self.admin_client.table("expenses").update({
            "merchant_id": target_id,
            "vendor": targets[0]["canonical_name"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("merchant_id", source_id).execute()
        self.admin_client.table("merchants").update({"active": False}).eq("id", source_id).execute()

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

    def find_possible_duplicates(
        self, vendor: str, expense_date: Any, total: Any,
        receipt_sha256: Optional[str] = None, exclude_id: Optional[str] = None,
        text_sha256: Optional[str] = None, receipt_number: str = "", raw_text: str = "",
        currency: str = "USD", perceptual_hash_value: str = "",
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}

        def add(rows: list[dict[str, Any]], reason: str, score: float) -> None:
            for row in rows:
                if row.get("id") == exclude_id or row.get("deleted_at"):
                    continue
                item = candidates.setdefault(row["id"], {**row, "duplicate_reasons": [], "duplicate_score": 0.0})
                if reason not in item["duplicate_reasons"]:
                    item["duplicate_reasons"].append(reason)
                    item["duplicate_score"] = round(float(item["duplicate_score"]) + score, 3)

        fields = "id,vendor,expense_date,total,currency,receipt_name,receipt_number,raw_text,perceptual_hash,deleted_at"
        exact = self.admin_client.table("expenses").select(fields).eq(
            "duplicate_key", duplicate_key(vendor, expense_date, total)
        ).execute().data or []
        exact = [row for row in exact if (row.get("currency") or "USD") == (currency or "USD")]
        add(exact, "Same merchant, date, total and currency", .85)
        if receipt_sha256:
            rows = self.admin_client.table("expenses").select(fields).eq("receipt_sha256", receipt_sha256).execute().data or []
            add(rows, "Identical file (same SHA-256 hash)", 1.0)
        if text_sha256:
            rows = self.admin_client.table("expenses").select(fields).eq("text_sha256", text_sha256).execute().data or []
            add(rows, "Identical extracted document text", .9)
        if receipt_number.strip():
            rows = self.admin_client.table("expenses").select(fields).ilike("receipt_number", receipt_number.strip()).execute().data or []
            add(rows, f"Same receipt or invoice number ({receipt_number.strip()})", .8)
        if perceptual_hash_value:
            rows = self.admin_client.table("expenses").select(fields).neq("perceptual_hash", "").execute().data or []
            for row in rows:
                if hamming_hex(perceptual_hash_value, row.get("perceptual_hash") or "") <= 5:
                    add([row], "Visually near-identical receipt image", .8)
        try:
            parsed_date = datetime.fromisoformat(str(expense_date)[:10]).date()
        except (TypeError, ValueError):
            parsed_date = None
        if parsed_date and money(total) is not None:
            nearby = self.admin_client.table("expenses").select(
                fields
            ).gte("expense_date", (parsed_date - timedelta(days=2)).isoformat()).lte(
                "expense_date", (parsed_date + timedelta(days=2)).isoformat()
            ).execute().data or []
            amount = money(total) or 0
            for row in nearby:
                if row.get("id") == exclude_id:
                    continue
                same_amount = (
                    abs((money(row.get("total")) or 0) - amount) <= 0.01
                    and (row.get("currency") or "USD") == (currency or "USD")
                )
                if same_amount and merchant_similarity(vendor, row.get("vendor") or "") >= 0.86:
                    add([row], "Very similar merchant with the same amount near the same date", .7)
                similarity = jaccard_similarity(raw_text, row.get("raw_text") or "") if raw_text else 0
                if similarity >= .82:
                    add([row], f"Very similar document text ({similarity:.0%} match)", .7)
        return sorted(candidates.values(), key=lambda row: row["duplicate_score"], reverse=True)

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
        merchant = self.get_or_create_merchant(payload.get("vendor", ""), actor["email"])
        if merchant:
            payload["merchant_id"] = merchant["id"]
            payload["vendor"] = merchant["canonical_name"]
        rule = self.matching_merchant_rule(payload.get("vendor", ""))
        if rule:
            payload["category"] = rule.get("category") or payload.get("category")
            payload["project_name"] = rule.get("project") or payload.get("project_name")
            payload["tags"] = sorted(set((payload.get("tags") or []) + (rule.get("tags") or [])))
        payload = self._clean_expense(payload)
        payload.update({"created_by": actor["id"], "created_by_email": actor["email"]})
        created = self.admin_client.table("expenses").insert(payload).execute().data[0]
        self._audit(created["id"], "create", actor, None, created)
        return created

    def update_expense(self, expense_id: str, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        before = self.get_expense(expense_id)
        merchant = self.get_or_create_merchant(payload.get("vendor", ""), actor["email"])
        if merchant:
            payload["merchant_id"] = merchant["id"]
            payload["vendor"] = merchant["canonical_name"]
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
            deleted_at = datetime.now(timezone.utc).isoformat()
            self.admin_client.table("expenses").update({"deleted_at": deleted_at}).eq("id", expense_id).execute()
            self._audit(expense_id, "delete", actor, before, {**before, "deleted_at": deleted_at})
            count += 1
        return count

    def restore_expenses(self, ids: Iterable[str], actor: dict[str, Any]) -> int:
        count = 0
        for expense_id in ids:
            before = self.get_expense(expense_id)
            if before and before.get("deleted_at"):
                self.admin_client.table("expenses").update({"deleted_at": None}).eq("id", expense_id).execute()
                self._audit(expense_id, "restore", actor, before, {**before, "deleted_at": None})
                count += 1
        return count

    def permanently_delete_expenses(self, ids: Iterable[str]) -> int:
        count = 0
        for expense_id in ids:
            expense = self.get_expense(expense_id)
            if not expense or not expense.get("deleted_at"):
                continue
            self.admin_client.table("expenses").delete().eq("id", expense_id).execute()
            self.delete_receipt(expense.get("receipt_path") or "")
            count += 1
        return count

    def get_expense(self, expense_id: str) -> Optional[dict[str, Any]]:
        rows = self.admin_client.table("expenses").select("*,businesses(name)").eq("id", expense_id).limit(1).execute().data
        return rows[0] if rows else None

    def list_expenses(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        rows = self.admin_client.table("expenses").select("*,businesses(name)").order(
            "expense_date", desc=True
        ).order("created_at", desc=True).execute().data or []
        return rows if include_deleted else [row for row in rows if not row.get("deleted_at")]

    def set_review_status(self, expense_ids: Iterable[str], review_status: str, actor: dict[str, Any]) -> int:
        if review_status not in {"needs_review", "approved", "rejected"}:
            raise ValueError("Invalid review status")
        resolved = [self.get_expense(expense_id) for expense_id in expense_ids]
        if review_status == "approved":
            blocked = [
                row for row in resolved if row and (
                    row.get("possible_duplicate") or row.get("duplicate_of")
                    or not row.get("vendor") or not row.get("expense_date") or not money(row.get("total"))
                )
            ]
            if blocked:
                raise ValueError("Resolve duplicates and confirm merchant, date, and a non-zero total before approval")
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        for before in resolved:
            if not before:
                continue
            expense_id = before["id"]
            update = {"review_status": review_status, "approved_at": now if review_status == "approved" else None, "updated_at": now}
            after = self.admin_client.table("expenses").update(update).eq("id", expense_id).execute().data[0]
            self._audit(expense_id, "update", actor, before, after)
            count += 1
        return count

    def record_duplicate_decision(
        self, expense_id: str, matched_expense_id: str, decision: str,
        reasons: list[str], actor: dict[str, Any],
    ) -> None:
        if decision not in {"keep_both", "marked_duplicate"}:
            raise ValueError("Invalid duplicate decision")
        self.admin_client.table("duplicate_decisions").upsert({
            "expense_id": expense_id, "matched_expense_id": matched_expense_id,
            "decision": decision, "reasons": reasons, "actor_email": actor["email"],
        }, on_conflict="expense_id,matched_expense_id").execute()
        update = {
            "possible_duplicate": False,
            "duplicate_of": matched_expense_id if decision == "marked_duplicate" else None,
            "duplicate_candidate_id": None,
            "duplicate_reasons": reasons,
        }
        self.admin_client.table("expenses").update(update).eq("id", expense_id).execute()

    def create_reimbursement_batch(
        self, name: str, from_date: Any, to_date: Any, notes: str,
        expense_ids: list[str], actor: dict[str, Any],
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Batch name is required")
        expenses = [self.get_expense(expense_id) for expense_id in expense_ids]
        eligible = [row for row in expenses if row and is_reimbursement_eligible(row)]
        if len(eligible) != len(expense_ids):
            raise ValueError("Every selected expense must be approved, business-related, reimbursable, and outside another batch")
        batch = self.admin_client.table("reimbursement_batches").insert({
            "name": name, "from_date": str(from_date)[:10], "to_date": str(to_date)[:10],
            "notes": notes.strip() or None, "created_by": actor["id"], "created_by_email": actor["email"],
        }).execute().data[0]
        if expense_ids:
            self.admin_client.table("reimbursement_batch_expenses").insert([
                {"batch_id": batch["id"], "expense_id": expense_id} for expense_id in expense_ids
            ]).execute()
            self.admin_client.table("expenses").update({"reimbursement_status": "in_batch"}).in_("id", expense_ids).execute()
        return batch

    def list_reimbursement_batches(self) -> list[dict[str, Any]]:
        return self.admin_client.table("reimbursement_batches").select(
            "*,reimbursement_batch_expenses(expense_id)"
        ).order("created_at", desc=True).execute().data or []

    def reimbursement_batch_expenses(self, batch_id: str) -> list[dict[str, Any]]:
        links = self.admin_client.table("reimbursement_batch_expenses").select("expense_id").eq("batch_id", batch_id).execute().data or []
        ids = [row["expense_id"] for row in links]
        if not ids:
            return []
        return self.admin_client.table("expenses").select("*,businesses(name)").in_("id", ids).execute().data or []

    def set_reimbursement_batch_status(self, batch_id: str, status: str) -> None:
        if status not in {"draft", "submitted", "paid", "cancelled"}:
            raise ValueError("Invalid batch status")
        now = datetime.now(timezone.utc).isoformat()
        update: dict[str, Any] = {"status": status}
        if status == "submitted":
            update["submitted_at"] = now
        elif status == "paid":
            update["paid_at"] = now
        elif status == "cancelled":
            update["cancelled_at"] = now
        self.admin_client.table("reimbursement_batches").update(update).eq("id", batch_id).execute()
        expense_ids = [row["id"] for row in self.reimbursement_batch_expenses(batch_id)]
        if expense_ids:
            expense_update: dict[str, Any] = {}
            if status == "paid":
                expense_update = {"reimbursement_status": "reimbursed", "reimbursed_at": now}
            elif status == "cancelled":
                expense_update = {"reimbursement_status": "not_reimbursed", "reimbursed_at": None}
            elif status in {"draft", "submitted"}:
                expense_update = {"reimbursement_status": "in_batch", "submitted_at": now if status == "submitted" else None}
            self.admin_client.table("expenses").update(expense_update).in_("id", expense_ids).execute()

    def remove_expenses_from_batch(self, batch_id: str, expense_ids: list[str]) -> None:
        batches = self.admin_client.table("reimbursement_batches").select("status").eq("id", batch_id).limit(1).execute().data or []
        if not batches or batches[0].get("status") != "draft":
            raise ValueError("Expenses can only be removed while the batch is a draft")
        if expense_ids:
            self.admin_client.table("reimbursement_batch_expenses").delete().eq("batch_id", batch_id).in_("expense_id", expense_ids).execute()
            self.admin_client.table("expenses").update({"reimbursement_status": "not_reimbursed"}).in_("id", expense_ids).execute()

    def backup_payload(self) -> dict[str, Any]:
        table_names = [
            "businesses", "categories", "merchants", "merchant_rules", "expenses",
            "reimbursement_batches", "reimbursement_batch_expenses", "duplicate_decisions",
        ]
        return {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tables": {
                name: self.admin_client.table(name).select("*").execute().data or []
                for name in table_names
            },
        }

    def restore_backup_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("version") != 1 or not isinstance(payload.get("tables"), dict):
            raise ValueError("This is not a supported Receipt Tracker backup")
        table_names = [
            "businesses", "categories", "merchants", "merchant_rules", "expenses",
            "reimbursement_batches", "reimbursement_batch_expenses", "duplicate_decisions",
        ]
        for name in table_names:
            rows = payload["tables"].get(name) or []
            if rows:
                self.admin_client.table(name).upsert(rows).execute()

    def restore_receipt_at_path(self, path: str, content: bytes, mime_type: str) -> None:
        if not path or not content:
            return
        self.admin_client.storage.from_("receipts").upload(
            path=path, file=content,
            file_options={"content-type": mime_type or "application/octet-stream", "upsert": "true"},
        )

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
            "vendor", "merchant_id", "expense_date", "transaction_time", "subtotal", "tax", "tip", "fees", "discount",
            "total", "currency", "usd_total", "exchange_rate", "category",
            "business_id", "business_purpose", "client_name", "project_name",
            "payment_method", "card_last4", "receipt_number", "location", "email_from", "email_subject", "email_received_at", "document_kind",
            "tags", "department", "attendees", "mileage_miles", "is_personal", "reimbursable",
            "notes", "items", "confidence", "field_sources", "raw_text", "receipt_path", "receipt_name", "receipt_mime",
            "receipt_sha256", "text_sha256", "perceptual_hash", "duplicate_key", "possible_duplicate", "duplicate_of", "duplicate_candidate_id",
            "duplicate_reasons", "duplicate_score", "missing_fields", "status", "review_status",
            "reimbursement_status", "approved_at", "submitted_at", "reimbursed_at", "deleted_at",
        }
        clean = {key: value for key, value in payload.items() if key in allowed}
        for key in ("subtotal", "tax", "tip", "fees", "discount", "total", "usd_total", "exchange_rate", "mileage_miles", "duplicate_score"):
            clean[key] = money(clean.get(key))
        clean["fees"] = clean.get("fees") or 0.0
        clean["mileage_miles"] = clean.get("mileage_miles") or 0.0
        clean["duplicate_score"] = clean.get("duplicate_score") or 0.0
        clean["total"] = signed_total(clean.get("total"), clean.get("document_kind", "receipt"))
        if clean.get("raw_text") and not clean.get("text_sha256"):
            clean["text_sha256"] = text_fingerprint(clean["raw_text"])
        clean["duplicate_key"] = duplicate_key(clean.get("vendor", ""), clean.get("expense_date"), clean["total"])
        missing = [field for field in ("vendor", "expense_date", "total", "business_id", "category", "business_purpose") if not clean.get(field)]
        clean["missing_fields"] = missing
        clean["status"] = "needs_review" if missing or clean.get("possible_duplicate") else "complete"
        clean["review_status"] = clean.get("review_status") or "needs_review"
        if missing or clean.get("possible_duplicate"):
            clean["review_status"] = "needs_review"
        clean["approved_at"] = (
            datetime.now(timezone.utc).isoformat() if clean["review_status"] == "approved" else None
        )
        return clean
