# Object-Meaning Invariance Wave 12 Plan

## Scope

Boundary selected: RiskGuard bankroll-of-record/equity evidence -> derived operator status/report surface.

This wave is derived/read-model only. It does not authorize live unlock, live venue side effects, production DB mutation, schema migration, RiskGuard policy changes, risk-allocation changes, backfill, settlement harvest, redemption, or legacy relabeling.

## Route Evidence

- Root `AGENTS.md` and `src/observability/AGENTS.md` were read.
- `python3 scripts/topology_doctor.py --task-boot-profiles` returned `topology check ok`.
- First status-summary route attempts were `generic`/`advisory_only` because `src/observability/status_summary.py` is high-fanout and existing profiles only covered historical slices (`execution capability`, `canonical snapshot authority`, `v2 row counts`).
- A topology compatibility repair added the narrow profile `object meaning operator status bankroll semantics`, admitting only the derived operator status bankroll/equity semantics surface and forbidding RiskGuard, risk allocator, state, execution, engine, DB, schema, and live side-effect edits.
- New route command admitted this packet, `docs/operations/AGENTS.md`, `src/observability/status_summary.py`, `scripts/equity_curve.py`, `tests/test_phase10b_dt_seam_cleanup.py`, `tests/test_backtest_skill_economics.py`, `tests/test_pnl_flow_and_audit.py`, and `architecture/improvement_backlog.yaml`.
- Registering the Wave12 packet surfaced stale `docs/operations/AGENTS.md` registry entries for already-archived/missing packets. A narrow `docs navigation cleanup` route admitted the registry cleanup; stale live rows were removed and current Wave7/Wave8/Wave11/Wave12 packet rows were registered.

## Candidate Boundaries

| Candidate | Live-money relevance | Values crossing | Downstream consumers | Stale/bypass risk | Repair scope |
| --- | --- | --- | --- | --- | --- |
| RiskGuard bankroll details -> status_summary portfolio fields | Operator status influences manual monitoring/readiness interpretation; status output feeds reports/tools | `initial_bankroll`, `effective_bankroll`, `total_pnl`, `bankroll_truth_source`, `bankroll_truth` | status JSON, operator dashboards, equity/report scripts | pre-repair fallback could synthesize bankroll from analytics PnL when RiskGuard details omitted wallet equity | Safely scoped after new route |
| DB current-position read model -> RiskGuard provenance | Risk/exposure may consume numerically compatible but provenance-blind rows | `size_usd`, `cost_basis_usd`, fill authority fields | RiskGuard, monitor, status | route-blocked residual from Wave11 | Deferred: RiskGuard source forbidden in this wave |
| Corrected fill economics -> replay/report cohort gates | Can corrupt learning/report attribution if legacy/corrected mixed | fill/cost authority, cohort marker | replay, reports, learning exports | existing cohort gates appear strong | Not highest-risk for this wave |

Selected: RiskGuard bankroll/equity evidence -> `status_summary` derived operator read model.

## Lineage Table

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `risk_details.initial_bankroll` | Wallet-equity snapshot used as trailing-loss bankroll reference | `src/riskguard/riskguard.py::tick` latest `risk_state.details_json` | RiskGuard / `polymarket_wallet` when `bankroll_truth_source=polymarket_wallet` | USD wallet equity | risk check time | copied into status | `state/status_summary.json` | operators/reports | preserve |
| `risk_details.effective_bankroll` | Current bankroll-of-record/equity object | `src/riskguard/riskguard.py::tick` | RiskGuard / canonical wallet evidence | USD wallet equity | risk check time | copied into status when present | `state/status_summary.json` | operators/reports | preserve |
| `status.portfolio.total_pnl` | Analytics/reporting PnL, not bankroll truth | strategy health + position-current status summary | derived report evidence | USD PnL | status generation time | sum realized/unrealized if missing upstream | `state/status_summary.json` | operators/reports | preserve as analytics only |
| `cycle_summary.wallet_balance_usd` | Cycle-observed wallet balance | cycle summary input | runtime summary evidence | USD wallet equity | cycle time | fallback bankroll input when RiskGuard lacks initial bankroll | `state/status_summary.json` | operators/reports | explicit fallback |
| `bankroll_provider.current()` | Current wallet bankroll of record | `src/runtime/bankroll_provider.py` | canonical wallet provider or usable cache | USD wallet equity | fetch/cache time | fallback bankroll input when RiskGuard/cycle lacks initial bankroll | `state/status_summary.json` | operators/reports | explicit fallback |
| pre-repair `status.portfolio.effective_bankroll` fallback | Wallet + analytics PnL synthetic object | `src/observability/status_summary.py` | no upstream economic authority for this transform | USD mixed wallet/PnL | status generation time | analytics PnL folded into bankroll | `state/status_summary.json` | operators/reports | broken before Wave12 repair |
| `scripts/equity_curve.py::total_pnl` | Reported performance PnL | `status_summary.json.portfolio.total_pnl` | derived status/report evidence | USD PnL | report generation time | copied from explicit PnL field | report JSON/PNG | operator reports | repaired |

