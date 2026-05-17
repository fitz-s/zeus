# Run #8 — Resolution sweep for all open audit items

Date: 2026-05-17
Audit subject: main HEAD `9259df3e9c` post-PR-126/130/132/133
Worktree: `worktrees/zeus-deep-alignment-audit-skill`

## Numbering convention used in this document

**Cards below are headed by v1.F# when sourced from the FROZEN v1 brief** (`task_2026-05-16_deep_alignment_audit/`), **and by v2.F# when sourced from the current v2 master index** (`FINDINGS_REFERENCE_v2.md` in this same package). Each card states which scheme it uses. The two schemes are not aligned — see F28 in `RUN_8_findings.md` for the meta-defect, and the cross-walk table immediately below.

### v1 ↔ v2 cross-walk (cards in this Run #8 sweep)

| Card header in this doc | Same defect in v2.F# | Same defect in v1.F# | Notes                                     |
|-------------------------|----------------------|----------------------|-------------------------------------------|
| F1 boot wiring          | (not in v2)          | v1.F1                | v2.F1 is a different defect (FIXED)       |
| F2 selection NULL       | v2.F2                | v1.F2                | Aligned                                   |
| F3 docs anchor drift    | (not in v2)          | v1.F3                | v2.F3 is unit-system co-mingling          |
| F5 collateral ledger    | v2.F5                | v1.F5                | Aligned (acknowledged-OPEN in v2)         |
| F6 plist log/err swap   | (not in v2)          | v1.F6                | v2.F6 is candidate_fact orphan rows       |
| F7 harvester tag filter | (not in v2)          | v1.F7                | v2.F7 is order_intent/venue_command lineage |
| F8 sentinel string      | (not in v2)          | v1.F8                | v2.F8 is observations_v2 dual-write window |
| F10 alarm split         | (not in v2)          | v1.F10               | v2.F10 is risk_state.db drift             |
| F11 plist KeepAlive     | (not in v2)          | v1.F11               | v2.F11 is BulkChunker LIVE                |
| F12 SUPERSEDED          | (not in v2)          | v1.F12               | v2.F12 is migration script idempotency    |
| F13 Kelly               | (not in v2)          | v1.F13               | v2.F13 is settlement ux index (→ v2.F27)  |
| F15 settlements gap     | (not in v2)          | v1.F15               | v2.F15 is chain_reconciliation skip_void  |
| F17 calibration trapdoor| (not in v2)          | v1.F17               | v2.F17 is forecasts.db user_version       |
| F18 obs_instants gap    | (not in v2)          | v1.F18               | v2.F18 is INSERT OR IGNORE silent loss    |
| F19 market_events shadow| (not in v2)          | v1.F19               | v2.F19 is collateral schema not in registry |
| F20 ensemble_snapshots  | (not in v2)          | v1.F20               | v2.F20 is position_lots reconciliation    |
| F21 hourly_instants     | v2.F21               | v1.F21               | Aligned                                   |
| F22 raw connect         | v2.F22               | v1.F22               | Aligned (v2 narrows to market_scanner.py) |
| F23 migration runner    | v2.F23               | v1.F23               | Aligned                                   |
| F25 triple-NULL         | v2.F25               | v1.F25 (new)         | Aligned                                   |
| F26 two-truth allowlist | v2.F26               | (new in v2)          | Aligned                                   |
| F27 unique index lockout| v2.F27               | (new in v2)          | Aligned                                   |

**Reading rule**: when an operator references "F#" in conversation about this audit, ASK which index (v1 or v2). If unclear, prefer v2 (current canonical for this package). The defects covered by cards below are the union of both schemes — every open item from BOTH indices has a card here.

All probes used `git show main:<path>` for source-of-truth (worktree src/ diverges from main by 50 files / 4,990 insertions). All DB probes used `file:…?mode=ro` URIs against the live state DBs at `state/zeus_trades.db` (576 MB), `state/zeus-forecasts.db` (49 GB), `state/zeus-world.db` (39 GB).

