# Backlog snapshot — 2026-04-14

Rolling task list captured at the end of the calibration-refactor session
(commits `df13308` / `45745ba` / `854cf5d` / `ed13310`). Categorized by
unblocking condition, not by invention date. Task IDs match the session
TaskList; items referenced by letter (`B`, `F`, `G.6`, …) are the K4 /
100-bug change labels from the prior session plan.

---

## 1. In-progress (running background work)

| ID | Task | State |
|----|------|-------|
| **#55** | `backfill_wu_daily_all.py --all --missing-only --days 834` (2026-04-14 restart after killing stale-Layer3 PID 62635) | Sequential PID **49371** on Step 2 (Open-Meteo hourly). Step 1 WU done at 12:44. Many `SSL: UNEXPECTED_EOF_WHILE_READING` / `Connection reset by peer` chunks on Open-Meteo API side — all logged as `FAILED` in `data_coverage` with 1h retry embargo, fillback will re-fetch. |
| **#57** | [DATA LOSS EVENT] `historical_forecasts` / forecasts table Rainstorm-migrated 171K rows wiped | Will re-populate from `forecasts` table after backfill completes via `scripts/etl_historical_forecasts.py`. Blocked on backfill. |
| *waiter* | `post_sequential_fillback.sh` PID **50114** | Sleeping, polls PID 49371 every 60s. Will kick off WU `--all --missing-only` fillback + HKO refresh + `hole_scanner --scan all` when sequential exits. |

Monitor `bl49mvsry` persistent-tails the rebuild log. No manual intervention required unless mass SSL errors escalate to full-city failures.

---

## 2. Unblocked after backfill + TIGGE complete

These are the **post-download ETL cascade** that produces the 9 empty derived tables required by live-engine calibration.

| ID | Task | Depends on | Notes |
|----|------|------------|-------|
| **#63** | Post-fillback derived ETL cascade (11 steps) | Backfill (#55) + TIGGE raw ingest | Order: `rebuild_settlements --no-dry-run` → `rebuild_calibration_pairs_canonical --no-dry-run --force` (NEW path from `df13308`, replaces `generate_calibration_pairs.py`) → `etl_historical_forecasts.py` ∥ `etl_forecast_skill_from_forecasts.py` → `etl_hourly_observations.py` ∥ `etl_diurnal_curves.py` ∥ `etl_temp_persistence.py` → `refit_platt.py` → `hole_scanner --scan all` → `hole_scanner --report`. |
| **#61** | TIGGE raw→DB transfer rewrite | User's TIGGE download | Current 41,261 `ensemble_snapshots` rows all `data_version='tigge_step024_v1_*'` (partial, unaudited). The new rebuild script refuses to touch them without `--allow-unaudited-ensemble`. Full TIGGE ingest needs a rewrite of the raw→DB transfer path to produce audited `authority='VERIFIED'` rows with non-partial `data_version`. |
| **#52** | Change L — run TIGGE multi-step GRIB extraction | TIGGE download complete | Upstream of #61. |
| **#53** | Change M — create `scripts/ingest_grib_to_snapshots.py` | #52 | Writes `ensemble_snapshots` from extracted GRIB. |

**Critical architectural note:** task #63 used to read `generate_calibration_pairs.py` at step 2. The 2026-04-14 refactor (`df13308`) replaced that with `scripts/rebuild_calibration_pairs_canonical.py`, which no longer depends on `market_events` (which was lost in #57 and cannot be recovered per user directive — `rainstorm.db` is rejected as unaudited). The new script runs end-to-end from `observations` + `ensemble_snapshots.members_json` alone. See `~/.claude/plans/logical-chasing-ritchie.md` for the full design.

---

## 3. 100-bug calibration fixes (blocked on `calibration_pairs` being populated)

All four depend on #63 producing `calibration_pairs` rows under the new canonical grid before they can be verified end-to-end. Code can be written now; tests must pass with a seeded in-memory DB fixture.

