"""Command-line interface for receipt_processor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import ReceiptProcessor, ReceiptProcessingError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCR and parse receipt images or PDFs")
    parser.add_argument("input", type=Path, help="Receipt image, PDF, or UTF-8 text file")
    parser.add_argument("-o", "--output", type=Path, help="Output file; stdout when omitted")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--lang", default="eng", help="Tesseract language, e.g. eng or eng+fra")
    parser.add_argument("--pages", help="Comma-separated, 1-indexed PDF pages")
    parser.add_argument("--currency", help="Force ISO currency code, e.g. USD")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract page segmentation mode")
    parser.add_argument("--oem", type=int, default=3, help="Tesseract OCR engine mode")
    parser.add_argument("--dpi", type=int, default=300, help="PDF rendering DPI")
    parser.add_argument("--timeout", type=int, default=30, help="OCR timeout per page in seconds")
    parser.add_argument("--min-confidence", type=float, default=60.0)
    parser.add_argument("--no-preprocess", action="store_true")
    parser.add_argument("--no-deskew", action="store_true")
    parser.add_argument("--no-denoise", action="store_true")
    parser.add_argument("--no-threshold", action="store_true")
    parser.add_argument("--contrast", type=float, default=1.25)
    parser.add_argument("--scale", type=float, default=1.5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pages = [int(value) for value in args.pages.split(",")] if args.pages else None
    kwargs = dict(
        language=args.lang, pages=pages, psm=args.psm, oem=args.oem,
        dpi=args.dpi, timeout=args.timeout, min_confidence=args.min_confidence,
        preprocess=not args.no_preprocess, deskew=not args.no_deskew,
        denoise=not args.no_denoise, threshold=not args.no_threshold,
        contrast=args.contrast, scale=args.scale, currency=args.currency,
    )
    try:
        if args.input.suffix.lower() == ".txt":
            processor = ReceiptProcessor(**kwargs)
            result = processor.parse(text=args.input.read_text(encoding="utf-8"))
        else:
            processor = ReceiptProcessor(args.input, **kwargs)
            result = processor.parse()
        if args.output:
            if args.format == "json":
                processor.export_json(args.output, result)
            else:
                processor.export_csv(args.output, result)
        elif args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            raise ReceiptProcessingError("CSV output requires --output")
    except (OSError, ValueError, ReceiptProcessingError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
