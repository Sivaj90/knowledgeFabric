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
