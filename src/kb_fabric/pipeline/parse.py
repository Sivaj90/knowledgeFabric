"""Parse step (HLD §7.3 knowledge representation, Slice 1 capture pipeline).

Uses Unstructured.io's auto-partitioner for docx/pdf/pptx/md/txt, with
Tesseract OCR as the automatic fallback for scanned/image-only PDF pages
(unstructured handles this internally via its `strategy="auto"` / "hi_res"
detection -- we don't need to special-case OCR ourselves).
"""

from pathlib import Path

from unstructured.partition.auto import partition


def parse_file(path: Path) -> str:
    """Parse a single file into plain text, joining all extracted elements.

    Returns the concatenated text of every element Unstructured.io extracts
    (paragraphs, list items, table cells, etc.) in document order. Chunking
    (Phase 1.4 next step) operates on this joined text rather than the raw
    element list, matching the local VPC HLD's "one Section per file for
    Slice 1" simplification (see connectors/base.py Section docstring).
    """
    elements = partition(filename=str(path))
    return "\n\n".join(el.text for el in elements if el.text and el.text.strip())
