# Lane W9 — Query Efficiency Audit (read-only)

Scope: hot SELECTs on the reactor cycle / monitor tick / harvester path, harvested from
`src/engine/cycle_runner.py`, `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`,
`src/execution/harvester.py` and the `src/state`/`src/execution` modules they call.
Method: every plan is `EXPLAIN QUERY PLAN` via the venv-3.53.2 read-only pattern (EQP does
not execute). Table/index sizes cite `findings/census_raw.jsonl` line numbers. No writes,
no ANALYZE, no dbstat. WAL before 230 MiB → after 128 MiB (a background checkpoint ran; my
RO reads did not grow it). Forecasts WAL 169 KiB. Neither crossed the 512 MiB stop line.

Census caveat: `census_raw.jsonl` (304 rows) is a PARTIAL census — it omits `raw_model_forecasts`,
`idx_observations_city_date`, and the `idx_raw_model_forecasts_*` family on forecasts.db, all of
which EQP proves present. trades.db has NO `sqlite_stat1`; forecasts.db has `sqlite_stat1` with
only 1 cell (census L288) → effectively one indexed object has stats, the planner is blind
almost everywhere on both DBs.

Severity legend: **P1** = scaling landmine on a giant table / real waste; **P2** = correct-now
but blind/unindexed and grows O(rows); **OK** = optimal.

---

## (a)+(b) Hot SELECT inventory with EXPLAIN QUERY PLAN verdicts

### TRADES DB

| # | site (file:line) | cadence | table (size, census) | EQP plan | verdict |
|---|---|---|---|---|---|
| 1 | `exit_lifecycle.py:1771` & `:1894` `_executable_snapshot_min_order_size` | per monitor-tick, per held position | `executable_market_snapshots` (46.3 GB / 18.0M rows, L33) | `SEARCH USING INDEX idx_snapshots_selected_token_captured (selected_outcome_token_id=?)` + **`USE TEMP B-TREE FOR LAST TERM OF ORDER BY`** | **P1** — see F1 |
| 2 | `snapshot_repo.py:555` `latest_snapshot_for_market` fallback | per-tick fallback (when `_latest` misses) | `executable_market_snapshots` (46.3 GB, L33) | `SEARCH USING INDEX idx_snapshots_condition_captured (condition_id=?)` — no sort; **but no LIMIT + `SELECT *`** | **P1** — see F2 |
| 3 | `snapshot_repo.py:534` `latest_snapshot_for_market` primary | per-tick | `executable_market_snapshot_latest` (34 MB, L192) | `SEARCH USING INDEX idx_snapshot_latest_condition_captured (condition_id=?)` LIMIT 1 | OK |
| 4 | `harvester.py:1366` `_supplement_held_position_settlement_events` | hourly, per held condition | `executable_market_snapshots` (46.3 GB, L33) | `SEARCH USING INDEX idx_snapshots_condition_captured (condition_id=?)` LIMIT 1 | OK |
| 5 | `book_hash_transitions.py:63` write-probe `MAX(transition_seq)` | per book-hash write (high freq) | `book_hash_transitions` (2.29 GB / 10.3M rows, L110) | `SEARCH USING COVERING INDEX sqlite_autoindex_book_hash_transitions_1 (market_slug=? AND observed_at=?)` (MAX-optimized) | OK |
| 6 | `book_hash_transitions.py:124` `read_transitions_by_market` | on demand | `book_hash_transitions` (2.29 GB, L110) | `SEARCH USING INDEX sqlite_autoindex_book_hash_transitions_1 (market_slug=? AND observed_at>?)` | OK (PK serves it → see F4) |
| 7 | `evaluator.py:3330` `_has_same_token_blocking_open_db` | per entry candidate | `position_current` (917 KB / 1311 rows, L262) | **`SCAN position_current`** | P2 — see F5 |
| 8 | `evaluator.py:3233` `_has_positive_trade_fact_for_position_or_order` | per entry candidate | `venue_trade_facts`⋈`venue_commands` (2135 / 1469 rows) | **`SCAN vtf`** + `SEARCH vc USING sqlite_autoindex_venue_commands_1 (command_id=?)` | P2 — see F6 |
| 9 | `evaluator.py:3274` `_latest_entry_command_for_position` | per entry candidate | `venue_commands` (1 MB / 1469 rows, L22) | **`SCAN venue_commands`** + **`USE TEMP B-TREE FOR ORDER BY`** | P2 — see F6 |
| 10 | `evaluator.py:3251` `_has_terminal_no_fill_order_fact_for_command` | per entry candidate | `venue_order_facts` (33 MB / 44K rows, L44) | `SEARCH USING INDEX idx_order_facts_command (command_id=?)` + `USE TEMP B-TREE FOR ORDER BY` | P2 (bounded per-command) |
| 11 | `harvester.py:145` `_next_canonical_sequence_no` `MAX(sequence_no)` | per settled position | `position_events` (902 MB / 398K rows, L269) | `SEARCH USING COVERING INDEX sqlite_autoindex_position_events_3 (position_id=?)` (MAX-optimized) | OK |
| 12 | `harvester.py:736` `_snapshot_position_training_eligible` | per settlement snapshot | `position_current` (917 KB / 1311 rows, L262) | **`SCAN position_current`** (no index on `decision_snapshot_id`) | P2 — see F7 |
| 13 | `harvester.py:672` `enqueue_redeem_command` active lookup | per redeem | `settlement_commands` (78 KB / 143 rows, L93) | `SEARCH USING INDEX ux_settlement_commands_active_condition_asset (...)` (partial index used) | OK |
| 14 | `entry_exposure_obligation.py:239` `has_unbounded_obligation` | every reactor cycle | `entry_exposure_obligations` (61 KB / 228 rows, L252) | `SEARCH USING COVERING INDEX idx_entry_exposure_obligations_status_unbounded (status=? AND unbounded=?)` | OK |

