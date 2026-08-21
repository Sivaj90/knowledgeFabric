"""Phase 2.1: query-side vector search.

Embeds the query via the same LiteLLM proxy Slice 1 uses for ingestion
(landmark-text-embedding-3-large, dimensions=EMBEDDING_DIM=1536 -- same
fix as Slice 1's embed step, see kb_fabric.models.EMBEDDING_DIM docstring),
then runs a pgvector cosine-similarity query against chunks.embedding.

No authz filter applied (Phase 2.0 decision: authz not enforced in
Slice 2 -- see Landmark_Knowledgebase_Slice2_Retrieval_Design.md section 2).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from kb_fabric.models import Chunk
from kb_fabric.pipeline.embed import embed_texts
from kb_fabric.retrieval.config import VECTOR_TOP_N


@dataclass
class ScoredChunk:
    """One candidate chunk plus which engine(s) surfaced it and at what
    rank -- the shape RRF fusion (Phase 2.3) needs as input. `score` is
    engine-native (cosine distance for vector, ts_rank for keyword) --
    NOT comparable across engines directly, which is exactly why RRF
    fuses by rank, not by raw score.
    """

    chunk: Chunk
    rank: int  # 1-indexed position within this engine's result list
    score: float  # engine-native score, informational only


def vector_search(session: Session, query: str, top_n: int = VECTOR_TOP_N) -> list[ScoredChunk]:
    """Embed `query` and return the top_n nearest chunks by cosine distance.

    Returns an empty list for an empty/whitespace-only query rather than
    making a wasted embeddings API call.
    """
    if not query or not query.strip():
        return []

    query_vector = embed_texts([query])[0]

    distance = Chunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(Chunk, distance.label("distance"))
        .where(Chunk.embedding.isnot(None))
        .order_by(distance)
        .limit(top_n)
    )

    results = session.execute(stmt).all()
    return [
        ScoredChunk(chunk=row.Chunk, rank=i + 1, score=float(row.distance))
        for i, row in enumerate(results)
    ]
