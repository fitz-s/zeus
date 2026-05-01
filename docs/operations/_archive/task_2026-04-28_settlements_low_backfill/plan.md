# LOW (mn2t6) Settlements Backfill — Plan + Execution Record

| Field | Value |
|---|---|
| Created | 2026-04-28 |
| Status | **APPLIED 2026-04-28** (48 rows inserted: 4 VERIFIED + 44 QUARANTINED) |
| Authority basis | `src/types/metric_identity.py::LOW_LOCALDAY_MIN`, `src/contracts/settlement_semantics.py`, sibling packet `task_2026-04-28_settlements_physical_quantity_migration` |
| Sibling RFC | `task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md` (forecast-side LOW; this packet is settlement-side LOW) |
| Snapshot | `state/zeus-world.db.pre-low-backfill-2026-04-28` (1.82 GB, taken before INSERT) |

## What this packet did

Backfilled LOW (mn2t6) settlements rows into `state/zeus-world.db::settlements` from Polymarket gamma API closed-event truth, cross-validated against `observations.low_temp` ground truth.

Pre-this-packet: zero LOW rows (per earlier subagent finding).
Post-this-packet: 48 LOW rows. The settlement-side LOW void is no longer absolute.

## Reality reconciliation (vs earlier subagent assumption)

The subagent's [P0 hard-blocker scoping](../task_2026-04-28_settlements_physical_quantity_migration/) (sibling packet) noted:
> `data/pm_settlement_truth.json` (1566 entries): both files are HIGH-only — no LOW indicator
> Implication: There is presently NO source on disk from which LOW (city, date) settlement bins can be derived for backfill.

That was correct about the **on-disk** truth. But probing the live Polymarket gamma API showed **66 LOW events do exist** (48 closed + 18 active, 8 cities, dates 2026-04-15 .. 2026-04-29). They were never persisted to disk because zeus's market scanner only caches in memory.

This packet re-scrapes them from gamma API directly and persists the 48 closed/resolved events.

## Scope

- **8 cities**: London, Seoul, NYC, Tokyo, Shanghai, Paris, Miami, Hong Kong
- **Date range**: 2026-04-15 .. 2026-04-27 (closed events as of scrape)
- **Total events scraped**: 48
- **Bin grammar parsed**: point (`15°C`), finite_range (`68-69°F`), lower_shoulder (`9°C or below`), upper_shoulder (`19°C or higher`)

## Plan outcome (executed)

| Class | Count | Reason |
|---|---|---|
| VERIFIED | 4 | obs.low_temp falls in winning bin: London 4-15 (11°C), NYC 4-15 (68-69°F), Tokyo 4-15 (15°C), Shanghai 4-15 (15°C) |
| QUARANTINED | 1 | `obs_outside_winning_bin`: Seoul 4-15 obs=9°C, market settled 10°C — real 1°C drift, mirrors HIGH KL/Cape-Town pattern |
| QUARANTINED | 43 | `no_observation_for_target_date`: zeus daily obs ingest only goes to 2026-04-19; events 4-22 .. 4-27 await obs catch-up |

## Reuse audit (Fitz code-provenance)