UNKNOWN: whether every external operator dashboard treats `status_summary.json.portfolio.effective_bankroll` as purely visual or as a manual decision aid. This wave treats it as live-money relevant because the repo exposes it as operator status.

## Findings

### W12-F1 — S1 Active

Object meaning changed: wallet bankroll-of-record became wallet plus analytics PnL.

Boundary: `risk_state.details_json`/`bankroll_provider` wallet equity -> `status_summary.portfolio.effective_bankroll`.

Pre-repair code path: `src/observability/status_summary.py` fallback folded analytics PnL into `effective_bankroll` when RiskGuard details omitted wallet equity.

Economic impact: realized PnL is already in wallet balance and unrealized PnL is diagnostic-only. Adding either to wallet-equity can double count or fabricate bankroll, changing operator heat/equity interpretation and downstream report equity curves.

Reachability: active derived/operator-report path. Not a live order authorizer by itself, but it can corrupt reporting and manual monitoring context.

Repair invariant: `effective_bankroll`/`bankroll` in status output preserves wallet-equity identity. PnL stays analytics only. If no wallet truth exists, status degrades rather than synthesizing a bankroll object.

### W12-F2 — S1 Active

Object meaning changed: unproven historical `risk_state.details_json.effective_bankroll` could still be treated as wallet-equity if the latest RiskGuard row carried the retired fixed-capital + PnL value without wallet provenance.

Boundary: historical/legacy RiskGuard row -> derived status bankroll.

Code path: `src/observability/status_summary.py` initially accepted any non-null `risk_details.effective_bankroll`.

Economic impact: a pre-cutover row can reintroduce the exact wallet+PnL synthetic object Wave12 is removing.

Repair invariant: status accepts RiskGuard bankroll only when `bankroll_truth_source=polymarket_wallet`, nested `bankroll_truth.source=polymarket_wallet`, and `bankroll_truth.authority=canonical`; otherwise it rejects that source and falls back to explicit wallet truth or degrades.

### W12-F3 — S2 Active Diagnostic/Report

Object meaning changed: `scripts/equity_curve.py` derived report PnL as `effective_bankroll - initial_bankroll`, which reinterpreted wallet-equity snapshots as performance PnL after Wave12 clarified `effective_bankroll` as wallet bankroll.

Boundary: `status_summary` derived status -> diagnostic equity report.

Economic impact: report output and graphs could misstate performance when wallet-equity and report PnL have different time/evidence semantics.

Repair invariant: `equity_curve.py` now takes `total_pnl` from `status_summary.portfolio.total_pnl`, keeps `bankroll` as wallet-equity, and reports `performance_equity = initial_bankroll + total_pnl` explicitly.

### W12-F4 — S3 Active Topology Compatibility

Object-invariance status/equity boundaries were not routable under existing topology because `status_summary.py` profiles were historical-slice-specific. The route repair added a narrow profile so future work can enter this boundary without pretending it is v2 row counts or execution capability.

## Repair Plan

1. Add relationship test proving status fallback keeps `effective_bankroll == initial_bankroll` and does not add realized/unrealized PnL.
2. Update `status_summary` bankroll fallback:
   - Preserve upstream `risk_details.effective_bankroll` only when canonical wallet provenance is present.
   - Otherwise derive `effective_bankroll` from explicit wallet-equity sources only: `risk_details.initial_bankroll`, `cycle_summary.wallet_balance_usd`, or `bankroll_provider.current()`.
   - Leave missing bankroll as degraded/unknown instead of `0 + total_pnl`.
   - Annotate portfolio/truth fields with source, status, object identity, and derivation.
   - Reject unproven RiskGuard bankroll payloads and record `bankroll_rejected_source`.
   - Update `equity_curve.py` so diagnostic PnL comes from explicit PnL fields rather than wallet snapshot subtraction.
