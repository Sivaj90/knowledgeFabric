"""Runnable entrypoint: scan data/raw/ and enqueue new/changed documents.

Usage:
    python -m kb_fabric.run_ingest [--dry-run]

--dry-run prints what would be enqueued without touching Celery/Redis —
useful for verifying the connector's dedup logic in isolation.
"""

import argparse
import logging

from kb_fabric.celery_app import celery_app  # noqa: F401 (registers tasks)
from kb_fabric.config import get_settings
from kb_fabric.connectors.folder import FolderConnector
from kb_fabric.db import get_sessionmaker
from kb_fabric.tasks import envelope_to_task_payload, process_document_envelope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(dry_run: bool = False) -> int:
    settings = get_settings()
    Session = get_sessionmaker()
    enqueued = 0

    with Session() as session:
        connector = FolderConnector(root_dir=settings.raw_docs_dir, session=session)
        for batch in connector.load_from_state():
            for envelope in batch:
                if dry_run:
                    logger.info(
                        "[dry-run] would enqueue: %s (hash=%s)",
                        envelope.source_uri,
                        envelope.content_hash,
                    )
                else:
                    payload = envelope_to_task_payload(envelope)
                    process_document_envelope.delay(payload)
                    logger.info(
                        "enqueued: %s (hash=%s)", envelope.source_uri, envelope.content_hash
                    )
                enqueued += 1

    logger.info("done: %d document(s) %s", enqueued, "would be enqueued" if dry_run else "enqueued")
    return enqueued


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Don't touch Celery/Redis")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
