"""Resume format support: converts PDF, DOCX, and Markdown resumes to plain text.

ApplyPilot's pipeline expects a plain-text resume at ~/.applypilot/resume.txt.
This module provides converters for common resume formats so users don't need
to manually create a text version.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF file.

    Uses Playwright to render the PDF in a browser and extract text,
    which handles complex layouts better than pure Python PDF parsers.
    Falls back to a simple text extraction if Playwright is unavailable.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted plain text.

    Raises:
        FileNotFoundError: If the PDF file doesn't exist.
        ImportError: If no PDF extraction library is available.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    has_library = False

    # Try PyPDF2 first (lighter dependency)
    try:
        from PyPDF2 import PdfReader
        has_library = True
        reader = PdfReader(str(pdf_path))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        text = "\n".join(text_parts)
        if text.strip():
            return _clean_extracted_text(text)
    except ImportError:
        pass

    # Try pdfminer.six
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        has_library = True
        text = pdfminer_extract(str(pdf_path))
        if text.strip():
            return _clean_extracted_text(text)
    except ImportError:
        pass

    if has_library:
        raise ValueError(
            f"Could not extract text from {pdf_path.name}. "
            "The PDF may be image-only (scanned). Try OCR or provide a text version."
        )

    raise ImportError(
        "No PDF extraction library found. Install one of:\n"
        "  pip install PyPDF2\n"
        "  pip install pdfminer.six"
    )


def extract_text_from_docx(docx_path: Path) -> str:
    """Extract text from a DOCX file.

    Args:
        docx_path: Path to the DOCX file.

    Returns:
        Extracted plain text.

    Raises:
        FileNotFoundError: If the DOCX file doesn't exist.
        ImportError: If python-docx is not installed.
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")

    try:
        from docx import Document
    except ImportError:
        raise ImportError(
            "python-docx is required for DOCX support.\n"
            "  pip install python-docx"
        )

    doc = Document(str(docx_path))
    text_parts = []
    for para in doc.paragraphs:
        text_parts.append(para.text)

    # Also extract from tables
    for table in doc.tables:
        for row in table.rows:
            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_text:
                text_parts.append(" | ".join(row_text))

    text = "\n".join(text_parts)
    return _clean_extracted_text(text)


def convert_markdown_to_text(md_path: Path) -> str:
    """Convert a Markdown resume to plain text.

    Strips Markdown formatting (headers, bold, italic, links, lists)
    while preserving the text content and structure.

    Args:
        md_path: Path to the Markdown file.

    Returns:
        Plain text version.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    md_path = Path(md_path)
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    text = md_path.read_text(encoding="utf-8")

    # Strip Markdown formatting
    # Headers: ## Title -> TITLE
    text = re.sub(r"^#{1,6}\s+(.+)$", lambda m: m.group(1).upper(), text, flags=re.MULTILINE)

    # Bold: **text** or __text__ -> text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)

    # Italic: *text* or _text_ -> text
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)

    # Links: [text](url) -> text
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)

    # Inline code: `code` -> code
    text = re.sub(r"`(.+?)`", r"\1", text)

    # Unordered lists: - item -> - item (keep as-is)
    # Ordered lists: 1. item -> - item
    text = re.sub(r"^\d+\.\s+", "- ", text, flags=re.MULTILINE)

    # Horizontal rules: --- or *** -> empty line
    text = re.sub(r"^[-*]{3,}\s*$", "", text, flags=re.MULTILINE)

    return _clean_extracted_text(text)


def _clean_extracted_text(text: str) -> str:
    """Clean up extracted text: normalize whitespace, remove artifacts."""
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove leading/trailing whitespace from lines
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    return text.strip()


def convert_resume(input_path: Path, output_path: Path | None = None) -> str:
    """Auto-detect format and convert a resume to plain text.

    Supports: .pdf, .docx, .md, .markdown, .txt

    Args:
        input_path: Path to the resume file.
        output_path: If provided, write the text to this file.

    Returns:
        The extracted plain text.

    Raises:
        ValueError: If the file format is not supported.
        FileNotFoundError: If the input file doesn't exist.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Resume not found: {input_path}")

    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        text = extract_text_from_pdf(input_path)
    elif suffix == ".docx":
        text = extract_text_from_docx(input_path)
    elif suffix == ".doc":
        raise ValueError(
            f"Legacy .doc format is not supported. "
            "Please save as .docx and try again."
        )
    elif suffix in (".md", ".markdown"):
        text = convert_markdown_to_text(input_path)
    elif suffix == ".txt":
        text = input_path.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"Unsupported resume format: {suffix}. "
            "Supported: .pdf, .docx, .md, .txt"
        )

    log.info("Converted %s (%d chars) from %s", input_path.name, len(text), suffix)

    if output_path:
        output_path = Path(output_path)
        output_path.write_text(text, encoding="utf-8")
        log.info("Written to: %s", output_path)

    return text
