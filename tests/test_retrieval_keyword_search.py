"""Phase 2.2 tests: query-side keyword (FTS) search + the authz_filter hook.
Runs against the real kb_fabric Postgres DB (no mocks).
"""

import uuid

import pytest

from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Chunk, Document
from kb_fabric.retrieval.keyword_search import build_authz_filter, keyword_search


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
        source_uri=f"file:///data/raw/keyword-search-test-{uuid.uuid4()}.md",
        title="keyword-search-test.md",
        content_hash="sha256:" + "c" * 64,
        authors=[],
    )
    session.add(doc)
    session.flush()

    for i, content in enumerate(contents):
        chunk = Chunk(
            document_id=doc.document_id,
            source_system="local_folder",
            source_uri=doc.source_uri,
            content=content,
            content_hash=f"sha256:{'d' * 63}{i}",
            embedding=None,  # keyword search doesn't need embeddings
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


def test_keyword_search_matches_exact_terms(session):
    doc = _seed_document_with_chunks(
        session,
        [
            "JAFZA-1 warehouse capacity constraints for Q4 peak season.",
            "Unrelated content about quarterly marketing spend.",
        ],
    )
    try:
        results = keyword_search(session, "JAFZA warehouse capacity")
        assert len(results) >= 1
        assert "JAFZA" in results[0].chunk.content
    finally:
        session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
        session.commit()


def test_keyword_search_respects_top_n(session):
    doc = _seed_document_with_chunks(
        session,
        [f"Delivery promise logic test case number {i}." for i in range(5)],
    )
    try:
        results = keyword_search(session, "delivery promise logic", top_n=2)
        assert len(results) == 2
        assert [r.rank for r in results] == [1, 2]
    finally:
        session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
        session.commit()


def test_keyword_search_empty_query_returns_empty(session):
    assert keyword_search(session, "") == []
    assert keyword_search(session, "   ") == []


def test_keyword_search_no_match_returns_empty(session):
    doc = _seed_document_with_chunks(session, ["Completely unrelated content about cats."])
    try:
        results = keyword_search(session, "zzzznonexistenttermxyz")
        assert results == []
    finally:
        session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
        session.commit()


def test_authz_filter_returns_none_when_not_enforced():
    """Phase 2.0 decision: AUTHZ_ENFORCED=False in Slice 2 -- confirm the
    hook is a documented no-op, not silently broken."""
    assert build_authz_filter({"functions_allowed": ["rnd"]}) is None
    assert build_authz_filter(None) is None
