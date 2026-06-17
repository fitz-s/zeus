# Zero-Legacy Live-Path Audit — 2026-06-16

Operator directive: **zero legacy on the live decision path; correct, up-to-date data + math.**
Method: 4 parallel hunters (legacy data-source / legacy math / stale data / version-suffix+dead-flags)
→ 29 candidate findings → adversarial live-reachability verification → hand-confirmation of every
"violation" (the workflow's synthesis step rate-limited before persisting; this report reconstructs the
result from the surviving verify verdicts + direct re-verification). **Bar: a finding counts only if it
reaches a LIVE entry / exit / sizing / submit decision with current flags** (`qkernel_spine_enabled=true`,
`live_execution_mode=edli_live`). Dead/diagnostics-only/contract reads are not violations.

## VERDICT
After the fixes below, the live decision path is **substantially zero-legacy on correct, up-to-date data**.
The audit surfaced 29 candidates; verification + hand-confirmation reduced them to **2 genuine live
violations (both fixed)** + **1 stale-data artifact (refreshed)**, with **3 workflow false-positives caught
by hand** and the remainder **proven clean**.

## 1. GENUINE LIVE VIOLATIONS — FIXED (commit `97b3b8a60a`)

| Sev | Violation | Live impact | Fix |
|---|---|---|---|
| **HIGH** | `bias_decay_kelly_haircut` (legacy `edli_per_city_v1` **ENSEMBLE-bias** system) halved the spine's Kelly stake — its q_source skip-guard (`event_reactor_adapter.py:12228`) exempted `emos`/`raw_honest` but **not** `qkernel_spine`. | The corrected spine could pick the right leg and still be **sized at 50%** — a legacy ensemble-bias system overriding the corrected size. | Added `qkernel_spine` to the exemption. The legacy haircut now touches **zero** live decisions (`emos`/`raw_honest` already skipped). |
| **MED** | Exit/monitor belief could substitute the cold `ensemble_snapshots` EMOS center for **non-edli** positions (the ensemble-suppressor guard was edli-only). | The 2 legacy open positions' hold/exit belief re-derived off the cold ensemble. | Widened the suppressor (`monitor_refresh.py`) to **all non-day0 positions** → they price exit off the multi-model `forecast_posteriors` (coverage proven fresh). |

## 2. STALE DATA — REFRESHED (the "up-to-date data" half)

| Artifact | Was | Now | Note |
|---|---|---|---|
| `state/settlement_sigma_floor.json` (feeds live spine entry σ-floor) | asof **06-10**, 185 residual pairs | asof **06-16**, **4329 pairs** | The old fit was badly under-powered (185). k=1.3 PIT-widen **preserved**; floor widened 1.19→1.45°C, which *helps* the known under-dispersion. |

## 3. WORKFLOW FALSE-POSITIVES — caught by hand verification (NO fix needed)

The workflow's adversarial verify agents produced 3 false-positive "violations" by reading docstrings /
call-sites without tracing into the consuming code. Hand-verified as non-issues:

- **`per_bin_yes_q_lcb` (legacy q_lcb) at the spine sizing seam** — flagged HIGH. **Vestigial param**: passed
  to `decide_family_via_spine` but **never consumed** (the spine builds its own `joint_q`; `engine.decide()`
  never receives it). Verified: only occurrences in the bridge are the param + a misleading docstring.
- **Exit lane on cold ensemble** — flagged HIGH. The edli book's exit primary is `forecast_posteriors`
  (multi-model); the ensemble reader is a **suppressed fallback** (early-return before the ensemble registry).
  6/8 open positions are edli. (Only the 2 legacy ones needed the §1 guard widening.)
- **`sigma_scale_fit.json` numerically active** — flagged MED. Direct check: 2 families (C, F), **both
  k=1.0 / w=0.0 = exact identity** = numerically inert; zero effect on live σ despite the 06-13 timestamp.

## 4. PROVEN CLEAN — verified NOT live-legacy (so they were checked, not missed)

- `ensemble_snapshots` cannot seed a spine **ENTRY** decision under `qkernel_spine_enabled=true` (entry fixed to `raw_model_forecasts`, commit `9ee1936148`).
- Market-anchor / one-sided q_lcb cap (`event_reactor_adapter.py:7943-7974`) is **gated off** behind the replacement-authority flag — not applied to any live q_lcb.
- Binary per-candidate Kelly runs on the **current** strategy-of-record economics, not legacy q.
- The legacy ENS/Platt/`baseline_q_lcb_reference` baseline is **diagnostics-only provenance**, never joined onto the live q/edge/size.
- `executable_cost._levels_for_direction` is **leaf-only** (confirmed by codegraph).
- `shoulder_strategy_vnext` classifier is not on the live decision path.
- Stale EMOS center artifacts contribute **zero** to the spine entry center (`build_center` runs on the raw multi-model envelope).

## 5. MINOR / RESIDUAL (low, not fixed this pass)
- **LOW** — day0 lane can use an ENS-EMOS center, but **only on a realized-observation refresh** (not the cold forecast center) — the day0 lane is excluded from the §1 guard by design (it has its own multi-model day0 source).
- **LOW** — `_build_monitor_one_calibrator_q` (monitor EMOS q) reachable on the monitor lane; bounded, low impact.

## 6. STRUCTURAL ANTIBODY (prevents recurrence)
`tests/architecture/test_forecast_source_parity.py` (NEW) asserts: entry spine reads `raw_model_forecasts`,
exit primary reads `forecast_posteriors`, the replay harness reads `raw_model_forecasts`, and **no live-path
docstring claims unenforced "ARM-validated / identical-to-replay" parity**. This closes the source-divergence
+ false-provenance classes structurally so they cannot silently recur.

---
**Cost note:** the audit was run as a large concurrent multi-agent workflow that hit provider rate limits and
burned quota. The findings here are reconstructed from the surviving verdicts + direct hand-verification; the
remediation was done sequentially by hand (no agent fan-out). 3 of the workflow's "violations" were
false-positives the concurrent verify agents got wrong — direct file:line tracing was required to confirm.
