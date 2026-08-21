"""Slice 2 retrieval config -- module-level constants, same pattern as
Slice 1's kb_fabric.models.EMBEDDING_DIM / kb_fabric.pipeline.chunk.CHUNK_SIZE.
Values proposed by Hermes and confirmed by user 2026-08-20; see
Landmark_Knowledgebase_Slice2_Retrieval_Design.md section 8.1 for rationale.
"""

# --- Candidate generation (Phase 2.1/2.2) ---
VECTOR_TOP_N = 20
KEYWORD_TOP_N = 20

# --- RRF fusion (Phase 2.3) ---
RRF_K = 60

# --- Context assembly (Phase 2.4) ---
CONTEXT_MAX_CHUNKS = 8
CONTEXT_MAX_TOKENS = 4000

# --- Sufficiency loop (Phase 2.6, per HLD 8.3a) ---
COVERAGE_THRESHOLD = 0.7
GROUNDEDNESS_THRESHOLD = 0.8
MAX_RETRIEVAL_LOOPS = 1

# --- Authorization (Phase 2.0 decision: SKIPPED for Slice 2) ---
# Confirmed by user 2026-08-20: no authz enforcement in Slice 2. This is a
# real, tracked gap (see design doc section 2, HLD section 19 item 9), not
# an oversight. AUTHZ_ENFORCED=False must surface in every /query response
# (transparency.authorization field) and in a startup log warning -- never
# silently true-by-omission.
AUTHZ_ENFORCED = False
