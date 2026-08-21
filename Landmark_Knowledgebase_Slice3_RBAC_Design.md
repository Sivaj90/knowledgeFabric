# Landmark Knowledge Fabric — Slice 3 Tech Design (Real RBAC / Classification)

**Status:** DRAFT — scoping only, no implementation started.
Companion to `Landmark_Enterprise_Knowledge_Fabric_HLD_Updated.md` §6
(Identity/AuthN/AuthZ foundation) and §7.2 (Classification), source of
truth for this design. Same lifecycle as Slice 1/2: analyze -> tech design
-> plan -> implement -> unit test -> functional test -> deploy locally
(`AGENTS.md`).

---

## 0. Why this slice exists (closing the Slice 2 gap)

Slice 2 shipped a fully working `/query` endpoint with **authz explicitly
skipped** — a confirmed, tracked, loudly-surfaced scope cut (design doc
`Landmark_Knowledgebase_Slice2_Retrieval_Design.md` §2,
`transparency.authorization = "not_enforced_slice2"` on every response,
service bound to `127.0.0.1` only). Slice 3 is what closes that gap. It
also replaces Slice 1's hardcoded `classification_tier=internal` /
`functions=[]` / `is_public=false` on every chunk with a real classifier.

**These two things are scoped together, not separately, because they are
the same mechanism from two ends:** classification produces the values
(`functions[]`, `effective_tier`, `is_public`) that the authz filter
checks against. Building the filter without real classified data (or
classifying data without a filter that uses it) is each individually
pointless — Slice 3 has to deliver both to deliver either.

## 1. What Slice 3 delivers

```
Person/service login
  -> Azure AD SSO (OAuth2/OIDC) authenticate            (HLD §6)
  -> RBAC lookup: role -> functions_allowed + classification_ceiling
  -> mint signed JWT (PyJWT), cache in Redis              (HLD §6, §6.1)
  -> [downstream: every /query call carries this token]

Document ingested (Slice 1 pipeline)
  -> rule-based classifier: tier (Public/Internal/Confidential/Restricted)
     + functions[] tags, PER CHUNK not per document          (HLD §7.2)
  -> fold in native source ACL -> effective_tier = max(rule, native)
  -> owner confirms Confidential+ tier assignments (human-in-the-loop)

Query (Slice 2 pipeline, now with a REAL token)
  -> HARD authz pre-filter actually enforced:
     (chunk.functions ∩ token.functions_allowed ≠ ∅
      OR chunk.project_ids ∩ token.project_grants ≠ ∅)
     AND rank(chunk.effective_tier) <= rank(token.classification_ceiling)
  -> [rest of Slice 2 pipeline unchanged: RRF, arbitration, answer-gen, sufficiency]
  -> transparency.authorization = "enforced" (flips from Slice 2's
     "not_enforced_slice2")
```

**Explicitly OUT of Slice 3 scope** (deferred further, consistent with
the phased approach):
- **Azure AD SSO itself** — this local VPC has no real Azure AD tenant to
  integrate against. Slice 3 needs a **local substitution decision** (see
  §2 below) for authentication, the same pattern Slice 1 used for
  connectors (local folder instead of SharePoint) and Slice 2 used for
  authz (skip entirely). This is the single biggest open decision in this
  slice and needs your input before implementation starts.
