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

- **onyx-dot-app/onyx** — https://github.com/onyx-dot-app/onyx
- **semantica-agi/semantica** — https://github.com/semantica-agi/semantica

Findings and how they influenced our design: **[TO BE FILLED IN — pending
research subagent results]**

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
