# Audit Log — Landmark Enterprise Knowledge Fabric (local VPC implementation)

Chronological record of every system-level and project-level action taken by
Hermes Agent on this VPC in service of this project: package installs,
service starts/stops, config changes, and file/document changes. File-level
content changes are also captured with more detail in `git log` (this repo
is git-initialized); this log is the single place that additionally covers
VM/system-level actions git can't see (dnf installs, systemd services,
env/service config).

Format per entry: `[YYYY-MM-DD HH:MM UTC] ACTION — details — reason/requirement it serves`

---

## 2026-08-20

- **[repo init]** Initialized git repository at `/var/lib/aiprojects/knowledgebase`.
  Added `.gitignore` (excludes venvs, secrets, raw ingested data, pgdata).
  Committed existing design docs as baseline (HLD, concept doc, leadership
  deck, AGENTS.md). — *Requirement: user asked for an audit trail before any
  system-level changes begin.*
- **[audit]** Created this `AUDIT_LOG.md`. — *Requirement: user asked to be
  able to see every action taken and every change made, per project, before
  implementation work starts.*

---

## Environment baseline (recorded before any provisioning, for reference)

Captured 2026-08-20, VM state prior to any installs:
- OS: Oracle Linux Server 9.8, 8 vCPU, 30 GiB RAM
- Python: 3.9.25 (system), no pip
- No Docker/Podman installed
- No PostgreSQL, Redis, or psql/redis-cli installed
- Node v24.18.0 + npm available (nvm-managed)
- Passwordless sudo available for `opc` user
- Outbound internet reachable (pypi.org, github.com confirmed)
- Existing project files at `/var/lib/aiprojects/knowledgebase/`: HLD docs,
  concept doc, leadership deck (docx/html/pptx) — no application code yet.

## Decisions on record (from planning discussion, not yet executed)

- Local implementation is a **substitution-set POC**, not the production
  Azure architecture: no Azure AD, no SharePoint, no AKS, no Azure Blob in
  this environment. HLD (`Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md`)
  remains the production design of record and will NOT be edited for local
  substitutions — those go in a separate `Landmark_Knowledgebase_Local_Implementation_Plan.md`.
- First implementation slice = full V0/L0 capture pipeline: ingest → classify
  (hardcoded `internal` tier) → chunk → embed → store in Postgres (metadata)
  + pgvector + Postgres FTS. Apache AGE graph store deferred to a later slice
  (source-compile risk on Oracle Linux 9).
- Source connector substitute: local filesystem folder-watcher reading from
  `/var/lib/aiprojects/knowledgebase/data/raw/` (stands in for SharePoint).
  File types to support from day one: docx, pdf, md, pptx (via Unstructured.io
  + Tesseract OCR fallback).
- Runtime: fully native, no containers — PostgreSQL + Redis as native
  systemd services; app code (FastAPI/Celery/workers) in a Python venv.
- Python upgrade to 3.11 required (system Python 3.9 too old for
  LangChain/pgvector client/Unstructured.io compatibility).
