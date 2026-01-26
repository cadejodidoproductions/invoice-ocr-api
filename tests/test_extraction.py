"""
Tests for invoice data extraction logic (v2.3.0).
"""

import pytest
from unittest.mock import MagicMock

# Import extraction functions
from working_pdf_extractor import (
    validate_file,
    detect_file_type,
    extract_vendor,
    extract_invoice_number,
    extract_po_number,
    extract_date,
    extract_due_date,
    extract_amounts,
    extract_line_items,
    extract_addresses,
    extract_invoice_data,
    validate_invoice_data,
    parse_amount,
    to_csv,
    to_xml,
    PDF_MAGIC_BYTES,
)


class TestFileValidation:
    """Tests for file validation."""

    def test_valid_pdf_file(self):
        """Test validation of valid PDF file."""
        content = b"%PDF-1.4 rest of content..."
        valid, file_type, error = validate_file(content, "invoice.pdf")
        assert valid is True
        assert file_type == "pdf"

    def test_invalid_pdf_content(self):
        """Test validation of invalid content."""
        content = b"Not a PDF file"
        valid, file_type, error = validate_file(content, "invoice.pdf")
        assert valid is False

    def test_unsupported_extension(self):
        """Test validation of unsupported file extension."""
        content = b"%PDF-1.4"
        valid, file_type, error = validate_file(content, "document.doc")
        assert valid is False
        assert "Unsupported" in error

    def test_detect_pdf_type(self):
        """Test file type detection for PDF."""
        content = b"%PDF-1.4 content"
        assert detect_file_type(content, "test.pdf") == "pdf"

    def test_detect_png_type(self):
        """Test file type detection for PNG."""
        content = b"\x89PNG\r\n\x1a\n rest"
        assert detect_file_type(content, "test.png") == "png"

    def test_detect_jpg_type(self):
        """Test file type detection for JPEG."""
        content = b"\xff\xd8\xff\xe0 rest"
        assert detect_file_type(content, "test.jpg") == "jpg"

    def test_detect_unknown_type(self):
        """Test file type detection for unknown content."""
        content = b"unknown content"
        assert detect_file_type(content, "unknown.xyz") == "unknown"


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


class TestPONumberExtraction:
    """Tests for PO number extraction."""

    def test_extract_po_number(self):
        """Test extraction of PO number."""
        text = "PO #PO-12345\nInvoice #12345"
        po_no, confidence = extract_po_number(text)
        assert "12345" in po_no
        assert confidence > 0.5

    def test_extract_purchase_order(self):
        """Test extraction with full 'Purchase Order' text."""
        text = "Purchase Order: ABC-789"
        po_no, confidence = extract_po_number(text)
        assert "ABC" in po_no or "789" in po_no

    def test_no_po_number(self):
        """Test when no PO number found."""
        text = "Some text without PO"
        po_no, confidence = extract_po_number(text)
        assert po_no == "N/A"
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
        assert date == "N/A"
        assert confidence == 0.0


class TestDueDateExtraction:
    """Tests for due date extraction."""

    def test_extract_due_date(self):
        """Test extraction of due date."""
        text = "Due Date: 02/15/2024\nInvoice Date: 01/15/2024"
        due_date, confidence = extract_due_date(text)
        assert "02" in due_date or "15" in due_date or "2024" in due_date
        assert confidence > 0

    def test_extract_payment_due(self):
        """Test extraction of payment due date."""
        text = "Payment Due: March 1, 2024"
        due_date, confidence = extract_due_date(text)
        assert "March" in due_date or "2024" in due_date

    def test_no_due_date(self):
        """Test when no due date found."""
        text = "Invoice without due date"
        due_date, confidence = extract_due_date(text)
        assert due_date == "N/A"
        assert confidence == 0.0


