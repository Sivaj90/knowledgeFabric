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
  Apache AGE graph-store integration patterns. Full findings and rationale
  in `Landmark_Knowledgebase_Local_VPC_HLD.md` §5. Concrete patterns folded
  into the phases below:
  - **Chunker config (Phase 1.4):** cap metadata-suffix injection at 25% of
    token budget (onyx pattern); no chunk overlap, for clean recombination
    and because it matches our "chunk = atomic permission unit" rule.
  - **Data model (Phase 1.2):** add `is_public boolean` and
    `chunk_acl_tokens text[]` (GIN-indexed) columns now, even though Slice 1
    hardcodes every chunk as `internal`/non-public — so the later RBAC slice
    is a filter-clause change, not a schema migration + rewrite.
  - **Connector interface (Phase 1.3):** shape our folder connector's
    interface like onyx's `BaseConnector`/`LoadConnector`
    (`load_credentials()`, checkpointed poll, yields `Document` objects with
    `Section`s) so a real SharePoint connector can swap in later without
    changing downstream code.
  - **Apache AGE mechanics (future graph-store slice, NOT Slice 1):**
    semantica's concrete AGE patterns — idempotent
    `CREATE EXTENSION IF NOT EXISTS age` + graph creation, one-label-per-
    vertex constraint (multi-label via a `labels` array property), keep our
    own IDs separate from AGE's internal IDs, and mandatory literal
    escaping/whitelisting since AGE's `cypher()` wrapper has no `$param`
    binding.

## Build sequence

### Phase 1.0 — Environment provisioning
- [x] Install Python 3.11 (dnf, `python3.11` + `python3.11-devel` + pip) —
      2026-08-20
- [x] Create project venv at `/var/lib/aiprojects/knowledgebase/.venv` —
      Python 3.11.15, pip 24.0
- [x] Install PostgreSQL 16 (Oracle Linux module stream `postgresql:16`) +
      pgvector extension (0.6.2, Oracle Linux appstream package, no source
      compile needed)
- [x] Install Redis (native, systemd) — 6.2.22
- [x] Verify both services start via systemd and survive reboot config
      (`systemctl enable --now` — both `active` + `enabled`)
- [x] Create local Postgres role (`kb_fabric`) + database (`kb_fabric`) for
      this project; switched TCP loopback auth from `ident` to
      `scram-sha-256` in `pg_hba.conf` (backed up original first) so the app
      can connect with username/password; verified via `psql -h 127.0.0.1`
- [x] Confirm connectivity to Landmark LiteLLM proxy — verified via direct
      curl to `/v1/chat/completions` with `gpt-5.5` (see earlier fallback
      setup); `.env` / `.env.example` created with real (gitignored) and
      template connection values respectively

### Phase 1.1 — Project scaffolding
- [x] Python package layout — `src/kb_fabric/` (src-layout, installed
      editable via `pip install -e .`)
- [x] `requirements.txt` + `pyproject.toml` (setuptools, src-layout,
      pytest config) — SQLAlchemy 2.0.35, psycopg3 3.2.3, pgvector 0.3.6,
      alembic 1.13.3, celery 5.4.0, redis-py 5.1.1, unstructured 0.15.13
      (docx/pptx/pdf/md extras), langchain 0.3.7, openai 1.54.4 (LiteLLM
      proxy is OpenAI-compatible), pytest 8.3.3 — all installed clean,
      `pip check` passes
