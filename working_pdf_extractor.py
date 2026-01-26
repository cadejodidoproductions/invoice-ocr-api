"""
Invoice OCR API - PDF Data Extraction Service
Performance-optimized with authentication, rate limiting, and caching.
"""

import asyncio
import hashlib
import io
import logging
import os
import re
import secrets
import signal
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
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

# Request timeout (seconds)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# Batch processing limit
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "10"))

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
# PDF MAGIC BYTES VALIDATION
# =============================================================================

# PDF files start with these magic bytes
PDF_MAGIC_BYTES = b"%PDF"


def validate_pdf_magic_bytes(content: bytes) -> bool:
    """Validate that file content starts with PDF magic bytes."""
    return content[:4] == PDF_MAGIC_BYTES


# =============================================================================
# PRE-COMPILED REGEX PATTERNS (Performance optimization)
# =============================================================================

# Invoice number patterns - compiled once at module load
INVOICE_PATTERNS = [
    re.compile(r'(?:Invoice|Inv|INV)\s*[#:.\-]?\s*([A-Z0-9\-]+)', re.IGNORECASE),
    re.compile(r'(?:Bill|Receipt)\s*[#:.\-]?\s*([A-Z0-9\-]+)', re.IGNORECASE),
    re.compile(r'(?:Order)\s*[#:.\-]?\s*([A-Z0-9\-]+)', re.IGNORECASE),
    re.compile(r'#\s*([A-Z0-9\-]{4,})', re.IGNORECASE),  # Generic # followed by ID
]

# Date patterns - multiple formats supported
DATE_PATTERNS = [
    # Month name formats: Jan 15, 2024 or January 15, 2024
    (re.compile(
        r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+\d{1,2},?\s+\d{4})',
        re.IGNORECASE
    ), 0.95),
    # ISO format: 2024-01-15
    (re.compile(r'(\d{4}-\d{2}-\d{2})'), 0.90),
    # US format: 01/15/2024 or 01-15-2024
    (re.compile(r'(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})'), 0.85),
    # European format with dots: 15.01.2024
    (re.compile(r'(\d{1,2}\.\d{1,2}\.\d{4})'), 0.80),
]

# Currency patterns - multi-currency support
CURRENCY_PATTERNS = [
    # USD
    (re.compile(r'\$\s*([\d,]+(?:\.\d{2})?)'), "USD", "$"),
    # EUR
    (re.compile(r'€\s*([\d,]+(?:[.,]\d{2})?)'), "EUR", "€"),
    (re.compile(r'EUR\s*([\d,]+(?:[.,]\d{2})?)'), "EUR", "EUR"),
    # GBP
    (re.compile(r'£\s*([\d,]+(?:\.\d{2})?)'), "GBP", "£"),
    (re.compile(r'GBP\s*([\d,]+(?:\.\d{2})?)'), "GBP", "GBP"),
    # Generic amount (fallback)
    (re.compile(r'(?:Total|Amount|Due|Balance)[:\s]+\$?([\d,]+(?:\.\d{2})?)'), "USD", "$"),
]

# Vendor/company patterns
VENDOR_PATTERNS = [
    re.compile(r'(?:From|Vendor|Seller|Company|Bill\s*From)[:\s]+([^\n]+)', re.IGNORECASE),
    re.compile(r'^([A-Z][A-Za-z0-9\s&.,]+(?:Inc|LLC|Ltd|Corp|Co|Company)?\.?)[\s\n]', re.MULTILINE),
]

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
            client_id
            for client_id, timestamps in self.requests.items()
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
                oldest_in_window = (
                    min(self.requests[client_id]) if self.requests[client_id] else now
                )
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
                1 for ts, _ in self.cache.values() if now - ts < self.ttl_seconds
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
        self.timeout_errors = 0
        self.batch_requests = 0
        self._lock = threading.Lock()

    def record_request(
        self, success: bool, processing_time: float, cache_hit: bool = False
    ):
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

    def record_timeout(self):
        with self._lock:
            self.timeout_errors += 1

    def record_batch_request(self):
        with self._lock:
            self.batch_requests += 1

    def get_metrics(self) -> dict:
        with self._lock:
            uptime = time.time() - self.start_time
            avg_processing_time = (
                self.total_processing_time / self.total_requests
                if self.total_requests > 0
                else 0
            )
            return {
                "uptime_seconds": round(uptime, 2),
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_hit_rate": round(
                    self.cache_hits / max(1, self.cache_hits + self.cache_misses) * 100,
                    2,
                ),
                "avg_processing_time_ms": round(avg_processing_time * 1000, 2),
                "rate_limited_requests": self.rate_limited_requests,
                "auth_failures": self.auth_failures,
                "timeout_errors": self.timeout_errors,
                "batch_requests": self.batch_requests,
            }