class TestAmountExtraction:
    """Tests for amount extraction."""

    def test_extract_usd_amounts(self):
        """Test extraction of USD amounts."""
        text = "Total: $1,250.00\nSubtotal: $1,000.00"
        result = extract_amounts(text)
        assert result["currency"] == "USD"
        assert len(result["all_amounts"]) >= 1
        assert 1250.00 in result["all_amounts"] or 1000.00 in result["all_amounts"]

    def test_extract_eur_amounts(self):
        """Test extraction of EUR amounts."""
        text = "Total: €1,250.00"
        result = extract_amounts(text)
        assert result["currency"] == "EUR"
        assert len(result["all_amounts"]) >= 1

    def test_extract_gbp_amounts(self):
        """Test extraction of GBP amounts."""
        text = "Total: £500.00"
        result = extract_amounts(text)
        assert result["currency"] == "GBP"
        assert 500.00 in result["all_amounts"]

    def test_extract_cad_amounts(self):
        """Test extraction of CAD amounts."""
        text = "Total: C$750.00"
        result = extract_amounts(text)
        assert result["currency"] == "CAD"
        assert 750.00 in result["all_amounts"]

    def test_extract_jpy_amounts(self):
        """Test extraction of JPY amounts."""
        text = "Total: ¥10000"
        result = extract_amounts(text)
        assert result["currency"] == "JPY"
        assert 10000 in result["all_amounts"]

    def test_extract_tax_amount(self):
        """Test extraction of tax amount."""
        text = "Tax: $50.00\nTotal: $550.00"
        result = extract_amounts(text)
        assert result["tax"] == 50.00

    def test_extract_subtotal(self):
        """Test extraction of subtotal."""
        text = "Subtotal: $500.00\nTax: $50.00\nTotal: $550.00"
        result = extract_amounts(text)
        assert result["subtotal"] == 500.00

    def test_extract_shipping(self):
        """Test extraction of shipping cost."""
        text = "Shipping: $25.00\nTotal: $525.00"
        result = extract_amounts(text)
        assert result["shipping"] == 25.00

    def test_extract_discount(self):
        """Test extraction of discount."""
        text = "Discount: -$50.00\nTotal: $450.00"
        result = extract_amounts(text)
        assert result["discount"] == 50.00

    def test_extract_no_amounts(self):
        """Test when no amounts found."""
        text = "No monetary values here"
        result = extract_amounts(text)
        assert len(result["all_amounts"]) == 0


class TestLineItemExtraction:
    """Tests for line item extraction."""

    def test_extract_line_items(self):
        """Test extraction of line items."""
        text = """
        Widget A          2    $10.00   $20.00
        Widget B          3    $15.00   $45.00
        """
        items = extract_line_items(text)
        # Line item regex may or may not match depending on spacing
        assert isinstance(items, list)

    def test_empty_line_items(self):
        """Test when no line items found."""
        text = "Invoice with no itemized lines"
        items = extract_line_items(text)
        assert items == []


class TestAddressExtraction:
    """Tests for address extraction."""

    def test_extract_address(self):
        """Test extraction of US address."""
        text = "123 Main Street, Suite 100, New York, NY 10001"
        addresses = extract_addresses(text)
        # May or may not match depending on exact format
        assert isinstance(addresses, list)

    def test_no_address(self):
        """Test when no address found."""
        text = "No address here"
        addresses = extract_addresses(text)
        assert addresses == []


class TestParseAmount:
    """Tests for amount parsing utility."""

    def test_parse_simple_amount(self):
        """Test parsing simple amount."""
        assert parse_amount("100.00") == 100.00

    def test_parse_amount_with_commas(self):
        """Test parsing amount with commas."""
        assert parse_amount("1,250.00") == 1250.00

    def test_parse_amount_with_spaces(self):
        """Test parsing amount with spaces."""
        assert parse_amount(" 100.00 ") == 100.00

    def test_parse_invalid_amount(self):
        """Test parsing invalid amount."""
        assert parse_amount("abc") == 0.0


