"""Functional tests for the Phase 1.4 processing pipeline (parse -> chunk ->
classify -> embed -> write). Runs against the real Postgres DB and the real
LiteLLM embeddings endpoint (network call) -- not mocked -- because the
whole point of Phase 1.4 is proving the pipeline works against real
infrastructure, matching how Phases 1.1-1.3 were verified.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from kb_fabric.connectors.base import DocumentEnvelope, Section
from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Chunk, Document
from kb_fabric.pipeline.chunk import chunk_text
from kb_fabric.pipeline.classify import classify_chunk
from kb_fabric.pipeline.embed import embed_texts
from kb_fabric.pipeline.orchestrator import process_envelope, source_uri_to_path
from kb_fabric.pipeline.parse import parse_file
from kb_fabric.pipeline.write import write_document_envelope

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def session():
    Session = get_sessionmaker()
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _mk_envelope(source_uri: str, title: str, content_hash: str) -> DocumentEnvelope:
    return DocumentEnvelope(
        source_system="local_folder",
        source_uri=source_uri,
        title=title,
        content_hash=content_hash,
        sections=[Section(text="")],
        owner=None,
        authors=[],
        is_public=False,
        acl_tokens=[],
    )


# --- parse ---

def test_parse_markdown_file():
    text = parse_file(FIXTURES_DIR / "sample1.md")
    assert "Landmark" in text
    assert "supply-chain" in text


def test_parse_text_file():
    text = parse_file(FIXTURES_DIR / "sample2.txt")
    assert "Plain text ingestion fixture" in text


# --- chunk ---

def test_chunk_text_splits_on_size():
    long_text = "word " * 500  # well over CHUNK_SIZE=1000 chars
    chunks = chunk_text(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= 1100 for c in chunks)  # some slack for separator boundaries


def test_chunk_text_empty_input_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_chunk_text_no_overlap_config():
    from kb_fabric.pipeline.chunk import CHUNK_OVERLAP

    assert CHUNK_OVERLAP == 0  # onyx-pattern decision, local VPC HLD


# --- classify ---

def test_classify_always_returns_internal():
    tier, effective = classify_chunk("any content at all")
    assert tier == "internal"
    assert effective == "internal"


# --- embed (real network call to LiteLLM) ---

def test_embed_texts_returns_correct_dimension():
    from kb_fabric.models import EMBEDDING_DIM

    vecs = embed_texts(["a short test sentence for embedding"])
    assert len(vecs) == 1
    assert len(vecs[0]) == EMBEDDING_DIM


def test_embed_texts_empty_list_returns_empty():
    assert embed_texts([]) == []


def test_embed_texts_preserves_order():
    vecs = embed_texts(["apple", "zebra"])
    assert len(vecs) == 2
    assert vecs[0] != vecs[1]  # sanity: different inputs -> different vectors


# --- write ---

def test_write_document_envelope_roundtrip(session):
    envelope = _mk_envelope(
        source_uri="file:///data/raw/write-test.md",
        title="write-test.md",
        content_hash="sha256:" + "a" * 64,
    )
    chunks = ["first chunk of content", "second chunk of content"]
    embeddings = embed_texts(chunks)

    doc = write_document_envelope(
        session=session,
        envelope=envelope,
        chunk_texts=chunks,
        embeddings=embeddings,
        classification_tier="internal",
        effective_tier="internal",
    )
    session.flush()

    fetched_chunks = session.execute(
        select(Chunk).where(Chunk.document_id == doc.document_id)
    ).scalars().all()
    assert len(fetched_chunks) == 2
    assert {c.content for c in fetched_chunks} == set(chunks)
    assert all(c.classification_tier == "internal" for c in fetched_chunks)
    assert all(c.is_public is False for c in fetched_chunks)
    assert all(len(c.embedding) == 1536 for c in fetched_chunks)


def test_write_document_envelope_mismatched_lengths_raises(session):
    envelope = _mk_envelope(
        source_uri="file:///data/raw/mismatch-test.md",
        title="mismatch-test.md",
        content_hash="sha256:" + "b" * 64,
    )
    with pytest.raises(AssertionError):
        write_document_envelope(
            session=session,
            envelope=envelope,
            chunk_texts=["one", "two"],
            embeddings=[[0.0] * 1536],  # only 1 embedding for 2 chunks
            classification_tier="internal",
            effective_tier="internal",
        )


# --- orchestrator (full pipeline, real file + real DB + real embeddings) ---

def test_source_uri_to_path_roundtrip():
    path = FIXTURES_DIR / "sample1.md"
    uri = path.resolve().as_uri()
    assert source_uri_to_path(uri) == path.resolve()


def test_source_uri_to_path_rejects_non_file_scheme():
    with pytest.raises(ValueError):
        source_uri_to_path("https://example.com/doc.pdf")


def test_process_envelope_full_pipeline_writes_real_chunks(session, tmp_path):
    """End-to-end: real file -> real parse/chunk/classify/embed -> real
    Postgres write, all in one call, no mocks.

    Note: process_envelope() commits internally (production behavior --
    Celery tasks need durability per envelope). To keep this test from
    leaving rows behind, we delete what we wrote in a finally block rather
    than relying on session.rollback() (which won't undo an already-committed
    transaction)."""
    test_file = tmp_path / "orchestrator_test.md"
    test_file.write_text("# Orchestrator Test\n\nEnd-to-end pipeline verification content.\n")

    envelope = _mk_envelope(
        source_uri=test_file.resolve().as_uri(),
        title="orchestrator_test.md",
        content_hash="sha256:" + "c" * 64,
    )

    try:
        chunks_written = process_envelope(session, envelope)
        assert chunks_written >= 1

        doc = session.execute(
            select(Document).where(Document.source_uri == envelope.source_uri)
        ).scalar_one()
        chunks = session.execute(
            select(Chunk).where(Chunk.document_id == doc.document_id)
        ).scalars().all()
        assert len(chunks) == chunks_written
        assert all(c.embedding is not None for c in chunks)
        assert all(c.classification_tier == "internal" for c in chunks)
    finally:
        session.execute(
            Document.__table__.delete().where(Document.source_uri == envelope.source_uri)
        )
        session.commit()


def test_process_envelope_empty_file_writes_zero_chunks(session, tmp_path):
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n\n  ")

    envelope = _mk_envelope(
        source_uri=empty_file.resolve().as_uri(),
        title="empty.txt",
        content_hash="sha256:" + "d" * 64,
    )

    chunks_written = process_envelope(session, envelope)
    assert chunks_written == 0
    # Zero chunks means process_envelope returns before any commit -- no
    # cleanup needed, but assert that to be sure the "no rows left behind"
    # invariant holds here too.
    row = session.execute(
        select(Document).where(Document.source_uri == envelope.source_uri)
    ).first()
    assert row is None
