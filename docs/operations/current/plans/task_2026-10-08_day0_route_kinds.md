# Day0 typed route kinds: landing note (2026-10-08)

Branch `fix/day0-fast-route-kinds`, rebased on origin/live `1e3db865f` (G9/G10 loaded). Built from the D1/G1/G8 work and the round-3 consult NO-GO for this branch (blockers A1 to A3) plus the independent review findings (A4 to A7).

## Deploy shape (A5): binding
- This branch changes the one Day0 fact law that the seed side (forecast-live / data-ingest) and the adapter side (MAIN) both read. A Data-only restart splits that law across processes. Replayed with branch seeds and the live adapter, 7 of 20 families fail binding (Tokyo `GLOBAL_DAY0_CONDITIONING_OBSERVATION_MISMATCH`; Lucknow, Helsinki, Munich, Amsterdam and Tokyo LOW `..._TIME_MISMATCH`).
- Load it only as `deploy_live.py restart all`, behind the reviewed-range gate (`safe_restart.sh`) and the forecast-live replay gate. Never split-load it.
- Pre-restart check: no open position in a proxy-conditioned family (Tokyo/Toronto JMA/SWOB). At 2026-10-09T00:04Z there were 0; the 8 rows in the affected cities are all `admin_closed` June positions with no shares.

## Who changes (A7, measured, not inferred)
- Fact sweep, all 54 cities, last 24 h hourly, `_latest_authorized_day0_fact` in both roles plus `latest_fast_station_conditioning`, branch head vs origin/live: 43 cities identical; 11 change.
- The 11 are the 7 route cities (Tokyo, Toronto, Ankara, Istanbul, Lucknow, Moscow, Helsinki) and the 4 physical-only-route cities (Warsaw `imgw_synop`, Munich `dwd_cdc`, Amsterdam `knmi_observations`, Jinan `wu_station_current`): physical-only routes are no Day0 fact.
- G8 (`fast_extreme_supersedes_settlement` in settlement integers) is fleet-wide. US °F sweep: 1 intended difference (Denver LOW 10-07 09:10Z, fast 15.3 °C = 59.5 °F rounds to 60, same as the page).

## What each item does
- A4, native report and AWC copy are one report (`_latest_authorized_day0_fact`). Identity is station + METAR issued instant + value. The copy received first owns the report; the other cannot move its clock. Test: `test_native_report_and_its_awc_copy_are_one_report` (native first and AWC first; ENTRY and HELD at +10 min).
  - Live replay of the 60 newest native-conditioned posteriors (Ankara 47, Istanbul 1, Moscow 12): ENTRY binds 60/60 at +2 and +10 min. Without A4: 24/60 and 4/60.
- A2, retired sources (`day0_is_retired_fact_source`: route kind INSTRUMENT_PROXY or PHYSICAL):
  - `_day0_observation_lag_reason` returns `basis=day0_retired_fact_source`, page row or not, so the plan marks the family uncovered and seed discovery reseeds it.
  - The queue boundary `_seed_source_cycle_boundary` admits a Day0-fact seed over a retired-source incumbent through the typed witness `_fact_seed_retires_retired_source`. Only that observation clock yields. Cycle ordering, fact-to-fact ordering, and retired-source seeds stay rejected.
  - Test: the consult's no-page Tokyo case (JMA 20.3 @06:44, AWC 20 @06:30) recovers at ENTRY and HELD.
- A6: `_request_contract_lapse_reason` retires a queued request that carries a retired source, whatever the exposure, before any child process. No revision bump. At 00:04Z the queue held 4 such seeds (Tokyo JMA, Warsaw IMGW x2, Helsinki FMI), 0 requests and 0 in-flight.
- A3, optional inputs: a native-sourced request whose shared carrier cannot be built because hourly vectors or the current-temperature state are absent no longer blocks. It writes the q that the source wrote before it joined the carrier region (`fused_normal_direct`, observation as provenance). Any other carrier failure still raises. Test: `test_native_report_without_carrier_inputs_serves_as_before` (q equal to the live branch of the same request; AWC still blocks).
- A1, native ENTRY age parity: the gate is in the protected adapter. Patch and tests are in the coordinator's scratchpad (`route_kinds_adapter_patch.diff`, `test_day0_native_entry_age.py`). It is not applied here; the owning lane lands it.

## Continuity evidence (A3, read-only live DBs, 2026-10-09T00:10Z)
- Latest live posterior per family, 11 affected cities, both trees:
  - Every retired-source posterior (Tokyo JMA, Helsinki FMI, Amsterdam KNMI) gets the retired lag reason on the branch.
  - Native-conditioned posteriors (Ankara, Istanbul, Moscow 10-09) bind ENTRY and HELD in both trees.
  - Tokyo 10-09 JMA posteriors bind on live and fail `..._OBSERVATION_MISMATCH` on the branch. That is the retirement, and its reseed reason is present.
- Branch-built seeds at 00:10Z for the 7 route cities bind 14/14 for ENTRY and HELD.
- Strict replay (`src/engine/replay.py`, `src/decision_kernel/**`, `scripts/*replay*`) never re-derives the Day0 binding from world facts. `verifier.py:336` reads the persisted `_edli_global_day0_binding`, and `certificates/execution.py:346-349` copies `settlement_source` and the provenance hash at decision time. Persisted certificates therefore replay unchanged.

- Steady state, causal replay 2026-10-08 05:50Z to 23:50Z, every 10 min: each tree builds its own seed at t from WORLD as of t, then binds ENTRY at t+2 and t+10 min. ENTRY binds at +10 min:
  | city | branch | live |
  | --- | --- | --- |
  | Ankara | native 116/174 | AWC 118/192 |
  | Moscow | native 137/203 | AWC 122/192 |
  | Istanbul | native 114/174 | AWC 120/194 |
  | Lucknow | page 146/212 | IMD 142/210 |
  | Tokyo | AWC 136/210 | JMA 37/131 + AWC 40/82 |
  | Helsinki | AWC 136/208 | FMI 2/110 + AWC 47/102 |
  The remaining `TIME_MISMATCH` at +10 min is the next report arriving inside the window. HELD binds those, and the next seed carries the new report, in both trees.
- Carrier-less native posteriors (the A3 fallback, and the persisted live rows). Branch consumer trace, read only:
  - `day0_conditioning_key`, `_day0_replacement_conditioning`, the bundle readers and coverage SQL treat a carrier-less native `fused_normal_direct` row as live does, or more permissively.
  - The one stricter path: when the current payload source is native (no page fact yet), the q path needs a survival likelihood and rebuilds the carrier from current vectors. Live blocks that case outright (`PROVISIONAL_SOURCE_REVISION_MODEL_UNAVAILABLE`); the branch prices it with the survival mixture.

## Residuals
- A1 is open until the adapter patch lands. Until then, a native conditioning older than 900 s is not ENTRY-refused, as on live.
- Lucknow's METAR margin of 7.0 °C (`metar_margin_units_for_city`) sinks IMD and AWC alike, so Lucknow seeds come from the page. This predates the branch.
- Not traced: whether the opportunity-events branch of the fact law can select a native source (no native event exists in the last 24 h); the adapter path at ~36334 (provisional finality with no prepared family) when the payload source is native.
