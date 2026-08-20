"""Celery tasks. Phase 1.3 only enqueues; Phase 1.4 implements the actual
parse -> chunk -> classify -> embed -> write processing pipeline body.
"""

import dataclasses

from kb_fabric.celery_app import celery_app
from kb_fabric.connectors.base import DocumentEnvelope


@celery_app.task(name="kb_fabric.process_document_envelope")
def process_document_envelope(envelope_dict: dict) -> str:
    """Entry point for the processing pipeline (Phase 1.4). Takes a
    JSON-serializable dict form of DocumentEnvelope (Celery messages must be
    JSON-serializable, not arbitrary dataclasses) and will eventually
    parse/chunk/classify/embed/write it. For now (Phase 1.3 scope), this is
    an intentional stub — it exists so Phase 1.3's connector has a real
    Celery task to enqueue onto, satisfying "Enqueues envelope onto Celery"
    without pretending the processing pipeline already exists.
    """
    raise NotImplementedError(
        "Processing pipeline is Phase 1.4 scope — not yet implemented. "
        f"Received envelope for source_uri={envelope_dict.get('source_uri')!r}."
    )


def envelope_to_task_payload(envelope: DocumentEnvelope) -> dict:
    """Convert a DocumentEnvelope (dataclass, has datetimes) into a plain
    JSON-serializable dict suitable for a Celery task argument."""
    payload = dataclasses.asdict(envelope)
    for key in ("created_at", "last_modified"):
        if payload.get(key) is not None:
            payload[key] = payload[key].isoformat()
    return payload