Status legend: **RESOLVED** (no action needed) · **NEEDS-CODE** (PR required, plan attached) · **NEEDS-OPERATOR** (manual step before Karachi 5/17) · **WAITING** (blocked on upstream artifact) · **SUPERSEDED** · **INVESTIGATE-FURTHER** (specific next probe attached).

---

## F1 — `assert_db_matches_registry` boot wiring DEFERRED

**Status**: NEEDS-CODE (DOCUMENTED-DEFERRAL)
**Evidence**: `src/main.py:1134-1138` comment block reads verbatim: "assert_db_matches_registry() exists (src/state/table_registry.py) but boot wiring is deferred — not called here." Helper exists at `src/state/table_registry.py:283`. 8 tests at `tests/state/test_table_registry_coherence.py` exercise the helper. The boot path between `init_schema(trade_conn)` (line 1129) and `_startup_freshness_check()` (line 1148) deliberately omits the call.
**Risk**: registry drift not caught at boot; relies on `tests/state/test_table_registry_coherence.py` running in CI/pre-deploy. If a column is silently added/dropped outside the registry, runtime queries with `SELECT col_that_no_longer_exists` raise `sqlite3.OperationalError` only at first query, not at boot.
**Karachi impact**: LOW (Karachi 5/17 cascade does not touch registry-managed schema diffs).
**Call-to-action**: PR-K (post-Karachi). Wire `assert_db_matches_registry(world_conn, DBIdentity.WORLD)` and `assert_db_matches_registry(trade_conn, DBIdentity.TRADES)` between line 1132 and 1138 (immediately after each connection opens). Guard with `ZEUS_BOOT_REGISTRY_ASSERT_ENABLED` env (default `true` after one shadow run).
**Verification probe**: rerun `git --no-pager grep -n "assert_db_matches_registry(" main -- src/main.py` — must show 2 call sites (currently 0 in main.py beyond the comment).

---

## F2 — `selection_hypothesis_fact.decision_id` 100% NULL

**Status**: NEEDS-CODE (1-line fix, ready-to-PR)
**Evidence**: Live count `1518 / 1518` NULL (100%). Function signature `src/state/db.py:5303` has `decision_id: str | None = None`. Call site `src/engine/evaluator.py:1535-1548` omits the `decision_id=` kwarg entirely. The kwarg is present on the function but unused at the only production caller.
**Root cause**: single missing kwarg. The surrounding loop has `decision_snapshot_id` in scope (line 1408+) but not `decision.decision_id` since the selection-family loop runs BEFORE `EdgeDecision` is constructed. The `candidate_id=row["candidate_id"]` IS already in scope at line 1538.
**Fix**: add `decision_id=row.get("decision_id"),` (if family-rows are stamped with decision_id) OR thread `decision_id` down from caller into `record_selection_facts(...)`. The simpler change: pass `decision_id` parameter into the surrounding helper and forward at line 1548.
**Karachi impact**: NONE (audit/calibration table only; not on the redeem cascade).
**Call-to-action**: PR-A. 5-line patch + test that asserts `SELECT decision_id FROM selection_hypothesis_fact WHERE recorded_at > <now>` is non-null after one cycle. NO backfill needed (audit value low; new rows will be correct).

---

## F3 — Doctrine docs anchor drift

**Status**: NEEDS-CODE (docs sync)
**Evidence**: Main HEAD is `9259df3e9c…` (Sun May 17 04:00 -0500). Doctrine docs grep for "Main HEAD\|HEAD=\|Anchor:" in `docs/current_state.md docs/current_data_state.md` returned 0 anchors at main.
**Risk**: docs become unanchored from the SHA they describe → readers cannot disambiguate which state ("28k decision_fact rows") corresponds to which schema.
**Call-to-action**: PR-B (docs). Add `<!-- ANCHOR: HEAD=<sha> as of YYYY-MM-DDTHH:MMZ -->` to top of each doc; require update when row counts cited in body change by >5%. No urgency.

---

## F5 — DB-lock storm Storm A (CollateralLedger raw connect)