metrics = MetricsCollector()

# =============================================================================
# GRACEFUL SHUTDOWN
# =============================================================================

shutdown_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifespan events for graceful shutdown."""
    # Startup
    logger.info("Invoice OCR API starting up...")

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def handle_shutdown(sig):
        logger.info(f"Received signal {sig}, initiating graceful shutdown...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown, sig)

    yield

    # Shutdown
    logger.info("Invoice OCR API shutting down...")
    # Allow in-flight requests to complete (max 10 seconds)
    await asyncio.sleep(0.5)
    logger.info("Shutdown complete")


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="Invoice OCR API",
    description="Extract structured data from PDF invoices",
    version="2.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
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
            status_code=401, detail="Missing API key. Provide key in X-API-Key header."
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_key.encode(), API_KEY.encode()):
        metrics.record_auth_failure()
        raise HTTPException(status_code=401, detail="Invalid API key.")
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
            headers=headers,
        )
    return True


# =============================================================================
# PDF EXTRACTION LOGIC
# =============================================================================


def calculate_confidence(value: str, pattern_confidence: float, text: str) -> float:
    """Calculate confidence score for an extracted value."""
    if not value or value in ("N/A", "Unknown", "No date found"):
        return 0.0

    # Base confidence from pattern
    confidence = pattern_confidence

    # Boost if value appears multiple times
    occurrences = text.lower().count(value.lower())
    if occurrences > 1:
        confidence = min(1.0, confidence + 0.05 * (occurrences - 1))

    return round(confidence, 2)


def extract_vendor(full_text: str) -> tuple[str, float]:
    """Extract vendor name with confidence score."""
    # Try specific patterns first
    for pattern in VENDOR_PATTERNS:
        match = pattern.search(full_text)
        if match:
            vendor = match.group(1).strip()
            if len(vendor) > 2 and len(vendor) < 100:
                return vendor, 0.85

    # Fallback to first non-empty line
    lines = [
        line.strip() for line in full_text.strip().split("\n") if line.strip()
    ]
    if lines:
        # Skip lines that look like dates or amounts
        for line in lines[:5]:
            if not re.match(r'^[\d/$€£]', line) and len(line) > 2:
                return line[:100], 0.60

    return "Unknown", 0.0


def extract_invoice_number(full_text: str) -> tuple[str, float]:
    """Extract invoice number with confidence score."""
    for pattern in INVOICE_PATTERNS:
        match = pattern.search(full_text)
        if match:
            invoice_no = match.group(1).strip()
            if len(invoice_no) >= 2:
                # Higher confidence for longer, more specific matches
                confidence = 0.80 if len(invoice_no) >= 4 else 0.65
                return invoice_no, confidence

    return "N/A", 0.0


def extract_date(full_text: str) -> tuple[str, float]:
    """Extract date with confidence score."""
    for pattern, confidence in DATE_PATTERNS:
        match = pattern.search(full_text)
        if match:
            return match.group(1), confidence

    return "No date found", 0.0


def extract_amounts(full_text: str) -> tuple[list[dict], str, float]:
    """Extract all monetary amounts with currency detection."""
    all_amounts = []
    detected_currency = "USD"
    currency_symbol = "$"

    for pattern, currency, symbol in CURRENCY_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            detected_currency = currency
            currency_symbol = symbol
            for match in matches:
                try:
                    # Handle European number format (1.234,56)
                    cleaned = match.replace(" ", "")
                    if "," in cleaned and "." in cleaned:
                        # Determine format by position
                        if cleaned.rfind(",") > cleaned.rfind("."):
                            # European: 1.234,56
                            cleaned = cleaned.replace(".", "").replace(",", ".")
                        else:
                            # US: 1,234.56
                            cleaned = cleaned.replace(",", "")
                    else:
                        cleaned = cleaned.replace(",", "")

                    val = float(cleaned)
                    if val > 0:
                        all_amounts.append(
                            {"amount": val, "currency": currency, "symbol": symbol}
                        )
                except ValueError:
                    pass
            break  # Use first matching currency

    # Calculate total confidence based on amounts found
    confidence = 0.0
    if all_amounts:
        confidence = min(0.95, 0.70 + 0.05 * len(all_amounts))

    return all_amounts, detected_currency, confidence


def extract_invoice_data(full_text: str, log: RequestIdAdapter) -> dict:
    """Extract structured data from PDF text with confidence scores."""

    # Extract vendor
    vendor, vendor_confidence = extract_vendor(full_text)
    log.info(f"Extracted vendor: {vendor[:50]} (confidence: {vendor_confidence})")

    # Extract invoice number
    invoice_no, invoice_confidence = extract_invoice_number(full_text)
    log.info(
        f"Extracted invoice number: {invoice_no} (confidence: {invoice_confidence})"
    )

    # Extract date
    date, date_confidence = extract_date(full_text)
    log.info(f"Extracted date: {date} (confidence: {date_confidence})")

    # Extract amounts
    amounts_data, currency, amounts_confidence = extract_amounts(full_text)
    log.info(
        f"Found {len(amounts_data)} amounts in {currency} (confidence: {amounts_confidence})"
    )

    # Get amounts as simple list for backward compatibility
    amounts = [a["amount"] for a in amounts_data]
    total = max(amounts) if amounts else 0.0

    # Calculate overall confidence
    confidences = [vendor_confidence, invoice_confidence, date_confidence, amounts_confidence]
    valid_confidences = [c for c in confidences if c > 0]
    overall_confidence = (
        round(sum(valid_confidences) / len(valid_confidences), 2)
        if valid_confidences
        else 0.0
    )

    return {
        "vendor": vendor,
        "invoice_no": invoice_no,
        "date": date,
        "total": total,
        "currency": currency,
        "all_amounts": amounts,
        "confidence": {
            "overall": overall_confidence,
            "vendor": vendor_confidence,
            "invoice_no": invoice_confidence,
            "date": date_confidence,
            "amounts": amounts_confidence,
        },
    }


async def process_single_pdf(
    content: bytes, filename: str, log: RequestIdAdapter
) -> dict:
    """Process a single PDF file and return extracted data."""
    # Validate PDF magic bytes
    if not validate_pdf_magic_bytes(content):
        raise ValueError("Invalid PDF file: magic bytes not found")

    # Extract text from PDF
    pdf_file = io.BytesIO(content)
    pdf_reader = pypdf.PdfReader(pdf_file)

    full_text = ""
    for page_num, page in enumerate(pdf_reader.pages):
        page_text = page.extract_text() or ""
        full_text += page_text
        log.debug(f"Extracted {len(page_text)} chars from page {page_num + 1}")

    if not full_text.strip():
        raise ValueError(
            "Could not extract text from PDF. The file may be scanned/image-based."
        )

    # Extract invoice data
    result = extract_invoice_data(full_text, log)
    result["file_type"] = "PDF"
    result["pages_processed"] = len(pdf_reader.pages)
    result["filename"] = filename

    return result


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

    Returns extracted invoice data including vendor, invoice number, date, amounts,
    currency, and confidence scores.
    """
    start_time = time.time()
    log = get_logger(request)
    cache_hit = False

    try:
        # Read file content with timeout
        try:
            content = await asyncio.wait_for(
                file.read(), timeout=REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            metrics.record_timeout()
            raise HTTPException(
                status_code=408, detail=f"Request timeout after {REQUEST_TIMEOUT}s"
            )

        filename = file.filename.lower() if file.filename else ""
        log.info(f"Processing file: {filename} ({len(content)} bytes)")

        # Validate file size
        if len(content) > MAX_FILE_SIZE:
            log.warning(
                f"File too large: {len(content)} bytes (max: {MAX_FILE_SIZE})"
            )
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
            )

        # Validate file extension
        if not filename.endswith(".pdf"):
            log.warning(f"Unsupported file extension: {filename}")
            raise HTTPException(
                status_code=400,
                detail="Only PDF files are supported. Please upload a .pdf file.",
            )

        # Validate PDF magic bytes
        if not validate_pdf_magic_bytes(content):
            log.warning(f"Invalid PDF magic bytes: {filename}")
            raise HTTPException(
                status_code=400,
                detail="Invalid PDF file. File does not appear to be a valid PDF.",
            )

        # Check cache first
        cached_result = response_cache.get(content)
        if cached_result:
            log.info("Cache hit - returning cached result")
            cache_hit = True
            processing_time = time.time() - start_time
            metrics.record_request(
                success=True, processing_time=processing_time, cache_hit=True
            )

            response = JSONResponse(content={**cached_result, "cached": True})
            for key, value in request.state.rate_limit_headers.items():
                response.headers[key] = value
            return response

        # Process PDF with timeout
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: asyncio.run(process_single_pdf(content, filename, log))
                ),
                timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError:
            metrics.record_timeout()
            raise HTTPException(
                status_code=408,
                detail=f"PDF processing timeout after {REQUEST_TIMEOUT}s",
            )

        # Cache the result
        response_cache.set(content, result)

        processing_time = time.time() - start_time
        log.info(f"Processing completed in {processing_time*1000:.2f}ms")
        metrics.record_request(
            success=True, processing_time=processing_time, cache_hit=False
        )

        response = JSONResponse(content=result)
        for key, value in request.state.rate_limit_headers.items():
            response.headers[key] = value
        return response

    except HTTPException:
        raise
    except ValueError as e:
        processing_time = time.time() - start_time
        log.warning(f"Validation error: {str(e)}")
        metrics.record_request(
            success=False, processing_time=processing_time, cache_hit=cache_hit
        )
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        processing_time = time.time() - start_time
        log.error(f"Error processing invoice: {str(e)}")
        metrics.record_request(
            success=False, processing_time=processing_time, cache_hit=cache_hit
        )
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")