- **`action_scopes` / `project_grants`** — HLD §6.1 explicitly marks these
  as arriving with "the action-execution and cross-function phases," not
  the POC token. Slice 3's token is `sub · roles · functions_allowed ·
  classification_ceiling` only, matching the HLD's own POC scope line.
- **LLM-assisted classification on ambiguous content** — HLD §7.2/§17
  marks rule-based as `[POC]` and LLM-assist as `[END]`. Slice 3 ships the
  rule-based classifier only.
- **Owner confirmation workflow UI** for Confidential+ tier assignments —
  HLD says "owners confirm Confidential+," which implies a review
  interface; Slice 3 can log a pending-confirmation flag on such chunks,
  but a real approval UI is out of scope (no Next.js UI exists yet at
  all, consistent with every prior slice's scope cuts).
- **Multi-user testing at real scale** — Slice 3 proves the mechanism
  works correctly for a handful of distinct test roles/tokens; it does not
  validate performance/correctness under real concurrent multi-user load.

## 2. The open decision that blocks everything else: local auth substitution

Slice 1 substituted a local folder connector for SharePoint/Graph API.
Slice 2 substituted "skip entirely" for the Azure AD authz layer. Slice 3
cannot skip authn/authz again — that's the whole point of this slice — so
it needs a **real, working substitute** for Azure AD SSO in this local VPC.

Three honest options, in increasing order of realism (and cost):

**(a) Static role-to-token config file** (lowest effort). A YAML/JSON file
mapping a small number of named test identities to their claims directly
— e.g. `rnd-engineer: {functions_allowed: [rnd], classification_ceiling:
confidential}`. No real login flow — the `/query` request just includes
a `role` or `identity` field, and the API looks up that identity's claims
from the config file and signs a JWT from them. This proves the
**downstream mechanism** (JWT minting, Redis caching, the authz filter
actually gating retrieval) end-to-end, without needing any real identity
provider. Matches the "hardcode now, real value later" pattern from every
prior slice.

**(b) Local OIDC-compatible identity provider** (moderate effort). Run a
real, self-hosted OIDC provider locally (e.g. Keycloak, or a minimal
FastAPI-based mock IdP) so the actual OAuth2/OIDC handshake happens for
real, just against a local, not Azure-hosted, identity backend. This
proves more of the real flow (a genuine login redirect + token exchange)
but is a meaningfully bigger build for a POC that will be replaced by real
Azure AD in a later phase regardless.

**(c) Skip real SSO, but implement full RBAC + the JWT + the hard authz
filter using a simple built-in login** (username/password against a small
local `users` table, no OIDC at all). Splits the difference: no fake OIDC
theater, but still a genuine login step (not just a config lookup) and
the same downstream RBAC/JWT/filter mechanism as (a).

**Recommendation: (a).** The value of Slice 3 is proving the
**authorization filter and classification pipeline actually work
correctly** — that unauthorized content never reaches the LLM, that
`effective_tier` folds native ACLs correctly, that `project_grants`-style
cross-function access works as an explicit allow-list. None of that is
better tested by a real OIDC handshake versus a config-file identity
lookup; a real IdP integration is Azure-AD-specific work that gets thrown
away and redone for real in a later phase regardless of what's built here.
(a) gets to the real test — the authz filter — fastest and cheapest,
consistent with every "stub now, real later" decision so far in this
project. **Flagging as an open item for your confirmation, not deciding
unilaterally** — same posture as Slice 2's authz-skip decision.

## 3. Classification (HLD §7.2) — real, not hardcoded

Slice 1's `classify.py` currently returns a hardcoded
`(tier="internal", functions=[])` for every chunk. Slice 3 replaces this
with:

- **Rule-based tier assignment.** A small set of explicit rules (keyword/
  pattern matching, path-based hints, or simple heuristics on content) that
  assign `Public / Internal / Confidential / Restricted`. The exact rule
  set is an implementation-time decision (Phase 3.x below), not fixed
  here — but it must be genuinely rule-driven (inspectable, testable
  per-rule), not a single hardcoded constant, or this slice hasn't
  actually delivered anything over Slice 1.
- **Function tagging.** Each chunk gets a `functions[]` tag (e.g. `rnd`,
  `finance`, `ops`) — for this local POC, likely derived from a
  configurable mapping of **source path/document type -> function(s)**,
  since there's no real per-source metadata (SharePoint site, DevOps
  project) to read a real function signal from yet.
- **`effective_tier = max(rule_tier, native_acl_tier)`.** Since Slice 1's
  local folder connector has no native ACL signal at all (it's just files
  on disk), `native_acl_tier` is **stubbed as `Public`** (the most
  permissive floor) for Slice 3's local substitution — meaning
  `effective_tier` reduces to just the rule tier for now. This mirrors the
  same "stub now, real later" pattern as every previous slice; when a real
  connector with real ACLs (SharePoint) exists in a later phase, this
  becomes a real max(), not a no-op.
- **Re-classification on document update** (HLD §7.2) — Slice 1's Phase
  1.6 already implemented document versioning (`superseded_by`); Slice 3's
  classifier should run again on every new document version, not just once
  at first ingest, so a chunk's tier can change if content changes.
- **Owner confirmation for Confidential+** — Slice 3 logs a
  `pending_confirmation=true` flag (new chunk column) for any chunk
  classified Confidential or Restricted, rather than building a full
  review UI (out of scope, §1 above). The flag exists so a later slice's
  UI has something real to build against, and so it's visible/auditable
  which chunks are running on an unconfirmed classification.

## 4. The HARD authorization filter — now actually enforced

This is the direct continuation of Slice 2 §2/§3's `authz_filter=None`
hook — the whole reason that hook was built as a parameter rather than
inline code. Slice 3 implements the real filter expression in
`kb_fabric.retrieval.keyword_search.build_authz_filter()` (currently
raises `NotImplementedError` when `AUTHZ_ENFORCED=True`, by design):

```python
(Chunk.functions.overlap(token.functions_allowed)
 | Chunk.project_ids.overlap(token.project_grants))
