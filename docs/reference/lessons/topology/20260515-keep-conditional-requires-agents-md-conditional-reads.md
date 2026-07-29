---
id: 20260515-keep-conditional-requires-agents-md-conditional-reads
title: keep_conditional yaml action requires matching AGENTS.md conditional reads entry
topic: topology
extracted_from: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md:kelly-handoff
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
verification_command: python3 scripts/topology_doctor.py --reference-replacement
related: [20260515-ref-replacement-companion-gate-missing]
expected_signature: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
---

# keep_conditional yaml action requires matching AGENTS.md conditional reads entry

## What

When `architecture/reference_replacement.yaml` sets `allowed_action: keep_conditional`
for a reference doc, the topology checker also enforces that the doc's short filename
appears in the "Conditional reads" block of `docs/reference/AGENTS.md`. Violating this
produces a second BLOCKING issue: `reference_replacement_default_read_mismatch`. The
check is in `scripts/topology_doctor_reference_checks.py` at lines 253-260:
`if entry.get("allowed_action") == "keep_conditional" and short not in conditional_reads`.

## Why

During P8 remediation, `zeus_kelly_asymmetric_loss_handoff.md` needed `keep_conditional`
in the yaml matrix, but was absent from AGENTS.md conditional reads. Using `keep_conditional`
without the AGENTS.md companion would resolve the `missing_entry` BLOCKING issue but
immediately create a `default_read_mismatch` BLOCKING issue in its place — trading one
blocker for another. The fix required updating both files atomically.

## How To Apply

When adding a `keep_conditional` yaml stanza for a new reference doc, check
`docs/reference/AGENTS.md` and add the doc to the conditional reads block if absent.
The AGENTS.md entry should describe when to load the doc and what constraints it encodes.
Make both changes in the same commit. Similarly, when adding `default_read: true` /
`allowed_action: keep_default`, verify the doc appears in the "Default reads" block.

## Anti-Pattern

Adding the yaml stanza with `keep_conditional` but not updating AGENTS.md seems to
"fix" the missing_entry BLOCKING issue — but the next topology check immediately fires
`reference_replacement_default_read_mismatch`. The two files must stay in sync. Treat
them as a single atomic unit: yaml stanza + AGENTS.md conditional reads block.

## Provenance

- Originating packet: docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md
- Originating commit: (P8 commit on branch deploy/live-order-e2e-verification-2026-05-15)
- Operator confirmation: IMPLICIT (P8 task assigned by operator)
