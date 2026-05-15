---
id: 20260515-authority-drift-hypothesis-b-batch
title: Topology authority drift batches as Hyp-B when docs are current but yaml is absent
topic: topology
extracted_from: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md:cross-doc-root-cause
extracted_on: 2026-05-15
status: ACTIVE
authority_class: DESIGN_RATIONALE
last_verified: 2026-05-15
verification_command: python3 scripts/topology_doctor.py --reference-replacement
related: [20260515-ref-replacement-companion-gate-missing, 20260515-keep-conditional-requires-agents-md-conditional-reads]
---

# Topology authority drift batches as Hyp-B when docs are current but yaml is absent

## What

When `reference_replacement_missing_entry` BLOCKING issues accumulate in a batch, the
correct first hypothesis is Hypothesis B (yaml entry missing, doc is fine) rather than
Hypothesis A (doc content drifted from code). In practice, multiple missing entries
appearing together is almost always a process gap — docs were added without companion
yaml updates — rather than multiple independent doc-drift events. Grep-confirming content
currency before editing yaml prevents unnecessary doc rewrites.

## Why

P8 remediation found 3 BLOCKING `reference_replacement_missing_entry` issues across docs
added in April–May 2026. All 3 were Hypothesis B: content was current, code references
were live, all cited file paths existed. The correct fix was exclusively yaml additions,
not doc edits. Treating them as Hypothesis A (and editing the docs) would have been
incorrect and introduced unnecessary churn. The shared root cause — a single structural
gap (no companion-update gate) — explains the batch accumulation.

## How To Apply

When diagnosed with multiple `reference_replacement_missing_entry` issues simultaneously:
1. Check the creation dates of the affected docs (git log) vs the yaml creation date.
2. If the docs were added AFTER the yaml was created, suspect Hypothesis B (gap in process).
3. Grep each doc's code citations against live src/, scripts/, tests/ before editing.
4. If citations are live and content is current, proceed to yaml addition only.
5. Reserve Hypothesis A (doc edit) for cases where grep shows code has moved and the
   doc describes a stale location, interface, or behavior.

The remediation order matters: POSTMORTEM/companion-update first, then yaml edits, then
AGENTS.md updates, then lore cards. Never edit `architecture/` without a companion-update
record explaining why.

## Anti-Pattern

Treating every `reference_replacement_missing_entry` as Hypothesis A and auditing doc
content first wastes investigation time and risks introducing unnecessary doc edits.
A missing yaml entry is more likely than a doc that was current yesterday becoming stale
overnight. Start with the yaml gap check; move to content audit only if the yaml entry
already exists (or if the doc was added long ago and the codebase has evolved).

## Provenance

- Originating packet: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md
- Originating commit: (P8 commit on branch deploy/live-order-e2e-verification-2026-05-15)
- Operator confirmation: IMPLICIT (P8 task assigned by operator)
