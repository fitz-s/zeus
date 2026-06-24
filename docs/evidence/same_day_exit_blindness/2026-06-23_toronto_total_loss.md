# Same-Day Exit Blindness — Toronto NO@24°C −98.94% total loss (2026-06-23)

Created: 2026-06-23
Authority basis: standing money-path mission + operator incident report 2026-06-23
("no continuous monitoring decision → all lost"); settlement-graded live-chain evidence.

## Operator-reported symptom
- Position: "Will Toronto high be 24°C on June 23?" — **NO @ 66¢, 21.2 shares**.
- Outcome: NO → **0.7¢**, **−$13.83 (−98.94%) total loss**.
- 15 orders placed today, **again all buy_no**, no re-decision, all lost.
- "没有持续监控的决策最后的下场就是全部输掉" (no continuous monitoring → all lost).

## Settlement-graded root cause: same-day held positions go EXIT-BLIND

### 1. Same-day forecast belief freezes at local-day-start
`forecast_posteriors` (zeus-forecasts.db), target_date=2026-06-23:
| target_date | rows last 6h | latest computed_at |
|---|---|---|
| 2026-06-23 (today/settlement) | 20 | **01:23:31 UTC — FROZEN** |
| 2026-06-24 | 320 | 21:32 UTC (fresh) |
| 2026-06-25 | 160 | 21:42 UTC (fresh) |

Zero same-day recomputes after 02:00 UTC (probe at 22:21 UTC). Future dates fresh.
Mechanism: `replacement_forecast_current_target_plan.build_*` flags a target
`day0_observed_extreme_required` once the city local day starts
(`_day0_observed_extreme_required` → `has_city_local_day_started`), and such a
target is no longer normally seedable (`can_seed = ... and not day0_observed_extreme_required`).
It can only refresh via the day0 observed-extreme path, which is not producing fresh
same-day posteriors → freeze.

Toronto frozen belief (computed 01:23 UTC): 24°C bin q_lcb = **0.1675** (the MODE,
yet <17%). NO(24°C)=0.83 > price 0.66 → bet NO on the modal bin. Flat belief → every
bin reads as a NO → systematic buy_no.

### 2. Held-position exit monitor correctly refuses the stale belief
`src/engine/position_belief.py` (2026-06-12 K1 single-authority anti-stale fix):
held-position exit belief = freshest `forecast_posteriors` row, `DEFAULT_MAX_AGE_HOURS = 9.0`.
By 19:00 UTC (when temp hit 24°C) the frozen 01:23 posterior is **~17.6h old** → `is_fresh=False`
→ exit monitor refuses to act on a stale belief (the exact disease the 2026-06-12 fix
killed for Karachi — now resurrected via same-day posterior freeze). The guard is CORRECT;
the bug is that same-day posteriors stop refreshing.

### 3. The intraday observed running-max (which proves the loss) never drives re-decision
- `observation_instants` (zeus-**world**.db) Toronto today: running_max climbed
  22→23→**24.0°C by 19:00 UTC**, fresh to 21:00 UTC, fully eligible
  (authority=VERIFIED, source_role=historical_hourly, training_allowed=1,
  causality_status=OK, station=CYYZ, source=wu_icao_history).
- Live reactor (`src.main`) day0 emit, EVERY cycle today:
  `day0_observation_instants_emitted=0` (authority lane=3, fast lane 0–5).
  The canonical observation scan (`Day0ExtremeUpdatedTrigger.scan_observation_instants_rows`,
  src/events/triggers/day0_extreme_updated.py) emits **zero** day0 events despite eligible 24°C data.
  Candidate severed point(s): line 321 `live_authority_status != "live"` rejects the
  `historical_hourly` WU rows; and/or `_qualified_observation_instants_table` surface;
  and/or the 2026-06-15 change-gate watermark.
- `day0_metric_fact`: **0 rows total** (the observed-extreme conditioning fact is empty).
- `decision_events`: **0 rows for target_date=2026-06-23** (no trade decision logged for any
  same-day market today). Toronto decision_certificates stop at **08:12 UTC** (all NO_SUBMIT).

### Causal chain (settlement-graded)
overnight flat/cold belief → NO@24 looks +EV → entered → same-day posterior freezes 01:23 UTC
→ exit belief >9h stale → exit monitor blind → observed running-max 24°C (the realized loss bin)
collected but never converts to a re-decision (observation lane emits 0) → NO@24 rides to
settlement → −98.94% total loss. Repeats for all 15 same-day buy_no positions.

This is RULE-1-compliant: the defect is LOCATED (exit-blindness mechanism), not "no edge".

## Secondary (operator complaint #1): deleted sources still in chain
Operator deleted `ecmwf_ifs025` (0.25°) and `jma` from the fusion candidate set, but
(grep of live tree): `ecmwf_ifs025` remains as the STORED_ANCHOR_MODEL legacy storage format
bridging to the live 9km anchor prior; `jma_seamless` remains referenced in the model-source-map
and download routing. They are out of the forward candidate set but not fully removed from the
plumbing. (ECMWF_IFS 9km is LIVE/REQUIRED as ANCHOR_MODEL — must NOT be removed.)

