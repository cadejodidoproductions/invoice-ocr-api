# Invoice OCR API

Extracts vendor, invoice number, date, and total from PDF invoices.

## 🚀 Live Demo
https://github.com/cadejodidoproductions/invoice-ocr-api

## 💰 Pricing
$99/month for unlimited invoice processing

## 🛠️ Features
- Extracts vendor name
- Finds invoice number
- Detects date
- Calculates total amount
- Returns all found amounts

## 📦 Deploy on Render.com

1. Fork/Clone this repo
2. Connect to Render.com
3. Use these settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn working_pdf_extractor:app --host 0.0.0.0 --port $PORT`

## 🔧 Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn working_pdf_extractor:app --reload --port 8001
```

## 📝 API Usage

```bash
# Process a PDF invoice
curl -X POST -F "file=@invoice.pdf" https://your-app.onrender.com/invoice-to-json

# Example Response
{
  "vendor": "ACME Corporation",
  "invoice_no": "INV-2024-001",
  "date": "Jan 15, 2024",
  "total": 1250.00,
  "all_amounts": [1250.00, 500.00, 500.00, 250.00],
  "file_type": "PDF"
}
```

## 🎯 Target Customers
- Accounting firms
- Bookkeepers
- E-commerce businesses
- Anyone processing invoices manually

## 📈 Business Model
- $99/month subscription
- Unlimited invoice processing
- API access
- 99.9% uptime guarantee