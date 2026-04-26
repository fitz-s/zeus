# Live-Readiness Completion — Remaining Antibodies

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: source workbook `docs/to-do-list/zeus_live_readiness_upgrade_checklist_2026-04-23.md` (pro/con-Opus debate converged verdict, 2026-04-23). This packet absorbs the workbook's open items into a planning surface and prepares implementation slices.
Status: planning evidence; not authority. Implementation has NOT begun. No code/DB mutation in this packet body.
Companion: `docs/to-do-list/zeus_operations_archive_deferrals_2026-04-24.md` D1+D2 (operator-decision dependents — kept on the to-do-list workbook, NOT folded here).

## 0. Scope statement

This packet plans the **remaining live-readiness antibodies** that are:
1. Open per the 2026-04-23 live-readiness checklist (B/G/U/N items).
2. Not currently owned by another active agent worktree (verified 2026-04-26 against `zeus-pr18-fix-plan-20260426` HEAD `7ebed4e` and `zeus-fix-plan-20260426` HEAD `dfa36a9`).
3. Implementable without operator/substrate decisions still pending elsewhere.

The packet does NOT:
- Implement any slice. Each implementation slice is a separate child packet (`task_2026-04-XX_<slice>/`) when launched.
- Mutate production DB rows, schema, or views.
- Touch venue-command lifecycle (owned by `zeus-pr18-fix-plan-20260426`).
- Touch Phase 3 midstream-trust files (owned by `zeus-fix-plan-20260426`).
- Force a TIGGE substrate decision (B3, G8 — operator-locked via D1).

## 1. Source workbook closure premise

The 2026-04-23 live-readiness verdict enumerated 13 BLOCKERs+CONDITIONALs (B1–B5, G5–G10, R1, U1) plus 9 NICE-TO-HAVE hygiene items (N1.1–N1.9). Audit 2026-04-26 (evidence/audit_2026-04-26.md) finds:

| Family | Total | Closed | Open here | Out-of-scope |
|---|---|---|---|---|
| B (data-substrate) | 5 | B1✅, B5🟡(test only) | B2, B4, B5-backfill | B3 (TIGGE — D1 operator) |
| G (category-impossible gates) | 6 | none | G5, G6, G7, G9, G10 | G8 (depends B3) |
| R/U (resilience) | 2 | none | U1 | R1 (depends G9) |
| N (hygiene) | 9 | none confirmed | N1.1–N1.9 | — |

11 items in scope here; 3 explicitly deferred (B3/G8/R1) with documented gating; 0 ceded to another worktree (D3 venue_commands handled separately).

## 2. Why this packet exists (Fitz K<<N framing)

The 11 open items collapse into K=4 structural decisions:

| K | Decision | Items | Antibody class |
|---|---|---|---|
| **K1** | Make wrong code unconstructable at boot | G5, G6, G7 | typed frozensets + boot/pre-CLOB asserts |
| **K2** | Diagnosable failure mode (not silent / not tombstone-only) | G9 | structured artifact write before tombstone |
| **K3** | Decouple operationally co-resident layers | G10, B2 | new `scripts/ingest/` + `scripts/backfill_*` modules with isolation antibodies |
| **K4** | Constitutional review for divergent rounding | U1 | `SettlementSemantics` HK floor path + AGENTS.md update |

Plus operational hygiene N1.1–N1.9 as Wave 0 (independent micro-slices).

B4 (obs_v2 physical-bounds CHECK + per-city source-binding test) sits across K1+K3 (typed bound at write + isolation per source). B5-backfill (DST flag retroactive fill) is purely a one-shot script — Wave 0.

## 3. Authority order followed

