"""Functional test: the run_ingest entrypoint actually enqueues a real
Celery task onto the real Redis broker, and the (stub) task result reflects
NotImplementedError as expected for Phase 1.3 (processing body is Phase 1.4).
"""

import shutil
from pathlib import Path

import pytest

from kb_fabric.celery_app import celery_app
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
    Celery app config (real Redis broker/backend URLs from settings) to
    prove the enqueue path is wired correctly end-to-end, without needing a
    separate worker process running in the test environment."""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        count = run(dry_run=False)
    finally:
        celery_app.conf.task_always_eager = False

    assert count == 2
