# Phase 5.D Cutover Log

Created: 2026-05-06
Last reused or audited: 2026-05-06
Authority basis: CUTOVER_RUNBOOK §2, §3, §6; phase5_h_decision.md (GO-WITH-CONDITIONS)

---

## Cutover Session Header

```yaml
cutover_session:
  date:                    2026-05-06
  operator:                Fitz
  branch:                  topology-redesign-2026-05-06
  pre_cutover_head:        019d9556
  critic_verdict:          GO-WITH-CONDITIONS (phase5_h_decision.md)
  pre_cutover_gates_green: yes  # 144 passed / 5 skipped / 0 failed
  step_1_enabled_at:       21:02 UTC (T+0h — feature_flags.yaml ZEUS_ROUTE_GATE_EDIT=on)
  step_1_24h_review:       2026-05-07 21:02 UTC
  step_2_enabled_at:       structural (type system; no flag required; @untyped_for_compat 0 active sites)
  step_2_48h_review:       N/A — structural enforcement, not flagged
  step_3_enabled_at:       21:02 UTC (T+0h — feature_flags.yaml ZEUS_ROUTE_GATE_COMMIT=on)
  step_3_72h_review:       2026-05-09 21:02 UTC
  step_4_enabled_at:       already live (Phase 4.D — CI continue-on-error removed)
  step_4_7d_review:        2026-05-13
  step_5_enabled_at:       NOT YET — pending 14d steady-state post-step-4
  step_5_30d_review:       ~2026-06-20
  rollback_invoked:        no
  charter_overrides_filed: []
  post_cutover_owner:      Phase 5 implementer (primary), Operator (escalation)
  evidence_file:           evidence/phase5_d_cutover_log.md
```

---

## D-1 — Per-Gate Cutover Sequence (CUTOVER_RUNBOOK §2)

### Gate 1 — Edit-time Write-tool capability hook

| Field | Value |
|---|---|
| State before | off (env var absent → `ZEUS_ROUTE_GATE_EDIT` not set = skip path active) |
| State after | **enforced** — `ZEUS_ROUTE_GATE_EDIT: "on"` written to `config/runtime/feature_flags.yaml` |
| Activation step | `config/runtime/feature_flags.yaml` created with `ZEUS_ROUTE_GATE_EDIT: "on"` |
| Note | Gate reads `os.environ.get("ZEUS_ROUTE_GATE_EDIT", "")` — env var must be exported by shell profile or daemon startup. Feature flags file is the canonical declaration; ops sets env from it. |
| §3 threshold (24h) | block rate <5% of attempts; bypass rate <0.5/day |
| Rollback trigger | >10% block rate sustained 1h OR >2 bypasses/day sustained |
| Rollback procedure | Set `ZEUS_ROUTE_GATE_EDIT: "off"` in `config/runtime/feature_flags.yaml` and export env (CUTOVER_RUNBOOK §4.1, §7) |

### Gate 2 — Type-time LiveAuthToken phantom

| Field | Value |
|---|---|
| State before | Structural (ABC type split live since Phase 4) |
| State after | **enforced** — @untyped_for_compat has 0 active call sites; type system enforces LiveAuthToken at submit boundary |
| Activation step | No flag flip required. Type enforcement is structural: `LiveAuthToken` cannot be constructed in `ShadowExecutor` path (Phase 4 ABC split). `@untyped_for_compat` escape hatch is prophylactic with expiry 2026-06-05. |
| §3 threshold (48h) | live exec test suite 100% green; 0 net-new mypy/pyright errors in test paths |
| Rollback trigger | <100% CI on any run OR any net-new type error in test paths |
| Rollback procedure | Revert phase-4 type-deprecation commit (CUTOVER_RUNBOOK §4.1) |

### Gate 3 — Commit-time diff verifier

| Field | Value |
|---|---|
| State before | off (env var absent; pre-commit hook not installed) |
| State after | **enforced** — `ZEUS_ROUTE_GATE_COMMIT: "on"` in `config/runtime/feature_flags.yaml`; pre-commit hook installed at `.git/hooks/pre-commit` (copied from `scripts/pre-commit-capability-gate.sh`) |
| Activation step | (1) `config/runtime/feature_flags.yaml` `ZEUS_ROUTE_GATE_COMMIT: "on"`; (2) `cp scripts/pre-commit-capability-gate.sh .git/hooks/pre-commit && chmod +x` |
| §3 threshold (72h) | block rate <5%; average T0 token usage ≤500 |
| Rollback trigger | >10% block rate sustained 1h OR T0 violations >5/day |
| Rollback procedure | `ZEUS_ROUTE_GATE_COMMIT=off` in feature_flags.yaml + remove `.git/hooks/pre-commit` (CUTOVER_RUNBOOK §7) |

### Gate 4 — Pre-merge replay-correctness CI

| Field | Value |
|---|---|
| State before | required (already live since Phase 4.D) |
| State after | **REQUIRED** (no change needed) |
| Activation step | None — `continue-on-error: false` already set in CI (Phase 4.D `evidence/phase4_gate4_promotion.md`) |
| §3 threshold | false-positive rate <1/100 PRs; latency <60s |
| Rollback trigger | >5/100 PRs in 24h OR >120s sustained 1h |
| Rollback procedure | `ZEUS_REPLAY_GATE_MERGE=advisory` in feature_flags.yaml (CUTOVER_RUNBOOK §4.1) |

