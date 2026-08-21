"""Folder-scanner connector — Slice 1's local-filesystem substitute for the
real SharePoint/Azure DevOps/Teams connectors (see AGENTS.md / local VPC
HLD §1). Walks `data/raw/`, computes a content hash per file, and yields a
DocumentEnvelope only for files that are new or whose content changed since
the last run — matching the HLD's idempotency requirement (content_hash
drives incremental re-embedding, never blind re-ingest).
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from kb_fabric.connectors.base import DocumentEnvelope, LoadConnector, Section
from kb_fabric.models import Document

SOURCE_SYSTEM = "local_folder"

# Extensions Phase 1.4's Unstructured.io parse step will handle. The
# connector itself doesn't parse — it just decides what counts as an
# ingestible file — but filtering here keeps junk (.DS_Store, .gitkeep,
# tmp files) out of the pipeline entirely. .xlsx added 2026-08-21 when the
# user pointed real R&D docs at data/raw/ including a tracker spreadsheet
# -- requires the unstructured[xlsx] extra (openpyxl), added to
# requirements.txt at the same time.
SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".pptx", ".md", ".txt", ".xlsx"}


def compute_content_hash(path: Path) -> str:
    """sha256 of raw file bytes, formatted to match the HLD's
    "sha256:<hex>" convention used throughout documents/chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return f"sha256:{h.hexdigest()}"


class FolderConnector(LoadConnector):
    """Walks `root_dir` and yields a DocumentEnvelope for each new/changed
    file, using the `documents` table (source_system, source_uri,
    content_hash unique constraint) as the dedup checkpoint — no separate
    cursor file needed since Postgres already is the state store.
    """

    def __init__(self, root_dir: Path | str, session: Session):
        self.root_dir = Path(root_dir)
        self.session = session

    def load_credentials(self, credentials: dict) -> dict | None:
        # No auth needed for a local filesystem walk.
        return None

    def _iter_files(self) -> Iterator[Path]:
        if not self.root_dir.exists():
            return
        for path in sorted(self.root_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path

    def _already_ingested(self, source_uri: str, content_hash: str) -> bool:
        """Dedup gate: exact (source_system, source_uri, content_hash) match
        already exists in `documents` => this exact content was already
        captured, skip it entirely (no re-parse/re-chunk/re-embed).
        A changed file has the SAME source_uri but a DIFFERENT content_hash,
        so this check naturally treats it as new (matches HLD idempotency:
        "content_hash drives incremental re-embedding")."""
        stmt = select(Document.document_id).where(
            Document.source_system == SOURCE_SYSTEM,
            Document.source_uri == source_uri,
            Document.content_hash == content_hash,
        )
        return self.session.execute(stmt).first() is not None

    def load_from_state(self) -> Iterator[list[DocumentEnvelope]]:
        batch: list[DocumentEnvelope] = []
        for path in self._iter_files():
            source_uri = path.resolve().as_uri()
            content_hash = compute_content_hash(path)

            if self._already_ingested(source_uri, content_hash):
                continue

            stat = path.stat()
            envelope = DocumentEnvelope(
                source_system=SOURCE_SYSTEM,
                source_uri=source_uri,
                title=path.name,
                content_hash=content_hash,
                # Phase 1.4 (parse step) fills in real Section text via
                # Unstructured.io; the connector's job stops at "here is a
                # new/changed file", not parsing its content.
                sections=[Section(text="")],
                owner=None,
                authors=[],
                created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                is_public=False,
                acl_tokens=[],
            )
            batch.append(envelope)

        if batch:
            yield batch
