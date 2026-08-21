"""Phase 2.3: RRF fusion + arbitration (HLD 8.1 step 3, 8.2).

RRF formula: score(chunk) = sum over lists L containing chunk of
1/(RRF_K + rank_in_L(chunk)). Source-authority re-weighting is stubbed
(flat weight for every chunk, Phase 2.0 decision confirmed 2026-08-20 --
Slice 1 has no real canonical/informal tier data yet) so the formula is
correct and testable ahead of real authority data existing.

Arbitration (HLD 8.2, POC rule): "most recent wins within a tier". Since
every chunk is currently the same hardcoded tier (internal), this reduces
to "most recent wins" -- but it's still a real, testable behavior via
Document.superseded_by / last_modified (Slice 1 Phase 1.6 versioning),
not a stub.
"""

from dataclasses import dataclass

from kb_fabric.models import Chunk
from kb_fabric.retrieval.config import RRF_K
from kb_fabric.retrieval.vector_search import ScoredChunk

# Flat authority weight for every chunk -- Phase 2.0 stub decision. A
# constant multiplier contributes nothing to relative ranking (multiplying
# every RRF score by the same number doesn't change chunk order), which is
# exactly the point: the formula term exists and is wired, but has zero
# real effect until real per-chunk authority data exists in a later slice.
FLAT_AUTHORITY_WEIGHT = 1.0


@dataclass
class FusedChunk:
    chunk: Chunk
    rrf_score: float
    engines: list[str]  # which engine(s) surfaced this chunk, e.g. ["vector", "keyword"]


def rrf_fuse(
    vector_results: list[ScoredChunk],
    keyword_results: list[ScoredChunk],
    k: int = RRF_K,
) -> list[FusedChunk]:
    """Fuses two ranked candidate lists into one RRF-scored, deduped list,
    re-weighted by the (currently flat/stub) source-authority factor.

    A chunk appearing in both lists gets the sum of its per-list RRF
    contributions (this is the whole point of RRF: reward chunks that
    multiple retrieval strategies agree on), not double-counted as two
    separate entries.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, Chunk] = {}
    engines: dict[str, set[str]] = {}

    for engine_name, results in (("vector", vector_results), ("keyword", keyword_results)):
        for scored in results:
            chunk_id = str(scored.chunk.chunk_id)
            contribution = 1.0 / (k + scored.rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
            chunks[chunk_id] = scored.chunk
            engines.setdefault(chunk_id, set()).add(engine_name)

    fused = [
        FusedChunk(
            chunk=chunks[chunk_id],
            rrf_score=score * FLAT_AUTHORITY_WEIGHT,
            engines=sorted(engines[chunk_id]),
        )
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda fc: fc.rrf_score, reverse=True)
    return fused


def arbitrate(fused: list[FusedChunk]) -> list[FusedChunk]:
    """HLD 8.2 POC rule: drop any chunk whose document has been superseded
    (regardless of RRF rank -- version resolution, not a ranking tweak),
    then break remaining ties by recency within the same tier. Since every
    chunk is currently tier="internal" (Slice 1 hardcode), the tier
    grouping is a no-op for now, but the recency tie-break and the
    superseded-drop are both real, tested behavior.
    """
    # Hard rule: drop chunks belonging to a superseded document version.
    surviving = [fc for fc in fused if fc.chunk.document.superseded_by is None]

    # Soft tie-break: within equal (rounded) RRF scores, prefer more recent
    # last_modified. Real ties are rare with floating-point RRF scores, but
    # the rule is still meaningful once real per-tier authority weights
    # exist and produce genuine ties.
    surviving.sort(key=lambda fc: (fc.rrf_score, fc.chunk.last_modified), reverse=True)
    return surviving
