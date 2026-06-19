# mx2t3 / ensemble_snapshots → forecast_posteriors + raw_model_forecasts: minimal LIVE decouple

- Created: 2026-06-17
- Last audited: 2026-06-17
- Authority basis: operator single-truth law; carrier_decouple_plan GATE-1/GATE-2; reference
  branch `claude/agent-a4d0b2fb54b604bd2` (ported functions, tested there).
- Base: live HEAD `c62d53b190` (live trading branch tip). Designed to apply ON TOP of the
  operator's ~40-file uncommitted WIP in the main tree.

## Scope / hard constraint

This is the **t3-decouple ONLY**. It does NOT bring the GATE-0 bias-maze strip (the reference
branch deletes `emos_ci_*`, `settlement_residual_debias`, the `edli_emos_sole_calibrator_enabled`
flag, the conftest pin, etc.). On live HEAD GATE-0 is NOT applied, so the bias functions
(`_maybe_override_lcb_with_emos_ci`, the EMOS-shadow ledgers, the grid-repr correction) STILL
EXIST and are LEFT UNTOUCHED.

Files touched (4 + 1 new test):

| File | Touched | Operator WIP touches it? |
|---|---|---|
| `src/engine/event_reactor_adapter.py` | named functions + 3 new helpers only | YES — but DIFFERENT functions (no overlap; see below) |
| `src/events/triggers/forecast_snapshot_ready.py` | posterior-lane FSR fork | NO (clean apply) |
| `src/decision_kernel/verifier.py` | posterior allow-list widen | NO (clean apply) |
| `src/decision_kernel/compiler.py` | posterior allow-list widen (symmetric half of verifier) | NO (clean apply) |
| `tests/engine/test_mx2t3_carrier_decouple.py` | NEW file (5 antibodies) | NO |

### Applies-clean-on-WIP proof

Operator WIP modifies `event_reactor_adapter.py` at these functions ONLY (hunk contexts):
`_compute_selection_shrinkage` (~2079-2178), `_build_event_bound_no_submit_receipt_core`
(~2914-2987), `_replacement_no_lcb_for_bin` (~10454), `_replacement_authority_probability_and_fdr_proof`
(~10580-10778). A grep of the operator diff for my 5 target function names returns **nothing** —
my functions and the operator's are disjoint, and my nearest function
(`_canonical_probability_and_fdr_proof`, live :10863) is ~85 lines after the operator's last edit.
`verifier.py`, `compiler.py`, `forecast_snapshot_ready.py` are NOT in the operator WIP at all.

## Per-coupling change

### 1. DAY0 q seed (BELIEF) — `_market_analysis_from_event_snapshot` / `_canonical_probability_and_fdr_proof`

The day0 lane (`allow_latest_snapshot=True`) read `_snapshot_members(snapshot)` = cold mx2t3
ECMWF-ENS members as the forecast-base seed. Re-sourced off the multi-model `raw_model_forecasts`
fusion:
- PORTED `_day0_seed_members_multimodel` + `_latest_raw_model_cycle_for_family` from the reference
  branch (latest family cycle ≤ decision_time, latest-cycle-per-model, °C→native, ≥3-member floor).
- `_canonical_probability_and_fdr_proof` computes `_day0_seed_members` ONLY for the day0 lane and
  threads it into `_market_analysis_from_event_snapshot(day0_seed_members=...)`.
- `_market_analysis_from_event_snapshot` consumes the seed when `size >= 3`, else **fails closed to
  the legacy ensemble seed** (`_snapshot_members(snapshot)`) — never widens/fabricates. Forecast
  lane (`allow_latest=False`) unaffected.

### 2. σ-fallback (BELIEF) — `_trailing_residual_std_native`

Authored fresh (the reference branch left this function byte-identical — it did NOT re-source it).
The per-city residual-std σ previously joined `settlement_outcomes ⋈ ensemble_snapshots.members_json`
(ensemble member-mean). Re-sourced: the residual forecast term is now the multi-model member-mean
from `raw_model_forecasts` at the DECISION lead (`lead_days = 1`, native schema column),
latest-cycle-per-model (`ROW_NUMBER() … ORDER BY source_cycle_time DESC`, keep rank 1) → `AVG`
per settled `(city, target_date)`, °C→native converted, differenced against the native
`settlement_value`. Trailing-window (`_REPRESENTATIVENESS_FALLBACK_WINDOW_DAYS`), min-n
(`_REPRESENTATIVENESS_FALLBACK_MIN_N`), and native-unit semantics are UNCHANGED; only the data
source swapped. Functionally verified (C-unit, F-unit conversion, stale-cycle exclusion,
lead_days=2 exclusion, min-n→None) — see below.

### 3. Carrier cert + cycle-pin — `_forecast_authority_payload_and_clock` / `_spine_multimodel_members_for_event`

