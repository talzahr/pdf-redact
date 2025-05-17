MIT license, Copyright (c) 2025, Bo Davidson

# PDF-Redact
**Purpose:** Easily redact PDF files of sensitive information such as bank account numbers from bank statements. Useful for document requests.

Modify *patterns.yaml* with Python regular expressions to automatically redact from a PDF file. The YAML includes basic instructions.
Your favorite LLM can provide assistance on more complex patterns or breaking them down for explaination.

Works on both digitally "scanned" (OCR) PDFs and photo-scanned non-OCR documents.
The latter is accomplished with an OCR (Optical Character Recognition) library that works with Tesseract OCR's AI model.
Then we parse the patterns and place a bounding box over the matched pattern.
Finally, the PDF is rendered and saved with the use of PyMuPDF.

## Installation
This should work fine with all latest dependency versions in the requirements.txt. 

1. Open up your favorite console terminal emulator (i.e. `CMD` in Windows)

2. Clone this repo: `git clone https://github.com/pdf-redact.git`

3. Change to the directory git clone created: `cd pdf-redact`

4a. **OPTION 1: Install dependencies globally:**
Install dependency libraries globally: `pip install -r requirements.txt`

4b. **OPTION 2: Install dependencies in a Python virtual environment.**
Create the venv in the application's directory: `python -m venv venv`
Activate the venv (windows): `.\venv\Scripts\activate.bat`
Activate the venv (Linux/POSIX): `./venv/Scripts/activate` (shell script)
Install dependency libraries: `pip install -r requirements.txt`

6. **You must install Tesseract OCR as an application**! The library requires to use its AI model.
   i.e. Windows: `winget install UB-Mannheim.TesseractOCR` (you may need to add the exe to your PATH or to pdf_redact.py in the commented area near the top)
   i.e. Ubuntu: `apt install tesseract-ocr` 
   i.e. Arch Linux: `pacman -S tesseract`
   [Official Github Repo for Tesseract-OCR](https://github.com/tesseract-ocr/tesseract)

5. Modify the `patterns.yaml` and read instructions there.

## Usage

Typical use is: `pdf_redact.py -o output_pdf_filename.pdf input_pdf_filename.pdf`
When encountering spaces in a PDF filename, enclose the entire name in quotations.

Even easier: `pdf_redact.py input_pdf_filename.pdf`
The output file will be written as *<input_pdf_name>_redacted.pdf*

Create a dummy PDF for testing: `pdf_redact.py --create-dummy [FILENAME]`

See `pdf_redact.py --help` for full list of arguments and usage.

**Always carefully review the output PDF document to ensure that all sensitive information has been redacted!**
It worked well in my testing and use-cases, but OCR is not perfect. 