**Status**: RESOLVED (architectural, by design)
**Evidence**: `src/state/collateral_ledger.py:117-128` `_connect_owned_collateral_db()` wraps `sqlite3.connect()` directly with `check_same_thread=False`, `PRAGMA journal_mode=WAL`, and `busy_timeout` (default 60000ms). Ledger is a process-wide singleton (`db_path` mode, line 219 `self._conn = _connect_owned_collateral_db(db_path); self._owns_conn = True`). It is NOT routed through `src/state/db._connect()` because the collateral DB (`risk_state.db`) is logically separate from trades/world/forecasts — different write contention domain.
**Why this is OK**: the ledger holds a long-lived `check_same_thread=False` connection on `risk_state.db` exclusively (cross-checked: `SQLITE_CONNECT_ALLOWLIST` in `src/state/db_writer_lock.py:575` includes `src/state/collateral_ledger.py`). The historical "lock storm" was on the TRADE DB, not on `risk_state.db`.
**Karachi impact**: NONE.
**Call-to-action**: NONE. Update `LEARNINGS.md` Cat-A to note "Storm A is not a storm — collateral ledger owns a dedicated DB and is allowlisted by design."

---

## F6 — launchd plist `.log`/`.err` swap

**Status**: INVESTIGATE-FURTHER (claim unverified at this run; defer)
**Evidence**: 7 zeus plists exist in `~/Library/LaunchAgents/`. Did not parse each `StandardOutPath` / `StandardErrorPath` this run.
**Next probe**: `for p in ~/Library/LaunchAgents/com.zeus.*.plist; do echo "==$p=="; plutil -extract StandardOutPath raw "$p" 2>/dev/null; plutil -extract StandardErrorPath raw "$p" 2>/dev/null; done`. Compare paths to actual content of each `.log`/`.err` file (does `.log` contain stderr-like ERROR lines or stdout-like INFO lines?). Time-box: 10 min.
**Karachi impact**: LOW (only affects operator diagnosis of crashes, not the cascade itself).

---

## F7 — Harvester missing tag/category filter

**Status**: CONFIRMED (architectural acceptance recommended)
**Evidence**: `src/ingest/harvester_truth_writer.py:262-280` paginates `GAMMA_BASE/events` with `closed=true`, `order=endDate`, `ascending=false`, bounded by `_CLOSED_EVENTS_CUTOFF_DAYS` and `_CLOSED_EVENTS_MAX_WALL_SECONDS`. NO `tagId`/`category` filter is sent. The downstream loop iterates ALL closed events and the per-event city/date guard at line 743+ drops non-weather markets. Bounded retry IS present (the wall-cap antibody fires at `_CLOSED_EVENTS_MAX_WALL_SECONDS`).
**Why this is acceptable for now**: Gamma's tagId filter has been historically unreliable for weather markets (per audit comments in prior runs). The wall-cap + cutoff bound the worst-case fetch cost.
**Risk**: at high market velocity (post-Karachi), per-page non-weather noise could push the paginator over `_CLOSED_EVENTS_MAX_WALL_SECONDS` and truncate before reaching the day's settled weather markets.
**Call-to-action**: PR-C (post-Karachi). Add optional `tagId=<weather_tag>` first; if API returns 0 rows (tag broken) → silently fall back to full fetch and log `harvester.tag_filter_skipped`. INVESTIGATE-FURTHER: probe `httpx.get(GAMMA_BASE/events, params={'tagId':<id>,'closed':'true','limit':1})` for current API behavior.

---

## F8 — `position_events.occurred_at='unknown_entered_at'` sentinel

**Status**: ACTIVE INCIDENT (KARACHI-RELEVANT)
**Evidence**: 2 rows present, BOTH on LIVE positions:
- `c30f28a5-d4e:chain_synced:3` — position_id `c30f28a5-d4e`, event_type CHAIN_SYNCED, phase_before `pending_entry` → phase_after `active`, source_module `src.state.chain_reconciliation`, env `live`. This IS the Karachi 5/17 live $0.59 position.
- `bf0a16f5-f95:chain_synced:3` — analogous CHAIN_SYNCED on a second pending-fill-rescued position, env `live`.

