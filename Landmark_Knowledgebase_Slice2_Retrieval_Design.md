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
project_ids=[]`. The full HLD (§6) has Azure AD SSO + signed JWTs minted
by a dedicated authz service — none of that exists yet in this local VPC.

**Decision confirmed by user (2026-08-20): skip authz enforcement entirely
for Slice 2.** No hard pre-filter query, no stub token gating retrieval.
This is a deliberate scope cut, not an oversight — recorded here so it is
never mistaken for "already handled."

**Because this is a real, documented gap against a non-negotiable HLD rule
("early-binding authorization... enforced in code"), it needs explicit,
loud guardrails so it cannot be silently carried into a later slice or a
multi-user deployment:**

- **Every `/query` response includes an authz-status field** (e.g.
  `"authorization": "not_enforced_slice2"`) in the transparency block
  (§7) — not just a log line nobody reads, a field in the actual API
  contract every caller sees on every response.
- **A startup-time log warning** from the FastAPI service on boot:
  something like `AUTHZ NOT ENFORCED — Slice 2 scope, single-user local
  POC only. Do not expose this endpoint beyond localhost / a trusted
  operator.` — so it's impossible to run the service without seeing it.
- **This item is tracked as a named, standing open item** in both this
  design doc (here) and `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`
  §19 (added below) — carried forward explicitly into Slice 3's scoping
  discussion (real RBAC/classification) rather than left to be
  rediscovered.
- **The query code is still structured to make adding the real filter a
  small change, not a rewrite:** the retrieval query builder takes an
  optional `authz_filter` parameter that is `None` in Slice 2 (skipping
  the `WHERE` clause additions entirely) — when real tokens exist, passing
  a real filter object activates the clause from HLD §8.1 step 2 without
  restructuring the query path. This is a code-shape decision now, not a
  functional authz control — worth being precise about that distinction
  so nobody mistakes "the hook exists" for "it's enforced."

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
- No authz `WHERE` clause added in Slice 2, per §2 above — every chunk in
  the DB is a retrieval candidate (acceptable only because §2's guardrails
  make this an explicit, visible, tracked gap rather than a silent one)

## 4. RRF fusion + arbitration

- **RRF formula:** `score(chunk) = sum over lists L containing chunk of
  1/(k + rank_in_L(chunk))`, `k` a config constant (60 is the common RRF
  default, worth stating explicitly rather than picking silently).
- **Source-authority re-weighting** requires a per-chunk "authority" value
  — Slice 1 doesn't have a real source-authority signal yet (no canonical
  vs. informal tier assigned to chunks). **Confirmed by user: stub this**
  — hardcode a flat authority weight for everything (same "no-op now,
  real value later" pattern as Slice 1's classification tier) so the RRF
  *formula* is correct and testable even before real authority data
  exists. A flat weight means this term contributes nothing to ranking
  yet — worth noting in the transparency block/tests so a reviewer
  doesn't mistake "authority-weighted" for "authority data exists."
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

### 6.1 Not losing information across the three separate calls

Splitting into three focused calls is a token-efficiency choice, not a
license to drop context each call actually needs. **Explicit rule: each
call gets exactly what it needs to do its job well — no less — passed as
structured, explicit inputs, never assumed via "the model remembers."**
There is no shared conversation/session across these calls (they may even
hit different model instances/replicas) — everything each call needs must
be in that call's own prompt.

| Call | Needs as input | Must NOT need |
|---|---|---|
| **1. Query planner** | Original user query; conversation history if multi-turn (for pronoun/context resolution); the token's `functions_allowed`/`classification_ceiling` shape (not real values in Slice 2, per §2, but the *field* so reframing never accidentally implies cross-function scope) | Retrieved chunks (nothing retrieved yet) |
| **2. Answer generation** | The (possibly reframed) query from call 1 **verbatim** — not re-derived, not summarized, passed through as a literal string; the final ranked+budget-truncated chunk set from retrieval/arbitration, each chunk with its `chunk_id`+`source_uri` for citation; if this is a **retry pass** (post-sufficiency, see below), the previous pass's `missing_aspects` so generation actively tries to address the gap, not blindly repeat the same answer | The query planner's *reasoning* (only its output — routing decision + reframed query — needs to survive, not its internal deliberation) |
| **3. Sufficiency check** | The **original** user query (not just the reframed one — sufficiency is judged against what the user actually asked) **and** the reframed query if they differ (so the check can tell "the reframe missed something" apart from "the reframe was fine but retrieval came up short"); the draft answer from call 2; the chunk set that produced it (to check groundedness against, not just coverage against the query) | The query planner's routing rationale (not needed to judge the answer) |

**Concrete mechanism, not just a principle:** every LLM call in this
pipeline is defined with an explicit typed input schema (e.g. a Pydantic
model: `QueryPlannerInput`, `AnswerGenInput`, `SufficiencyCheckInput`) —
the orchestrating code assembles that struct from the *outputs of prior
steps plus the original request*, never from an implicit "whatever's in
scope." This is the same design pattern already used for `DocumentEnvelope`
in Slice 1 (Phase 1.3) — a typed contract at each pipeline boundary, so a
reviewer (or a test) can see exactly what crosses each seam instead of
having to trust that nothing leaked or got dropped.

**On retry (sufficiency triggers another pass):** the retry's answer-gen
call additionally receives the **previous attempt's full context** —
previous draft answer, previous chunk set, and the sufficiency verdict's
`missing_aspects` — not a fresh start that's forgotten what was already
tried. Without this, a retry could plausibly re-run the exact same failed
search and burn the loop budget with zero improvement, which would make
the whole sufficiency mechanism pointless.

**Testability follow-on:** since each call has an explicit typed input,
Phase 2.5/2.6's unit tests (per the phase breakdown below) can construct
that input directly and assert the call's output, without needing to run
the full multi-call pipeline for every test case — this is a side benefit
of the "don't lose anything, make it explicit" design, not just a
correctness requirement.

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
    "groundedness_score": 0.91,
    "authorization": "not_enforced_slice2"
  }
}
```

