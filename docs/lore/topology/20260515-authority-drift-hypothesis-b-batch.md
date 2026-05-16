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
Hypothesis A (doc content drifted from code).

## Why

P8 remediation found 3 BLOCKING `reference_replacement_missing_entry` issues across docs
added in April–May 2026. All 3 were Hypothesis B: content was current, code references
were live, all cited file paths existed. The shared root cause — a single structural
gap (no companion-update gate) — explains the batch accumulation.

## How To Apply

When diagnosed with multiple `reference_replacement_missing_entry` issues simultaneously:
1. Check the creation dates of the affected docs (git log) vs the yaml creation date.
2. If the docs were added AFTER the yaml was created, suspect Hypothesis B.
3. Grep each doc's code citations against live src/, scripts/, tests/ before editing.
4. If citations are live and content is current, proceed to yaml addition only.

## Provenance

- Originating packet: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md
- Operator confirmation: IMPLICIT
