"""Celery tasks. Phase 1.4 implements the actual parse -> chunk -> classify
-> embed -> write processing pipeline body (kb_fabric.pipeline.orchestrator).
"""

import dataclasses
from datetime import datetime

from kb_fabric.celery_app import celery_app
from kb_fabric.connectors.base import DocumentEnvelope, Section
from kb_fabric.db import get_sessionmaker
from kb_fabric.pipeline.orchestrator import process_envelope


def _dict_to_envelope(envelope_dict: dict) -> DocumentEnvelope:
    """Reverse of envelope_to_task_payload: rebuild a DocumentEnvelope from
    the plain dict a Celery task argument arrives as (JSON round-trip loses
    the dataclass type and turns datetimes into ISO strings)."""
    data = dict(envelope_dict)
    data["sections"] = [Section(**s) for s in data.get("sections", [])]
    for key in ("created_at", "last_modified"):
        if data.get(key):
            data[key] = datetime.fromisoformat(data[key])
    return DocumentEnvelope(**data)


@celery_app.task(name="kb_fabric.process_document_envelope")
def process_document_envelope(envelope_dict: dict) -> int:
    """Entry point for the processing pipeline. Takes a JSON-serializable
    dict form of DocumentEnvelope (Celery messages must be JSON-serializable,
    not arbitrary dataclasses), rebuilds it, and runs the full
    parse/chunk/classify/embed/write pipeline. Returns the number of chunks
    written (0 for an empty/unparseable source file -- not an error)."""
    envelope = _dict_to_envelope(envelope_dict)
    Session = get_sessionmaker()
    with Session() as session:
        return process_envelope(session, envelope)


def envelope_to_task_payload(envelope: DocumentEnvelope) -> dict:
    """Convert a DocumentEnvelope (dataclass, has datetimes) into a plain
    JSON-serializable dict suitable for a Celery task argument."""
    payload = dataclasses.asdict(envelope)
    for key in ("created_at", "last_modified"):
        if payload.get(key) is not None:
            payload[key] = payload[key].isoformat()
    return payload