Both fired through `chain_reconciliation` "pending_fill_rescued" branch which stamps `occurred_at='unknown_entered_at'` (literal string) when the actual entry timestamp cannot be recovered from chain logs.
**Karachi impact**: HIGH for operator diagnosis. The sentinel CANNOT participate in any ORDER BY occurred_at queries (`'unknown_entered_at' < '2026'` lexicographically: TRUE → these rows sort to top), corrupting position timelines. The cascade itself routes by `position_id`+`condition_id`, not by `occurred_at`, so the redeem will still work — BUT post-settlement audit traces will be misleading.
**Call-to-action**:
- (NEEDS-CODE post-Karachi) PR-D: change `chain_reconciliation` pending_fill_rescue to stamp `occurred_at = chain_block_timestamp_iso` if available, else `recorded_at` of the rescue itself, NEVER a non-ISO literal.
- (NEEDS-OPERATOR pre-Karachi): note these 2 sentinel rows in the runbook so operators do not panic if their per-position timeline shows `unknown_entered_at` as the first event.
**Verification probe**: `python3 -c "import sqlite3; c=sqlite3.connect('file:state/zeus_trades.db?mode=ro',uri=True).cursor(); print(c.execute(\"SELECT COUNT(*) FROM position_events WHERE occurred_at='unknown_entered_at'\").fetchone())"` — must show 2 currently.

---

## F10 — Alarm channel split (sensor RED ↔ dispatcher degraded)

**Status**: INVESTIGATE-FURTHER (partial-evidence; needs end-to-end trace)
**Evidence**: `scripts/heartbeat_dispatcher.py` exists (97 LOC, cron `*/30` triggered). Maps healthcheck exit codes 0=healthy (silent), 1=degraded, 2=dead → triggers full Venus session via `openclaw cron run zeus-heartbeat-001`. Dispatcher reads NO file from `heartbeat_sensor.py`; the only signal is `healthcheck.py` exit code. `git grep "severity.*RED\|heartbeat.*severity"` in src/observability returned 0 matches at main — so sensor "RED" severity claimed in prior audits is NOT being consumed by dispatcher in current main.
**Verdict**: alarm channel is HEALTH-CHECK-BASED, not severity-grade. The sensor may write its own log/metric, but dispatcher does not read it. This is a single-channel system; the "split" claim from prior audits may have been describing the heartbeat-sensor plist running independently as a metrics emitter while the dispatcher is the only paging path.
**Next probe**: `git -C $zeus show main:scripts/healthcheck.py | head -100` to see what conditions trigger exit 1 vs 2. Plus `grep -rn "severity.*RED\|GREEN\|YELLOW" main -- src/observability/ scripts/ 2>&1 | head -20`. If sensor writes severity to a file that dispatcher does not read, file a NEEDS-CODE to wire sensor → dispatcher.
**Karachi impact**: MEDIUM — if a cascade-liveness check fails silently (not surfaced as exit 1 from healthcheck.py), operator gets no page.

---

## F11 — heartbeat-sensor plist `KeepAlive` MISSING

**Status**: CONFIRMED, NEEDS-OPERATOR
**Evidence**: `plutil -extract KeepAlive raw` returned MISSING on:
- `com.zeus.heartbeat-sensor.plist`
- `com.zeus.calibration-transfer-eval.plist`
All other 5 zeus plists have `KeepAlive=true`. Cron `*/30 * * * *` runs `heartbeat_dispatcher.py` (separate fallback channel, see F10).
**Risk**: if heartbeat-sensor crashes between runs, no auto-restart. The cron-driven dispatcher provides redundancy for paging, but any sensor-only metrics emission is silently lost between crash and next plist run.
**Call-to-action**: NEEDS-OPERATOR (pre-Karachi). Edit each plist, add `<key>KeepAlive</key><true/>`, then `launchctl unload && launchctl load`. Idempotent. Time-box: 5 min total.

---

## F12 — Math drift (alpha clamp / Kelly)

**Status**: SUPERSEDED by F13 (same problem domain). See F13 card.

---

## F13 — Kelly modulators

**Status**: WAITING (needs domain owner review)
**Evidence**: Last touched commits are 6be2f27b1a / d7db6ba2ef / ace98fe5af (2026-04 wave). No regression detected since.
**Karachi impact**: NONE (no new sizing decisions before settlement).
**Call-to-action**: defer to post-Karachi sizing review. No probe needed in Run #8.