- [x] `.env.example` documenting all required vars (Postgres, Redis/Celery,
      LiteLLM base_url+key+models, data paths) — fixed a bug found during
      this phase: `DATABASE_URL` must use `postgresql+psycopg://` (psycopg3)
      not bare `postgresql://` (SQLAlchemy defaults that scheme to psycopg2,
      which isn't installed) — fixed in both `.env` and `.env.example`
- [x] `kb_fabric.config.Settings` (pydantic-settings) — typed, centralized
      env loading, `get_settings()` cached accessor
- [x] Alembic migration setup — `alembic/env.py` wired to
      `kb_fabric.config.get_settings()` (single source of truth for the DB
      URL, not duplicated in `alembic.ini`) and `kb_fabric.models.Base`
      metadata for autogenerate; verified live: `alembic current` connects
      to Postgres successfully, `alembic revision --autogenerate` produces
      a correct empty baseline (no tables yet — expected, Phase 1.2 adds
      the schema)
- [x] Smoke tests (`tests/test_config.py`) — settings load from `.env`,
      live `SELECT 1` DB connectivity via SQLAlchemy engine — both pass
      (`pytest`: 2 passed)

### Phase 1.2 — Data model
- [x] Postgres schema: `documents`, `chunks` tables matching HLD §7.4 chunk
      metadata schema (chunk_id, document_id, source_system, source_uri,
      content, content_hash, embedding_id, functions[], classification_tier,
      effective_tier, owner, authors, project_ids, entities, timestamps,
      version, superseded_by) — implemented as SQLAlchemy ORM models in
      `src/kb_fabric/models.py`, Alembic migration
      `b8ac0ffa5f4e_documents_and_chunks_tables_hld_7_4_.py`, applied to the
      live `kb_fabric` DB
- [x] Added `is_public boolean DEFAULT false` and `chunk_acl_tokens text[]`
      (GIN-indexed) columns now (onyx-pattern early-binding ACL, see local
      VPC HLD §5.1) — populated as `is_public=false`/`chunk_acl_tokens={}`
      for every chunk in Slice 1 (real values wired in a later RBAC slice;
      verified the exact target query shape
      `WHERE is_public OR chunk_acl_tokens && :user_tokens` works correctly
      against seeded public/restricted/unauthorized test rows)
- [x] pgvector column on `chunks.embedding` with an HNSW (cosine) ANN index
      — **dimension bug found + fixed:** `landmark-text-embedding-3-large`
      natively outputs 3072 dims, but pgvector 0.6.2 hard-caps HNSW/IVFFlat
      indexes at 2000 dims (hit live: `InternalError: column cannot have
      more than 2000 dimensions for hnsw index`). Resolved by requesting
      embeddings at reduced width via OpenAI's officially-supported
      `dimensions=1536` API parameter (Matryoshka-style truncation, not a
      hack) — `EMBEDDING_DIM=1536` is now the single source of truth in
      `kb_fabric/models.py`; Phase 1.4's embed step must pass
      `dimensions=EMBEDDING_DIM` on every embeddings call
- [x] Postgres FTS index (tsvector column + GIN index) on chunk content —
      implemented as a Postgres **generated STORED column**
      (`to_tsvector('english', content)`), kept in sync automatically by
      Postgres on every insert/update, never written by the app; verified
      with a live `plainto_tsquery` search
- [x] Functional verification (`tests/test_schema.py`, 6 tests, all run
      against the real `kb_fabric` Postgres DB, not mocks): insert/roundtrip,
      dedup-gate unique constraint enforcement, cascade delete
      (document→chunks), pgvector cosine similarity ranking correctness,
      generated FTS column + keyword search, and the exact authz filter
      query pattern from the local VPC HLD. `pytest`: 8 passed (2 from
      Phase 1.1 + 6 new)

### Phase 1.3 — Ingestion connector (local folder substitute)
- [x] Connector interface (`src/kb_fabric/connectors/base.py`) — `Section`,
      `DocumentEnvelope`, `LoadConnector` ABC shaped after onyx's
      `BaseConnector`/`LoadConnector` (`load_credentials()`,
      `load_from_state()` checkpointed poll yielding `DocumentEnvelope`
      batches) per local VPC HLD's stated rationale, so a real SharePoint
      connector can implement the same interface later without touching
      downstream code
- [x] Folder-scanner connector (`src/kb_fabric/connectors/folder.py`) —
      walks `data/raw/` (extension-filtered: docx/pdf/pptx/md/txt),
      computes `sha256:<hex>` `content_hash` per file, detects new/changed
      files via a dedup gate against the live `documents` table
      `(source_system, source_uri, content_hash)` unique constraint — no
      separate cursor file needed since Postgres is already the state store
- [x] Normalizes each file into `DocumentEnvelope` (content + stub ACL +
      metadata) — `is_public=False`/`acl_tokens=[]` hardcoded for Slice 1,
      same envelope shape a real connector will produce later
- [x] Celery wiring (`src/kb_fabric/celery_app.py`, `src/kb_fabric/tasks.py`)
      — Redis-backed broker/result-backend from settings;
      `process_document_envelope` task registered as the enqueue target
      (Phase 1.4 fills in the actual parse/chunk/classify/embed/write body
      — intentionally raises `NotImplementedError` for now, not a silent
      no-op)
- [x] Runnable entrypoint `python -m kb_fabric.run_ingest [--dry-run]` —
      verified live end-to-end against the real filesystem, real Postgres
      dedup gate, and real Redis broker: dry-run correctly reported 0 docs
      against the actual empty `data/raw/`, then 1 after adding a real test
      file; real (non-dry-run) enqueue landed an actual task on the Redis
      `celery` queue (`redis-cli llen celery` confirmed 1); started a real
      Celery worker which picked up the task and failed with the expected
      `NotImplementedError` (proves the full connector→Celery→worker path
      is wired, and cleanly hands off to Phase 1.4). Redis and `data/raw/`
      cleaned up after the manual verification run.
- [x] Functional tests (`tests/test_folder_connector.py`,
      `tests/test_ingest_entrypoint.py`, 9 tests total, against real
      Postgres + real fixture files + real Celery eager-mode execution):
      deterministic hashing, unsupported-extension filtering, new-file
      enqueue with correct stub ACL, dedup gate skips unchanged files,
      dedup gate re-enqueues changed files (different hash), empty/missing
      root dir handling, dry-run count, and real Celery task dispatch.
      `pytest`: 17 passed (8 from Phases 1.1/1.2 + 9 new)

### Phase 1.4 — Processing pipeline (Celery worker)
- [x] Parse: Unstructured.io (docx/pdf/pptx/md/txt) + Tesseract OCR fallback
      for scanned content — `src/kb_fabric/pipeline/parse.py`, uses
      `unstructured.partition.auto.partition` (auto-detects file type and
      OCR need). **2 real environment bugs found + fixed:** (1) PDF import
      failed with `libGL.so.1` missing — fixed via `dnf install
      mesa-libGL`; (2) `unstructured==0.15.13`'s pdf extra pulled in a
      `pdfminer.six`/`pdfplumber` combo where neither the old nor a
      hand-pinned-down `pdfminer.six` satisfied both packages' constraints
      simultaneously — fixed properly by bumping `unstructured` to 0.16.25
      (not by fighting the transitive pins), then letting pip re-resolve
      `pdfminer.six` to the version both `unstructured` 0.16.25 and
      `pdfplumber` actually agree on. `pip check`: clean.
- [x] Chunk: LangChain `RecursiveCharacterTextSplitter` —
      `src/kb_fabric/pipeline/chunk.py`. **Concrete numbers now pinned**
      (was an open item): `CHUNK_SIZE=1000` chars, `CHUNK_OVERLAP=0` (the
      zero-overlap choice matches the local VPC HLD's stated onyx-pattern
      rationale — chunk = atomic permission unit, so no chunk boundary
      should let the same sentence live under two different ACL/tier
      decisions).
- [x] Classify: hardcoded `classification_tier = "internal"`,
      `effective_tier = "internal"` for every chunk (no real rules/LLM yet)
      — `src/kb_fabric/pipeline/classify.py`, isolated as its own function
      so the real classifier is a drop-in replacement later
- [x] Embed: call `landmark-text-embedding-3-large` via the LiteLLM proxy —
      `src/kb_fabric/pipeline/embed.py`, **always passes
      `dimensions=EMBEDDING_DIM` (1536)** per the Phase 1.2 pgvector
      HNSW-index-cap fix. **Real bug found + fixed:** `openai==1.54.4`'s
      bundled HTTP client passed a `proxies` kwarg that the already-pinned
      `httpx==0.28.1` no longer accepts (`TypeError: Client.__init__() got
      an unexpected keyword argument 'proxies'`) — fixed by bumping
      `openai` to `>=3.0.0`. Verified live against the real LiteLLM
      endpoint: confirmed HTTP 200 and exactly 1536-dim vectors returned.
- [x] Write: fan out to Postgres metadata table (`documents`/`chunks`),
      pgvector column, FTS index (generated column, no app write needed) —
      `src/kb_fabric/pipeline/write.py`
- [x] Orchestrator (`src/kb_fabric/pipeline/orchestrator.py`) wires all
      five steps together (`process_envelope`) and is called from the real
      Celery task (`kb_fabric.tasks.process_document_envelope`, no longer
      the Phase 1.3 `NotImplementedError` stub)
- [x] **Live end-to-end verification** (real file, real worker, real
      network, real DB — not test fixtures): dropped a real `.md` file into
      `data/raw/`, ran `python -m kb_fabric.run_ingest`, started a real
      Celery worker which picked up the task, called the real LiteLLM
      embeddings endpoint (confirmed `HTTP/1.1 200 OK` in worker logs),
      and wrote 1 document + 1 chunk to the real `kb_fabric` Postgres DB.
      Verified via `psql`: real 1536-dim embedding stored
      (`vector_dims(embedding)` = 1536), `content_tsv` populated, correct
      `internal`/`internal` tier, `is_public=false`. Ran a live pgvector
      cosine-similarity query against the real embedded chunk with a new
      query embedding — returned a sensible similarity distance (0.2987)
      for a semantically related query. Test artifacts (file + DB rows +
      Redis queues) cleaned up after manual verification.
- [x] Functional tests (`tests/test_pipeline.py`, 15 tests — parse/chunk/
      classify/embed/write/orchestrator, all against real fixture files,
      the real LiteLLM embeddings endpoint, and real Postgres, not mocks).
      Also updated `tests/test_ingest_entrypoint.py`'s eager-mode test
      since it now exercises the real (no-longer-stub) pipeline body.
      **Test-hygiene fix:** `process_envelope`/Celery tasks commit
      internally (needed for production durability), so tests that invoke
      them now explicitly delete what they wrote in a `finally` block —
      verified by running the full suite twice in a row and confirming
      zero row accumulation in Postgres. `pytest`: 32 passed total (17
      existing + 15 new), confirmed stable across repeated runs.

### Phase 1.5 — Unit testing
- [x] Test folder-scanner dedup logic (same file twice = no re-embed;
      changed file = re-embed) — done in Phase 1.3's
      `tests/test_folder_connector.py`
      (`test_dedup_gate_skips_already_ingested_unchanged_file`,
      `test_dedup_gate_re_enqueues_changed_file`)
- [x] Test chunker on each file type (docx/pdf/md/pptx) with fixture files
      — `tests/test_parse_filetypes.py` (real generated .docx/.pptx/.pdf
      fixtures, plus a scanned/image-only .pdf for the OCR path) alongside
      `tests/test_pipeline.py`'s md/txt coverage
- [x] Test classify stub always returns "internal" —
      `test_classify_always_returns_internal`
- [x] Test embedding call wires into pgvector write correctly — done
      against the **real** LiteLLM endpoint (not mocked, per this project's
      established "verify against real infra" pattern) in
      `test_embed_texts_returns_correct_dimension`,
      `test_write_document_envelope_roundtrip`,
      `test_process_envelope_full_pipeline_writes_real_chunks`
- [x] Test chunk metadata schema fields are all populated (no nulls where
      HLD schema requires a value) — covered by
      `test_process_envelope_full_pipeline_writes_real_chunks` asserting
      non-null embedding + correct tier on every written chunk, and by
      Phase 1.2's `test_schema.py` NOT NULL constraints at the DB level
      (a bad write would fail the insert, not just leave a null)

### Phase 1.6 — Functional testing
- [x] End-to-end: drop a real docx/pdf/md/pptx file in `data/raw/`, run the
      pipeline, verify rows appear in Postgres metadata + pgvector + FTS —
      done live manually for `.md` (Phase 1.4 verification), and now also
      covered by real generated `.docx`/`.pptx`/`.pdf` fixtures parsed
      through the actual pipeline in `tests/test_parse_filetypes.py`
- [x] Idempotency: re-run on unchanged file, verify no duplicate rows / no
      re-embedding — `tests/test_versioning.py`
      `test_idempotent_reingest_via_live_cli_no_duplicate_rows` runs the
      **actual CLI entrypoint** (`python -m kb_fabric.run_ingest`
      equivalent) twice against the same file: first run enqueues+processes
      1 document, second run's connector dedup gate yields 0 (file skipped
      entirely, not even enqueued) — confirmed exactly 1 `Document` row
      exists after both runs, not 2
