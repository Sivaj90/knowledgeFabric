"""Functional tests for parsing real binary file types (docx/pptx/pdf) and
the Tesseract OCR fallback path for scanned/image-only PDFs -- the Phase
1.6 gap flagged after Phase 1.4 (only .md/.txt fixtures existed then).

Fixtures are generated on the fly in conftest-style fixtures rather than
checked into git as binaries, so they stay easy to regenerate/inspect and
don't bloat the repo with generated Office/PDF files.
"""

from pathlib import Path

import pytest

from kb_fabric.pipeline.chunk import chunk_text
from kb_fabric.pipeline.parse import parse_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_docx_real_file():
    text = parse_file(FIXTURES_DIR / "sample3.docx")
    assert "Landmark" in text
    assert "Q4 peak readiness" in text
    # sanity: docx parsing produced enough content to actually chunk
    assert len(chunk_text(text)) >= 1


def test_parse_pptx_real_file():
    text = parse_file(FIXTURES_DIR / "sample4.pptx")
    assert "Landmark" in text
    assert "supply-chain readiness" in text
    assert len(chunk_text(text)) >= 1


def test_parse_pdf_text_layer_real_file():
    """Text-based PDF (has a real text layer, not scanned) -- should parse
    via the fast text-extraction path, not OCR."""
    text = parse_file(FIXTURES_DIR / "sample5.pdf")
    assert "Landmark" in text
    assert "delivery promise logic" in text
    assert len(chunk_text(text)) >= 1


def test_parse_scanned_pdf_triggers_ocr_fallback():
    """Image-only PDF (no text layer at all) -- Unstructured.io must fall
    back to Tesseract OCR automatically; this is the one that would have
    failed with the pre-fix missing poppler-utils/libGL/tesseract deps."""
    text = parse_file(FIXTURES_DIR / "sample6_scanned.pdf")
    # OCR text has minor imprecision at times -- assert on the parts that
    # reliably come through clean rather than an exact string match.
    assert "Scanned Document" in text
    assert "Landmark" in text
    assert "JAFZA" in text
    assert len(chunk_text(text)) >= 1
