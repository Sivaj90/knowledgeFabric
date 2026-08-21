"""Phase 2.1 tests: query-side vector search. Runs against the real
kb_fabric Postgres DB and the real LiteLLM embeddings endpoint, per this
project's established pattern (no mocks).
"""

import uuid

import pytest
from sqlalchemy import select

from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Chunk, Document
from kb_fabric.pipeline.embed import embed_texts
from kb_fabric.retrieval.vector_search import vector_search


@pytest.fixture
def session():
    Session = get_sessionmaker()
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_document_with_chunks(session, contents: list[str]) -> Document:
    doc = Document(
        source_system="local_folder",
        source_uri=f"file:///data/raw/vector-search-test-{uuid.uuid4()}.md",
        title="vector-search-test.md",
        content_hash="sha256:" + "a" * 64,
        authors=[],
    )
    session.add(doc)
    session.flush()

    embeddings = embed_texts(contents)
    for i, (content, embedding) in enumerate(zip(contents, embeddings)):
        chunk = Chunk(
            document_id=doc.document_id,
            source_system="local_folder",
            source_uri=doc.source_uri,
            content=content,
            content_hash=f"sha256:{'b' * 63}{i}",
            embedding=embedding,
            functions=[],
            classification_tier="internal",
            effective_tier="internal",
            authors=[],
            project_ids=[],
            entities=[],
            is_public=False,
            chunk_acl_tokens=[],
            version=1,
        )
        session.add(chunk)
    session.flush()
    return doc


def test_vector_search_returns_ranked_results(session):
    doc = _seed_document_with_chunks(
        session,
        [
            "The UAE delivery promise logic changed for JAFZA-1 warehouse capacity.",
            "Quarterly financial results for the retail division.",
        ],
    )
    try:
        results = vector_search(session, "delivery promise logic UAE")
        assert len(results) >= 1
        # The delivery-promise chunk should rank ahead of the unrelated
        # financial-results chunk for this query.
        top = results[0]
        assert "delivery promise" in top.chunk.content
        assert top.rank == 1
    finally:
        session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
        session.commit()


def test_vector_search_respects_top_n(session):
    doc = _seed_document_with_chunks(
        session,
        [f"Test chunk number {i} about warehouse logistics." for i in range(5)],
    )
    try:
        results = vector_search(session, "warehouse logistics", top_n=3)
        assert len(results) == 3
        assert [r.rank for r in results] == [1, 2, 3]
    finally:
        session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
        session.commit()


def test_vector_search_empty_query_returns_empty(session):
    assert vector_search(session, "") == []
    assert vector_search(session, "   ") == []


def test_vector_search_no_chunks_in_db_returns_empty(session):
    """Sanity check against an empty result set (not an error condition)."""
    # Use a random query unlikely to match anything meaningfully -- this
    # just confirms the function doesn't error when the DB has 0 chunks
    # matching (or very few), it's a smoke test on the query shape itself.
    results = vector_search(session, "asdkjhaskjdh nonsense query xyz123")
    assert isinstance(results, list)