- [x] Update: modify a file, verify re-classification/re-chunk/re-embed
      happens and old version is versioned per `superseded_by` semantics —
      **implemented (was previously missing entirely, not just untested)**:
      `src/kb_fabric/pipeline/write.py` gained `find_current_version()` +
      versioning logic in `write_document_envelope()`. A changed-file
      re-ingest now creates a new `Document` with `version = previous + 1`
      and sets the previous `Document.superseded_by` to the new document's
      id. Verified in `tests/test_versioning.py` (4 tests) at both the
      `write_document_envelope` level and the full `process_envelope`
      pipeline level (parse real changed file content -> new version ->
      old version correctly marked superseded).
      **Documented scoping limitation (not an oversight):** chunk-level
      `superseded_by` (HLD §7.4 has this column on chunks too) is NOT
      populated — there's no well-defined 1:1 mapping between an old
      document's chunks and a new document's chunks without a real
      content-diffing algorithm, since re-chunking can shift every chunk
      boundary. Slice 1 relies on filtering by
      `document.superseded_by IS NULL` (a join) at retrieval time to
      exclude stale content, which is sufficient for now; true chunk-level
      diffing is deferred, flagged here for whoever designs the retrieval
      slice.
- [x] OCR path: drop a scanned/image-based PDF, verify Tesseract fallback
      triggers and text is extracted — `tests/test_parse_filetypes.py`
      `test_parse_scanned_pdf_triggers_ocr_fallback`, against a real
      generated image-only PDF fixture (`sample6_scanned.pdf`, rendered
      via PIL + reportlab, no embedded text layer at all — forces the OCR
      path, doesn't just test the same text-extraction path with a
      different file extension). **Real bug found + fixed:** the OCR path
      needs `poppler-utils` (`pdfinfo`/`pdftoppm`, used by `pdf2image` to
      rasterize PDF pages before handing them to Tesseract) — missing on
      this system, confirmed via the actual error
      (`PDFInfoNotInstalledError: Unable to get page count. Is poppler
      installed and in PATH?`), fixed via `dnf install poppler-utils`.
      This is the 4th real environment dependency bug found across
      Phases 1.4/1.6 (after libGL, the pdfminer/unstructured conflict, and
      the openai/httpx conflict) — Tesseract OCR itself worked correctly
      once poppler-utils was present, extracting "Scanned Document...
      Landmark warehouse incident report JAFZA..." from the image-only PDF.

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

