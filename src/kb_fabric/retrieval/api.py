"""Phase 2.7: FastAPI /query endpoint wiring together the full Slice 2
retrieval pipeline: query planning -> hybrid candidate search -> RRF
fusion -> arbitration -> context assembly -> answer generation ->
sufficiency-scored bounded retry loop -> response with transparency block.

AUTHZ IS NOT ENFORCED (Phase 2.0 decision, confirmed 2026-08-20) -- see
Landmark_Knowledgebase_Slice2_Retrieval_Design.md section 2 and HLD
section 19 item 9. This is loudly surfaced, not silently omitted:
  - a startup warning is logged
  - every response includes transparency.authorization = "not_enforced_slice2"
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from kb_fabric.db import get_sessionmaker
from kb_fabric.retrieval.answer_gen import assemble_context
from kb_fabric.retrieval.config import AUTHZ_ENFORCED
from kb_fabric.retrieval.fusion import FusedChunk, arbitrate, rrf_fuse
from kb_fabric.retrieval.keyword_search import keyword_search
from kb_fabric.retrieval.query_planner import QueryPlannerInput, plan_query
from kb_fabric.retrieval.sufficiency_loop import run_sufficiency_loop
from kb_fabric.retrieval.vector_search import vector_search

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not AUTHZ_ENFORCED:
        logger.warning(
            "AUTHZ NOT ENFORCED -- Slice 2 scope, single-user local POC only. "
            "Do not expose this endpoint beyond localhost / a trusted operator. "
            "See Landmark_Knowledgebase_Slice2_Retrieval_Design.md section 2."
        )
    yield


app = FastAPI(title="Landmark Knowledge Fabric - Query API (Slice 2)", lifespan=_lifespan)


class QueryRequest(BaseModel):
    query: str
    conversation_history: list[str] = []


class Citation(BaseModel):
    chunk_id: str
    source_uri: str
    title: str | None = None


class Transparency(BaseModel):
    engines_used: list[str]
    reframed_query: str | None
    retrieval_passes: int
    coverage_score: float
    groundedness_score: float
    authorization: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    transparency: Transparency


def _retrieve(session, query: str, engines: list[str]) -> list[FusedChunk]:
    """Runs whichever engine(s) the query planner selected, then fuses +
    arbitrates. `engines` is a subset of {"vector", "keyword"} (§2.5's
    QueryPlannerOutput already expands "hybrid" to both)."""
    vec_results = vector_search(session, query) if "vector" in engines else []
    kw_results = keyword_search(session, query) if "keyword" in engines else []
    fused = rrf_fuse(vec_results, kw_results)
    return arbitrate(fused)


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    Session = get_sessionmaker()
    with Session() as session:
        # Step 1: query planning (HLD 8.1 step 0) -- separate LLM call.
        plan = plan_query(
            QueryPlannerInput(query=request.query, conversation_history=request.conversation_history)
        )
        search_query = plan.reframed_query or request.query

        # Step 2-5: candidate search, authz (skipped, see module docstring),
        # RRF fusion, arbitration, context assembly.
        arbitrated = _retrieve(session, search_query, plan.engines)
        initial_chunks = assemble_context(arbitrated)

        def retrieve_fn(refined_query: str) -> list[FusedChunk]:
            return assemble_context(_retrieve(session, refined_query, plan.engines))

        # Step 6-7: answer generation + sufficiency-scored bounded retry
        # loop (HLD 8.3, 8.3a) -- two more separate, single-purpose LLM
        # calls per pass.
        best_pass, all_passes = run_sufficiency_loop(
            original_query=request.query,
            reframed_query=plan.reframed_query,
            initial_chunks=initial_chunks,
            retrieve_fn=retrieve_fn,
        )

        return QueryResponse(
            answer=best_pass.answer.answer,
            citations=[
                Citation(chunk_id=c.chunk_id, source_uri=c.source_uri, title=c.title)
                for c in best_pass.answer.citations
            ],
            transparency=Transparency(
                engines_used=plan.engines,
                reframed_query=plan.reframed_query,
                retrieval_passes=len(all_passes),
                coverage_score=best_pass.sufficiency.coverage_score,
                groundedness_score=best_pass.sufficiency.groundedness_score,
                authorization="not_enforced_slice2" if not AUTHZ_ENFORCED else "enforced",
            ),
        )
