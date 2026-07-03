# mx2t3 / ensemble_snapshots consumer classification

```
# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: read-only reachability trace under config/settings.json flags @ branch live/iteration-2026-06-13
#   (event_reactor_adapter.py HEAD 97b3b8a60a, 2026-06-16). Operator goal: strip every
#   mx2t3 / ensemble_snapshots consumer NOT on a live decision path.
```

## Scope and method

`ensemble_snapshots` (state/zeus-forecasts.db) holds the cold `mx2t3` ECMWF-ENS members
(`members_json`, 51 members, ~1-2°C cold vs settlement). Classified EVERY `src/` reader of
`ensemble_snapshots` rows and of `members_json` by **runtime reachability under current flags**,
not by docstring. Source grep: `grep -rn "ensemble_snapshots|members_json" src --include="*.py"`.

The `mx2t3` token grep is mostly schema / contract / ingest / provenance plumbing (writer-side,
identity strings, unit guards). Those are NOT decision consumers and are not in this table except
where a writer also reads members. The decision-relevant axis is **who READS `members_json` to
build a q / lcb / sizing / readiness verdict.**

## Verified live-flag state (config/settings.json)

| Flag | Value | Effect |
|---|---|---|
| `feature_flags.qkernel_spine_enabled` | **True** | spine is the live SELECTION authority (forecast lane) |
| `feature_flags.openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled` | **True** | replacement authority (raw_model_forecasts) is the live q on the forecast lane |
| `edli.edli_emos_sole_calibrator_enabled` | **True** | bias/grid/Platt maze bypassed (non-day0) |
| `edli.edli_bias_correction_enabled` | **False** | bias correction INERT |
| `baseline_bias_correction_enabled` | **False** | INERT |
| `edli.edli_emos_ci_live_enabled` | absent → **False** (code default) | EMOS-CI live override INERT |
| `edli.edli_emos_shadow_ledger_enabled` | absent → **False** (code default) | shadow ledger INERT |
| `edli.edli_live_scope` | **forecast_plus_day0** | **day0 lane is LIVE** (operator 2026-06-12 "现在就解除") |
| `edli.day0_remaining_day_q_enabled` | **True** | day0 q uses remaining-day member pool |
| `edli.real_order_submit_enabled` | **True** | live submit on |

---

## CRITICAL RULING — is `_market_analysis_from_event_snapshot` (cold-mx2t3 MC analysis) on a live decision path?

**FORECAST lane (`FORECAST_SNAPSHOT_READY`, `EDLI_REDECISION_PENDING`): NO — fully bypassed.**
**DAY0 lane (`DAY0_EXTREME_UPDATED`): YES — it IS the live day0 q builder, and day0 is live.**

Trace (`event_reactor_adapter.py`):

1. `_run_family_decision` (seam ~2438) calls `_generate_candidate_proofs` on **both** paths first;
   the spine flag (~2509) only switches the SELECTION authority (`decide_family_via_spine` vs
   `_selected_candidate_proof`), NOT the q producer. The q is built inside
   `_generate_candidate_proofs` → `_live_yes_probabilities` (~7658) on every path.
2. `_live_yes_probabilities` (~9804) for a **forecast-decision** event calls
   `_replacement_authority_probability_and_fdr_proof` FIRST (~9808). It returns the canonical
   fallback `_canonical_probability_and_fdr_proof` **only if the replacement returns `None`** (~9819-9821).
3. `_replacement_authority_probability_and_fdr_proof` (10457-10726) returns `None` at exactly ONE
   site — line ~10476, `if not _replacement_authority_enabled(): return None`. Every other exit is a
   `raise ValueError(...)` (→ no-submit receipt) or the live bundle `return` (~10696).
   `_replacement_authority_enabled()` reads
   `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled` = **True**.
   ∴ on the forecast lane the function **never returns `None`** → the canonical fallback (and thus
   `_market_analysis_from_event_snapshot` at ~10891) is **UNREACHABLE**.
4. The spine member envelope is sourced from **`raw_model_forecasts`** via
   `_spine_multimodel_members_for_event` (seam ~7770-7789, root-cause fix 2026-06-16) and
   UNCONDITIONALLY OVERWRITES the decision-consumed `_edli_spine_*` keys, so "an ensemble-derived
   value can never reach a spine DECISION." `qkernel_spine_bridge._served_predictive_inputs`
   (~470) reads only those `raw_model_forecasts`-sourced keys.
