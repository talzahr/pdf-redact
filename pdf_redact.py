import fitz  # PyMuPDF
import re
import os
from PIL import Image
import pytesseract # For OCR

# --- Configuration ---
# If Tesseract is not in your PATH, set its location here
# Example for Windows:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
# Example for Linux/macOS (if not in PATH, though usually it is):
# pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract' # Or /usr/bin/tesseract

# --- Helper Functions ---

def is_likely_scanned_pdf(page, text_threshold=100):
    """
    Heuristic to check if a page is likely scanned (image-based).
    If it has very little extractable text, it's probably an image.
    """
    text = page.get_text("text")
    return len(text.strip()) < text_threshold

def ocr_page_to_get_text_and_boxes(page, lang='eng'):
    """
    Performs OCR on a PDF page and returns text with bounding boxes.
    Returns a list of (text, fitz.Rect) tuples for found text.
    """
    ocr_instances = []
    try:
        pix = page.get_pixmap(dpi=300) # Higher DPI for better OCR
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Use image_to_data to get bounding boxes
        ocr_data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        
        n_boxes = len(ocr_data['level'])
        for i in range(n_boxes):
            if int(ocr_data['conf'][i]) > 60: # Confidence threshold
                text = ocr_data['text'][i].strip()
                if text: # Only consider non-empty text
                    (x, y, w, h) = (ocr_data['left'][i], ocr_data['top'][i], ocr_data['width'][i], ocr_data['height'][i])
                    # Convert OCR coordinates (from top-left of image) to PDF coordinates
                    # PyMuPDF coordinates are from top-left of page, but OCR gives image pixel coordinates.
                    # We need to scale these back if the get_pixmap DPI was different from 72.
                    # For simplicity, assume the pixmap covers the whole page and scale is 1:1 for now.
                    # This needs careful handling if page.rect != pix.irect
                    # For now, let's use the coordinates directly, assuming get_pixmap() gives full page at a scale
                    # that pytesseract uses. This might need refinement for very precise redaction.
                    
                    # The coordinates from image_to_data are in pixels relative to the image.
                    # The pixmap from get_pixmap also has width/height in pixels.
                    # The PDF page has a rectangle (page.rect) in points (1/72 inch).
                    # We need to map pixel coordinates from OCR back to PDF points.
                    
                    page_rect = page.rect # Page dimensions in PDF points
                    img_width = pix.width
                    img_height = pix.height

                    x0 = (x / img_width) * page_rect.width
                    y0 = (y / img_height) * page_rect.height
                    x1 = ((x + w) / img_width) * page_rect.width
                    y1 = ((y + h) / img_height) * page_rect.height
                    
                    ocr_instances.append((text, fitz.Rect(x0, y0, x1, y1)))
        return ocr_instances
    except Exception as e:
        print(f"Error during OCR: {e}")
        return []


def find_and_redact_text_on_page(page, patterns_to_redact):
    """
    Finds text matching patterns on a page and adds redaction annotations.
    Handles both text-based and image-based (via OCR) PDFs.
    """
    redacted_count = 0

    # 1. Try direct text extraction (for text-based PDFs)
    # PyMuPDF's search_for can handle simple regex and is very efficient
    for pattern_str in patterns_to_redact:
        # PyMuPDF's regex is not as full-featured as Python's `re`
        # For specific numbers like "123456789", it's fine.
        # For more complex patterns, you might need to extract all words and then use `re`.
        
        # Option A: Using page.search_for (good for known strings and simpler patterns)
        # This finds whole words or phrases matching the pattern.
        # Example: if pattern_str is r"\b123456789\b"
        try:
            text_instances = page.search_for(pattern_str, flags=fitz.TEXT_SEARCH_REGEX)
            for inst in text_instances:
                page.add_redact_annot(inst, fill=(0, 0, 0)) # Black fill
                redacted_count += 1
                # print(f"Redacting (direct): '{page.get_textbox(inst)}' at {inst}")
        except Exception as e:
            # This can happen if the regex is too complex for MuPDF's engine
            # print(f"MuPDF search_for failed for pattern '{pattern_str}': {e}. Falling back to word iteration.")
            
            # Option B: Iterate through words and use Python's `re` (more flexible regex)
            # This is more robust for complex regex but can be slower.
            words = page.get_text("words")  # list of [x0, y0, x1, y1, "word", block_no, line_no, word_no]
            compiled_pattern = re.compile(pattern_str) # Compile for efficiency
            
            # Simple word-by-word matching (might miss multi-word patterns)
            for word_info in words:
                word_text = word_info[4]
                if compiled_pattern.search(word_text):
                    rect = fitz.Rect(word_info[0:4])
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    redacted_count +=1
                    # print(f"Redacting (word-by-word): '{word_text}' at {rect}")
            
            # TODO: For multi-word patterns with this method, you'd need to concatenate words
            # from lines or blocks and then try to map the match back to constituent word rectangles.
            # This is complex. For now, simpler regex with search_for or word-by-word regex is easier.

    # 2. If page seems scanned or few redactions were made, try OCR
    # You might adjust the condition, e.g., if redacted_count == 0 and is_likely_scanned_pdf(page)
    if is_likely_scanned_pdf(page) or (redacted_count == 0 and len(page.get_text("text")) < 500): # Heuristic
        print(f"Page {page.number + 1} seems image-based or has few text elements. Trying OCR.")
        ocr_instances = ocr_page_to_get_text_and_boxes(page)
        
        for pattern_str in patterns_to_redact:
            compiled_pattern = re.compile(pattern_str, re.IGNORECASE)
            for text, rect in ocr_instances:
                if compiled_pattern.search(text):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    redacted_count += 1
                    # print(f"Redacting (OCR): '{text}' at {rect}")
    
    if redacted_count > 0:
        page.apply_redactions() # Actually remove the content
    return redacted_count