- PORTED `_forecast_authority_payload_from_posterior` + `_posterior_horizon_profile` + the posterior
  constants. `_forecast_authority_payload_and_clock` now forks on the existing
  `_replacement_authority_enabled()` flag: for FORECAST decision lanes (NOT day0) it builds the
  no-submit cert's FORECAST_AUTHORITY from `forecast_posteriors` + `raw_model_forecasts`
  (`members_json_source="raw_model_forecasts.multimodel"`); the ensemble path is KEPT forked for
  the DAY0/legacy/flag-OFF case and returns `None`-fallthrough on any miss.
- `_spine_multimodel_members_for_event`: added the neutral `rmf-<city>|<target>|<metric>|<cycle_date>`
  causal_snapshot_id cycle-DATE parse (+ B2 `_latest_raw_model_cycle_for_family` fallback). **No
  model-SELECTION filtering added** — only the cycle-id parse, per the task constraint.
- `forecast_snapshot_ready.py`: PORTED the posterior-lane FSR readiness/selection fork (mints the
  neutral `rmf-...` snapshot id; legacy ensemble path kept under `else:`).
- `verifier.py` + `compiler.py`: PORTED the posterior allow-list widen — a posterior-provenance
  cert is validated by the EQUALLY-STRICT posterior invariant set (decorrelated-model-count
  completeness ≥3, causality/authority/freshness), the ensemble branch UNCHANGED.

## Remaining `ensemble_snapshots` references (classification)

Live-decision BELIEF reads of `ensemble_snapshots.members_json` remaining — all are the explicit
fail-closed day0/legacy fallback or provenance-only:

| Location | Classification |
|---|---|
| `_market_analysis_from_event_snapshot` :`raw_members = _snapshot_members(snapshot)` (else branch) | **fail-closed-fallback** — only when day0 multimodel seed < 3 / absent |
| `_forecast_authority_payload_and_clock` ensemble path (`members_json_source="ensemble_snapshots.daily_extrema"`) | **fail-closed-fallback** — DAY0/legacy/flag-OFF; posterior fork returns first when ON |
| `_bound_forecast_snapshot_row_for_spine` / `_forecast_snapshot_row_for_event` (`_authority_table_ref("ensemble_snapshots")`) | **decoupled (cycle-pin only)** — spine now parses cycle from the neutral id first; this is the B1 legacy pin, not a belief read |
| `_snapshot_members` calls at adapter :13112, :13394 | **provenance-only / shadow** — EMOS-shadow-ledger writers (mirror the EMOS fit source); not q/sizing/submit belief |
| FSR :690 / :739 / `_FORECAST_TABLES` | **fail-closed-fallback** — legacy `else:` branch only (flag-OFF / no posterior table) |
| verifier `ENSEMBLE_MEMBERS_JSON_SOURCE` :991 | **decoupled (provenance allow-list)** — one of two accepted sources; posterior source added |
| `_trailing_residual_std_native` | **decoupled** — re-sourced to raw_model_forecasts (no ensemble read) |

No live-decision-path BELIEF read of `ensemble_snapshots.members_json` remains except the explicit
fail-closed day0/legacy fallback.

## Verification (verbatim tails)

Hard gate — `tests/money_path/ tests/strategy/live_inference/ tests/architecture/`:
```
........................................................................ [ 95%]
..................                                                       [100%]
378 passed in 6.05s
```

Carrier-decouple antibodies — `tests/engine/test_mx2t3_carrier_decouple.py`:
```
.....                                                                    [100%]
5 passed in 1.06s
```

Imports:
```
python -c "import src.main; import src.engine.event_reactor_adapter; import src.engine.qkernel_spine_bridge; import src.decision_kernel.verifier"
IMPORTS-OK
```

σ-fallback functional verification (authored, not covered by ported tests):
```
C MATCH: True ret= 0.25       # multimodel member-mean − settlement, stale+lead2 rows excluded
F MATCH: True ret= 0.45       # °C→°F conversion applied before differencing native settlement
min-n guard None: True        # only 2 settled days → None → caller falls back to 0.0
```

Regression — NO new failures. `tests/engine/ tests/decision/ tests/events/`:
- clean HEAD `c62d53b190` baseline: **37 failed, 1201 passed, 9 skipped, 4 xfailed**
- patched tree: **37 failed, 1206 passed, 9 skipped, 4 xfailed**
- Identical 37 pre-existing failures (operator-WIP-era surfaces:
  `_replacement_authority_probability_and_fdr_proof` FUSED_NORMAL bounds, day0-shadow dual-persist,
  crossing-decision, always-decidable — none touched by this patch). Patch adds the 5 new passing
  antibodies (1201 + 5 = 1206). **Zero new failures introduced.**

## `git diff c62d53b190 --stat`

```
 src/decision_kernel/compiler.py                |  55 +++
 src/decision_kernel/verifier.py                |  98 +++++-
 src/engine/event_reactor_adapter.py            | 448 +++++++++++++++++++++++--
 src/events/triggers/forecast_snapshot_ready.py | 344 +++++++++++++------
 4 files changed, 814 insertions(+), 131 deletions(-)
```
(plus new untracked `tests/engine/test_mx2t3_carrier_decouple.py`, 176 lines)
