"""
EYES — Document text extraction layer.

Consolidated wrapper over Docling + PyMuPDF. Returns plain text (or an empty
string if OCR failed). The caller — intelligence.py — then decides whether
to feed the text to Claude (cheap text mode) or fall back to Claude Vision.

Strategy:
  - PDFs: try PyMuPDF first (fast, no ML), then Docling (handles scanned PDFs)
  - Images: Docling with its built-in OCR backend
  - Any other: plain-text read

Coordinate-aware PDF extraction:
  Multi-column PDFs (e.g. Amex statements, HSBC) extract badly with plain
  get_text() because text streams are column-by-column. Using word coordinates
  we reconstruct the visual row order: group words that share the same Y-band,
  sort each band by X, and join. This gives properly formed rows like:
      December 5  THINKIFIC.COM VANCOUVER  432.00  3,430.91
  instead of all dates in one block, all merchants in another, all amounts last.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Below this, we consider the extraction "empty" and fall back to Vision
MIN_TEXT_LENGTH = 100

# Lazy-loaded singletons (Docling is heavy)
_docling_converter = None


@dataclass
class EyesResult:
    text: str = ""
    page_count: int | None = None
    is_scanned: bool = False
    method: str = ""  # "pymupdf", "docling", "plain", "failed"


# ---------------------------------------------------------------------------
# PyMuPDF — fast path for text-based PDFs
# ---------------------------------------------------------------------------

def _extract_pdf_pymupdf(file_bytes: bytes) -> tuple[str, int]:
    """Extract text from a PDF using PyMuPDF with coordinate-aware row reconstruction.

    Multi-column PDFs (e.g. Amex statements) extract badly with plain get_text()
    because text streams are column-by-column. Using word coordinates we can
    reconstruct the visual row order: group words that share the same Y-band,
    sort each band by X, and join. This gives properly formed rows like:
        December 5  THINKIFIC.COM VANCOUVER  432.00  3,430.91
    instead of all dates in one block, all merchants in another, all amounts last.
    """
    try:
        import fitz
    except ImportError:
        log.warning("PyMuPDF not installed")
        return "", 0

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)
        page_texts = []

        for page in doc:
            # get_text('words') → (x0,y0,x1,y1, word, block_no, line_no, word_no)
            words = page.get_text("words")
            if not words:
                page_texts.append(page.get_text())  # fallback
                continue

            # Group words into row-buckets by rounding Y to nearest 8px
            rows: dict[int, list[tuple[float, str]]] = {}
            for w in words:
                y_bucket = round(w[1] / 8) * 8
                rows.setdefault(y_bucket, []).append((w[0], w[4]))

            # Build lines: sort rows top-to-bottom, words left-to-right
            lines = []
            for y in sorted(rows):
                line_words = [word for _, word in sorted(rows[y])]
                line = "  ".join(line_words)
                lines.append(line)

            page_texts.append("\n".join(lines))

        doc.close()
        full_text = "\n\n".join(t for t in page_texts if t.strip())
        return full_text, page_count
    except Exception as e:
        log.warning(f"PyMuPDF extraction failed: {e}")
        return "", 0


# ---------------------------------------------------------------------------
# Docling — handles both PDFs (including scanned) and images
# ---------------------------------------------------------------------------

def _get_docling_converter():
    """Lazy-load Docling DocumentConverter (heavy ML models)."""
    global _docling_converter
    if _docling_converter is None:
        try:
            from docling.document_converter import DocumentConverter
            _docling_converter = DocumentConverter()
            log.info("Docling DocumentConverter initialized")
        except ImportError:
            log.warning("Docling not installed")
            raise
    return _docling_converter


def _extract_with_docling(file_bytes: bytes, suffix: str) -> tuple[str, int | None]:
    """Run Docling on the bytes by writing to a temp file."""
    try:
        converter = _get_docling_converter()
    except ImportError:
        return "", None

    # Docling needs a file path
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        tmp.close()

        result = converter.convert(tmp.name)
        text = result.document.export_to_markdown()
        page_count = None
        if hasattr(result.document, "pages"):
            try:
                page_count = len(result.document.pages)
            except Exception:
                page_count = None
        return text or "", page_count
    except Exception as e:
        log.warning(f"Docling extraction failed: {e}")
        return "", None
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_text(file_bytes: bytes, mime_type: str, filename: str = "") -> EyesResult:
    """
    Unified text extraction.

    Returns an EyesResult with:
      - text:       extracted plain text (possibly empty)
      - page_count: number of pages for PDFs (None otherwise)
      - is_scanned: True if the text came out too short (likely needs vision)
      - method:     which backend succeeded ("pymupdf", "docling", "plain", "failed")
    """
    result = EyesResult()

    if not file_bytes:
        result.method = "failed"
        return result

    is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")
    is_image = mime_type.startswith("image/") or any(
        filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic")
    )

    # PDF path: PyMuPDF first, then Docling
    if is_pdf:
        text, page_count = _extract_pdf_pymupdf(file_bytes)
        result.text = text
        result.page_count = page_count
        result.method = "pymupdf"

        if len(text.strip()) < MIN_TEXT_LENGTH:
            # Scanned PDF — try Docling
            log.info("PyMuPDF text too short, trying Docling")
            docling_text, dc_pages = _extract_with_docling(file_bytes, ".pdf")
            if len(docling_text.strip()) >= len(text.strip()):
                result.text = docling_text
                result.page_count = dc_pages or page_count
                result.method = "docling"

        result.is_scanned = len(result.text.strip()) < MIN_TEXT_LENGTH
        return result

    # Image path: Docling (handles OCR internally)
    if is_image:
        suffix = ".jpg"
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            if filename.lower().endswith(ext):
                suffix = ext
                break
        text, _ = _extract_with_docling(file_bytes, suffix)
        result.text = text
        result.method = "docling"
        result.is_scanned = len(text.strip()) < MIN_TEXT_LENGTH
        return result

    # Plain-text fallback
    try:
        result.text = file_bytes.decode("utf-8", errors="replace")
        result.method = "plain"
    except Exception:
        result.method = "failed"

    result.is_scanned = len(result.text.strip()) < MIN_TEXT_LENGTH
    return result


def has_enough_text(result: EyesResult, threshold: int = MIN_TEXT_LENGTH) -> bool:
    """Caller helper — is the extracted text good enough for text-mode Claude?"""
    return result.text is not None and len(result.text.strip()) >= threshold