### FORECASTS DB

| # | site (file:line) | cadence | table | EQP plan | verdict |
|---|---|---|---|---|---|
| 15 | `monitor_refresh.py:1427` `_read_day0_raw_model_extrema` latest-cycle | per monitor-tick, Day0 held | `raw_model_forecasts` (not in census; large multi-model store) | `SEARCH USING INDEX idx_raw_model_forecasts_endpoint_family_cycle_members (endpoint,city,target_date,metric)` + **`USE TEMP B-TREE FOR ORDER BY`** | P2 — see F8 |
| 16 | `monitor_refresh.py:1451` `_read_day0_raw_model_extrema` members | per monitor-tick, Day0 held | `raw_model_forecasts` | `SEARCH USING INDEX idx_raw_model_forecasts_endpoint_family_cycle_members (…AND source_cycle_time=?)` + `USE TEMP B-TREE FOR ORDER BY` (order by `model`) | P2 (small fan-out) |
| 17 | `harvester.py:535` `_lookup_settlement_obs` | hourly, per settled city | `observations` (92 MB / 71.6K rows, L280) | `SEARCH USING INDEX idx_observations_city_date (city=? AND target_date=?)` | OK |

---

## (b) Detailed findings

### F1 — [P1] Per-tick temp B-tree on the 46 GB snapshot table (`exit_lifecycle`)
`_executable_snapshot_min_order_size` (`exit_lifecycle.py:1759` and `:1884`) runs on EVERY
monitor tick for EVERY held position against `executable_market_snapshots` (46.3 GB, 18.0M rows):
```sql
SELECT min_order_size FROM executable_market_snapshots
 WHERE selected_outcome_token_id = ?
   AND freshness_deadline IS NOT NULL
   AND datetime(freshness_deadline) >= datetime(?)
 ORDER BY captured_at DESC, snapshot_id DESC LIMIT 1
```
EQP: `SEARCH … USING INDEX idx_snapshots_selected_token_captured (selected_outcome_token_id=?)`
**+ `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`**. The index is `(selected_outcome_token_id,
captured_at DESC)` (1.74 GB, L150); it satisfies the token seek and the `captured_at DESC` order,
but the `snapshot_id DESC` tiebreak is not in the index so SQLite builds a temp B-tree over the
whole token partition BEFORE applying LIMIT 1. Two aggravators, both stat1-blind:
(i) `datetime(freshness_deadline) >= datetime(?)` is a non-sargable residual (function wraps the
column) — every index row in the token partition is deserialized and filtered; (ii) with no
`sqlite_stat1` the planner cannot know `captured_at` is (almost surely) unique per token, so it
cannot prove the tiebreak is dead and elide the temp B-tree.
Fix belongs in the remediation packet, not here: drop the `snapshot_id` tiebreak (or add it to
the index) to remove the temp B-tree; store/compare `freshness_deadline` as plain ISO text so the
predicate is sargable. Today it is bounded per-token but the temp B-tree scales with a token's
snapshot history.