| Artifact | Verdict |
|---|---|
| HIGH `pe_reconstruct.py` (archived) | STALE_REWRITE — string-literal drift on `physical_quantity`; not reused. New script writes canonical strings directly. |
| `src/contracts/settlement_semantics.py` | CURRENT_REUSABLE; metric-agnostic. Used implicitly via the schema's bin grammar. |
| `src/types/metric_identity.py::LOW_LOCALDAY_MIN` | CURRENT_REUSABLE; canonical strings hardcoded into the script (stdlib-only). |
| settlements triggers (authority_monotonic, non_null_metric, verified_insert_integrity) | CURRENT_REUSABLE; LOW INSERTs verified compatible (VERIFIED need non-null value+bin, QUARANTINED don't). |

## Idempotency / atomicity / reversibility

- **Idempotency**: script ABORTS if any LOW row already exists from this writer. Rerun is safe (no-op).
- **Atomicity**: snapshot via `shutil.copy2` BEFORE any connection opens; `BEGIN IMMEDIATE` TXN; post-count assertion against plan total before `COMMIT`; auto-`ROLLBACK` on exception.
- **Reversibility**: `cp state/zeus-world.db.pre-low-backfill-2026-04-28 state/zeus-world.db` restores pre-state.

## Antibody coverage

- `tests/test_settlements_physical_quantity_invariant.py::test_settlements_low_uses_canonical_physical_quantity_or_absent` — PASSES post-backfill: every LOW row carries `physical_quantity = "mn2t6_local_calendar_day_min"`.
- `tests/test_settlements_physical_quantity_invariant.py::test_canonical_strings_match_registry` — guards canonical string registry.

## Follow-up tasks (out of scope here)

1. **Daily obs ingest catch-up**: zeus ingest pipeline lags ~9 days. Once `observations.low_temp` populates 2026-04-20 onward, the 43 QUARANTINED `no_observation_for_target_date` rows can be reactivated:
   - For each row, check if obs now exists; if obs in winning bin → transition to VERIFIED with `provenance_json.reactivated_by` set (per `settlements_authority_monotonic` trigger requirement)
   - For each row where obs outside bin → leave QUARANTINED, change reason to `obs_outside_winning_bin`
   - Estimated: ~40 rows will go VERIFIED once obs catches up

2. **Live LOW market scanner persistence**: the in-memory cache in `src/data/market_scanner.py` should also write to `market_events_v2` for future on-disk truth. Today: 0 rows in market_events*. Future: write LOW market scans to disk, eliminating the gap that this backfill closed manually.

3. **Continuous LOW backfill cron**: 8 cities ✕ ~1 market/day = 8 new closed LOW events per day. A daily cron rerun of `scrape_low_markets.py` + `backfill_low_settlements.py` would keep the LOW settlements current without manual intervention.

4. **Investigate Seoul 4-15 1°C drift**: the `obs_outside_winning_bin` quarantine. Could be:
   - WU rounding direction (half-up at 9.5 → 10)
   - WU finalized data ≠ initial-API-fetched data
   - Same root cause as HIGH KL/Cape-Town 1-unit drift (already enumerated in current_data_state.md §7)

## Files in this packet

| File | Purpose |
|---|---|
| `plan.md` | this file |
| `scripts/scrape_low_markets.py` | Polymarket gamma API → manifest JSON |
| `scripts/backfill_low_settlements.py` | manifest + obs JOIN → plan + DB INSERT (gated by --apply) |
| `evidence/pm_settlement_truth_low.json` | 48-event scraped manifest |
| `evidence/low_backfill_plan.json` | computed plan with VERIFIED/QUARANTINED classification |

## Reproducibility

```bash
# 1. scrape
python3 scripts/scrape_low_markets.py --out evidence/pm_settlement_truth_low.json

# 2. plan only (no DB writes)
python3 scripts/backfill_low_settlements.py \
    --manifest evidence/pm_settlement_truth_low.json \
    --db-path  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
    --plan-out evidence/low_backfill_plan.json

# 3. apply (only after plan review)
python3 scripts/backfill_low_settlements.py \
    --manifest evidence/pm_settlement_truth_low.json \
    --db-path  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
    --plan-out evidence/low_backfill_plan.json \
    --apply
```

## What this packet does NOT solve

- **Forecast-side LOW calibration**: solved by sibling RFC (`task_2026-04-28_weighted_platt_precision_weight_rfc/`) using `observations.low_temp` directly (NOT settlements). Settlements LOW is too sparse (48 rows, 8 cities, 13 days) to anchor calibration; obs LOW (42,749 rows, 51 cities, 28 months) is the proper training source.
- **51-city LOW coverage**: Polymarket only offers LOW markets for 8 cities. The other 43 zeus cities will never have LOW settlement rows from this source. This is a market-coverage limitation, not a data pipeline failure.
- **Pre-2026-04-15 LOW history**: Polymarket did not offer LOW markets before mid-April 2026. There is no historical LOW market truth to scrape.