# --- Main Application Logic ---
def redact_account_numbers_from_pdf(input_pdf_path, output_pdf_path, custom_patterns=None):
    """
    Analyzes a PDF, redacts specified account number patterns, and saves a new PDF.
    """
    if not os.path.exists(input_pdf_path):
        print(f"Error: Input PDF not found at {input_pdf_path}")
        return

    # --- Define Account Number Patterns (Regular Expressions) ---
    # Add more patterns as needed. Be careful with generic patterns to avoid false positives.
    # \b ensures word boundaries (e.g., doesn't match part of a longer number).
    account_number_patterns = [
        r"\b123456789\b",  # Specific example from user
        r"\b\d{8,16}\b",   # Generic: 8 to 16 digits (common account number length)
        r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{0,4}\b", # Numbers with optional dashes/spaces like XXXX-XXXX-XXXX-XXXX
        r"(?i)Account\s*(?:Number|No\.?|#)?\s*[:\-]?\s*([A-Za-z0-9\-]{6,20}\b)", # "Account Number: XXXXXX"
        # Add more specific bank patterns if known, e.g.:
        # r"\bCHASE-\d{10}\b",
        # r"\bBOA\s\d{4}-\d{4}-\d{4}\b",
    ]
    
    if custom_patterns:
        if isinstance(custom_patterns, list):
            account_number_patterns.extend(custom_patterns)
        else:
            print("Warning: custom_patterns should be a list of regex strings.")

    # For the "Account Number: XXXXX" type pattern, we only want to redact the number part.
    # This is harder with page.search_for if the regex captures groups.
    # For such cases, the word-by-word or OCR method with Python's `re` is better.
    # For now, let's simplify and ensure the regexes primarily target the number itself.

    print(f"Using patterns: {account_number_patterns}")

    try:
        doc = fitz.open(input_pdf_path)
        total_redactions = 0
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            print(f"Processing Page {page_num + 1}/{len(doc)}...")
            
            # For patterns like "Account No: 12345", we want to redact "12345"
            # The regex `(?i)Account\s*(?:Number|No\.?|#)?\s*[:\-]?\s*([A-Za-z0-9\-]{6,20}\b)`
            # has a capturing group for the number.
            # `page.search_for` doesn't directly give you sub-matches.
            # A workaround is to first find the whole phrase, then search *within that phrase's area*
            # for just the number part, or iterate words.

            # Let's stick to patterns that directly match the sensitive numbers for page.search_for
            # or use the word iteration for more complex regex.
            
            # For complex patterns with groups, an approach:
            effective_patterns = []
            for p_str in account_number_patterns:
                # If pattern is like "Account No: (actual_number_pattern)"
                if re.search(r"\(\?i\).*\(.+\)", p_str) or '(' in p_str.replace(r'\(', ''): # crude check for capturing groups
                    # This indicates a pattern that might capture more than just the number.
                    # For these, we'll rely more on the word iteration / OCR with Python's `re`
                    # Or, if using search_for, you'd search for the whole thing, then try to narrow down.
                    # For simplicity here, we'll just pass all patterns through.
                    # The word-by-word search in `find_and_redact_text_on_page` will use Python's `re`.
                    effective_patterns.append(p_str)
                else:
                    effective_patterns.append(p_str)


            redactions_on_page = find_and_redact_text_on_page(page, effective_patterns)
            total_redactions += redactions_on_page
            print(f"Redacted {redactions_on_page} instances on page {page_num + 1}.")

        if total_redactions > 0:
            # Save the document with redactions applied
            # garbage=4 cleans up unused objects, deflate compresses
            doc.save(output_pdf_path, garbage=4, deflate=True)
            print(f"\nRedacted PDF saved to: {output_pdf_path}")
            print(f"Total redactions made: {total_redactions}")
        else:
            print("\nNo matching account numbers found to redact.")
            # Optionally save a copy anyway, or don't save if no changes.
            # doc.save(output_pdf_path, garbage=4, deflate=True) 
            # print(f"No redactions, original content (potentially) saved to: {output_pdf_path}")


        doc.close()

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

