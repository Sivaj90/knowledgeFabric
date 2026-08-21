"""Phase 2.2: query-side keyword search (Postgres FTS).

Uses the generated content_tsv column (Phase 1.2) + plainto_tsquery,
ranked by ts_rank. Also defines the authz_filter hook per the Phase 2.0
decision: authz is NOT enforced in Slice 2 (see
Landmark_Knowledgebase_Slice2_Retrieval_Design.md section 2, HLD section 19
item 9) -- the query builder accepts an optional filter clause so real
enforcement later is a parameter, not a rewrite.
"""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from kb_fabric.models import Chunk
from kb_fabric.retrieval.config import AUTHZ_ENFORCED, KEYWORD_TOP_N
from kb_fabric.retrieval.vector_search import ScoredChunk


def build_authz_filter(auth_token: dict | None) -> ColumnElement | None:
    """Returns a SQLAlchemy WHERE clause for the HLD 8.1 step 2 hard
    pre-filter, or None if authz is not enforced.

    Phase 2.0 decision (confirmed 2026-08-20): AUTHZ_ENFORCED=False for
    Slice 2 -- this always returns None right now. The function exists so
    activating real enforcement later means implementing the filter
    expression here and flipping AUTHZ_ENFORCED, not restructuring every
    query call site that accepts authz_filter=... .
    """
    if not AUTHZ_ENFORCED:
        return None
    # Real filter (HLD 8.1 step 2), NOT implemented in Slice 2:
    #   (chunk.functions overlaps token.functions_allowed
    #    OR chunk.project_ids overlaps token.project_grants)
    #   AND rank(chunk.effective_tier) <= rank(token.classification_ceiling)
    raise NotImplementedError(
        "AUTHZ_ENFORCED=True but the real filter expression is not implemented -- "
        "Slice 2 scope explicitly excludes authz enforcement (see design doc section 2)."
    )


def keyword_search(
    session: Session,
    query: str,
    top_n: int = KEYWORD_TOP_N,
    authz_filter: ColumnElement | None = None,
) -> list[ScoredChunk]:
    """Full-text search against chunks.content_tsv, ranked by ts_rank.

    `authz_filter` is accepted but unused in Slice 2 (always None per the
    Phase 2.0 decision) -- present in the signature now so wiring a real
    filter later doesn't change every caller.
    """
    if not query or not query.strip():
        return []

    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(Chunk.content_tsv, tsquery)

    stmt = (
        select(Chunk, rank.label("rank_score"))
        .where(Chunk.content_tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(top_n)
    )
    if authz_filter is not None:
        stmt = stmt.where(authz_filter)

    results = session.execute(stmt).all()
    return [
        ScoredChunk(chunk=row.Chunk, rank=i + 1, score=float(row.rank_score))
        for i, row in enumerate(results)
    ]