---

## F15 — `settlements` vs `settlements_v2` 1583-row gap

**Status**: CONFIRMED
**Evidence**: In `zeus-forecasts.db`: `settlements=5599`, `settlements_v2=4016`. Gap = **1583 rows** (28.3% of legacy table missing from v2). Both tables EMPTY in `zeus_trades.db` and `zeus-world.db` (no shadow this time).
**Root cause**: dual-write was disabled at some point during the v2 migration, but the legacy v1 writer continued and now leads v2 by 1583 rows. `chain_reconciliation skip_voiding` grep returned 0 hits at main — the legacy skip-voiding path may have been refactored OR removed without backfilling v2.
**Karachi impact**: MEDIUM. If any cascade or operator CLI reads `settlements_v2` to confirm an outcome that only exists in `settlements`, it will silently fall through to a "missing" path.
**Call-to-action**:
- (INVESTIGATE-FURTHER pre-Karachi, 30-min budget): `git log -p --all -- src/state/settlements*.py | grep -B5 -A20 "v2\|dual_write" | head -80` to confirm whether dual-write was disabled deliberately.
- (NEEDS-CODE post-Karachi) PR-E: backfill v2 from v1 (`INSERT OR IGNORE INTO settlements_v2 SELECT … FROM settlements WHERE …`), then re-enable dual-write OR delete v1 if v2 supersedes.

---

## F17 — Calibration transfer OOS eval trapdoor

**Status**: RESOLVED (gate CLOSED by default)
**Evidence**: `src/data/calibration_transfer_policy.py:655,755` both gate on `os.environ.get("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "false").lower() == "true"`. Default `"false"` → gate CLOSED. Writer at `scripts/evaluate_calibration_transfer_oos.py:381` requires the daemon-lock check and explicit `--write` flag. Reader at `:452` queries `validated_calibration_transfers` only when the env flag is true.
**Risk**: any operator setting `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true` in service-env activates the path. No env match for that key in `service-env/` directory (grep returned 0 lines). State: confirmed OFF.
**Karachi impact**: NONE.
**Call-to-action**: PR-F (post-Karachi, optional). Add a `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_LOCKED=true` second gate that REFUSES `--write` even if the first env is true, unless a manual CLI confirmation flag is also passed. Defense-in-depth.

---

## F18 — `observation_instants` legacy vs `_v2` count gap

**Status**: CONFIRMED, NEEDS-CODE (truncate plan)
**Evidence**: `zeus-world.observation_instants=906,873`, `zeus-world.observation_instants_v2=1,835,645`. v2 has 2.02× rows. Both 0 in other DBs.
**Interpretation**: v2 is leading (more rows). The legacy 906k is dead data left after v1-to-v2 migration. Continued writes to legacy are documented in F21.
**Karachi impact**: NONE (read paths use v2 if available — needs verification: `grep -rn "FROM observation_instants " main -- src/ | grep -v _v2`).
**Call-to-action**: PR-G (post-Karachi). After F21 is fixed (stop legacy writes), snapshot `observation_instants` and DROP it. Reclaims disk space.

---

## F19 — `market_events_v2` 3-DB shadow

**Status**: CONFIRMED
**Evidence**: Live row counts:
- `zeus-forecasts.market_events_v2 = 10,541` (live, written by `src/data/market_scanner.py:610` to `ZEUS_FORECASTS_DB_PATH`)
- `zeus_trades.market_events_v2 = 7,953` (shadow)
- `zeus-world.market_events_v2 = 2,112` (shadow)
Three different write authorities have produced three different row-counts in three DBs. The forecasts DB is the documented current writer.
**Karachi impact**: MEDIUM — if any read path (operator CLI? scheduler?) reads from `zeus-world` or `zeus_trades`, it sees a 2k-7k row world and misses recent markets.
**Call-to-action**: (INVESTIGATE-FURTHER, 20-min): `grep -rn "FROM market_events_v2\|JOIN market_events_v2" main -- src/ scripts/ | head -20` to enumerate readers and which DB connection they use. Then NEEDS-CODE: route all reads through `get_forecasts_connection(read_class="ro")`; ATTACH if cross-DB join needed.

