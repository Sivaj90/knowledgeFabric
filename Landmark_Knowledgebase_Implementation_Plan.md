# Landmark Enterprise Knowledge Fabric — Implementation Plan (Local VPC)

**Status:** DRAFT — living document, updated as work proceeds.
Companion to `Landmark_Knowledgebase_Local_VPC_HLD.md` (the "what/why of
local substitutions") and `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`
(the production design of record). This document is the "how, in what
order" — build/test/deploy steps, tracked against the requirement lifecycle
in `AGENTS.md`: analyze → tech design → plan → implement → unit test →
functional test → deploy locally.

---

## Slice 1 scope (current)

Full V0/L0 **capture pipeline**, no retrieval/query API yet:

```
Local folder (data/raw/) → parse → chunk → classify (hardcoded) → embed
   → write to Postgres (metadata) + pgvector + Postgres FTS
```

Out of scope for Slice 1: Apache AGE graph store, retrieval/answer API,
real RBAC/classification, Next.js UI, observability, CI/CD.

## Reference research (inputs before design)

- `onyx-dot-app/onyx` and `semantica-agi/semantica` studied for connector
  abstraction, chunking, permission-aware retrieval, and (for semantica)
  Apache AGE graph-store integration patterns.
- Findings folded into design decisions below — **[pending final subagent
  output; this section updated once research completes]**.

## Build sequence

### Phase 1.0 — Environment provisioning
- [ ] Install Python 3.11 (dnf module stream or source build)
- [ ] Create project venv at `/var/lib/aiprojects/knowledgebase/.venv`
- [ ] Install PostgreSQL 16 (PGDG repo) + pgvector extension
- [ ] Install Redis (native, systemd)
- [ ] Verify both services start via systemd and survive reboot config
      (enabled, not just started)
- [ ] Create local Postgres role + database for this project
- [ ] Confirm connectivity to Landmark LiteLLM proxy from Python (chat +
      embeddings) — already verified via curl; re-verify via the actual
      client library we choose (OpenAI SDK pointed at custom base_url)

### Phase 1.1 — Project scaffolding
- [ ] Python package layout (`src/kb_fabric/` or similar — TBC naming)
- [ ] `requirements.txt` / `pyproject.toml` — LangChain, Unstructured.io,
      psycopg (pgvector), redis-py, celery, python-dotenv, pytest
- [ ] `.env.example` documenting required vars (DB DSN, Redis URL, LiteLLM
      base_url + key env var name, no real secrets committed)
- [ ] Alembic (or equivalent) migration setup for the metadata schema

### Phase 1.2 — Data model
- [ ] Postgres schema: `documents`, `chunks` tables matching HLD §7.4 chunk
      metadata schema (chunk_id, document_id, source_system, source_uri,
      content, content_hash, embedding_id, functions[], classification_tier,
      effective_tier, owner, authors, project_ids, entities, timestamps,
      version, superseded_by)
- [ ] pgvector column on chunks (or a separate vectors table — decide during
      implementation) sized to the embedding model's dimension
- [ ] Postgres FTS index (tsvector column + GIN index) on chunk content

### Phase 1.3 — Ingestion connector (local folder substitute)
- [ ] Folder-scanner connector: walks `data/raw/`, computes `content_hash`
      per file, detects new/changed files (dedup gate, matches HLD idempotency
      requirement)
- [ ] Normalizes each file into the same "Document envelope" shape the real
      SharePoint connector would produce (content + stub ACL + metadata),
      so swapping in a real connector later doesn't change downstream code
- [ ] Enqueues envelope onto Celery (Redis-backed)

### Phase 1.4 — Processing pipeline (Celery worker)
- [ ] Parse: Unstructured.io (docx/pdf/pptx/md) + Tesseract OCR fallback for
      scanned content
- [ ] Chunk: LangChain semantic chunking (chunk size/overlap — TBC, see open
      item in local VPC HLD)
- [ ] Classify: hardcoded `classification_tier = "internal"`,
      `effective_tier = "internal"` for every chunk (no real rules/LLM yet)
- [ ] Embed: call `landmark-text-embedding-3-large` via LiteLLM proxy, only
      for chunks whose `content_hash` changed
- [ ] Write: fan out to Postgres metadata table, pgvector column, FTS index

### Phase 1.5 — Unit testing
- [ ] Test folder-scanner dedup logic (same file twice = no re-embed;
      changed file = re-embed)
- [ ] Test chunker on each file type (docx/pdf/md/pptx) with fixture files
- [ ] Test classify stub always returns "internal"
- [ ] Test embedding call (mocked LiteLLM response) wires into pgvector
      write correctly
- [ ] Test chunk metadata schema fields are all populated (no nulls where
      HLD schema requires a value)

### Phase 1.6 — Functional testing
- [ ] End-to-end: drop a real docx/pdf/md/pptx file in `data/raw/`, run the
      pipeline, verify rows appear in Postgres metadata + pgvector + FTS
- [ ] Idempotency: re-run on unchanged file, verify no duplicate rows / no
      re-embedding
- [ ] Update: modify a file, verify re-classification/re-chunk/re-embed
      happens and old version is versioned per `superseded_by` semantics
- [ ] OCR path: drop a scanned/image-based PDF, verify Tesseract fallback
      triggers and text is extracted

### Phase 1.7 — Local deployment
- [ ] systemd unit files (or equivalent) for: Postgres (native package
      default), Redis (native package default), Celery worker, Celery beat
      (if scheduled re-scan needed)
- [ ] Document exact start/stop/status commands in this plan's "Operating
      the local stack" section (added once built)
- [ ] Confirm pipeline survives a VM reboot (services auto-start, or
      documented manual start sequence)

## Decisions log (append as they're made)

- 2026-08-20: Slice 1 = capture pipeline only, no query API. Graph store
  (Apache AGE) deferred. Classification hardcoded to "internal" tier.
  Fully native runtime (no Docker). Python 3.11 required.

## Open questions (carried from local VPC HLD §7)

- [ ] PostgreSQL version + pgvector availability via PGDG repo on OL9
- [ ] Retain `data/processed/` intermediate artifacts or treat as ephemeral?
- [ ] Concrete chunk size/overlap default (HLD doesn't pin numbers)

## Next slice (not started, for context only)

Once Slice 1 (capture) is proven: Slice 2 = retrieval (hybrid BM25+vector,
RRF fusion, FastAPI `/query` endpoint, grounded answer via GPT-5.5 mini) —
matches HLD §8. Apache AGE graph store and real classification/RBAC to be
scheduled as their own slices after that, per user's phased approach.