@app.post("/invoice-to-json/batch")
async def process_invoice_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit),
):
    """
    Process multiple PDF invoices in a single request.

    - **files**: List of PDF files to process (max 10 files, 10MB each)

    Returns a list of extraction results for each file.
    """
    start_time = time.time()
    log = get_logger(request)
    metrics.record_batch_request()

    # Validate batch size
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum batch size is {MAX_BATCH_SIZE}.",
        )

    if len(files) == 0:
        raise HTTPException(status_code=400, detail="No files provided.")

    results = []

    for idx, file in enumerate(files):
        file_start = time.time()
        filename = file.filename.lower() if file.filename else f"file_{idx}.pdf"

        try:
            content = await file.read()

            # Validate file size
            if len(content) > MAX_FILE_SIZE:
                results.append({
                    "filename": filename,
                    "success": False,
                    "error": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
                })
                continue

            # Validate file extension
            if not filename.endswith(".pdf"):
                results.append({
                    "filename": filename,
                    "success": False,
                    "error": "Only PDF files are supported.",
                })
                continue

            # Validate PDF magic bytes
            if not validate_pdf_magic_bytes(content):
                results.append({
                    "filename": filename,
                    "success": False,
                    "error": "Invalid PDF file.",
                })
                continue

            # Check cache
            cached_result = response_cache.get(content)
            if cached_result:
                results.append({
                    "filename": filename,
                    "success": True,
                    "cached": True,
                    "data": cached_result,
                })
                continue

            # Process PDF
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda c=content, f=filename: asyncio.run(
                    process_single_pdf(c, f, log)
                ),
            )

            # Cache result
            response_cache.set(content, result)

            results.append({
                "filename": filename,
                "success": True,
                "cached": False,
                "data": result,
            })

            file_time = time.time() - file_start
            log.info(f"Processed {filename} in {file_time*1000:.2f}ms")

        except Exception as e:
            log.error(f"Error processing {filename}: {str(e)}")
            results.append({
                "filename": filename,
                "success": False,
                "error": str(e),
            })

    processing_time = time.time() - start_time
    successful = sum(1 for r in results if r.get("success", False))
    log.info(
        f"Batch processing completed: {successful}/{len(files)} successful in {processing_time*1000:.2f}ms"
    )

    metrics.record_request(
        success=successful == len(files),
        processing_time=processing_time,
        cache_hit=False,
    )

    response_data = {
        "total": len(files),
        "successful": successful,
        "failed": len(files) - successful,
        "processing_time_ms": round(processing_time * 1000, 2),
        "results": results,
    }

    response = JSONResponse(content=response_data)
    for key, value in request.state.rate_limit_headers.items():
        response.headers[key] = value
    return response


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Invoice OCR API",
        "version": "2.1.0",
    }


@app.get("/health")
async def health_check():
    """Detailed health check endpoint."""
    return {
        "status": "healthy",
        "service": "Invoice OCR API",
        "version": "2.1.0",
        "features": {
            "authentication": API_KEY is not None,
            "rate_limiting": True,
            "caching": True,
            "batch_processing": True,
            "multi_currency": True,
            "confidence_scores": True,
        },
        "config": {
            "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
            "rate_limit": f"{RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
            "cache_ttl_seconds": CACHE_TTL,
            "request_timeout_seconds": REQUEST_TIMEOUT,
            "max_batch_size": MAX_BATCH_SIZE,
        },
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
        },
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
