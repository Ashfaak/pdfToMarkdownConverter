import os
import gc
import io
import time
import base64
import re
import shutil
import requests
import uuid
import json
from pathlib import Path

# Suppress Transformers tie_word_embeddings warning
import logging
from transformers import logging as transformers_logging
transformers_logging.set_verbosity_error()

import pypdfium2 as pdfium
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling_core.types.doc import PictureItem

# --- Configuration ---
OLLAMA_API_URL = "http://localhost:11434/api/chat"
CLOUD_MODEL = "gemma4:31b-cloud"

# Make paths dynamic based on script location
BASE_DIR = Path.cwd()
SOURCE_DIRS = [BASE_DIR / "Input"]
OUT_DIR_BASE = BASE_DIR / "Output"
CACHE_DIR = BASE_DIR / ".cache"
JSON_CACHE_FILE = CACHE_DIR / "image_descriptions.json"
TEMP_PDF_DIR = BASE_DIR / f"temp_pages_{uuid.uuid4().hex[:8]}"

PROMPT = (
    "You are an expert in Electrical Engineering. "
    "Describe this image concisely in 2-4 sentences. Focus on technical content "
    "(circuit diagrams, relay characteristics, graphs, equations, tables, system topologies). "
    "Be direct — do not start with 'The image shows' or 'This is a diagram of'."
)

FAILED_MARKER = "!!!API_FAILED_RETRY_LATER!!!"
FORMULA_MARKER = "<!-- formula-not-decoded -->"

# Global Error Counter
error_count = 0

def load_json_cache() -> dict:
    if JSON_CACHE_FILE.exists():
        try:
            return json.loads(JSON_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}

def save_json_cache(cache_data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    JSON_CACHE_FILE.write_text(json.dumps(cache_data, indent=4), encoding="utf-8")

def pil_to_b64(pil_image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def describe_image(pil_image) -> str:
    """Send image to Ollama API and return description. Retries on failure. Returns FAILED_MARKER on persistent error."""
    global error_count
    payload = {
        "model": CLOUD_MODEL,
        "messages": [
            {
                "role": "user",
                "content": PROMPT,
                "images": [pil_to_b64(pil_image)],
            }
        ],
        "stream": False,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Increase timeout for large cloud models
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
            response.raise_for_status()
            return response.json()["message"]["content"].strip()
        except Exception as e:
            print(f"      [!] API Error (Attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3)  # Wait 3 seconds before retrying

    error_count += 1
    return FAILED_MARKER

def get_page_cache_path(pdf_path: Path, page_no: int) -> Path:
    # Use stem of pdf + page number for uniqueness
    pdf_cache_dir = CACHE_DIR / pdf_path.stem
    pdf_cache_dir.mkdir(parents=True, exist_ok=True)
    return pdf_cache_dir / f"page_{page_no}.md"

def is_page_cached_successfully(cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    content = cache_path.read_text(encoding="utf-8", errors="ignore")
    # If the cached page has failed markers, we consider it unsuccessful and must reprocess
    if FAILED_MARKER in content or FORMULA_MARKER in content:
        return False
    return True

def should_process_file(pdf_path: Path, out_path: Path) -> tuple[bool, str]:
    """Check if we should process this PDF. Skip if valid MD exists without fail markers."""
    if not out_path.exists():
        return True, "NEW"
    
    content = out_path.read_text(encoding="utf-8", errors="ignore")
    if FAILED_MARKER in content or FORMULA_MARKER in content:
        print(f"  [i] Found failed/undecoded markers in {out_path.name}. Reprocessing (will use page cache).")
        return True, "RETRY"
        
    return False, "SKIP"

def convert_pdf_page_by_page(pdf_path: Path, out_path: Path):
    global error_count
    print(f"\nProcessing: {pdf_path.name}")
    
    # Load JSON cache for descriptions
    image_cache = load_json_cache()
    
    # Setup temporary directory for page-level PDFs
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    num_pages = len(pdf)
    
    full_md_parts = []
    global_pic_count = 0
    skipped_logos = 0
    
    try:
        for i in range(num_pages):
            page_no = i + 1
            print(f"  [Page {page_no}/{num_pages}] Processing...")
            
            page_cache_path = get_page_cache_path(pdf_path, page_no)
            
            if is_page_cached_successfully(page_cache_path):
                print(f"    -> Reusing successful cached Markdown for page {page_no}.")
                full_md_parts.append(page_cache_path.read_text(encoding="utf-8"))
                
                # We still need to increment global_pic_count for figures that exist in this page's MD
                # to keep figure numbering consistent across pages.
                content = page_cache_path.read_text(encoding="utf-8")
                # Count occurrences of Figure placeholders we injected
                matches = re.findall(r"> \*\*\[Figure \d+", content)
                global_pic_count += len(matches)
                continue
                
            # If not cached, process via Docling
            # Save a single page to temporary PDF
            temp_pdf_path = TEMP_PDF_DIR / f"temp_page_{page_no}.pdf"
            new_pdf = pdfium.PdfDocument.new()
            new_pdf.import_pages(pdf, [i])
            new_pdf.save(temp_pdf_path)
            new_pdf.close()
            
            # 1st Pass: No formula enrichment (fast)
            opts = PdfPipelineOptions()
            opts.do_ocr = False
            opts.do_table_structure = True
            opts.generate_picture_images = True
            
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=opts,
                        backend=PyPdfiumDocumentBackend,
                    )
                }
            )
            
            result = converter.convert(str(temp_pdf_path))
            page_md = result.document.export_to_markdown(image_placeholder="<!-- image -->")
            
            # Check if formula enrichment is needed for THIS specific page
            if FORMULA_MARKER in page_md:
                print(f"    -> Found undecoded formula. Rerunning page {page_no} with Formula Enrichment...")
                
                # Unload previous models to save memory
                if hasattr(result, "input") and hasattr(result.input, "_backend"):
                    result.input._backend.unload()
                del result
                del converter
                gc.collect()
                
                # 2nd Pass: With formula enrichment
                opts.do_formula_enrichment = True
                converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(
                            pipeline_options=opts,
                            backend=PyPdfiumDocumentBackend,
                        )
                    }
                )
                result = converter.convert(str(temp_pdf_path))
                page_md = result.document.export_to_markdown(image_placeholder="<!-- image -->")
                
            # Process images for this page
            for item, _level in result.document.iterate_items():
                if isinstance(item, PictureItem):
                    pil_img = item.get_image(doc=result.document)
                    if pil_img:
                        width, height = pil_img.size
                        
                        # Skip tiny logos
                        if width < 150 or height < 150:
                            skipped_logos += 1
                            page_md = page_md.replace("<!-- image -->", "", 1)
                            continue
                            
                        global_pic_count += 1
                        cache_key = f"{pdf_path.name}_page_{page_no}_fig_{global_pic_count}"
                        
                        # Check if we already have a successful description from JSON cache
                        if cache_key in image_cache and image_cache[cache_key] != FAILED_MARKER:
                            print(f"    Figure {global_pic_count} (Page {page_no}): Reusing existing description from cache.")
                            desc = image_cache[cache_key]
                        else:
                            print(f"    Describing Figure {global_pic_count} (Page {page_no}, Dim: {width}x{height})... ", end="", flush=True)
                            t0 = time.time()
                            desc = describe_image(pil_img)
                            t1 = time.time()
                            
                            if desc == FAILED_MARKER:
                                print("FAILED")
                            else:
                                print(f"done ({t1-t0:.1f}s)")
                                # Update JSON Cache immediately
                                image_cache[cache_key] = desc
                                save_json_cache(image_cache)
                                
                        caption = item.caption_text(doc=result.document)
                        caption_text = f">\n> *Caption: {caption}*\n" if caption else ""
                        
                        replacement_text = f"\n> **[Figure {global_pic_count} (Page {page_no})]**\n> {desc}\n{caption_text}\n"
                        page_md = page_md.replace("<!-- image -->", replacement_text, 1)
                        
            # Remove any remaining placeholders (e.g. if extraction failed)
            page_md = page_md.replace("<!-- image -->", "")
            
            # Save to Page Cache so we don't need to re-convert next time
            page_cache_path.write_text(page_md, encoding="utf-8")
            
            full_md_parts.append(page_md)
            
            # Cleanup page resources
            if hasattr(result, "input") and hasattr(result.input, "_backend"):
                result.input._backend.unload()
            del result
            del converter
            gc.collect()
            
            # Remove temp single-page PDF
            temp_pdf_path.unlink(missing_ok=True)
            
    except KeyboardInterrupt:
        print(f"\n  [!] Interrupted by user! Saving progress for {pdf_path.name}...")
        error_count += 1
        raise
    finally:
        pdf.close()
        
    # Save final assembled Markdown
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n\n".join(full_md_parts), encoding="utf-8")
    print(f"  Saved: {out_path.name} ({global_pic_count} figures processed, {skipped_logos} logos skipped)")

