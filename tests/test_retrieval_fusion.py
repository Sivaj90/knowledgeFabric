"""Phase 2.3 tests: RRF fusion + arbitration. Uses in-memory ScoredChunk
fixtures (not real DB round-trips) since fusion/arbitration operate purely
on already-fetched chunk objects and ranks -- no new DB queries happen
here, so a constructed Chunk/Document pair is sufficient and faster than
re-running vector_search/keyword_search for pure fusion-logic tests. Live
end-to-end verification (fusion over real search results) was run manually
against the real DB -- see PR/commit notes.
"""

import uuid
from datetime import datetime, timedelta, timezone

from kb_fabric.models import Chunk, Document
from kb_fabric.retrieval.fusion import FLAT_AUTHORITY_WEIGHT, arbitrate, rrf_fuse
from kb_fabric.retrieval.vector_search import ScoredChunk


def _mk_document(superseded_by=None, last_modified=None) -> Document:
    doc = Document(
        document_id=uuid.uuid4(),
        source_system="local_folder",
        source_uri="file:///data/raw/fusion-test.md",
        content_hash="sha256:" + "e" * 64,
        authors=[],
        superseded_by=superseded_by,
        last_modified=last_modified or datetime.now(timezone.utc),
    )
    return doc


def _mk_chunk(document: Document, content: str, chunk_id=None) -> Chunk:
    chunk = Chunk(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=document.document_id,
        source_system="local_folder",
        source_uri=document.source_uri,
        content=content,
        content_hash="sha256:" + "f" * 64,
        functions=[],
        classification_tier="internal",
        effective_tier="internal",
        authors=[],
        project_ids=[],
        entities=[],
        is_public=False,
        chunk_acl_tokens=[],
        version=1,
        last_modified=document.last_modified,
    )
    chunk.document = document  # wire the relationship without a DB round-trip
    return chunk


def test_rrf_fuse_dedupes_chunk_in_both_lists():
    doc = _mk_document()
    shared_chunk = _mk_chunk(doc, "appears in both engines")
    vector_only_chunk = _mk_chunk(doc, "vector only")

    vector_results = [
        ScoredChunk(chunk=shared_chunk, rank=1, score=0.1),
        ScoredChunk(chunk=vector_only_chunk, rank=2, score=0.3),
    ]
    keyword_results = [ScoredChunk(chunk=shared_chunk, rank=1, score=0.9)]

    fused = rrf_fuse(vector_results, keyword_results)

    # shared_chunk must appear exactly once, not twice.
    ids = [f.chunk.chunk_id for f in fused]
    assert ids.count(shared_chunk.chunk_id) == 1
    assert len(fused) == 2


def test_rrf_fuse_chunk_in_both_lists_scores_higher_than_single_engine():
    doc = _mk_document()
    shared_chunk = _mk_chunk(doc, "in both")
    single_chunk = _mk_chunk(doc, "vector only, same rank")

    vector_results = [
        ScoredChunk(chunk=shared_chunk, rank=1, score=0.1),
        ScoredChunk(chunk=single_chunk, rank=2, score=0.1),
    ]
    keyword_results = [ScoredChunk(chunk=shared_chunk, rank=1, score=0.1)]

    fused = rrf_fuse(vector_results, keyword_results)
    fused_by_id = {f.chunk.chunk_id: f for f in fused}

    assert fused_by_id[shared_chunk.chunk_id].rrf_score > fused_by_id[single_chunk.chunk_id].rrf_score
    assert fused_by_id[shared_chunk.chunk_id].engines == ["keyword", "vector"]
    assert fused_by_id[single_chunk.chunk_id].engines == ["vector"]


def test_rrf_fuse_sorted_descending_by_score():
    doc = _mk_document()
    high = _mk_chunk(doc, "rank 1 both engines")
    low = _mk_chunk(doc, "rank 3 vector only")

    vector_results = [
        ScoredChunk(chunk=high, rank=1, score=0.1),
        ScoredChunk(chunk=low, rank=3, score=0.5),
    ]
    keyword_results = [ScoredChunk(chunk=high, rank=1, score=0.1)]

    fused = rrf_fuse(vector_results, keyword_results)
    assert fused[0].chunk.chunk_id == high.chunk_id
    scores = [f.rrf_score for f in fused]
    assert scores == sorted(scores, reverse=True)


def test_rrf_fuse_empty_inputs_returns_empty():
    assert rrf_fuse([], []) == []


def test_flat_authority_weight_does_not_change_relative_order():
    """Confirms the Phase 2.0 stub decision: a flat multiplier on every
    chunk's score must not alter ranking order (that's the whole point of
    it being a no-op stub, not real per-chunk authority weighting)."""
    assert FLAT_AUTHORITY_WEIGHT == 1.0  # explicit: currently a true no-op


def test_arbitrate_drops_superseded_document_chunks():
    newer_doc_id = uuid.uuid4()
    old_doc = _mk_document(superseded_by=newer_doc_id)
    new_doc = _mk_document()

    old_chunk = _mk_chunk(old_doc, "old, superseded version")
    new_chunk = _mk_chunk(new_doc, "new, current version")

    fused = rrf_fuse(
        [ScoredChunk(chunk=old_chunk, rank=1, score=0.1), ScoredChunk(chunk=new_chunk, rank=2, score=0.2)],
        [],
    )
    arbitrated = arbitrate(fused)

    ids = [f.chunk.chunk_id for f in arbitrated]
    assert old_chunk.chunk_id not in ids
    assert new_chunk.chunk_id in ids


def test_arbitrate_recency_tiebreak_within_equal_scores():
    older_time = datetime.now(timezone.utc) - timedelta(days=10)
    newer_time = datetime.now(timezone.utc)

    doc_older = _mk_document(last_modified=older_time)
    doc_newer = _mk_document(last_modified=newer_time)

    older_chunk = _mk_chunk(doc_older, "older chunk, equal score")
    newer_chunk = _mk_chunk(doc_newer, "newer chunk, equal score")
    older_chunk.last_modified = older_time
    newer_chunk.last_modified = newer_time

    # Force identical RRF scores by giving both chunks the same rank in
    # the same single engine.
    same_rank_results_older = [ScoredChunk(chunk=older_chunk, rank=1, score=0.1)]
    same_rank_results_newer = [ScoredChunk(chunk=newer_chunk, rank=1, score=0.1)]

    fused_older = rrf_fuse(same_rank_results_older, [])
    fused_newer = rrf_fuse(same_rank_results_newer, [])
    combined = fused_older + fused_newer

    arbitrated = arbitrate(combined)
    # Same RRF score -> recency tie-break -> newer_chunk first.
    assert arbitrated[0].chunk.chunk_id == newer_chunk.chunk_id
