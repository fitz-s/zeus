# Archive Registry

This file is the visible historical interface for Zeus.

It is not authority. It does not turn archive bodies into default context.

## What this file is for

Use this file when you need to answer:

- when archive material is appropriate to read
- what kinds of archive categories exist
- how to label archive-derived claims
- what guardrails apply before promoting historical material into active docs

## Default rule

Archive bodies are historical cold storage.

- They are not peer authority to `architecture/**`, active packet docs, source
  code, tests, or canonical DB truth.
- They are not default-read boot surfaces.
- They may be consulted deliberately when a task needs historical evidence.

Visible historical protocol:

- `docs/archive_registry.md` - access and promotion rules
- `architecture/history_lore.yaml` - compressed durable lessons

Cold historical storage when present locally:

- `docs/archives/**`
- local archive bundles such as `docs/archives.zip`
- retired overlays, scratch packages, and archived work packets

Do not assume those cold bodies are reviewer-visible.

## When to use archives

Read archives only when the task explicitly needs one of these:

- prior-failure evidence
- old packet lineage or decision history
- proof that a proposed fix was already tried and rejected
- secret-contamination or artifact-provenance review
- historical context dense enough that `architecture/history_lore.yaml` is not
  sufficient

Prefer `architecture/history_lore.yaml` first. Only open raw archive material
when the dense lore card is insufficient.

## Retrieval Decision Tree

Use this order:

1. Start with current law: `AGENTS.md`, `workspace_map.md`, relevant
   `architecture/**` manifests, active packet docs, and source/tests when
   behavior is involved.
2. Check `architecture/history_lore.yaml` for a dense card matching the task.
3. If the lore card is enough, stop. Do not open archive bodies.
4. If a live question still needs historical proof, identify the narrow archive
   category and the smallest specific file or packet needed.
5. Before reading or promoting anything, assume contamination and scan for
   secrets, binary debris, local-only paths, and obsolete operating modes.
6. Promote only a rewritten, current-tense lesson into an active surface.

Stop immediately if the archive material would be used to override current
source, tests, manifests, or canonical DB truth. That requires a new packet, not
archive lookup.

## Archive categories

Typical categories include:

- work packets
- governance and design notes
- audits, findings, and investigations
- migration and rebuild material
- research, reports, and results
- overlay packages and local scratch residue
- binary or mixed artifacts such as `.db`, `.xlsx`, `.pyc`, and platform junk

These categories are evidence classes, not authority classes.

## Category Guide

| Category | Use | Do not use for |
|---|---|---|
| Work packets | Prior scope, decisions, and closeout evidence | Current active packet truth |
| Governance/design notes | Historical rationale and rejected alternatives | Present-tense authority without manifest backing |
| Audits/findings/investigations | Repeated failure modes and risk patterns | Runtime behavior claims without code/test proof |
| Migration/rebuild material | Provenance for data or schema decisions | Live DB mutation authority |
| Research/reports/results | Evidence and hypotheses | Strategy promotion by itself |
| Overlay/local scratch | Explaining drift or abandoned modes | Default onboarding or active law |
| Binary/mixed artifacts | Provenance only after explicit handling | Direct active docs authority |

## How to cite archive material

Any claim derived from archive material must be labeled:

`[Archive evidence]`

Use summaries, not long raw excerpts. Do not silently blend archive claims into
present-tense law.

## Promotion guardrails

Historical material may be promoted into active docs only when all of the
following are true:

1. it solves a still-live problem
2. it is consistent with current manifests and runtime truth, or an explicit
   packet is superseding them
3. it has been sanitized
4. the promoted result is rewritten into active form instead of copied
   wholesale

## Promotion Checklist

Before promoting any historical lesson, confirm:

- Live need: the lesson prevents a still-plausible failure.
- Current consistency: the lesson agrees with active manifests, source, tests,
  and packet state, or an explicit packet supersedes them.
- Sanitization: no secrets, credentials, private tokens, binary debris, or
  accidental local-only data are carried forward.
- Density: the promoted result is a compact rule, guardrail, or lore card, not
  a chronological summary.
- Antibody: the promoted result names a test, manifest, checker, runbook, or
  explicit residual risk.
- Labeling: archive-derived claims are marked `[Archive evidence]`.
- Placement: durable law goes to manifests/tests/authority docs; compressed
  memory goes to `architecture/history_lore.yaml`; access policy stays here.

## Contamination warning

Treat archive bodies as potentially contaminated until proven otherwise.

Known risks include:

- plaintext secret references
- local absolute paths
- binary debris and cache artifacts
- stale overlays that describe abandoned operating modes
- historical DBs, spreadsheets, and generated outputs that look factual but are
  only provenance/evidence

Before promoting any archive-derived content:

- scan for secrets
- redact sensitive lines
- remove laptop-specific details unless they are themselves the evidence
- rewrite into concise current-tense language

Known contamination examples from the reconstruction package review include
plaintext `WU_API_KEY` references in historical markdown and mixed `.db`,
`.xlsx`, `.pyc`, and `.DS_Store` debris. Treat these as examples of the class
of risk; do not copy those archive bodies into active docs.

## What not to do

- do not make archives default-read
- do not copy archive bodies wholesale into active docs
- do not promote `.db`, `.xlsx`, `.pyc`, `.DS_Store`, or scratch artifacts into
  authority
- do not let archive prose overrule manifests, tests, or present-tense source
  behavior
