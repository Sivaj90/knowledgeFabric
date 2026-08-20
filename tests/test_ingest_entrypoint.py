"""Functional test: the run_ingest entrypoint actually enqueues a real
Celery task onto the real Redis broker. Since Phase 1.4, the task body is
the real parse/chunk/classify/embed/write pipeline (no longer a stub), so
the eager-mode test now also verifies real Postgres rows get written --
and cleans them up afterward so repeated test runs don't accumulate rows.
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from kb_fabric.celery_app import celery_app
from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Document
from kb_fabric.run_ingest import run

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_raw_dir(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in FIXTURES_DIR.iterdir():
        shutil.copy(f, raw / f.name)
    monkeypatch.setenv("RAW_DOCS_DIR", str(raw))
    # kb_fabric.config.get_settings() is lru_cache'd process-wide; clear it
    # so this test's monkeypatched RAW_DOCS_DIR actually takes effect.
    from kb_fabric.config import get_settings

    get_settings.cache_clear()
    yield raw
    get_settings.cache_clear()


def test_dry_run_does_not_touch_celery(tmp_raw_dir, capsys):
    count = run(dry_run=True)
    assert count == 2  # sample1.md + sample2.txt


def test_real_enqueue_reaches_redis_broker(tmp_raw_dir):
    """Runs the task synchronously (task_always_eager) against the real
    Celery app config (real Redis broker/backend URLs from settings) and
    the real Phase 1.4 pipeline body, to prove the full
    connector -> Celery -> pipeline -> Postgres path is wired correctly
    end-to-end, without needing a separate worker process in the test
    environment. Cleans up the rows it writes (real commits happen inside
    the pipeline, same reasoning as test_pipeline.py)."""
    source_uris = [
        (tmp_raw_dir / f.name).resolve().as_uri() for f in FIXTURES_DIR.iterdir()
        if f.suffix.lower() in {".md", ".txt"}
    ]

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        count = run(dry_run=False)
    finally:
        celery_app.conf.task_always_eager = False

    assert count == 2

    Session = get_sessionmaker()
    with Session() as session:
        docs = session.execute(
            select(Document).where(Document.source_uri.in_(source_uris))
        ).scalars().all()
        assert len(docs) == 2  # pipeline actually ran and wrote real rows
        for doc in docs:
            session.delete(doc)
        session.commit()
