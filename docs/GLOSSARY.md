# SHIELD Glossary

Short definitions for the terms a new admin or reviewer will hit in the UI,
spec docs, and threat model. Plain English first, then the technical
spelling in parentheses where the two diverge. If a term is mostly visible
in `git` history or DB schemas — and not in the running UI — it lives in
the [unified portal spec](unified-portal-spec.md), not here.

## Roles

- **Client.** A user from a customer organization. Sees only the intake
  surface (upload documents); never the repository, picker, or platform
  workspaces.
- **Reviewer.** A read-only role that can walk every project's finished
  artifacts but cannot trigger analyses or change anything.
- **Admin.** Full read-write across every project they're authorized to
  see. The only role that can trigger background analyses, approve drafts
  for reuse, and access the Activity (queue) page.

## The three platforms

- **Tech Debt.** Capability-list reconciliation: take what the client
  says they have, find what overlaps, what's redundant, and what's
  missing. Output is a tidied, deduplicated capability list.
- **Zero Trust.** Posture assessment against a chosen framework
  (CISA ZTMM 2.0, DoD Zero Trust, or NIST CSF 2.0). Output is a
  current-state assessment, a desired future state, and a transition
  roadmap.
- **Attack Surface.** ATT&CK coverage. For each MITRE ATT&CK technique,
  do the client's capabilities provide detection, prevention, and
  response? Output is a coverage classification per technique plus
  a top-three blind-spots headline.

## Documents and drafts

- **Source.** Anything uploaded by a client (a CSV, a PDF, a
  questionnaire response). Treated as ground truth — automated analyses
  *cite* sources, never replace them. Stored with origin
  `human_input`.
- **Automated draft.** What the analysis engine produces. Always
  labeled as a draft, never as source. Stored with origin
  `ai_generated`. A draft is something a human reviewer is meant to
  read and either accept, edit, or reject — not file as a finding on
  its own. Footer disclosure: PII is masked before any input leaves
  SHIELD for analysis.
- **Your reviewed version.** What you produce when you confirm,
  correct, or merge a draft against the sources. This is the
  audit-grade artifact. Stored with origin `human_ai_informed`.

The three labels exist *because* the integrity model refuses to ever
collapse them into one. A draft never becomes a source; a reviewed
version never silently goes back to being a draft. See
[architecture/INTEGRITY_MODEL.md](architecture/INTEGRITY_MODEL.md).

## Reuse, approval, and the picker

- **Picker.** The chokepoint where you pick a prior artifact to start a
  new project from. If you pick an automated-analysis draft, the form
  requires a one-click acknowledgment first — re-using a draft as a
  starting point is a deliberate act and we record it.
- **Approval (was: "promotion").** When a draft is good enough to be
  reusable in other projects, an admin or reviewer can approve it for
  reuse. Approval is a status flag on the draft; the draft *stays*
  labeled as a draft. Approval does not convert a draft into source data.

## Background work

- **Activity page.** Admin-only view of queued, running, failed, and
  recently-finished background analyses. The technical underbelly is
  Redis + RQ. Reviewers don't see this — they walk finished artifacts
  instead.
- **Wait page.** What you see right after starting an analysis. Polls
  every 2 seconds; redirects you to the result when it's ready. You can
  leave and come back — the work keeps running.

## Privacy and security

- **Redaction.** Every input that leaves SHIELD for off-platform
  analysis is first run through `shield.ai.redact` (regex layer +
  Presidio NER). Emails, phones, SSN, credit-card runs, IP addresses,
  URLs, US street addresses, and the client's organization name are
  replaced with typed placeholders. The redaction report — counts
  only, never raw values — lands in the artifact's lineage so the
  audit log shows the layer ran.
- **Lineage.** Every automated draft has a lineage record: which prompt
  version was used, which model, when, how many tokens, and the
  redaction summary. Visible on the artifact detail page.
- **Audit log.** Append-only Postgres-trigger-backed log of every
  state-changing action. Independent of application code — even a
  buggy route can't suppress an audit row.

## Frameworks (Zero Trust)

- **NIST CSF 2.0.** 185 subcategories across 6 functions
  (Govern, Identify, Protect, Detect, Respond, Recover). Loaded from
  NIST's official OSCAL JSON.
- **CISA ZTMM 2.0.** Cybersecurity & Infrastructure Security Agency's
  Zero Trust Maturity Model, version 2. 35 functions across 8 pillars
  (Identity, Devices, Networks, Apps & Workloads, Data, Visibility &
  Analytics, Automation & Orchestration, Governance).
- **DoD Zero Trust.** Department of Defense Zero Trust Strategy &
  Reference Architecture. 44 capabilities across 7 pillars (User,
  Device, App & Workload, Data, Network & Environment, Automation &
  Orchestration, Visibility & Analytics).
- **MITRE ATT&CK.** Adversarial Tactics, Techniques, and Common Knowledge.
  A public knowledge base of attacker behaviors. SHIELD uses the
  Enterprise matrix (222 top-level techniques) for the Attack Surface
  platform. Sub-techniques are out of scope in v1.

## What things in `git` are called

These are mentioned in commit messages and the spec but rarely visible
in the UI:

- **Spine.** The shared scaffolding under the three platforms: identity,
  repository, audit, capability lists, picker. Everything that isn't
  platform-specific.
- **Origin enum.** The database column on `artifacts` and
  `capability_lists` that holds one of `human_input`, `ai_generated`,
  or `human_ai_informed`. These three values never change (a 3-way
  immutability guard enforces it: a SQLAlchemy validator, a session
  listener, and a Postgres trigger).
- **Capability list version.** A snapshot of a client's capability list
  at a point in time. New versions are created; old versions are never
  edited.

---

This glossary is intentionally short. If a term in the UI confuses you
and isn't here, that's a UX bug — open an issue.
