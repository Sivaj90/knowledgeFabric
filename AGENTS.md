# Landmark Enterprise Knowledgebase (RAG Platform)

Permission-aware RAG/knowledgebase platform. Pilot scope: R&D team, sourced
from SharePoint + Azure DevOps + Teams. Reference docs in this repo:
`Landmark_Enterprise_Knowledgebase_HLD.md` (high-level design, source of
truth for architecture decisions) and the leadership deck
(`Landmark_Leadership_Overview.html` / `.pptx`).

## Status

Design/planning phase — no application code committed yet. This file will
grow real build/test/run commands as implementation starts; treat anything
below marked TBD as not yet true, not a promise of scaffolding that exists.

## Committed stack (per HLD)

- **Identity/authz:** Azure AD (Entra ID) SSO for people, Entra ID app
  registrations for agents/services. RBAC (role + function grant +
  classification ceiling) enforced in application logic — no external
  policy engine. Signed session token (JWT) minted per query, cached in
  Redis, signing keys in Azure Key Vault.
- **Hosting:** AKS (Azure Kubernetes Service) — committed.
- **Object storage:** Azure Blob Storage (raw source files).
- **Metadata store:** PostgreSQL (system of record).
- **Keyword index:** PostgreSQL FTS / BM25.
- **Vector store:** pgvector (managed alt: Azure AI Search / OpenSearch).
- **Graph store:** Apache AGE / Neo4j (end-state; nodes + edges).
- **Queue/async:** Celery + Redis.
- **Ingestion tooling:** Unstructured.io (parsing), Tesseract (OCR),
  LangChain (chunking/orchestration).
- **Embeddings:** Azure OpenAI `landmark-text-embedding-3-large`.
- **Answer-gen LLM:** Azure OpenAI GPT-5.5 mini — grounding-only, never
  fine-tuned on raw enterprise data, inline citations via LangChain.
- **Classification:** rule-based classifier (Public/Internal/Confidential/
  Restricted) + LLM-assist on ambiguous content; owners confirm
  Confidential+.
- **Retrieval:** hybrid keyword+vector candidate gen -> HARD authz filter
  (drops unauthorized/out-of-function content before it ever reaches the
  LLM) -> reciprocal-rank fusion -> context assembly under a fixed token
  budget. End-state adds a cross-encoder reranker (Cohere/bge) and graph
  traversal (Cypher/Gremlin/GraphRAG).
- **CI/CD:** Azure DevOps Pipelines.
- **Observability:** OpenTelemetry + Prometheus + Grafana + Loki (managed
  alt: Azure Monitor / App Insights).
- **PII/content-safety:** Microsoft Presidio (redaction, end-state);
  Azure AI Content Safety / Rebuff / Llama Guard (prompt-injection
  defense, end-state — POC defense is prompt-level only).

## Non-negotiable design rules

- **Early-binding authorization** — filter by the caller's token *before*
  ranking or LLM context assembly. Never retrieve-all-then-hide.
- **Grounding only** — the LLM answers strictly from authorized, retrieved
  chunks with citations. Never fine-tuned on raw enterprise data.
- Ingested content is treated as untrusted data; conflicting sources are
  shown, not silently resolved.

## Working with Hermes on this project

Per requirement, the expected lifecycle is:
1. Understand/analyze the specific requirement and how to implement it
   against the committed stack above.
2. Tech design doc.
3. Plan.
4. Implementation.
5. Unit testing.
6. Functional testing.
7. Local deployment.

Additional needs are called out per-requirement as they arise — don't
assume this list is exhaustive for any given task.

Leadership-facing deliverables from this project must stay
audience-agnostic (never name the reviewer/manager directly), and use real
sourced data (actual vendor pricing, cited research) over placeholder
figures — mark anything unconfirmed as "indicative, not a quote."
Leadership decks are typically delivered as both HTML and PowerPoint.

## Build / test / run

TBD — no scaffolding yet. Update this section with real commands as soon
as the first service/module is created (package manager, entrypoint, unit
test runner, functional/integration test setup, local-deploy steps e.g.
docker-compose or a local k8s target).
