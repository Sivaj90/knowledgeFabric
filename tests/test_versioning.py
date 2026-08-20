"""Functional tests for Phase 1.6: idempotent re-ingest via the live CLI,
and document versioning (superseded_by) when a file's content changes.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from kb_fabric.celery_app import celery_app
from kb_fabric.connectors.base import DocumentEnvelope, Section
from kb_fabric.connectors.folder import compute_content_hash
from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Chunk, Document
from kb_fabric.pipeline.orchestrator import process_envelope
from kb_fabric.pipeline.write import find_current_version, write_document_envelope
from kb_fabric.run_ingest import run


@pytest.fixture
def session():
    Session = get_sessionmaker()
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _mk_envelope(source_uri: str, content_hash: str) -> DocumentEnvelope:
    return DocumentEnvelope(
        source_system="local_folder",
        source_uri=source_uri,
        title="versioning-test.md",
        content_hash=content_hash,
        sections=[Section(text="")],
        is_public=False,
        acl_tokens=[],
    )


# --- Idempotent re-ingest, via the live CLI entrypoint (not just the
# dedup-gate unit test from Phase 1.5 -- this exercises the whole
# connector -> Celery -> pipeline -> Postgres path twice in a row) ---

def test_idempotent_reingest_via_live_cli_no_duplicate_rows(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    test_file = raw / "idempotent_test.md"
    test_file.write_text("# Idempotency Test\n\nContent that should only be ingested once.\n")

    monkeypatch.setenv("RAW_DOCS_DIR", str(raw))
    from kb_fabric.config import get_settings

    get_settings.cache_clear()

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    source_uri = test_file.resolve().as_uri()

    try:
        # First run: should enqueue + process 1 new file.
        first_count = run(dry_run=False)
        assert first_count == 1

        # Second run, unchanged file: dedup gate must skip it entirely --
        # the connector shouldn't even yield it, so nothing gets enqueued.
        second_count = run(dry_run=False)
        assert second_count == 0

        Session = get_sessionmaker()
        with Session() as verify_session:
            docs = verify_session.execute(
                select(Document).where(Document.source_uri == source_uri)
            ).scalars().all()
            assert len(docs) == 1  # exactly one Document row, no duplicate
    finally:
        celery_app.conf.task_always_eager = False
        get_settings.cache_clear()
        # cleanup
        Session = get_sessionmaker()
        with Session() as cleanup_session:
            cleanup_session.execute(
                Document.__table__.delete().where(Document.source_uri == source_uri)
            )
            cleanup_session.commit()


# --- Versioning: changed file supersedes the prior Document version ---

def test_write_document_envelope_versions_on_content_change(session):
    source_uri = "file:///data/raw/versioning-test.md"

    v1 = _mk_envelope(source_uri, content_hash="sha256:" + "1" * 64)
    doc_v1 = write_document_envelope(
        session=session,
        envelope=v1,
        chunk_texts=["version 1 content"],
        embeddings=[[0.0] * 1536],
        classification_tier="internal",
        effective_tier="internal",
    )
    session.flush()
    assert doc_v1.version == 1
    assert doc_v1.superseded_by is None

    v2 = _mk_envelope(source_uri, content_hash="sha256:" + "2" * 64)
    doc_v2 = write_document_envelope(
        session=session,
        envelope=v2,
        chunk_texts=["version 2 content, changed"],
        embeddings=[[0.0] * 1536],
        classification_tier="internal",
        effective_tier="internal",
    )
    session.flush()

    assert doc_v2.version == 2
    assert doc_v2.superseded_by is None  # v2 is current, nothing supersedes it

    # Refresh doc_v1 from the DB to see the superseded_by write.
    session.refresh(doc_v1)
    assert doc_v1.superseded_by == doc_v2.document_id
    assert doc_v1.version == 1  # v1's own version number doesn't change


def test_find_current_version_returns_latest_not_superseded(session):
    source_uri = "file:///data/raw/current-version-test.md"

    v1 = _mk_envelope(source_uri, content_hash="sha256:" + "3" * 64)
    write_document_envelope(
        session=session, envelope=v1, chunk_texts=["v1"], embeddings=[[0.0] * 1536],
        classification_tier="internal", effective_tier="internal",
    )
    session.flush()

    # Before a second write, current version is v1.
    current = find_current_version(session, "local_folder", source_uri)
    assert current is not None
    assert current.version == 1

    v2 = _mk_envelope(source_uri, content_hash="sha256:" + "4" * 64)
    write_document_envelope(
        session=session, envelope=v2, chunk_texts=["v2"], embeddings=[[0.0] * 1536],
        classification_tier="internal", effective_tier="internal",
    )
    session.flush()

    # After the second write, current version is v2, not v1 (v1 now superseded).
    current = find_current_version(session, "local_folder", source_uri)
    assert current is not None
    assert current.version == 2


def test_find_current_version_returns_none_for_unknown_uri(session):
    assert find_current_version(session, "local_folder", "file:///nonexistent.md") is None


def test_process_envelope_versions_on_reingest_of_changed_file(session, tmp_path):
    """Full pipeline (not just write_document_envelope directly): re-running
    process_envelope on the SAME source_uri with different content produces
    a second Document version and marks the first superseded."""
    test_file = tmp_path / "process_versioning_test.md"
    test_file.write_text("# Version 1\n\nOriginal content for versioning test.\n")
    source_uri = test_file.resolve().as_uri()

    envelope_v1 = _mk_envelope(source_uri, content_hash=compute_content_hash(test_file))
    try:
        chunks_v1 = process_envelope(session, envelope_v1)
        assert chunks_v1 >= 1

        # Simulate a changed file: same source_uri, different content/hash.
        test_file.write_text("# Version 2\n\nUpdated content, should supersede v1.\n")
        envelope_v2 = _mk_envelope(source_uri, content_hash=compute_content_hash(test_file))
        chunks_v2 = process_envelope(session, envelope_v2)
        assert chunks_v2 >= 1

        docs = session.execute(
            select(Document).where(Document.source_uri == source_uri).order_by(Document.version)
        ).scalars().all()
        assert len(docs) == 2
        assert docs[0].version == 1
        assert docs[0].superseded_by == docs[1].document_id
        assert docs[1].version == 2
        assert docs[1].superseded_by is None
    finally:
        session.execute(Document.__table__.delete().where(Document.source_uri == source_uri))
        session.commit()
