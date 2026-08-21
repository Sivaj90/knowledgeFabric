"""Phase 2.6: post-answer sufficiency check + bounded retry loop
(HLD 8.3a). A separate, single-purpose LLM call, distinct from query
planning and answer generation (design doc section 6). Returns a
structured JSON verdict (coverage_score, groundedness_score,
missing_aspects, verdict, suggested_refinement), not free-form judgment,
so the loop can act on it deterministically.
"""

import json
from dataclasses import dataclass, field

from openai import OpenAI

from kb_fabric.config import get_settings
from kb_fabric.retrieval.config import COVERAGE_THRESHOLD, GROUNDEDNESS_THRESHOLD
from kb_fabric.retrieval.fusion import FusedChunk


@dataclass
class SufficiencyCheckInput:
    """Explicit typed input -- design doc section 6.1. Needs the ORIGINAL
    query (not just any reframed version -- sufficiency is judged against
    what the user actually asked) plus the reframed query if it differs,
    the draft answer, and the chunk set that produced it (to check
    groundedness against, not just coverage)."""

    original_query: str
    reframed_query: str | None
    draft_answer: str
    chunks: list[FusedChunk]


@dataclass
class SufficiencyCheckOutput:
    coverage_score: float
    groundedness_score: float
    missing_aspects: list[str] = field(default_factory=list)
    verdict: str = "insufficient"  # "sufficient" | "insufficient"
    suggested_refinement: str | None = None

    @property
    def is_sufficient(self) -> bool:
        return (
            self.verdict == "sufficient"
            and self.coverage_score >= COVERAGE_THRESHOLD
            and self.groundedness_score >= GROUNDEDNESS_THRESHOLD
        )


_SYSTEM_PROMPT = """You are a strict quality-control judge for a RAG (retrieval-augmented
generation) answer. Score the draft answer against the question and the
context chunks it was generated from.

Return ONLY a JSON object, no other text:
{
  "coverage_score": <0.0-1.0, how fully the context addresses the question>,
  "groundedness_score": <0.0-1.0, how well the answer is supported by the context, no unsupported claims>,
  "missing_aspects": [<short strings naming what's unaddressed, empty list if none>],
  "verdict": "sufficient" or "insufficient",
  "suggested_refinement": <a refined/expanded query string to try next, or null if sufficient>
}
"""


def check_sufficiency(input_data: SufficiencyCheckInput) -> SufficiencyCheckOutput:
    """Single-purpose LLM call: scoring/verdict only -- no answer
    regeneration happens here (that's a separate answer_gen retry call if
    triggered)."""
    settings = get_settings()
    client = OpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)

    context_text = "\n\n---\n\n".join(fc.chunk.content for fc in input_data.chunks) or "(no context retrieved)"
    query_block = f"Original question: {input_data.original_query}"
    if input_data.reframed_query and input_data.reframed_query != input_data.original_query:
        query_block += f"\nReframed/searched query: {input_data.reframed_query}"

    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{query_block}\n\nDraft answer: {input_data.draft_answer}\n\nContext used:\n{context_text}",
            },
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    parsed = json.loads(raw)

    return SufficiencyCheckOutput(
        coverage_score=float(parsed.get("coverage_score", 0.0)),
        groundedness_score=float(parsed.get("groundedness_score", 0.0)),
        missing_aspects=parsed.get("missing_aspects", []) or [],
        verdict=parsed.get("verdict", "insufficient"),
        suggested_refinement=parsed.get("suggested_refinement") or None,
    )