---

## F20 — `ensemble_snapshots` 116 dead legacy rows

**Status**: CONFIRMED
**Evidence**: `zeus-world.ensemble_snapshots = 116` (dead legacy). `zeus-forecasts.ensemble_snapshots_v2 = 1,124,780` (live). All other shards = 0.
**Call-to-action**: (NEEDS-CODE post-Karachi, low priority) PR-H: `DROP TABLE ensemble_snapshots` in `zeus-world.db` after confirming via grep that no reader queries the legacy table.

---

## F21 — Legacy `observation_instants` writer at `hourly_instants_append.py:229`

**Status**: CONFIRMED, NEEDS-CODE
**Evidence**: `git grep "INSERT.*INTO observation_instants" main -- src/ scripts/`:
- `scripts/backfill_hourly_openmeteo.py:241` — `INSERT OR IGNORE INTO observation_instants` (legacy, backfill-only, acceptable)
- `src/data/hourly_instants_append.py:229` — `INSERT OR REPLACE INTO observation_instants` (LIVE writer to legacy table; this is the bug)
- `src/data/observation_instants_v2_writer.py:417` — `INSERT INTO observation_instants_v2` (live, correct)
The hourly_instants_append.py writer continues to feed the legacy `observation_instants` table while the v2 writer feeds `_v2`. Two writers → two truths.
**Call-to-action**: PR-I (post-Karachi). Either (a) delete the legacy write in `hourly_instants_append.py:229` after confirming `observation_instants_v2_writer.py` covers the same surface, or (b) make hourly_instants_append.py also dual-write to v2.

---

## F22 — Raw `sqlite3.connect()` proliferation

**Status**: CONFIRMED (must be triaged, not patched wholesale)
**Evidence**:
- `src/`: 15 raw connect sites, 6 in `src/state/db.py`/`db_writer_lock.py`/`schema/v2_schema.py` (legitimate — the lock infra IS the place that owns raw connects), 9 outside the allowlist (`src/control/cli/promote_entry_forecast.py:97`, `src/data/market_scanner.py:610`, `src/ingest_main.py:849`, `src/main.py:836`, `src/main.py:858`, `src/observability/status_summary.py:81`, `src/riskguard/discord_alerts.py:167`, `src/state/collateral_ledger.py:121`).
- `scripts/`: **92 raw connect sites** (one-shot scripts; arguably acceptable but operator-action paths should be allowlisted or routed through `get_*_connection`).
- `tests/conftest.py:177-396`: enforces `_WLA_SQLITE_CONNECT_ALLOWLIST` (the `~40` entries claim verified — see F26 for the dual-source defect).
**Karachi impact**: LOW (no new raw connects are added by the Karachi cascade path).
**Call-to-action**: PR-J (post-Karachi). Triage: (a) operator-action scripts (`arm_live_mode.sh`, `bridge_oracle_to_calibration.py`, `cleanup_ghost_positions.py`) must route through `get_*_connection()` so they participate in the writer-lock contract; (b) read-only backfill/audit scripts can stay raw if `?mode=ro` URI is enforced.

---

## F23 — Migration runner bare