3. Run focused route, unit, py_compile, digest export, schema, planning-lock, and static contamination sweeps.
4. Run a critic on the full Wave12 diff with explicit prompts to inspect RiskGuard/status/report/equity consumers and route compatibility.

## Verification Plan

- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_operator_status_bankroll_semantics_routes_to_wave12_profile`
- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py -k 'bankroll_semantics'`
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py -k 'equity_curve'`
- Optional legacy check with local stubs: `tests/test_pnl_flow_and_audit.py::test_inv_status_fallback_bankroll_uses_initial_bankroll`
- `python3 -m py_compile src/observability/status_summary.py scripts/equity_curve.py tests/test_phase10b_dt_seam_cleanup.py tests/test_backtest_skill_economics.py tests/test_pnl_flow_and_audit.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
- `python3 scripts/digest_profiles_export.py --check`
- `python3 scripts/topology_doctor.py --schema`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave12/PLAN.md`
- Static sweep for `effective_bankroll`, `initial_bankroll`, `total_pnl`, and status-summary consumers.

## Verification Results

- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py` passed: 24 passed.
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py` passed: 19 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_operator_status_bankroll_semantics_routes_to_wave12_profile` passed.
- Optional legacy check with local `sklearn` and `apscheduler` stubs passed: `tests/test_pnl_flow_and_audit.py::test_inv_status_fallback_bankroll_uses_initial_bankroll`.
- `python3 -m py_compile src/observability/status_summary.py scripts/equity_curve.py tests/test_phase10b_dt_seam_cleanup.py tests/test_backtest_skill_economics.py tests/test_pnl_flow_and_audit.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` passed.
- `python3 scripts/digest_profiles_export.py --check` passed.
- `python3 scripts/topology_doctor.py --schema` passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave12/PLAN.md` passed.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...` passed.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files scripts/equity_curve.py tests/test_phase10b_dt_seam_cleanup.py tests/test_backtest_skill_economics.py tests/test_pnl_flow_and_audit.py tests/test_digest_profile_matching.py` passed.
- `git diff --check` passed.
- Static sweep for executable wallet+PnL recomposition found only historical comments/tests in `src/riskguard/riskguard.py` and `tests/test_riskguard_onchain_bankroll.py`.
- `python3 scripts/topology_doctor.py --scripts --json` remains globally red due pre-existing script manifest/naming issues unrelated to `scripts/equity_curve.py`; Wave12 used focused route evidence plus closeout map-maintenance instead.
- `python3 scripts/topology_doctor.py --tests --json` remains globally red due pre-existing test topology registry debt unrelated to Wave12 touched tests.

## Critic Verdict

- First critic pass: REVISE. It found W12-F2 (unproven historical RiskGuard bankroll row could still materialize as status bankroll) and W12-F3 (`equity_curve.py` derived report PnL from wallet bankroll deltas).
- Repair after critic: status now rejects unproven RiskGuard bankroll payloads and records `bankroll_rejected_source`; equity curve now consumes explicit `status_summary.portfolio.total_pnl` and separates wallet bankroll from `performance_equity`.
- Second critic pass: APPROVE. No S0/S1/S2 findings remained; the critic confirmed the Wave12 profile blocks RiskGuard, risk allocator, state DB, schema, and live surfaces.

## Compatibility Notes

- Topology friction was real, not noise: high-fanout status read-model repairs need semantic route phrases tied to object meaning, not only legacy feature names.
- The new profile is intentionally narrow and forbids the producer/protective zones that would make this a cross-zone policy rewrite.
- `docs/operations/AGENTS.md` contained stale active rows for archived/missing packet paths. Touching the registry for a new packet made those old rows hard blockers; this is a route/system compatibility issue rather than Wave12 domain logic.
- Critic REVISE found both the unproven-risk-row path and equity-curve report drift; both are now treated as Wave12 scope because they are direct downstream materializations of the same bankroll object boundary.
