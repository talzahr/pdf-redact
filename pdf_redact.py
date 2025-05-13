__version__ = "0.1"
__revision__ = "4" 

import fitz  # PyMuPDF
import re
import os
from PIL import Image
import pytesseract # For OCR
import argparse 
import yaml 

# --- Configuration ---
# If Tesseract is not in your PATH, set its location here
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Functions

def is_likely_scanned_pdf(page, text_threshold=100):
    text = page.get_text("text")
    return len(text.strip()) < text_threshold

def ocr_page_to_get_text_and_boxes(page, lang='eng'):
    ocr_instances = []
    try:
        pix = page.get_pixmap(dpi=300) 
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr_data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        
        n_boxes = len(ocr_data['level'])
        page_rect = page.rect 
        img_width = pix.width
        img_height = pix.height

        for i in range(n_boxes):
            if int(ocr_data['conf'][i]) > 50: # Confidence threshold
                text = ocr_data['text'][i].strip()
                if text: 
                    (x, y, w, h) = (ocr_data['left'][i], ocr_data['top'][i], ocr_data['width'][i], ocr_data['height'][i])
                    x0 = (x / img_width) * page_rect.width
                    y0 = (y / img_height) * page_rect.height
                    x1 = ((x + w) / img_width) * page_rect.width
                    y1 = ((y + h) / img_height) * page_rect.height
                    ocr_instances.append((text, fitz.Rect(x0, y0, x1, y1)))
        return ocr_instances
    except Exception as e:
        print(f"Error during OCR on page {page.number + 1}: {e}")
        return []

def find_and_redact_text_on_page(page, patterns_to_redact):
    redacted_count = 0
    redaction_fill_color = (0, 0, 0) # Black

    # 1. Try direct text extraction using PyMuPDF's search_for
    for pattern_str in patterns_to_redact:
        try:
            text_instances = page.search_for(pattern_str, flags=8) # 8 is fitz.TEXT_SEARCH_REGEX
            for inst_rect in text_instances:
                is_covered = any(annot.rect.contains(inst_rect) for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]))
                if not is_covered:
                    page.add_redact_annot(inst_rect, fill=redaction_fill_color)
                    redacted_count += 1
        
        except Exception as e_search:
            # This might happen if MuPDF's regex engine can't handle the pattern.
            
            # Fallback: Iterate through words and use Python's `re`
            try:
                words = page.get_text("words")  # list of [x0, y0, x1, y1, "word", ...]
                compiled_pattern = re.compile(pattern_str) # for efficiency
                
                for word_info in words:
                    word_text = word_info[4]
                    word_rect = fitz.Rect(word_info[0:4])
                    
                    for match in compiled_pattern.finditer(word_text):
                        is_covered = any(annot.rect.contains(word_rect) for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]))
                        if not is_covered:
                            page.add_redact_annot(word_rect, fill=redaction_fill_color)
                            redacted_count += 1
                            # print(f"DEBUG: Redacting (word-by-word re.search): '{word_text}' (match: '{match.group(0)}') at {word_rect} for pattern '{pattern_str}'")
                            break 
            except Exception as e_re:
                print(f"Error during word-by-word regex for pattern '{pattern_str}': {e_re}")


    # 2. If page seems scanned or few redactions were made by direct search, try OCR
    run_ocr = is_likely_scanned_pdf(page)
    if not run_ocr and redacted_count == 0 : 
        text_content = page.get_text("text")
        if 0 < len(text_content.strip()) < 500: # Arbitrary threshold for sparse text
             run_ocr = True

    if run_ocr:
        print(f"Page {page.number + 1}: Attempting OCR.")
        ocr_instances = ocr_page_to_get_text_and_boxes(page)
        
        for pattern_str in patterns_to_redact:
            try:
                compiled_pattern = re.compile(pattern_str, re.IGNORECASE) # OCR text might have case variations
                for text, ocr_rect in ocr_instances:
                    if compiled_pattern.search(text): # Search if pattern exists in the OCR'd text block
                        is_covered = any(annot.rect.intersects(ocr_rect) for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]))
                        if not is_covered:
                            page.add_redact_annot(ocr_rect, fill=redaction_fill_color)
                            redacted_count += 1
                            # print(f"DEBUG: Redacting (OCR): '{text}' at {ocr_rect} for pattern '{pattern_str}'")
            except Exception as e_ocr_re:
                print(f"Error during OCR regex for pattern '{pattern_str}': {e_ocr_re}")
    
    # Apply actual redactions for the page if any redaction annotations were added
    if list(page.annots(types=[fitz.PDF_ANNOT_REDACT])): 
         page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE) # ..._REMOVE replaced with ..._NONE to prevent blanking scanned PDFs
    return redacted_count

