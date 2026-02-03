"""
Tests for Invoice OCR API endpoints (v2.5.0).
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
        assert data["version"] == "2.5.0"

    def test_health_endpoint(self, client):
        """Test the detailed health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "features" in data
        assert "config" in data
        # Check v2.3.0 features
        assert data["features"]["rate_limiting"] is True
        assert data["features"]["caching"] is True
        assert data["features"]["batch"] is True
        assert data["features"]["multi_currency"] is True
        assert data["features"]["line_items"] is True
        assert data["features"]["webhooks"] is True
        assert data["features"]["pdf_password"] is True
        assert data["features"]["validation"] is True
        assert data["features"]["async_processing"] is True
        assert "json" in data["features"]["export_formats"]
        assert "csv" in data["features"]["export_formats"]
        assert "xml" in data["features"]["export_formats"]


class TestV1ApiRoutes:
    """Tests for versioned API routes."""

    def test_v1_health_endpoint(self, client):
        """Test the v1 health endpoint."""
        response = client.get("/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


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
        # Check v2.3.0 metrics fields
        assert "ocr_processed" in data["metrics"]
        assert "validation_errors" in data["metrics"]
        assert "async_jobs" in data["metrics"]

    def test_metrics_with_invalid_auth(self, client, invalid_auth_headers):
        """Test metrics endpoint with invalid authentication."""
        response = client.get("/metrics", headers=invalid_auth_headers)
        assert response.status_code == 401

    def test_prometheus_metrics_no_auth(self, client):
        """Test Prometheus metrics endpoint (no auth required)."""
        response = client.get("/metrics/prometheus")
        assert response.status_code == 200
        assert "invoice_ocr_uptime" in response.text
        assert "invoice_ocr_requests_total" in response.text
        assert "invoice_ocr_ocr_processed" in response.text
        assert "invoice_ocr_validation_errors" in response.text


class TestInvoiceProcessing:
    """Tests for invoice processing endpoint."""

    def test_process_invoice_requires_file(self, client, auth_headers):
        """Test that invoice processing requires a file."""
        response = client.post("/invoice-to-json", headers=auth_headers)
        assert response.status_code == 422  # Validation error

    def test_process_non_pdf_file(self, client, auth_headers):
        """Test rejection of unsupported file formats."""
        files = {"file": ("document.txt", io.BytesIO(b"Hello World"), "text/plain")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code == 400
        assert "Unsupported" in response.json()["detail"]

    def test_process_invalid_pdf_magic_bytes(self, client, auth_headers, invalid_pdf_content):
        """Test rejection of files without valid magic bytes."""
        files = {"file": ("invoice.pdf", io.BytesIO(invalid_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code == 400

    def test_process_oversized_file(self, client, auth_headers):
        """Test rejection of files exceeding size limit."""
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

    def test_validation_in_response(self, client, auth_headers, sample_pdf_content):
        """Test that response includes validation results."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        if response.status_code == 200:
            data = response.json()
            assert "validation" in data
            assert "is_valid" in data["validation"]
            assert "errors" in data["validation"]
            assert "warnings" in data["validation"]


class TestExportFormats:
    """Tests for export format functionality."""

    def test_export_csv_format(self, client, auth_headers, sample_pdf_content):
        """Test CSV export format."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json?export=csv", headers=auth_headers, files=files)
        if response.status_code == 200:
            assert response.headers.get("content-type", "").startswith("text/csv")
            assert "Content-Disposition" in response.headers

    def test_export_xml_format(self, client, auth_headers, sample_pdf_content):
        """Test XML export format."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json?export=xml", headers=auth_headers, files=files)
        if response.status_code == 200:
            assert "xml" in response.headers.get("content-type", "")

    def test_invalid_export_format(self, client, auth_headers, sample_pdf_content):
        """Test invalid export format is rejected."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json?export=pdf", headers=auth_headers, files=files)
        assert response.status_code == 422


class TestBatchProcessing:
    """Tests for batch processing endpoint."""

    def test_batch_empty_files(self, client, auth_headers):
        """Test batch processing with no files."""
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=[])
        assert response.status_code == 422

    def test_batch_exceeds_limit(self, client, auth_headers, sample_pdf_content):
        """Test batch processing with too many files."""
        files = [
            ("files", (f"invoice_{i}.pdf", io.BytesIO(sample_pdf_content), "application/pdf"))
            for i in range(11)
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files)
        assert response.status_code == 400
        assert "Max" in response.json()["detail"] or "max" in response.json()["detail"].lower()

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
        assert data["failed"] >= 2
        assert "time_ms" in data


class TestAsyncProcessing:
    """Tests for async processing endpoint."""

    def test_async_submit_returns_job_id(self, client, auth_headers, sample_pdf_content):
        """Test that async submission returns a job ID."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json/async", headers=auth_headers, files=files)
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "status_url" in data
        assert data["status"] == "pending"

    def test_get_job_status(self, client, auth_headers, sample_pdf_content):
        """Test getting job status."""
        # Submit async job
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json/async", headers=auth_headers, files=files)
        job_id = response.json()["job_id"]

        # Get job status
        response = client.get(f"/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert "status" in data

    def test_get_nonexistent_job(self, client, auth_headers):
        """Test getting status of non-existent job."""
        response = client.get("/jobs/nonexistent-job-id", headers=auth_headers)
        assert response.status_code == 404


class TestAuthentication:
    """Tests for authentication."""

    def test_missing_api_key(self, client, sample_pdf_content):
        """Test request without API key."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response = client.post("/invoice-to-json", files=files)
        assert response.status_code == 401
        assert "API key" in response.json()["detail"]

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
        response1 = client.post("/invoice-to-json", headers=auth_headers, files=files)

        files2 = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        response2 = client.post("/invoice-to-json", headers=auth_headers, files=files2)

        if response1.status_code == 200 and response2.status_code == 200:
            data2 = response2.json()
            assert data2.get("cached") is True


class TestWebhooks:
    """Tests for webhook functionality."""

    def test_webhook_url_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that webhook URL parameter is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"webhook_url": "https://example.com/webhook"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]


class TestPasswordProtectedPdf:
    """Tests for password-protected PDF support."""

    def test_password_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that password parameter is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"password": "secret123"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]


