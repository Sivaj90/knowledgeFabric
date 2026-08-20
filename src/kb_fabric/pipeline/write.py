"""Write step (HLD §7.3): fan out parsed+chunked+classified+embedded content
to Postgres -- documents/chunks metadata, pgvector column, FTS (generated
column, no app write needed).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from kb_fabric.connectors.base import DocumentEnvelope
from kb_fabric.models import Chunk, Document


def write_document_envelope(
    session: Session,
    envelope: DocumentEnvelope,
    chunk_texts: list[str],
    embeddings: list[list[float]],
    classification_tier: str,
    effective_tier: str,
) -> Document:
    """Writes one Document row + N Chunk rows (one per chunk_texts entry) in
    a single transaction. Caller commits (or the Celery task wrapper does).

    Idempotency note: this assumes the caller (Phase 1.3's dedup gate) has
    already confirmed this (source_system, source_uri, content_hash) combo
    is new -- this function does an unconditional insert, it does not
    re-check for an existing Document itself (single responsibility: the
    connector owns "is this new", this module owns "write it").
    """
    assert len(chunk_texts) == len(embeddings), (
        f"chunk/embedding count mismatch: {len(chunk_texts)} chunks vs "
        f"{len(embeddings)} embeddings"
    )

    document = Document(
        source_system=envelope.source_system,
        source_uri=envelope.source_uri,
        title=envelope.title,
        content_hash=envelope.content_hash,
        owner=envelope.owner,
        authors=envelope.authors,
        created_at=envelope.created_at or datetime.now(timezone.utc),
        last_modified=envelope.last_modified or datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()  # assign document.document_id for the chunk FK

    for i, (chunk_text, embedding) in enumerate(zip(chunk_texts, embeddings)):
        chunk = Chunk(
            document_id=document.document_id,
            source_system=envelope.source_system,
            source_uri=envelope.source_uri,
            content=chunk_text,
            # Per-chunk content_hash (distinct from the document-level hash)
            # so the uq_chunk_document_hash constraint can distinguish
            # chunks within the same document.
            content_hash=_chunk_hash(chunk_text),
            embedding=embedding,
            functions=[],
            classification_tier=classification_tier,
            effective_tier=effective_tier,
            owner=envelope.owner,
            authors=envelope.authors,
            project_ids=[],
            entities=[],
            is_public=envelope.is_public,
            chunk_acl_tokens=envelope.acl_tokens,
            version=1,
        )
        session.add(chunk)

    session.flush()
    return document


def _chunk_hash(text: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
