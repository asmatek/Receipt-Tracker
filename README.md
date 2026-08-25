# Receipt Tracker 4.0 — Streamlit + Supabase

A secure multi-user business-expense tracker. Streamlit provides the interface and OCR workflow; Supabase permanently stores expense records, user access, audit history, and original receipt files.

## Version 4 features

- Multi-file receipt inbox with progress, per-receipt review, and save status
- Automatic merchant extraction and reusable merchant records
- Editable merchant names and a safe merge tool for duplicates
- Duplicate checks using file hashes, exact fields, and fuzzy OCR-name matching
- Polished responsive cards, status pills, and simplified navigation
- Receipt, invoice, credit, refund, statement, email, and non-receipt classification
- EML and text-document ingestion in addition to images and PDFs
- Explainable duplicate scoring using file hashes, OCR-text fingerprints, receipt numbers, visual hashes, and transaction fields
- Approval, needs-review, and rejection workflow with reimbursement-safe totals
- Draft, submitted, paid, and cancelled reimbursement batches
- PDF, CSV, and ZIP reimbursement packages with original receipt attachments
- Refund-safe negative accounting, tax totals, richer metadata, line-item editing, tags, attendees, mileage, departments, and personal/reimbursable controls
- Merchant rules for automatic category, project, and tag assignment
- Full ZIP backup/restore and a recoverable recycle bin
- Email/password login restricted to administrator-approved emails
- Permanent Supabase database and private receipt-file storage
- Editable businesses and expense categories
- Receipt OCR for images and scanned PDFs
- Merchant, date, subtotal, tax, tip, discount, total, currency, and line items
- Business purpose, client, project, payment method, and notes
- Duplicate detection using receipt hashes and merchant/date/total matching
- Search, filters, receipt detail and download, editing, and batch deletion
- Monthly and annual dashboard totals
- Monthly, quarterly, and yearly reports by category, business, project, or client
- CSV exports and complete audit history
- Admin team allow-list with access disable/restore controls

## 1. Create Supabase

1. Create a project at [supabase.com](https://supabase.com/).
2. Open **SQL Editor**, create a query, paste all of `supabase_setup.sql`, and click **Run**. Existing installations can safely run the file again to add the merchant upgrade.
3. In **Authentication → URL Configuration**, set **Site URL** to your Streamlit app URL.
4. In **Project Settings → API Keys**, copy the project URL, anon key, and service-role key.

The service-role key is highly sensitive. Never put it in GitHub or share it with app users.

## 2. Configure Streamlit Secrets

Open **Streamlit → Manage app → Settings → Secrets** and add:

```toml
[supabase]
url = "https://YOUR-PROJECT.supabase.co"
anon_key = "YOUR-ANON-KEY"
service_role_key = "YOUR-SERVICE-ROLE-KEY"

[app]
admin_emails = ["your-email@example.com"]
```

Replace the admin email with the email you will use to sign in. Save the secrets and reboot the app.

## 3. Create the first administrator account

1. Open the Streamlit app.
2. Choose **Create invited account**.
3. Enter the exact email listed in `admin_emails` and create a password.
4. Confirm the email if Supabase sends a confirmation message.
5. Return to the app and sign in.

Once signed in, create at least one business, then upload receipts. Use the **Team** page to authorize additional emails. Invited people open the same app, select **Create invited account**, and use the exact email you authorized.

## Accounting workflow

1. Upload several documents in **Receipt inbox**.
2. Confirm critical OCR fields and save each record.
3. Resolve warnings in **Duplicate review** and approve eligible expenses in **Expenses**.
4. Create a batch in **Reimbursements**. Only approved, business, reimbursable, non-duplicate expenses are eligible.
5. Submit or mark the batch paid, then download its PDF, CSV, or complete ZIP package.

## Deploy on Streamlit Community Cloud

1. Extract this project and upload all files to a GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in with GitHub.
3. Click **Create app**, then **Yup, I have an app**.
4. Select the GitHub repository and branch.
5. Set **Main file path** to `streamlit_app.py`.
6. In **Advanced settings**, select Python 3.12.
7. Click **Deploy**.

Community Cloud reads `requirements.txt` for Python packages and `packages.txt` for the Tesseract system packages. Supabase credentials must be added through Streamlit Secrets.

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
