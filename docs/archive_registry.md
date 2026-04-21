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

## Contamination warning

Treat archive bodies as potentially contaminated until proven otherwise.

Known risks include:

- plaintext secret references
- local absolute paths
- binary debris and cache artifacts
- stale overlays that describe abandoned operating modes

Before promoting any archive-derived content:

- scan for secrets
- redact sensitive lines
- remove laptop-specific details unless they are themselves the evidence
- rewrite into concise current-tense language

## What not to do

- do not make archives default-read
- do not copy archive bodies wholesale into active docs
- do not promote `.db`, `.xlsx`, `.pyc`, `.DS_Store`, or scratch artifacts into
  authority
- do not let archive prose overrule manifests, tests, or present-tense source
  behavior
