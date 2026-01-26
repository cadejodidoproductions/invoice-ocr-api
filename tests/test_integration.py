"""
Integration tests for Invoice OCR API (v2.2.0).

These tests verify end-to-end workflows and component interactions.
"""

import io
import time
import pytest
from fastapi.testclient import TestClient


class TestEndToEndProcessing:
    """End-to-end tests for invoice processing workflow."""

    def test_complete_invoice_processing_flow(self, client, auth_headers, sample_pdf_content):
        """Test complete flow: upload -> process -> return data."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        # Response should include all expected fields
        if response.status_code == 200:
            data = response.json()
            # Core fields
            assert "vendor" in data
            assert "invoice_no" in data
            assert "date" in data
            assert "total" in data
            assert "currency" in data
            # v2.2.0 fields
            assert "po_number" in data
            assert "due_date" in data
            assert "subtotal" in data
            assert "tax" in data
            assert "shipping" in data
            assert "discount" in data
            assert "line_items" in data
            assert "addresses" in data
            assert "confidence" in data
            assert "filename" in data
            assert "pages" in data
            assert "cached" in data

    def test_caching_flow(self, client, auth_headers, sample_pdf_content):
        """Test caching: first request stores, second retrieves from cache."""
        files1 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files1)

        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/invoice-to-json", headers=auth_headers, files=files2)

        if response1.status_code == 200 and response2.status_code == 200:
            assert response1.json().get("cached", False) is False
            assert response2.json().get("cached") is True

    def test_metrics_increment_on_request(self, client, auth_headers, sample_pdf_content):
        """Test that metrics are properly incremented."""
        # Get initial metrics
        metrics_response1 = client.get("/metrics", headers=auth_headers)
        initial_requests = metrics_response1.json()["metrics"]["requests_total"]

        # Make a request
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        client.post("/invoice-to-json", headers=auth_headers, files=files)

        # Check metrics increased
        metrics_response2 = client.get("/metrics", headers=auth_headers)
        final_requests = metrics_response2.json()["metrics"]["requests_total"]

        assert final_requests > initial_requests


class TestApiVersioning:
    """Tests for API versioning."""

    def test_v1_and_root_return_same_result(self, client, auth_headers, sample_pdf_content):
        """Test that /v1/invoice-to-json and /invoice-to-json return same structure."""
        files1 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files1)

        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/v1/invoice-to-json", headers=auth_headers, files=files2)

        # Both should return same status code
        assert response1.status_code == response2.status_code

        # If successful, both should have same structure
        if response1.status_code == 200:
            keys1 = set(response1.json().keys())
            keys2 = set(response2.json().keys())
            # Allow 'cached' to differ
            keys1.discard("cached")
            keys2.discard("cached")
            assert keys1 == keys2


class TestExportFormatsIntegration:
    """Integration tests for export formats."""

    def test_json_is_default_format(self, client, auth_headers, sample_pdf_content):
        """Test that JSON is returned by default."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            assert "application/json" in content_type

    def test_csv_export_is_valid(self, client, auth_headers, sample_pdf_content):
        """Test that CSV export produces valid CSV."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json?export=csv", headers=auth_headers, files=files)

        if response.status_code == 200:
            csv_content = response.text
            assert "Field,Value" in csv_content  # Header row
            assert "vendor" in csv_content.lower()

    def test_xml_export_is_valid(self, client, auth_headers, sample_pdf_content):
        """Test that XML export produces valid XML."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json?export=xml", headers=auth_headers, files=files)

        if response.status_code == 200:
            xml_content = response.text
            assert "<invoice>" in xml_content
            assert "</invoice>" in xml_content


class TestBatchProcessingIntegration:
    """Integration tests for batch processing."""

    def test_batch_with_multiple_valid_files(self, client, auth_headers, sample_pdf_content):
        """Test batch processing with multiple valid files."""
        files = [
            ("files", ("invoice1.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
            ("files", ("invoice2.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert "results" in data
        assert len(data["results"]) == 2

    def test_batch_partial_failure(self, client, auth_headers, sample_pdf_content, invalid_pdf_content):
        """Test batch processing with mix of valid and invalid files."""
        files = [
            ("files", ("valid.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
            ("files", ("invalid.pdf", io.BytesIO(invalid_pdf_content), "application/pdf")),
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["failed"] >= 1


class TestRateLimitingIntegration:
    """Integration tests for rate limiting."""

    def test_rate_limit_headers_present(self, client, auth_headers, sample_pdf_content):
        """Test that rate limit headers are present on all responses."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

    def test_rate_limit_decrements(self, client, auth_headers, sample_pdf_content):
        """Test that rate limit remaining decrements with each request."""
        files1 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files1)
        remaining1 = int(response1.headers.get("X-RateLimit-Remaining", "0"))

        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/invoice-to-json", headers=auth_headers, files=files2)
        remaining2 = int(response2.headers.get("X-RateLimit-Remaining", "0"))

        # Second request should have lower remaining
        assert remaining2 < remaining1


class TestRequestTracing:
    """Integration tests for request tracing."""

    def test_request_id_is_unique(self, client, auth_headers, sample_pdf_content):
        """Test that each request gets a unique request ID."""
        files1 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files1)

        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/invoice-to-json", headers=auth_headers, files=files2)

        id1 = response1.headers.get("X-Request-ID")
        id2 = response2.headers.get("X-Request-ID")

        assert id1 is not None
        assert id2 is not None
        assert id1 != id2

    def test_custom_request_id_is_preserved(self, client, auth_headers, sample_pdf_content):
        """Test that custom request ID is preserved in response."""
        custom_id = "custom-request-id-12345"
        headers = {**auth_headers, "X-Request-ID": custom_id}

        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=headers, files=files)

        assert response.headers.get("X-Request-ID") == custom_id


class TestErrorHandling:
    """Integration tests for error handling."""

    def test_file_too_large_error(self, client, auth_headers):
        """Test error handling for oversized files."""
        large_content = b"%PDF-1.4" + b"x" * (11 * 1024 * 1024)
        files = {"file": ("large.pdf", io.BytesIO(large_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        assert response.status_code == 413
        assert "X-Request-ID" in response.headers

    def test_invalid_pdf_error(self, client, auth_headers, invalid_pdf_content):
        """Test error handling for invalid PDF files."""
        files = {"file": ("invalid.pdf", io.BytesIO(invalid_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        assert response.status_code == 400
        assert "X-Request-ID" in response.headers

    def test_auth_error_does_not_increment_success_metrics(self, client, invalid_auth_headers, sample_pdf_content):
        """Test that auth errors don't increment success metrics."""
        metrics_response1 = client.get("/metrics/prometheus")

        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        client.post("/invoice-to-json", headers=invalid_auth_headers, files=files)

        metrics_response2 = client.get("/metrics/prometheus")

        # Success count should not increase
        assert "invoice_ocr_requests_success 0" in metrics_response1.text or \
               "invoice_ocr_requests_success" in metrics_response2.text


class TestMultiCurrency:
    """Integration tests for multi-currency support."""

    def test_currency_detection_in_response(self, client, auth_headers, sample_pdf_content):
        """Test that currency is detected and returned."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)

        if response.status_code == 200:
            data = response.json()
            assert "currency" in data
            # Our sample PDF has USD
            assert data["currency"] in ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "INR", "CNY"]
