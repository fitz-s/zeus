---
id: 20260515-ref-replacement-companion-gate-missing
title: New docs/reference/*.md files require companion reference_replacement.yaml entry
topic: topology
extracted_from: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md:root-cause
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
verification_command: python3 scripts/topology_doctor.py --reference-replacement
related: []
expected_signature: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
---

# New docs/reference/*.md files require companion reference_replacement.yaml entry

## What

Every file added to `docs/reference/` (except `AGENTS.md`) must have a corresponding
entry in `architecture/reference_replacement.yaml`. The topology checker
`scripts/topology_doctor_reference_checks.py:run_reference_replacement()` emits a
BLOCKING `reference_replacement_missing_entry` issue for any `docs/reference/*.md`
without a yaml stanza. This is not a style lint — it is a hard block that prevents
topology checks from passing.

## Why

Three reference docs were added in April–May 2026 without companion yaml entries:
`zeus_calibration_weighting_authority.md` (~2026-04-29),
`zeus_kelly_asymmetric_loss_handoff.md` (2026-05-03), and
`zeus_vendor_change_response_registry.md` (~2026-05-03). All three accumulated as
BLOCKING `reference_replacement_missing_entry` issues discovered in P8 of the
runtime-improvement engineering package. The docs themselves were current and
correct — the gap was purely a missing yaml stanza in the matrix. No enforcement
gate existed at the time to catch this at doc-creation time.

## How To Apply

When creating a new `docs/reference/<slug>.md` file, immediately add a companion
stanza to `architecture/reference_replacement.yaml`. Required fields: `path`,
`default_read`, `replacement_status`, `delete_allowed`, `replaced_by`,
`unique_remaining`, `allowed_action`, `rationale`. The rationale MUST cite the
companion-update (the doc or commit that explains why the entry is being added).
If the new doc is conditional-access only, set `default_read: false` and
`allowed_action: keep_conditional`; also add the doc to the conditional reads block
in `docs/reference/AGENTS.md` (or the topology checker will emit a second BLOCKING
issue: `reference_replacement_default_read_mismatch`).

## Anti-Pattern

Adding a `docs/reference/*.md` without the companion yaml stanza appears harmless
until `python3 scripts/topology_doctor.py --reference-replacement` runs and blocks
all topology-gated work. The checker cannot distinguish "I forgot" from "I intended
to leave this unreferenced" — every undeclared file is BLOCKING. Do not add reference
docs and leave the yaml entry for a follow-up PR; do it in the same commit.

## Provenance

- Originating packet: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md
- Originating commit: (P8 commit on branch deploy/live-order-e2e-verification-2026-05-15)
- Operator confirmation: IMPLICIT (P8 task assigned by operator)
