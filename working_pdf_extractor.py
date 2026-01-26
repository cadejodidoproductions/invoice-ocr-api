"""
Invoice OCR API - PDF Data Extraction Service
Version 2.2.0 - Enhanced with line items, multi-currency, export formats, webhooks.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import signal
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
import pypdf
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

# =============================================================================
# CONFIGURATION
# =============================================================================

API_KEY = os.getenv("INVOICE_OCR_API_KEY", None)
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "100"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "10"))
WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "10"))
JSON_LOGGING = os.getenv("JSON_LOGGING", "false").lower() == "true"

# =============================================================================
# JSON STRUCTURED LOGGING
# =============================================================================


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "N/A"),
        })


log_handler = logging.StreamHandler()
if JSON_LOGGING:
    log_handler.setFormatter(JSONFormatter())
else:
    log_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[log_handler])


class RequestIdAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})["request_id"] = self.extra.get("request_id", "N/A")
        return msg, kwargs


logger = logging.getLogger("invoice_ocr")

# =============================================================================
# PDF VALIDATION
# =============================================================================

PDF_MAGIC_BYTES = b"%PDF"


def validate_pdf_magic_bytes(content: bytes) -> bool:
    return content[:4] == PDF_MAGIC_BYTES


# =============================================================================
# REGEX PATTERNS
# =============================================================================

INVOICE_PATTERNS = [
    re.compile(r"(?:Invoice|Inv|INV)\s*[#:.\-]?\s*([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"(?:Bill|Receipt)\s*[#:.\-]?\s*([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"(?:Order)\s*[#:.\-]?\s*([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"#\s*([A-Z0-9\-]{4,})", re.IGNORECASE),
]

PO_PATTERNS = [
    re.compile(r"(?:P\.?O\.?|Purchase\s*Order)\s*[#:.\-]?\s*([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"PO\s*(?:Number|#)?[:\s]+([A-Z0-9\-]+)", re.IGNORECASE),
]

DATE_PATTERNS = [
    (re.compile(r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})", re.IGNORECASE), 0.95),
    (re.compile(r"(\d{4}-\d{2}-\d{2})"), 0.90),
    (re.compile(r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})"), 0.85),
    (re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})"), 0.80),
]

DUE_DATE_PATTERNS = [
    re.compile(r"(?:Due\s*Date|Payment\s*Due|Pay\s*By|Due\s*By)[:\s]+([^\n]{5,30})", re.IGNORECASE),
    re.compile(r"(?:Due|Payable)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})", re.IGNORECASE),
]

# Multi-currency patterns (expanded)
CURRENCY_PATTERNS = [
    (re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)"), "USD", "$"),
    (re.compile(r"USD\s*([\d,]+(?:\.\d{2})?)"), "USD", "USD"),
    (re.compile(r"€\s*([\d,]+(?:[.,]\d{2})?)"), "EUR", "€"),
    (re.compile(r"EUR\s*([\d,]+(?:[.,]\d{2})?)"), "EUR", "EUR"),
    (re.compile(r"£\s*([\d,]+(?:\.\d{2})?)"), "GBP", "£"),
    (re.compile(r"GBP\s*([\d,]+(?:\.\d{2})?)"), "GBP", "GBP"),
    (re.compile(r"C\$\s*([\d,]+(?:\.\d{2})?)"), "CAD", "C$"),
    (re.compile(r"CAD\s*([\d,]+(?:\.\d{2})?)"), "CAD", "CAD"),
    (re.compile(r"A\$\s*([\d,]+(?:\.\d{2})?)"), "AUD", "A$"),
    (re.compile(r"AUD\s*([\d,]+(?:\.\d{2})?)"), "AUD", "AUD"),
    (re.compile(r"¥\s*([\d,]+)"), "JPY", "¥"),
    (re.compile(r"JPY\s*([\d,]+)"), "JPY", "JPY"),
    (re.compile(r"CHF\s*([\d,]+(?:\.\d{2})?)"), "CHF", "CHF"),
    (re.compile(r"₹\s*([\d,]+(?:\.\d{2})?)"), "INR", "₹"),
    (re.compile(r"INR\s*([\d,]+(?:\.\d{2})?)"), "INR", "INR"),
    (re.compile(r"CNY\s*([\d,]+(?:\.\d{2})?)"), "CNY", "CNY"),
    (re.compile(r"RMB\s*([\d,]+(?:\.\d{2})?)"), "CNY", "RMB"),
]

TAX_PATTERNS = [
    (re.compile(r"(?:Tax|VAT|GST|HST|Sales\s*Tax)[:\s]+[\$€£¥₹]?\s*([\d,]+(?:\.\d{2})?)"), "tax"),
    (re.compile(r"(?:Subtotal|Sub-total|Sub\s*Total)[:\s]+[\$€£¥₹]?\s*([\d,]+(?:\.\d{2})?)"), "subtotal"),
    (re.compile(r"(?:Shipping|Freight|Delivery)[:\s]+[\$€£¥₹]?\s*([\d,]+(?:\.\d{2})?)"), "shipping"),
    (re.compile(r"(?:Discount)[:\s]+[\$€£¥₹]?\s*-?([\d,]+(?:\.\d{2})?)"), "discount"),
    (re.compile(r"(?:Grand\s*Total|Total\s*Due|Amount\s*Due|Total)[:\s]+[\$€£¥₹]?\s*([\d,]+(?:\.\d{2})?)"), "total"),
]

LINE_ITEM_PATTERN = re.compile(
    r"^(.{5,50}?)\s{2,}(\d+(?:\.\d+)?)\s+[\$€£]?([\d,]+(?:\.\d{2})?)\s+[\$€£]?([\d,]+(?:\.\d{2})?)$",
    re.MULTILINE,
)

VENDOR_PATTERNS = [
    re.compile(r"(?:From|Vendor|Seller|Bill\s*From)[:\s]+([^\n]+)", re.IGNORECASE),
    re.compile(r"^([A-Z][A-Za-z0-9\s&.,]+(?:Inc|LLC|Ltd|Corp|Co)?\.?)[\s\n]", re.MULTILINE),
]

ADDRESS_PATTERN = re.compile(
    r"(\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln)[.,]?\s*"
    r"(?:Suite|Ste|Apt|Unit|#)?\s*\d*[.,]?\s*[A-Za-z\s]+,?\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?)",
    re.IGNORECASE,
)

# =============================================================================
# RATE LIMITER
# =============================================================================


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> tuple[bool, dict]:
        now = time.time()
        with self._lock:
            self.requests[client_id] = [t for t in self.requests[client_id] if t > now - self.window_seconds]
            if len(self.requests[client_id]) >= self.max_requests:
                return False, {"X-RateLimit-Limit": str(self.max_requests), "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(min(self.requests[client_id]) + self.window_seconds))}
            self.requests[client_id].append(now)
            return True, {"X-RateLimit-Limit": str(self.max_requests), "X-RateLimit-Remaining": str(self.max_requests - len(self.requests[client_id])), "X-RateLimit-Reset": str(int(now + self.window_seconds))}


rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)

# =============================================================================
# RESPONSE CACHE
# =============================================================================


class ResponseCache:
    def __init__(self, max_size: int, ttl_seconds: int):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, content: bytes) -> Optional[dict]:
        key = hashlib.sha256(content).hexdigest()
        with self._lock:
            if key in self.cache and time.time() - self.cache[key][0] < self.ttl_seconds:
                return self.cache[key][1].copy()
            self.cache.pop(key, None)
        return None

    def set(self, content: bytes, data: dict):
        key = hashlib.sha256(content).hexdigest()
        with self._lock:
            if len(self.cache) >= self.max_size:
                oldest = min(self.cache, key=lambda k: self.cache[k][0])
                del self.cache[oldest]
            self.cache[key] = (time.time(), data.copy())

    def stats(self) -> dict:
        with self._lock:
            valid = sum(1 for t, _ in self.cache.values() if time.time() - t < self.ttl_seconds)
            return {"total_entries": len(self.cache), "valid_entries": valid, "max_size": self.max_size}


response_cache = ResponseCache(CACHE_MAX_SIZE, CACHE_TTL)

# =============================================================================
# METRICS (with Prometheus export)
# =============================================================================


class MetricsCollector:
    def __init__(self):
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._c = defaultdict(int)
        self._times: list[float] = []

    def inc(self, name: str, val: int = 1):
        with self._lock:
            self._c[name] += val

    def observe_time(self, val: float):
        with self._lock:
            self._times.append(val)
            if len(self._times) > 1000:
                self._times = self._times[-1000:]

    def get(self) -> dict:
        with self._lock:
            avg = sum(self._times) / len(self._times) if self._times else 0
            return {
                "uptime_seconds": round(time.time() - self.start_time, 2),
                "requests_total": self._c["requests"],
                "requests_success": self._c["success"],
                "requests_failed": self._c["failed"],
                "cache_hits": self._c["cache_hit"],
                "cache_misses": self._c["cache_miss"],
                "rate_limited": self._c["rate_limited"],
                "auth_failures": self._c["auth_fail"],
                "timeouts": self._c["timeout"],
                "webhooks_sent": self._c["webhook"],
                "avg_time_ms": round(avg * 1000, 2),
            }

    def prometheus(self) -> str:
        m = self.get()
        lines = [
            f"# HELP invoice_ocr_uptime Uptime in seconds",
            f"# TYPE invoice_ocr_uptime gauge",
            f"invoice_ocr_uptime {m['uptime_seconds']}",
            f"# HELP invoice_ocr_requests_total Total requests",
            f"# TYPE invoice_ocr_requests_total counter",
            f"invoice_ocr_requests_total {m['requests_total']}",
            f"invoice_ocr_requests_success {m['requests_success']}",
            f"invoice_ocr_requests_failed {m['requests_failed']}",
            f"invoice_ocr_cache_hits {m['cache_hits']}",
            f"invoice_ocr_cache_misses {m['cache_misses']}",
            f"invoice_ocr_rate_limited {m['rate_limited']}",
            f"invoice_ocr_avg_time_ms {m['avg_time_ms']}",
        ]
        return "\n".join(lines) + "\n"


metrics = MetricsCollector()

# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================


def parse_amount(s: str) -> float:
    s = s.replace(" ", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def extract_vendor(text: str) -> tuple[str, float]:
    for p in VENDOR_PATTERNS:
        m = p.search(text)
        if m and 2 < len(m.group(1).strip()) < 100:
            return m.group(1).strip(), 0.85
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for l in lines[:5]:
        if not re.match(r"^[\d/$€£¥₹]", l) and len(l) > 2:
            return l[:100], 0.60
    return "Unknown", 0.0


def extract_invoice_number(text: str) -> tuple[str, float]:
    for p in INVOICE_PATTERNS:
        m = p.search(text)
        if m and len(m.group(1)) >= 2:
            return m.group(1).strip(), 0.80
    return "N/A", 0.0


def extract_po_number(text: str) -> tuple[str, float]:
    for p in PO_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(1).strip(), 0.85
    return "N/A", 0.0


def extract_date(text: str) -> tuple[str, float]:
    for p, conf in DATE_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(1), conf
    return "N/A", 0.0


def extract_due_date(text: str) -> tuple[str, float]:
    for p in DUE_DATE_PATTERNS:
        m = p.search(text)
        if m:
            val = m.group(1).strip()
            for dp, c in DATE_PATTERNS:
                dm = dp.search(val)
                if dm:
                    return dm.group(1), c
            return val[:30], 0.70
    return "N/A", 0.0


def extract_amounts(text: str) -> dict:
    result = {"subtotal": None, "tax": None, "shipping": None, "discount": None, "total": None, "currency": "USD", "all_amounts": []}
    for p, t in TAX_PATTERNS:
        m = p.search(text)
        if m:
            result[t] = parse_amount(m.group(1))
    for p, cur, sym in CURRENCY_PATTERNS:
        matches = p.findall(text)
        if matches:
            result["currency"] = cur
            result["all_amounts"] = [parse_amount(m) for m in matches if parse_amount(m) > 0]
            break
    if result["total"] is None and result["all_amounts"]:
        result["total"] = max(result["all_amounts"])
    return result


def extract_line_items(text: str) -> list[dict]:
    items = []
    for m in LINE_ITEM_PATTERN.findall(text)[:20]:
        if len(m) >= 4:
            items.append({"description": m[0].strip(), "quantity": parse_amount(m[1]), "unit_price": parse_amount(m[2]), "amount": parse_amount(m[3])})
    return items


def extract_addresses(text: str) -> list[str]:
    return [m.strip() for m in ADDRESS_PATTERN.findall(text)[:3]]


def extract_invoice_data(text: str, log: RequestIdAdapter) -> dict:
    vendor, vc = extract_vendor(text)
    inv_no, ic = extract_invoice_number(text)
    po_no, pc = extract_po_number(text)
    date, dc = extract_date(text)
    due, duc = extract_due_date(text)
    amounts = extract_amounts(text)
    items = extract_line_items(text)
    addrs = extract_addresses(text)

    confs = [c for c in [vc, ic, dc] if c > 0]
    overall = round(sum(confs) / len(confs), 2) if confs else 0.0

    log.info(f"Extracted: vendor={vendor[:30]}, inv={inv_no}, total={amounts['total']}, items={len(items)}")

    return {
        "vendor": vendor,
        "invoice_no": inv_no,
        "po_number": po_no,
        "date": date,
        "due_date": due,
        "subtotal": amounts["subtotal"],
        "tax": amounts["tax"],
        "shipping": amounts["shipping"],
        "discount": amounts["discount"],
        "total": amounts["total"],
        "currency": amounts["currency"],
        "all_amounts": amounts["all_amounts"],
        "line_items": items,
        "addresses": addrs,
        "confidence": {"overall": overall, "vendor": vc, "invoice_no": ic, "po_number": pc, "date": dc, "due_date": duc},
    }


# =============================================================================
# PDF PROCESSING
# =============================================================================


async def process_pdf(content: bytes, filename: str, log: RequestIdAdapter, password: Optional[str] = None) -> dict:
    if not validate_pdf_magic_bytes(content):
        raise ValueError("Invalid PDF: bad magic bytes")

    pdf = io.BytesIO(content)
    try:
        reader = pypdf.PdfReader(pdf, password=password) if password else pypdf.PdfReader(pdf)
        if reader.is_encrypted and not password:
            raise ValueError("PDF is encrypted. Provide password.")
    except pypdf.errors.FileNotDecryptedError:
        raise ValueError("PDF encrypted. Wrong password.")

    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    if not text.strip():
        raise ValueError("No text extracted. PDF may be scanned.")

    result = extract_invoice_data(text, log)
    result["file_type"] = "PDF"
    result["pages"] = len(reader.pages)
    result["filename"] = filename
    return result


# =============================================================================
# EXPORT FUNCTIONS
# =============================================================================


def to_csv(data: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Field", "Value"])
    for k in ["vendor", "invoice_no", "po_number", "date", "due_date", "currency", "subtotal", "tax", "shipping", "discount", "total"]:
        w.writerow([k, data.get(k, "")])
    if data.get("line_items"):
        w.writerow([])
        w.writerow(["Description", "Qty", "Unit Price", "Amount"])
        for i in data["line_items"]:
            w.writerow([i.get("description"), i.get("quantity"), i.get("unit_price"), i.get("amount")])
    return out.getvalue()


def to_xml(data: dict) -> str:
    root = ET.Element("invoice")
    for k in ["vendor", "invoice_no", "po_number", "date", "due_date", "currency", "subtotal", "tax", "shipping", "discount", "total"]:
        ET.SubElement(root, k).text = str(data.get(k) or "")
    if data.get("line_items"):
        items = ET.SubElement(root, "line_items")
        for i in data["line_items"]:
            item = ET.SubElement(items, "item")
            for f in ["description", "quantity", "unit_price", "amount"]:
                ET.SubElement(item, f).text = str(i.get(f, ""))
    return ET.tostring(root, encoding="unicode")


# =============================================================================
# WEBHOOK
# =============================================================================


async def send_webhook(url: str, data: dict, req_id: str):
    metrics.inc("webhook")
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as c:
            await c.post(url, json={"request_id": req_id, "data": data, "ts": datetime.utcnow().isoformat()})
    except Exception:
        metrics.inc("webhook_fail")


# =============================================================================
# FASTAPI APP
# =============================================================================

shutdown_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Invoice OCR API v2.2.0")
    yield
    logger.info("Shutting down")


app = FastAPI(title="Invoice OCR API", version="2.2.0", lifespan=lifespan)
v1 = APIRouter(prefix="/v1", tags=["v1"])

app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"])


@app.middleware("http")
async def req_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = rid
    request.state.rate_limit_headers = {}
    resp = await call_next(request)
    resp.headers["X-Request-ID"] = rid
    return resp


def get_log(request: Request) -> RequestIdAdapter:
    return RequestIdAdapter(logger, {"request_id": getattr(request.state, "request_id", "N/A")})


async def auth(request: Request):
    if API_KEY and (not request.headers.get("X-API-Key") or not secrets.compare_digest(request.headers.get("X-API-Key", "").encode(), API_KEY.encode())):
        metrics.inc("auth_fail")
        raise HTTPException(401, "Invalid API key")


async def rate_limit(request: Request):
    ok, hdrs = rate_limiter.is_allowed(request.headers.get("X-API-Key", request.client.host if request.client else "unknown"))
    request.state.rate_limit_headers = hdrs
    if not ok:
        metrics.inc("rate_limited")
        raise HTTPException(429, "Rate limited", headers=hdrs)


# =============================================================================
# ENDPOINTS
# =============================================================================


@app.post("/invoice-to-json")
async def process_invoice(
    request: Request,
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    password: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    export: Optional[str] = Query(None, regex="^(json|csv|xml)$"),
    _a: bool = Depends(auth),
    _r: bool = Depends(rate_limit),
):
    """Extract data from PDF invoice. Supports password-protected PDFs, webhooks, and export formats."""
    t0 = time.time()
    log = get_log(request)
    metrics.inc("requests")

    try:
        content = await asyncio.wait_for(file.read(), REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        metrics.inc("timeout")
        raise HTTPException(408, "Timeout")

    fn = (file.filename or "").lower()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File too large")
    if not fn.endswith(".pdf"):
        raise HTTPException(400, "Only PDF supported")
    if not validate_pdf_magic_bytes(content):
        raise HTTPException(400, "Invalid PDF")

    cached = response_cache.get(content) if not password else None
    if cached:
        metrics.inc("cache_hit")
        metrics.inc("success")
        if webhook_url:
            bg.add_task(send_webhook, webhook_url, cached, request.state.request_id)
        return _resp(cached, export, request, True)

    metrics.inc("cache_miss")
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, lambda: asyncio.run(process_pdf(content, fn, log, password))),
            REQUEST_TIMEOUT
        )
    except asyncio.TimeoutError:
        metrics.inc("timeout")
        metrics.inc("failed")
        raise HTTPException(408, "Processing timeout")
    except ValueError as e:
        metrics.inc("failed")
        raise HTTPException(422, str(e))

    if not password:
        response_cache.set(content, result)

    metrics.inc("success")
    metrics.observe_time(time.time() - t0)
    if webhook_url:
        bg.add_task(send_webhook, webhook_url, result, request.state.request_id)

    return _resp(result, export, request, False)


def _resp(data: dict, fmt: Optional[str], request: Request, cached: bool) -> Response:
    hdrs = dict(request.state.rate_limit_headers)
    if fmt == "csv":
        return Response(to_csv(data), media_type="text/csv", headers={**hdrs, "Content-Disposition": "attachment; filename=invoice.csv"})
    if fmt == "xml":
        return Response(to_xml(data), media_type="application/xml", headers={**hdrs, "Content-Disposition": "attachment; filename=invoice.xml"})
    r = JSONResponse({**data, "cached": cached})
    for k, v in hdrs.items():
        r.headers[k] = v
    return r


@app.post("/invoice-to-json/batch")
async def batch(request: Request, files: list[UploadFile] = File(...), password: Optional[str] = Form(None), _a: bool = Depends(auth), _r: bool = Depends(rate_limit)):
    """Process multiple PDFs in one request."""
    t0 = time.time()
    log = get_log(request)
    metrics.inc("requests")

    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(400, f"Max {MAX_BATCH_SIZE} files")
    if not files:
        raise HTTPException(400, "No files")

    results = []
    for f in files:
        fn = (f.filename or "").lower()
        try:
            content = await f.read()
            if len(content) > MAX_FILE_SIZE or not fn.endswith(".pdf") or not validate_pdf_magic_bytes(content):
                results.append({"filename": fn, "success": False, "error": "Invalid file"})
                continue
            cached = response_cache.get(content) if not password else None
            if cached:
                results.append({"filename": fn, "success": True, "cached": True, "data": cached})
                continue
            data = await asyncio.get_event_loop().run_in_executor(None, lambda c=content, n=fn: asyncio.run(process_pdf(c, n, log, password)))
            if not password:
                response_cache.set(content, data)
            results.append({"filename": fn, "success": True, "cached": False, "data": data})
        except Exception as e:
            results.append({"filename": fn, "success": False, "error": str(e)})

    ok = sum(1 for r in results if r.get("success"))
    metrics.inc("success" if ok == len(files) else "failed")
    metrics.observe_time(time.time() - t0)

    r = JSONResponse({"total": len(files), "successful": ok, "failed": len(files) - ok, "time_ms": round((time.time() - t0) * 1000, 2), "results": results})
    for k, v in request.state.rate_limit_headers.items():
        r.headers[k] = v
    return r


@app.get("/")
async def root():
    return {"status": "healthy", "service": "Invoice OCR API", "version": "2.2.0"}


@app.get("/health")
async def health():
    return {
        "status": "healthy", "version": "2.2.0",
        "features": {"auth": API_KEY is not None, "rate_limiting": True, "caching": True, "batch": True, "multi_currency": True,
                     "line_items": True, "export_formats": ["json", "csv", "xml"], "webhooks": True, "pdf_password": True},
        "config": {"max_file_mb": MAX_FILE_SIZE // 1024 // 1024, "rate_limit": f"{RATE_LIMIT_REQUESTS}/{RATE_LIMIT_WINDOW}s", "timeout": REQUEST_TIMEOUT, "batch_size": MAX_BATCH_SIZE}
    }


@app.get("/metrics")
async def metrics_json(_a: bool = Depends(auth)):
    return {"metrics": metrics.get(), "cache": response_cache.stats()}


@app.get("/metrics/prometheus")
async def metrics_prom():
    return PlainTextResponse(metrics.prometheus(), media_type="text/plain")


# V1 API routes
v1.add_api_route("/invoice-to-json", process_invoice, methods=["POST"])
v1.add_api_route("/invoice-to-json/batch", batch, methods=["POST"])
v1.add_api_route("/health", health, methods=["GET"])
v1.add_api_route("/metrics", metrics_json, methods=["GET"])
app.include_router(v1)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("working_pdf_extractor:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")), workers=int(os.getenv("WORKERS", "4")))