**Slice 2 scope update (added 2026-08-20, per user request) — keep AI in
the retrieval loop itself, not just as the final answer-generation step.**
Two agentic additions to design and build alongside baseline retrieval, now
also documented in `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`
§8.1 step 0 and §8.3a:

- **Pre-retrieval query planning (HLD §8.1 step 0).** Before candidate
  generation runs, an LLM call decides (a) which engine(s) to invoke —
  vector-only, keyword-only, hybrid (default), or vector+graph for
  multi-hop questions — and (b) whether the query needs reframing
  (expansion, pronoun/context resolution, decomposition into sub-queries).
  Both the routing decision and any reframed query are logged and surfaced
  in the "why you're seeing this" transparency block.
- **Post-answer sufficiency check with a scored, configurable retry loop
  (HLD §8.3a — mechanism now resolved, not just flagged).** After the
  answer-gen LLM produces a draft, a structured LLM call scores it on two
  dimensions — `coverage_score` (did retrieval get enough) and
  `groundedness_score` (is the answer actually supported by cited chunks)
  — each 0.0-1.0, returned as JSON, not free-form judgment. If either score
  falls below its configured threshold (POC defaults: coverage 0.7,
  groundedness 0.8), the loop re-enters retrieval with a suggested refined
  query, up to a **configurable `max_retrieval_loops`** (POC default: 1
  extra pass; per-function overridable at end-state, same governance
  pattern as arbitration rules — a knowledge owner sets it, every change
  audited). If the cap is hit without reaching threshold, the
  **best-scoring pass so far** is returned with an explicit "could not
  fully verify" caveat — never silently returned as if fully verified,
  never looped indefinitely. Both scores are **persisted to Audit for every
  query**, which is also the resolution to the previously-flagged "no
  per-response quality score exists" gap — this score uses the same metric
  vocabulary as RAGAS (`coverage_score` ≈ RAGAS `context_precision`/
  `context_recall`, `groundedness_score` ≈ RAGAS `faithfulness`), so the
  cheap synchronous runtime score and the heavier periodic RAGAS/Azure AI
  Evaluation batch layer are complementary, not two competing choices.

