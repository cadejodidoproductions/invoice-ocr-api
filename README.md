# Invoice OCR API

Extracts vendor, invoice number, date, and total from PDF invoices.

## Live Demo
https://github.com/cadejodidoproductions/invoice-ocr-api

## Pricing
$99/month for unlimited invoice processing

## Features
- Extracts vendor name
- Finds invoice number
- Detects date (multiple formats supported)
- Calculates total amount
- Returns all found amounts
- Response caching for repeated files
- Rate limiting (100 requests/minute)
- Optional API key authentication
- Request tracing with unique IDs
- Metrics and monitoring endpoint

## Deploy on Render.com

1. Fork/Clone this repo
2. Connect to Render.com
3. Use these settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn working_pdf_extractor:app --host 0.0.0.0 --port $PORT`

### Environment Variables (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `INVOICE_OCR_API_KEY` | None | API key for authentication (disabled if not set) |
| `RATE_LIMIT_REQUESTS` | 100 | Max requests per rate limit window |
| `RATE_LIMIT_WINDOW` | 60 | Rate limit window in seconds |
| `MAX_FILE_SIZE` | 10485760 | Max file size in bytes (10MB) |
| `CACHE_TTL` | 300 | Cache time-to-live in seconds |
| `CACHE_MAX_SIZE` | 100 | Maximum cached responses |
| `CORS_ORIGINS` | * | Comma-separated allowed origins |
| `LOG_LEVEL` | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `WORKERS` | 4 | Number of Uvicorn workers (when running directly) |

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (single worker with hot reload)
uvicorn working_pdf_extractor:app --reload --port 8001

# Run with multiple workers (production-like)
python working_pdf_extractor.py
```

## API Endpoints

### POST /invoice-to-json
Process a PDF invoice and extract structured data.

```bash
# Without authentication
curl -X POST -F "file=@invoice.pdf" https://your-app.onrender.com/invoice-to-json

# With authentication (if API key is configured)
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -F "file=@invoice.pdf" \
  https://your-app.onrender.com/invoice-to-json
```

**Response:**
```json
{
  "vendor": "ACME Corporation",
  "invoice_no": "INV-2024-001",
  "date": "Jan 15, 2024",
  "total": 1250.00,
  "all_amounts": [1250.00, 500.00, 500.00, 250.00],
  "file_type": "PDF",
  "pages_processed": 1
}
```

**Response Headers:**
- `X-Request-ID`: Unique request identifier for tracing
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

### GET /
Health check endpoint.

```bash
curl https://your-app.onrender.com/
```

### GET /health
Detailed health check with configuration info.

```bash
curl https://your-app.onrender.com/health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "Invoice OCR API",
  "version": "2.0.0",
  "features": {
    "authentication": false,
    "rate_limiting": true,
    "caching": true
  },
  "config": {
    "max_file_size_mb": 10,
    "rate_limit": "100 requests per 60s",
    "cache_ttl_seconds": 300
  }
}
```

### GET /metrics
API metrics and statistics (requires authentication if API key is configured).

```bash
curl -H "X-API-Key: your-api-key" https://your-app.onrender.com/metrics
```

**Response:**
```json
{
  "metrics": {
    "uptime_seconds": 3600.0,
    "total_requests": 150,
    "successful_requests": 145,
    "failed_requests": 5,
    "cache_hits": 30,
    "cache_misses": 120,
    "cache_hit_rate": 20.0,
    "avg_processing_time_ms": 85.5,
    "rate_limited_requests": 2,
    "auth_failures": 1
  },
  "cache": {
    "total_entries": 50,
    "valid_entries": 48,
    "max_size": 100,
    "ttl_seconds": 300
  },
  "rate_limiter": {
    "max_requests": 100,
    "window_seconds": 60,
    "active_clients": 5
  }
}
```

## Error Responses

| Status Code | Description |
|-------------|-------------|
| 400 | Invalid file type (not PDF) |
| 401 | Invalid or missing API key |
| 413 | File too large (>10MB) |
| 422 | Could not extract text from PDF |
| 429 | Rate limit exceeded |
| 500 | Internal server error |

## Target Customers
- Accounting firms
- Bookkeepers
- E-commerce businesses
- Anyone processing invoices manually

## Business Model
- $99/month subscription
- Unlimited invoice processing
- API access
- 99.9% uptime guarantee
