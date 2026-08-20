# Landmark Enterprise Knowledge Fabric — Local VPC Implementation HLD

**Companion to, not a replacement for,
`Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`.** That document is the
committed production/Azure-leaning architecture and remains the design of
record for the platform. This document describes **what we actually build
and run in this local VPC sandbox** — a substitution-set proof of concept —
and, explicitly, **why each substitution differs from the production HLD**,
so nobody mistakes a local shortcut for a production decision.

**Status:** DRAFT — being written alongside first implementation work.

---

## 1. Why a separate document

The production HLD assumes Azure AD, Azure OpenAI, Azure Blob, AKS, and real
SharePoint/Azure DevOps connectivity. None of that exists in this VPC. Every
substitution below is a **deliberate, temporary stand-in** to prove the
platform's core mechanics (capture → classify → chunk → embed → retrieve →
ground) without cloud dependencies or enterprise source access. Nothing here
overrides a production decision in the main HLD; where the two differ, the
production HLD wins for anything destined for Azure/AKS.

## 2. Component substitution map

| HLD component (production) | Local VPC substitute | Why it differs |
|---|---|---|
| SharePoint / Azure DevOps / Teams (Microsoft Graph API, ADO REST) | Local filesystem folder-watcher reading `data/raw/` | No enterprise source access from this VPC; folder mimics the same "Document envelope" contract (content + stub ACL + metadata) so the real connector can swap in later without touching downstream code |
| Azure AD (Entra ID) SSO + RBAC | **Deferred for V0** — no auth substitute yet; single implicit "internal" tier for every chunk | Explicitly agreed with user: no fake RBAC yet, add real classification/authz once capture pipeline works end-to-end |
| Azure Blob Storage (raw originals) | Local filesystem (`data/raw/` originals, `data/processed/` archival copy) | No Azure subscription in this VPC |
| Azure Key Vault (secrets) | `.env` file (local, gitignored) | No Azure Key Vault available; acceptable for a local POC, NOT acceptable for anything deployed further |
| AKS (hosting) | Native systemd services + Python venv processes on this VM | User explicitly chose native processes over Docker/containers for this phase |
| Azure OpenAI (chat + embeddings) | **Same models, different transport** — Landmark's internal LiteLLM proxy (`https://lmlitellm.landmarkgroup.com`), model ids `gpt-5.5` (= HLD's "GPT-5.5 mini") and `landmark-text-embedding-3-large` | Not a substitution really — this is the same committed model choice, just reached via the LiteLLM proxy already configured for this environment rather than direct Azure OpenAI endpoints |
| Apache AGE (graph store) | **Deferred** — no graph store in this first slice | AGE requires compiling from source on Oracle Linux 9 (not in default dnf repos); user chose to de-risk the core pipeline first and add the graph layer once Postgres+pgvector+FTS+Celery is proven |
| Azure DevOps Pipelines (CI/CD) | Manual / none yet | Out of scope for first implementation slice |
| OpenTelemetry + Prometheus + Grafana + Loki | **Deferred** — plain logging only for V0 | Out of scope for first slice; add once core pipeline is stable |

## 3. What IS the same as production HLD

- **Golden rules still apply in code**, even without the full authz layer:
  chunk is the atomic unit; content is data never instructions; everything
  logged (even if only to plain logs for now).
- **Chunk metadata schema** — same fields as HLD §7.4 (functions[], tier,
  effective_tier, project_ids, etc.) populated with placeholder/defaults
  where the real source doesn't exist yet (e.g. `source_system: "local-fs"`,
  `classification_tier: "internal"` hardcoded).
- **Model choice** — GPT-5.5 mini + landmark-text-embedding-3-large, exactly
  as committed in the production HLD.
- **Retrieval algorithm** — hybrid BM25+vector, RRF fusion, planned to match
  HLD §8.1 once retrieval work starts (not part of the first capture-only
  slice).

## 4. Scope of this first implementation slice

Full V0/L0 **capture pipeline only** (no query/retrieval API yet):
local folder connector → parse (Unstructured.io, docx/pdf/md/pptx + Tesseract
OCR fallback) → hardcoded classify (`internal` tier) → chunk (LangChain) →
embed (landmark-text-embedding-3-large via LiteLLM) → write to Postgres
(metadata, system of record) + pgvector (vector) + Postgres FTS (keyword).

Explicitly out of scope for this slice: graph store, query/answer API,
real authz/RBAC, reranking, observability stack, CI/CD.

## 5. Reference architecture research

Before designing our own pipeline, two open-source reference implementations
were studied for patterns worth borrowing or explicitly deviating from:

- **onyx-dot-app/onyx** — https://github.com/onyx-dot-app/onyx (mature,
  production enterprise RAG platform — Python/FastAPI/Celery/Postgres/
  OpenSearch/Redis/MinIO, Docker Compose + Kubernetes)
- **semantica-agi/semantica** — https://github.com/semantica-agi/semantica
  (Python KG/GraphRAG library, pluggable graph backends including Apache AGE)

### 5.1 Patterns adopted from onyx