& (TIER_RANK[Chunk.effective_tier] <= TIER_RANK[token.classification_ceiling])
```

- Applied to **both** vector and keyword search (Slice 2's `vector_search`
  needs the same `authz_filter` parameter added — it currently has none;
  this is a real gap in the Slice 2 code that Slice 3 must close, not an
  oversight to carry forward again).
- **Before ranking** (HLD golden rule #1: "never retrieve-all-then-hide")
  — the filter is a `WHERE` clause on the candidate-generation query
  itself, not a post-hoc filter on the fused/ranked list.
- `Slice 2`'s `AUTHZ_ENFORCED` flag flips from `False` to `True`; the
  `"not_enforced_slice2"` transparency value becomes `"enforced"`.
- The service can now safely bind to something other than `127.0.0.1`
  once this ships — Slice 2's localhost-only restriction was directly
  because of this gap (design doc §10 security note), so lifting it is a
  real, testable consequence of Slice 3 landing, not a separate decision.

## 5. Proposed phase breakdown

| Phase | Deliverable |
|---|---|
| 3.0 | Confirm the local auth-substitution decision (§2) with user before building anything |
| 3.1 | RBAC data model: `roles`, `role_function_grants`, `users`/`identities` tables (or config-file equivalent per 3.0's decision) + Alembic migration |
| 3.2 | JWT minting (PyJWT) + Redis token cache, matching HLD §6.1's exact claim shape (POC subset: `sub`/`roles`/`functions_allowed`/`classification_ceiling`) |
| 3.3 | Rule-based classifier replacing Slice 1's hardcoded `classify.py` — real tier + function-tag rules, wired into the existing pipeline orchestrator |
| 3.4 | `effective_tier` computation (stubbed native-ACL floor per §3) + `pending_confirmation` flag for Confidential+ |
| 3.5 | Re-classification on document version update (ties into Slice 1 Phase 1.6 versioning) |
| 3.6 | Implement the real `build_authz_filter()` expression (closes the Slice 2 `NotImplementedError` stub) + add the missing `authz_filter` parameter to `vector_search` (closes a real Slice 2 gap) |
| 3.7 | Wire real tokens through the FastAPI `/query` endpoint (replace the no-token-required Slice 2 request shape with real auth), flip `AUTHZ_ENFORCED=True`, update transparency block |
| 3.8 | Unit tests: per-role token minting, per-rule classification, the filter's boundary conditions (exact-tier match, one-tier-over, function overlap, project_grant override, superseded-document exclusion still applies) |
| 3.9 | Functional tests: ingest content classified into 2+ different tiers/functions, issue tokens for 2+ different roles, confirm each role's `/query` results are correctly scoped — this is the test that actually proves the gap is closed, not just that code exists |
| 3.10 | Local deployment: update the query-API systemd unit's bind address (now safe per §4) if desired; document the new login/token-issuance step in "Operating the local stack" |

This mirrors the Slice 1/Slice 2 phase-breakdown pattern.

## 6. Known challenges / risks

1. **The local auth substitution (§2) is inherently a compromise** — 
   whichever option is chosen, it will need to be replaced (not just
   extended) when real Azure AD integration happens. Worth being explicit
   that Slice 3's authn is throwaway scaffolding in a way Slice 1/2's
   connector and retrieval code mostly isn't.
2. **Rule-based classification accuracy is unvalidated** — like Slice 2's
   sufficiency thresholds, the actual classification rules will need
   tuning against real content once real documents exist; a rule set that
   works for 3 test documents may not generalize.
3. **`effective_tier`'s native-ACL stub means this slice doesn't fully
   test the "never trust a looser tier than the source" HLD principle** —
   that principle only becomes a real test once a connector with genuine
   native ACLs (SharePoint) exists. Slice 3 proves the *mechanism*
   (`max()` computation, filter enforcement) but not that specific
   guarantee end-to-end.
4. **No owner-confirmation UI means Confidential+ content is retrievable
   before a human ever reviews it** — the `pending_confirmation` flag
   makes this visible/auditable, but doesn't block retrieval on it (HLD
   doesn't specify blocking either — worth confirming that's acceptable
   for this slice rather than assuming).
5. **Testing multi-role correctness is harder than testing single-user
   correctness** — Phase 3.9's functional tests need genuinely distinct
   token/content combinations to prove anything; a lazy test (one token,
   one tier) would pass without proving the filter actually discriminates.

No code written yet — this is the design/plan step; implementation starts
only after Phase 3.0's open decision is confirmed.

## 7. Status update (2026-08-21): DEFERRED, not cancelled

User confirmed: real authentication/authorization work is deferred until
after the Azure AD app registration is completed on the Microsoft side —
at that point Slice 3 resumes with the **real** Azure AD SSO integration,
not the local substitution (option a/b/c in §2 above never gets built; §2
existed only to unblock a **local** stand-in, which is now moot since real
Azure AD is coming instead). Current priority, per user: validate that
Slice 1 (ingestion) and Slice 2 (retrieval) actually work correctly end to
end — "how are the documents being ingested and is it getting retrieved
properly" — before adding more slices on top. Slice 2's `/query` endpoint
therefore continues to run with `AUTHZ_ENFORCED=False` /
`authorization: not_enforced_slice2` for the time being — this remains a
correctly-tracked, loudly-surfaced gap (HLD §19 item 9), not a silently
abandoned one; it is simply not next in the work queue.
