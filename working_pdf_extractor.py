"""
Invoice OCR API - PDF Data Extraction Service
Performance-optimized with authentication, rate limiting, and caching.
"""

import hashlib
import io
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict
from typing import Optional

import pypdf
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# =============================================================================
# CONFIGURATION
# =============================================================================

# API Key for authentication (set via environment variable)
API_KEY = os.getenv("INVOICE_OCR_API_KEY", None)

# Rate limiting configuration
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))  # requests per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # window in seconds

# File size limit (10MB)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))

# Cache configuration
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 minutes
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "100"))

# CORS configuration
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Custom adapter to include request_id in logs
class RequestIdAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        request_id = self.extra.get("request_id", "N/A")
        kwargs["extra"] = {"request_id": request_id}
        return msg, kwargs

logger = logging.getLogger("invoice_ocr")

# =============================================================================
# PRE-COMPILED REGEX PATTERNS (Performance optimization)
# =============================================================================

# Invoice number patterns - compiled once at module load
INVOICE_PATTERN = re.compile(
    r'(?:Invoice|Inv|INV|Bill|Receipt)\s*[#:.\-]?\s*([A-Z0-9\-]+)',
    re.IGNORECASE
)

# Date patterns - multiple formats supported
DATE_PATTERNS = [
    # Month name formats: Jan 15, 2024 or January 15, 2024
    re.compile(
        r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2},?\s+\d{4})',
        re.IGNORECASE
    ),
    # Numeric formats: 01/15/2024, 01-15-2024, 2024-01-15
    re.compile(r'(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})'),
    re.compile(r'(\d{4}[/\-]\d{1,2}[/\-]\d{1,2})'),
]

# Dollar amount pattern
DOLLAR_PATTERN = re.compile(r'\$\s*([\d,]+(?:\.\d{2})?)')

# =============================================================================
# IN-MEMORY RATE LIMITER
# =============================================================================

