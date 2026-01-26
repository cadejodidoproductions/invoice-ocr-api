"""
Test configuration and fixtures for Invoice OCR API tests (v2.3.0).
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

from working_pdf_extractor import app, rate_limiter, response_cache, metrics, job_store


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


@pytest.fixture
def sample_png_content():
    """Create minimal valid PNG content for testing."""
    # Minimal 1x1 white PNG
    return b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82'


@pytest.fixture
def sample_jpg_content():
    """Create minimal valid JPEG content for testing."""
    # Minimal 1x1 white JPEG
    return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7telecast,entity-telecast-entity\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\xc7\xff\xd9'


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before each test."""
    # Clear rate limiter
    with rate_limiter._lock:
        rate_limiter.requests.clear()

    # Clear cache
    with response_cache._lock:
        response_cache.cache.clear()

    # Reset metrics (v2.3.0 uses _c dict and _times list)
    with metrics._lock:
        metrics._c.clear()
        metrics._times.clear()

    # Clear job store
    with job_store._lock:
        job_store._jobs.clear()

    yield
