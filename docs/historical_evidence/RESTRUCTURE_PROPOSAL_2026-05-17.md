# Evidence Directory Restructure Proposal — 2026-05-17

Prepared by: executor agent (session a6cf3ad3a68006965)
Status: PROPOSAL ONLY — no moves executed. Operator approves before any git mv.

---

## What evidence/ is

`evidence/` holds audit trails, gate decisions, baselines, and override records
from past task packets. Files here are produced during task execution as proof
artifacts — not authority docs, not live state.

Current inventory: 30 loose files + 14 named subdirs (24 files across them).

---

## Named subdirs — context and disposition

These subdirs are already structured. No move needed; they are self-explanatory.

| Subdir | Files | Content | Recommended action |
|--------|-------|---------|-------------------|
| `activation/` | 2 | C1 rollout gate + C3 writer SQL (2026-05-04) | KEEP as-is |
| `baseline/` | 6 | 20h replay friction + false block rate JSON/MD (2026-05-06) | KEEP as-is |
| `baseline_ratchets/` | 0 | Empty | KEEP (may receive future ratchet artifacts) |
| `charter_overrides/` | 1 | Phase 0 shadow gate override (2026-05-06) | KEEP as-is |
| `cotenant_shims/` | 0 | Empty | KEEP |
| `destructive_checkouts/` | 0 | Empty | KEEP |
| `hook_schema_changes/` | 1 | Hook schema change record (2026-05-06) | KEEP as-is |
| `main_regressions/` | 1 | Main regression record (2026-05-07) | KEEP as-is |
| `operator_overrides/` | 0 | Empty | KEEP |
| `operator_signed/` | 0 | Empty | KEEP |
| `replay_baseline/` | 1 | Replay baseline JSON (2026-05-06) | KEEP as-is |
| `secrets_overrides/` | 0 | Empty | KEEP |
| `shadow_router/` | 2 | Shadow router agreement + calibration (2026-05-06) | KEEP as-is |
| `topology_v_next_shadow/` | 1 | Divergence log (2026-05-16) | KEEP as-is |

---

## Loose files — mapping proposal

30 files sit directly in `evidence/`. Each is classified below with a proposed
destination. No moves are executed here.

### Group A: Hook redesign evidence → docs/operations/task_2026-05-06_hook_redesign/

These files are clearly the artifact trail of the hook redesign task packet.
`docs/operations/task_2026-05-06_hook_redesign/` exists.

| File | Proposed destination |
|------|---------------------|
| `hook_common_inventory.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_common_inventory.md` |
| `hook_phase3_decision.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_phase3_decision.md` |
| `hook_phase3r_legacy_test_disposition.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_phase3r_legacy_test_disposition.md` |
| `hook_redesign_critic_opus_final_v2.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_redesign_critic_opus_final_v2.md` |
| `hook_redesign_critic_opus_final.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_redesign_critic_opus_final.md` |
| `hook_redesign_critic_opus.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_redesign_critic_opus.md` |
| `hook_redesign_v2_critic.md` | `docs/operations/task_2026-05-06_hook_redesign/evidence/hook_redesign_v2_critic.md` |

### Group B: Navigation Topology v2 phase decisions → docs/operations/task_2026-05-07_navigation_topology_v2/ (if exists) or evidence/navigation_topology_v2/

The `phase*_h_decision.md`, `phase*_d_*`, `r*` files are phase gate decisions
from the Navigation Topology v2 redesign (`task_2026-05-07_navigation_topology_v2`).
That packet dir was archived; the archived stub was deleted in TASK 3, but the
canonical content is in `docs/archives/packets/` if it was promoted.

**Check first:** `ls docs/archives/packets/ | grep navigation_topology_v2`

If the packet exists in archives: `git mv` these into its `evidence/` subdir.
If not: create `evidence/navigation_topology_v2/` as a local evidence home.

| File | Proposed destination |
|------|---------------------|
| `phase0_d_fossil_audit.md` | `evidence/navigation_topology_v2/phase0_d_fossil_audit.md` |
| `phase0_h_decision.md` | `evidence/navigation_topology_v2/phase0_h_decision.md` |
| `phase1_h_decision.md` | `evidence/navigation_topology_v2/phase1_h_decision.md` |
| `phase2_h_decision.md` | `evidence/navigation_topology_v2/phase2_h_decision.md` |
| `phase3_drift_check.md` | `evidence/navigation_topology_v2/phase3_drift_check.md` |
| `phase3_h_decision.md` | `evidence/navigation_topology_v2/phase3_h_decision.md` |
| `phase3_import_cleanup.md` | `evidence/navigation_topology_v2/phase3_import_cleanup.md` |
| `phase4_gate4_promotion.md` | `evidence/navigation_topology_v2/phase4_gate4_promotion.md` |
| `phase4_h_decision.md` | `evidence/navigation_topology_v2/phase4_h_decision.md` |
| `phase5_d_cutover_log.md` | `evidence/navigation_topology_v2/phase5_d_cutover_log.md` |
| `phase5_h_decision.md` | `evidence/navigation_topology_v2/phase5_h_decision.md` |
| `phase5_replay_rerun.md` | `evidence/navigation_topology_v2/phase5_replay_rerun.md` |
| `r1_closure.md` | `evidence/navigation_topology_v2/r1_closure.md` |
| `r12_disposition.md` | `evidence/navigation_topology_v2/r12_disposition.md` |
| `r12_phase3_resolution.md` | `evidence/navigation_topology_v2/r12_phase3_resolution.md` |
| `r9_inv_gap_audit.md` | `evidence/navigation_topology_v2/r9_inv_gap_audit.md` |

### Group C: Topology v2 critic reviews → evidence/navigation_topology_v2/ (same group)

| File | Proposed destination |
|------|---------------------|
| `topology_v2_critic_opus.md` | `evidence/navigation_topology_v2/topology_v2_critic_opus.md` |
| `topology_v2_pr72_full_critic_opus_v2.md` | `evidence/navigation_topology_v2/topology_v2_pr72_full_critic_opus_v2.md` |

### Group D: Ritual signal baseline → evidence/baseline/ (existing subdir)

| File | Proposed destination |
|------|---------------------|
| `ritual_signal_baseline.json` | `evidence/baseline/ritual_signal_baseline.json` |
| `ritual_signal_baseline.md` | `evidence/baseline/ritual_signal_baseline.md` |

### Group E: Orphan phase closure docs (no clear packet match)

These files reference cross-phase gate closures that don't map cleanly to a
single task packet. Surfaced for operator decision.

| File | Notes |
|------|-------|
| `l3_expiry_guard.md` | L3 expiry guard — possibly task_2026-05-08_alignment_repair_workflow |
| `m1_m5_status.md` | M1/M5 status — ANTI_DRIFT_CHARTER milestones; cross-packet |
| `od2_gate_closure.md` | OD2 gate closure — possibly task_2026-05-09_daemon_restart_and_backfill |

**Operator decision needed:** assign to a packet or promote to `evidence/closeout_decisions/`.

---

## Summary

| Action | Files | Operator approval needed? |
|--------|-------|--------------------------|
| Group A → hook_redesign task dir | 7 files | Yes — git mv |
| Group B+C → navigation_topology_v2 subdir | 18 files | Yes — git mv or new subdir |
| Group D → evidence/baseline/ | 2 files | Yes — git mv |
| Group E — orphan | 3 files | Yes — assign first |
| Named subdirs | 14 dirs | No change needed |

**Total loose files accounted for:** 30 (7+18+2+3 = 30 ✓)

No moves executed. Operator confirms destination for each group, then
`git mv` batch can be run as a single commit.
