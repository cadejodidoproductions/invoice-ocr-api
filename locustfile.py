"""
Load testing configuration for Invoice OCR API using Locust.

Run with: locust -f locustfile.py --host=http://localhost:8000

For headless mode:
locust -f locustfile.py --host=http://localhost:8000 --headless -u 10 -r 2 -t 60s
"""

import io
import os
from locust import HttpUser, task, between


# Test API key - ensure this matches your server configuration
API_KEY = os.getenv("INVOICE_OCR_API_KEY", "test-api-key-12345")

# Sample PDF content for testing
SAMPLE_PDF = b"""%PDF-1.4
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


class InvoiceOCRUser(HttpUser):
    """Simulated user for load testing Invoice OCR API."""

    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks

    def on_start(self):
        """Set up authentication headers."""
        self.headers = {"X-API-Key": API_KEY}

    @task(10)
    def process_invoice(self):
        """Test main invoice processing endpoint."""
        files = {"file": ("invoice.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")}
        self.client.post("/invoice-to-json", headers=self.headers, files=files)

    @task(3)
    def process_invoice_csv(self):
        """Test invoice processing with CSV export."""
        files = {"file": ("invoice.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")}
        self.client.post("/invoice-to-json?export=csv", headers=self.headers, files=files)

    @task(3)
    def process_invoice_xml(self):
        """Test invoice processing with XML export."""
        files = {"file": ("invoice.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")}
        self.client.post("/invoice-to-json?export=xml", headers=self.headers, files=files)

    @task(2)
    def batch_process(self):
        """Test batch processing endpoint."""
        files = [
            ("files", ("invoice1.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")),
            ("files", ("invoice2.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")),
            ("files", ("invoice3.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")),
        ]
        self.client.post("/invoice-to-json/batch", headers=self.headers, files=files)

    @task(5)
    def health_check(self):
        """Test health endpoint."""
        self.client.get("/health")

    @task(2)
    def metrics(self):
        """Test metrics endpoint."""
        self.client.get("/metrics", headers=self.headers)

    @task(2)
    def prometheus_metrics(self):
        """Test Prometheus metrics endpoint."""
        self.client.get("/metrics/prometheus")

    @task(1)
    def root(self):
        """Test root endpoint."""
        self.client.get("/")


class HighVolumeUser(HttpUser):
    """High-volume user for stress testing."""

    wait_time = between(0.1, 0.5)  # Minimal wait time

    def on_start(self):
        """Set up authentication headers."""
        self.headers = {"X-API-Key": API_KEY}

    @task
    def rapid_invoice_processing(self):
        """Rapid-fire invoice processing for stress testing."""
        files = {"file": ("invoice.pdf", io.BytesIO(SAMPLE_PDF), "application/pdf")}
        self.client.post("/invoice-to-json", headers=self.headers, files=files)
