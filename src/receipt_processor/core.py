"""OCR-backed receipt extraction with a testable text-only parsing path."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class ReceiptProcessingError(RuntimeError):
    """Raised when a receipt cannot be loaded, OCR'd, or exported."""


@dataclass(frozen=True)
class ReceiptItem:
    description: str
    quantity: Optional[Decimal]
    unit_price: Optional[Decimal]
    total: Decimal


@dataclass(frozen=True)
class OCRSettings:
    language: str = "eng"
    pages: Optional[Sequence[int]] = None
    psm: int = 6
    oem: int = 3
    dpi: int = 300
    timeout: int = 30
    min_confidence: float = 60.0
    preprocess: bool = True
    deskew: bool = True
    denoise: bool = True
    threshold: bool = True
    contrast: float = 1.25
    scale: float = 1.5


Source = Union[str, Path, Image.Image, bytes]


class ReceiptProcessor:
    """Process an image/PDF receipt or parse already-extracted receipt text.

    The OCR step is lazy, so ``parse_text`` and ``parse(text=...)`` do not need
    Tesseract or PyMuPDF. Monetary values are returned as strings in serialized
    output to avoid floating-point rounding errors.
    """

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
    MONEY = r"(?:[$€£¥]\s*)?[-+]?(?:\d{1,3}(?:[ ,.']\d{3})+|\d+)(?:[.,]\d{2})"
    DATE_PATTERNS = (
        r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
        r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b",
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    )
    SUMMARY_LABELS = {
        "subtotal": ("subtotal", "sub total", "net amount"),
        "tax": ("tax", "vat", "gst", "hst", "sales tax"),
        "tip": ("tip", "gratuity"),
        "fees": ("fee", "fees", "service charge", "delivery charge"),
        "discount": ("discount", "coupon", "savings"),
        "total": ("grand total", "amount due", "balance due", "total"),
    }

    def __init__(
        self,
        source: Optional[Source] = None,
        *,
        language: str = "eng",
        pages: Optional[Sequence[int]] = None,
        psm: int = 6,
        oem: int = 3,
        dpi: int = 300,
        timeout: int = 30,
        min_confidence: float = 60.0,
        preprocess: bool = True,
        deskew: bool = True,
        denoise: bool = True,
        threshold: bool = True,
        contrast: float = 1.25,
        scale: float = 1.5,
        currency: Optional[str] = None,
    ) -> None:
        if not 0 <= min_confidence <= 100:
            raise ValueError("min_confidence must be between 0 and 100")
        if scale <= 0 or contrast <= 0:
            raise ValueError("scale and contrast must be greater than zero")
        if pages and any(page < 1 for page in pages):
            raise ValueError("pages are 1-indexed positive integers")
        self.source = source
        self.currency_override = currency.upper() if currency else None
        self.settings = OCRSettings(
            language, pages, psm, oem, dpi, timeout, min_confidence,
            preprocess, deskew, denoise, threshold, contrast, scale,
        )
        self._last_result: Optional[dict[str, Any]] = None

    def parse(self, *, text: Optional[str] = None) -> dict[str, Any]:
        """Return structured receipt data from supplied text or OCR input."""
        if text is None:
            if self.source is None:
                raise ValueError("source or text is required")
            text, confidence, page_count = self.extract_text()
        else:
            confidence, page_count = None, None
        result = self.parse_text(text, currency=self.currency_override)
        result["ocr"] = {
            "confidence": confidence,
            "language": self.settings.language,
            "pages": page_count,
        }
        self._last_result = result
        return result

    @classmethod
    def parse_text(cls, text: str, *, currency: Optional[str] = None) -> dict[str, Any]:
        """Parse raw OCR text into a stable, JSON-ready receipt schema."""
        cleaned = cls._clean_text(text)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        summary = {key: cls._find_labeled_amount(lines, labels) for key, labels in cls.SUMMARY_LABELS.items()}
        total = summary["total"] or cls._last_amount(lines)
        vendor = cls._vendor(lines)
        date = cls._date(cleaned)
        transaction_time = cls._time(cleaned)
        inferred_currency = currency.upper() if currency else cls._currency(cleaned)
        items = cls._items(lines)

        warnings: list[str] = []
        if not vendor:
            warnings.append("vendor_not_found")
        if total is None:
            warnings.append("total_not_found")
        if summary["subtotal"] is not None and summary["tax"] is not None and total is not None:
            expected = (
                summary["subtotal"] + summary["tax"] + (summary["tip"] or Decimal("0"))
                + (summary["fees"] or Decimal("0")) - (summary["discount"] or Decimal("0"))
            )
            if abs(expected - total) > Decimal("0.02"):
                warnings.append("totals_do_not_reconcile")

        result = {
            "vendor": vendor,
            "date": date,
            "time": transaction_time,
            "currency": inferred_currency,
            "items": [cls._serialize_item(item) for item in items],
            "subtotal": cls._decimal_string(summary["subtotal"]),
            "tax": cls._decimal_string(summary["tax"]),
            "tip": cls._decimal_string(summary["tip"]),
            "fees": cls._decimal_string(summary["fees"]),
            "discount": cls._decimal_string(summary["discount"]),
            "total": cls._decimal_string(total),
            "payment_method": cls._payment_method(cleaned),
            "card_last4": cls._card_last4(cleaned),
            "receipt_number": cls._receipt_number(cleaned),
            "location": "",
            "document_kind": cls._document_kind(cleaned),
            "warnings": warnings,
            "raw_text": cleaned,
        }
        return result

    def extract_text(self) -> tuple[str, Optional[float], int]:
        """OCR the configured source and return text, mean confidence, pages."""
        try:
            import pytesseract
        except ImportError:
            pytesseract = None
        if shutil.which("tesseract") is None:
            raise ReceiptProcessingError("Tesseract OCR executable was not found on PATH")

        images = self._load_images(self.source)
        selected = self._select_pages(images)
        texts: list[str] = []
        confidences: list[float] = []
        config = f"--psm {self.settings.psm} --oem {self.settings.oem}"
        for image in selected:
            prepared = self._preprocess(image) if self.settings.preprocess else ImageOps.exif_transpose(image).convert("RGB")
            try:
                if pytesseract is not None:
                    data = pytesseract.image_to_data(
                        prepared, lang=self.settings.language, config=config,
                        output_type=pytesseract.Output.DICT, timeout=self.settings.timeout,
                    )
                    page_text = pytesseract.image_to_string(
                        prepared, lang=self.settings.language, config=config,
                        timeout=self.settings.timeout,
                    )
                else:
                    page_text, data = self._ocr_with_command(prepared)
                for word, conf in zip(data["text"], data["conf"]):
                    word = word.strip()
                    try:
                        score = float(conf)
                    except (TypeError, ValueError):
                        continue
                    if word and score >= self.settings.min_confidence:
                        confidences.append(score)
                texts.append(page_text)
            except (RuntimeError, subprocess.SubprocessError) as exc:
                raise ReceiptProcessingError(f"OCR failed: {exc}") from exc
        mean = round(sum(confidences) / len(confidences), 1) if confidences else None
        return "\n\n".join(texts), mean, len(selected)

    def _ocr_with_command(self, image: Image.Image) -> tuple[str, dict[str, list[str]]]:
        """Use the Tesseract executable when pytesseract is unavailable."""
        common = [
            "tesseract", "stdin", "stdout", "-l", self.settings.language,
            "--psm", str(self.settings.psm), "--oem", str(self.settings.oem),
        ]
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = buffer.getvalue()
        text_run = subprocess.run(
            common, input=payload, capture_output=True, timeout=self.settings.timeout,
        )
        if text_run.returncode:
            raise ReceiptProcessingError(text_run.stderr.decode("utf-8", errors="replace").strip())
        tsv_run = subprocess.run(
            common + ["tsv"], input=payload, capture_output=True,
            timeout=self.settings.timeout,
        )
        if tsv_run.returncode:
            raise ReceiptProcessingError(tsv_run.stderr.decode("utf-8", errors="replace").strip())
        rows = list(csv.DictReader(io.StringIO(tsv_run.stdout.decode("utf-8", errors="replace")), delimiter="\t"))
        data = {
            "text": [row.get("text", "") for row in rows],
            "conf": [row.get("conf", "-1") for row in rows],
        }
        return text_run.stdout.decode("utf-8", errors="replace"), data

    def export_json(self, path: Union[str, Path], result: Optional[dict[str, Any]] = None) -> Path:
        result = result or self._last_result or self.parse()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    def export_csv(self, path: Union[str, Path], result: Optional[dict[str, Any]] = None) -> Path:
        result = result or self._last_result or self.parse()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = ["vendor", "date", "currency", "description", "quantity", "unit_price", "item_total", "subtotal", "tax", "tip", "discount", "total"]
        rows = result["items"] or [{}]
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in rows:
                writer.writerow({
                    "vendor": result["vendor"], "date": result["date"], "currency": result["currency"],
                    "description": item.get("description"), "quantity": item.get("quantity"),
                    "unit_price": item.get("unit_price"), "item_total": item.get("total"),
                    "subtotal": result["subtotal"], "tax": result["tax"], "tip": result["tip"],
                    "discount": result["discount"], "total": result["total"],
                })
        return target

    def _load_images(self, source: Optional[Source]) -> list[Image.Image]:
        if isinstance(source, Image.Image):
            return [source.copy()]
        if isinstance(source, bytes):
            return [Image.open(io.BytesIO(source)).convert("RGB")]
        if not isinstance(source, (str, Path)):
            raise ReceiptProcessingError("Unsupported receipt source")
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".pdf":
            try:
                import fitz
            except ImportError as exc:
                raise ReceiptProcessingError("Install PyMuPDF to process PDF receipts") from exc
            images: list[Image.Image] = []
            with fitz.open(path) as document:
                matrix = fitz.Matrix(self.settings.dpi / 72, self.settings.dpi / 72)
                for page in document:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            return images
        if path.suffix.lower() not in self.IMAGE_SUFFIXES:
            raise ReceiptProcessingError(f"Unsupported file extension: {path.suffix}")
        with Image.open(path) as image:
            return [ImageOps.exif_transpose(image).convert("RGB")]

    def _select_pages(self, images: list[Image.Image]) -> list[Image.Image]:
        if not self.settings.pages:
            return images
        invalid = [page for page in self.settings.pages if page > len(images)]
        if invalid:
            raise ReceiptProcessingError(f"Page(s) out of range: {invalid}; document has {len(images)} page(s)")
        return [images[page - 1] for page in self.settings.pages]

    def _preprocess(self, image: Image.Image) -> Image.Image:
        image = ImageOps.exif_transpose(image).convert("L")
        if self.settings.scale != 1:
            image = image.resize(
                (round(image.width * self.settings.scale), round(image.height * self.settings.scale)),
                Image.Resampling.LANCZOS,
            )
        if self.settings.denoise:
            image = image.filter(ImageFilter.MedianFilter(size=3))
        if self.settings.contrast != 1:
            image = ImageEnhance.Contrast(image).enhance(self.settings.contrast)
        if self.settings.threshold:
            # Autocontrast plus a conservative fixed threshold works without OpenCV.
            image = ImageOps.autocontrast(image).point(lambda px: 255 if px > 165 else 0)
        if self.settings.deskew:
            try:
                import cv2
                import numpy as np
                array = np.array(image)
                coords = np.column_stack(np.where(array < 250))
                if len(coords) > 20:
                    angle = cv2.minAreaRect(coords[:, ::-1].astype("float32"))[-1]
                    angle = -(90 + angle) if angle < -45 else -angle
                    if abs(angle) <= 15:
                        center = (array.shape[1] / 2, array.shape[0] / 2)
                        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
                        array = cv2.warpAffine(array, matrix, (array.shape[1], array.shape[0]), borderValue=255)
                        image = Image.fromarray(array)
            except ImportError:
                pass
        return image

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")).strip()

    @classmethod
    def _vendor(cls, lines: Sequence[str]) -> Optional[str]:
        skip = re.compile(r"(?i)^(receipt|invoice|tax invoice|date|time|tel|phone|www\.|https?://|#\d+)")
        for line in lines[:8]:
            if not skip.search(line) and not re.fullmatch(cls.MONEY, line.strip()) and len(re.findall(r"[A-Za-z]", line)) >= 2:
                return line[:160]
        return None

    @classmethod
    def _date(cls, text: str) -> Optional[str]:
        for pattern in cls.DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(0)
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
                    try:
                        return datetime.strptime(raw, fmt).date().isoformat()
                    except ValueError:
                        continue
                return raw
        return None

    @staticmethod
    def _time(text: str) -> Optional[str]:
        match = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[AP]M)?\b", text, re.IGNORECASE)
        return match.group(0).upper().replace(" ", "") if match else None

    @staticmethod
    def _card_last4(text: str) -> Optional[str]:
        match = re.search(r"(?i)\b(?:visa|mastercard|master card|amex|discover|card)?\s*(?:x{2,}|\*{2,}|ending\s+in)\s*[- ]?(\d{4})\b", text)
        return match.group(1) if match else None

    @staticmethod
    def _payment_method(text: str) -> Optional[str]:
        for label in ("Visa", "Mastercard", "Amex", "Discover", "Apple Pay", "Google Pay", "Cash"):
            if re.search(rf"(?i)\b{re.escape(label)}\b", text):
                return label
        return None

    @staticmethod
    def _receipt_number(text: str) -> Optional[str]:
        match = re.search(r"(?im)\b(?:receipt|invoice|order|transaction)\s*(?:no\.?|number|#|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,30})\b", text)
        return match.group(1) if match else None

    @staticmethod
    def _document_kind(text: str) -> str:
        if re.search(r"(?i)\b(refund|refunded)\b", text):
            return "refund"
        if re.search(r"(?i)\b(credit memo|credit note)\b", text):
            return "credit"
        if re.search(r"(?i)\bstatement\b", text):
            return "statement"
        if re.search(r"(?i)\binvoice\b", text):
            return "invoice"
        return "receipt"

    @classmethod
    def _find_labeled_amount(cls, lines: Sequence[str], labels: Iterable[str]) -> Optional[Decimal]:
        candidates: list[Decimal] = []
        label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
        for line in lines:
            if re.search(rf"(?i)\b(?:{label_pattern})\b", line):
                amounts = re.findall(cls.MONEY, line)
                if amounts:
                    value = cls._amount(amounts[-1])
                    if value is not None:
                        candidates.append(value)
        return candidates[-1] if candidates else None

    @classmethod
    def _last_amount(cls, lines: Sequence[str]) -> Optional[Decimal]:
        for line in reversed(lines):
            amounts = re.findall(cls.MONEY, line)
            if amounts:
                value = cls._amount(amounts[-1])
                if value is not None:
                    return value
        return None

    @classmethod
    def _items(cls, lines: Sequence[str]) -> list[ReceiptItem]:
        items: list[ReceiptItem] = []
        summary_words = tuple(word for values in cls.SUMMARY_LABELS.values() for word in values)
        amount_at_end = re.compile(rf"^(?P<body>.+?)\s+(?P<amount>{cls.MONEY})\s*$", re.IGNORECASE)
        qty_pattern = re.compile(r"^(?P<qty>\d+(?:[.,]\d+)?)\s*[xX@]\s*(?P<unit>\d+(?:[.,]\d{2}))\s+(.+)$")
        for line in lines:
            lower = line.lower()
            if any(re.search(rf"\b{re.escape(word)}\b", lower) for word in summary_words):
                continue
            match = amount_at_end.match(line)
            if not match:
                continue
            total = cls._amount(match.group("amount"))
            body = match.group("body").strip(" .:-")
            if total is None or len(re.findall(r"[A-Za-z]", body)) < 2 or re.search(r"(?i)\b(date|time|change|cash|card|auth|invoice|receipt)\b", body):
                continue
            quantity = unit_price = None
            qty_match = qty_pattern.match(body)
            if qty_match:
                quantity = cls._amount(qty_match.group("qty"), require_cents=False)
                unit_price = cls._amount(qty_match.group("unit"))
                body = qty_match.group(3).strip()
            items.append(ReceiptItem(body[:240], quantity, unit_price, total))
        return items

    @staticmethod
    def _amount(raw: str, *, require_cents: bool = True) -> Optional[Decimal]:
        value = re.sub(r"[^0-9,.'+-]", "", raw).replace("'", "")
        if not value:
            return None
        if "," in value and "." in value:
            decimal_mark = "," if value.rfind(",") > value.rfind(".") else "."
            thousands_mark = "." if decimal_mark == "," else ","
            value = value.replace(thousands_mark, "").replace(decimal_mark, ".")
        elif "," in value:
            tail = value.rsplit(",", 1)[-1]
            value = value.replace(",", "." if len(tail) == 2 else "")
        else:
            value = value.replace(",", "")
        value = value.replace(" ", "")
        try:
            result = Decimal(value)
            if require_cents and result.as_tuple().exponent > -2:
                return result.quantize(Decimal("0.01"))
            return result
        except InvalidOperation:
            return None

    @staticmethod
    def _currency(text: str) -> Optional[str]:
        upper = text.upper()
        for marker, code in (("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("USD", "USD"), ("CAD", "CAD"), ("AUD", "AUD"), ("EUR", "EUR"), ("GBP", "GBP")):
            if marker in upper:
                return code
        return "USD" if "$" in text else None

    @staticmethod
    def _decimal_string(value: Optional[Decimal]) -> Optional[str]:
        return format(value, "f") if value is not None else None

    @classmethod
    def _serialize_item(cls, item: ReceiptItem) -> dict[str, Optional[str]]:
        data = asdict(item)
        return {key: cls._decimal_string(value) if isinstance(value, Decimal) else value for key, value in data.items()}
