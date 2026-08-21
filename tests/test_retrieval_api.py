"""Phase 2.7 tests: FastAPI /query endpoint, end-to-end against the real
DB, real LiteLLM calls (query planning, answer-gen, sufficiency check),
and real ingested Slice 1 test data -- via FastAPI's TestClient (real HTTP
request/response cycle, not a bare function call), per this project's
established "verify against real infra" pattern.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from kb_fabric.db import get_sessionmaker
from kb_fabric.models import Chunk, Document
from kb_fabric.pipeline.embed import embed_texts
from kb_fabric.retrieval.api import app

client = TestClient(app)


@pytest.fixture
def seeded_document():
    """Seeds one real document + chunk with a real embedding so /query has
    something to actually retrieve, independent of whatever else is (or
    isn't) in data/raw/ at test time."""
    Session = get_sessionmaker()
    session = Session()
    content = "The Q4 peak readiness plan covers JAFZA-1 warehouse load testing in November."
    doc = Document(
        document_id=uuid.uuid4(),
        source_system="local_folder",
        source_uri=f"file:///data/raw/api-test-{uuid.uuid4()}.md",
        title="api-test.md",
        content_hash="sha256:" + "5" * 64,
        authors=[],
        last_modified=datetime.now(timezone.utc),
    )
    session.add(doc)
    session.flush()

    embedding = embed_texts([content])[0]
    chunk = Chunk(
        document_id=doc.document_id,
        source_system="local_folder",
        source_uri=doc.source_uri,
        content=content,
        content_hash="sha256:" + "6" * 64,
        embedding=embedding,
        functions=[],
        classification_tier="internal",
        effective_tier="internal",
        authors=[],
        project_ids=[],
        entities=[],
        is_public=False,
        chunk_acl_tokens=[],
        version=1,
    )
    session.add(chunk)
    session.commit()
    yield doc
    session.execute(Document.__table__.delete().where(Document.document_id == doc.document_id))
    session.commit()
    session.close()


def test_query_endpoint_returns_grounded_answer_with_citations(seeded_document):
    response = client.post("/query", json={"query": "What does the Q4 peak readiness plan cover?"})
    assert response.status_code == 200
    body = response.json()

    assert "answer" in body and body["answer"]
    assert "JAFZA" in body["answer"] or "warehouse" in body["answer"].lower()
    assert len(body["citations"]) >= 1
    assert body["citations"][0]["source_uri"] == seeded_document.source_uri


def test_query_endpoint_transparency_block_has_all_fields(seeded_document):
    response = client.post("/query", json={"query": "What does the Q4 peak readiness plan cover?"})
    assert response.status_code == 200
    transparency = response.json()["transparency"]

    assert "engines_used" in transparency
    assert set(transparency["engines_used"]) <= {"vector", "keyword"}
    assert "reframed_query" in transparency
    assert "retrieval_passes" in transparency and transparency["retrieval_passes"] >= 1
    assert 0.0 <= transparency["coverage_score"] <= 1.0
    assert 0.0 <= transparency["groundedness_score"] <= 1.0


def test_query_endpoint_authorization_field_present_and_not_enforced(seeded_document):
    """Confirms the Phase 2.0 authz-skip decision is loudly surfaced in
    every response, per design doc section 2 -- never silently omitted."""
    response = client.post("/query", json={"query": "Any question"})
    assert response.status_code == 200
    assert response.json()["transparency"]["authorization"] == "not_enforced_slice2"


def test_query_endpoint_no_matching_context_still_returns_200():
    """A query with no relevant ingested content should not error -- it
    should return a real (if unhelpful) response with empty/low-scoring
    context, per generate_answer's 'no relevant context' fallback."""
    response = client.post(
        "/query", json={"query": "asdkjhaskjdh completely nonsensical query xyz999 unrelated"}
    )
    assert response.status_code == 200
    assert "answer" in response.json()


def test_query_endpoint_accepts_conversation_history(seeded_document):
    response = client.post(
        "/query",
        json={
            "query": "What warehouse does it mention?",
            "conversation_history": ["User previously asked about the Q4 peak readiness plan."],
        },
    )
    assert response.status_code == 200
    assert response.json()["answer"]
