# Replacement-Forecast Chain — Go-Live Arm Gap (CURRENT STATE)

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: read-only audit of live DBs (`state/zeus-forecasts.db` 38.7 GB, `zeus_trades.db` 26 GB, `zeus-world.db` 45 GB), `config/settings.json`, `docs/operations/current_*.md`, and the go-live report / runtime-policy / switch-decision / refit-gate / live-dry-run / schema modules. NOT authority law; audit-bound current fact only.
- Strategy: `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor`
- Scope: what stands between SHADOW_ONLY and LIVE_PROMOTION_READY, assessed against live data on 2026-06-16.

## TL;DR — the framing in the request is half-true; the live truth is split in two

There are **two different "go-live" objects** and they have diverged:

1. **The runtime ARM (what actually lets the chain place live orders).** This is **already ON.** `runtime_policy.status = LIVE_AUTHORITY`, `can_initiate_trade = True`; the operator arm `edli.edli_live_operator_authorized = True`; the full flag ladder (shadow→veto→trade_authority→kelly_increase→direction_flip) is all `True`; `edli_live_scope = forecast_plus_day0`. The reactor's live replacement path (`_replacement_authority_probability_and_fdr_proof`) is enabled and **reads the SHADOW_ONLY posteriors and accepts them** (the bundle reader admits `{SHADOW_ONLY, SHADOW_VETO_ONLY}`). Per OPERATOR DIRECTIVE 2026-06-08, the promotion/capital-objective evidence gate was **removed** from `runtime_policy` and `switch_decision`; live authority is FLAG-ONLY.

2. **The go-live readiness REPORT (`replacement_forecast_go_live_report.py`).** Run against live state it returns **`status: BLOCKED`** — but its three remaining blockers (`before_after`, `capital_replay`, `live_dry_run`) are the **circular "prove after-cost before trading" promotion bureaucracy the operator deliberately severed from the arm.** This report is *evidence composition only* (its own closing line: "cannot place orders … or authorize live promotion"). It is NOT the arm. It will stay BLOCKED until weeks of settled after-cost evidence accrue, and that is by design — it no longer gates anything live.

So: **`trade_authority_status='SHADOW_ONLY'` is a CHECK-locked PROVENANCE STAMP on research-accrual tables, NOT the live-trade switch.** The chain is *armed*. What actually throttles live orders right now is the **forward-risk gate the operator kept on purpose**: the FUSED_NORMAL q-mode eligibility gate, which today rejects **~63 % of live posteriors** because their multi-model fusion capture is missing.

## Live ground-truth (queried 2026-06-16 ~10:15Z)

- `forecast_posteriors` (soft_anchor): **6,008 rows, 100 % `trade_authority_status='SHADOW_ONLY'`, `training_allowed=0`.** Latest `computed_at = 2026-06-16T10:13Z` (materializer is live & fresh). target_date span 2026-06-08 … 2026-06-17.
- **q-mode of the latest 2,000 live posteriors** (the field the live submit gate actually reads):
  - `BAYES_PRECISION_FUSION_CAPTURE_MISSING` = **1,266 (63 %)** — `q_shape=aifs_member_votes_soft_anchor`, `q_lcb_basis=wilson_aifs_member_votes` → **NOT live-eligible** (q-mode gate rejects; only FUSED_NORMAL_FULL/PARTIAL pass).
  - `FUSED_NORMAL_PARTIAL` = 630, `FUSED_NORMAL_FULL` = 104 → **734 (37 %) live-eligible**, `q_lcb_basis=fused_center_bootstrap_p05`.
  - All 2,000 have a non-null `q_lcb_json`.
- `replacement_shadow_decisions`: **0 rows** (CHECK-locked `SHADOW_VETO_ONLY`). The shadow-decision lane is emitting nothing despite veto_enabled — separate observability gap, not a live blocker.
- `readiness_state` (soft_anchor): **549 READY, 101 BLOCKED**; latest READY `computed_at=10:13Z`, `expires_at=13:13Z` (3 h freshness window, current).
- `raw_forecast_artifacts` lineage: openmeteo_ecmwf_ifs_9km = 4,227; ecmwf_aifs_ens = 97 → lineage READY.
- Current-target coverage (`build_replacement_forecast_current_target_plan`): **`MISSING_REPLACEMENT_FUTURE_TARGET_COVERAGE`** — 96 future targets, 90 missing posteriors. The missing cohort is **2026-06-18** (next-day markets exist; posteriors only materialized through 06-17). Pure freshness lag, self-healing as the materializer advances; matches the 06-15 commit note "residual 0-coverage is no-open-inventory, not a bug."
- `config/settings.json` (MAIN tree = what the live daemon reads) and the worktree agree on every flag below.

