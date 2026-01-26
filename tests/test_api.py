"""
Tests for Invoice OCR API endpoints.
"""

import io
import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_root_endpoint(self, client):
        """Test the root endpoint returns health status."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Invoice OCR API"
        assert "version" in data

    def test_health_endpoint(self, client):
        """Test the detailed health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "features" in data
        assert "config" in data
        assert data["features"]["rate_limiting"] is True
        assert data["features"]["caching"] is True
        assert data["features"]["batch_processing"] is True
        assert data["features"]["multi_currency"] is True
        assert data["features"]["confidence_scores"] is True


class TestMetricsEndpoint:
    """Tests for metrics endpoint."""

    def test_metrics_requires_auth(self, client):
        """Test that metrics endpoint requires authentication."""
        response = client.get("/metrics")
        assert response.status_code == 401

    def test_metrics_with_valid_auth(self, client, auth_headers):
        """Test metrics endpoint with valid authentication."""
        response = client.get("/metrics", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert "cache" in data
        assert "rate_limiter" in data

    def test_metrics_with_invalid_auth(self, client, invalid_auth_headers):
        """Test metrics endpoint with invalid authentication."""
        response = client.get("/metrics", headers=invalid_auth_headers)
        assert response.status_code == 401


class TestInvoiceProcessing:
    """Tests for invoice processing endpoint."""

    def test_process_invoice_requires_file(self, client, auth_headers):
        """Test that invoice processing requires a file."""
        response = client.post("/invoice-to-json", headers=auth_headers)
        assert response.status_code == 422  # Validation error

    def test_process_non_pdf_file(self, client, auth_headers):
        """Test rejection of non-PDF files."""
        files = {"file": ("document.txt", io.BytesIO(b"Hello World"), "text/plain")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    def test_process_invalid_pdf_magic_bytes(self, client, auth_headers, invalid_pdf_content):
        """Test rejection of files without valid PDF magic bytes."""
        files = {"file": ("invoice.pdf", io.BytesIO(invalid_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code == 400
        assert "Invalid PDF" in response.json()["detail"]

    def test_process_oversized_file(self, client, auth_headers):
        """Test rejection of files exceeding size limit."""
        # Create a file larger than MAX_FILE_SIZE (10MB)
        large_content = b"%PDF-1.4" + b"x" * (11 * 1024 * 1024)
        files = {"file": ("large.pdf", io.BytesIO(large_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code == 413
        assert "too large" in response.json()["detail"].lower()

    def test_request_id_header(self, client, auth_headers, sample_pdf_content):
        """Test that response includes X-Request-ID header."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert "X-Request-ID" in response.headers

    def test_rate_limit_headers(self, client, auth_headers, sample_pdf_content):
        """Test that response includes rate limit headers."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers


class TestBatchProcessing:
    """Tests for batch processing endpoint."""

    def test_batch_empty_files(self, client, auth_headers):
        """Test batch processing with no files."""
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=[])
        assert response.status_code == 422  # FastAPI validation error for missing required field

    def test_batch_exceeds_limit(self, client, auth_headers, sample_pdf_content):
        """Test batch processing with too many files."""
        # Create 11 files (exceeds MAX_BATCH_SIZE of 10)
        files = [
            ("files", (f"invoice_{i}.pdf", io.BytesIO(sample_pdf_content), "application/pdf"))
            for i in range(11)
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files)
        assert response.status_code == 400
        assert "Too many files" in response.json()["detail"]

    def test_batch_mixed_valid_invalid(self, client, auth_headers, sample_pdf_content, invalid_pdf_content):
        """Test batch processing with mix of valid and invalid files."""
        files = [
            ("files", ("valid.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
            ("files", ("invalid.pdf", io.BytesIO(invalid_pdf_content), "application/pdf")),
            ("files", ("not_pdf.txt", io.BytesIO(b"text content"), "text/plain")),
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["failed"] >= 2  # At least invalid.pdf and not_pdf.txt should fail


class TestAuthentication:
    """Tests for authentication."""

    def test_missing_api_key(self, client, sample_pdf_content):
        """Test request without API key."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", files=files)
        assert response.status_code == 401
        assert "Missing API key" in response.json()["detail"]

    def test_invalid_api_key(self, client, invalid_auth_headers, sample_pdf_content):
        """Test request with invalid API key."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=invalid_auth_headers, files=files)
        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]


class TestCaching:
    """Tests for response caching."""

    def test_cache_hit_indicator(self, client, auth_headers, sample_pdf_content):
        """Test that cached responses include cached indicator."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}

        # First request - should not be cached
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files)

        # Second request with same content - should be cached
        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/invoice-to-json", headers=auth_headers, files=files2)

        # If processing succeeded, check cache behavior
        if response1.status_code == 200 and response2.status_code == 200:
            data2 = response2.json()
            assert data2.get("cached") is True