5. **Day0 exception:** `_live_yes_probabilities` for `DAY0_EXTREME_UPDATED` (~9844) calls
   `_canonical_probability_and_fdr_proof(... allow_latest_snapshot=True)` **directly** (no
   replacement gate). The spine hard-blocks day0 (`_is_day0_event` → legacy path, ~2507), so day0
   stays on this canonical lane. `_forecast_snapshot_row_for_event` (~11366) reads the `snapshot`
   from `ensemble_snapshots` for both causal and `allow_latest` (day0) modes. Inside
   `_market_analysis_from_event_snapshot` the `_emos_regime` is gated
   `family.event_type != "DAY0_EXTREME_UPDATED"` (~11636) → EMOS is SKIPPED for day0; the cold
   `raw_members = _snapshot_members(snapshot)` (~11596) is read as the seed pool for
   `_day0_remaining_day_members` (~11770). day0 is LIVE (`forecast_plus_day0`).

**Strip consequence:** `_market_analysis_from_event_snapshot` + its cold-mx2t3 chain
(`_snapshot_members`, `_snapshot_p_raw`, `_snapshot_p_cal`, the bias/grid/Platt maze) **cannot be
removed** while day0 trades through it. It is dead ONLY for the forecast lane. A clean strip
requires first re-sourcing the day0 q off `ensemble_snapshots.members_json` (e.g. onto
`raw_model_forecasts` like the spine), OR scoping day0 out. Until then: **KEEP, day0-only.**

---

## Consumer table

