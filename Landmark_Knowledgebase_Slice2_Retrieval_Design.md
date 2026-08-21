# Landmark Knowledge Fabric — Slice 2 Tech Design (Retrieval)

**Status:** DRAFT — scoping only, no implementation started.
Companion to `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md` §8
(Retrieval/Arbitration/Answer-gen, source of truth for the design) and
`Landmark_Knowledgebase_Local_VPC_HLD.md` (local-substitution rationale).
This is the "how, in what order" for Slice 2, same structure as
`Landmark_Knowledgebase_Implementation_Plan.md` used for Slice 1: analyze →
tech design → plan → implement → unit test → functional test → deploy
locally (per `AGENTS.md`).

---

## 1. What Slice 2 delivers

A read-only query API on top of Slice 1's capture pipeline:

```
Query (+ authz token)
  -> [query planning: engine routing + reframe]      (HLD §8.1 step 0)
  -> hybrid candidate search (BM25 + vector, parallel) (HLD §8.1 step 1)
  -> HARD authz pre-filter (before ranking)            (HLD §8.1 step 2)
  -> RRF fusion + source-authority/recency re-weight    (HLD §8.1 step 3)
  -> [optional graph expansion -- OUT OF SCOPE, no AGE yet]
  -> context assembly (fixed budget, citations)         (HLD §8.1 step 5)
  -> arbitration (fixed "most recent wins in tier")     (HLD §8.2, POC rule)
  -> answer generation (grounded, cited)                (HLD §8.3)
  -> [sufficiency check: coverage/groundedness scoring, bounded retry loop]
                                                          (HLD §8.3a)
  -> response + "why you're seeing this" transparency block
```

