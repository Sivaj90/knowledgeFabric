"""Connector interface — shaped after onyx's BaseConnector/LoadConnector ABC
(load_credentials(), checkpointed poll, yields Document objects with
Sections) per local VPC HLD's stated adaptation rationale. Slice 1 only
implements a folder connector, but any future real SharePoint / Azure
DevOps / Teams connector should implement this same interface so downstream
code (Celery enqueue, processing pipeline) never has to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator


@dataclass
class Section:
    """One sub-piece of a source document (page, slide, cell range, etc.).
    Slice 1's folder connector produces exactly one Section per file (the
    whole file body) — multi-section documents are a real-connector concern.
    """

    text: str
    link: str | None = None  # deep link to this section, if the source supports it


@dataclass
class DocumentEnvelope:
    """The connector output contract every downstream stage consumes.

    Deliberately mirrors the `documents` table + a stub ACL, independent of
    where the bytes came from — a local folder today, SharePoint/Azure
    DevOps/Teams later — so Phase 1.4 (parse/chunk/classify/embed/write)
    never needs a source-specific branch.
    """

    source_system: str
    source_uri: str
    title: str
    content_hash: str  # "sha256:<64 hex chars>"
    sections: list[Section]
    owner: str | None = None
    authors: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    last_modified: datetime | None = None
    # Stub ACL for Slice 1 — always empty/non-public. Real connectors
    # populate this from the source's native permissions in a later slice;
    # the field exists now so the envelope shape doesn't change later.
    is_public: bool = False
    acl_tokens: list[str] = field(default_factory=list)


class LoadConnector(ABC):
    """Bulk/incremental loader — one poll cycle yields every new-or-changed
    document since the last checkpoint. Shaped after onyx's LoadConnector.
    """

    @abstractmethod
    def load_credentials(self, credentials: dict) -> dict | None:
        """Validate/attach credentials needed to reach the source.
        Folder connector has none; a real SharePoint connector would use
        this for the Graph API token. Returns any credential state that
        needs to be persisted (None if nothing changed)."""
        raise NotImplementedError

    @abstractmethod
    def load_from_state(self) -> Iterator[list[DocumentEnvelope]]:
        """Yield batches of DocumentEnvelope for every new-or-changed source
        item since the connector's last checkpoint. Checkpointing strategy
        is connector-specific (folder connector: content_hash comparison
        against the `documents` table; a real connector would track a
        source-native cursor/delta token)."""
        raise NotImplementedError
