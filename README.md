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

# Response
{
  "vendor": "Wix.com LTD",
  "invoice_no": "1044495965",
  "date": "Mar 8, 2023",
  "total": 828.0,
  "all_amounts": [828.0, 414.0, 414.0, 414.0],
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