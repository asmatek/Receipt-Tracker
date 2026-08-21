# Custom Receipt Processor — Streamlit Edition

A Streamlit application and Python package derived from the supplied OCR Document Processor `SKILL.md`. It accepts receipt images and scanned PDFs, performs optional preprocessing and Tesseract OCR, and produces structured JSON or CSV.

## Deploy on Streamlit Community Cloud

1. Extract this project and upload all files to a GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in with GitHub.
3. Click **Create app**, then **Yup, I have an app**.
4. Select the GitHub repository and branch.
5. Set **Main file path** to `streamlit_app.py`.
6. In **Advanced settings**, select Python 3.12.
7. Click **Deploy**.

Community Cloud reads `requirements.txt` for Python packages and `packages.txt` for the Tesseract system packages. No API key is required.

Repository layout:

```text
streamlit_app.py          # Streamlit entrypoint
requirements.txt          # Python dependencies
packages.txt              # Tesseract system dependencies
.streamlit/config.toml    # Upload limit and theme
src/receipt_processor/    # Receipt OCR and parser
tests/                    # Parser tests
```

## Run locally

Install Tesseract with your operating system's package manager, then run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

On Debian/Ubuntu, install the same external dependencies with:

```bash
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-fra tesseract-ocr-deu
```

## Returned fields

`vendor`, `date` (ISO where unambiguous), `currency`, `items`, `subtotal`, `tax`, `tip`, `discount`, `total`, `warnings`, `raw_text`, and OCR metadata.

Amounts are serialized as decimal strings so financial values are not rounded through binary floating point.

## Install

Python 3.9+ and the Tesseract system executable are required for OCR. The tool uses `pytesseract` when installed and falls back to the Tesseract command directly. Text-only parsing does not require Tesseract.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
```

Install additional Tesseract language data when using `--lang` values other than `eng`.

## Command line

```bash
# Image to JSON
receipt-process receipt.jpg -o receipt.json

# Selected PDF pages, French + English OCR, CSV output
receipt-process receipt.pdf --pages 1,2 --lang eng+fra --format csv -o items.csv

# Parse an already-OCR'd text file without running OCR
receipt-process receipt.txt
```

Important parameters:

| Parameter | Default | Purpose |
|---|---:|---|
| `--lang` | `eng` | Tesseract language(s) |
| `--pages` | all | 1-indexed PDF pages, comma-separated |
| `--format` | `json` | JSON or CSV |
| `--currency` | inferred | Force an ISO currency code |
| `--min-confidence` | `60` | Word-confidence floor used in OCR scoring |
| `--psm` / `--oem` | `6` / `3` | Tesseract modes |
| `--dpi` | `300` | PDF render resolution |
| `--timeout` | `30` | OCR timeout per page |
| `--no-preprocess` | off | Disable image preparation |
| `--no-deskew`, `--no-denoise`, `--no-threshold` | off | Disable individual preprocessing stages |
| `--contrast`, `--scale` | `1.25`, `1.5` | Image enhancement factors |

## Python API

```python
from receipt_processor import ReceiptProcessor

processor = ReceiptProcessor(
    "receipt.jpg",
    language="eng",
    currency="USD",
    min_confidence=60,
    preprocess=True,
)
receipt = processor.parse()
processor.export_json("receipt.json", receipt)
```

For deterministic integration tests or an upstream OCR service:

```python
receipt = ReceiptProcessor.parse_text("Store\nCoffee 3.50\nTax 0.29\nTotal 3.79")
```

## Notes

- Receipt layouts vary. Review `warnings`, especially `totals_do_not_reconcile`, before posting transactions automatically.
- Date normalization is conservative. Ambiguous numeric dates are interpreted month/day/year, matching the default US-oriented configuration.
- OpenCV is optional. If it is absent, the other preprocessing stages still run and deskew is skipped.

## Test

```bash
python -m pytest
```
