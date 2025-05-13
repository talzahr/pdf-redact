import fitz  # PyMuPDF
import re
import os
from PIL import Image
import pytesseract # For OCR
import argparse # For command-line arguments

# --- Configuration ---
# (Same as before)
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


# --- Helper Functions ---

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
            if int(ocr_data['conf'][i]) > 50: 
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

    # 1. Try direct text extraction
    for pattern_str in patterns_to_redact:
        try:
            # PyMuPDF's search_for regex capabilities are more limited than Python's 're'
            # For complex patterns, ensure they are compatible or use the word-by-word approach below.
            # The TEXT_SEARCH_REGEX flag might depend on PyMuPDF version (e.g., 1.18.0+)
            # If it causes issues, remove it or check your PyMuPDF version.
            # For most versions, just passing a regex string enables regex search.
            text_instances = page.search_for(pattern_str) # Simpler call, relies on needle being a regex
                                                          # Add flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE if needed and compatible
            for inst in text_instances:
                # Check if the found instance is already part of an existing redaction to avoid overlap issues
                # This is a basic check; more sophisticated overlap detection might be needed for complex cases.
                is_already_covered = False
                for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]):
                    if annot.rect.intersects(inst) and annot.rect.contains(inst):
                        is_already_covered = True
                        break
                if not is_already_covered:
                    page.add_redact_annot(inst, fill=redaction_fill_color)
                    redacted_count += 1
        except Exception as e:
            # print(f"Warning: MuPDF search_for may have issues with pattern '{pattern_str}': {e}. Trying word-by-word.")
            # Fallback word-by-word for complex regex or if search_for has issues
            words = page.get_text("words")
            compiled_pattern = re.compile(pattern_str)
            for word_info in words:
                word_text = word_info[4]
                if compiled_pattern.fullmatch(word_text): # Ensure the whole word matches
                    rect = fitz.Rect(word_info[0:4])
                    is_already_covered = False
                    for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]):
                        if annot.rect.intersects(rect) and annot.rect.contains(rect):
                            is_already_covered = True
                            break
                    if not is_already_covered:
                        page.add_redact_annot(rect, fill=redaction_fill_color)
                        redacted_count +=1
    
    # 2. OCR for scanned/image-based content
    run_ocr = is_likely_scanned_pdf(page)
    if not run_ocr and redacted_count == 0:
        text_content = page.get_text("text")
        if 0 < len(text_content.strip()) < 500:
             run_ocr = True

    if run_ocr:
        print(f"Page {page.number + 1}: Attempting OCR.")
        ocr_instances = ocr_page_to_get_text_and_boxes(page)
        
        for pattern_str in patterns_to_redact:
            compiled_pattern = re.compile(pattern_str, re.IGNORECASE)
            for text, rect in ocr_instances:
                if compiled_pattern.search(text): # Search if pattern exists in the OCR'd text block
                    # Check for overlap before adding new redaction
                    is_already_covered = False
                    for annot in page.annots(types=[fitz.PDF_ANNOT_REDACT]):
                        if annot.rect.intersects(rect): # A simpler intersection check for OCR blocks
                            is_already_covered = True
                            break
                    if not is_already_covered:
                        page.add_redact_annot(rect, fill=redaction_fill_color)
                        redacted_count += 1
                        # Break if one match is found in this OCR block for this pattern to avoid multiple overlaps
                        # from the same pattern on the same text block if the pattern is general.
                        # However, if a block has "123456789 and 987654321" and pattern is \d{9}, both should be caught.
                        # The current ocr_page_to_get_text_and_boxes gives word-level rects, so this should be fine.

    # Apply actual redactions for the page
    if list(page.annots(types=[fitz.PDF_ANNOT_REDACT])): # Check if any redaction annots exist
        # ***** THIS IS THE CORRECTED LINE *****
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE) 
        # fitz.PDF_REDACT_IMAGE_REMOVE (value 0) ensures that image parts
        # under the redaction are also removed/filled with the redaction color.
    return redacted_count