| ID | Change | File:line | Rationale |
|----|--------|-----------|-----------|
| **#47** | J — eps value | `src/calibration/*` | OPEN QUESTION: current code uses `eps=0.01`, math spec (`docs/reference/zeus_math_spec.md`) says `1e-6`. Pick one and align both code + spec. Neither is obviously wrong — `0.01` is conservative against numerical underflow at Platt edges, `1e-6` is theoretically tighter. User decision required. |
| **#48** | K — Platt bootstrap by decision_group | `src/calibration/platt.py:112-113` (the `rng.choice(len(outcomes), len(outcomes), replace=True)` call) | DEFERRED §10.1. Current bootstrap resamples at the **pair row** level, which inflates effective sample size because many pairs come from the same forecast event. Spec: resample at the `decision_group_id` level. Requires refactoring `ExtendedPlattCalibrator.fit()` to accept groups and resample groups, not rows. |
| **#49** | N — live `_bin_probability` histogram equivalence | `src/calibration/market_fusion.py` or wherever `_bin_probability` lives | DEFERRED §10.1. Prove the live bin probability histogram matches the one used during Platt training (same MC path now shared via `p_raw_vector_from_maxes` after `df13308`, which simplifies this). Write a property-based relationship test. |
| **#50** | G.7 — `maturity_level` uses `n_eff` not row count | `src/calibration/manager.py` `maturity_level()` | DEFERRED §10.1. Currently `maturity_level(n_samples)` where `n_samples = len(pairs)`. Should be `maturity_level(n_eff)` where `n_eff = COUNT(DISTINCT decision_group_id)`. Ties into #48. |

---

## 4. Deferred / quarantined changes from prior plan

These are NOT unblocked by post-download work; they're small targeted cleanups the prior plan deferred out of scope.

| ID | Change | File:line | Priority |
|----|--------|-----------|----------|
| **#41** | G.6 — delete `store.py decision_group_id` fallback | `src/calibration/store.py:77-78` (the `if decision_group_id is None: decision_group_id = f"{city}|..."` block) | 7 substeps documented in prior plan. DEFERRED. Removing the fallback turns silent misuse into a loud error; call sites must supply `decision_group_id` explicitly. |
| **#42** | H — `Bin` ±inf + `to_json_safe/from_json_safe` | `src/types/market.py` | DEFERRED. JSON serialization for `Bin` with `low=None` / `high=None` currently relies on caller-side `None` handling. Canonical (de)serialization helpers would remove that burden. |
| **#43** | I — `validate_bin_topology` helper | `src/types/market.py` or new contract | DEFERRED. Runtime assertion that a `list[Bin]` forms a valid partition (non-overlapping, complete coverage) — currently enforced only by `CanonicalBinGrid` (`df13308`). A standalone helper lets market-side code self-check too. |
| **#45** | B — `load_cities` metadata validation | `src/config.py:load_cities` | DEFERRED. Validate that every city in `config/cities.json` has the required `cluster` / `wu_station` / `settlement_unit` / `timezone` / `lat` / `lon`. Currently partial. |
| **#51** | Test file prune — remove R2, R3.3, R4.2, R5.2, R6 from legacy relationship tests | `tests/test_*_relationships.py` | DEFERRED. These are obsolete tests from earlier iterations of the calibration contract, superseded by the K2 24-test relationship suite (`test_k2_live_ingestion_relationships.py`). Confirm supersession then prune. |

---

## 5. Operational / security

| ID | Task | Action |
|----|------|--------|
| **#62** | WU_API_KEY rotation (security) | Transition key `e1f10a1e78da46f5b10a1e78da96f525` is still exported inline by `scripts/post_sequential_fillback.sh` and `scripts/resume_backfills_sequential.sh`. **Operator action:** (a) rotate at weather.com, (b) set new key in operator env (`~/.zshrc` / launchd plist), (c) delete the two inline `export WU_API_KEY=...` blocks and re-commit the scripts. Currently blocks clean fresh-clone deployment. |
| **#64** | K2 Phase C cleanup (deferred reviewer findings) | `source_applies_to_city → cities.json` config; `_log_availability_failure` fd leak; `forecasts.rebuild_run_id → ForecastRow`; shared Open-Meteo client extraction; **post-ingestion relationship tests for Layer 3 replacement** (Layer 3 deletion at `ff287ad` left a relationship gap — `test_ingestion_guard.py` no longer tests seasonal implausibility; the replacement should be a post-ingest anomaly detector, not a pre-ingest gate). |