- **Early-binding ACL filter, denormalized onto the chunk row.** Onyx computes
  a flat ACL token set per user at query time (`user_email:<email>`,
  `group:<name>`, etc. — a prefixed-string namespace to avoid collisions
  across identity types) and stores the equivalent flattened list directly
  on every **chunk** (not just the document) as a filterable field. The
  search-engine query itself includes a hard boolean filter clause —
  `public OR access_control_list overlaps user_acl` — evaluated *before*
  scoring/ranking, never as an application-layer post-filter.
  **Our adaptation (once real classification/RBAC lands, not in Slice 1):**
  add a `functions text[]` (already in HLD schema) + a `chunk_acl_tokens
  text[]` column with a **GIN index**, and express the authz gate as
  `WHERE is_public OR chunk_acl_tokens && :user_acl_tokens` inside the same
  SQL query that does the pgvector/FTS ranking — not a second query, not a
  post-filter step. This matches the production HLD's "hard-filter before
  ranking" rule exactly, just implemented as a Postgres array-overlap
  instead of an OpenSearch filter clause.
- **Multi-granularity chunking.** Onyx indexes "mini" (sub-chunk, precision),
  "base" (normal), and "large" (recombined, full-document-context) chunks
  simultaneously, and caps metadata-suffix injection at 25% of the token
  budget so metadata never crowds out content. **Adopted for Slice 1's
  chunker config** — cap metadata suffix percentage, keep chunk overlap
  disabled for clean recombination (their stated reasoning: overlaps make
  combining chunks back into "large chunks" ambiguous — matches our own
  "chunk = atomic permission unit" rule, which wants clean non-overlapping
  boundaries too).
- **Separate embedding/model-serving process.** Onyx runs a standalone
  `inference_model_server` microservice so API/worker processes never load
  model weights themselves. **Not needed for Slice 1** (we call the remote
  LiteLLM proxy, no local model weights to isolate) but worth remembering if
  we ever run a local/self-hosted embedding model instead of LiteLLM.
- **Connector interface shape.** Onyx's `BaseConnector`/`LoadConnector`
  ABC (`load_credentials()`, checkpointed incremental poll, yields
  `Document` objects containing `Section`s) is a reasonable interface to
  imitate for our own folder-connector, even though onyx's own "file
  connector" is upload-staging-oriented, not a live filesystem walker — we
  still need to write our own directory-walking connector, just shaped like
  their interface so a real SharePoint connector could swap in later.
- **Not adopting:** OpenSearch, MinIO, Vespa — no need for a second
  search/index engine when Postgres FTS + pgvector already covers hybrid
  retrieval in one engine (this was itself a validating data point: onyx
  moved OFF Vespa onto a combined vector+keyword engine, which is the same
  "one engine, two retrieval modes" strategy our HLD already commits to).

### 5.2 Patterns adopted from semantica (for the later, deferred graph-store slice)

- **Apache AGE integration mechanics** — concrete, directly reusable
  patterns for whenever we build the AGE slice:
  - Idempotent connect: `CREATE EXTENSION IF NOT EXISTS age`, `LOAD 'age'`,
    `SET search_path = ag_catalog,"$user",public`, create the named graph if
    absent.
  - AGE allows only **one label per vertex** — multi-label nodes must be
    emulated via a `labels` property array, reconstructed on read.
  - Keep **our own ID** in an app-level property (e.g. `chunk_id`/`entity_id`)
    completely separate from AGE's own auto-generated internal vertex/edge
    ID — never conflate the two.
  - AGE's `cypher()` wrapper does **not** support `$param` binding — all
    values must be escaped/whitelisted before interpolation into the Cypher
    string (regex-restricted to alnum+underscore for labels/rel-types) —
    a real injection-mitigation requirement, not optional hardening.
- **RRF + alpha-blend fusion for graph+vector** — semantica's
  `hybrid_alpha`-based blending between vector similarity and graph-hop
  traversal results is a simple, provably-workable fusion strategy;
  confirms our own planned RRF approach (HLD §8.1) is a reasonable target,
  just extended with our own FTS rank as a third fusion input once AGE
  lands.
- **Not adopting / explicit gaps identified in semantica:** it has **no
  real ACL/RBAC/multi-tenancy at all** (single shared API key only, no
  per-node/per-document permission filtering) — confirms our own
  authorization design (early-binding, chunk-level, per HLD §6) is not
  something to borrow from this repo; we must build it ourselves regardless.
  It also has no BM25/full-text fusion (only vector+metadata+graph) and
  Celery is declared but never actually wired into their pipeline (uses an
  in-process threaded DAG executor instead) — validates our own choice to
  use real Celery+Redis rather than a lighter in-process alternative, since
  we already need durable async processing for ingestion.

## 6. Local environment topology

```
[TO BE FILLED IN once services are provisioned — target shape:]

  data/raw/  --(folder watcher / manual scan)-->  Ingestion worker (Celery)
                                                         |
                                                         v
                                       Unstructured.io parse + Tesseract OCR
                                                         |
                                                         v
                                              LangChain chunking
                                                         |
                                          +--------------+---------------+
                                          |                              |
                                   Classify (hardcoded              Embed via
                                   "internal" tier)              LiteLLM proxy
                                          |                              |
                                          v                              v
                                   PostgreSQL (metadata)            pgvector
                                          |
                                          v
                                   Postgres FTS (keyword index)

  Redis: Celery broker/result backend
  FastAPI: not yet built in this slice (capture-only)
```

## 7. Open items to confirm with user before/while building

- [ ] Confirm PostgreSQL version (16, per production HLD's polyglot store
      table) and pgvector extension availability via dnf/PGDG repo on
      Oracle Linux 9.
- [ ] Confirm whether `data/processed/` (parsed/chunked intermediate
      artifacts) should be retained on disk for debugging or treated as
      ephemeral.
- [ ] Confirm chunk size / overlap strategy — HLD does not pin exact
      numbers; needs a concrete default for implementation.