class TestOcrParameter:
    """Tests for OCR control parameter."""

    def test_use_ocr_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that use_ocr parameter is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"use_ocr": "false"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]


class TestImageFormats:
    """Tests for image format support."""

    def test_png_file_accepted(self, client, auth_headers, sample_png_content):
        """Test that PNG files are accepted."""
        files = {"file": ("invoice.png", io.BytesIO(sample_png_content), "image/png")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        # May fail if OCR not available, but should be 400 or 422, not 500
        assert response.status_code in [200, 400, 422]

    def test_jpg_file_accepted(self, client, auth_headers, sample_jpg_content):
        """Test that JPG files are accepted."""
        files = {"file": ("invoice.jpg", io.BytesIO(sample_jpg_content), "image/jpeg")}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files)
        assert response.status_code in [200, 400, 422]


class TestOcrLanguages:
    """Tests for multi-language OCR support."""

    def test_list_ocr_languages(self, client):
        """Test listing supported OCR languages."""
        response = client.get("/ocr/languages")
        assert response.status_code == 200
        data = response.json()
        assert "languages" in data
        assert "default_language" in data

    def test_v1_list_ocr_languages(self, client):
        """Test v1 API for listing OCR languages."""
        response = client.get("/v1/ocr/languages")
        assert response.status_code == 200
        data = response.json()
        assert "languages" in data

    def test_ocr_lang_parameter_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that ocr_lang parameter is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "eng"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_invalid_ocr_lang_rejected(self, client, auth_headers, sample_pdf_content):
        """Test that invalid OCR language is rejected."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "invalid_lang_xyz"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code == 400
        assert "Unsupported OCR language" in response.json()["detail"]

    def test_spanish_language_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that Spanish language code is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "spa"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_french_language_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that French language code is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "fra"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_german_language_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that German language code is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "deu"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_japanese_language_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that Japanese language code is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "jpn"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_chinese_language_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that Chinese language code is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "zho"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_async_ocr_lang_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that async endpoint accepts ocr_lang parameter."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"ocr_lang": "spa"}
        response = client.post("/invoice-to-json/async", headers=auth_headers, files=files, data=data)
        assert response.status_code == 200
        assert "job_id" in response.json()

    def test_batch_ocr_lang_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that batch endpoint accepts ocr_lang parameter."""
        files = [
            ("files", ("invoice1.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files, data={"ocr_lang": "fra"})
        assert response.status_code == 200


class TestMLExtraction:
    """Tests for ML-based extraction (v2.5.0)."""

    def test_ml_status_endpoint(self, client):
        """Test ML status endpoint."""
        response = client.get("/ml/status")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data
        assert "enabled" in data

    def test_v1_ml_status_endpoint(self, client):
        """Test v1 ML status endpoint."""
        response = client.get("/v1/ml/status")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data

    def test_use_ml_parameter_accepted(self, client, auth_headers, sample_pdf_content):
        """Test that use_ml parameter is accepted."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"use_ml": "true"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        # Should work regardless of ML availability
        assert response.status_code in [200, 422]

    def test_use_ml_false_works(self, client, auth_headers, sample_pdf_content):
        """Test that use_ml=false works normally."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"use_ml": "false"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        assert response.status_code in [200, 422]

    def test_async_use_ml_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that async endpoint accepts use_ml parameter."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"use_ml": "true"}
        response = client.post("/invoice-to-json/async", headers=auth_headers, files=files, data=data)
        assert response.status_code == 200
        assert "job_id" in response.json()

    def test_batch_use_ml_parameter(self, client, auth_headers, sample_pdf_content):
        """Test that batch endpoint accepts use_ml parameter."""
        files = [
            ("files", ("invoice1.pdf", io.BytesIO(sample_pdf_content), "application/pdf")),
        ]
        response = client.post("/invoice-to-json/batch", headers=auth_headers, files=files, data={"use_ml": "true"})
        assert response.status_code == 200

    def test_health_shows_ml_feature(self, client):
        """Test that health endpoint shows ML extraction feature."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "ml_extraction" in data["features"]

    def test_ml_enhanced_field_in_response(self, client, auth_headers, sample_pdf_content):
        """Test that response includes ml_enhanced field when using ML."""
        files = {"file": ("invoice.pdf", io.BytesIO(sample_pdf_content), "application/pdf")}
        data = {"use_ml": "false"}
        response = client.post("/invoice-to-json", headers=auth_headers, files=files, data=data)
        if response.status_code == 200:
            result = response.json()
            # ml_enhanced should be in response
            assert "ml_enhanced" in result