def load_patterns_from_yaml(yaml_file_path="patterns.yaml"):
    """Loads redaction patterns from a YAML file."""
    default_patterns = [
        r"\b123456789\b", # Fallback specific example
        r"\b\d{8,17}\b",   # Fallback generic
    ]
    try:
        if not os.path.exists(yaml_file_path):
            print(f"Warning: Patterns file '{yaml_file_path}' not found. Using default/hardcoded patterns.")
            return default_patterns
        
        with open(yaml_file_path, 'r') as f:
            data = yaml.safe_load(f)
            if data and 'patterns' in data and isinstance(data['patterns'], list):
                loaded_patterns = [str(p) for p in data['patterns']] # Ensure they are strings
                if not loaded_patterns:
                    print(f"Warning: No patterns found in '{yaml_file_path}'. Using default/hardcoded patterns.")
                    return default_patterns
                print(f"Successfully loaded {len(loaded_patterns)} patterns from '{yaml_file_path}'.")
                return loaded_patterns
            else:
                print(f"Warning: Invalid format in '{yaml_file_path}'. Expected a list under 'patterns' key. Using default patterns.")
                return default_patterns
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file '{yaml_file_path}': {e}. Using default patterns.")
        return default_patterns
    except Exception as e:
        print(f"An unexpected error occurred while loading patterns from '{yaml_file_path}': {e}. Using default patterns.")
        return default_patterns

# Main logic
def redact_account_numbers_from_pdf(input_pdf_path, output_pdf_path, patterns_file="patterns.yaml", additional_patterns=None):
    if not os.path.exists(input_pdf_path):
        print(f"Error: Input PDF not found at {input_pdf_path}")
        return False

    effective_patterns = load_patterns_from_yaml(patterns_file)
    if additional_patterns:
        if isinstance(additional_patterns, list):
            effective_patterns.extend(additional_patterns)
        else:
            print("Warning: additional_patterns should be a list of regex strings.")
    
    if not effective_patterns:
        print("Error: No redaction patterns available. Aborting.")
        return False

    print(f"Using {len(effective_patterns)} redaction patterns for processing.")
    # print(f"Using redaction patterns: {effective_patterns}") 

    try:
        doc = fitz.open(input_pdf_path)
        if doc.is_encrypted:
            if not doc.authenticate(""): # Try empty password
                print(f"Error: PDF '{input_pdf_path}' is encrypted and requires a password.")
                doc.close()
                return False
            print(f"Successfully decrypted PDF '{input_pdf_path}'.")

        total_redactions = 0
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            print(f"Processing Page {page_num + 1}/{len(doc)}...")
            
            redactions_on_page = find_and_redact_text_on_page(page, effective_patterns)
            total_redactions += redactions_on_page
            if redactions_on_page > 0:
                print(f"Redacted {redactions_on_page} instances on page {page_num + 1}.")

        if total_redactions > 0:
            doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
            print(f"\nRedacted PDF saved to: {output_pdf_path}")
            print(f"Total redactions made: {total_redactions}")
        else:
            print("\nNo matching items found to redact based on the provided patterns.")
            if input_pdf_path != output_pdf_path :
                 doc.save(output_pdf_path, garbage=1, deflate=False)
                 print(f"No redactions made. A copy of the original saved to {output_pdf_path}")
            else:
                 print(f"No redactions made. Output path is same as input, so no new file saved.")

        doc.close()
        return True

    except Exception as e:
        print(f"An error occurred during PDF processing: {e}")
        import traceback
        traceback.print_exc()
        return False