Per `zeus/AGENTS.md`:
- **Authority**: `docs/authority/zeus_current_delivery.md`, `docs/authority/zeus_current_architecture.md`, INV/NC manifests.
- **Lifecycle**: every new file carries `# Lifecycle:` header per L20–L21 protocol.
- **Test relationships before code** (Fitz §1): each slice ships antibody test FIRST, asserts current red, then implementation flips to green.
- **Planning-lock awareness**: G6 / G7 / G8 / G10 partial all touch `src/main.py` boot path → planning-lock on architecture/**, src/main.py merges. This packet pre-clears the planning-lock by enumerating exact hunks per slice.

## 4. Worktree-collision audit (2026-04-26)

| Slice | Target file(s) | Conflicts with worktree | Disposition |
|---|---|---|---|
| B2 settlement backfill | `scripts/backfill_settlement_values.py`, `scripts/compute_settlement_winning_bins.py`, `tests/test_settlement_bin_resolution_complete.py` | None — all NEW files | Wave 1 SAFE |
| B4 physical-bounds CHECK | `src/data/observation_instants_v2_writer.py`, `tests/test_obs_v2_physical_bounds.py`, new per-city source-binding test | None — neither active worktree touches obs_v2 writer | Wave 1 SAFE |
| B5 DST backfill | `scripts/backfill_dst_missing_local_hour.py` (new), extend existing `tests/test_ingestion_guard.py` DST coverage to obs_v2 row | None | Wave 1 SAFE |
| G5 paper/live DB isolation | `tests/test_paper_live_db_isolation.py` (new) + READ from `src/state/db.py` | `src/state/db.py` touched by `zeus-fix-plan-20260426` (P2-fix5/P2 fix6) AND `zeus-pr18-fix-plan-20260426` (venue_commands schema) | Wave 2 — gate on both worktrees merging or rebasing |
| G6 LIVE_SAFE_STRATEGIES | `src/control/control_plane.py` module constant + `src/main.py` boot assert + `tests/test_live_safe_strategies.py` | `src/control/control_plane.py`: NO conflict; `src/main.py`: NO worktree currently touches it | Wave 1 SAFE |
| G7 LIVE_SAFE_CITIES | `src/execution/executor.py` pre-CLOB assert + `tests/test_live_safe_cities.py` | `src/execution/executor.py` touched by `zeus-pr18-fix-plan-20260426` (decorative-fields removal) | Wave 2 — gate on pr18 P0 merging |
| G9 auto-pause diagnostics | `src/engine/cycle_runner.py:381-386` + `state/auto_pause_diagnostics/` writer + `tests/test_auto_pause_diagnostics.py` | `src/engine/cycle_runner.py` touched by BOTH worktrees | Wave 2 — gate on both worktrees |
| G10 ingest decoupling (scaffold) | `scripts/ingest/__init__.py`, `_shared.py`, 8 tick scripts, `tests/test_ingest_isolation.py`, 8 launchd plists | NEW subdir — no conflicts | Wave 1 SAFE — scaffold only |
| G10 ingest decoupling (cutover) | `src/main.py` removal of `_k2_*_tick` + `_ecmwf_open_data_cycle` from scheduler | Touches `src/main.py` boot wiring | Wave 2 — gate on G10-scaffold green + scheduled launchd live for ≥1 cycle |
| U1 HK floor rounding | `src/contracts/settlement_semantics.py` HK path + `tests/test_hk_settlement_floor_rounding.py` + AGENTS.md L49 constitutional review | NO conflict — neither worktree touches settlement_semantics | Wave 1 SAFE |
| N1.1 Meteostat dropout | runbook + bulk endpoint probe + `scripts/fill_obs_v2_meteostat.py` re-run | None | Wave 0 |
| N1.2 Lagos gap | per midstream_fix_plan #23 already FIXED 2026-03-31 (retroactive accounting) | — | Wave 0 — verify only |
| N1.3 0-byte db delete | `state/observations.db` orphan removal | None | Wave 0 |
| N1.4 delta_rate_per_h | populate or mark reserved + absence test | None | Wave 0 |
| N1.5 solar_daily 5-city | refresh script | None | Wave 0 |
| N1.6 spot-check 0.0°C | manual receipt | None | Wave 0 |
| N1.7 ogimet_metar_fact | promote/deprecate decision | None | Wave 0 |
| N1.8 Warsaw validator | extend `availability_fact` validator coverage | None | Wave 0 |
| N1.9 legacy `observation_instants` DROP | `grep -rn "observation_instants[^_]" src/ scripts/` cleanup + table DROP after Gate F dwell | None | Wave 0 — gated on +30 day dwell from Gate F |

**Verdict**: 7 slices Wave-1-safe (B2, B4, B5-backfill, G6, G10-scaffold, U1, plus N1.x as Wave 0); 4 slices Wave-2-gated on other worktrees (G5, G7, G9, G10-cutover).

## 5. Sequencing

### Wave 0 — operational hygiene (N1.1–N1.9, ~6h)
Independent micro-slices. Can ship in any order, in a single child packet `task_2026-04-XX_n1_operational_hygiene/`. N1.9 (legacy table DROP) gated on +30-day dwell from Gate F backfill — defer to a tombstoned wakeup if dwell not reached.

### Wave 1 — parallel-safe antibodies (~5 engineer-days)
- **K1.G6** LIVE_SAFE_STRATEGIES typed frozenset + boot assert (~0.5d)
- **K3.B2** settlement backfill scripts + bin-resolution antibody (~1d)
- **K3.G10-scaffold** `scripts/ingest/*` skeleton + isolation antibody (~1.5d, no `src/main.py` changes yet)
- **K1+K3.B4** obs_v2 physical-bounds CHECK + per-city source-binding test (~1d)
- **K3.B5-backfill** DST retroactive fill script + obs_v2 row coverage extension (~0.5d)
- **K4.U1** HK floor rounding + constitutional review packet (~0.5d, plus operator review time)

Each slice is its own child packet with plan + scope.yaml + work_log + receipt. Lands independently; no cross-deps within Wave 1.

### Wave 2 — coordinated antibodies (gated on other worktrees, ~3 engineer-days)
- **K1.G7** LIVE_SAFE_CITIES executor.py assert — wait for `zeus-pr18-fix-plan-20260426` to merge / rebase
- **K1.G5** paper/live DB isolation antibody — wait for both worktrees' `db.py` edits to settle
- **K2.G9** auto-pause diagnostics structured-artifact write — coordinate with both worktrees on `cycle_runner.py`
- **K3.G10-cutover** remove `_k2_*_tick` from `src/main.py` — only after G10-scaffold green + launchd cycle proven

### Out-of-scope (explicit handoffs)
- **B3 calibration refit** — TIGGE substrate; ensemble_snapshots_v2 still empty per `current_state.md:74-76`. Gated on D1 operator decision in `zeus_operations_archive_deferrals_2026-04-24.md §D1`.
- **G8 live-boot precondition on calibrated v2** — depends on B3 substrate. Defer until B3 closes.
- **R1 auto-pause RCA / decision-to-defer-resume** — depends on G9 first (per workbook). Will be re-planned after G9 lands.
- **D3 venue_commands spine** — owned by `zeus-pr18-fix-plan-20260426` (P1.S1 already landed at `0a7845f`).

## 6. Acceptance criteria (per child packet, not this planning packet)

Each Wave 1 / Wave 2 slice is acceptance-tested by:
1. New antibody test exists with `# Lifecycle:` + `# Purpose:` + `# Reuse:` headers.
2. Test was RED before implementation (committed as red, then flipped — Fitz §1).
3. `pytest -q tests/<antibody>` green.
4. No regression on `tests/test_architecture_contracts.py`, `tests/test_cross_module_invariants.py`, `tests/test_live_safety_invariants.py`.
5. New file paths registered in `architecture/test_topology.yaml` and (where applicable) `architecture/script_manifest.yaml`.
6. Per-slice `scope.yaml` + `receipt.json` filed under the child packet.

This packet's own acceptance:
- Plan + scope.yaml + audit evidence committed to `docs/operations/task_2026-04-26_live_readiness_completion/`.
- Source workbook `zeus_live_readiness_upgrade_checklist_2026-04-23.md` archived to `docs/to-do-list/archive/2026-04-26_closed/` with a redirect note pointing here.
- 4 closed sibling workbooks (`midstream_fix_plan_2026-04-23`, `midstream_trust_upgrade_checklist_2026-04-23`, `bug100_dual_track_reassessment`, `bug100_reassessment_table.csv`) archived alongside.
- `docs/to-do-list/AGENTS.md` registry updated.
- `current_state.md` is NOT altered by this packet (it remains pointed at the midstream remediation mainline; this is a parallel infrastructure-completion packet, not the active execution packet).

## 7. Cross-references

- Source workbook: `docs/to-do-list/zeus_live_readiness_upgrade_checklist_2026-04-23.md` (post-archive: `docs/to-do-list/archive/2026-04-26_closed/`).
- Sibling open workbook (D1/D2 pending operator): `docs/to-do-list/zeus_operations_archive_deferrals_2026-04-24.md`.
- Active worktree owning D3 (venue_commands): `/Users/leofitz/.openclaw/workspace-venus/zeus-pr18-fix-plan-20260426/docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/`.
- Active worktree owning Phase 2/3 midstream-trust adjacents: `/Users/leofitz/.openclaw/workspace-venus/zeus-fix-plan-20260426/docs/operations/task_2026-04-26_full_data_midstream_fix_plan/`.
- Authority anchors: `docs/authority/zeus_current_architecture.md`, `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`.
- Audit evidence (verdicts table + collision matrix): `evidence/audit_2026-04-26.md`.

## 8. Provenance

Written 2026-04-26. Verdicts verified by direct git log + filesystem inspection (no memory-cited data). Worktree HEADs sampled live: `zeus-fix-plan-20260426@dfa36a9`, `zeus-pr18-fix-plan-20260426@7ebed4e`. All 30 closure-claim commits in `midstream_fix_plan_2026-04-23` independently verified reachable on this clone.