### F2 — [P1] `SELECT *` with no LIMIT on the 46 GB table (`snapshot_repo` fallback)
`latest_snapshot_for_market` (`snapshot_repo.py:551-560`) fallback, reached whenever
`executable_market_snapshot_latest` misses:
```sql
SELECT * FROM executable_market_snapshots
 WHERE condition_id = ? AND freshness_deadline >= ? ORDER BY captured_at DESC
```
EQP: `SEARCH … USING INDEX idx_snapshots_condition_captured (condition_id=?)` — index-ordered, no
sort. The defect is fetch-amplification: `SELECT *` of every fresh, wide row for the condition
with **no LIMIT**, while the caller only consumes the first non-invalidated snapshot in a Python
loop. A hot condition can have thousands of fresh snapshots (18.0M rows / 10.3M distinct-condition
autoindex cells, L34 ≈ ~1.7 rows per condition-key overall, but recent live conditions skew far
higher). With no `sqlite_stat1` the planner has no cardinality signal to expose the blow-up.
Remediation: add `LIMIT` and select only the needed columns.

### F3 — [P1] Redundant duplicate index: `market_price_history` (~87.8 MB reclaimable)
`market_price_history` declares `UNIQUE(token_id, recorded_at)` (table constraint →
`sqlite_autoindex_market_price_history_1`) AND an explicit
`CREATE INDEX idx_market_price_history_token_recorded ON market_price_history(token_id, recorded_at)`
(dump `trades_indexes.txt:196`). Same columns, same order, same BINARY collation, neither partial
→ an EXACT duplicate. Census proves it byte-for-byte: both are **87,838,720 bytes, 622,649 cells,
mx_payload 118** (L73 autoindex, L75 explicit). `idx_market_price_history_token_recorded` is fully
redundant and reclaimable (~87.8 MB), and it is a wasted 5th b-tree on every insert to this
657K-row table whose indexes (296 MB) already dwarf the table (142 MB).

### F4 — [P1] Redundant prefix index: `book_hash_transitions` (~955 MB reclaimable)
`book_hash_transitions` PRIMARY KEY is `(market_slug, observed_at, transition_seq)`
(`trades_tables.txt` book_hash_transitions → `sqlite_autoindex_book_hash_transitions_1`, 962 MB,
L111). The explicit `idx_book_hash_transitions_market_time ON (market_slug, observed_at)`
(`trades_indexes.txt:1`) is an exact 2-column PREFIX of that 3-column PK, non-partial, same
collation → strictly dominated. EQP confirms the PK autoindex already serves both real access
shapes: the write-probe `MAX(transition_seq) WHERE market_slug=? AND observed_at=?`
(`SEARCH USING COVERING INDEX sqlite_autoindex_book_hash_transitions_1`) and the range read
`WHERE market_slug=? AND observed_at>=? ORDER BY observed_at, transition_seq`
(`SEARCH USING INDEX sqlite_autoindex_book_hash_transitions_1`). `idx_book_hash_transitions_market_time`
is **955,666,432 bytes (L112)** of pure redundancy and one extra b-tree per insert on a 10.3M-row
append table. Largest single reclaim in this lane.