## Go-live REPORT result against live state (real `promotion_evidence.json` + live inventory)

```
status: BLOCKED
runtime_policy_status: LIVE_AUTHORITY      switch_decision_status: LIVE_AUTHORITY
switch_can_initiate_trade: True
source_fact_status: CURRENT_FOR_LIVE       data_fact_status: CURRENT_FOR_LIVE
config_switch_status: READY
before_after_official_days: 3  rows: 28    after_cost_delta: -10.71  (replacement LOSES to legacy)
blockers: { before_after, capital_replay, live_dry_run }   # refit/current_facts/rollback/switch_decision/live_switch/config_switch all CLEAR
```

## Blocker-by-blocker (every gate, ACTIVE/CLEAR, live evidence, clearing action, who clears it)

| # | Blocker (go-live report key) | State | Live evidence | What clears it | Action class |
|---|---|---|---|---|---|
| 1 | **current_facts** (source_fact_status / data_fact_status) | **CLEAR** | Both `docs/operations/current_source_validity.md` and `current_data_state.md` line 3 = `Status: CURRENT_FOR_LIVE`; `Last audited 2026-06-07T00:50Z`, max-staleness 14 d → valid until ~06-21. `_current_fact_status` reads them CURRENT. | Already clear. Re-audit both docs before 2026-06-21 to keep within the 14-day window (else they silently revert to STALE). | doc refresh (operator/maintenance) |
| 2 | **refit** (`product_specific_training_allowed`) | **CLEAR** | `state/replacement_forecast_shadow/refit_handoff.json`: `status=REFIT_HANDOFF_READY`, `refit_decision.product_specific_training_allowed=true`, `PRODUCT_SPECIFIC_REFIT_READY`, `emos_key_schema=replacement_product_keyed_v1`, `emos_identity_evidence_status` satisfied, 5 days/250 rows, bucket rows 250. NOTE: this is the *gate verdict* (handoff). The DB `training_allowed` column is still CHECK-locked `=0`; product-specific *training* has not actually executed — but the gate that the report consults is GREEN. | Already clear at the report level. To actually run product-specific training you must also lift the `training_allowed CHECK(=0)` (see #5). | offline fit already produced the handoff; CLEAR |
| 3 | **rollback** | **CLEAR** | Report run shows no `rollback` blocker: rollback plan carries `feature_flag_updates` and lists `delete_shadow_rows` in `prohibited_actions` (`rollback_reversible=True`). | Already clear. | code (CLEAR) |
| 4 | **switch_decision** | **CLEAR** | `switch_decision_status=LIVE_AUTHORITY`, `can_initiate_trade=True`. Post-2026-06-08 the refit-live-promotion + capital-objective vetoes were removed from this resolver. | Already clear. | code (CLEAR) |
| 5 | **live_switch** (read surface / facts / tables / evidence gates) | **CLEAR** | No `live_switch` blocker: all REQUIRED_LIVE_READ_FILES present, forecast/world/trade tables present, evidence gates satisfied, facts CURRENT. | Already clear. | code (CLEAR) |
| 6 | **config_switch** | **CLEAR** | `config_switch_status=READY`, no JSON patch outstanding (`build_replacement_forecast_live_authority_config_switch_plan` returns READY because trade_authority flag + promotion_evidence present). | Already clear. | code (CLEAR) |
| 7 | **runtime_policy** | **CLEAR** | `runtime_policy.status=LIVE_AUTHORITY`, `can_initiate_trade=True`, reason `REPLACEMENT_PROMOTED_WITH_EVIDENCE` (legacy name; flag-only per 2026-06-08). | Already clear. | flag (CLEAR) |
| 8 | **before_after** (after_cost_delta > 0) | **ACTIVE — but DE-BOUND from the arm** | Real `promotion_evidence.json` before_after: 28 rows over **3** official days (need ≥5 days, ≥250 rows). Σ baseline after-cost PnL −26.76, Σ replacement −37.47 → **after_cost_delta = −10.71 (NEGATIVE; replacement underperforms legacy on this tiny cohort)**; bucket regressions present; brier_delta −0.0164 (replacement Brier better). | To CLEAR *the report*: accumulate ≥5 official-truth days / ≥250 SCORED rows whose settlement-graded replacement after-cost PnL beats legacy with no negative guardrail bucket. This is settled-outcome accrual over time — it CANNOT be produced offline; it requires the chain to keep running (and ideally to trade) and markets to settle. It does NOT gate the live arm (the 2026-06-08 directive removed it from runtime_policy/switch_decision). | settled-data accrual over time (NOT a code/worktree fix; NOT an operator write) |
| 9 | **capital_replay** (`promotion_grade`) | **ACTIVE — DE-BOUND from the arm** | `promotion_evidence.json` capital_replay: `status=EMPIRICAL_WINNER`, selected_label matches `…_w0.80_sigma3.00`, source_availability `observed` / 0 violations, BUT `coverage.promotion_grade=False`, `promotion_blocker="capital replay uses live DB raw artifact source time; live promotion still requires product-specific refit/EMOS and broader official cohort evidence"`, evidence_grade `shadow_economic_with_live_db_raw_artifact_source_time`, rows 200 / skipped 326. | Same accrual as #8 plus actually-executed product-specific refit/EMOS. De-bound from the live arm. | settled-data accrual + offline refit (NOT the arm) |
| 10 | **live_dry_run** | **ACTIVE — transient/freshness** | `live_dry_run.status=BLOCKED`, sole reason `REPLACEMENT_DRY_RUN_CURRENT_TARGET_COVERAGE_NOT_READY`. Every *sub-status* is READY (raw_artifact_lineage READY, latest_readiness_artifact READY, configured_refit_handoff READY). The block is purely the 2026-06-18 future-target coverage gap (90 future targets, posteriors materialized only through 06-17). | Self-heals as the materializer advances to cover the next day's open markets. No code/operator action; just time + the materializer cycle (interval 5 min). | runtime freshness (self-healing) |
| — | **emos_identity_evidence / calibration coverage** | **CLEAR at gate level** | `refit_handoff.json` carries the product-keyed EMOS cell key (7 parts, `replacement_product_keyed_v1`) and a satisfied identity status; calibration q_lcb basis on the eligible posteriors is `fused_center_bootstrap_p05`. The MISSING template default is overridden by the live handoff. | Already clear for the report. | offline fit (CLEAR) |

## The schema CHECK (`trade_authority_status`) — what an unlock would actually require, and whether it is needed

`src/state/schema/v2_schema.py` CHECK-locks the value to SHADOW-only on every replacement table:

- `raw_forecast_artifacts` (302), `deterministic_forecast_anchors` (336), `raw_model_forecasts` (495): `CHECK (trade_authority_status IN ('SHADOW_ONLY'))`
- `forecast_posteriors` (378): `CHECK (… IN ('SHADOW_ONLY','SHADOW_VETO_ONLY'))`
- `replacement_shadow_decisions` (421): `CHECK (… IN ('SHADOW_VETO_ONLY'))`
- All five also `CHECK (training_allowed = 0)`.

**There is NO `LIVE_ELIGIBLE` value in any of these CHECKs.** `LIVE_ELIGIBLE` appears 160× elsewhere in the repo (calibration-serving / readiness vocabulary), but it is NOT the value these tables can hold.

**Is a migration on the critical path to live trading? NO — verified by reading the consumers.** The live-trade consumer (`event_reactor_adapter._replacement_authority_probability_and_fdr_proof` → `read_replacement_forecast_bundle`) **explicitly admits `{SHADOW_ONLY, SHADOW_VETO_ONLY}`** (`replacement_forecast_bundle_reader.py:112` raises only if the value is *outside* that set). Every other consumer (`current_target_plan`, `seed_discovery`, `event_payload`, `live_dry_run`) filters `IN ('SHADOW_ONLY','SHADOW_VETO_ONLY')`. So a SHADOW_ONLY posterior **can already drive a live 0/1 probability**; the stamp is a provenance label for a research-accrual surface, not a trade switch. A migration to a `LIVE_ELIGIBLE` value would be a *labeling clean-up* (and would actually require touching all those `IN (…)` readers to accept it) — it is NOT what's between the chain and live orders. The live switch is the flag + operator arm, both already ON.

## What ACTUALLY throttles live orders right now (the operator-kept forward-risk gate)

The arm is on; the binding live constraint is the gate the operator deliberately retained:

1. **FUSED_NORMAL q-mode eligibility** (`event_reactor_adapter._replacement_q_mode_live_eligibility`, eligible set = {FUSED_NORMAL_FULL, FUSED_NORMAL_PARTIAL}). **63 % of live posteriors are `BAYES_PRECISION_FUSION_CAPTURE_MISSING`** → deterministic no-submit (sizes Kelly under the wrong probability regime). Only the 37 % fused-Normal rows can submit. **This is the highest-leverage real gap: fix the multi-model BAYES_PRECISION_FUSION capture so more posteriors materialize as FUSED_NORMAL.** This is genuine correctness/coverage work in code + the capture pipeline.
2. **Settlement-backward coverage ARM gate** (`settlement_backward_coverage.arm_gate_coverage_blocks`): blocks ONLY on `UNLICENSED` (settled record PROVES overconfidence); `LICENSED`/`INSUFFICIENT_DATA` pass (license-by-default on thin data). Not a default-deny.
3. **q_lcb settlement-σ floor + fractional Kelly + direction law + RiskGuard**: enforced downstream; unaffected by any of the above.

The 2026-06-15 market-anchor cap was **RETIRED** by the operator ("fix live + stop anchoring our forecast to the market price"); the calibrated forecast q_lcb is now the live authority.

## Shortest path to LIVE_PROMOTION_READY (the report status) vs. to live orders flowing

These are different finish lines. State both honestly:

**A. To make live orders flow (the operator's actual goal — the arm is already ON):**
1. **Nothing in the arm chain is blocking.** Flags + operator arm + scope are ON; runtime_policy=LIVE_AUTHORITY; the reactor accepts SHADOW_ONLY bundles.
2. **Raise FUSED_NORMAL yield** — fix `BAYES_PRECISION_FUSION_CAPTURE_MISSING` (63 % of posteriors) so the q-mode gate admits them. **(code + capture pipeline — highest leverage.)**
3. Let the 06-18 current-target coverage gap self-heal via the materializer (transient). 
4. (Optional, separately tracked) investigate why `replacement_shadow_decisions` has 0 rows.
   None of these is an operator live-write to a DB; the operator live-writes (`edli_live_operator_authorized`, the flag ladder, `edli_live_scope`) are **already done**.

**B. To flip the go-live REPORT to LIVE_PROMOTION_READY (NOT required for the arm, by operator directive):**
- Accrue ≥5 official-truth days / ≥250 settled SCORED rows with **positive replacement-vs-legacy after-cost delta and no negative guardrail bucket** (#8), and a promotion-grade capital replay (#9). This is **settled-outcome accrual over time** — un-fakeable, un-shortcuttable, and only earned by the chain running live. The report stays BLOCKED until then, and that is the intended, harmless state (it is evidence-composition only). Per memory `success-criterion-no-fixed-number`, the real bar is continuous settlement-graded positive after-cost EV + calibration coverage, not this report's `LIVE_PROMOTION_READY` flag.

## Action classification summary

- **Code / worktree:** raise FUSED_NORMAL capture yield (fix `BAYES_PRECISION_FUSION_CAPTURE_MISSING`); optional `replacement_shadow_decisions`-empty investigation; optional schema label clean-up (NOT on the trade path).
- **Offline fit / script:** product-specific refit/EMOS (handoff already READY; actual training still gated by `training_allowed CHECK(=0)` if you want DB-persisted trained models).
- **Operator live-write:** **already done** (operator arm, flag ladder, scope all ON). The only recurring operator/maintenance task is re-auditing the two `current_*.md` facts before the 14-day staleness window lapses (~2026-06-21).
- **Time / settled-data accrual (no human action):** the go-live report's before_after (#8) + capital_replay (#9); the live_dry_run 06-18 coverage gap (#10) self-heals on the next materializer cycles.

## Determinability caveats

- The DB `training_allowed=0` CHECK means no product-specific *trained* model is persisted; the refit *gate* verdict (handoff) is READY but the trained-artifact existence could not be confirmed from the DB (no LIVE/trained rows exist by construction). 
- Whether live orders are *currently being emitted* by the FUSED_NORMAL-eligible 37 % was not confirmed from `zeus_trades.db` in this pass (the trade-emission audit is a separate question from the arm-gap audit requested here); the arm and the gates are confirmed, the realized submit count is not.