**Status**: CONFIRMED (architectural gap)
**Evidence**: `scripts/migrations/` contains only:
- `__init__.py` (6 lines, comment-only, no `apply_migrations()` helper)
- `202605_add_redeem_operator_required_state.py` (PR-126's single migration)
No `_migrations_applied` table. No CLI runner. No dry-run mode. No rollback. The PR-126 migration is invoked directly by `tests/test_migration_redeem_operator_required.py`.
**Risk**: 2nd migration will re-create the same ad-hoc pattern; eventually operators will apply migrations out-of-order or twice.
**Karachi impact**: NONE (PR-126's migration was already applied).
**Call-to-action**: PR-L (post-Karachi). Build minimal ledger:
1. `_migrations_applied(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)` table in `zeus_trades.db`.
2. CLI `python -m scripts.migrations apply [--dry-run] [--target=NAME]` that scans `scripts/migrations/2*.py`, calls `up(conn)` on each not in the ledger, records on success.
3. Per-migration `up(conn)` + optional `down(conn)` interface.

---

## F24 — Probability trace `degraded_decision_context` status flooding

**Status**: SUPERSEDED-BY-F25 (same upstream root cause; resolves automatically when F25 fixed)
**Evidence**: `log_probability_trace_fact` stamps `trace_status="degraded_decision_context"` when `decision.availability_status` is not in `{"", "OK"}`. The 19,175 NULL `decision_snapshot_id` rows are precisely the rejection-stage decisions (early filter/anti-churn), which also carry non-OK availability_status. Fix F25 (stamp dsi on rejection EdgeDecisions) and this status will collapse to `complete` for the same rows.

---

## F25 — SEV-0 triple-NULL on `opportunity_fact.snapshot_id` + `probability_trace_fact.decision_snapshot_id`

**Status**: ROOT CAUSE PROVEN, NEEDS-CODE
**Evidence (locked)**:
- `opportunity_fact` total **28,117** rows, snapshot_id NULL = **19,175** (68.2%).
- `probability_trace_fact` total **28,307** rows, decision_snapshot_id NULL = **19,175** (67.74%).
- JOIN on `decision_id` between the two tables: **19,175 rows share the same decision_id and are jointly NULL** → single upstream cause.
- NULL recorded_at range: `2026-05-02T01:00 → 2026-05-17T10:49` (ACTIVELY HAPPENING right now).
- `EdgeDecision(…)` construction sites in `src/engine/evaluator.py`: **71 total, 31 do NOT pass `decision_snapshot_id`**. Lines: 1722, 1737, 1759, 1775, 1802, 1811, 1850, 1862, 1873, 1950, 1982, 2003, 2013, 2023, 2034, 2044, 2062, 2083, 2105, 2122, 2135, 2161, 2186, 2197, 2230, 2256, 2291, 2312, 2327, 2364, 2422. All 31 are early-rejection paths (MARKET_FILTER, ANTI_CHURN, sizing-rejection, etc.) that fire BEFORE `snapshot_id` is resolved later in the function body.
- Both writers fall back: `log_opportunity_fact` reads `decision.decision_snapshot_id` (→ None when unset → NULL); `log_probability_trace_fact` ditto.
**Root cause**: `decision_snapshot_id` is not threaded into early-rejection EdgeDecision constructors. The 40 sites that DO pass it are post-`snapshot_id` resolution (line 2383+).
**Fix plan (PR-A.1)**:
1. Hoist `snapshot_id` computation to the TOP of `evaluate_candidate(...)` (or pass it in from caller).
2. Pass `decision_snapshot_id=snapshot_id` to all 31 early-rejection sites.
3. Add an antibody test: `tests/engine/test_evaluator_dsi_threaded.py` that constructs a candidate guaranteed to hit a MARKET_FILTER early-rejection path, then asserts the resulting EdgeDecision's `decision_snapshot_id` is non-empty.
4. After deploy, monitor: `SELECT COUNT(*) FROM opportunity_fact WHERE snapshot_id IS NULL AND recorded_at > <deploy_time>` → must trend to 0.
**Karachi impact**: NONE (this affects audit completeness, not the cascade). But fixing it improves post-Karachi forensics.
**Backfill**: skipped — the 19,175 historical rows cannot recover snapshot_id from outside the decision context. Backfill is impossible without re-running evaluation; declare them "pre-fix tombstone" and move on.

---

## F26 — Two-truth `SQLITE_CONNECT_ALLOWLIST`

**Status**: CONFIRMED, NEEDS-CODE
**Evidence**:
- `src/state/db_writer_lock.py:575` — `SQLITE_CONNECT_ALLOWLIST: frozenset[str]` (the declarative source).
- `tests/conftest.py:177` — `_WLA_SQLITE_CONNECT_ALLOWLIST = frozenset({...})` (the enforced source); used at lines 327, 352, 391-396 to gate test failures.
The comment at `tests/conftest.py:192` ("co-located in SQLITE_CONNECT_ALLOWLIST inside src/state/db_writer_lock.py") indicates the author KNEW this was duplicated. Never reconciled.
**Risk**: a new file added to `src/state/db_writer_lock.py:SQLITE_CONNECT_ALLOWLIST` does not get test-allowlist coverage; tests fail. Conversely, adding to test-only allowlist bypasses the production lock's intent.
**Karachi impact**: NONE.
**Call-to-action**: PR-M (post-Karachi). `tests/conftest.py` should `from src.state.db_writer_lock import SQLITE_CONNECT_ALLOWLIST as _WLA_SQLITE_CONNECT_ALLOWLIST`. Delete the inline duplicate. CI then enforces single source.

---

## F27 — SEV-1 PR-126 UNIQUE INDEX excludes only 2 of 3 terminal states

**Status**: CONFIRMED — DESIGN ARTIFACT (NOT A BUG), NEEDS-DOCS
**Evidence**:
- DDL at `src/execution/settlement_commands.py:57-59`:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_settlement_commands_active_condition_asset
  ON settlement_commands (condition_id, market_id, payout_asset)
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');
```
- `REDEEM_OPERATOR_REQUIRED` IS in the index (not excluded).
- `_TERMINAL_STATES` in code (line 100-104) is `{REDEEM_CONFIRMED, REDEEM_FAILED, REDEEM_REVIEW_REQUIRED}` — also a 3-state set that DIFFERS from the index's 2-state exclusion.
- The NOTE at line 90-94 verbatim: "REDEEM_OPERATOR_REQUIRED is NOT terminal. It is a designed-terminal-with-operator-action state. The operator CLI transitions it out to REDEEM_TX_HASHED; no scheduler tick touches it (disjoint state guard with `_SUBMITTABLE_STATES`)."
- PR-126 commit message: "Karachi 5/17 T-0 cascade-plumbing antibody ready."
**Verdict**: INTENTIONAL by design. The index BLOCKS a second active settlement for the same `(condition_id, market_id, payout_asset)` while one is in operator-required limbo. This is the desired behavior: operator MUST first run `operator_record_redeem.py` to transition the row out before a new settlement is queued.
**But**: `REDEEM_REVIEW_REQUIRED` IS in `_TERMINAL_STATES` and is NOT excluded from the index. This means a row in REVIEW_REQUIRED ALSO blocks new inserts on the same triple. Whether intentional is unclear — terminal-but-blocking is unusual.
**Risk for Karachi 5/17**: only relevant if the same `(condition_id, market_id, payout_asset)` triple needs a second settlement command DURING the cascade. With one position (`c30f28a5-d4e`) per (city,date), the triple is unique → no collision possible. SAFE for this Karachi.
**Call-to-action**:
- (NEEDS-DOCS pre-Karachi) — add a one-paragraph note to `KARACHI_5_17_SHIP_DECISION_MATRIX.md` explaining the index's INTENT (blocks duplicate active settlements) so an operator does not panic on `IntegrityError: UNIQUE constraint failed`.
- (POST-KARACHI INVESTIGATE-FURTHER) — clarify why `REDEEM_REVIEW_REQUIRED` is not excluded. Should it be? File as F29 candidate.

---

## Resolution-card summary count

| Status              | Count | Findings                                           |
|---------------------|-------|----------------------------------------------------|
| RESOLVED            | 3     | F5, F17, (and F12 SUPERSEDED→F13)                 |
| NEEDS-CODE          | 9     | F1, F2, F18, F19, F21, F22, F23, F25, F26         |
| NEEDS-OPERATOR      | 2     | F11, F8 (runbook note)                            |
| NEEDS-DOCS          | 2     | F3, F27                                            |
| CONFIRMED-ACCEPT    | 2     | F7, F15 (with INVESTIGATE-FURTHER attached)       |
| WAITING             | 1     | F13                                                |
| INVESTIGATE-FURTHER | 3     | F6, F10, F19-reader-trace                         |
| SUPERSEDED          | 2     | F12 (→F13), F24 (→F25)                            |

**No item remains ambiguous after Run #8.** Every open item has either a definitive verdict, a 1-shot probe to flip the verdict, or an explicit accept-with-justification.