### F4b — [P2] Redundant prefix index: `venue_command_events` (~172 KB)
`venue_command_events` has `UNIQUE(command_id, sequence_no)` (autoindex). The explicit
`idx_venue_command_events_command ON (command_id)` (`trades_indexes.txt:329`) is a prefix of that
autoindex → redundant. Small (176,128 B, L31) but real; drop it.

### F5 — [P2] `position_current` full scan on token/NOT-IN entry dedup (`evaluator`)
`_has_same_token_blocking_open_db` (`evaluator.py:3330`): `WHERE (token_id=? OR no_token_id=?) AND
phase NOT IN (...)` → EQP `SCAN position_current`. The only secondary index
`idx_position_current_phase_quote(phase, chain_state, chain_shares, token_id, no_token_id)` leads
with `phase` (filtered by non-sargable `NOT IN`), so token lookup cannot use it. 1311 rows today =
cheap, but it is O(open-positions) on the per-candidate entry path and there is no token index.

### F6 — [P2] Blind join order + unindexed ORDER-BY on OR predicates (`evaluator`)
Three per-candidate entry-dedup queries, all stat1-blind:
- `_has_positive_trade_fact_for_position_or_order` (`evaluator.py:3233`): `venue_trade_facts JOIN
  venue_commands` with `(vc.position_id=? OR vc.venue_order_id=?)` → EQP drives `SCAN vtf` then
  probes `vc` by PK. The OR (venue_order_id is unindexed) blocks index-driving `vc`; join order is
  a guess — with no `sqlite_stat1` the planner has no row estimate to pick the smaller side. Benign
  at 2135/1469 rows; a landmine if either table grows.
- `_latest_entry_command_for_position` (`evaluator.py:3274`): OR + `ORDER BY updated_at DESC` →
  `SCAN venue_commands` + `USE TEMP B-TREE FOR ORDER BY` (updated_at unindexed).
- `_has_terminal_no_fill_order_fact_for_command` (`evaluator.py:3251`): indexed by command_id but
  `ORDER BY local_sequence DESC` (not in `idx_order_facts_command(command_id, observed_at)`) →
  temp B-tree, bounded to one command's rows.

### F7 — [P2] `position_current` full scan by un-indexed `decision_snapshot_id` (`harvester`)
`_snapshot_position_training_eligible` (`harvester.py:736`):
`WHERE decision_snapshot_id = ?` → `SCAN position_current`. No index on `decision_snapshot_id`
(only PK `position_id` and `idx_position_current_phase_quote`). 1311 rows = cheap now; O(rows) per
settled snapshot.

### F8 — [P2] `datetime()` wrap defeats index-ordered Day0 lookup (`monitor_refresh`, forecasts)
`_read_day0_raw_model_extrema` latest-cycle probe (`monitor_refresh.py:1427`) uses
`idx_raw_model_forecasts_endpoint_family_cycle_members` for the 4-col equality seek but adds
`USE TEMP B-TREE FOR ORDER BY` because `GROUP BY source_cycle_time ORDER BY
datetime(source_cycle_time) DESC` wraps the column in `datetime()`, making it non-sargable for
index order even though `source_cycle_time` is in the index. If stored as ISO-8601 UTC text, a
plain `ORDER BY source_cycle_time DESC` is lexical==chronological and index-usable, removing the
sort. Per monitor-tick for Day0-held positions.

---

## (c) sqlite_stat1 blindness — concrete planner guesses on trades.db

trades.db has NO `sqlite_stat1`; forecasts.db's `sqlite_stat1` holds 1 cell (L288). Per consult
§25/§119 the absence bites joins, competing indexes, ranges, skew, IN/OR, partial indexes. The
plans above expose exactly these classes:

1. **JOIN order (F6, `eval_tradefact_join`)** — `venue_trade_facts ⋈ venue_commands` with an OR:
   the planner picked `SCAN vtf` as the outer driver by its hard-coded default row estimates, not
   by data. Because both drivers here have an OR term it cannot index-drive, it is choosing which
   table to full-scan blind. Correct today only because both are tiny; on growth the wrong driver
   is a full O(N×M)-ish scan.
2. **Competing indexes on the 46 GB snapshot table** — `executable_market_snapshots` carries four
   near-symmetric secondary indexes: `idx_snapshots_condition_captured` (2.09 GB, L35),
   `_selected_token_captured` (1.74 GB, L150), `_yes_token_captured` (1.76 GB, L151),
   `_no_token_captured` (1.76 GB, L151). Any future query whose predicate could match more than one
   (e.g. filtering a token AND a condition) is a coin-flip: with no stats SQLite assumes ~10 rows
   per `=` on any index, off by ~6 orders of magnitude vs 18.0M rows, so its index choice is
   arbitrary. Today each hot query matches exactly one, so it lucks into the right index.
3. **Range + skew under-estimation (F2)** — `condition_id=? AND freshness_deadline>=?` on 18.0M
   rows: the planner's default selectivity cannot see that recent live conditions hold thousands of
   fresh snapshots, so it cannot cost the no-LIMIT `SELECT *` blow-up. This is the parameter-
   sensitive plan failure the consult (§17/§25) warns is *more* plausible without stats.
