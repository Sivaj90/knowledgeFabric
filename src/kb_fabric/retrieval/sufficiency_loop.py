"""Bounded retry loop tying sufficiency check + a retrieval retry pass
together (HLD 8.3a). This module owns the loop control; it calls back into
the retrieval pipeline (vector/keyword search + fusion + arbitration +
answer generation) for each pass rather than duplicating that logic.
"""

from dataclasses import dataclass

from kb_fabric.retrieval.answer_gen import AnswerGenInput, AnswerGenOutput, PreviousAttempt, generate_answer
from kb_fabric.retrieval.config import MAX_RETRIEVAL_LOOPS
from kb_fabric.retrieval.fusion import FusedChunk
from kb_fabric.retrieval.sufficiency import SufficiencyCheckInput, SufficiencyCheckOutput, check_sufficiency


@dataclass
class RetrievalPass:
    """One retrieval+answer-gen+sufficiency-check pass, kept for the
    final response's transparency block and for selecting the
    best-scoring pass if the loop exhausts its cap without reaching
    'sufficient' (HLD 8.3a: return best-available, not just the last)."""

    query_used: str
    chunks: list[FusedChunk]
    answer: AnswerGenOutput
    sufficiency: SufficiencyCheckOutput


def run_sufficiency_loop(
    original_query: str,
    reframed_query: str | None,
    initial_chunks: list[FusedChunk],
    retrieve_fn,
    max_loops: int = MAX_RETRIEVAL_LOOPS,
) -> tuple[RetrievalPass, list[RetrievalPass]]:
    """Runs answer-gen -> sufficiency-check, retrying up to `max_loops`
    additional times with a refined query if insufficient.

    `retrieve_fn` is a callable `(query: str) -> list[FusedChunk]` -- the
    caller (Phase 2.7's endpoint code) supplies the actual retrieval
    pipeline (vector/keyword search + fusion + arbitration) so this module
    stays focused purely on loop control, not retrieval mechanics.

    Returns (best_pass, all_passes). best_pass is the highest
    coverage+groundedness pass seen, not necessarily the last one tried --
    per HLD 8.3a: "return the best-available answer... if the cap is hit
    without reaching sufficient."
    """
    query_for_pass = reframed_query or original_query
    chunks_for_pass = initial_chunks
    passes: list[RetrievalPass] = []
    previous_attempt: PreviousAttempt | None = None

    attempt = 0
    while True:
        answer = generate_answer(
            AnswerGenInput(query=query_for_pass, chunks=chunks_for_pass, previous_attempt=previous_attempt)
        )
        sufficiency = check_sufficiency(
            SufficiencyCheckInput(
                original_query=original_query,
                reframed_query=query_for_pass if query_for_pass != original_query else None,
                draft_answer=answer.answer,
                chunks=chunks_for_pass,
            )
        )
        passes.append(
            RetrievalPass(query_used=query_for_pass, chunks=chunks_for_pass, answer=answer, sufficiency=sufficiency)
        )

        if sufficiency.is_sufficient or attempt >= max_loops:
            break

        # Insufficient and budget remains: retry with the refined query.
        previous_attempt = PreviousAttempt(
            draft_answer=answer.answer, missing_aspects=sufficiency.missing_aspects
        )
        query_for_pass = sufficiency.suggested_refinement or query_for_pass
        chunks_for_pass = retrieve_fn(query_for_pass)
        attempt += 1

    best_pass = max(
        passes, key=lambda p: (p.sufficiency.coverage_score + p.sufficiency.groundedness_score)
    )
    return best_pass, passes
