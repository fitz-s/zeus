# RUN 16 — Track E: Forecast Pipeline Freshness End-to-End Audit

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `724bed64da`
**Worktree**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill`
**Run date**: 2026-05-18T00:03Z
**Probe target**: Karachi 2026-05-17 day0_window position `c30f28a5-d4e` (only active day0 KAR position)
**Method**: READ-ONLY. SQLite3 URI ro-mode probes (`file:{db}?mode=ro&timeout=60`) + ripgrep code-pattern audit. No state or production code mutated.

---

## §0 TL;DR — the central freshness gap

**Producers** stamp `available_at` / `fetch_time` / `recorded_at` / `retrieved_at` / `imported_at` on every row. **Readers** never test those timestamps against a max-age threshold. The pattern across `executable_forecast_reader.py`, `ecmwf_open_data.py`, `tigge_pipeline.py`, `market_fusion`, and `monitor_refresh.probability_refresh` is identical:

```sql
SELECT * FROM <table>
WHERE city=? AND target_date=? AND <provenance keys>
ORDER BY <issue/cycle/available> DESC
LIMIT 1
```

No `WHERE available_at >= ?`. No Python-side `assert age_hours < THRESHOLD`. The reader returns the latest row that exists — even if it is 16 hours old, 2.6 days old, or 8 days old. The only truth-attestation in the live pipeline is a producer-returned `is_fresh: bool` (`monitor_refresh:1358` `prob_refresh_is_fresh`) whose construction does not include any time-window check — it reflects "computation succeeded with non-NaN inputs", not "input ts is recent".

For the only Karachi 5/17 position in day0_window, the actual freshness at NOW=2026-05-18T00:03Z is:

| Stage | Latest write | Age | Threshold (if any) | Verdict |
|---|---|---|---|---|
| WU obs (WORLD) | utc_timestamp=2026-05-17T18:00Z, imported 23:47Z | obs=6.05h, ingest=16min | Only Day0 obs has `DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS=1.0h` (`evaluator.py:146`); applies to executable_market_snapshots, not raw obs | **CHECKED at evaluator** ✓ |
| ENS_v2 (FC) KAR HIGH | fetch_time=2026-05-17T07:34Z | **16.5h** | NONE | **NO-FRESHNESS-CHECK** ✗ |
| ENS_v2 (FC) KAR LOW | fetch_time=2026-05-17T07:39Z | **16.4h** | NONE | **NO-FRESHNESS-CHECK** ✗ |
| historical_forecasts_v2 KAR 5/17 | **0 rows** (both DBs) | ∞ | NONE | **EMPTY**, no failure path |
| forecasts (legacy 5-source) KAR 5/17 | retrieved 2026-05-10T18:03Z | **8.0 days** | NONE | **NO-FRESHNESS-CHECK** ✗ |
| market_events_v2 (FC) KAR 5/17 | recorded 2026-05-15T08:36Z | **2.6 days** | NONE | **NO-FRESHNESS-CHECK** ✗ |
| market_price_history (WORLD JOIN m_events_v2 WORLD) | **0 rows** | ∞ | NONE | **EMPTY** (cross-DB asymmetry; events live in FC, price tries to join WORLD's empty events table) |
| probability_trace_fact KAR 5/17 | 0 rows | ∞ | NONE | **NEVER WRITTEN** for an actively-traded position |
| trade_decisions (any city) 5/17 | 0 rows | ∞ | NONE | **NEVER WRITTEN** today |
| decision_log (any) 5/17 | 0 rows | ∞ | NONE | **NEVER WRITTEN** today |
| position_current KAR 5/17 in WORLD | 0 rows | ∞ | — | Lives in trades.db, not WORLD; expected |
| position_lots (ALL CITIES, ALL TIME) | **0 rows** | ∞ | — | **F114 NEW: position_lots table empty across the entire system** |
| venue_commands `c30f28a5%` | 1 row, EXPIRED ENTRY, last update 2026-05-16T17:47Z | **30.3h** | NONE | sole order on Karachi day0 position is a 30-hour-old EXPIRED entry, never replaced |
| oracle_error_rates.json | **MISSING** | ∞ | — | **F117 NEW**: oracle bridge artifact absent at expected path |
| forecast_live_daemon.heartbeat.json | **MISSING** | ∞ | — | F100 echo: writer present in code, file never created |
| live-trading.heartbeat.json | **MISSING** | ∞ | — | F100 echo |
| riskguard.heartbeat.json | **MISSING** | ∞ | — | F100 echo |

**Composite verdict**: a Karachi 5/17 monitor cycle in this state will:
1. Read 16.5h-old ENS_v2 → recompute `p_posterior` against today's market price → silently emit `prob_refresh_is_fresh=True` despite stale input.
2. Read 8-day-old legacy `forecasts` if any downstream code uses them → no warning.
3. Skip oracle penalty entirely (file missing → `_artifact_age_hours()` returns None → STALE classification deferred per `oracle_penalty.py:300`).
4. Have no `probability_trace_fact` rows for forensic reconstruction.
5. Have no fresh exit order; 30-hour EXPIRED entry sitting in `venue_commands` is the last execution-side signal.

This composite — fresh-looking timestamps from stale inputs, empty trace tables, missing oracle bridge — is the system the user has been seeing from the outside as "Karachi position frozen with no clear diagnostic".

---

## §1 Stage-by-stage pipeline map (writer ↔ reader ↔ threshold)

| # | Stage / table | Writer | Cadence | Reader(s) | Reader freshness check | Last write | Tolerance threshold (code-defined) |
|---|---|---|---|---|---|---|---|
| S1a | `observation_instants_v2` (WORLD) — WU/Tigge raw obs | `observation_instants_v2_writer` via `ingest_main.daily_tick` + `wu_hourly_client` | hourly | `evaluator._evaluate_executable_market_snapshot` (Day0 obs path) | **YES** — `DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS=1.0h` at `evaluator.py:146,698` | KAR 2026-05-17T23:47Z | 1.0 h |
| S1b | `observation_instants_v2` (FC) | (per `cycle_runner` ATTACH `forecasts`) — **never written for Karachi** | — | (would-be) cycle reader if it tried FC bare-name | NO | **0 rows** | — |
| S1c | `observation_instants_current` (WORLD, view-like) | `observation_instants_v2_writer` | hourly | unknown — schema-only check; no rg hits in src/engine, src/strategy | NO | KAR max=2026-05-17T18:00Z (39367 rows) | — |
| S2a | `ensemble_snapshots_v2` (FC) — ECMWF/Tigge ENS | `ecmwf_open_data.ingest`, `tigge_pipeline`, `tigge_db_fetcher` | per-issue (00/06/12/18 UTC) | `executable_forecast_reader._ensemble_snapshot_query` (4 variants at L377/395/451/469); `ecmwf_open_data._snapshot_rows_for_source_run` L580-600 | **NO** — `ORDER BY source_cycle_time DESC, available_at DESC LIMIT 1`, no `available_at >= ?` | KAR 5/17: HIGH fetch 2026-05-17T07:34Z, LOW 07:39Z (**16.5h ago**) | — |
| S2b | `ensemble_snapshots_v2` (WORLD) — same schema | none (writer was migrated to FC) | — | `executable_forecast_reader` `world.ensemble_snapshots_v2` branch (L411+, L444+) | NO | **0 rows for KAR 5/17** | — |
| S3a | `historical_forecasts_v2` (FC + WORLD) — vendor skill bands | `forecasts_append`, `tigge_db_fetcher` | per ingest tick | `forecast_skill` builders | NO `available_at >=` filter found | **0 rows** for KAR 5/17 in either DB | — |
| S3b | `forecasts` (WORLD, legacy 5-source: ecmwf_/gfs_/icon_/openmeteo_/ukmo_previous_runs) | legacy `forecasts_append` daily | daily | `ingest_main:381` `SELECT MAX(captured_at) FROM forecasts` boot staleness probe (writer-side, not consumer-side); no signal consumer | **only at boot, only by writer**; no live consumer | KAR 5/17: retrieved/imported 2026-05-10T18:03Z (**8.0 days**) | `_BOOT_FRESHNESS_THRESHOLD_HOURS` at boot only |
| S3c | `forecast_skill` (WORLD) — actual-vs-forecast residual | `tools/calibration/*` (offline) | as-needed | calibration consumers (offline); `main.py:1241` `COUNT(DISTINCT city)` (city-coverage probe only) | NO | **0 rows for KAR 5/17** | — |
| S4a | `market_events_v2` (FC) | `ingest_main._market_events_v2_refresh` L982 | every 5 min | `executable_forecast_reader` + `executor` join path | NO | KAR 5/17: 11 rows, last recorded 2026-05-15T08:36Z (**2.6 days**) | — |
| S4b | `market_events_v2` (WORLD) | none (sibling-DB asymmetry; F46-family echo) | — | (would-be) joiners | NO | **0 rows** for KAR 5/17 | — |
| S5 | `market_price_history` (WORLD) — GAMMA_SCANNER quotes | `gamma_scanner` ingest path | per scan tick (~minute) | `monitor_quote_refresh` (CLOB-direct) + observability/price_evidence_report | NO `WHERE recorded_at >=` filter found in 30 reader hits | **0 rows JOIN m_events_v2 WORLD** for KAR 5/17; **MAX(recorded_at) over entire table = NULL** (table is unused or never written?) | — |
| S6 | `executable_market_snapshots` (WORLD) | `monitor_quote_refresh` writes via builder | per cycle | `evaluator._evaluate_executable_market_snapshot` | YES (S1a chain) | — | 1.0h via DAY0 path |
| S7 | `probability_trace_fact` (WORLD) — posterior trace fact | `monitor_refresh.refresh_position_probability` (when present) | per cycle | offline forensic readers | NA (write-side missing) | **0 rows for KAR 5/17** | — |
| S8 | `decision_log` (WORLD) — decision/refresh artifact JSON | `cycle_runtime._persist_decision_artifact` | per cycle | offline | NA | **0 rows since 2026-05-17T00:00Z** | — |
| S9 | `trade_decisions` (WORLD) — kelly sizing record | `executor` after sizing | per entry | offline | NA | **0 rows since 2026-05-17T00:00Z** | — |
| S10 | `position_current` (TRADES) | `cycle_runtime` post-monitor | per cycle | next cycle + UI | NA | KAR 5/17: 1 row updated_at 2026-05-17T23:58Z (16 min ago) | — |
| S11 | `position_lots` (WORLD) — fill-side ledger | `chain_reconciliation` | per fill | reconciliation + reporting | NA | **0 rows in entire table** | — |
| S12 | `venue_commands` (TRADES) | `executor.send_*` | per intent | `command_recovery`, `exit_lifecycle` | NA | c30f28a5: 1 row, EXPIRED ENTRY, updated 2026-05-16T17:47Z (**30.3h ago**); MAX(created_at) overall=2026-05-17T22:23Z (1.7h ago — for non-KAR) | — |
| S13 | `oracle_error_rates.json` (state/) — calibration bridge | `tools/oracle/*` (offline) | weekly? | `oracle_penalty._artifact_age_hours` + `_load_oracle_info` (`oracle_penalty.py:446,504`) | **YES** — `> 7 days → STALE` mult 0.7 (`oracle_penalty.py:27, 300, 314`) | **FILE MISSING** | 7 days |
| S14 | `forecast_live_daemon.heartbeat.json` | `forecast_live_daemon` (if PID handler installed) | per loop | `heartbeat_supervisor` consumer (F91 NO-WIRE per Run #15 T3) | YES on consumer side (`DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS=8s`) but no wire | **FILE MISSING** | 8 s |
| S15 | `daemon-heartbeat-ingest.json` | ingest daemon | per loop | (none functional — F91) | NA | 2026-05-18T00:03Z (0s) | — |
| S16 | `venue-heartbeat-keeper.json` | venue keeper | per loop | (none functional — F91) | NA | 2026-05-18T00:03Z (4s) | — |

---

## §2 NO-FRESHNESS-CHECK reader inventory

The following readers ingest writer-stamped timestamps but never filter / threshold against them. Verified by ripgrep on `src/`:

```
$ rg -t py 'WHERE.*(available_at|fetch_time|recorded_at|retrieved_at|imported_at|utc_timestamp)\s*>=' src/
(zero matches)
```

| Reader file:line | Table read | Time-column written by producer | Threshold? |
|---|---|---|---|
| `src/data/executable_forecast_reader.py:377,395,411,444,451,469` | `ensemble_snapshots_v2` (×6 query variants) | `available_at`, `fetch_time`, `source_cycle_time` | **NO** |
| `src/data/ecmwf_open_data.py:600,1687` | `ensemble_snapshots_v2` | same | **NO** |
| `src/data/tigge_pipeline.py:161` | `ensemble_snapshots_v2` | `issue_time` (uses MAX(DATE(issue_time)) for catch-up logic, not freshness gate) | **NO** |
| `src/strategy/market_fusion.py:91-134` | (no direct SQL — consumes `freshness_status` literal `"FRESH"` / `"UNKNOWN"` set upstream, but no setter assigns it — confirmed by `rg 'freshness_status\s*='` returns 0 hits outside dataclass + validator) | — | **NEVER ASSIGNED** — defaults to `"UNKNOWN"` permanently |
| `src/observability/price_evidence_report.py:314,320,330` | `market_price_history` | `recorded_at` | NO (count queries only) |
| `src/main.py:1241` | `forecast_skill` | `available_at` | NO (DISTINCT city count probe) |
| `src/ingest_main.py:381,386,404-454` | `forecasts`, `solar_daily` | `captured_at`, `fetched_at` | **YES at boot only** (`_BOOT_FRESHNESS_THRESHOLD_HOURS`) — not at every read |
| `src/engine/monitor_refresh.py:1358` | (delegates to `monitor_probability_refresh`) | — | **YES (binary)** — `prob_refresh_is_fresh: bool` but construction never compares to wall clock |
| `src/engine/evaluator.py:146,698` | `executable_market_snapshots` Day0 obs | `observation_time` | **YES** — only authentic max-age check in the entire data-read path: `DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS = 1.0h` |
| `src/strategy/oracle_penalty.py:300,314,446` | `state/oracle_error_rates.json` mtime | filesystem mtime | **YES** — `> 7 days → STALE mult 0.7` |
| `src/runtime/bankroll_provider.py:40` | (cache TTL) | — | YES — `_DEFAULT_MAX_AGE_SECONDS=30.0s` |
| `src/control/ws_gap_guard.py:53,64` | WS message arrival | wall clock | YES — `stale_after_seconds` |
| `src/control/heartbeat_supervisor.py:28,29` | heartbeat envelope mtime | wall clock | YES — `8s` / `30s` (but consumer NO-WIRE per F91) |

**Aggregate**: out of 10 data-stage tables in the forecast pipeline (S1a-S13), exactly **2** have a code-enforced freshness threshold consulted at every read (Day0 obs at evaluator + oracle file mtime), **1** has a boot-only threshold (forecasts at daemon startup), and **7** have no freshness gate of any kind.

---

## §3 Timezone audit hints

The only consistent timezone convention found in the data path is **WU obs → `Asia/Karachi` localisation correct**:

```
('2026-05-17T18:00:00+00:00', '2026-05-17T23:00:00+05:00', 'Asia/Karachi', 'utc_hour_bucket_extremum', 'wu_icao_history')
('2026-05-17T17:00:00+00:00', '2026-05-17T22:00:00+05:00', 'Asia/Karachi', 'utc_hour_bucket_extremum', 'wu_icao_history')
```

UTC offset is +05:00 (PKT) consistently. `time_basis='utc_hour_bucket_extremum'` is set, and `target_date='2026-05-17'` matches the local civil date the Polymarket question resolves on. **No DST hazard for Karachi (PKT has no DST)** — the London 2025-03-30 spring-forward case-study failure mode does not apply to this position.

However, the broader convention drift is unverified at this read: search for `tz_localize|ZoneInfo|datetime.utcnow|datetime.now\(` shows mixed `utcnow()` and `now()` callsites scattered across `src/data/`, `src/engine/`, `src/strategy/`. A targeted timezone audit (Run #17 candidate) is required to prove the mix is consistent under DST cities (London, Berlin, Madrid).

---

## §4 Karachi `c30f28a5-d4e` 5/17 freshness trace (single-position)

```
Position : c30f28a5-d4e... (Karachi 2026-05-17 day0_window opening_inertia)
NOW_UTC  : 2026-05-18T00:03:23Z
position_current.updated_at: 2026-05-17T23:58:41Z (16 min ago) ✓

Stage age (sec) at NOW:
  S1a WU obs imported_at        :     16 min ago  ✓ ingest fresh
  S1a WU obs utc_timestamp       :    363 min ago  ✓ within 6h Pakistan-evening WU lag
  S1b FC obs_instants_v2         :           ∞ (0 rows for KAR — cross-DB asymmetry)
  S2a ENS_v2 FC HIGH fetch_time  :    989 min ago  ✗ 16.5h ENS staleness, NO ALARM
  S2a ENS_v2 FC LOW  fetch_time  :    984 min ago  ✗ 16.4h ENS staleness, NO ALARM
  S2a ENS_v2 valid_time          :   2163 min ago  ✗ valid_time=2026-05-16T12:00Z (36h-stale forecast horizon)
  S2b ENS_v2 WORLD               :           ∞ (0 rows for KAR — F46-family writer asymmetry)
  S3a historical_forecasts_v2    :           ∞ (0 rows for KAR — table effectively unused)
  S3b legacy forecasts (5 src)   :  11542 min ago  ✗ 8 days stale, last imported 2026-05-10
  S3c forecast_skill             :           ∞ (0 rows for KAR 5/17)
  S4a market_events_v2 FC        :   3927 min ago  ✗ 2.6 days, no market refresh for KAR
  S5 market_price_history        : NULL MAX(recorded_at) over entire table — never written or table dead
  S7 probability_trace_fact      :           ∞ (0 rows for KAR 5/17)
  S8 decision_log                :           ∞ (0 rows today)
  S9 trade_decisions             :           ∞ (0 rows today)
  S11 position_lots              :           ∞ (0 rows globally — empty table)
  S12 venue_commands c30f28a5    :   1816 min ago  ✗ 30h since EXPIRED entry order, never replaced
  S13 oracle_error_rates.json   :           ∞ (MISSING)
  S14 forecast_live HB           :           ∞ (MISSING — matches F100/F86)
  S15 ingest HB                  :      0 sec ago  ✓
  S16 venue HB                   :      4 sec ago  ✓
```

The position appears "alive" to the supervisor (heartbeats fresh, position_current updated 16 min ago) but **every authoritative data input is stale and no reader flags it**.

---

## §5 New findings (proposed F111–F119)

> **F-number context**: max F# in committed `FINDINGS_REFERENCE_v2.md` is **F105** (Run #16 Track D). Uncommitted sibling work (Tracks A/B/F) reuses F105 and adds F106–F110. To avoid further collision I start at **F111**. The original task said "F105+"; F105 was already consumed before this run began.

| F# | Title | Sev | Status | Owner (path) | First seen | Last verified |
|---|---|---|---|---|---|---|
| **F111** | **Universal no-freshness-check on `ensemble_snapshots_v2` reads**: all 6 query variants in `executable_forecast_reader.py` use `ORDER BY source_cycle_time DESC LIMIT 1` with no `WHERE available_at >= ?` time-window filter. ECMWF/Tigge ingest can stall ≥16h (Karachi 5/17 case: HIGH fetch_time=2026-05-17T07:34Z, age 16.5h at NOW=2026-05-18T00:03Z) and downstream `monitor_probability_refresh` will silently produce `prob_refresh_is_fresh=True` against stale forecast. | **SEV-1 HOT** | NEW (Run #16 T E) | `src/data/executable_forecast_reader.py:377,395,411,444,451,469` + `src/data/ecmwf_open_data.py:600,1687` | Run #16 T E | Run #16 T E |
| **F112** | **`MarketPriorDistribution.freshness_status` is never assigned**: dataclass at `src/strategy/market_fusion.py:91` declares `Literal["FRESH","UNKNOWN"]` and validator enforces the literal, but `rg 'freshness_status\s*='` returns ZERO writer hits in `src/` outside the dataclass default. Field is dead code / permanent `"UNKNOWN"`. | SEV-2 | NEW (Run #16 T E) | `src/strategy/market_fusion.py:91-134` | Run #16 T E | Run #16 T E |
| **F113** | **`market_price_history` may be unused or never written**: `SELECT MAX(recorded_at) FROM market_price_history` returns NULL over the entire 38GB WORLD DB. Karachi 5/17 join against `market_events_v2 WORLD` returns 0 rows (because `market_events_v2` itself is 0-row in WORLD — F46-family asymmetry; events live only in FC). The 3 hits in `price_evidence_report.py:314,320,330` are observability counts only; no live consumer. | SEV-2 | NEW (Run #16 T E) | `src/observability/price_evidence_report.py` + writer (unknown — gamma_scanner path) | Run #16 T E | Run #16 T E |
| **F114** | **`position_lots` table is globally empty** (0 rows over entire WORLD DB). Lot-level fill ledger receives zero writes from `chain_reconciliation` despite 16 active + 5 economically_closed positions per Run #16 Track F. Lot↔position invariant cannot be checked because both sides are absent. | **SEV-1 HOT** | NEW (Run #16 T E) | `src/state/position_lots.py` + `src/state/chain_reconciliation.py` (writer path) | Run #16 T E | Run #16 T E |
| **F115** | **`historical_forecasts_v2` cross-DB write asymmetry**: 0 rows for KAR 5/17 in BOTH WORLD and FC despite being declared the canonical "vendor skill band" surface. `forecast_skill` table also 0 rows for KAR 5/17. Either the writer is broken or the table is superseded by `ensemble_snapshots_v2`; no inline doc states which. Reader code references it under both `forecasts.historical_forecasts_v2` and bare-name → if reader ever fires it silently returns empty without error. | SEV-2 | NEW (Run #16 T E) | `src/data/forecasts_append.py` + `src/data/tigge_db_fetcher.py` (writer); reader scan needed | Run #16 T E | Run #16 T E |
| **F116** | **Legacy `forecasts` table 8 days stale for Karachi 5/17** (last `retrieved_at`/`imported_at`=2026-05-10T18:03Z across 5 sources: `ecmwf_/gfs_/icon_/openmeteo_/ukmo_previous_runs`). Boot-only `_BOOT_FRESHNESS_THRESHOLD_HOURS` probe in `ingest_main.py:381,404-454` does fire at daemon start but the per-cycle reader path has no equivalent gate; if a consumer reads this table during a live cycle, 8-day-old forecasts are returned without warning. | SEV-2 | NEW (Run #16 T E) | `src/ingest_main.py:404-454` (boot-only); no live-cycle gate | Run #16 T E | Run #16 T E |
| **F117** | **`state/oracle_error_rates.json` missing**: `oracle_penalty.py:300,314,446` consults file mtime against `> 7 days → STALE mult 0.7` block; with file absent, `_artifact_age_hours()` returns `None`, `_estimator_classify` falls into `artifact_age_hours=None` branch, and the block_reason never fires. No oracle penalty applied for ANY position in current state. | **SEV-1 HOT** | NEW (Run #16 T E) | `src/strategy/oracle_penalty.py:446,504` (reader) + offline `tools/oracle/*` (writer) | Run #16 T E | Run #16 T E |
| **F118** | **`probability_trace_fact` never written for Karachi 5/17** despite position being in day0_window for 1+ days. Forensic reconstruction of why `p_posterior` settled at its current value is impossible from the live DB. Combines with F114 (no position_lots) and 0 `decision_log`/`trade_decisions` rows today to produce a position with no audit trail at all. | SEV-2 | NEW (Run #16 T E) | `src/engine/cycle_runtime.py` (trace-fact writer path) | Run #16 T E | Run #16 T E |
| **F119** | **Cross-DB asymmetry inventory** (consolidation of S1b, S2b, S4b): `observation_instants_v2`, `ensemble_snapshots_v2`, `market_events_v2` each have rows in only ONE of {WORLD, FC}. `cycle_runner.py:86-91` ATTACHes forecasts AS `forecasts`; reads that omit the schema prefix bind to MAIN trades.db → 0 rows. Same family as F48/F103 but for 3 forecast/market tables, not just `settlements_v2`. | **SEV-1 HOT** | NEW (Run #16 T E) | `src/engine/cycle_runner.py:86-91` + downstream readers | Run #16 T E | Run #16 T E |

---

## §6 APPEND-BLOCK for FINDINGS_REFERENCE_v2.md (NOT applied; sibling-uncommitted contamination protection)

> **Why not applied**: `git status` showed `AUDIT_HISTORY.md`, `CONSOLIDATED_FINDINGS_DOSSIER.md`, `FINDINGS_REFERENCE_v2.md` already have uncommitted sibling-agent edits (Tracks A/B/F adding F105-F110 and a Track B Run-row). Per Fitz cross-contamination antibody (incident #3, 2026-04-17 zeus), I must not `git add` a file whose diff is not entirely mine. The append-block below is provided verbatim; the owning agent for the contested files can paste it after their own commit lands.

Paste after the Track F additions block in `FINDINGS_REFERENCE_v2.md`:

````markdown
## Run #16 Track E additions (F111–F119) — forecast pipeline freshness e2e

Track E traced every stage from raw obs ingest → ENS_v2 → market_events_v2 → market_price_history → monitor_refresh → executor for Karachi `c30f28a5-d4e` 2026-05-17. Found 7-of-10 data-stage tables have NO code-enforced max-age check at read; the only authentic freshness gates are `DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS=1.0h` (`evaluator.py:146`) and `oracle_error_rates.json` mtime > 7 days. Karachi 5/17 measured: ENS 16.5h stale, legacy forecasts 8 days stale, market_events 2.6 days stale, oracle file MISSING, `position_lots` table globally empty, `market_price_history` table never written, `probability_trace_fact` never written for the position. READ-ONLY; no production code or schema mutated.

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F111** | Universal no-freshness-check on `ensemble_snapshots_v2` reads — all 6 query variants `ORDER BY ... DESC LIMIT 1` without `WHERE available_at >= ?`; KAR 5/17 ENS 16.5h stale, `prob_refresh_is_fresh=True` silently | **SEV-1 HOT** | NEW (Run #16 T E) | `src/data/executable_forecast_reader.py:377,395,411,444,451,469` | Run #16 T E | Run #16 T E |
| **F112** | `MarketPriorDistribution.freshness_status` never assigned anywhere in src/ — permanent default `"UNKNOWN"`, dead semantic field | SEV-2 | NEW (Run #16 T E) | `src/strategy/market_fusion.py:91-134` | Run #16 T E | Run #16 T E |
| **F113** | `market_price_history` `MAX(recorded_at)` is NULL over entire 38GB WORLD DB — table unused or never written; KAR 5/17 join returns 0 rows (compound with F119 events asymmetry) | SEV-2 | NEW (Run #16 T E) | gamma_scanner writer path + `src/observability/price_evidence_report.py` | Run #16 T E | Run #16 T E |
| **F114** | `position_lots` table globally empty — 0 rows over entire WORLD DB despite 16 active + 5 economically_closed positions; lot ledger writer wired but receives no writes | **SEV-1 HOT** | NEW (Run #16 T E) | `src/state/position_lots.py` + `src/state/chain_reconciliation.py` | Run #16 T E | Run #16 T E |
| **F115** | `historical_forecasts_v2` cross-DB write asymmetry — 0 rows in WORLD AND FC for KAR 5/17; superseded by `ensemble_snapshots_v2`? No inline doc clarifies | SEV-2 | NEW (Run #16 T E) | `src/data/forecasts_append.py`, `src/data/tigge_db_fetcher.py` | Run #16 T E | Run #16 T E |
| **F116** | Legacy `forecasts` table 8 days stale for KAR 5/17; `_BOOT_FRESHNESS_THRESHOLD_HOURS` fires only at daemon start, no live-cycle gate | SEV-2 | NEW (Run #16 T E) | `src/ingest_main.py:404-454` | Run #16 T E | Run #16 T E |
| **F117** | `state/oracle_error_rates.json` MISSING — `_artifact_age_hours()` returns None → no oracle penalty applied for ANY position systemwide | **SEV-1 HOT** | NEW (Run #16 T E) | `src/strategy/oracle_penalty.py:446,504` + offline writer | Run #16 T E | Run #16 T E |
| **F118** | `probability_trace_fact` never written for KAR 5/17 day0_window position; no forensic audit trail for live posterior decisions | SEV-2 | NEW (Run #16 T E) | `src/engine/cycle_runtime.py` (trace-fact writer) | Run #16 T E | Run #16 T E |
| **F119** | Cross-DB asymmetry trinity — `observation_instants_v2`, `ensemble_snapshots_v2`, `market_events_v2` each rows in only one of {WORLD, FC}; same family as F48/F103 but spanning forecast/market surfaces, not just settlements | **SEV-1 HOT** | NEW (Run #16 T E) | `src/engine/cycle_runner.py:86-91` + downstream readers | Run #16 T E | Run #16 T E |

> See `RUN_16_track_E_forecast_pipeline_freshness_e2e.md` §1 (stage map), §2 (no-freshness-check inventory), §4 (Karachi 5/17 trace), §5 (F111–F119 with fix specs).
````

---

## §7 APPEND-BLOCK for AUDIT_HISTORY.md (NOT applied; same reason as §6)

````markdown
| Run #16 T E | 2026-05-18 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ pending-commit | Track E: end-to-end forecast pipeline freshness audit | **+9** (F111–F119; 3× SEV-1 HOT, 5× SEV-2, 1× SEV-2) | 7-of-10 data-stage tables have NO max-age gate at read; only authentic gates are DAY0 obs `1.0h` (evaluator.py:146) and oracle file mtime `7d` (oracle_penalty.py:300). Karachi 5/17 case: ENS 16.5h stale, legacy forecasts 8d stale, market_events 2.6d stale, oracle file MISSING, position_lots empty, market_price_history dead, probability_trace_fact unwritten, venue_commands 30h-old EXPIRED entry — system "looks alive" via heartbeats but every data input is stale and no reader flags it. F119 generalizes F48/F103 cross-DB asymmetry to 3 forecast/market tables. READ-ONLY; no production code mutated. |
````

---

## §8 APPEND-BLOCK for CONSOLIDATED_FINDINGS_DOSSIER.md (NOT applied; same reason)

Suggested new top-level section to add at end:

````markdown
## Run #16 Track E — forecast pipeline freshness end-to-end (F111–F119)

**Net delta**: +9 findings (3× SEV-1 HOT: F111 ENS no-freshness-check, F114 position_lots empty, F117 oracle file missing, F119 cross-DB asymmetry trinity; 5× SEV-2). Run E's central structural finding is that 7-of-10 data-stage reader paths in the forecast pipeline have zero code-enforced max-age gate; producers stamp `available_at`/`fetch_time`/`recorded_at` honestly but readers never compare to wall clock. Composed with F119 (3 tables each live in only one of {WORLD, FC}), the system can present "fresh-looking" position state (position_current updated 16 min ago, heartbeats green) while every authoritative input — ENS, market events, oracle, lot ledger, trace fact — is stale or absent. Karachi `c30f28a5-d4e` 5/17 is the live exemplar: 30-hour EXPIRED entry, no exit order, no audit trail, no oracle penalty.

Recommended next-run focus: (a) Track G — write spec for `WHERE available_at >= ?` filter retrofit on the 6 `ensemble_snapshots_v2` query variants + per-table `MAX_STALENESS_HOURS` constants colocated with reader; (b) Track H — oracle_error_rates.json writer audit and missing-file alarm; (c) Track I — position_lots writer wiring trace from `chain_reconciliation` (compound with Track F's F108/F110 position-lifecycle gaps).

See `RUN_16_track_E_forecast_pipeline_freshness_e2e.md` for full evidence.
````

---

## §9 Operational notes

- **Sync state**: HEAD `724bed64da` matches `origin/fix/wave-2-lineage-and-k1-cleanup-2026-05-17` exactly (fast-forward-only, no divergence).
- **Read pattern used** (all probes): `sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=60)`. Probe script at `/tmp/run16_probe.py`, output at `/tmp/run16_results.txt`. Per the probe-output antibody, all critical results were written to /tmp file and read back via `read_file` rather than trusting terminal stdout (the desync incident from earlier this session corrupted the first attempt).
- **Commit scope**: this run commits ONLY this new file. The 3 contested files (`AUDIT_HISTORY.md`, `CONSOLIDATED_FINDINGS_DOSSIER.md`, `FINDINGS_REFERENCE_v2.md`) already carry Track A/B/F uncommitted sibling deltas; staging them now would cross-contaminate per Fitz antibody #3. Sibling agents who own those tracks should commit their work, then a follow-up run can apply §6/§7/§8 append-blocks cleanly.
- **F-number collision warning**: Track D committed F105 (model-allowlist drift). Track F uncommitted reuses F105 (EXIT_ORDER_REJECTED phase_before). Future maintainer should renumber Track F's F105–F110 before merging to main; my F111–F119 are positioned to skip the contested range entirely.

---

*Run #16 Track E complete. READ-ONLY. No production code, schema, or state mutated. 0 changes to `src/`, `state/`, or any LaunchAgent plist.*