**Configuration surface (new, POC defaults — lives alongside arbitration
rules / `project_grants` / action-approval thresholds as one per-function
config surface, not a separate system):**

| Setting | POC default | Purpose |
|---|---|---|
| `coverage_threshold` | 0.7 | Below this → trigger retry |
| `groundedness_threshold` | 0.8 | Below this → trigger retry (weighted higher: ungrounded is worse than incomplete) |
| `max_retrieval_loops` | 1 | Hard cap on extra retrieval passes; 0 disables the loop (today's single-pass behavior) |
| `sufficiency_model` | same as answer-gen model | May switch to a cheaper/faster model at end-state, see challenge #1 below |

**Challenges / risks flagged now, before Slice 2 design starts in earnest:**
1. **Latency stacking.** Query planning adds one LLM round-trip *before*
   retrieval; the sufficiency check adds another *after* generation, and
   each triggered retry adds a full extra retrieval+generation cycle. Three
   to four sequential LLM calls per query (planner → answer-gen →
   sufficiency → possible retry generation) is a real latency budget
   problem against the "sub-second retrieval" non-functional target
   (HLD §16) — that target was written for the hybrid search step alone,
   not a multi-call agentic loop wrapped around it. Needs an explicit
   updated latency target for the full agentic path before this is built,
   or a fast/cheap model choice for the planner + sufficiency steps
   specifically (separate from the answer-gen model).
2. **Cost stacking.** Same shape as #1 but for spend — every query
   potentially becomes 2-4 LLM calls instead of 1. Needs a per-query cost
   budget decided alongside the latency target (ties into HLD §17 "cost
   per query" POC evaluation criterion, and §18 evaluation criterion 7).
3. **Quality score now defined, calibration still TBD.** The sufficiency
   check (coverage/groundedness, 0.0-1.0, see above) **is** the per-response
   quality score — the earlier-flagged "no scoring mechanism exists" gap is
   resolved in the HLD. What remains open: the POC thresholds (0.7/0.8) are
   reasoned starting points, not measured against real usage yet, and will
   need per-function tuning once Slice 2 has real query traffic to look at.
4. **Reframing breaks naive caching.** If/when a query-result cache is
   added (HLD §16 lists Redis caching as a latency lever), a reframed query
   string no longer matches a cache keyed on the raw user query. Cache key
   strategy needs to account for this (e.g. key on reframed query, or
   cache at the chunk-candidate level rather than final-answer level).
5. **Explainability surface area.** The existing "why you're seeing this"
   transparency block (HLD §8.1 step 4, §8.4) now needs to communicate
   three additional things without becoming noise: what engine(s) were
   chosen and why, whether/how the query was reframed, and whether a retry
   pass happened and why. Needs a concrete UI/API contract for this, not
   just "log it" — logging alone (Audit, Layer 5) satisfies the audit
   requirement but not the user-facing transparency requirement, which are
   two different consumers of the same event.
6. **Sufficiency-check false positives/negatives are themselves a new
   failure mode.** A sufficiency check that's too lenient defeats its own
   purpose (never triggers a retry, same as not having it); one that's too
   strict burns the latency/cost budget in #1/#2 on every query. The
   0.7/0.8 POC defaults need validating against real usage once Slice 2 is
   live — this is exactly what the periodic RAGAS/Azure AI Evaluation batch
   layer (HLD §15) is for: auditing whether the cheap synchronous score is
   itself well-calibrated.

No implementation started on any of this yet — Slice 1 (capture) continues
as originally planned; this is design-ahead documentation only, per user
request to capture it now before Phase 1.4 implementation resumes.

