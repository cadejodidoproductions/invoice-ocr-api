# Invoice OCR API

Extracts vendor, invoice number, date, and total from PDF invoices with confidence scores and multi-currency support.

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
- **Multi-currency support** (USD, EUR, GBP)
- **Confidence scores** for all extracted fields
- **Batch processing** (up to 10 files per request)
- Response caching for repeated files
- Rate limiting (100 requests/minute)
- Optional API key authentication
- Request tracing with unique IDs
- Metrics and monitoring endpoint
- **Request timeout protection**
- **PDF magic byte validation**
- **Graceful shutdown handling**

## Quick Start

### Using Docker

```bash
# Build and run
docker build -t invoice-ocr-api .
docker run -p 8000:8000 invoice-ocr-api

# Or use docker-compose
docker-compose up
```

### Using Python

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (development)
uvicorn working_pdf_extractor:app --reload --port 8000

# Run with multiple workers (production)
python working_pdf_extractor.py
```

## Deploy on Render.com

1. Fork/Clone this repo
2. Connect to Render.com
3. Use these settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn working_pdf_extractor:app --host 0.0.0.0 --port $PORT`

## Environment Variables

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
| `WORKERS` | 4 | Number of Uvicorn workers |
| `REQUEST_TIMEOUT` | 30 | Request timeout in seconds |
| `MAX_BATCH_SIZE` | 10 | Maximum files per batch request |

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
  "date": "January 15, 2024",
  "total": 1250.00,
  "currency": "USD",
  "all_amounts": [1250.00, 500.00, 500.00, 250.00],
  "file_type": "PDF",
  "pages_processed": 1,
  "filename": "invoice.pdf",
  "confidence": {
    "overall": 0.85,
    "vendor": 0.85,
    "invoice_no": 0.80,
    "date": 0.95,
    "amounts": 0.90
  }
}
```

### POST /invoice-to-json/batch
Process multiple PDF invoices in a single request.

```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf" \
  -F "files=@invoice3.pdf" \
  https://your-app.onrender.com/invoice-to-json/batch
```

**Response:**
```json
{
  "total": 3,
  "successful": 3,
  "failed": 0,
  "processing_time_ms": 450.5,
  "results": [
    {
      "filename": "invoice1.pdf",
      "success": true,
      "cached": false,
      "data": { ... }
    },
    ...
  ]
}
```

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
  "version": "2.1.0",
  "features": {
    "authentication": false,
    "rate_limiting": true,
    "caching": true,
    "batch_processing": true,
    "multi_currency": true,
    "confidence_scores": true
  },
  "config": {
    "max_file_size_mb": 10,
    "rate_limit": "100 requests per 60s",
    "cache_ttl_seconds": 300,
    "request_timeout_seconds": 30,
    "max_batch_size": 10
  }
}
```

### GET /metrics
API metrics and statistics (requires authentication if API key is configured).

```bash
curl -H "X-API-Key: your-api-key" https://your-app.onrender.com/metrics
```

## Response Headers

All responses include:
- `X-Request-ID`: Unique request identifier for tracing
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

## Error Responses

| Status Code | Description |
|-------------|-------------|
| 400 | Invalid file type or invalid PDF |
| 401 | Invalid or missing API key |
| 408 | Request timeout |
| 413 | File too large (>10MB) |
| 422 | Could not extract text from PDF |
| 429 | Rate limit exceeded |
| 500 | Internal server error |

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run tests with coverage
pytest --cov=working_pdf_extractor --cov-report=html
```

### Linting

```bash
# Check code style
ruff check .

# Format code
ruff format .
```

### Security Scanning

```bash
# Run security linter
bandit -r working_pdf_extractor.py

# Check dependencies for vulnerabilities
safety check
```

## Docker

### Build Image

```bash
docker build -t invoice-ocr-api .
```

### Run Container

```bash
docker run -p 8000:8000 \
  -e INVOICE_OCR_API_KEY=your-secret-key \
  invoice-ocr-api
```

### Docker Compose

```bash
# Production
docker-compose up

# Development (with hot reload)
docker-compose --profile dev up invoice-ocr-api-dev
```

## CI/CD

The project includes a GitHub Actions workflow that:
1. Runs linting (Ruff)
2. Runs tests with coverage
3. Performs security scanning (Bandit, Safety)
4. Builds Docker image
5. Deploys to Render.com (on main branch push)

## Supported Formats

### Date Formats
- Month name: `January 15, 2024`, `Jan 15, 2024`
- ISO: `2024-01-15`
- US: `01/15/2024`, `01-15-2024`
- European: `15.01.2024`

### Currencies
- USD ($)
- EUR (€)
- GBP (£)

### Invoice Number Patterns
- `Invoice #12345`
- `INV-2024-001`
- `Bill #98765`
- `Order #ORD-123`

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