# --- Example Usage ---
if __name__ == "__main__":
    # Create a dummy PDF for testing if you don't have one
    # (This part is just for making the example self-contained)
    def create_dummy_pdf(filename="dummy_statement.pdf"):
        doc = fitz.open()
        page = doc.new_page()
        
        # Text-based content
        page.insert_text(fitz.Point(50, 50), "Bank Statement Confidential")
        page.insert_text(fitz.Point(50, 100), "Customer Name: John Doe")
        page.insert_text(fitz.Point(50, 120), "Account Number: 123456789 Details") # Target
        page.insert_text(fitz.Point(50, 140), "Some other text 9876543210 also an account.") # Target
        page.insert_text(fitz.Point(50, 160), "Transaction for account 123-456-7890.") # Target
        page.insert_text(fitz.Point(50, 180), "Account # 112233445566") # Target
        page.insert_text(fitz.Point(50, 200), "Not an account: 12345") # Should not be redacted by \d{8,16}

        # Simulate a scanned image part (by inserting an image of text)
        # For a real test, you'd use an actual scanned PDF.
        # This is a bit contrived but demonstrates OCR path.
        try:
            img_page = doc.new_page()
            img_page.insert_text(fitz.Point(50,50), "This is an image page simulation.")
            img_page.insert_text(fitz.Point(50,70), "Image Account: 123456789") # Target for OCR
            pix = img_page.get_pixmap()
            doc.delete_page(img_page.number) # remove the text page
            
            page_for_image = doc.new_page() # new blank page
            page_for_image.insert_image(page_for_image.rect, pixmap=pix)
        except Exception as e:
            print(f"Could not create image part for dummy PDF (likely Pillow issue or no text for pixmap): {e}")


        doc.save(filename)
        doc.close()
        print(f"Dummy PDF created: {filename}")

    # --- Main execution ---
    # Path to your input PDF
    # INPUT_PDF = "path/to/your/bank_statement.pdf" 
    # create_dummy_pdf("sample_bank_statement.pdf") # Create a test PDF
    # INPUT_PDF = "sample_bank_statement.pdf"
    
    # ---- USER: Set your PDF path here ----
    INPUT_PDF = "test_statement.pdf" # << MAKE SURE THIS FILE EXISTS
                                     # Or uncomment create_dummy_pdf above to make one.
    
    if not os.path.exists(INPUT_PDF):
        print(f"Creating a dummy PDF named '{INPUT_PDF}' for testing as it was not found.")
        create_dummy_pdf(INPUT_PDF)

    OUTPUT_PDF = "redacted_statement.pdf"

    # You can add more complex/specific regex patterns here if needed
    # For example, patterns that look for "Account No." followed by digits
    # Make sure regex patterns are well-tested to avoid redacting non-sensitive info.
    custom_regex_patterns = [
        # r"(?i)(?:account|acct\.?|acct no\.?)\s*:?\s*(\d[\d\s-]{7,15}\d)", # Looks for "Account: XXX" then redacts XXX
                                                                     # This is complex due to capturing group for `search_for`
                                                                     # Better handled by word iteration or careful regex for search_for
        # r"\b\d{3}-\d{2}-\d{4}\b" # Example: SSN-like pattern (if relevant)
    ]

    redact_account_numbers_from_pdf(INPUT_PDF, OUTPUT_PDF, custom_patterns=custom_regex_patterns)

    print("\n--- Important Considerations ---")
    print("1. Regex Quality: The accuracy depends heavily on the quality of your regex patterns.")
    print("   Test them thoroughly to avoid false positives (redacting too much) or false negatives (missing numbers).")
    print("2. Scanned PDFs: OCR is not 100% accurate. Some numbers might be missed or misread.")
    print("   Manual review of critical redactions is often recommended.")
    print("3. PDF Complexity: Very complex PDF layouts or non-standard fonts might pose challenges.")
    print("4. Tesseract Path: Ensure Tesseract OCR is installed and its path is correctly configured if not in system PATH.")
    print("5. Security: `apply_redactions()` is good, but for extreme security, verify the output PDF doesn't retain hidden data.")