| Consumer (file:line) | Classification | Evidence (reachability under current flags) | Strip / KEEP |
|---|---|---|---|
| `event_reactor_adapter.py:11583 _market_analysis_from_event_snapshot` | **LIVE_USED (day0 only)** | Unreachable on forecast lane (replacement authority never returns None, §ruling 1-4). Reached LIVE on the day0 lane via `_live_yes_probabilities`→`_canonical_…`(~9844)→here; day0 is live (`forecast_plus_day0`). | KEEP (live day0 q builder). Becomes DEAD only after day0 q is re-sourced off ensemble_snapshots. |
| `event_reactor_adapter.py:11596 _snapshot_members(snapshot)` (the cold members read) | **LIVE_USED (day0 only)** | Called inside `_market_analysis_from_event_snapshot`; same reachability. Forecast lane never calls it; day0 uses it as the seed pool for `_day0_remaining_day_members`. | KEEP (day0). |
| `event_reactor_adapter.py:12191 _snapshot_members` (def) | **LIVE_USED (day0) + SHADOW/INERT callers** | Live caller = day0 `_market_analysis_…`. Other callers: `_snapshot_members_json_hash` (12199, receipt provenance hash only), `_write_emos_shadow_ledger` (12728, INERT), `_maybe_override_lcb_with_emos_ci` (13010, INERT). | KEEP (def is live via day0). |
| `event_reactor_adapter.py:12939 _maybe_override_lcb_with_emos_ci` | **INERT** | Gated `edli_emos_ci_live_enabled` (12978) = False (absent from settings). Also only called from inside `_market_analysis_…`(11054) — i.e. day0-only-reachable even if flipped. No-op today. | STRIP candidate: the def + call site 11046-11061 + `src/calibration/emos_ci_license.py` + tests. Flag-OFF byte-identical. Confirm operator never intends to arm EMOS-CI before deleting. |
| `event_reactor_adapter.py:12683 _write_emos_shadow_ledger` | **SHADOW (INERT)** | Observability ledger. Call site 11131-11135 gated `edli_emos_shadow_ledger_enabled` = False (absent). Reads `_snapshot_members` (12728) for EMOS forward-q telemetry only; never feeds q/lcb/sizing/submit. | STRIP candidate: def + call site 11130-11141 + `emos_ledger`/`emos_ci_shadow` writers. Pure shadow, no live reach. |
| `event_reactor_adapter.py:11185 _spine_multimodel_members_for_event` | **LIVE_USED (raw_model_forecasts, NOT mx2t3)** | The live spine producer. Sources members from `raw_model_forecasts`, NOT `ensemble_snapshots.members_json`. NOT a cold-mx2t3 consumer. | KEEP (this is the replacement for the cold path). |
| `event_reactor_adapter.py:11294 _bound_forecast_snapshot_row_for_spine` | **CARRIER (causal-cycle pin, members NOT read)** | Reads an `ensemble_snapshots` row to pin the event's causal CYCLE (source_cycle_time DATE) for the multi-model query. Docstring + code: `members_json` is NOT read for belief (cold-center root cause explicitly excluded). | KEEP (cycle pin only; ensemble_snapshots row, but members untouched). |
| `event_reactor_adapter.py:11366 _forecast_snapshot_row_for_event` | **LIVE_USED (day0) / DEAD (forecast)** | Reads the `ensemble_snapshots` row. Forecast-lane caller `_canonical_…` is unreachable; day0 caller (`allow_latest=True`) is live. | KEEP (day0 snapshot binding). Forecast-mode branch dies with `_canonical_…`. |
| `event_reactor_adapter.py:10863 _canonical_probability_and_fdr_proof` | **LIVE_USED (day0) / DEAD (forecast)** | Forecast-lane invocation (9821) unreachable (§ruling 3). Day0 invocation (9844) live. | KEEP (day0). Forecast path is dead but shares the function. |
| `event_reactor_adapter.py:6538 / 6574 _snapshot_members_json_hash` | **SHADOW (provenance hash)** | Receipt/provenance field `members_json_hash` + `members_json_source: "ensemble_snapshots.daily_extrema"`. Hash of members for audit; does not drive a decision. Reached wherever the snapshot is built (incl. day0). | KEEP-low (cheap provenance). Strip only if the whole ensemble_snapshots snapshot dict is removed. |
| `events/triggers/forecast_snapshot_ready.py:284/366/588/918 (members_json)` | **CARRIER (readiness/completeness)** | Reads `members_json` ONLY for `len(...)` → `observed_members`/`member_count` completeness gating, and threads `members_json` into the emitted FORECAST_SNAPSHOT_READY snapshot dict (which the forecast lane no longer consumes for q). The members count gates readiness, not q. | KEEP (family-readiness CARRIER, per operator "handled separately"). The threaded `members_json` payload field becomes vestigial once `_canonical_…` forecast path is removed, but day0 still reads it. |
| `events/triggers/forecast_snapshot_ready.py:723-760 executable_forecast_live_eligible_reader` | **CARRIER (live-eligibility boolean)** | Delegates to `read_executable_forecast_snapshot`; uses only `result.ok` (eligibility verdict). Members parsed but discarded for the gate. | KEEP (readiness CARRIER). |
| `data/executable_forecast_reader.py:296/855 _members / read_executable_forecast_snapshot` | **CARRIER (eligibility)** | Parses `members_json` into an `ExecutableForecastSnapshot`; the live consumer (`forecast_snapshot_ready` reader-block) reads only `.ok`. Not a live q source. | KEEP (CARRIER eligibility). |
| `data/tigge_db_fetcher.py:115/173 fetch_from_db (members_json)` | **DEAD (legacy TIGGE bundle path)** | Reads `ensemble_snapshots.members_json` (forecasts.db) to assemble a 51×hourly grid for the legacy `EnsembleSignal`/`ForecastBundle` path via `tigge_client._fetch_db_payload`. The live forecast q is the replacement authority (raw_model_forecasts); this legacy member-vote bundle is not on the live spine/replacement decision path. (Also reused by `ecmwf_open_data_ingest._fetch_db_payload` — ingest assembly, not a decision.) | QUARANTINED → verify no live caller of `tigge_client` forecast bundle remains; if confirmed dead, STRIP the fetch_from_db decision use. Ingest reuse must be repointed first. |
| `calibration/ens_bias_repo.py:408 load_bucket_residuals (members_json)` | **DEAD-for-live / training-time** | Reads `members_json` JOIN settlement_outcomes for offline bias-model FITTING. Callers: `ens_error_model.py` only, itself called ONLY by `scripts/*` (onboard_cities, fit_full_transport_error_models, run_platt_oos_scoring, replay_equivalence…). No live daemon caller. | KEEP (offline calibration fit input — not a live consumer, but a legitimate training surface). Not in strip scope. |
| `calibration/ens_bias_repo.py:827 _forecast_means (members_json)` | **DEAD-for-live / training-time** | Same: offline forecast-mean computation for bias buckets, script-only reachability. | KEEP (offline). |
| `calibration/ens_bias_repo.py:614 read_bias_model` | **INERT (reads model rows, not members_json)** | Live callers `evaluator.py` + `monitor_refresh.py`, but the bias-correction they feed is INERT (`edli_bias_correction_enabled=False` + `edli_emos_sole_calibrator_enabled=true`). Does NOT read `members_json`. | KEEP (not a members consumer; bias path inert but out of mx2t3-strip scope). |
| `engine/evaluator.py:3957 / monitor_refresh.py:857 EnsembleSignal(...)` | **NOT an ensemble_snapshots consumer** | Build `EnsembleSignal` from `ens_result["members_hourly"]` — a FRESH source-client fetch, NOT `ensemble_snapshots.members_json`. Live (cycle FT entry / exit monitor) but reads the live hourly members, not the cold stored snapshot. | KEEP / OUT OF SCOPE (does not touch the cold ensemble_snapshots table). |
| `contracts/forecast_object.py:100 / calibration_bins.py:331-452 / ensemble_snapshot_provenance.py / snapshot_ingest_contract.py` | **CARRIER (contract/guard, writer-side)** | Unit-identity guards, provenance allow-lists, ForecastObject builder. Validate writes/reads of `ensemble_snapshots.members_json`; not decision producers. | KEEP (integrity guards). |
| `analysis/market_analysis_vnext.py:68/111 (bin_grid_id)` | **NOT a members consumer** | References only `ensemble_snapshots.bin_grid_id` (a column), used by `cycle_runtime` for spread/display. Does not read `members_json`. | KEEP / OUT OF SCOPE. |
| `state/day0_nowcast_store.py:162 (bin_grid_id comment)` | **NOT a members consumer** | bin_grid_id propagation note. | OUT OF SCOPE. |
| `ingest_main.py`, `data/ecmwf_open_data*.py`, `state/db.py`, `state/schema/v2_schema.py`, `observability/*`, `backtest/shadow_replay_harness.py` | **WRITER / SCHEMA / OFFLINE** | Ingest writers, DDL, status summaries, replay harness. Producer/plumbing side; not live decision consumers. | KEEP (write/schema). Out of strip scope. |