---

## 6. Open questions requiring user decision

| ID | Question | Options |
|----|----------|---------|
| **#46** | ETL script fate for `scripts/etl_tigge_calibration.py` and `scripts/etl_tigge_ens.py` | (a) retain for TIGGE direct calibration path, (b) supersede entirely by `ingest_grib_to_snapshots.py` (#53), (c) mark `stale_deprecated` in `script_manifest.yaml`. Need user call once #52/#53 land. |
| **#47** | Eps value (see §3 above) | `0.01` (current code, conservative) vs `1e-6` (math spec, tight) |
| **#56** | Plan patch v2.2 → v2.3: physical-data-only scope narrowing | Whether to ship a trimmed v2.3 plan that explicitly excludes market-events recovery (aligned with 2026-04-14 refactor decision) |

---

## 7. Completed in this session (for session-local orientation only — not backlog items)

- `#54` Change C — `IngestionGuard` unit + hemisphere rigor
- `#58` `backfill_hko_daily.py` created + running
- `#59` K2 live-ingestion packet (4 append modules + hole scanner + data_coverage ledger)
- `#60` Layer 3 seasonal envelope deletion (`ff287ad`)
- `#65` `src/contracts/calibration_bins.py` — canonical bin grid contract
- `#66` Extract `p_raw_vector_from_maxes` free function
- `#67` Add `bin_source` column to `calibration_pairs`
- `#68` `scripts/rebuild_calibration_pairs_canonical.py`
- `#69` Tests R1-R13 in `tests/test_calibration_bins_canonical.py` (28/28 green)
- `#70` Unit tests + dry-run smoke + commit (`df13308`)

---

## Data-loss event registry (per CLAUDE.md `DATA_REBUILD_LIVE_MATH_CERTIFICATION_BLOCKED`)

**2026-04-12 / #57 — Rainstorm migration data loss.** `rainstorm.db` no longer exists at its canonical path. Tables lost:

- `market_events` (0 rows in zeus-world.db)
- `token_price_log` (0)
- `market_price_history` (0)
- `chronicle` (0)
- `outcome_fact` (0)
- `opportunity_fact` (0)
- `historical_forecasts` (0)

Tables preserved (Zeus wrote them directly, not via Rainstorm migration):

- `observations` (33k+ rows, rebuilt by backfill)
- `ensemble_snapshots` members_json (40,350 rows, partial TIGGE — to be overwritten)
- `solar_daily`, `forecasts`

**Recovery decision (2026-04-14, user directive):** `rainstorm.db` is rejected as recovery source because it never passed audit. The calibration path was refactored (commit `df13308`) to eliminate the `market_events` dependency entirely. `market_price_history`, `chronicle`, `outcome_fact`, `opportunity_fact` remain empty and are NOT on the critical path for live math — live scanning + K2 live-ingestion will populate them going forward.

---

## References

- Prior session plans: `docs/operations/data_rebuild_plan.md`, `docs/operations/current_state.md`
- Calibration refactor plan: `~/.claude/plans/logical-chasing-ritchie.md`
- Active topology law: `AGENTS.md`, `architecture/topology.yaml`, `architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`
- Commit trail (this session, data-improve branch):
  - `df13308` feat(calibration): canonical-bin Platt training path (code only, no retraining)
  - `45745ba` docs(topology): register calibration refactor in machine manifests
  - `854cf5d` feat(topology): advisory/precommit/closeout modes + close registration gaps
  - `ed13310` ops(K2): sequential backfill restart with --all --missing-only + WU_API_KEY guard
