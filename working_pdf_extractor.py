from fastapi import FastAPI, File, UploadFile
import re
import io

app = FastAPI()

@app.post("/invoice-to-json")
async def process_invoice(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename.lower()
    
    if filename.endswith('.pdf'):
        try:
            import pypdf
            # Extract text from PDF
            pdf_file = io.BytesIO(content)
            pdf_reader = pypdf.PdfReader(pdf_file)
            full_text = ""
            for page in pdf_reader.pages:
                full_text += page.extract_text()
            
            # Debug - see what we're working with
            print("EXTRACTED TEXT:", full_text[:500])
            
            # Extract vendor (first line)
            lines = full_text.strip().split('\n')
            vendor = lines[0] if lines else "Unknown"
            
            # Extract invoice number
            invoice_match = re.search(r'Invoice\s*#?\s*(\d+)', full_text)
            invoice_no = invoice_match.group(1) if invoice_match else "N/A"
            
            # Extract date
            date_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}', full_text)
            date = date_match.group(0) if date_match else "No date found"
            
            # Extract ALL dollar amounts
            dollar_amounts = re.findall(r'\$\s*([\d,]+(?:\.\d{2})?)', full_text)
            print("FOUND DOLLAR AMOUNTS:", dollar_amounts)
            
            # Convert to floats and find largest (usually total)
            amounts = []
            for amt in dollar_amounts:
                try:
                    val = float(amt.replace(',', ''))
                    amounts.append(val)
                except:
                    pass
            
            # Get the largest amount as total
            total = max(amounts) if amounts else 0.0
            
            return {
                "vendor": vendor,
                "invoice_no": invoice_no,
                "date": date,
                "total": total,
                "all_amounts": amounts,  # Show all amounts found
                "file_type": "PDF",
                "debug_text": full_text[:200]
            }
            
        except Exception as e:
            return {"error": str(e), "file_type": "PDF"}
    
    else:
        # Images - just return dummy for now
        return {
            "vendor": "Use PDF for testing",
            "invoice_no": "IMG-001",
            "date": "2024-07-25",
            "total": 0,
            "file_type": "Image"
        }

@app.get("/")
def root():
    return {"status": "Real PDF Data Extraction"}