---

## Summary verdicts

- **LIVE_USED on the cold ensemble_snapshots/mx2t3 members:** exactly the **day0 lane** —
  `_market_analysis_from_event_snapshot` → `_snapshot_members` → `_day0_remaining_day_members`,
  bound via `_forecast_snapshot_row_for_event(allow_latest=True)`. The forecast lane does NOT use
  it (replacement authority + spine on raw_model_forecasts).
- **The cold-mx2t3 MC analysis is NOT on the forecast decision path** (spine, non-spine/legacy
  selection, or q). It is bypassed whenever the event is `FORECAST_SNAPSHOT_READY` /
  `EDLI_REDECISION_PENDING`, independent of `qkernel_spine_enabled` — the bypass is owned by the
  replacement-authority flag, not the spine flag. It is ONLY live for `DAY0_EXTREME_UPDATED`.
- **Directly strippable now (no live reach, flag-OFF byte-identical):**
  `_maybe_override_lcb_with_emos_ci` (INERT) and `_write_emos_shadow_ledger` (SHADOW) + their call
  sites + `emos_ci_license.py` / `emos_ledger` / `emos_ci_shadow` writers + tests.
- **Strippable after a repoint:** the forecast-lane half of `_market_analysis_from_event_snapshot`
  / `_canonical_probability_and_fdr_proof` (dead for forecast, alive for day0) — remove only once
  day0 q is re-sourced off `ensemble_snapshots.members_json`. `tigge_db_fetcher.fetch_from_db`
  decision use (legacy EnsembleSignal bundle) — strip after confirming no live `tigge_client`
  forecast-bundle caller and repointing the ingest reuse.
- **Out of strip scope (legitimate non-decision surfaces):** offline bias-fit reads
  (`load_bucket_residuals` / `_forecast_means`, script-only), readiness CARRIERs
  (`forecast_snapshot_ready`, `executable_forecast_reader` eligibility), contract/unit guards,
  ingest writers/schema, provenance hashes, and `EnsembleSignal` (fresh-fetch, not the cold table).
