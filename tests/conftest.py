"""
Test configuration and fixtures for Invoice OCR API tests (v2.2.0).
"""

import io
import os
import pytest
from fastapi.testclient import TestClient

# Set test environment variables before importing app
os.environ["INVOICE_OCR_API_KEY"] = "test-api-key-12345"
os.environ["RATE_LIMIT_REQUESTS"] = "1000"
os.environ["RATE_LIMIT_WINDOW"] = "60"
os.environ["REQUEST_TIMEOUT"] = "30"
os.environ["MAX_BATCH_SIZE"] = "10"

from working_pdf_extractor import app, rate_limiter, response_cache, metrics


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    """Return authentication headers for protected endpoints."""
    return {"X-API-Key": "test-api-key-12345"}


@pytest.fixture
def invalid_auth_headers():
    """Return invalid authentication headers."""
    return {"X-API-Key": "invalid-key"}


@pytest.fixture
def sample_pdf_content():
    """Create a minimal valid PDF content for testing."""
    # Minimal PDF structure
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(ACME Corporation
Invoice #12345
Date: January 15, 2024
Total: $1,250.00
Subtotal: $1,000.00
Tax: $250.00) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
307
%%EOF"""
    return pdf_content


@pytest.fixture
def sample_pdf_file(sample_pdf_content):
    """Create a file-like object from sample PDF content."""
    return ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")


@pytest.fixture
def invalid_pdf_content():
    """Create content that is not a valid PDF."""
    return b"This is not a PDF file"


@pytest.fixture
def empty_pdf_content():
    """Create a PDF with no extractable text."""
    # Minimal PDF with empty content stream
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << >> >>
endobj
4 0 obj
<< /Length 0 >>
stream
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000208 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
258
%%EOF"""
    return pdf_content


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before each test."""
    # Clear rate limiter
    with rate_limiter._lock:
        rate_limiter.requests.clear()

    # Clear cache
    with response_cache._lock:
        response_cache.cache.clear()

    # Reset metrics (v2.2.0 uses _c dict and _times list)
    with metrics._lock:
        metrics._c.clear()
        metrics._times.clear()

    yield