Exact schema is a Phase 2.x implementation detail, not fixed here — this
section exists so the shape is discussed before code, not invented
mid-implementation.

## 8. Proposed phase breakdown (mirrors Slice 1's Phase 1.0-1.7 structure)

| Phase | Deliverable |
|---|---|
| 2.0 | Confirm remaining open decisions (top-N candidates per engine, context budget size/token limit, RRF `k` constant) with user before building — the authz (§2) and authority-weight (§4) decisions are already confirmed (2026-08-20) |
| 2.1 | Query-side vector search (embed query, pgvector similarity query) + unit tests against real DB |
| 2.2 | Query-side keyword search (FTS query) + RRF fusion query builder shaped with the `authz_filter=None` hook from §2 (not enforced, but structured so it activates later without a rewrite) + unit tests |
| 2.3 | RRF fusion + arbitration (most-recent-wins-in-tier) + unit tests |
| 2.4 | Context assembly (budget truncation) + answer generation (grounded, cited) with the typed `AnswerGenInput` contract from §6.1 + unit tests |
| 2.5 | Query planner (engine routing + reframe) as a separate LLM call with typed `QueryPlannerInput`/output, wired in front of 2.1-2.2 |
| 2.6 | Sufficiency check (scored, bounded retry loop) as a separate LLM call with typed `SufficiencyCheckInput`/output, wired after 2.4, including the retry-context passthrough from §6.1 |
| 2.7 | FastAPI `/query` endpoint wrapping the above, with the transparency block (incl. `authorization: not_enforced_slice2` field) and the startup-time authz warning log |
| 2.8 | Unit testing pass (per-component, real DB/LLM calls per this project's established pattern — including constructing each LLM call's typed input directly per the §6.1 testability note) |
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