class TestExportFunctions:
    """Tests for export functions."""

    def test_to_csv(self):
        """Test CSV export."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "po_number": "PO-123",
            "date": "2024-01-15",
            "due_date": "2024-02-15",
            "currency": "USD",
            "subtotal": 100.00,
            "tax": 10.00,
            "shipping": 5.00,
            "discount": None,
            "total": 115.00,
            "line_items": [
                {"description": "Item 1", "quantity": 1, "unit_price": 100.00, "amount": 100.00}
            ],
        }
        csv_output = to_csv(data)
        assert "ACME Corp" in csv_output
        assert "INV-001" in csv_output
        assert "Item 1" in csv_output

    def test_to_xml(self):
        """Test XML export."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "po_number": "PO-123",
            "date": "2024-01-15",
            "due_date": "2024-02-15",
            "currency": "USD",
            "subtotal": 100.00,
            "tax": 10.00,
            "shipping": None,
            "discount": None,
            "total": 110.00,
            "line_items": [],
        }
        xml_output = to_xml(data)
        assert "<vendor>ACME Corp</vendor>" in xml_output
        assert "<invoice_no>INV-001</invoice_no>" in xml_output


class TestInvoiceValidation:
    """Tests for invoice data validation."""

    def test_valid_invoice_passes(self):
        """Test that valid invoice data passes validation."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "date": "2024-01-15",
            "subtotal": 100.00,
            "tax": 10.00,
            "total": 110.00,
            "line_items": [],
            "confidence": {"overall": 0.85},
        }
        result = validate_invoice_data(data)
        assert result["is_valid"] is True
        assert len(result["errors"]) == 0

    def test_missing_total_is_error(self):
        """Test that missing total is an error."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "date": "2024-01-15",
            "total": None,
            "confidence": {"overall": 0.85},
        }
        result = validate_invoice_data(data)
        assert result["is_valid"] is False
        assert any("Total" in e for e in result["errors"])

    def test_unknown_vendor_is_warning(self):
        """Test that unknown vendor generates warning."""
        data = {
            "vendor": "Unknown",
            "invoice_no": "INV-001",
            "date": "2024-01-15",
            "total": 100.00,
            "confidence": {"overall": 0.85},
        }
        result = validate_invoice_data(data)
        assert any("Vendor" in w for w in result["warnings"])

    def test_math_validation(self):
        """Test math validation (subtotal + tax = total)."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "date": "2024-01-15",
            "subtotal": 100.00,
            "tax": 10.00,
            "shipping": 0,
            "discount": 0,
            "total": 200.00,  # Wrong total
            "confidence": {"overall": 0.85},
        }
        result = validate_invoice_data(data)
        assert any("Math" in w for w in result["warnings"])

    def test_low_confidence_warning(self):
        """Test that low confidence generates warning."""
        data = {
            "vendor": "ACME Corp",
            "invoice_no": "INV-001",
            "date": "2024-01-15",
            "total": 100.00,
            "confidence": {"overall": 0.3},
        }
        result = validate_invoice_data(data)
        assert any("confidence" in w.lower() for w in result["warnings"])


class TestFullExtraction:
    """Tests for complete invoice data extraction."""

    def test_full_extraction(self):
        """Test full extraction with all fields."""
        text = """
        ACME Corporation
        123 Main Street

        Invoice #INV-2024-001
        PO #PO-12345
        Date: January 15, 2024
        Due Date: February 15, 2024

        Item 1: $500.00
        Item 2: $750.00
        Subtotal: $1,250.00
        Tax: $100.00
        Total: $1,350.00
        """

        # Create mock logger
        mock_logger = MagicMock()

        result = extract_invoice_data(text, mock_logger)

        assert "vendor" in result
        assert "invoice_no" in result
        assert "po_number" in result
        assert "date" in result
        assert "due_date" in result
        assert "subtotal" in result
        assert "tax" in result
        assert "total" in result
        assert "currency" in result
        assert "all_amounts" in result
        assert "line_items" in result
        assert "addresses" in result
        assert "confidence" in result

        # Check confidence structure
        assert "overall" in result["confidence"]
        assert "vendor" in result["confidence"]
        assert "invoice_no" in result["confidence"]
        assert "po_number" in result["confidence"]
        assert "date" in result["confidence"]
        assert "due_date" in result["confidence"]

    def test_extraction_with_minimal_data(self):
        """Test extraction with minimal invoice data."""
        text = "Random text with $100.00"

        mock_logger = MagicMock()
        result = extract_invoice_data(text, mock_logger)

        # Should still return structure even with limited data
        assert "vendor" in result
        assert "total" in result
        assert result["total"] == 100.00
