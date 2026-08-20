"""Functional tests for the Phase 1.2 documents/chunks schema.

These exercise the actual Postgres instance (pgvector + FTS + GIN indexes),
not mocks -- this is deliberately schema-level verification, ahead of
Phase 1.3/1.4 which will build the real ingestion/embedding pipeline that
writes these rows for real.
"""

import uuid

import pytest
from sqlalchemy import select, text

from kb_fabric.db import get_sessionmaker
from kb_fabric.models import EMBEDDING_DIM, Chunk, Document


@pytest.fixture
def session():
    Session = get_sessionmaker()
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _mk_document(**overrides):
    defaults = dict(
        source_system="local_folder",
        source_uri="file:///data/raw/test-doc.md",
        title="Test Document",
        content_hash="sha256:" + "a" * 64,
        owner="team:test",
        authors=["u:tester"],
    )
    defaults.update(overrides)
    return Document(**defaults)


def _mk_chunk(document_id, embedding=None, **overrides):
    defaults = dict(
        document_id=document_id,
        source_system="local_folder",
        source_uri="file:///data/raw/test-doc.md",
        content="The quick brown fox jumps over the lazy dog.",
        content_hash="sha256:" + "b" * 64,
        embedding=embedding or ([0.001] * EMBEDDING_DIM),
        functions=["ecommerce"],
        classification_tier="internal",
        effective_tier="internal",
        owner="team:test",
        authors=["u:tester"],
        project_ids=["q4-test"],
        entities=["sku:1"],
        is_public=False,
        chunk_acl_tokens=[],
        version=1,
    )
    defaults.update(overrides)
    return Chunk(**defaults)


def test_insert_document_and_chunk_roundtrip(session):
    doc = _mk_document()
    session.add(doc)
    session.flush()  # assign document_id without committing

    chunk = _mk_chunk(doc.document_id)
    session.add(chunk)
    session.flush()

    fetched = session.get(Chunk, chunk.chunk_id)
    assert fetched is not None
    assert fetched.document_id == doc.document_id
    assert fetched.effective_tier == "internal"
    assert fetched.is_public is False
    assert fetched.chunk_acl_tokens == []
    assert len(fetched.embedding) == EMBEDDING_DIM


def test_document_chunk_hash_uniqueness_enforced(session):
    """Same document_id + content_hash twice must be rejected (dedup gate)."""
    doc = _mk_document(source_uri="file:///data/raw/dup-test.md")
    session.add(doc)
    session.flush()

    session.add(_mk_chunk(doc.document_id, content_hash="sha256:" + "c" * 64))
    session.flush()

    session.add(_mk_chunk(doc.document_id, content_hash="sha256:" + "c" * 64))
    with pytest.raises(Exception):
        session.flush()


def test_cascade_delete_document_removes_chunks(session):
    doc = _mk_document(source_uri="file:///data/raw/cascade-test.md")
    session.add(doc)
    session.flush()
    chunk = _mk_chunk(doc.document_id, content_hash="sha256:" + "d" * 64)
    session.add(chunk)
    session.flush()
    chunk_id = chunk.chunk_id

    session.delete(doc)
    session.flush()

    assert session.get(Chunk, chunk_id) is None


def test_vector_similarity_search(session):
    """pgvector HNSW cosine search returns the nearer vector first."""
    doc = _mk_document(source_uri="file:///data/raw/vec-test.md")
    session.add(doc)
    session.flush()

    near = _mk_chunk(
        doc.document_id,
        embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
        content_hash="sha256:" + "1" * 64,
        content="near vector chunk",
    )
    far = _mk_chunk(
        doc.document_id,
        embedding=[0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2),
        content_hash="sha256:" + "2" * 64,
        content="far vector chunk",
    )
    session.add_all([near, far])
    session.flush()

    query_vec = [0.99, 0.01] + [0.0] * (EMBEDDING_DIM - 2)
    stmt = (
        select(Chunk)
        .where(Chunk.document_id == doc.document_id)
        .order_by(Chunk.embedding.cosine_distance(query_vec))
        .limit(1)
    )
    top = session.execute(stmt).scalar_one()
    assert top.chunk_id == near.chunk_id


def test_fts_generated_column_and_search(session):
    """content_tsv is server-generated; a keyword search should find the row."""
    doc = _mk_document(source_uri="file:///data/raw/fts-test.md")
    session.add(doc)
    session.flush()

    chunk = _mk_chunk(
        doc.document_id,
        content="Landmark supply-chain readiness for Q4 peak season.",
        content_hash="sha256:" + "3" * 64,
    )
    session.add(chunk)
    session.flush()

    row = session.execute(
        text(
            "SELECT chunk_id FROM chunks "
            "WHERE content_tsv @@ plainto_tsquery('english', :q) "
            "AND document_id = :doc_id"
        ),
        {"q": "supply chain readiness", "doc_id": str(doc.document_id)},
    ).fetchone()
    assert row is not None
    assert row[0] == chunk.chunk_id


def test_acl_filter_query_pattern(session):
    """Verify the exact authz filter clause the local VPC HLD specifies:
    WHERE is_public OR chunk_acl_tokens && :user_acl_tokens
    Slice 1 hardcodes is_public=False/tokens=[] at write time, but the query
    shape must already work so the later RBAC slice is filter-only.
    """
    doc = _mk_document(source_uri="file:///data/raw/acl-test.md")
    session.add(doc)
    session.flush()

    restricted = _mk_chunk(
        doc.document_id,
        content_hash="sha256:" + "4" * 64,
        chunk_acl_tokens=["team:ecom-content"],
    )
    public = _mk_chunk(
        doc.document_id,
        content_hash="sha256:" + "5" * 64,
        is_public=True,
    )
    unauthorized = _mk_chunk(
        doc.document_id,
        content_hash="sha256:" + "6" * 64,
        chunk_acl_tokens=["team:finance-only"],
    )
    session.add_all([restricted, public, unauthorized])
    session.flush()

    rows = session.execute(
        text(
            "SELECT chunk_id FROM chunks "
            "WHERE document_id = :doc_id "
            "AND (is_public OR chunk_acl_tokens && :user_tokens)"
        ),
        {"doc_id": str(doc.document_id), "user_tokens": ["team:ecom-content"]},
    ).fetchall()
    returned_ids = {r[0] for r in rows}

    assert restricted.chunk_id in returned_ids
    assert public.chunk_id in returned_ids
    assert unauthorized.chunk_id not in returned_ids