# Create a dummy example PDF
def create_dummy_pdf(filename="dummy_statement.pdf", include_image_page=True):
    doc = fitz.open()
    page = doc.new_page()
    
    page.insert_text(fitz.Point(50, 50), "Bank Statement Confidential")
    page.insert_text(fitz.Point(50, 100), "Customer Name: John Doe")
    page.insert_text(fitz.Point(50, 120), "Account Number: 123456789 Details")
    page.insert_text(fitz.Point(50, 140), "Main account 9876543210987654.")
    page.insert_text(fitz.Point(50, 160), "Transaction for account 123-456-7890.")
    page.insert_text(fitz.Point(50, 180), "Account # 112233445566")
    page.insert_text(fitz.Point(50, 200), "Not an account: 12345 (short)")
    page.insert_text(fitz.Point(50, 220), "Reference AB12345678CD, value 500.00")
    page.insert_text(fitz.Point(50, 240), "My Number is: 000-123456-00")

    if include_image_page:
        try:
            img_text_page = doc.new_page()
            img_text_page.insert_text(fitz.Point(50,50), "This is an image page simulation.")
            img_text_page.insert_text(fitz.Point(50,70), "Image Account: 123456789 scanned")
            img_text_page.insert_text(fitz.Point(50,90), "Another number 555666777888 on image")
            pix = img_text_page.get_pixmap(dpi=150)
            doc.delete_page(img_text_page.number)
            
            page_for_image = doc.new_page(width=pix.width, height=pix.height)
            page_for_image.insert_image(page_for_image.rect, pixmap=pix)
        except Exception as e:
            print(f"Warning: Could not create image part for dummy PDF: {e}")

    doc.save(filename, garbage=4, deflate=True)
    doc.close()
    print(f"Dummy PDF created: {filename}")

# Main execution 
if __name__ == "__main__":
    print(f"PDF Redactor v{__version__} (rev {__revision__})")

    parser = argparse.ArgumentParser(description=f"Redacts information from PDF files based on YAML patterns. Version {__version__}")
    parser.add_argument("input_pdf", help="Path to the input PDF file.")
    parser.add_argument("-o", "--output_pdf", help="Path for the redacted output PDF file. "
                                                 "Defaults to '<input_pdf_name>_redacted.pdf'.")
    parser.add_argument("-p", "--patterns", default="patterns.yaml",
                        help="Path to the YAML file containing redaction patterns. Defaults to 'patterns.yaml'.")
    parser.add_argument("--create-dummy", metavar="FILENAME", nargs='?', const="dummy_statement.pdf",
                        help="Create a dummy PDF for testing (e.g., 'dummy_statement.pdf') and exit. "
                             "Optionally specify a filename for the dummy PDF.")


    args = parser.parse_args()

    if args.create_dummy:
        dummy_filename = args.create_dummy
        create_dummy_pdf(dummy_filename)
        print(f"Dummy PDF '{dummy_filename}' created. Run the script again with it as input to test redaction.")
        exit(0)

    input_file = args.input_pdf
    patterns_file_path = args.patterns

    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' not found.")
        if input_file == "test_statement.pdf" and not os.path.exists("test_statement.pdf"): # Common placeholder
            print("Hint: You can create a dummy PDF with '--create-dummy' option.")
        exit(1)

    if args.output_pdf:
        output_file = args.output_pdf
    else:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_redacted{ext}"

    print(f"Input PDF: {input_file}")
    print(f"Output PDF: {output_file}")
    print(f"Patterns File: {patterns_file_path}")


    success = redact_account_numbers_from_pdf(input_file, output_file, 
                                              patterns_file=patterns_file_path, 
                                              additional_patterns=None)

    if success:
        print("\nProcessing complete.")
    else:
        print("\nProcessing encountered errors or no patterns were available.")

    print("\n--- Important Considerations ---")
    print("1. Regex Quality in YAML: Ensure patterns in YAML are correct and test them thoroughly.")
    print("   Remember to escape backslashes for YAML (e.g., '\\b' for regex `\\b`).")
    print("2. Scanned PDFs & OCR: OCR accuracy is not 100%. Manual review is recommended.")
    print("3. Tesseract Path: Configure `pytesseract.pytesseract.tesseract_cmd` if Tesseract isn't in PATH.")
