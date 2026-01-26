"""
Tests for invoice data extraction logic.
"""

import pytest
from unittest.mock import MagicMock

# Import extraction functions
from working_pdf_extractor import (
    validate_pdf_magic_bytes,
    extract_vendor,
    extract_invoice_number,
    extract_date,
    extract_amounts,
    extract_invoice_data,
    calculate_confidence,
    PDF_MAGIC_BYTES,
)


class TestPdfValidation:
    """Tests for PDF magic bytes validation."""

    def test_valid_pdf_magic_bytes(self):
        """Test validation of valid PDF magic bytes."""
        content = b"%PDF-1.4 rest of content..."
        assert validate_pdf_magic_bytes(content) is True

    def test_invalid_pdf_magic_bytes(self):
        """Test validation of invalid magic bytes."""
        content = b"Not a PDF file"
        assert validate_pdf_magic_bytes(content) is False

    def test_empty_content(self):
        """Test validation of empty content."""
        assert validate_pdf_magic_bytes(b"") is False

    def test_partial_magic_bytes(self):
        """Test validation of partial magic bytes."""
        assert validate_pdf_magic_bytes(b"%PD") is False
        assert validate_pdf_magic_bytes(b"%PDF") is True


class TestVendorExtraction:
    """Tests for vendor name extraction."""

    def test_extract_vendor_from_pattern(self):
        """Test extraction using vendor patterns."""
        text = "From: ACME Corporation\nInvoice #12345"
        vendor, confidence = extract_vendor(text)
        assert "ACME" in vendor
        assert confidence > 0.5

    def test_extract_vendor_from_first_line(self):
        """Test extraction from first line fallback."""
        text = "ABC Company Inc.\n123 Main Street\nInvoice #12345"
        vendor, confidence = extract_vendor(text)
        assert "ABC" in vendor

    def test_extract_vendor_unknown(self):
        """Test extraction when no vendor found."""
        text = ""
        vendor, confidence = extract_vendor(text)
        assert vendor == "Unknown"
        assert confidence == 0.0

    def test_skip_date_like_lines(self):
        """Test that date-like lines are skipped."""
        text = "01/15/2024\nABC Company\nInvoice"
        vendor, confidence = extract_vendor(text)
        assert "ABC" in vendor or "Company" in vendor


class TestInvoiceNumberExtraction:
    """Tests for invoice number extraction."""

    def test_extract_invoice_with_hash(self):
        """Test extraction of invoice number with hash."""
        text = "Invoice #12345\nDate: Jan 15, 2024"
        invoice_no, confidence = extract_invoice_number(text)
        assert invoice_no == "12345"
        assert confidence >= 0.65

    def test_extract_invoice_with_colon(self):
        """Test extraction of invoice number with colon."""
        text = "Invoice: INV-2024-001"
        invoice_no, confidence = extract_invoice_number(text)
        assert "INV" in invoice_no or "2024" in invoice_no

    def test_extract_bill_number(self):
        """Test extraction of bill number."""
        text = "Bill #98765"
        invoice_no, confidence = extract_invoice_number(text)
        assert invoice_no == "98765"

    def test_extract_order_number(self):
        """Test extraction of order number."""
        text = "Order #ORD-123"
        invoice_no, confidence = extract_invoice_number(text)
        assert "ORD" in invoice_no or "123" in invoice_no

    def test_no_invoice_number(self):
        """Test when no invoice number found."""
        text = "Some random text without invoice number"
        invoice_no, confidence = extract_invoice_number(text)
        assert invoice_no == "N/A"
        assert confidence == 0.0


