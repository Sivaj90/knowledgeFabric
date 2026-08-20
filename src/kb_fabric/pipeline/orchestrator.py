"""Pipeline orchestrator: parse -> chunk -> classify -> embed -> write.
This is the function Phase 1.3's Celery task stub (kb_fabric.tasks) calls;
kept separate from tasks.py so it's independently unit-testable without a
Celery/Redis dependency.
"""

import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from kb_fabric.connectors.base import DocumentEnvelope
from kb_fabric.pipeline.chunk import chunk_text
from kb_fabric.pipeline.classify import classify_chunk
from kb_fabric.pipeline.embed import embed_texts
from kb_fabric.pipeline.parse import parse_file
from kb_fabric.pipeline.write import write_document_envelope

logger = logging.getLogger(__name__)


def source_uri_to_path(source_uri: str) -> Path:
    """Slice 1 only: source_uri is a file:// URI from the folder connector.
    Real connectors (SharePoint etc.) would fetch bytes over the network
    instead of resolving a local path -- this helper is folder-connector
    specific, not a general envelope->bytes contract."""
    parsed = urlparse(source_uri)
    if parsed.scheme != "file":
        raise ValueError(f"Only file:// source_uri is supported in Slice 1, got: {source_uri!r}")
    return Path(unquote(parsed.path))


def process_envelope(session: Session, envelope: DocumentEnvelope) -> int:
    """Runs the full parse -> chunk -> classify -> embed -> write pipeline
    for one DocumentEnvelope. Returns the number of chunks written.

    Returns 0 (and logs a warning, does not raise) for a file that parses to
    no usable text -- an empty/whitespace-only source file is not an error,
    it's just nothing to index.
    """
    path = source_uri_to_path(envelope.source_uri)

    text = parse_file(path)
    chunks = chunk_text(text)
    if not chunks:
        logger.warning("no chunks produced for %s (empty or whitespace-only parse)", path)
        return 0

    classification_tier, effective_tier = classify_chunk(text)
    embeddings = embed_texts(chunks)

    write_document_envelope(
        session=session,
        envelope=envelope,
        chunk_texts=chunks,
        embeddings=embeddings,
        classification_tier=classification_tier,
        effective_tier=effective_tier,
    )
    session.commit()

    logger.info("processed %s: %d chunk(s) written", path, len(chunks))
    return len(chunks)