# --- Main Application Logic ---
def redact_account_numbers_from_pdf(input_pdf_path, output_pdf_path, custom_patterns=None):
    if not os.path.exists(input_pdf_path):
        print(f"Error: Input PDF not found at {input_pdf_path}")
        return False

    effective_patterns = [
        r"\b123456789\b",
        r"\b\d{8,17}\b", 
        r"\b\d{3,6}[-\s]\d{2,6}[-\s]\d{3,6}(?:[-\s]\d{3,6})?\b",
        r"\b[A-Za-z]{0,2}\d{6,18}\b", 
        # Example for account numbers that might be preceded by keywords.
        # This pattern uses Python's `re` capabilities. \K is not supported by MuPDF's C regex.
        # Such patterns are best handled by iterating text blocks/words and using Python's `re`.
        # The current find_and_redact_text_on_page primarily uses page.search_for or word-level fullmatch.
        # For contextual patterns like "Account No: XXXX", you might need a more advanced text extraction
        # and regex application strategy if simple patterns for XXXX don't suffice.
        # Let's ensure the provided patterns are mostly self-contained numbers for `search_for`.
    ]

    if custom_patterns:
        if isinstance(custom_patterns, list):
            effective_patterns.extend(custom_patterns)
        else:
            print("Warning: custom_patterns should be a list of regex strings.")

    print(f"Using redaction patterns: {effective_patterns}")

    try:
        doc = fitz.open(input_pdf_path)
        if doc.is_encrypted:
            # Attempt to decrypt with an empty password, common for some "locked" PDFs
            if not doc.authenticate(""):
                print(f"Error: PDF '{input_pdf_path}' is encrypted and cannot be processed without the correct password.")
                doc.close()
                return False
            else:
                print(f"Successfully decrypted PDF '{input_pdf_path}' with empty password.")


        total_redactions = 0
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            print(f"Processing Page {page_num + 1}/{len(doc)}...")
            
            redactions_on_page = find_and_redact_text_on_page(page, effective_patterns)
            total_redactions += redactions_on_page
            if redactions_on_page > 0:
                print(f"Redacted {redactions_on_page} instances on page {page_num + 1}.")

        if total_redactions > 0:
            # `clean=True` can help further sanitize. `garbage=4` is good for removing unused objects.
            doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
            print(f"\nRedacted PDF saved to: {output_pdf_path}")
            print(f"Total redactions made: {total_redactions}")
        else:
            print("\nNo matching account numbers found to redact.")
            # If no redactions, you might choose not to save a new file,
            # or save it to indicate it has been processed. For now, let's save.
            if input_pdf_path != output_pdf_path : # Avoid overwriting if no changes and paths are same
                 doc.save(output_pdf_path, garbage=1, deflate=False) # Save quickly if no changes
                 print(f"No redactions made. A copy saved to {output_pdf_path}")
            else:
                 print(f"No redactions made. Output path is same as input, so no new file saved.")


        doc.close()
        return True

    except Exception as e:
        print(f"An error occurred during PDF processing: {e}")
        import traceback
        traceback.print_exc()
        return False

# --- Dummy PDF Creator ---
def create_dummy_pdf(filename="dummy_statement.pdf", include_image_page=True):
    # (Implementation from previous response, unchanged)
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


# --- Main execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redacts account numbers from PDF files.")
    parser.add_argument("input_pdf", help="Path to the input PDF file.")
    parser.add_argument("-o", "--output_pdf", help="Path for the redacted output PDF file. "
                                                 "If not provided, defaults to '<input_pdf_name>_redacted.pdf'.")
    
    args = parser.parse_args()
    input_file = args.input_pdf

    if not os.path.exists(input_file):
        # Check if the user might have forgotten to provide a real file and is using the placeholder
        if input_file == "test_statement.pdf" and not os.path.exists("test_statement.pdf"):
            print(f"Input file '{input_file}' not found. Creating a dummy 'test_statement.pdf' for this run.")
            create_dummy_pdf("test_statement.pdf")
        else:
            print(f"Error: Input file '{input_file}' not found.")
            exit(1)

    if args.output_pdf:
        output_file = args.output_pdf
    else:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_redacted{ext}"

    print(f"Input PDF: {input_file}")
    print(f"Output PDF: {output_file}")

    custom_regex_patterns = [] # Add any CLI-loaded or config-loaded patterns here

    success = redact_account_numbers_from_pdf(input_file, output_file, custom_patterns=custom_regex_patterns)

    if success:
        print("\nProcessing complete.")
    else:
        print("\nProcessing encountered errors.")

    print("\n--- Important Considerations ---")
    # (Same considerations as before)
    print("1. Regex Quality: The accuracy depends heavily on the quality of your regex patterns.")
    print("   Test them thoroughly to avoid false positives or false negatives.")
    print("2. Scanned PDFs & OCR: OCR is not 100% accurate. Manual review is often recommended for critical redactions.")
    print("3. PDF Complexity: Complex layouts or non-standard fonts might pose challenges.")
    print("4. Tesseract Path: Ensure Tesseract OCR is installed and its path is correctly configured if not in system PATH.")
    print("5. Security: `apply_redactions()` is generally good. For extreme security, verify output PDFs carefully.")