4. **Partial index (settlement_commands)** — the planner DID correctly use the partial
   `ux_settlement_commands_active_condition_asset` (F13/#13). Note this only works because the query
   predicate `state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')` textually matches the partial
   WHERE; a differently-phrased state filter would silently skip the partial index and, blind, fall
   to a scan.

Remediation direction (NOT run here): a vetted fixed `sqlite_stat1` set from a representative clone
(consult §453/§591), or bounded `PRAGMA optimize` per the one-DB-at-a-time rollout — never a live
full `ANALYZE`. The venv runtime is STAT4-compiled, so a clone-derived STAT4 sample would materially
help the skewed multi-token / range predicates on the 46 GB and 20 GB tables (consult §485).

---

## (d) Exact-prefix-redundant index audit (109 explicit + 85 autoindexes = 194)

Method: for each explicit `CREATE INDEX` I checked whether its full key-tuple is a prefix of
another index's key on the same table, guarded by uniqueness, collation, and partial-predicate
per the consult warning that "prefix overlap alone is insufficient."

**Confirmed redundant (reclaimable / drop candidates):**
| index | dominated by | reclaim | evidence |
|---|---|---|---|
| `idx_market_price_history_token_recorded (token_id, recorded_at)` | `sqlite_autoindex_market_price_history_1` = `UNIQUE(token_id, recorded_at)` | **~87.8 MB** | identical bytes/cells L73≡L75 |
| `idx_book_hash_transitions_market_time (market_slug, observed_at)` | PK `(market_slug, observed_at, transition_seq)` | **~955 MB** | L111 (PK) dominates L112; EQP F4 |
| `idx_venue_command_events_command (command_id)` | `UNIQUE(command_id, sequence_no)` autoindex | ~172 KB | L31; prefix of autoindex |

**Examined and NOT redundant (documented so they are not "fixed" by mistake):**
- `idx_settlement_commands_condition (condition_id, market_id)` vs
  `ux_settlement_commands_active_condition_asset (condition_id, market_id, payout_asset) WHERE state
  NOT IN (...)` — the superset is PARTIAL, so it cannot serve all-state queries; the plain index is
  required. (Exactly the partial-predicate trap the consult flagged.)
- `idx_payout_observations_active_lookup (condition_id, outcome_index, superseded_by)` vs
  `idx_payout_observations_condition (condition_id, outcome_index, id)` — share a 2-col prefix but
  diverge in the 3rd column; neither is a prefix of the other. Soft overlap only; both retained.
- `idx_wallet_fill_observations_trade (trade_id, id)` vs
  `idx_wallet_fill_observations_idempotent (trade_id, raw_payload_hash) UNIQUE` — share `trade_id`
  lead, diverge 2nd column; not prefix-redundant.
- `executable_market_snapshots` `idx_snapshots_{yes,no,selected}_token_captured` — three DIFFERENT
  token columns; all three are legitimately distinct, not redundant.
- Minor schema smell (not an index): `executable_market_snapshots` declares both
  `snapshot_id TEXT PRIMARY KEY` and a redundant table-level `UNIQUE (snapshot_id)`
  (`trades_tables.txt`); SQLite materialized only one autoindex (L34), so no wasted b-tree, but the
  duplicate constraint should be dropped from the DDL for clarity.

---

## (e) Write amplification — indexes maintained per INSERT on top-ingest tables

Counting every b-tree (PK/UNIQUE autoindexes + secondary) that an INSERT must maintain. Consult
threshold: >5 non-constraint secondary indexes on a top-ingest table = investigate.

| table (rows, table bytes) | b-trees/INSERT | non-constraint secondary | total index bytes | note |
|---|---|---|---|---|
| `executable_market_snapshots` (18.0M, 46.3 GB, L33) | **5** | 4 | **~7.99 GB** (L34,35,150,151 + 149) | 4 secondary each 1.7–2.1 GB; just under threshold but the heaviest amplification in the DB — every capture writes 5 b-trees |
| `execution_feasibility_evidence` (30.5M, 20.4 GB, L162) | 3 | 2 | ~10.26 GB (L163,164,189) | both secondaries lead with `token_id`, differ only in 2nd sort col (`quote_seen_at` vs `created_at DESC`); 4.56 GB + 3.24 GB |
| `book_hash_transitions` (10.3M, 2.29 GB, L110) | 3 → **2 after F4** | 2 → 1 | ~2.73 GB | one of the two secondaries (`market_time`, 955 MB) is redundant (F4) |
| `position_events` (398K, 902 MB, L269) | **6** | 3 (+1 partial) | ~62 MB | highest b-tree COUNT per insert: 3 constraint (event_id PK, idempotency_key, position_id+seq) + `position_type_sequence` + `position_phase_after_sequence` + partial `settled_env_position_sequence`; all appear justified |
| `market_price_history` (657K, 142 MB, L72) | 5 → **4 after F3** | 4 → 3 | ~296 MB (> table) | includes the exact-duplicate `token_recorded` (F3); indexes are 2× the table size |
| `decision_log` (190K, 8.16 GB, L14) | 1 | 1 | 5.4 MB | low index-amp but avg row ~42 KB, max 1.15 MB payload (L14 mx_payload 1,146,549) — row-size, not index, is the cost |
| `token_price_log` (217K, 55 MB, L4) | 1 | 1 | 24.8 MB | fine |

Top write-amplification concerns: (1) `executable_market_snapshots` maintains 5 b-trees per capture
on the busiest table in the system; (2) `execution_feasibility_evidence` carries ~10.3 GB of index
(more than half its 20.4 GB) across two token-leading secondaries that are near-duplicates in lead
column; (3) `position_events` maintains 6 b-trees per append (all justified). None strictly exceed
the >5 non-constraint-secondary threshold, but `executable_market_snapshots` (4 secondary × multi-GB)
and `execution_feasibility_evidence` (near-duplicate token indexes) are the two to carry into the
remediation packet.

---

## Reclaim summary (drops only — no ANALYZE, no VACUUM here)
- `idx_book_hash_transitions_market_time` → ~955 MB (F4)
- `idx_market_price_history_token_recorded` → ~87.8 MB (F3)
- `idx_venue_command_events_command` → ~0.17 MB (F4b)
- Total immediate index reclaim ≈ **1.04 GB**, plus one fewer b-tree per insert on three append
  tables. Space reclaim requires an offline VACUUM (out of scope; disk is 87% full — sequence and
  co-tenant safety belong to the remediation packet).
