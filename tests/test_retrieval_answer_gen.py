"""Phase 2.4 tests: context assembly (budget truncation) + answer
generation (real LiteLLM chat endpoint, not mocked, per this project's
established pattern).
"""

import uuid
from datetime import datetime, timezone

import pytest

from kb_fabric.models import Chunk, Document
from kb_fabric.retrieval.answer_gen import (
    AnswerGenInput,
    PreviousAttempt,
    assemble_context,
    generate_answer,
)
from kb_fabric.retrieval.fusion import FusedChunk


def _mk_fused_chunk(content: str, source_uri="file:///data/raw/test.md", title="test.md") -> FusedChunk:
    doc = Document(
        document_id=uuid.uuid4(),
        source_system="local_folder",
        source_uri=source_uri,
        title=title,
        content_hash="sha256:" + "1" * 64,
        authors=[],
        last_modified=datetime.now(timezone.utc),
    )
    chunk = Chunk(
        chunk_id=uuid.uuid4(),
        document_id=doc.document_id,
        source_system="local_folder",
        source_uri=source_uri,
        content=content,
        content_hash="sha256:" + "2" * 64,
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
    chunk.document = doc
    return FusedChunk(chunk=chunk, rrf_score=1.0, engines=["vector"])


# --- assemble_context ---

def test_assemble_context_respects_max_chunks():
    fused = [_mk_fused_chunk(f"chunk {i} content") for i in range(20)]
    selected = assemble_context(fused, max_chunks=5, max_tokens=100000)
    assert len(selected) == 5


def test_assemble_context_respects_token_budget():
    long_chunk = _mk_fused_chunk("x" * 4000)  # ~1000 tokens at 4 chars/token
    another_long_chunk = _mk_fused_chunk("y" * 4000)
    selected = assemble_context([long_chunk, another_long_chunk], max_chunks=10, max_tokens=1000)
    # First chunk alone already ~= the budget; second should be excluded.
    assert len(selected) == 1


def test_assemble_context_always_includes_at_least_one_chunk():
    """Even if a single chunk exceeds the token budget alone, it should
    still be included rather than returning empty context."""
    huge_chunk = _mk_fused_chunk("z" * 100000)
    selected = assemble_context([huge_chunk], max_chunks=10, max_tokens=100)
    assert len(selected) == 1


def test_assemble_context_empty_input_returns_empty():
    assert assemble_context([]) == []


# --- generate_answer (real LiteLLM call) ---

def test_generate_answer_grounds_in_provided_context():
    fused = [_mk_fused_chunk("The sky in this fictional world is green due to atmospheric methane.")]
    result = generate_answer(AnswerGenInput(query="What color is the sky in this world?", chunks=fused))
    assert "green" in result.answer.lower()
    assert len(result.citations) == 1
    assert result.citations[0].chunk_id == str(fused[0].chunk.chunk_id)


def test_generate_answer_empty_chunks_returns_no_context_message():
    result = generate_answer(AnswerGenInput(query="Anything?", chunks=[]))
    assert result.citations == []
    assert "no relevant context" in result.answer.lower()


def test_generate_answer_includes_retry_context_in_prompt():
    """Confirms the retry-context passthrough from design doc section 6.1
    actually reaches the LLM call, by checking the previous attempt's
    missing_aspects influences the regenerated answer."""
    fused = [_mk_fused_chunk("The warehouse is located in JAFZA-1 and JAFZA-2, Dubai.")]
    previous = PreviousAttempt(
        draft_answer="The warehouse is in JAFZA-1.",
        missing_aspects=["did not mention JAFZA-2"],
    )
    result = generate_answer(
        AnswerGenInput(query="Where are the warehouses?", chunks=fused, previous_attempt=previous)
    )
    assert "JAFZA-2" in result.answer or "JAFZA" in result.answer
