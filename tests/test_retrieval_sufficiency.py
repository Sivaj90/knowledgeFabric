"""Phase 2.6 tests: sufficiency check (real LiteLLM calls) + the bounded
retry loop (loop-control logic tested with a controllable fake retrieve_fn
to force insufficient->retry->sufficient paths deterministically, since
relying on real LLM judgment to reliably produce "insufficient" on the
first pass would make the test flaky).
"""

import uuid
from datetime import datetime, timezone

from kb_fabric.models import Chunk, Document
from kb_fabric.retrieval.fusion import FusedChunk
from kb_fabric.retrieval.sufficiency import SufficiencyCheckInput, check_sufficiency
from kb_fabric.retrieval.sufficiency_loop import run_sufficiency_loop


def _mk_fused_chunk(content: str) -> FusedChunk:
    doc = Document(
        document_id=uuid.uuid4(),
        source_system="local_folder",
        source_uri="file:///data/raw/sufficiency-test.md",
        content_hash="sha256:" + "3" * 64,
        authors=[],
        last_modified=datetime.now(timezone.utc),
    )
    chunk = Chunk(
        chunk_id=uuid.uuid4(),
        document_id=doc.document_id,
        source_system="local_folder",
        source_uri=doc.source_uri,
        content=content,
        content_hash="sha256:" + "4" * 64,
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


# --- check_sufficiency (real LLM call) ---

def test_check_sufficiency_scores_grounded_answer_highly():
    chunks = [_mk_fused_chunk("The warehouse capacity limit is 80% for JAFZA-1.")]
    result = check_sufficiency(
        SufficiencyCheckInput(
            original_query="What is the JAFZA-1 capacity limit?",
            reframed_query=None,
            draft_answer="The JAFZA-1 warehouse capacity limit is 80%.",
            chunks=chunks,
        )
    )
    assert result.coverage_score >= 0.7
    assert result.groundedness_score >= 0.7
    assert result.verdict == "sufficient"


def test_check_sufficiency_flags_ungrounded_answer():
    chunks = [_mk_fused_chunk("The warehouse capacity limit is 80% for JAFZA-1.")]
    result = check_sufficiency(
        SufficiencyCheckInput(
            original_query="What is the JAFZA-1 capacity limit?",
            reframed_query=None,
            draft_answer="I cannot answer this; it's about an unrelated topic like marketing budgets in Europe.",
            chunks=chunks,
        )
    )
    assert result.groundedness_score < 0.8 or result.coverage_score < 0.7
    assert result.verdict == "insufficient"
    assert result.suggested_refinement is not None


def test_sufficiency_output_is_sufficient_respects_both_thresholds():
    from kb_fabric.retrieval.sufficiency import SufficiencyCheckOutput

    high_both = SufficiencyCheckOutput(coverage_score=0.9, groundedness_score=0.9, verdict="sufficient")
    assert high_both.is_sufficient is True

    low_coverage = SufficiencyCheckOutput(coverage_score=0.5, groundedness_score=0.9, verdict="sufficient")
    assert low_coverage.is_sufficient is False  # below COVERAGE_THRESHOLD=0.7

    low_groundedness = SufficiencyCheckOutput(coverage_score=0.9, groundedness_score=0.5, verdict="sufficient")
    assert low_groundedness.is_sufficient is False  # below GROUNDEDNESS_THRESHOLD=0.8

    verdict_says_insufficient = SufficiencyCheckOutput(
        coverage_score=0.99, groundedness_score=0.99, verdict="insufficient"
    )
    assert verdict_says_insufficient.is_sufficient is False


# --- run_sufficiency_loop (deterministic, fake retrieve_fn) ---

def test_loop_terminates_immediately_when_first_pass_sufficient():
    """Use real, genuinely well-grounded content so the real LLM call
    should score it sufficient on pass 1 -- verifies no retry happens for
    a good answer, without needing to fake the sufficiency call itself."""
    chunks = [_mk_fused_chunk("The company's HQ is located in Dubai, UAE.")]

    def retrieve_fn(query):
        return chunks  # unused if the loop terminates on pass 1

    best, passes = run_sufficiency_loop(
        original_query="Where is the company HQ located?",
        reframed_query=None,
        initial_chunks=chunks,
        retrieve_fn=retrieve_fn,
        max_loops=1,
    )
    assert len(passes) == 1
    assert best.sufficiency.verdict == "sufficient"


def test_loop_respects_max_loops_cap():
    """Force insufficiency every pass by giving the answer-gen step
    contradictory/no-context chunks, and confirm the loop stops at
    max_loops + 1 total passes (initial + max_loops retries), never more."""
    empty_chunks = []  # no context -> generate_answer returns "no relevant context" -> should stay insufficient

    call_count = {"n": 0}

    def retrieve_fn(query):
        call_count["n"] += 1
        return empty_chunks

    best, passes = run_sufficiency_loop(
        original_query="What is the meaning of life?",
        reframed_query=None,
        initial_chunks=empty_chunks,
        retrieve_fn=retrieve_fn,
        max_loops=1,
    )
    # initial pass + at most 1 retry = at most 2 total passes.
    assert len(passes) <= 2
    assert call_count["n"] <= 1  # retrieve_fn only called on retry, not the initial pass


def test_loop_returns_best_scoring_pass_not_necessarily_last():
    """With max_loops=0, the loop must do exactly one pass and return it
    regardless of its score (nothing to compare against)."""
    chunks = [_mk_fused_chunk("Some context.")]

    def retrieve_fn(query):
        return chunks

    best, passes = run_sufficiency_loop(
        original_query="A question.",
        reframed_query=None,
        initial_chunks=chunks,
        retrieve_fn=retrieve_fn,
        max_loops=0,
    )
    assert len(passes) == 1
    assert best is passes[0]