class TestDateExtraction:
    """Tests for date extraction."""

    def test_extract_month_name_date(self):
        """Test extraction of date with month name."""
        text = "Date: January 15, 2024"
        date, confidence = extract_date(text)
        assert "January" in date or "Jan" in date
        assert "15" in date
        assert "2024" in date
        assert confidence >= 0.9

    def test_extract_abbreviated_month(self):
        """Test extraction of date with abbreviated month."""
        text = "Date: Jan 15, 2024"
        date, confidence = extract_date(text)
        assert "Jan" in date
        assert confidence >= 0.9

    def test_extract_iso_date(self):
        """Test extraction of ISO format date."""
        text = "Date: 2024-01-15"
        date, confidence = extract_date(text)
        assert date == "2024-01-15"
        assert confidence >= 0.85

    def test_extract_us_date_format(self):
        """Test extraction of US date format."""
        text = "Date: 01/15/2024"
        date, confidence = extract_date(text)
        assert "01" in date and "15" in date and "2024" in date

    def test_extract_european_date_format(self):
        """Test extraction of European date format."""
        text = "Date: 15.01.2024"
        date, confidence = extract_date(text)
        assert "15" in date and "01" in date and "2024" in date

    def test_no_date_found(self):
        """Test when no date found."""
        text = "Some text without a date"
        date, confidence = extract_date(text)
        assert date == "No date found"
        assert confidence == 0.0


class TestAmountExtraction:
    """Tests for amount extraction."""

    def test_extract_usd_amounts(self):
        """Test extraction of USD amounts."""
        text = "Total: $1,250.00\nSubtotal: $1,000.00"
        amounts, currency, confidence = extract_amounts(text)
        assert len(amounts) == 2
        assert currency == "USD"
        assert any(a["amount"] == 1250.00 for a in amounts)
        assert any(a["amount"] == 1000.00 for a in amounts)

    def test_extract_eur_amounts(self):
        """Test extraction of EUR amounts."""
        text = "Total: €1.250,00"
        amounts, currency, confidence = extract_amounts(text)
        assert currency == "EUR"
        assert len(amounts) >= 1

    def test_extract_gbp_amounts(self):
        """Test extraction of GBP amounts."""
        text = "Total: £500.00"
        amounts, currency, confidence = extract_amounts(text)
        assert currency == "GBP"
        assert len(amounts) >= 1
        assert any(a["amount"] == 500.00 for a in amounts)

    def test_extract_no_amounts(self):
        """Test when no amounts found."""
        text = "No monetary values here"
        amounts, currency, confidence = extract_amounts(text)
        assert len(amounts) == 0
        assert confidence == 0.0


class TestConfidenceCalculation:
    """Tests for confidence score calculation."""

    def test_zero_confidence_for_na(self):
        """Test that N/A values get zero confidence."""
        assert calculate_confidence("N/A", 0.8, "some text") == 0.0
        assert calculate_confidence("Unknown", 0.8, "some text") == 0.0
        assert calculate_confidence("No date found", 0.8, "some text") == 0.0

    def test_base_confidence_preserved(self):
        """Test that base confidence is used."""
        result = calculate_confidence("ACME Corp", 0.85, "Invoice from ACME Corp")
        assert result >= 0.85

    def test_confidence_boost_for_multiple_occurrences(self):
        """Test confidence boost for repeated values."""
        text = "ACME Corp invoice. ACME Corp address."
        result = calculate_confidence("ACME Corp", 0.80, text)
        assert result > 0.80


class TestFullExtraction:
    """Tests for complete invoice data extraction."""

    def test_full_extraction(self):
        """Test full extraction with all fields."""
        text = """
        ACME Corporation
        123 Main Street

        Invoice #INV-2024-001
        Date: January 15, 2024

        Item 1: $500.00
        Item 2: $750.00
        Total: $1,250.00
        """

        # Create mock logger
        mock_logger = MagicMock()

        result = extract_invoice_data(text, mock_logger)

        assert "vendor" in result
        assert "invoice_no" in result
        assert "date" in result
        assert "total" in result
        assert "currency" in result
        assert "all_amounts" in result
        assert "confidence" in result

        # Check confidence structure
        assert "overall" in result["confidence"]
        assert "vendor" in result["confidence"]
        assert "invoice_no" in result["confidence"]
        assert "date" in result["confidence"]
        assert "amounts" in result["confidence"]

    def test_extraction_with_minimal_data(self):
        """Test extraction with minimal invoice data."""
        text = "Random text with $100.00"

        mock_logger = MagicMock()
        result = extract_invoice_data(text, mock_logger)

        # Should still return structure even with limited data
        assert "vendor" in result
        assert "total" in result
        assert result["total"] == 100.00