**Explicitly OUT of Slice 2 scope** (deferred to later slices, per user's
phased approach and the HLD's own phasing in §17):
- Apache AGE graph store / graph expansion (HLD §8.1 step 4) — no graph
  data exists yet (Slice 1 never wrote graph edges); building graph
  expansion against an empty graph is pointless work
- Real RBAC/classification (Slice 1 hardcodes `internal` tier for
  everything) — the authz *filter mechanics* (§2 below) are built now
  since retrieval needs them, but the *real classifier* that assigns
  real tiers/functions to chunks is a separate slice
- Cross-encoder reranker (Cohere/bge) — HLD marks this `[END]`, not `[POC]`
- Next.js UI — API only, no frontend in this slice
- RAGAS/Azure AI Evaluation periodic batch layer (HLD §15) — the runtime
  sufficiency score (§4 below) ships in Slice 2; the offline batch
  evaluation tool is a separate, still-undecided item

## 2. Authorization — the hardest correctness requirement

Slice 1 hardcoded every chunk to `is_public=false, chunk_acl_tokens=[],
classification_tier=internal, effective_tier=internal, functions=[],
project_ids=[]`. Retrieval must still enforce the HARD pre-filter (HLD
§8.1 step 2) even though every chunk currently looks identical — the
mechanism has to be correct now so it's a no-op change (not a rewrite) once
Slice 1's classify step gets real values in a later slice.

**Decision needed before implementation starts:** where does the caller's
authz token come from in Slice 2? The full HLD (§6) has Azure AD SSO +
signed JWTs minted by a dedicated authz service — none of that exists yet.
Two honest options for Slice 2's scope:
- **(a) Stub token** — a hardcoded/config-driven fake token (e.g. one
  functions_allowed list, one classification_ceiling) passed to every
  query, just enough to prove the filter *mechanism* works, matching
  Slice 1's "hardcode now, real value later" pattern.
- **(b) Skip authz entirely for Slice 2, flag it explicitly** — riskier:
  a working query API that skips a documented non-negotiable design rule
  (HLD: "early-binding authorization... enforced in code") is exactly the
  kind of gap that's easy to forget to close later.

**Recommendation: (a).** It costs almost nothing (a hardcoded token dict)
and keeps the "authz is never optional" rule intact end-to-end, even in a
single-user local POC. Flagging as an open item for your confirmation
before Phase 2.2 (below) starts, not deciding unilaterally.

## 3. Candidate generation — BM25 + vector, in parallel

Both engines already exist from Slice 1's schema (Phase 1.2): pgvector
HNSW index + generated `content_tsv` GIN index on `chunks`. Slice 2 adds
the actual **query-side** code:
- Vector: embed the query via the same LiteLLM proxy
  (`landmark-text-embedding-3-large`, `dimensions=1536` — same fix as
  Slice 1's embed step) → `ORDER BY embedding <=> :query_vec` (cosine)
- Keyword: `plainto_tsquery('english', :query) @@ content_tsv`,
  ranked by `ts_rank`
- Both run in parallel (asyncio or two sequential queries — a decision
  for the plan, not the design; Postgres can serve both from one
  connection pool either way)
- Each returns its own top-N (config value, not hardcoded — same pattern
  as Slice 1's `EMBEDDING_DIM`/`CHUNK_SIZE` module-level constants)

## 4. RRF fusion + arbitration

- **RRF formula:** `score(chunk) = sum over lists L containing chunk of
  1/(k + rank_in_L(chunk))`, `k` a config constant (60 is the common RRF
  default, worth stating explicitly rather than picking silently).
- **Source-authority re-weighting** requires a per-chunk "authority" value
  — Slice 1 doesn't have a real source-authority signal yet (no canonical
  vs. informal tier assigned to chunks). POC option: hardcode a flat
  authority weight for everything (same "no-op now, real value later"
  pattern as authz) so the RRF *formula* is correct and testable even
  before real authority data exists.
- **Arbitration (HLD §8.2, POC rule):** "most recent wins within a tier"
  — since every chunk is currently the same tier (`internal`), Slice 2's
  arbitration effectively becomes "most recent wins" full stop until real
  tiers exist. This is a real, testable behavior (verify via
  `superseded_by`/`last_modified`, which Slice 1's Phase 1.6 versioning
  work already populates) — not a stub.

## 5. Context assembly + answer generation

- Fixed context budget: max chunk count AND/OR max token budget (config,
  not hardcoded) — enforce via truncating the ranked list before it's
  handed to the answer-gen LLM call.
- Answer-gen: separate LLM call from query planning/sufficiency check (per
  user's stated preference — keep each LLM call single-purpose/lean rather
  than one combined mega-prompt, where reasonably possible). Cites chunk
  IDs; citations map back to `source_uri` for the transparency block.
- Prompt-injection posture: retrieved content is data in the prompt, never
  instructions — system prompt and retrieved context kept in clearly
  separated message roles/sections, matching HLD §8.3/§15's stated POC
  posture (prompt-level only, no dedicated content-safety tool yet).

## 6. Query planning + sufficiency loop (HLD §8.1 step 0, §8.3a)

Already designed in the HLD (added this session, previous turns):
- **Query planner** — separate, single-purpose LLM call: decides
  engine routing (vector/keyword/hybrid/vector+graph — graph option is
  moot until AGE exists, so effectively vector/keyword/hybrid for Slice 2)
  and whether to reframe the query. Logged, not silent.
- **Sufficiency check** — separate, single-purpose LLM call after answer
  generation: structured JSON verdict (`coverage_score`,
  `groundedness_score`, `missing_aspects`, `verdict`,
  `suggested_refinement`). Config: `coverage_threshold=0.7`,
  `groundedness_threshold=0.8`, `max_retrieval_loops=1` (POC defaults,
  per-function overridable at end-state).
- **Both are genuinely separate LLM calls from answer generation** — three
  distinct, focused calls per query in the worst case (planner → answer-gen
  → sufficiency), not one giant combined prompt. This directly follows the
  user's stated preference for lean, single-purpose LLM calls, and also
  keeps each step's JSON output schema simple and independently testable.

## 7. API surface

FastAPI `/query` endpoint (HLD's stated framework choice, local VPC HLD
§ noted "FastAPI: not yet built in this slice" — Slice 2 is where it gets
built). Minimal shape for Slice 2:

```
POST /query
{
  "query": "...",
  "auth_token": { ... stub token, see §2 ... }
}
->
{
  "answer": "...",
  "citations": [{"chunk_id": "...", "source_uri": "...", "..."}],
  "transparency": {
    "engines_used": ["vector", "keyword"],
    "reframed_query": null,
    "retrieval_passes": 1,
    "coverage_score": 0.85,
    "groundedness_score": 0.91
  }
}
```

Exact schema is a Phase 2.x implementation detail, not fixed here — this
section exists so the shape is discussed before code, not invented
mid-implementation.

## 8. Proposed phase breakdown (mirrors Slice 1's Phase 1.0-1.7 structure)

| Phase | Deliverable |
|---|---|
| 2.0 | Confirm open decisions (§2 authz-stub approach, §4 authority-weight stub, top-N/budget config defaults) with user before building |
| 2.1 | Query-side vector search (embed query, pgvector similarity query) + unit tests against real DB |
| 2.2 | Query-side keyword search (FTS query) + hard authz pre-filter (stub token) + unit tests |
| 2.3 | RRF fusion + arbitration (most-recent-wins-in-tier) + unit tests |
| 2.4 | Context assembly (budget truncation) + answer generation (grounded, cited) + unit tests |
| 2.5 | Query planner (engine routing + reframe) as a separate LLM call, wired in front of 2.1-2.2 |
| 2.6 | Sufficiency check (scored, bounded retry loop) as a separate LLM call, wired after 2.4 |
| 2.7 | FastAPI `/query` endpoint wrapping the above, with the transparency block |
| 2.8 | Unit testing pass (per-component, real DB/LLM calls per this project's established pattern) |
| 2.9 | Functional testing pass (end-to-end real queries against real ingested Slice 1 data) |
| 2.10 | Local deployment (systemd unit for the FastAPI service, alongside the existing Celery worker) |

This mirrors Slice 1's lifecycle exactly (analyze → design → plan →
implement → unit test → functional test → deploy) but broken into smaller
phases since retrieval has more moving, LLM-call-heavy parts than the
capture pipeline did.

## 9. Known challenges (carried forward from the earlier HLD discussion,
restated here so they're visible in the design doc itself, not only buried
in the implementation plan's "Next slice" section)

1. Latency stacking — 3 sequential LLM calls per query in the worst case
   (planner → answer-gen → sufficiency), each adding real wall-clock time
   on top of the DB retrieval itself.
2. Cost stacking — same shape, for token spend.
3. Quality score exists (coverage/groundedness) but thresholds (0.7/0.8)
   are reasoned defaults, not yet validated against real query traffic.
4. Reframed queries complicate any future query-result cache design.
5. Transparency block needs a real UI/API contract, not just "log it."
6. The sufficiency check's own false-positive/negative rate is an
   unmeasured new failure mode until real usage exists.

No code written yet — this document is the design/plan step; implementation
starts only after Phase 2.0's open decisions are confirmed.