## VERIFIED ROOT (post-consult + live-log forensics, 2026-06-23)

The 0-emit is BENIGN (Toronto's observation_instants row converts to `live_authority_status="live"` — verified by running `observation_instant_row_to_day0_observation` on the live row; and 9 Toronto `DAY0_EXTREME_UPDATED` events fired today reaching high=24.0, so day0 events DO fire). The real sever is DOWNSTREAM — **the day0 event/observed-extreme never materializes a fresh `forecast_posteriors` row**, so the held-position exit monitor reads the frozen 01:23 prior (twin-authority: entry/day0 lane prices the observed 24°C in-memory; exit lane reads the stale persisted posterior).

Two compounding defects in the same-day belief-refresh path:
1. **Coverage gap:** `monitor_refresh._enqueue_single_family_belief_reseed_failsoft` (→ `enqueue_single_family_cycle_advance_reseed`, which already accepts `day0_observed_extreme_c` + `held_position`) fired for Dallas (172) / Milan (147) / Wellington (2) same-day today but **ZERO times for Toronto 2026-06-23**. Toronto's held position never reached the reseed call site (monitor_refresh.py:3086/3126/3151). And every reseed logged today carried `day0_observed_extreme=None` (mechanism CAN populate it — 137 historical, e.g. Wuhan 24→26 — but did not today).
2. **Plateau/intraday idempotency gap:** `cycle_advance_enqueues UNIQUE(city,target_date,metric,target_cycle_time)` is keyed by the MODEL cycle (`target_cycle` = freshest materializable cycle), NOT the observation version. Once a family is enqueued for the day's model cycle, intraday observation climbs/plateaus (running_max 22→23→24, same model cycle) hit `_already_enqueued=True` → reseed SKIPPED → posterior frozen. The model does not re-run intraday for same-day, so `target_cycle_time` never advances → the observed extreme never re-materializes.

Net: the held-position exit belief froze because the same-day day0-conditioned posterior refresh (a) didn't fire for Toronto and (b) is structurally blocked from intraday refresh by model-cycle idempotency.

## Consult-verified fix (REQ-20260623-174044-18fe71, Pro Extended)
- DO NOT lower `DEFAULT_MAX_AGE_HOURS=9.0` (it kills the stale-belief disease; correct). Make the posterior FRESH from observed data instead.
- **Observation-version materializer:** write a fresh `forecast_posteriors` via `condition_day0` on the canonical observed running max/min whenever a fresh observation version exists for a held/tradeable same-day family. Idempotency keyed by `(city,target_date,metric,station_id,observation_available_at,high_so_far/low_so_far)` — so PLATEAUS refresh (new obs version = new info: remaining-heating window shrank). Freshness (`computed_at`/`source_cycle_time`) tied to `observation_available_at`, not wall-clock — preserves the 9h guard (passes only when fresh observed evidence exists).
- **Catch-up/recovery materializer:** if a `DAY0_EXTREME_UPDATED` event (or fresh observation version) exists but no corresponding fresh `forecast_posteriors` row, write the missing posterior directly from `world.observation_instants` — a systematic safety net (fixes the Toronto coverage gap; the watermark suppresses re-emit, so recovery cannot depend on a duplicate event).
- Keep the 2026-06-15 monotone-advance gate on `DAY0_EXTREME_UPDATED` events (firehose control) but DECOUPLE posterior refresh from it (observation-version keyed).
- **Hard-fact exit:** already false-exit-safe (finite-bin-containing-extreme → None). Ship posterior refresh FIRST; hard-fact is a narrow absorbing-boundary salvage rail. EXACT-bin equality before terminal closure must stay HOLD. Add the full weather-change scenario matrix (consult-provided: high+low × >=/<=/exact × rising/touch/plateau/overshoot/fallback/oscillate/terminal) — invariant: before official terminal finality, finite/exact-bin containment is NEVER a hard-fact exit for the side that can still win.

## Fix surfaces (executable, operator-law-aligned — refresh the same-day belief; NO de-bias, NO cap)
- **A (exit-blindness, primary):** make same-day held positions re-decidable intraday — either
  (i) re-materialize a fresh same-day `forecast_posteriors` conditioned on the observed running-max
  via the existing day0 machinery (`replacement_cycle_advance_trigger` day0_observed_extreme_c +
  `predictive_distribution_builder.condition_day0`), keeping the exit belief fresh; and/or
  (ii) wire the observed hard-fact exit (`src/execution/day0_hard_fact_exit.py`) so a running-max
  that settles a held bin forces the exit regardless of posterior freshness.
- **B (concrete severed wire):** `scan_observation_instants_rows` emits 0 on eligible data —
  pin the exact reject (live_authority_status / surface / change-gate) and fix so the observed
  extreme drives re-decision. Make the empty-surface fallback fail-loud, not silent.
- **C (complaint #1):** finish removing `jma`/`ecmwf_ifs025` from model-source-map + download
  routing (keep ECMWF_IFS 9km anchor).