def main():
    global error_count
    
    if not OLLAMA_API_URL:
        print("Error: OLLAMA_API_URL not configured.")
        return
        
    print(f"Starting Smart Batch Conversion using {CLOUD_MODEL}")
    print("=" * 50)
    
    for src_dir in SOURCE_DIRS:
        if not src_dir.exists():
            print(f"Input directory not found: {src_dir}. Please create it and add PDFs.")
            continue
            
        rel_dir = src_dir.relative_to(BASE_DIR)
        # Handle case where Input is root of relative or named "Input"
        if str(rel_dir) == "." or str(rel_dir) == "Input":
            out_dir = OUT_DIR_BASE
        else:
            out_dir = OUT_DIR_BASE / rel_dir
            
        pdf_files = list(src_dir.rglob("*.pdf"))
        print(f"\nScanning '{src_dir.name}' ({len(pdf_files)} PDFs found)")
        
        for pdf_path in pdf_files:
            # Maintain sub-directory structure if any
            rel_pdf = pdf_path.relative_to(src_dir)
            out_path = out_dir / rel_pdf.parent / (pdf_path.stem + ".md")
            
            should_process, mode = should_process_file(pdf_path, out_path)
            if not should_process:
                print(f"  [Skipped] {pdf_path.name} (already processed completely)")
                continue
                
            try:
                convert_pdf_page_by_page(pdf_path, out_path)
            except KeyboardInterrupt:
                print("\nBatch conversion aborted by user.")
                break
            except Exception as e:
                error_count += 1
                print(f"  [!] Critical Error processing {pdf_path.name}: {e}")
                
    # Final cleanup of temp directory - only if empty
    if TEMP_PDF_DIR.exists():
        try:
            if not any(TEMP_PDF_DIR.iterdir()):
                time.sleep(0.5)  # Give Windows a moment to release any pending file locks
                shutil.rmtree(TEMP_PDF_DIR, ignore_errors=True)
        except Exception:
            pass  # Suppress warnings if it still fails due to OS locks
    print("\n" + "=" * 50)
    print("Batch conversion complete!")
    if error_count > 0:
        print(f"\n[WARNING] There were {error_count} error(s) during processing.")
        print("Please rerun the script to retry failed items. Successful pages are cached and will be skipped.")

if __name__ == "__main__":
    main()
