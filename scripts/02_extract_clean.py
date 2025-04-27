#!/usr/bin/env python3
"""
Script for Phase 2: Text Extraction & Cleanup

Tasks:
1. Read the manifest file.
2. For each PDF listed:
   a. Extract raw text using a chosen library (e.g., PyMuPDF).
   b. Apply cleaning rules:
      - Remove headers/footers (page numbers, titles).
      - Normalize whitespace, unicode characters, quotes, dashes.
      - Potentially remove watermarks if identifiable.
   c. (Optional but Recommended) Inject metadata (YAML front-matter).
   d. Save the cleaned text to the 'data/extracted_text/' directory,
      possibly using a consistent naming scheme (e.g., SxxEyy.txt).
"""

import argparse
import os
import pathlib
import pandas as pd
# Import your chosen PDF extraction library, e.g.:
# import fitz  # PyMuPDF

def extract_text_from_pdf(pdf_path):
    """Extracts text from a single PDF file."""
    print(f"  Extracting text from: {pdf_path.name}")
    # Placeholder - Replace with actual extraction logic
    try:
        # Example using PyMuPDF (uncomment import fitz)
        # doc = fitz.open(pdf_path)
        # text = ""
        # for page in doc:
        #     text += page.get_text()
        # doc.close()
        # return text
        with open(pdf_path, 'r', errors='ignore') as f: # Incorrect for PDF, just a placeholder!
             return f.read()
        print(f"    (Placeholder: Read {len(text)} chars)")
        return "Placeholder text for " + pdf_path.name
    except Exception as e:
        print(f"    Error extracting text from {pdf_path.name}: {e}")
        return None

def clean_text(raw_text, filename):
    """Applies cleaning rules to the extracted text."""
    print(f"  Cleaning text for: {filename}")
    # TODO: Implement cleaning logic
    # - Regex for headers/footers (often page numbers at bottom/top)
    # - Unicode normalization (unicodedata library)
    # - Whitespace cleanup (strip lines, replace multiple spaces)
    # - Smart quote/dash replacement
    text = raw_text
    # Example: Basic whitespace strip
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(filter(None, lines))
    print(f"    (Placeholder: Cleaned {len(text)} chars)")
    return text

def inject_metadata(cleaned_text, metadata):
    """Prepends YAML front-matter to the text."""
    front_matter = "---
"
    for key, value in metadata.items():
        # Basic YAML formatting (handle potential quotes later if needed)
        front_matter += f"{key}: {value}\n"
    front_matter += "---
"
    return front_matter + cleaned_text

def main():
    parser = argparse.ArgumentParser(description='Extract and clean text from Wild Kratts PDF scripts.')
    parser.add_argument('--manifest', type=pathlib.Path, default='data/manifest.csv', help='Path to the input manifest CSV file.')
    parser.add_argument('--pdf_dir', type=pathlib.Path, required=True, help='Directory where the original PDF files are located (based on manifest).')
    parser.add_argument('--output_dir', type=pathlib.Path, default='data/extracted_text', help='Directory to save cleaned TXT files.')
    parser.add_argument('--skip_existing', action='store_true', help='Do not re-process if output file already exists.')
    parser.add_argument('--add_metadata', action='store_true', help='Inject YAML front-matter metadata into output files.')

    args = parser.parse_args()

    try:
        manifest_df = pd.read_csv(args.manifest)
    except FileNotFoundError:
        print(f"Error: Manifest file not found: {args.manifest}")
        return
    except Exception as e:
        print(f"Error reading manifest file: {e}")
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {args.output_dir}")

    processed_count = 0
    skipped_count = 0
    error_count = 0

    for index, row in manifest_df.iterrows():
        pdf_filename = row.get('file_name') # Use 'file_name' column
        if not pdf_filename or pd.isna(pdf_filename):
            print(f"Warning: Skipping row {index+2} due to missing file_name in manifest.")
            continue

        # Construct expected output filename (e.g., S01E01.txt)
        # Use info from manifest if available, otherwise fallback
        s = row.get('season')
        e = row.get('episode')
        if s is not None and e is not None and not pd.isna(s) and not pd.isna(e):
             output_filename = f"S{int(s):02d}E{int(e):02d}.txt"
        else:
             # Fallback using the original PDF name (minus extension)
             output_filename = pathlib.Path(pdf_filename).stem + ".txt"

        output_path = args.output_dir / output_filename
        pdf_path = args.pdf_dir / pdf_filename # Assume PDFs are directly in pdf_dir for now

        print(f"Processing manifest entry: {pdf_filename} -> {output_filename}")

        if args.skip_existing and output_path.exists():
            print(f"  Skipping, output file already exists: {output_path.name}")
            skipped_count += 1
            continue

        if not pdf_path.exists():
            print(f"  Error: PDF file not found at expected path: {pdf_path}")
            error_count += 1
            continue

        raw_text = extract_text_from_pdf(pdf_path)
        if raw_text is None:
            error_count += 1
            continue

        cleaned_text = clean_text(raw_text, pdf_filename)

        final_text = cleaned_text
        if args.add_metadata:
             metadata = row.to_dict()
             # Remove checksum or other irrelevant fields for front-matter
             metadata.pop('checksum', None)
             metadata.pop('file_name', None) # Maybe keep original filename?
             metadata = {k:v for k, v in metadata.items() if pd.notna(v)} # Remove NaN
             final_text = inject_metadata(cleaned_text, metadata)

        try:
            output_path.write_text(final_text, encoding='utf-8')
            print(f"  Successfully wrote: {output_path.name}")
            processed_count += 1
        except Exception as e:
            print(f"  Error writing output file {output_path.name}: {e}")
            error_count += 1

    print("\nExtraction complete.")
    print(f"  Processed: {processed_count}")
    print(f"  Skipped:   {skipped_count}")
    print(f"  Errors:    {error_count}")

if __name__ == "__main__":
    main()
