"""Functional tests for the Phase 1.3 folder connector.

Runs against the real `kb_fabric` Postgres DB (dedup gate relies on the
actual `documents` table unique constraint) and real fixture files on disk.
Celery/Redis enqueue path is exercised separately in test_ingest_entrypoint.
"""

import shutil
from pathlib import Path

import pytest

from kb_fabric.connectors.folder import FolderConnector, compute_content_hash
from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Document

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_raw_dir(tmp_path):
    """Copy the checked-in fixtures into a throwaway dir per test so tests
    can mutate files (rewrite content) without touching the repo fixtures."""
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in FIXTURES_DIR.iterdir():
        shutil.copy(f, raw / f.name)
    return raw


@pytest.fixture
def session():
    Session = get_sessionmaker()
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_content_hash_is_deterministic_sha256_prefixed():
    h1 = compute_content_hash(FIXTURES_DIR / "sample1.md")
    h2 = compute_content_hash(FIXTURES_DIR / "sample1.md")
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64


def test_unsupported_extension_is_skipped(tmp_raw_dir, session):
    connector = FolderConnector(root_dir=tmp_raw_dir, session=session)
    envelopes = [e for batch in connector.load_from_state() for e in batch]
    uris = {e.source_uri for e in envelopes}
    assert not any(u.endswith("ignored.log") for u in uris)
    assert any(u.endswith("sample1.md") for u in uris)
    assert any(u.endswith("sample2.txt") for u in uris)


def test_new_files_are_enqueued_with_stub_acl(tmp_raw_dir, session):
    connector = FolderConnector(root_dir=tmp_raw_dir, session=session)
    envelopes = [e for batch in connector.load_from_state() for e in batch]
    assert len(envelopes) == 2  # sample1.md + sample2.txt, .log excluded

    for e in envelopes:
        assert e.source_system == "local_folder"
        assert e.content_hash.startswith("sha256:")
        assert e.is_public is False
        assert e.acl_tokens == []


def test_dedup_gate_skips_already_ingested_unchanged_file(tmp_raw_dir, session):
    """Same file, same hash, already in `documents` -> must not be re-yielded."""
    target = tmp_raw_dir / "sample1.md"
    content_hash = compute_content_hash(target)
    source_uri = target.resolve().as_uri()

    doc = Document(
        source_system="local_folder",
        source_uri=source_uri,
        title="sample1.md",
        content_hash=content_hash,
        authors=[],
    )
    session.add(doc)
    session.flush()

    connector = FolderConnector(root_dir=tmp_raw_dir, session=session)
    envelopes = [e for batch in connector.load_from_state() for e in batch]
    uris = {e.source_uri for e in envelopes}

    assert source_uri not in uris  # already-ingested file must be skipped
    assert any(u.endswith("sample2.txt") for u in uris)  # other new file still yielded


def test_dedup_gate_re_enqueues_changed_file(tmp_raw_dir, session):
    """Same source_uri, different content -> different hash -> must be
    treated as new (this is the "changed file = re-embed" requirement)."""
    target = tmp_raw_dir / "sample1.md"
    source_uri = target.resolve().as_uri()
    old_hash = compute_content_hash(target)

    doc = Document(
        source_system="local_folder",
        source_uri=source_uri,
        title="sample1.md",
        content_hash=old_hash,
        authors=[],
    )
    session.add(doc)
    session.flush()

    # Mutate the file content -> new hash.
    target.write_text("# Sample Document 1 (updated)\n\nContent has changed.\n")
    new_hash = compute_content_hash(target)
    assert new_hash != old_hash

    connector = FolderConnector(root_dir=tmp_raw_dir, session=session)
    envelopes = [e for batch in connector.load_from_state() for e in batch]
    changed = [e for e in envelopes if e.source_uri == source_uri]

    assert len(changed) == 1
    assert changed[0].content_hash == new_hash


def test_no_files_yields_no_batches(tmp_path, session):
    empty_dir = tmp_path / "empty_raw"
    empty_dir.mkdir()
    connector = FolderConnector(root_dir=empty_dir, session=session)
    batches = list(connector.load_from_state())
    assert batches == []


def test_missing_root_dir_yields_no_batches(tmp_path, session):
    missing = tmp_path / "does_not_exist"
    connector = FolderConnector(root_dir=missing, session=session)
    batches = list(connector.load_from_state())
    assert batches == []
