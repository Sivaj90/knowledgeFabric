"""SQLAlchemy ORM models for the metadata store.

Schema follows the production HLD §7.4 chunk metadata schema
(`Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`), plus the
early-binding ACL columns called for in the local VPC HLD §5.1
(`is_public`, `chunk_acl_tokens`) so the later RBAC slice is a filter-clause
change, not a schema migration + rewrite.

Slice 1 note: `classification_tier`/`effective_tier` are hardcoded to
"internal" and `is_public=False`/`chunk_acl_tokens={}` for every chunk at
write time (Phase 1.4) — there is no real classifier/RBAC yet. The columns
exist now so nothing downstream has to change shape later.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kb_fabric.db import Base

# landmark-text-embedding-3-large -> OpenAI text-embedding-3-large model.
# Native output is 3072 dims, but pgvector 0.6.2 caps HNSW/IVFFlat ANN
# indexes at 2000 dimensions (InternalError: "column cannot have more than
# 2000 dimensions for hnsw index" — hit and confirmed live during Phase 1.2
# migration). Fix: request embeddings at reduced dimensionality via OpenAI's
# officially-supported `dimensions` API parameter (Matryoshka-style
# truncation, not a hack) — 1536 keeps well under the index limit at no
# real quality cost for this use case. Phase 1.4's embed step MUST pass
# `dimensions=EMBEDDING_DIM` on every embeddings.create() call, or the
# vectors written won't match this column's declared width.
EMBEDDING_DIM = 1536


class Document(Base):
    """One ingested source file/page (HLD 'document' — parent of chunks).

    Slice 1 source_system is always "local_folder" (data/raw/ substitute for
    the real SharePoint/Azure DevOps/Teams connectors), but the column is
    free-text now so the real connectors don't need a schema change later.
    """

    __tablename__ = "documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hash of the raw file content — drives the folder-scanner dedup gate
    # (Phase 1.3): same hash => skip re-parse/re-chunk/re-embed entirely.
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)  # "sha256:" + 64 hex

    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authors: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_modified: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.document_id"), nullable=True
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Idempotency / dedup gate: same source_uri + content_hash is a no-op
        # re-ingest (matches HLD "content_hash drives incremental re-embedding").
        UniqueConstraint(
            "source_system", "source_uri", "content_hash", name="uq_document_source_hash"
        ),
        Index("ix_documents_source_uri", "source_uri"),
    )


class Chunk(Base):
    """One retrieval/permission unit — matches HLD §7.4 exactly.

    Chunk, not document, is the atomic unit of permission and retrieval
    (non-negotiable design rule, see AGENTS.md / HLD §7.3).
    """

    __tablename__ = "chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Denormalized off the parent document for HLD-schema fidelity + so a
    # chunk row alone (as returned by retrieval) carries full provenance
    # without a join.
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    # Postgres FTS: generated (STORED) tsvector column + GIN index — kept in
    # sync automatically by Postgres on every INSERT/UPDATE of `content`,
    # never written by the app.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True), nullable=True
    )

    functions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    classification_tier: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )
    effective_tier: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )

    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    project_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    entities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    # --- Early-binding ACL columns (local VPC HLD §5.1, onyx pattern) ---
    # Slice 1: always is_public=False, chunk_acl_tokens=[] (hardcoded, no
    # real RBAC yet). Exists now so the RBAC slice is a WHERE-clause change:
    #   WHERE is_public OR chunk_acl_tokens && :user_acl_tokens
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chunk_acl_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_modified: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.chunk_id"), nullable=True
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_chunk_document_hash"),
        Index("ix_chunks_document_id", "document_id"),
        # GIN indexes for array-overlap authz filters and FTS, per both HLDs.
        Index("ix_chunks_chunk_acl_tokens", "chunk_acl_tokens", postgresql_using="gin"),
        Index("ix_chunks_functions", "functions", postgresql_using="gin"),
        Index("ix_chunks_project_ids", "project_ids", postgresql_using="gin"),
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        # ANN index for vector search (HNSW, pgvector >=0.5). Cosine distance
        # matches OpenAI/Azure OpenAI embedding similarity convention.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
