"""Write step (HLD §7.3): fan out parsed+chunked+classified+embedded content
to Postgres -- documents/chunks metadata, pgvector column, FTS (generated
column, no app write needed).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from kb_fabric.connectors.base import DocumentEnvelope
from kb_fabric.models import Chunk, Document


def find_current_version(session: Session, source_system: str, source_uri: str) -> Document | None:
    """Returns the current (not-yet-superseded) Document for this
    source_system/source_uri, if one exists -- i.e. the version a
    changed-file re-ingest should supersede. None for a brand-new file."""
    stmt = (
        select(Document)
        .where(
            Document.source_system == source_system,
            Document.source_uri == source_uri,
            Document.superseded_by.is_(None),
        )
        .order_by(Document.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


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
    re-check "is this exact hash new" itself (single responsibility: the
    connector owns "is this new", this module owns "write it").

    Versioning (Phase 1.6): if a PRIOR version of this same source_uri
    exists (different content_hash, not yet superseded), this write
    supersedes it -- the new Document's version = previous.version + 1,
    and the previous Document's `superseded_by` is set to the new
    Document's id, matching HLD §7.4's `version`/`superseded_by` fields.

    Scoping note (documented limitation, not an oversight): chunk-level
    `superseded_by` is NOT populated here. HLD §7.4 has a `superseded_by`
    column on chunks too, but there is no well-defined 1:1 mapping between
    an old document's chunks and a new document's chunks without a real
    content-diffing algorithm (chunk boundaries can shift entirely on a
    re-chunk). For Slice 1, retrieval-time filtering on
    `document.superseded_by IS NULL` (a join) is sufficient to exclude
    stale chunks -- true chunk-level diffing is deferred to when a real
    versioning/diffing design is needed (flagged in the implementation
    plan, not silently skipped).
    """
    assert len(chunk_texts) == len(embeddings), (
        f"chunk/embedding count mismatch: {len(chunk_texts)} chunks vs "
        f"{len(embeddings)} embeddings"
    )

    previous = find_current_version(session, envelope.source_system, envelope.source_uri)
    next_version = (previous.version + 1) if previous else 1

    document = Document(
        source_system=envelope.source_system,
        source_uri=envelope.source_uri,
        title=envelope.title,
        content_hash=envelope.content_hash,
        owner=envelope.owner,
        authors=envelope.authors,
        created_at=envelope.created_at or datetime.now(timezone.utc),
        last_modified=envelope.last_modified or datetime.now(timezone.utc),
        version=next_version,
    )
    session.add(document)
    session.flush()  # assign document.document_id for the chunk FK and superseded_by FK

    if previous is not None:
        previous.superseded_by = document.document_id

    for chunk_text_value, embedding in zip(chunk_texts, embeddings):
        chunk = Chunk(
            document_id=document.document_id,
            source_system=envelope.source_system,
            source_uri=envelope.source_uri,
            content=chunk_text_value,
            # Per-chunk content_hash (distinct from the document-level hash)
            # so the uq_chunk_document_hash constraint can distinguish
            # chunks within the same document.
            content_hash=_chunk_hash(chunk_text_value),
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
            version=next_version,
        )
        session.add(chunk)

    session.flush()
    return document


def _chunk_hash(text: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