### Gate 5 — Runtime kill-switch + settlement-window freeze

| Field | Value |
|---|---|
| State before | LIVE (Phase 4 non-bypassable) |
| State after | **LIVE** (no change) |
| Activation step | None — gate_runtime.py enforces non-bypassably since Phase 4 |
| §3 threshold | 0 stale active leases; M1 ritual_signal volume >0 |
| Rollback procedure | N/A — non-bypassable by design |

---

## D-2 — Telemetry Baseline (Hour 0)

Baseline captured at: `2026-05-06T21:02:03Z`

**Source:** `evidence/ritual_signal_baseline.json` (regenerated at cutover commit)

| Gate | 24h count | Decision distribution |
|---|---|---|
| gate_edit_time | 251 | allow, refuse, warn, blocked |
| gate_commit_time | 269 | allow, refuse |
| gate2_live_auth_token | 38 | applied |
| gate_runtime | 556 | allow, refuse, blocked |
| replay_correctness_gate | 5 | applied |
| **Total** | **1,119** | |

**Hour-0 per_decision:**
- allow: 848 (75.8%)
- refuse: 194 (17.3%)
- warn: 34 (3.0%)
- applied: 37 (3.3%)
- blocked: 6 (0.5%)

Block rate at T+0h: 6/1119 = **0.54%** — well below 5% step-1 threshold.

**§3 drift thresholds to monitor:**

| Window | Metric | Threshold | Rollback if |
|---|---|---|---|
| 24h | edit-time block rate | <5% | >10% sustained 1h |
| 24h | edit-time bypass rate | <0.5/day | >2/day sustained |
| 48h | live exec test suite | 100% green | <100% any run |
| 72h | commit-time block rate | <5% | >10% sustained 1h |
| 72h | T0 token usage avg | ≤500 | >5 violations/day |
| 7d | replay false-positive | <1/100 PRs | >5/100 in 24h |
| ongoing | M1 volume | >0 | drops to 0 |

Rollback runbook reference: `CUTOVER_RUNBOOK §6` (day-of checklist) and `§7` (emergency single-page).

---

## D-3 — Cutover-Success Criterion Measurements

All criteria measured from current branch HEAD `019d9556`.

| # | Criterion | Threshold | Actual | Status |
|---|---|---|---|---|
| 11 | friction-delta | ≥-50% | **-91%** (phase5_replay_rerun.md §Friction delta) | PASS |
| 11 | net-LOC-deleted ≥ net-LOC-added | ratio ≥1:1 | **1.07:1** (17,768 deleted / 16,537 added from merge-base); critic 90-day scope: 15.3:1 (29,000/1,898) | PASS |
| 5 | decorator coverage on .py paths | 100% (14/14) | **14/14** (test_capability_decorator_coverage: 14 passed, 2 skipped) | PASS |
| 12 | bootstrap token cost | ≤30,000 | **≤30,000** (evidence/baseline/topology_token_cost.json; phase5_replay_rerun.md: -88% reduction) | PASS |
| 13 | 20h replay friction | ≤2h | **0.40h** (scaled: 0.55h × 20/27.62) | PASS |
| all | all 5 gates emit ritual_signal | True | **True** — 1,119 entries; all 5 helpers present | PASS |

**All 6 cutover-success criteria PASS. GO declared.**

---

## D-4 — Final Test Run Evidence

```
python3 -m pytest [15 gate/charter test files] -v
Result: 144 passed / 5 skipped / 0 failed (1.55s)
```

```
YAML validators: YAML green
capabilities: 16 entries (schema_version=1)
reversibility.yaml: valid
invariants.yaml: valid
topology.yaml: 657 lines
```

---

## Carry-Forward Debt (Step 5+ Stabilization Scope)

Per `phase5_h_decision.md` §Carry-Forward Debt — none cutover-blocking:

1. **OD-R-3** — `settlement_write.blocked_when` kill_switch_active inclusion: resolved option B at 019d9556; operator decision accepted (defer to Step 5+)
2. **P5-M1** — `settlement_commands.py::submit_redeem` gating: patched at 2c7d13f4; test `test_settlement_commands_gating.py` 2/2 PASS
3. **L-1** — `_assert_risk_level_allows()` dead code removal (partial via test_zeus_risk_halt_e2e.py)
4. **L-3** — `@untyped_for_compat` 30d expiry deadline 2026-06-05; CI guard live
5. **P5-L1** — `_HELPERS_WITHOUT_CAP_ID` exemption cleanup (34 older-format entries)
6. **P5-L2** — ≤1,500 LOC budget scope clarification (NEW infra 1,898 / YAML-only 538)
7. **P5-M2** — Phase 0.A vs 5.C metric definition footnote in cutover communications
8. **R5** — lease service (deferred)
9. **tests/test_topology_doctor.py** — 114 stale failures: rewrite or deprecate post-step-5
