# Archived to-do-list workbooks — 2026-04-26

Archive batch closed 2026-04-26. Verdicts and closure evidence audit:
`docs/operations/task_2026-04-26_live_readiness_completion/evidence/audit_2026-04-26.md`.

| File | Verdict | Successor |
|------|---------|-----------|
| `zeus_midstream_fix_plan_2026-04-23.md` | CLOSED — W1-4 complete (30/30 commits verified); W5 substrate-deferred (live-B3 + ≥30 settlements) | `docs/operations/task_2026-04-23_midstream_remediation/` (operational evidence) + `docs/operations/known_gaps.md` for residual W5 dependencies |
| `zeus_midstream_trust_upgrade_checklist_2026-04-23.md` | CLOSED — companion verdict source for the joint plan | (same as above) |
| `zeus_data_improve_bug_audit_100_dual_track_reassessment.md` | CLOSED — DT Program closed via `d717647`; 100/100 routed | bug100 closure absorbed into Dual-Track refactor commits |
| `zeus_bug100_reassessment_table.csv` | CLOSED — companion machine-readable table (78 closed, 4 forwarded, 1 SEMANTICS_CHANGED) | (same) |
| `zeus_live_readiness_upgrade_checklist_2026-04-23.md` | PARTIAL → ABSORBED — open items moved to active packet | `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` |

## Archive policy

These files are **historical cold storage**. They are not peer authority to
`architecture/**`, active packet docs, or active source/tests. Read them only
to understand a closed verdict's reasoning, not to extract still-load-bearing
rules. Any rule that survives the closure was extracted to a machine manifest,
test, contract, or lore card per `docs/to-do-list/AGENTS.md` rule.

Do NOT modify the archived files in place. If you need to update an extracted
rule, update the durable surface (test, contract, manifest, lore card) and
leave the archive body intact as evidence.