- Model access: Landmark LiteLLM proxy (`https://lmlitellm.landmarkgroup.com`),
  chat = `gpt-5.5` (mapped to HLD's "GPT-5.5 mini"), embeddings =
  `landmark-text-embedding-3-large` — both already verified reachable.

---

*(Entries below this line are appended as work proceeds — one entry per
system action or meaningful file/doc change, newest at the bottom.)*

---

## 2026-08-20 (continued)

- **[research]** Dispatched two background research subagents to study
  reference repos for architecture patterns before drafting the local
  implementation plan and local-VPC HLD:
  - `onyx-dot-app/onyx` (github.com/onyx-dot-app/onyx) — open-source
    enterprise RAG/search platform, studied for connector abstraction,
    chunking/embedding pipeline, permission-aware retrieval, deployment model.
  - `semantica-agi/semantica` (github.com/semantica-agi/semantica) — studied
    for knowledge-graph schema/build pipeline and hybrid vector+graph
    retrieval fusion patterns.
  No code copied verbatim; used for pattern/architecture reference only,
  per user request to "get the inputs from that as well" before building.
  — *Requirement: user wants proven external patterns considered before
  committing to our own local pipeline design.*

- **[research complete]** Both subagents finished (~2.5 min each). Key
  takeaways folded into `Landmark_Knowledgebase_Local_VPC_HLD.md` §5 and
  `Landmark_Knowledgebase_Implementation_Plan.md`: onyx's early-binding
  ACL-on-chunk pattern (flattened token array + GIN index + hard SQL filter
  before ranking), multi-granularity chunking with a metadata-suffix token
  cap, and a connector interface shape to imitate; semantica's concrete
  Apache AGE integration mechanics (idempotent extension/graph creation,
  one-label-per-vertex workaround, ID separation, literal-escaping
  requirement) for the later deferred graph-store slice, plus confirmation
  that neither reference repo solves real ACL/RBAC for us — that remains
  ours to build. Full briefs saved by the delegation subsystem at
  `~/.hermes/cache/delegation/subagent-summary-0-20260820_080425_258358.txt`
  (onyx) and `-1-20260820_080425_260392.txt` (semantica).

- **[tooling]** Installed `gh` CLI (GitHub CLI) via `sudo dnf install -y gh`
  — not yet authenticated (no token provided). User wants this repo pushed
  to a private GitHub repo for access outside this VPC; paused pending a
  personal access token or the user running `gh auth login` themselves.
  — *Requirement: user asked whether the git repo is reachable from outside
  the VPC; answer is not yet, needs a remote configured.*

- **[github remote]** User provided a GitHub fine-grained PAT and created
  the target repository at `https://github.com/Sivaj90/knowledgeFabric`.
  Token stored in `~/.hermes/.env` as `GITHUB_TOKEN` (secrets-only location,
  never committed). Initial push attempts failed with 403 (token lacked
  Contents:write); user updated the token's repository permissions on
  github.com, then push succeeded via `git push --force-with-lease` (a
  stray one-off API test-write commit on the empty remote was overwritten
  by our real local history — no project content was lost, remote had
  nothing but that test file). All 7 local commits + full file tree
  confirmed present on GitHub via `gh api repos/.../contents/` listing.
  `origin` remote now tracks `main`. — *Requirement: user wants the repo
  accessible from outside this VPC.*

- **[provisioning]** Installed local stack packages via `sudo dnf`:
  - `python3.11`, `python3.11-pip`, `python3.11-devel` (system Python 3.9
    too old for LangChain/pgvector-client/Unstructured.io)
  - PostgreSQL 16 (`dnf module enable postgresql:16`, then
    `postgresql-server` + `postgresql-contrib`) — chosen over default
    non-module PG13 to match HLD's committed PostgreSQL 16
  - `pgvector` (0.6.2, from Oracle Linux appstream, not source-compiled)
  - `redis` (6.2.22)
  All via `sudo dnf install -y <pkg>` — no manual repo config needed, all
  packages came from Oracle Linux's own appstream/module repos.

- **[provisioning]** Initialized and started services:
  - `sudo postgresql-setup --initdb` — created `/var/lib/pgsql/data`
  - `sudo systemctl enable --now postgresql` and `redis` — both `active` +
    `enabled` (survive reboot)
  - Created Postgres role `kb_fabric` (password auth) and database
    `kb_fabric` owned by that role
  - `CREATE EXTENSION vector;` in `kb_fabric` DB — confirmed pgvector 0.6.2
    active (`\dx` shows ivfflat + hnsw access methods available)
  - **Modified `/var/lib/pgsql/data/pg_hba.conf`** (backed up first as
    `pg_hba.conf.bak`): changed TCP loopback auth (127.0.0.1/32, ::1/128)
    from `ident` to `scram-sha-256` so the app can connect with
    username/password over TCP, not just Unix-socket `peer` auth. Unix
    socket / replication auth left untouched. Reloaded postgresql to apply.
    Verified: `psql -h 127.0.0.1 -U kb_fabric -d kb_fabric` succeeds with
    password auth.
  - **Local dev credential note:** role password is `kb_fabric_local_dev`
    — a plaintext local-only dev password, intentionally simple since this
    is a sandboxed local VPC POC with no external network exposure; NOT
    suitable if this environment is ever exposed beyond localhost.

- **[provisioning]** Created Python 3.11 virtualenv at
  `/var/lib/aiprojects/knowledgebase/.venv` (`python3.11 -m venv .venv`).
  Confirmed Python 3.11.15 + pip 24.0 inside the venv. No packages
  installed into it yet (next: requirements.txt / pyproject.toml per
  implementation plan Phase 1.1).

- **[Phase 1.1 — project scaffolding]** Created `src/kb_fabric/` package
  (src-layout), `pyproject.toml` (setuptools + pytest config), and
  `requirements.txt` pinning: SQLAlchemy 2.0.35, psycopg[binary] 3.2.3,
  pgvector 0.3.6, alembic 1.13.3, celery 5.4.0, redis-py 5.1.1,
  unstructured[docx,pptx,pdf,md] 0.15.13, langchain 0.3.7 +
  langchain-text-splitters 0.3.2, openai 1.54.4 (LiteLLM proxy is
  OpenAI-compatible), pydantic-settings 2.5.2, pytest 8.3.3 +
  pytest-mock 3.14.0. Installed into project `.venv` via
  `pip install -r requirements.txt` — clean install, `pip check` reports
  no broken requirements. Installed project itself editable
  (`pip install -e .`). — *Requirement: Phase 1.1 of implementation plan.*
- **[bug found + fixed]** `.env` / `.env.example` `DATABASE_URL` used bare
  `postgresql://` scheme; SQLAlchemy resolves that to the psycopg2 driver
  by default, which is not installed (project uses psycopg3). Fixed both
  files to use `postgresql+psycopg://`. Verified fix via live Postgres
  connection through SQLAlchemy engine (`SELECT 1`) and via
  `alembic current`. — *Requirement: Phase 1.1, caught during verification,
  not left for later phases to discover.*
- **[Phase 1.1 — Alembic]** Ran `alembic init alembic`; rewired
  `alembic/env.py` to import `kb_fabric.config.get_settings()` for the DB
  URL (single source of truth, not duplicated in `alembic.ini`) and
  `kb_fabric.models.Base.metadata` as `target_metadata` for autogenerate.
  Verified live against the real `kb_fabric` Postgres DB: `alembic current`
  connects successfully; `alembic revision --autogenerate -m "baseline (no
  tables yet)"` produces a correctly empty migration (no models defined
  yet — expected, schema lands in Phase 1.2). — *Requirement: Phase 1.1.*
- **[Phase 1.1 — tests]** Added `tests/test_config.py`: settings load
  correctly from `.env`, and a live DB connectivity smoke test
  (`SELECT 1` via the SQLAlchemy engine). `pytest`: 2 passed. — *Requirement:
  Phase 1.1 / unit-test lifecycle step per AGENTS.md.*
- **[gitignore]** Added `.pytest_cache/` to `.gitignore` (egg-info and
  pycache patterns already covered generated dirs).

- **[Phase 1.2 — data model]** Implemented `documents` and `chunks` tables
  as SQLAlchemy ORM models (`src/kb_fabric/models.py`) matching production
  HLD §7.4 chunk metadata schema exactly, plus the early-binding ACL columns
  (`is_public`, `chunk_acl_tokens`, GIN-indexed) called for in the local VPC
  HLD §5.1. Generated + applied Alembic migration
  `b8ac0ffa5f4e_documents_and_chunks_tables_hld_7_4_.py` against the live
  `kb_fabric` DB. — *Requirement: Phase 1.2 of implementation plan.*
- **[bug found + fixed]** pgvector 0.6.2 hard-caps HNSW/IVFFlat ANN indexes
  at 2000 dimensions; `landmark-text-embedding-3-large` natively outputs
  3072-dim vectors — hit live as `InternalError: column cannot have more
  than 2000 dimensions for hnsw index` when applying the migration. Fixed
  by switching to OpenAI's `dimensions=1536` API parameter (a supported,
  documented truncation feature of that embedding model, not a hack) —
  `EMBEDDING_DIM=1536` centralized in `kb_fabric/models.py` as the single
  source of truth; flagged for Phase 1.4 (embed step) to pass
  `dimensions=EMBEDDING_DIM` on every embeddings call. — *Requirement:
  Phase 1.2, caught during verification.*