class RateLimiter:
    """
    Thread-safe in-memory rate limiter using sliding window.

    Note: When running with multiple workers (processes), each worker maintains
    its own rate limit state. For distributed rate limiting, use Redis or similar.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # Clean up stale entries every 5 minutes

    def _cleanup_stale_entries(self, now: float) -> None:
        """Remove entries for clients with no recent requests."""
        if now - self._last_cleanup < self._cleanup_interval:
            return

        window_start = now - self.window_seconds
        stale_clients = [
            client_id for client_id, timestamps in self.requests.items()
            if not timestamps or max(timestamps) < window_start
        ]
        for client_id in stale_clients:
            del self.requests[client_id]
        self._last_cleanup = now

    def is_allowed(self, client_id: str) -> tuple[bool, dict]:
        """Check if request is allowed and return rate limit info."""
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            # Periodic cleanup of stale entries
            self._cleanup_stale_entries(now)

            # Clean old requests outside the window for this client
            self.requests[client_id] = [
                ts for ts in self.requests[client_id] if ts > window_start
            ]

            current_count = len(self.requests[client_id])
            remaining = max(0, self.max_requests - current_count)

            if current_count >= self.max_requests:
                # Calculate reset time
                oldest_in_window = min(self.requests[client_id]) if self.requests[client_id] else now
                reset_time = int(oldest_in_window + self.window_seconds)
                return False, {
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                }

            # Record this request
            self.requests[client_id].append(now)

            return True, {
                "X-RateLimit-Limit": str(self.max_requests),
                "X-RateLimit-Remaining": str(remaining - 1),
                "X-RateLimit-Reset": str(int(now + self.window_seconds)),
            }

rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)

# =============================================================================
# RESPONSE CACHE
# =============================================================================

class ResponseCache:
    """
    Thread-safe in-memory cache with TTL for API responses.

    Note: When running with multiple workers (processes), each worker maintains
    its own cache. For shared caching, use Redis or similar.
    """

    def __init__(self, max_size: int, ttl_seconds: int):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def _generate_key(self, content: bytes) -> str:
        """Generate cache key from file content hash."""
        return hashlib.sha256(content).hexdigest()

    def get(self, content: bytes) -> Optional[dict]:
        """Get cached response if exists and not expired."""
        key = self._generate_key(content)
        with self._lock:
            if key in self.cache:
                timestamp, data = self.cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    return data.copy()  # Return copy to prevent mutation
                else:
                    # Expired, remove it
                    del self.cache[key]
        return None

    def set(self, content: bytes, data: dict) -> None:
        """Cache a response."""
        key = self._generate_key(content)
        with self._lock:
            # Evict oldest entries if at capacity
            if len(self.cache) >= self.max_size:
                oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][0])
                del self.cache[oldest_key]

            self.cache[key] = (time.time(), data.copy())  # Store copy

    def stats(self) -> dict:
        """Return cache statistics."""
        now = time.time()
        with self._lock:
            valid_entries = sum(
                1 for ts, _ in self.cache.values()
                if now - ts < self.ttl_seconds
            )
            return {
                "total_entries": len(self.cache),
                "valid_entries": valid_entries,
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
            }

response_cache = ResponseCache(CACHE_MAX_SIZE, CACHE_TTL)

# =============================================================================
# METRICS COLLECTOR
# =============================================================================

class MetricsCollector:
    """
    Thread-safe metrics collector for monitoring.

    Note: When running with multiple workers (processes), each worker maintains
    its own metrics. For aggregated metrics, use Prometheus or similar.
    """

    def __init__(self):
        self.start_time = time.time()
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.total_processing_time = 0.0
        self.rate_limited_requests = 0
        self.auth_failures = 0
        self._lock = threading.Lock()

    def record_request(self, success: bool, processing_time: float, cache_hit: bool = False):
        with self._lock:
            self.total_requests += 1
            self.total_processing_time += processing_time
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1
            if cache_hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def record_rate_limit(self):
        with self._lock:
            self.rate_limited_requests += 1

    def record_auth_failure(self):
        with self._lock:
            self.auth_failures += 1

    def get_metrics(self) -> dict:
        with self._lock:
            uptime = time.time() - self.start_time
            avg_processing_time = (
                self.total_processing_time / self.total_requests
                if self.total_requests > 0 else 0
            )
            return {
                "uptime_seconds": round(uptime, 2),
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_hit_rate": round(
                    self.cache_hits / max(1, self.cache_hits + self.cache_misses) * 100, 2
                ),
                "avg_processing_time_ms": round(avg_processing_time * 1000, 2),
                "rate_limited_requests": self.rate_limited_requests,
                "auth_failures": self.auth_failures,
            }

metrics = MetricsCollector()

# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="Invoice OCR API",
    description="Extract structured data from PDF invoices",
    version="2.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)

# =============================================================================
# MIDDLEWARE & DEPENDENCIES
# =============================================================================

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add unique request ID to each request for tracing."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def get_client_id(request: Request) -> str:
    """Extract client identifier for rate limiting."""
    # Use API key if present, otherwise use IP
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key[:8]}"

    # Use forwarded IP if behind proxy, otherwise direct IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def get_logger(request: Request) -> RequestIdAdapter:
    """Get a logger with request ID context."""
    request_id = getattr(request.state, "request_id", "N/A")
    return RequestIdAdapter(logger, {"request_id": request_id})


async def verify_api_key(request: Request):
    """Verify API key if authentication is enabled."""
    if API_KEY is None:
        # Authentication disabled
        return True

    provided_key = request.headers.get("X-API-Key")
    if not provided_key:
        metrics.record_auth_failure()
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide key in X-API-Key header."
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_key.encode(), API_KEY.encode()):
        metrics.record_auth_failure()
        raise HTTPException(
            status_code=401,
            detail="Invalid API key."
        )
    return True


async def check_rate_limit(request: Request):
    """Check if request is within rate limits."""
    client_id = get_client_id(request)
    allowed, headers = rate_limiter.is_allowed(client_id)

    # Store headers for response
    request.state.rate_limit_headers = headers

    if not allowed:
        metrics.record_rate_limit()
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds.",
            headers=headers
        )
    return True

# =============================================================================
# PDF EXTRACTION LOGIC
# =============================================================================

def extract_invoice_data(full_text: str, log: RequestIdAdapter) -> dict:
    """Extract structured data from PDF text using pre-compiled patterns."""

    # Extract vendor (first non-empty line)
    lines = [line.strip() for line in full_text.strip().split('\n') if line.strip()]
    vendor = lines[0] if lines else "Unknown"
    log.info(f"Extracted vendor: {vendor[:50]}")

    # Extract invoice number
    invoice_match = INVOICE_PATTERN.search(full_text)
    invoice_no = invoice_match.group(1) if invoice_match else "N/A"
    log.info(f"Extracted invoice number: {invoice_no}")

    # Extract date (try multiple patterns)
    date = "No date found"
    for pattern in DATE_PATTERNS:
        date_match = pattern.search(full_text)
        if date_match:
            date = date_match.group(1)
            break
    log.info(f"Extracted date: {date}")

    # Extract dollar amounts
    dollar_amounts = DOLLAR_PATTERN.findall(full_text)
    log.info(f"Found {len(dollar_amounts)} dollar amounts")

    # Convert to floats
    amounts = []
    for amt in dollar_amounts:
        try:
            val = float(amt.replace(',', ''))
            amounts.append(val)
        except ValueError:
            pass

    # Get the largest amount as total
    total = max(amounts) if amounts else 0.0

    return {
        "vendor": vendor,
        "invoice_no": invoice_no,
        "date": date,
        "total": total,
        "all_amounts": amounts,
    }

# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.post("/invoice-to-json")
async def process_invoice(
    request: Request,
    file: UploadFile = File(...),
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit),
):
    """
    Extract structured data from a PDF invoice.

    - **file**: PDF file to process (max 10MB)

    Returns extracted invoice data including vendor, invoice number, date, and amounts.
    """
    start_time = time.time()
    log = get_logger(request)
    cache_hit = False

    try:
        # Read file content
        content = await file.read()
        filename = file.filename.lower() if file.filename else ""

        log.info(f"Processing file: {filename} ({len(content)} bytes)")

        # Validate file size
        if len(content) > MAX_FILE_SIZE:
            log.warning(f"File too large: {len(content)} bytes (max: {MAX_FILE_SIZE})")
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB."
            )

        # Validate file type
        if not filename.endswith('.pdf'):
            log.warning(f"Unsupported file type: {filename}")
            return JSONResponse(
                content={
                    "error": "Only PDF files are supported",
                    "file_type": "Unsupported",
                    "hint": "Please upload a .pdf file"
                },
                status_code=400
            )

        # Check cache first
        cached_result = response_cache.get(content)
        if cached_result:
            log.info("Cache hit - returning cached result")
            cache_hit = True
            processing_time = time.time() - start_time
            metrics.record_request(success=True, processing_time=processing_time, cache_hit=True)

            response = JSONResponse(content={**cached_result, "cached": True})
            for key, value in request.state.rate_limit_headers.items():
                response.headers[key] = value
            return response

        # Extract text from PDF
        log.info("Extracting text from PDF")
        pdf_file = io.BytesIO(content)
        pdf_reader = pypdf.PdfReader(pdf_file)

        full_text = ""
        for page_num, page in enumerate(pdf_reader.pages):
            page_text = page.extract_text() or ""
            full_text += page_text
            log.debug(f"Extracted {len(page_text)} chars from page {page_num + 1}")

        if not full_text.strip():
            log.warning("No text extracted from PDF")
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from PDF. The file may be scanned/image-based."
            )

        # Extract invoice data
        result = extract_invoice_data(full_text, log)
        result["file_type"] = "PDF"
        result["pages_processed"] = len(pdf_reader.pages)

        # Cache the result
        response_cache.set(content, result)

        processing_time = time.time() - start_time
        log.info(f"Processing completed in {processing_time*1000:.2f}ms")
        metrics.record_request(success=True, processing_time=processing_time, cache_hit=False)

        response = JSONResponse(content=result)
        for key, value in request.state.rate_limit_headers.items():
            response.headers[key] = value
        return response

    except HTTPException:
        raise
    except Exception as e:
        processing_time = time.time() - start_time
        log.error(f"Error processing invoice: {str(e)}")
        metrics.record_request(success=False, processing_time=processing_time, cache_hit=cache_hit)
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Invoice OCR API",
        "version": "2.0.0",
    }


@app.get("/health")
async def health_check():
    """Detailed health check endpoint."""
    return {
        "status": "healthy",
        "service": "Invoice OCR API",
        "version": "2.0.0",
        "features": {
            "authentication": API_KEY is not None,
            "rate_limiting": True,
            "caching": True,
        },
        "config": {
            "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
            "rate_limit": f"{RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
            "cache_ttl_seconds": CACHE_TTL,
        }
    }


@app.get("/metrics")
async def get_metrics(
    request: Request,
    _auth: bool = Depends(verify_api_key),
):
    """
    Get API metrics and statistics.
    Requires authentication if API key is configured.
    """
    return {
        "metrics": metrics.get_metrics(),
        "cache": response_cache.stats(),
        "rate_limiter": {
            "max_requests": RATE_LIMIT_REQUESTS,
            "window_seconds": RATE_LIMIT_WINDOW,
            "active_clients": len(rate_limiter.requests),
        }
    }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    # Get configuration from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    workers = int(os.getenv("WORKERS", "4"))

    print(f"Starting Invoice OCR API on {host}:{port} with {workers} workers")

    uvicorn.run(
        "working_pdf_extractor:app",
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )
