# mx2t3 Lifecycle-Carrier Decoupling — Design

```
# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: spine-source rewire 2026-06-16 (raw_model_forecasts belief);
#                  forecast_snapshot_ready.py family-readiness; replacement_0_1 trade authority
# Status: READ-ONLY DESIGN. No code edited. Exact edits specified for a later implementing session.
```

## TL;DR

`mx2t3` (the ECMWF OpenData 3h-max ENS product, `_opendata_mx2t6_cycle` job, 07:30 UTC)
is the **sole live writer of three tables that the trade lifecycle still rides on**:

| Table | Sole live writer | Verified |
|---|---|---|
| `ensemble_snapshots` (data_version `ecmwf_opendata_mx2t3/mn2t3...`) | `ecmwf_open_data` | rows to target 2026-06-22, max `available_at` 2026-06-16T12:00 |
| `source_run` | `ecmwf_open_data` ONLY (`SELECT DISTINCT source_id` = `['ecmwf_open_data']`) | confirmed |
| `source_run_coverage` | `ecmwf_open_data` ONLY (`SELECT DISTINCT source_id` = `['ecmwf_open_data']`) | confirmed |

The spine's **belief** (μ*, σ, members, q) was already rewired off mx2t3 on 2026-06-16 — it
reads `raw_model_forecasts` (8+ decorrelated NWP models/family) via
`_spine_multimodel_members_for_event`. **But three structural plumbing dependencies on the
mx2t3-fed tables remain**, and ANY of them stops the lifecycle if mx2t3 ingest is killed:

1. **Family-readiness / FSR selection** — `forecast_snapshot_ready.py::scan_committed_snapshots`
   joins `source_run_coverage` → `source_run` → `ensemble_snapshots`. No mx2t3 → empty join → **no FSR emitted → nothing decides.** (the problem stated.)
2. **Causal-cycle pin for the spine** — `_spine_multimodel_members_for_event` calls
   `_bound_forecast_snapshot_row_for_spine`, which pins on `event.causal_snapshot_id`
   against `ensemble_snapshots` **only to read `source_cycle_time` DATE**. No ensemble row →
   `bound is None` → `SPINE_INPUTS_UNAVAILABLE` → **no positive-edge candidate.** (NOT covered by the FSR fix alone — this is the second severed wire.)
3. **No-submit certificate forecast authority** — `_build_no_submit_proof_bundle_from_adapter_evidence`
   → `_forecast_authority_payload_and_clock` → `_read_executable_forecast_bundle_result` →
   `read_executable_forecast`, which reads `ensemble_snapshots.members_json` + derives
   `source_run`/`source_run_coverage` from `snapshot.source_run_id`. Runs for EVERY decision (incl. the live spine lane). No ensemble row → `FORECAST_AUTHORITY_EVIDENCE_MISSING` → **every candidate dies before submit.**

**Conclusion: decoupling the FSR trigger (task as literally stated) is NECESSARY BUT NOT SUFFICIENT.**
You must also re-source (2) the causal-cycle pin and (3) the no-submit cert's forecast
authority, or the lifecycle still dies one layer deeper. This document specifies all three,
plus the safe neutral carrier and the day0/exit edge cases.

The recommended neutral carrier is **`forecast_posteriors`** (product
`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1`), already materialized from
`raw_model_forecasts`, mx2t3-independent, current to target 2026-06-18 / computed 2026-06-17.
It already carries `(city, target_date, temperature_metric, source_cycle_time,
source_available_at, family_id, source_id, data_version)` — everything readiness + the causal
pin need — and is gated by the same `_replacement_trade_authority_enabled()` flag that already
governs the live lane.

---

## 1. What the `ensemble_snapshots` JOIN provides in `forecast_snapshot_ready.py`

`scan_committed_snapshots` (lines 470-717) builds `_select_sql_base` (lines 552-604). The
relevant structure:

```
WITH ranked_coverage AS (
    SELECT c0.*, ROW_NUMBER() OVER (PARTITION BY city,target_local_date,temperature_metric
        ORDER BY <LIVE_ELIGIBLE first>, computed_at DESC, coverage_id DESC) AS _family_rank
    FROM source_run_coverage c0
    WHERE c0.computed_at IS NULL OR c0.computed_at <= ?
)
SELECT c.*, sr.<run fields>, s.snapshot_id, s.city, s.target_date, s.temperature_metric,
       s.available_at, s.fetch_time, s.manifest_hash, s.members_json
FROM ranked_coverage c
JOIN source_run sr      ON sr.source_run_id = c.source_run_id
JOIN ensemble_snapshots s ON s.source_run_id = c.source_run_id
                          AND s.city=c.city AND s.target_date=c.target_local_date
                          AND s.temperature_metric=c.temperature_metric
                          AND s.snapshot_id = (SELECT MAX(s2.snapshot_id) ... )   -- _snapshot_latest_join
WHERE c._family_rank = 1
  AND COALESCE(s.available_at, sr.source_available_at, c.computed_at) <= ?
  ...
```

The `ensemble_snapshots s` JOIN contributes exactly these columns into the row dict, consumed by
`_snapshot_from_join` (lines 917-931) and the FSR payload (`build_forecast_snapshot_ready_event`):

| Column from `s` | Where it lands | LOAD-BEARING? | Spine consumes or overrides? |
|---|---|---|---|
| `s.snapshot_id` | `snapshot["snapshot_id"]` → `payload.snapshot_id` → `event.causal_snapshot_id` | **YES — identity binding.** This is the FSR's causal pin. | Spine **uses it as a pin only** (re-resolves `source_cycle_time` via it in `_bound_forecast_snapshot_row_for_spine`). Does NOT use its member content. |
| `s.members_json` | `snapshot["members_json"]`, `member_count` | Used by `classify_forecast_snapshot` for the `observed_members` floor fallback (`len(members_json)`) and by the FSR `member_count`. | **OVERRIDDEN for belief** — confirmed. Spine re-sources members from `raw_model_forecasts` via `_spine_multimodel_members_for_event` (adapter line 7770). The FSR's `members_json` never feeds q/σ/sizing on the live spine lane. |
| `s.available_at` | freshness (`snapshot_available_at`) → `available_at` payload, `COALESCE` gate | Used as a freshness floor in the WHERE and as the FSR `available_at` (proof-of-possession). | Freshness semantics needed; the **specific ensemble row** is not. |
| `s.fetch_time` | `snapshot_fetch_time` → `available_at` / `captured_at` preference | Possession clock. | Same — semantics needed, ensemble row not. |
| `s.manifest_hash` | `snapshot["snapshot_hash"]` → `payload.snapshot_hash` | Provenance hash on the FSR payload (defaults to snapshot_id if absent). | Not load-bearing for the decision; provenance only. |
| `s.source_run_id` (= c.source_run_id) | links the join | Used to fetch `source_run`/`coverage` rows. | The **causal source_run linkage**. (mx2t3-only.) |

**`_snapshot_latest_join`** (lines 539-548) picks the latest `snapshot_id` per
(source_run, city, target, metric) — a within-ensemble dedup that becomes moot once the JOIN is removed.

**Verdict on §1:** Of everything the JOIN provides, only **(a) a `snapshot_id` value to carry as
`causal_snapshot_id`** and **(b) a freshness `available_at`/`fetch_time`** are genuinely needed by
the FSR contract. `members_json` is overridden (confirmed). The `source_run_id` linkage is needed
only because the no-submit cert (§3) re-derives `source_run`/`coverage` from it — and that
linkage is itself an mx2t3 artifact we are decoupling.

---

## 2. Schemas + population (verified against `state/zeus-forecasts.db`, read-only)

### `source_run_coverage` (readiness authority)
Columns: `coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
observation_field, data_version, expected_members, observed_members, expected_steps_json,
observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
completeness_status, readiness_status, reason_code, computed_at, expires_at, recorded_at`

Population: **`source_id = 'ecmwf_open_data'` ONLY.** Recent rows are
`data_version='ecmwf_opendata_mx2t3_local_calendar_day_max'`, `readiness_status='LIVE_ELIGIBLE'`,
`completeness_status='COMPLETE'`, ~1476 rows/day. **No open-meteo / raw_model_forecasts coverage rows exist.**

### `source_run`
Population: **`source_id='ecmwf_open_data'` ONLY** (`mx2t6_high_full_horizon` / `mn2t6_low_full_horizon`).

### `raw_model_forecasts` (the live belief source)
Columns include: `raw_model_forecast_id, model, city, target_date, metric, source_cycle_time,
source_available_at, captured_at, lead_days, forecast_value_c, source_id, source_family,
product_id, ...`

Population: open-meteo multi-model (`ecmwf_ifs`, `gfs_global`, `icon_global`, `gem_global`,
`jma_seamless`, `ukmo_*`, `ncep_nbm_conus`, `meteofrance_arome`, ...). Current to target
2026-06-19, captured 2026-06-17. **8+ distinct models per (city, target, metric, cycle).**

**Critical finding:** `raw_model_forecasts` carries per-`(city, target_date, metric,
date(source_cycle_time))` rows (200 distinct family-cycle keys for targets ≥ 06-17) BUT has **no
`source_run_coverage` / `source_run` rows of its own**, and no `readiness_status`/`completeness_status`
column. So readiness cannot be JOINed straight off it without either (a) synthesizing coverage, or
(b) using the already-materialized `forecast_posteriors` neutral carrier.

### `forecast_posteriors` (RECOMMENDED neutral carrier — already mx2t3-independent)
Columns: `posterior_id, source_id, product_id, data_version, city, target_date,
temperature_metric, source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
q_ucb_json, posterior_method, aifs_source_run_id, openmeteo_anchor_id,
dependency_source_run_ids_json, family_id, bin_topology_hash, dependency_hash,
posterior_config_hash, posterior_identity_hash, provenance_json, trade_authority_status,
training_allowed, recorded_at`

Population: `product_id='openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1'`, 7374 rows,
current to target 2026-06-18, computed 2026-06-17T06:33. Materialized from `raw_model_forecasts`
by `src/data/replacement_forecast_materializer.py`. **This row already exists at decision time on
the live replacement lane** (the FSR `replacement_filter`, lines 511-523, already requires it:
`EXISTS (SELECT 1 FROM forecast_posteriors fp WHERE fp.product_id=REPLACEMENT_0_1_PRODUCT_ID AND
fp.city=c.city AND fp.target_date=c.target_local_date AND fp.temperature_metric=c.temperature_metric
AND fp.source_available_at <= ? AND fp.computed_at <= ?)`).

---

## 3. The decoupling design

### Design principle
Keep the public FSR contract byte-stable (same `OpportunityEvent`/`ForecastSnapshotReadyPayload`
shape, same `causal_snapshot_id` field), but **change the three data sources** so none of the
three plumbing dependencies touches mx2t3:

- **(A) Readiness/selection** ← `forecast_posteriors` (or synthesized neutral coverage) instead of
  `source_run_coverage` JOIN `source_run` JOIN `ensemble_snapshots`.
- **(B) Causal-cycle pin** ← `raw_model_forecasts.source_cycle_time` instead of the bound
  `ensemble_snapshots.source_cycle_time`.
- **(C) No-submit cert forecast authority** ← a `raw_model_forecasts`/`forecast_posteriors`-backed
  reader instead of `read_executable_forecast` over `ensemble_snapshots.members_json`.

Because the live lane is already gated by `_replacement_trade_authority_enabled()`, the cleanest
implementation **forks the readiness query on that flag**: when replacement authority is ON (the
live regime that does not consult ensemble belief), readiness + binding come from
`forecast_posteriors`; the legacy ensemble path stays intact and untouched for OFF.

### (A) Readiness / selection — `forecast_snapshot_ready.py`

The `causal_snapshot_id` does NOT have to be an `ensemble_snapshots.snapshot_id`. It is an opaque
string identity carried on the event and re-resolved downstream. We mint a **deterministic neutral
snapshot identity** from the posterior/raw-model family-cycle instead.

**Old (lines 552-604, the `_select_sql_base` core):**
```sql
FROM ranked_coverage c
JOIN source_run sr ON sr.source_run_id = c.source_run_id
JOIN ensemble_snapshots s
  ON s.source_run_id = c.source_run_id
 AND s.city = c.city AND s.target_date = c.target_local_date
 AND s.temperature_metric = c.temperature_metric
 {_snapshot_latest_join}
WHERE c._family_rank = 1
  AND COALESCE(s.available_at, sr.source_available_at, c.computed_at) <= ?
  ...
```

**New (replacement-authority lane): source readiness from `forecast_posteriors`, mint a neutral
snapshot id.** Replace the ensemble JOIN with a `forecast_posteriors fp` JOIN and project a
synthesized snapshot identity:

```sql
WITH ranked_posterior AS (
    SELECT
        fp.*,
        ROW_NUMBER() OVER (
            PARTITION BY fp.city, fp.target_date, fp.temperature_metric
            ORDER BY fp.source_cycle_time DESC, fp.computed_at DESC, fp.posterior_id DESC
        ) AS _family_rank
      FROM forecast_posteriors fp
     WHERE fp.product_id = :replacement_product_id
       AND (fp.source_available_at IS NULL OR fp.source_available_at <= ?)
       AND (fp.computed_at        IS NULL OR fp.computed_at        <= ?)
)
SELECT
    p.city, p.target_date AS target_local_date, p.temperature_metric,
    p.city AS snapshot_city, p.target_date AS snapshot_target_date,
    p.temperature_metric AS snapshot_temperature_metric,
    p.source_id, p.data_version, p.family_id,
    p.source_cycle_time     AS sr_source_cycle_time,
    p.source_available_at   AS sr_source_available_at,
    p.source_available_at   AS snapshot_available_at,
    p.computed_at           AS snapshot_fetch_time,
    -- NEUTRAL synthesized snapshot identity (no ensemble_snapshots row needed):
    'rmf-' || p.city || '|' || p.target_date || '|' || p.temperature_metric
            || '|' || substr(p.source_cycle_time,1,10) AS snapshot_id,
    p.posterior_identity_hash AS snapshot_manifest_hash,
    NULL AS snapshot_members_json,          -- spine overrides; never read for belief
    'COMPLETE'      AS completeness_status,  -- the posterior's own existence IS completeness
    'LIVE_ELIGIBLE' AS readiness_status,
    NULL AS expected_steps_json, NULL AS observed_steps_json,
    NULL AS expected_members,  NULL AS observed_members,
    p.source_cycle_time AS source_cycle_time
FROM ranked_posterior p
WHERE p._family_rank = 1
  AND (p.source_available_at IS NULL OR p.source_available_at <= ?)
  AND (p.computed_at        IS NULL OR p.computed_at        <= ?){market_filter}
ORDER BY p.source_cycle_time DESC, p.computed_at DESC
```

Notes / why this is safe:
- **`snapshot_id`** is now a deterministic `rmf-<city>|<target>|<metric>|<cycle_date>` string. It is
  unique per family-cycle, stable across re-scans (idempotency preserved), and is exactly what the
  causal-cycle pin in (B) parses (`[:10]` of cycle date is embedded).
- **`completeness_status='COMPLETE'` / `readiness_status='LIVE_ELIGIBLE'`** are correct by
  construction: a `forecast_posteriors` row only exists when the materializer has a complete
  decorrelated model set (its own `< 3 members` / topology gates already ran upstream). We are not
  relaxing a gate — we are reading a DIFFERENT, already-certified completeness authority.
- The `classify_forecast_snapshot` member-floor checks (`observed_members >= expected_members`,
  `required_steps_present`) degrade to vacuously-true when those columns are NULL/posterior-sourced.
  **Implementation must set `min_members_floor`-equivalent and `required_steps` handling to treat a
  posterior-backed row as complete** — see the classifier edit below.
- The existing `replacement_filter` (lines 511-523) becomes **redundant** on this lane (we now JOIN
  the posterior directly) — keep it harmless or drop it under the same flag.

**`_FORECAST_TABLES` guard (line 847):** today `('source_run','source_run_coverage','ensemble_snapshots')`.
Under the replacement lane this must become `('forecast_posteriors',)` (or test all-of-either) so
`scan_committed_snapshots` does not early-return `[]` when the ensemble tables are absent/stale.

**`classify_forecast_snapshot` (lines 240-336):** add a posterior-backed short-circuit. When the
row carries `readiness_status='LIVE_ELIGIBLE'` + `completeness_status='COMPLETE'` from the
posterior AND `members_json`/steps are NULL (posterior provenance), return
`ForecastSnapshotClassification("COMPLETE", True, True, True, "COMPLETE_POSTERIOR_BACKED")` without
demanding `observed_members >= expected_members` (which would be `0 >= 51` and fail). Gate this on a
`source == posterior` / data_version marker so the legacy ensemble path keeps its full member checks.

**`_serving_track_label` (lines 811-826):** already returns `REPLACEMENT_0_1_TRACK_LABEL` when
`_replacement_trade_authority_enabled()` — unchanged, correct.

### (B) Causal-cycle pin — `event_reactor_adapter.py::_spine_multimodel_members_for_event` (11185) and `_bound_forecast_snapshot_row_for_spine` (11320)

Today `_spine_multimodel_members_for_event` does:
```python
bound = _bound_forecast_snapshot_row_for_spine(conn, event=event, family=family, decision_time=decision_time)
if bound is None: return None
_causal_sct = bound.get("source_cycle_time") or bound.get("issue_time")
causal_cycle_date = str(_causal_sct)[:10]
```
`_bound_forecast_snapshot_row_for_spine` pins `CAST(snapshot_id AS TEXT) = event.causal_snapshot_id`
against `ensemble_snapshots`. **With (A) the causal_snapshot_id is now `rmf-...|<cycle_date>` and
there is no ensemble row → `bound is None` → spine returns None → SPINE_INPUTS_UNAVAILABLE.**

**Fix — derive the causal cycle WITHOUT the ensemble row.** Two clean options:

- **Option B1 (preferred, zero new lookup):** parse the cycle date directly from the neutral
  `causal_snapshot_id` minted in (A). The id ends in `|YYYY-MM-DD`. Replace the bound-row pin with:
  ```python
  _cid = str(getattr(event, "causal_snapshot_id", "") or "")
  if _cid.startswith("rmf-") and "|" in _cid:
      causal_cycle_date = _cid.rsplit("|", 1)[-1][:10]
  else:
      # legacy ensemble path (flag OFF): keep the existing _bound_forecast_snapshot_row_for_spine pin
      bound = _bound_forecast_snapshot_row_for_spine(...)
      ...
  ```
- **Option B2 (robust fallback):** when no ensemble row binds, pin the causal cycle to the **latest
  `raw_model_forecasts.source_cycle_time` for `(city, target_date, metric)` with
  `source_available_at <= decision_time`**:
  ```sql
  SELECT MAX(date(source_cycle_time)) FROM raw_model_forecasts
   WHERE city=? AND metric=? AND target_date=? AND source_available_at <= ?
  ```
  This is the same family-cycle the member query then reduces over, so causal-run == reader-run is
  preserved (the 2026-06-04 0-receipts invariant).

Prefer **B1** for deterministic causal/executable-run equality (the cycle in the id is exactly the
one (A) selected); keep **B2** as the fallback when the id is legacy-shaped. The downstream member
SELECT over `raw_model_forecasts` (lines 11255-11283) is **unchanged** — it already keys on
`date(source_cycle_time)=causal_cycle_date` and needs no ensemble row.

### (C) No-submit certificate forecast authority — the deepest cut

`_forecast_authority_payload_and_clock` (6485) is called from
`_build_no_submit_proof_bundle_from_adapter_evidence` (6073) for **every** decision, including the
live spine lane. It:
1. `_forecast_snapshot_row_for_event(... allow_latest = (event_type=='DAY0_EXTREME_UPDATED'))` —
   reads the `ensemble_snapshots` row pinned on `causal_snapshot_id` (raises
   `FORECAST_AUTHORITY_EVIDENCE_MISSING:snapshot` if absent).
2. Derives `source_run` and `source_run_coverage` from `snapshot.source_run_id`.
3. `_read_executable_forecast_bundle_result` → `read_executable_forecast`, which reads
   `ensemble_snapshots.members_json` (`executable_forecast_reader.py` lines 633-671, 855).
4. Emits `members_json_source = "ensemble_snapshots.daily_extrema"`, `members_json_hash`, and the
   `ens_result` carried into the cert.

**This entire payload is PROVENANCE for the no-submit certificate — NOT belief.** The belief (q/σ)
on the live spine lane comes from the spine (B). But the cert still binds the ensemble row, so with
mx2t3 stopped, step 1 raises and the candidate dies.

**Fix — fork `_forecast_authority_payload_and_clock` on the replacement lane** to build the same
payload shape from `forecast_posteriors` + `raw_model_forecasts` instead of `ensemble_snapshots`:

```python
def _forecast_authority_payload_and_clock(conn, *, event, family, payload, decision_time):
    if _replacement_trade_authority_enabled() and event.event_type in _FORECAST_DECISION_EVENT_TYPES \
       and not event.event_type in _DAY0_LANE_EVENT_TYPES:
        return _forecast_authority_payload_from_posterior(conn, event=event, family=family,
                                                          payload=payload, decision_time=decision_time)
    # ...existing ensemble-backed body unchanged (legacy + day0 lanes)...
```

`_forecast_authority_payload_from_posterior` (NEW) reads the `forecast_posteriors` row for
`(product_id, city, target_date, temperature_metric, source_available_at<=t, computed_at<=t)` and
the `raw_model_forecasts` member set for its `source_cycle_time` date, then assembles the SAME
`payload_out` dict keys (`identity`, `snapshot_id`, `reader_authority`, `city`, `target_date`,
`metric`, `members_json_source='raw_model_forecasts.multimodel'`, `members_json_hash=<sha256 of the
sorted native member set>`, `forecast_source_id=product_id`, `model`, `forecast_issue_time=source_cycle_time`,
`forecast_available_at=source_available_at`, `data_version`, `source_run_id=posterior_identity_hash`,
`track=REPLACEMENT_0_1_TRACK_LABEL`, ...). The `EvidenceClock` is built from
`source_available_at`/`computed_at`/`decision_time` exactly as the ensemble path builds it from the
snapshot/run clocks.

Key substitutions for the cert payload:
- `snapshot_id` / `identity` ← the neutral `rmf-...` id (same as `causal_snapshot_id`).
- `members_json_hash` ← sha256 of the sorted `raw_model_forecasts` native member list (the same set
  the spine used) — preserves the hash-binding invariant without an ensemble row.
- `source_run_id` ← `posterior_identity_hash` (a real, unique provenance id from the posterior).
- The `read_executable_forecast` call (step 3) is **replaced** by reading the posterior's
  `q_json`/`q_lcb_json` (already mx2t3-free) OR simply by NOT calling it (the spine already owns
  belief; the cert needs identity+hash+clock, which we now have). Confirm with the cert builder which
  `ens_result` fields are read back: they are provenance fields only (`model`, `issue_time`,
  `valid_time`, `fetch_time`, `available_at`, `degradation_level`) — all derivable from the posterior
  + raw_model_forecasts without `members_json`.

**This is the largest edit.** It must preserve every `payload_out` key the cert consumer reads, or
the no-submit cert validation fails on a missing field. The safe path: enumerate the keys
`_build_no_submit_proof_bundle_from_adapter_evidence` and the cert validator actually read from
`forecast_payload` (grep `forecast_payload.get(` / `forecast_payload[`), and populate exactly those
from the posterior. Anything used purely for the legacy ensemble cert (e.g.
`local_date_window_hash` over `local_day_start_utc`/`forecast_window_*`) gets a posterior-equivalent
or a documented neutral constant.

---

## 4. Edge cases / risks — what must NOT break

### Day0 lane (`DAY0_EXTREME_UPDATED`) — DO NOT decouple; keep ensemble.
- Day0 is **not** in `_FORECAST_DECISION_EVENT_TYPES`; it routes to the LEGACY decision path
  (adapter lines 2495-2508: the spine reads no day0 observation, `_NoDay0Reader`).
- `_forecast_authority_payload_and_clock` uses `allow_latest=True` for day0 →
  `_forecast_snapshot_row_for_event` returns the **latest available** ensemble snapshot (base), not
  the causal one, because day0 prices an observed running extreme against a forecast base.
- **Risk:** if mx2t3 stops, the day0 lane's ensemble base goes stale/empty. **The fork in (C) MUST
  exclude day0** (`not in _DAY0_LANE_EVENT_TYPES`) so day0 keeps its ensemble path. This means: **if
  mx2t3 ingest is fully stopped, the day0 lane degrades** (no fresh ensemble base). Operator decision
  required: either (i) day0 is already shadow-only on the live scope
  (`edli_live_scope` in `retired_day0_no_submit_scope`/`forecast_plus_day0`, RETIRED_DAY0_NO_SUBMIT_MARKER → never submits)
  so its degradation is harmless, OR (ii) day0 needs its own raw_model_forecasts base before mx2t3
  can stop. **Verify the live `edli_live_scope` first** — if day0 is shadow, decoupling is safe to
  ship without a day0 base.

### Exit / monitor / settlement lane — NOT on the FSR/decision carrier.
- Exit and settlement (harvester) read `ensemble_snapshots` for **historical learning-context joins
  and settlement labeling** (`harvester.py` lines 180-182, 674, 1762-1847), NOT for live
  family-readiness. These are keyed on already-written rows and tolerate the ingest stopping going
  forward (they read what exists). **No change needed**, but confirm no exit path re-derives a live
  `causal_snapshot_id` against `ensemble_snapshots` for an open position opened under the new neutral
  id — if it does, it must accept the `rmf-...` id shape.

### Idempotency / re-decision continuity
- `entity_key = city|target|metric|source_run_id` and `causal_snapshot_id` change shape under the
  neutral carrier. Open positions / pending events minted under the OLD ensemble `snapshot_id` must
  still reconcile. **Risk:** a cutover mid-flight could orphan a pending FSR whose `causal_snapshot_id`
  is an integer ensemble id while the spine pin now expects `rmf-...`. Mitigate: keep Option B2
  (raw_model_forecasts MAX-cycle fallback) so a legacy-shaped causal id still resolves a cycle, AND
  drain/settle in-flight events before the cutover.

### Causal-run == executable-run invariant (2026-06-04 0-receipts root)
- The `CoverageFairnessRequest.select_rows` freshness tie-break (lines 122-192) elects the FRESHEST
  run; the spine member query must reduce over the SAME cycle. Under the neutral carrier the
  freshness key uses `source_cycle_time` (posterior) and the pin (B1) parses the SAME cycle from the
  id → equality preserved. **Must verify** the posterior's `source_cycle_time` equals the
  `raw_model_forecasts` cycle its members come from (the materializer should guarantee this; confirm).

### Member-floor / completeness honesty
- The posterior-backed `COMPLETE` short-circuit (A) must NOT admit a family the materializer left
  partial. `forecast_posteriors` rows are written only after the materializer's own
  decorrelated-model + topology gates pass, so existence ⇒ complete. **Confirm** the materializer
  has no "partial posterior" write path before relying on existence-as-completeness.

### Flag coherence
- All three edits fork on `_replacement_trade_authority_enabled()`
  (`openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled`). If that flag is OFF, the legacy
  ensemble path runs and mx2t3 CANNOT be stopped. **Pre-stop checklist: confirm the flag is ON live**
  (it is the live probability authority per the spine rewire). With it ON, the ensemble lane is
  already dormant for belief; these edits remove its last plumbing footholds.

---

## 5. Stop-order / cutover sequence (so nothing breaks)

1. Confirm `_replacement_trade_authority_enabled()` is ON live and `forecast_posteriors`
   (`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1`) is current to the live target horizon.
2. Confirm `edli_live_scope` for day0 (shadow vs live). If day0 is live-submitting, build a
   raw_model_forecasts day0 base FIRST (out of scope here) or accept day0 going shadow.
3. Land edits (A)+(B)+(C) behind the replacement flag; the legacy ensemble path stays intact for OFF.
4. Soak in parallel: with mx2t3 STILL running, verify the live lane emits FSR + decides + builds
   no-submit certs entirely from the posterior/raw_model_forecasts sources (grep receipts for
   `members_json_source='raw_model_forecasts.multimodel'` and neutral `rmf-...` snapshot ids; assert
   ZERO `FORECAST_AUTHORITY_EVIDENCE_MISSING` / `SPINE_INPUTS_UNAVAILABLE` on the live lane).
5. Drain in-flight ensemble-id events; then stop `_opendata_mx2t6_cycle` / `ingest_opendata_daily_mx2t6`.
6. Post-stop: `ensemble_snapshots`/`source_run`/`source_run_coverage` freeze. Verify the lifecycle
   still produces FSR → decision → submit with zero dependency on the frozen tables.

---

## 6. Exact-edit index (for the implementing session)

| # | File | Symbol / lines | Edit |
|---|---|---|---|
| A1 | `src/events/triggers/forecast_snapshot_ready.py` | `_select_sql_base` 552-604 | Fork on replacement flag: replace `source_run_coverage`→`source_run`→`ensemble_snapshots` CTE+JOINs with the `ranked_posterior` query over `forecast_posteriors` (§3A). Mint neutral `snapshot_id`. |
| A2 | same | `_FORECAST_TABLES` 847 | On replacement lane, require `('forecast_posteriors',)` instead of the 3 ensemble tables. |
| A3 | same | `classify_forecast_snapshot` 240-336 | Posterior-backed `COMPLETE` short-circuit (skip member/step floors when posterior-sourced). |
| A4 | same | `_snapshot_from_join` 917-931 / `_snapshot_latest_join` 539-548 | `members_json` → empty list (overridden); drop `_snapshot_latest_join` on the posterior lane. |
| B1 | `src/engine/event_reactor_adapter.py` | `_spine_multimodel_members_for_event` 11185-11283 | Parse causal cycle from the `rmf-...|<date>` `causal_snapshot_id`; keep ensemble `_bound_...` only for legacy ids. Add raw_model_forecasts MAX-cycle fallback (B2). |
| B2 | same | `_bound_forecast_snapshot_row_for_spine` 11320 | No edit if B1 short-circuits; otherwise leave for legacy lane. |
| C1 | same | `_forecast_authority_payload_and_clock` 6485-6600+ | Fork on replacement+non-day0: route to new `_forecast_authority_payload_from_posterior`. |
| C2 | same | NEW `_forecast_authority_payload_from_posterior` | Build the identical `payload_out`/`EvidenceClock` from `forecast_posteriors`+`raw_model_forecasts` (no `ensemble_snapshots`, no `read_executable_forecast`). |
| — | day0 / exit / harvester | — | NO CHANGE. day0 keeps ensemble (must stay excluded from the fork); exit/settlement read historical rows. |

---

## 7. Confirmations of the brief's stated facts

- ✅ Spine builds belief from `raw_model_forecasts`, NOT `ensemble_snapshots` — confirmed
  (`qkernel_spine_bridge.py` lines 54-66, 175-181, 464-596; adapter `_spine_multimodel_members_for_event`).
- ✅ `members_json` from the FSR is OVERRIDDEN — confirmed (adapter lines 7749-7785 stash
  `raw_model_forecasts` members onto `payload["_edli_spine_*_members_native"]`).
- ✅ Family-readiness rides `ensemble_snapshots` via the `ranked_coverage`→`source_run`→
  `ensemble_snapshots` JOIN — confirmed (lines 552-604).
- ✅ `source_run_coverage`/`source_run` populated ONLY by `ecmwf_open_data` (mx2t3) — confirmed.
- ✅ The spine STILL needs a `snapshot_id` — confirmed, but only as a CAUSAL-CYCLE PIN
  (`_bound_forecast_snapshot_row_for_spine` reads `source_cycle_time` DATE off it), NOT for members.
  The fix re-sources the cycle from the neutral id / raw_model_forecasts.
- ➕ **NEW (not in brief): the no-submit certificate forecast authority** also hard-binds
  `ensemble_snapshots` + `read_executable_forecast(members_json)` for EVERY decision
  (`_forecast_authority_payload_and_clock` → `_read_executable_forecast_bundle_result`). This is a
  THIRD severed wire the FSR fix alone does not cover; (C) addresses it.