- **[Phase 1.2 — FTS]** Implemented `chunks.content_tsv` as a Postgres
  generated STORED column (`to_tsvector('english', content)`) rather than a
  plain column the app must remember to populate — always in sync with
  `content` automatically. GIN index added. Verified with a live
  `plainto_tsquery` search. — *Requirement: Phase 1.2.*
- **[Phase 1.2 — tests]** Added `tests/test_schema.py`: 6 functional tests
  against the real `kb_fabric` Postgres DB (not mocks) — insert/roundtrip,
  unique-constraint dedup-gate enforcement, cascade delete, pgvector cosine
  similarity ranking correctness, generated-column FTS search, and the
  exact authz filter query pattern
  (`WHERE is_public OR chunk_acl_tokens && :user_tokens`) from the local VPC
  HLD, seeded with public/restricted/unauthorized rows to prove the filter
  is correct. `pytest`: 8 passed total (2 existing + 6 new). Confirmed no
  test data leaked into the DB (all tests roll back; `SELECT count(*)` on
  both tables returns 0 post-run). — *Requirement: Phase 1.2 / unit-test
  lifecycle step per AGENTS.md.*

- **[Phase 1.3 — ingestion connector]** Implemented the connector interface
  (`src/kb_fabric/connectors/base.py`: `Section`, `DocumentEnvelope`,
  `LoadConnector` ABC) shaped after onyx's `BaseConnector`/`LoadConnector`
  per the local VPC HLD's explicit rationale, and the concrete
  `FolderConnector` (`src/kb_fabric/connectors/folder.py`) walking
  `data/raw/` with sha256 content-hash dedup against the live `documents`
  table. Wired Celery (`celery_app.py`, `tasks.py`, Redis-backed) with a
  `process_document_envelope` stub task (raises `NotImplementedError` —
  Phase 1.4 scope — rather than silently no-op'ing) and a runnable
  `python -m kb_fabric.run_ingest [--dry-run]` entrypoint. — *Requirement:
  Phase 1.3 of implementation plan.*
- **[Phase 1.3 — live verification]** Ran the real entrypoint against the
  actual `data/raw/` directory (not just test fixtures): dry-run correctly
  reported 0 docs when empty, 1 after adding a real file; real enqueue
  landed an actual Celery task on the live Redis broker (confirmed via
  `redis-cli llen celery` = 1); started a real Celery worker process which
  picked up the task and failed with the expected `NotImplementedError`,
  proving the full connector -> Celery -> worker path works end-to-end.
  Cleaned up Redis queues (`flushdb` on db 0 and 1) and the test file from
  `data/raw/` afterward — Phase 1.3 leaves no artifacts behind for Phase
  1.4 to trip over. — *Requirement: Phase 1.3, live/manual verification
  ahead of unit tests.*
- **[Phase 1.3 — tests]** Added `tests/test_folder_connector.py` (7 tests)
  and `tests/test_ingest_entrypoint.py` (2 tests) — all against real
  Postgres, real fixture files (`tests/fixtures/`), and real Celery
  eager-mode task execution (not mocks). Covers: deterministic hashing,
  extension filtering, stub-ACL envelope shape, dedup gate for
  unchanged/changed files, empty/missing directory handling, dry-run
  counting, and real task dispatch. `pytest`: 17 passed total (8 existing +
  9 new). — *Requirement: Phase 1.3 / unit-test lifecycle step per
  AGENTS.md.*

- **[HLD design update — no code, docs only]** Per user request: (1) flagged
  the absence of any per-response quality/scoring mechanism as an explicit
  open gap in `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md` §15 and
  §19 (item 6) — previously only implicit in "RAGAS deferred", now called
  out directly with a warning marker so it isn't mistaken for solved. (2)
  Added AI-driven query planning (engine routing + query reframing) as HLD
  §8.1 step 0 and as open item #7 in §19. (3) Added a post-answer
  sufficiency check with a bounded iterative retrieval loop as new HLD
  §8.3a and open item #8 in §19. (4) Updated the §8.4 query→answer sequence
  diagram and prose to show both new steps in context. (5) Mirrored all of
  this into `Landmark_Knowledgebase_Implementation_Plan.md`'s "Next slice"
  section for Slice 2 (retrieval), including 6 explicit challenges/risks
  (latency stacking, cost stacking, no persisted quality score yet,
  reframing breaking naive caching, explainability surface area, and the
  sufficiency check's own false-positive/negative risk). No implementation
  work done — Slice 1 (capture, Phase 1.4 next) is unaffected and continues
  as planned; this is design-ahead documentation only. — *Requirement: user
  request to flag the scoring gap and pre-design agentic retrieval
  behavior before Phase 1.4 implementation resumes.*

- **[HLD design update — resolves previously-flagged scoring gap]** Per user
  request: fleshed out §8.3a's sufficiency check with a concrete,
  machine-parseable scoring mechanism (`coverage_score` +
  `groundedness_score`, 0.0-1.0, JSON verdict) instead of free-form LLM
  judgment, deliberately using the same metric vocabulary as RAGAS
  (context_precision/recall ~= coverage, faithfulness ~= groundedness) so
  the runtime score and the future periodic RAGAS batch layer are
  complementary rather than two separate scoring systems. Added a
  configurable `max_retrieval_loops` (POC default 1) plus
  `coverage_threshold`/`groundedness_threshold` (POC defaults 0.7/0.8) as a
  new per-function config surface, same governance pattern as arbitration
  rules (owner-set, every change audited). Both scores are now persisted to
  Audit per query, which directly resolves the "no per-response quality
  score exists" gap flagged in the previous session — updated §15 and §19
  to mark that item resolved (checked) rather than open, while leaving the
  RAGAS-vs-Azure-AI-Evaluation tool choice for the periodic batch layer as
  the one remaining open item. Updated the §8.4 sequence diagram/prose to
  reflect scored thresholds instead of a vague "sufficient?" check.
  Mirrored the same resolution into
  Landmark_Knowledgebase_Implementation_Plan.md's Slice 2 section
  (challenge #3 updated from "no score exists" to "score exists, needs
  calibration"). No code written -- HLD/plan documentation only, ahead of
  resuming Phase 1.4 implementation. Requirement: user request to keep
  the scoring mechanism and configurable loop cap, then proceed to Phase
  1.4.

- **[Phase 1.4 complete]** Implemented the full parse->chunk->classify->
  embed->write pipeline (src/kb_fabric/pipeline/: parse.py, chunk.py,
  classify.py, embed.py, write.py, orchestrator.py), replacing the Phase
  1.3 NotImplementedError stub in the Celery task. Concrete decisions
  pinned: CHUNK_SIZE=1000/CHUNK_OVERLAP=0 (onyx-pattern rationale from
  local VPC HLD), hardcoded internal/internal classification tier per
  Slice 1 scope, embed always passes dimensions=1536 per the Phase 1.2
  pgvector index-cap fix. Requirement: Phase 1.4 of implementation plan.
- **[bugs found + fixed, Phase 1.4]** (1) PDF parsing failed with missing
  libGL.so.1 -- fixed via dnf install mesa-libGL. (2) unstructured==0.15.13
  pdf extra had an unresolvable pdfminer.six/pdfplumber version conflict
  (ImportError: cannot import name PSSyntaxError) -- fixed properly by
  bumping unstructured to 0.16.25 rather than hand-pinning pdfminer down
  (which just traded one conflict for another with pdfplumber's own pin).
  (3) openai==1.54.4's bundled HTTP client passed a proxies kwarg that
  httpx==0.28.1 no longer accepts -- fixed by bumping openai to >=3.0.0.
  All three confirmed fixed via live imports/API calls, not just pip
  installing and assuming; pip check clean after each fix.
- **[Phase 1.4 live verification]** Dropped a real .md file into
  data/raw/, ran the real CLI entrypoint, started a real Celery worker
  which called the real LiteLLM embeddings endpoint (confirmed HTTP 200 in
  worker logs) and wrote 1 document + 1 chunk to the real kb_fabric
  Postgres DB -- verified via psql: 1536-dim embedding actually stored,
  FTS generated column populated, correct tier/is_public values. Ran a
  live pgvector cosine-similarity query against the real embedded chunk --
  returned a sensible distance for a related query. Cleaned up test
  artifacts (file, DB rows, Redis queues) after manual verification.
- **[Phase 1.4 tests + test-hygiene fix]** Added tests/test_pipeline.py
  (15 tests, real fixture files + real LiteLLM network calls + real
  Postgres, not mocks). Discovered and fixed a test-hygiene bug: since the
  pipeline now does real work (was a stub before), tests invoking
  process_envelope/the real Celery task commit real rows internally
  (needed for production durability) -- rollback-based cleanup doesn't
  undo an already-committed transaction. Fixed both
  tests/test_pipeline.py and tests/test_ingest_entrypoint.py to explicitly
  delete what they wrote in a finally block; verified by running the full
  suite twice in a row and confirming zero row accumulation in Postgres
  both times. pytest: 32 passed total (17 existing + 15 new), stable
  across repeated runs. Requirement: Phase 1.4 unit-test lifecycle step
  per AGENTS.md.
- **[Phase 1.5/1.6 plan reconciliation]** Marked several Phase 1.5 (unit
  testing) items complete that were actually already satisfied by Phase
  1.3's and 1.4's test suites (dedup logic, classify stub, embedding
  wiring, schema field population) -- these were written organically
  during 1.3/1.4 rather than as a separate later pass, so the plan is
  updated to reflect reality rather than leave them looking unstarted.
  Flagged genuine remaining gaps for Phase 1.6: real .docx/.pdf/.pptx
  fixture files (only .md/.txt tested so far), OCR path with an actual
  scanned-PDF fixture, idempotent re-run via the live CLI (dedup logic
  itself is unit-tested but not exercised end-to-end twice), and
  superseded_by versioning on file update -- which does not exist in the
  pipeline code yet at all, not just untested.

- **[Phase 1.5/1.6 complete]** Generated real binary test fixtures
  (tests/fixtures/sample3.docx, sample4.pptx, sample5.pdf via
  python-docx/python-pptx/reportlab) and a real scanned/image-only PDF
  (sample6_scanned.pdf -- text rendered into a PIL image with no embedded
  text layer, to force the Tesseract OCR path rather than the fast
  text-extraction path). Added tests/test_parse_filetypes.py (4 tests)
  covering all four real file types through the actual parse pipeline.
  Requirement: Phase 1.5/1.6 of implementation plan, closing the gaps
  flagged after Phase 1.4 (only .md/.txt fixtures existed then).
- **[bug found + fixed, 4th of this project]** OCR path failed with
  PDFInfoNotInstalledError -- poppler-utils (pdfinfo/pdftoppm) missing,
  needed by pdf2image to rasterize PDF pages before Tesseract can OCR
  them. Fixed via dnf install poppler-utils. Confirmed live: OCR
  correctly extracted "Scanned Document... Landmark warehouse incident
  report JAFZA..." from the image-only PDF fixture.
- **[superseded_by versioning implemented, Phase 1.6]** This did not exist
  anywhere in the pipeline before now -- Phase 1.2's schema had the
  version/superseded_by columns but nothing populated them. Implemented
  find_current_version() + versioning logic in
  src/kb_fabric/pipeline/write.py: a changed-file re-ingest creates a new
  Document with version = previous+1 and marks the previous Document's
  superseded_by. Deliberately scoped to document-level only, NOT
  chunk-level -- documented as a real limitation (no well-defined 1:1
  chunk mapping across re-chunks without content-diffing), not silently
  skipped. Verified in tests/test_versioning.py at both the write-function
  level and the full pipeline level (parse real changed file -> new
  version -> old version correctly marked superseded).
- **[idempotent re-run verified via live CLI, Phase 1.6]**
  tests/test_versioning.py's
  test_idempotent_reingest_via_live_cli_no_duplicate_rows runs the actual
  run_ingest entrypoint twice against the same unchanged file: confirmed
  first run processes 1 document, second run's dedup gate yields 0 (file
  not even enqueued), and exactly 1 Document row exists in Postgres after
  both runs.
- **[test-hygiene regression found + fixed]** Adding the new binary
  fixtures broke 3 existing tests that hardcoded "2 fixture files"
  (test_new_files_are_enqueued_with_stub_acl,
  test_dry_run_does_not_touch_celery,
  test_real_enqueue_reaches_redis_broker) -- they picked up all 6 sample
  files instead of the original 2, and one test also leaked 12 real rows
  into Postgres in the process. Fixed by deriving the expected count from
  SUPPORTED_EXTENSIONS + the fixtures directory contents at test-run time
  rather than a hardcoded literal, so the test suite doesn't silently
  break every time a new fixture file is added. Verified by running the
  full suite twice in a row: 41 passed both times, zero rows left in
  Postgres afterward.

- **[Phase 1.7 complete: local deployment]** Added
  deploy/systemd/kb-fabric-celery-worker.service (Postgres and Redis were
  already native systemd services, enabled since Phase 1.0). Installed to
  /etc/systemd/system/, daemon-reload'd, enabled, started. Deliberately
  did NOT add Celery beat -- Slice 1 has no scheduled re-scan requirement
  (ingestion is manual-trigger via python -m kb_fabric.run_ingest);
  building an unused scheduler now would be ahead of an actual
  requirement. Documented full start/stop/status/logs/survival-on-restart
  commands in a new "Operating the local stack" section in the
  implementation plan.
- **[bug found + fixed, 5th of this project]** SELinux (Enforcing mode on
  Oracle Linux 9) blocked the systemd-managed Celery worker from executing
  at all -- confirmed via ausearch -m avc: avc: denied { execute } against
  both the project venv's bin/ (labeled var_lib_t) and the underlying
  uv-managed Python interpreter's bin/ (labeled data_home_t), neither of
  which init_t (the systemd service domain) may execute. Fixed properly
  via semanage fcontext -a -t bin_t <path> + restorecon -Rv on both paths
  -- not by disabling SELinux enforcement, which would have been the
  wrong fix for a system meant to run enterprise data. Verified live: the
  systemd-managed worker (not a manually-foregrounded one, unlike every
  prior phase's verification) received a real enqueued task, called the
  real LiteLLM embeddings endpoint (HTTP 200), and wrote the chunk to
  Postgres -- confirmed via journalctl. Also verified clean
  start/stop/restart, and ran the full 41-test pytest suite with the
  systemd worker running continuously in the background with no
  interference. All three services (postgresql, redis,
  kb-fabric-celery-worker) confirmed enabled -- will auto-start on the
  next system startup without manual intervention.
- **[AGENTS.md update blocked]** Attempted to update AGENTS.md's "Build /
  test / run" section (previously marked TBD) with the now-real commands.
  The write was blocked by a protected-file approval prompt that timed out
  without a response -- per the tool's explicit instruction, did not retry
  or route around it via another tool. AGENTS.md's Build/test/run section
  therefore still reads TBD even though real commands now exist (see this
  plan's Phase 1.7 section and "Operating the local stack" for the actual
  commands) -- flagging this explicitly so it isn't mistaken for
  forgotten; the user can approve/apply that edit directly whenever
  convenient.
