# FINDINGS — DB First-Principles Audit (synthesis verdict)

Created 2026-07-21 · Synthesis judge (main thread integrator) · read-only packet, **nothing implemented here**.
Inputs: `PLAN.md`, `consult_gpt56_answer.md` (GPT-5.6 Pro), 9 lane findings, `completeness_critique.md`, `census_tables.md` / `census_raw.jsonl`, AGENTS §5.
Every claim traces to a lane file (`findings/*.md`) or the consult; where a lane failed or data is partial it is said so.

Source tags used in the table: `Wn` = lane-verified with file:line/census; `consult+Wn` = consult claim verified against code by a lane; `consult-only` = consult claim not independently code-verified in this audit; `Gn` = surfaced by the completeness critique.

---

## 1. Executive verdict

- **Scale:** fleet **233.65 GB** across three live 24/7-traded WAL DBs (trades 100.83 / forecasts 42.87 / world 89.95); disk **87% full, 115 GiB free**; census **100% complete (799/799 objects, 0 timeouts)** — no re-polling needed.
- **Runway:** **ENOSPC in ~34 days** at the measured lower-bound growth of 3.38 GiB/day (6 sampled tables), ~17 days at 2×; `trades.decision_log` alone is **62% of that growth** (2.34 GB/day) and is mislabeled a droppable ghost — resolving it roughly triples the runway to ~95 days.
- **The single most urgent risk is a fact we never captured:** the production `sqlite_source_id()`. Builds through **3.51.2 sit in a documented WAL-reset corruption window** (fixed 3.51.3 / backported 3.50.7, 3.44.6); PLAN's CLI reading (3.51.2) is *inside* it, and the census just ran live `dbstat` under exactly the concurrent write+checkpoint topology the corruption gate exists to fence. Until the daemon's linked build is on record, the P0 ordering is provisional.
- **Standing correctness/recoverability holes that do not depend on the version question:** (a) **no tested off-machine backup** — a disk/controller failure loses all 218 GB of canonical state today; (b) **16 chain-confirmed-settled positions have no row in the canonical `settlement_outcomes` truth table**, up to 18 days stale; (c) a **live `database is locked` storm** (13,749 + 10,004 + 3,906 errors, recurring *during* the audit) from write-intent locking fragmented across four incompatible schemes.
- **Error surface is lock-only:** no corruption, no `SQLITE_IOERR`, no readonly-write, no disk-I/O fault despite the 87%-full disk (W5 corrected the prior draft's "zero-BUSY" substring artifacts).

---

## 2. Ranked findings

Ordered **money-path correctness > disk-runway > performance > hygiene**. Quadrant per PLAN §2: **KK**=Q1 known-known · **KU**=Q2 known-unknown · **UK**=Q3 known-but-unraised · **UU**=Q4 unknown-unknown. No fix is implemented — "Fix direction" is direction only.

### Tier A — money-path correctness / recoverability

| ID | Quad | Finding | Headline evidence | Fix direction | Blast radius | Owning surface | Source |
|---|---|---|---|---|---|---|---|
| F1 | KU | Production SQLite build never captured; may be in the WAL-reset corruption window | PLAN §1 CLI **3.51.2** (in window); only the audit venv 3.53.2 is on record — not the daemon's linked lib; `source_id` never read from any process | Capture `sqlite_source_id()`+`compile_options` from every daemon interpreter (main/ingest/price-channel/riskguard/deploy); gate startup+maintenance on an approved build; upgrade before any deliberate checkpoint concurrency | Fleet-wide corruption possibility on all 3 WAL DBs under concurrent write+checkpoint | deploy/runtime (`deploy_live.py`, LaunchAgents) | consult P0#1 + G1 |
| F2 | KK | No tested off-machine backup for any of the 3 live canonical DBs | `tmutil` = "Failed to mount destination"; only same-disk **purgeable** APFS snapshots; the one raw-copy path omits `-wal`/`-shm` and is unscheduled (`scripts/source_contract_auto_convert.py:1531-1550`) | SQLite backup API → external volume, one DB at a time; restore-drill verifying role/schema/watermark/invariants; retire the WAL-omitting `shutil.copy2` path | Disk/controller failure or corruption loses **all 218 GB** of canonical state, no recovery | ops/deploy | W13-F1 + consult P0#10 |
| F3 | KK | 16 chain-settled positions missing from canonical `settlement_outcomes`; cross-file crash-atomicity invariants never inventoried | 16 of 257 `chain_state='synced' phase='settled'` `position_current` rows have **no** `settlement_outcomes` row (Paris 18 d, HK 8 d stale); `WriteCoordinator` honestly refuses cross-file atomicity | Enumerate multi-DB settle/exit write invariants; classify must-be-atomic vs reconcilable (co-locate or durable outbox); root-cause whether F3 is a partial-commit or a writer-bug on a specific exit route | Missing rows in the settlement truth table feed P&L + calibration; systemic recoverability gap if partial-commit | settlement-semantics (`harvester*.py`) | W13-F2 + G8 + consult P0#2/#6 |
| F4 | KK | Live `no such table: main.*` cross-schema fail-soft; money-path blast radius undetermined | 157 in `zeus-live.err` (`main.ensemble_snapshots`), 55 in `post-trade-capital.err`, + `main.settlement_outcomes`/`calibration_pairs`/`alpha_overrides`; those tables live on forecasts.db, bare ref resolves against a trade-rooted `main` | ATTACH-caller inventory + CI fail on unqualified DML in attached connections; classify each emitting site as (a) logging fail-soft or (b) money-path read returning empty and changing a decision | (b) is a hard correctness bug; (a) is noise — split is unknown today | src/state provenance/cross-DB | W5 (punted) + G2 + consult P0#8 |
| F5 | KK | Registry drop-date labels mislabel **live** money/audit-path tables as droppable ghosts | `settlements`(forecasts) "dead 2026-06-03" but written every cycle (`harvester_truth_writer.py:747`, `harvester.py:1789`); `decision_log`(trade) "drop after 2026-08-09" = **8.16 GB live**, world canonical copy = **0 rows**; `chronicle` same pattern | Re-grep runtime writers for **every** PR-S4b "ghost drop-after-date" table before any drop; correct registry so canonical = where writes land | An unconditional 2026-08-09 drop deletes the live settlement/P&L audit trail + auction-receipt chain (SEVERE) | `architecture/db_table_ownership.yaml` + settlement | W7 top + W10 |
| F6 | KK | Durability policy implicit: `synchronous` never set; `fullfsync`/`checkpoint_fullfsync` never set (macOS power-loss hole) | grep `synchronous`/`fullfsync`/`checkpoint_fullfsync` in `src/` → **0 hits** → default `FULL` under WAL; on Darwin `fsync()` does not flush the drive cache, only `F_FULLFSYNC` does | Define durability by data class; `fullfsync=ON` for the authoritative trade DB, backed by a forced-power-loss recovery test (not just `kill -9`); deliberate NORMAL for reconstructible telemetry | Power loss can lose/corrupt committed money-path transactions (W5-1's "corruption-safe, no data-loss" is wrong for power loss) | `src/state/db.py` connection factory | W5-1 + G3 + consult P0#9 |
| F7 | KK | Write-intent locking fragmented across **4** incompatible schemes on the same 3 DBs → live `database is locked` storm | `.writer-lock.live` / `.writer-lock.bulk` / unified `.writer-lock` / in-process `threading.Lock`; cross-scheme writers do not exclude, fall through to `busy_timeout`; **13,749+10,004+3,906** errors, recurring 2026-07-21 01:12:57 during audit; `WriteCoordinator` is **live, not a skeleton** (`write_coordinator.py:135-143`) and added the 3rd namespace | Complete the WriteCoordinator cutover: one gate per DB shared by LIVE+BULK + in-process mutex + full writer enrollment; fenced restart removing the split; prefer one writer actor/queue per DB | Availability: market-backpressure queue pinned at its 1000 cap; latency. Not corruption | `src/state/write_coordinator.py` + `db_writer_lock.py` | W5 headline + consult P0#4 |
| F8 | KK | Writer-lifecycle defects: env-class race, `interrupt_main` wrong-thread, writable RO-intent handles, fail-open `rwc` create | `os.environ['ZEUS_DB_WRITE_CLASS']` process-wide (`db_writer_lock.py:992` vs docstring `:975-977`); `interrupt_main()` no main-thread guard (`db_writer_lock.py:493`) under ingest ThreadPool (`tigge_pipeline.py:379`); `connection_pair.py:261-265` RW despite "RO intent"; `_connect` `rwc`+`mkdir` (`db.py:249/259`) | contextvar/arg for write class; `Connection.interrupt()` on the exact connection + cooperative cancel; `mode=rw` + explicit provisioning + path-identity markers (application_id/inode/role/schema-hash) | Latent (write_class only counts today) but `interrupt_main` can shut the ingest daemon; fail-open create is the plausible source of F4 | src/state | W5 CLAIM2/3/8 + consult P0#5/#6/#7 |
| F9 | KK | Bare `get_connection()` fail-opens to schema-only `zeus.db` (1 MB, 0 data rows) | `db.py:1683` defaults to `ZEUS_DB_PATH`; ~15 manual scripts call it bare (`scripts/baseline_experiment.py:251` …); none cron/launchd-scheduled; **live daemon path verified NOT affected** (both live getters resolve canonical) | `mode=rw` low-level; explicit-path retrofit on the 15 sites before the documented "Phase 4" removal | Manual-script only: a human running one gets a silent near-empty read, not the canonical DB | `src/state/db.py` + scripts | W4 |
| F10 | KK | Checkpoint telemetry false-green; intended `TRUNCATE` shipped as `PASSIVE`; no forecasts backstop | PASSIVE's `busy` field is always 0 (`db.py:744/775` → `main.py:5437/5470`), so the reader-pinning WARNING (`main.py:5442-5449/5475-5480`) is dead code; comment says TRUNCATE (`main.py:6948/6959`), impl is PASSIVE (`db.py:742/774`) → trade WAL floats **95-373 MB** (the 2026-06-16 810 MB regime) | Alert on backlog (`log-checkpointed` frames), slope, oldest-reader age, time-to-reserve; one checkpoint owner per DB; add forecasts backstop; version-gate NOOP mode | Hidden WAL growth → the 810 MB incident class; forecasts entirely unguarded | `main.py` + `db.py` checkpoint path | W5-2/W5-3 + consult P1#11 |
| F11 | KK | `forecasts.calibration_pairs` has received **0 new rows in 51 days** while settlements are fresh through today | last row 2026-05-31; `settlements` fresh to 2026-07-21; `harvester.py:2459` still calls `add_calibration_pair` but something gates it off | Route to the calibration/Platt-boot owner: is the retraining loop training on data that stopped 51 days ago? | Calibration may be stale — money-path-adjacent | calibration | W10 + G14 |
| F12 | KK | `market_topology_state` (world) and `market_price_history` (trades) silently dead since ~2026-05-28, still referenced live | `market_topology_state` all 3,938 rows `status='CURRENT'` but MAX(recorded_at)=2026-05-28 → root cause of F4's 97% condition_id orphan rate; `market_price_history` newest rows are May, still referenced by 7 `src/` modules (likely superseded by `token_price_log`, unconfirmed) | Route to a dead-writer/provenance owner: confirm supersession, repoint or retire the 7 readers, drop the frozen tables | Readers of `market_topology_state`/`market_price_history` silently get May data as if current (fail-soft orphan class) | ingest/provenance | W13-F3/F5 |

### Tier B — disk runway

| ID | Quad | Finding | Headline evidence | Fix direction | Blast radius | Owning surface | Source |
|---|---|---|---|---|---|---|---|
| F13 | KK | No scheduled physical retention anywhere; 6 append tables ≥93 GB grow unbounded | `prune_terminal_opportunity_events.py` is the ONLY physical-delete script and is **not scheduled** (`db_writer_lock.py:679`); `opportunity_events` regrown to 44.3 GB since the one-off 2026-06-16 prune; mark-not-delete is architectural | Epoch rotation (bounded active epoch + sealed read-only history, O(1) whole-file retention) — **not** a giant live DELETE (consult E); schedule/replace the mark-only prune | ENOSPC → SQLITE_FULL → RED is deterministic, only the date is open | `src/events` (EDLI) + retention | W10 + W7(d) + consult |
| F14 | KK | `decision_log` = 62% of fleet growth (2.34 GB/day), mislabeled droppable ghost | 8.16 GB, ~115K live rows, fresh as of census; `mx_payload` 1.15 MB (auction-receipt JSON); world "canonical" copy = 0 rows | Confirm ownership (genuine vs accidental dup of `decision_events`); route to epoch rotation; resolve before the 2026-08-09 drop date | Dominant growth lever; wrong drop deletes live history (overlaps F5) | settlement / `global_batch_runtime.py` + registry | W10 |

### Tier C — performance / storage amplification (all remediation belongs on an external clone)

| ID | Quad | Finding | Headline evidence | Fix direction | Blast radius | Owning surface | Source |
|---|---|---|---|---|---|---|---|
| F15 | KK | Index redundancy: ~**1.04 GB** reclaimable from 3 drop-only redundant indexes | `idx_book_hash_transitions_market_time` **955 MB** (exact PK prefix, census L112); `idx_market_price_history_token_recorded` **87.8 MB** (byte-identical to UNIQUE autoindex, L73≡L75); `idx_venue_command_events_command` 172 KB | `DROP INDEX` (clone-verified no plan regression); physical reclaim needs external VACUUM | Low; one fewer b-tree per insert on 3 append tables | schema/migrations | W9-F3/F4/F4b |
| F16 | KK/KU | Missing statistics: trades has **no** `sqlite_stat1`; forecasts 1 cell; **world has `stat1`+`stat4`** — "planner blind everywhere" is wrong per-DB, and world's hot plans are unaudited | census: world `sqlite_stat1` (2 cells) + `sqlite_stat4` (50 cells); trades none; forecasts 1 cell; W9 covered only trades+forecasts | Vetted fixed `stat1` (STAT4 from clone) rolled one DB at a time with measured rollback, or bounded `PRAGMA optimize` — **never live full ANALYZE**; audit world EDLI hot plans; characterize world's existing stats | Parameter-sensitive plan failures on the 46 GB / 20 GB tables; world's live EDLI loop unexamined | query path + remediation-clone | W9(c) + G5 |
| F17 | KK | Per-tick query landmines on the 46 GB `executable_market_snapshots` | `exit_lifecycle.py:1771/:1894` forces a temp B-tree (`snapshot_id` tiebreak + non-sargable `datetime(freshness_deadline)`); `snapshot_repo.py:555` fallback does `SELECT *` with **no LIMIT** | Drop/add the tiebreak to the index; store freshness as sargable ISO text; add LIMIT + column projection | Per-monitor-tick, bounded now, scales with a token's snapshot history | `exit_lifecycle.py` + `snapshot_repo.py` | W9-F1/F2 |
| F18 | KK | Write amplification on the busiest tables | `executable_market_snapshots` = 5 b-trees/insert (~7.99 GB index); `execution_feasibility_evidence` carries ~10.26 GB index (>half its size) in 2 near-duplicate token-leading secondaries | Clone-replay drop of one near-dup token index gated on ≥15-20% WAL improvement, no p99 read regression (consult §449) | WAL/insert cost on the two highest-ingest tables | schema | W9(e) |
| F19 | KK | Decode/serialization: `orderbook_depth_json` = 46.3 GB / **55% of trades DB**, re-parsed at 10+ sites; `ActionableTradeCertificate` 93 KB/row triple-embeds economics; ensemble `provenance_json` double-encodes a native column | measured zlib-6 ratios 4.43×/7.12×; the working `zlib+base64+canonical-json-v1` pattern exists in-repo but is applied only to auction receipts. **Row-count caveat (G4): true rows ≈10.32M, not the 17.99M `cells`** | Decode-once-and-share; array-of-arrays vs array-of-objects; apply the existing zlib pattern where a larger sample confirms the ratio; strip the triple-embed + double-encoded column | Decode CPU per cycle + 55% of trades bytes; extrapolated savings are HYPOTHESIS until a larger sample | `snapshot_repo.py` + `decision_kernel` | W6 (+ G4) |
| F20 | UK | 32 GB mmap maps the **cold** end of 90 GB+ append DBs | mmap maps the first N bytes = lowest/oldest rowids; the hot append tail is unmapped; prior K3 cold-cache scar (PLAN Q1.8) | A/B `mmap_size=0` vs 32 GB on an external clone (hot-query p99, page faults, RSS, commit tails) | Possibly inverted benefit on the hot path | `db.py` + remediation-clone | G6 + consult P1#16 |

### Tier D — hygiene / registry truth

| ID | Quad | Finding | Headline evidence | Fix direction | Blast radius | Owning surface | Source |
|---|---|---|---|---|---|---|---|
| F21 | KK | Unregistered live table + undocumented ghost class + dead rename artifacts on forecasts.db | `day0_hourly_vectors` **21.2 MB/15,480 rows** live (writes `day0_hourly_vectors.py:396`, reads `:474`) but **absent from the ownership yaml**; 13-table world-schema ghost class on forecasts.db; 4 dead rename artifacts (`readiness_state_legacy_no_ready_…Z` alone 5.3 MB) | Register `day0_hourly_vectors`; audit+register-or-drop the 13 ghost class; DEAD_DELETE the 4 artifacts | Registry invisibility (AGENTS §4): the boot `assert_db_matches_registry(FORECASTS)` is silently permissive | registry + forecasts schema | W8 |
| F22 | KK | Registry rot + dead artifacts + unbounded audit log | ~30 stale manifest entries (17 corroborated) for tables already dropped 2026-06-09; `risk_state.db` 390 MB unbounded tick-log (113,029 rows, no retention); `cycle_phase_study.db` 477 MB completed-study delete candidate; `edli_live_cap_*` retired-but-classed-active | Registry cleanup PR; risk_state retention; delete `cycle_phase_study.db`; reclassify `edli_live_cap_*` | Cosmetic/hygiene; 477 MB best-value single reclaim | registry + ops | W4/W8/W10 |

---

## 3. Contradictions (both numbers on record)

1. **Row count via `cells` vs `max(rowid)` (G4).** W6/W7/W8 read census `cells` as row counts — `executable_market_snapshots` **17,989,157**, `opportunity_events` **24,737,537**. W10 alone used `max(rowid)`: true rows ≈ **10.32M** and **17.9M** (the one-per-row indexes carry 10,321,769 / 17,896,630 cells). `cells` over-counts ~74% / ~38% on high-`mx_payload` overflow tables. **Adopt `max(rowid)` fleet-wide** — W6's decode-per-cycle multipliers and W7/W8's row scales are inflated.
2. **"Planner blind everywhere / never ANALYZE'd" (PLAN §1, W9) vs census (G5).** world.db carries `sqlite_stat1` (2 cells) **and** `sqlite_stat4` (50 cells); trades has none; forecasts has 1 cell. The blindness claim is **per-DB**, not fleet-wide, and world's stats (why only world? stale/partial?) were never characterized.
3. **`WriteCoordinator` "skeleton" (prior W5 draft + consult L33) vs W5 final: REFUTED.** It is a complete 486-line live implementation, the write gate for ingest/price-channel/substrate since 2026-06-27 — and it added a **third** lock-file namespace instead of replacing the split. The consult's "complete the skeleton" is right in direction, wrong on current state.
4. **World.db byte coverage: W7/W8 "UNMEASURED" vs W10/W13 complete census (G7).** W7/W8 ran on a world-less partial census and left world sizing open; the final census has all 362 world objects. Now reconciled: `settlement_outcomes`(forecasts) = 0.02 GiB/13,477 cells (was UNMEASURED); world `decision_log` copy = 0 rows (resolves "which copy is canonical" → trades); position ghost shells confirmed 0 rows; the 13-table forecasts ghost class is measurable (e.g. `data_coverage` 299 KB). The synthesis uses the complete census, not the stale "world unmeasured" labels.
5. **"FULL is corruption-safe, no data-loss" (W5-1) vs macOS reality (G3).** True for process crash, **false for power loss** on Darwin because `F_FULLFSYNC` is off. Corrected in F6.
6. **Free-space drift:** PLAN 119 GB → W13 117 GB → W10 115 GiB over the audit window — normal live drift, not attributable to the read-only audit; use 115 GiB for the runway math.
7. **Trade-WAL snapshots:** W5 95→230→373 MB, W9 230→128 MB, W13/W10 288 MB — not a contradiction, this *is* the never-truncating PASSIVE oscillation of F10 seen at different instants.

---

## 4. Open gaps surviving as next probes

**Blocking the P0 ordering (must close before any deliberate checkpoint/maintenance concurrency):**
- **G1 — production `sqlite_source_id()`** from every daemon interpreter (read-only, doable now). Reorders the whole P0 list.
- **G2 — F4 money-path blast-radius split:** `rg -n "ATTACH " src/` inventory + classify each `no such table` emitter as logging-fail-soft vs money-path-empty.

**Material, before the corresponding remediation packet:**
- **G8 — cross-file crash-atomicity invariant inventory** on the settle/exit path; whether the 16 F3 gaps are partial-commit vs writer-bug.
- **G5 — world.db hot-query plans** (EDLI `opportunity_events`/`opportunity_event_processing`/`no_trade_regret_events`) + characterize world's `stat1`/`stat4`.
- **G6 — 32 GB mmap A/B** on an external clone.
- **G9 — overflow-page ratio per table** (`pagetype` aggregation) + WITHOUT-ROWID inventory + `application_id`/`user_version` read (0 hits today) — **external clone only**; detailed live `dbstat` on the 94 GB file is banned by Safety Law and the consult §B.
- **G13 — macOS in-process `flock` thread exclusion:** read `db_writer_lock.py:89-140` to confirm fresh-fd-per-acquisition (would mean two same-process LIVE threads do not exclude at that layer).

**Minor / read-only-derivable:**
- **G10** census live-impact (hot-query p99, IO latency) during any future detailed pass — unmeasured (only WAL size was guarded).
- **G11** lock-wait distribution / connects-per-cycle / how often the 30 s write `busy_timeout` is fully exhausted — the `database is locked` bursts are the 30 s ceiling blowing; timestamp-parseable from the `.err` logs read-only.
- **G12** disambiguate F10's never-truncating WAL between PASSIVE-not-TRUNCATE and a long-lived unfinalized read cursor pinning the floor (audit generator-style cursors over the trade DB).
- **Census coverage:** world.db was measured in aggregate only; no per-`pagetype` (overflow/leaf/internal) breakdown exists fleet-wide (G9).

**Routed follow-ups (correctness findings the census exposed, not storage findings):** F11 (`calibration_pairs` frozen 51 d) → calibration-boot owner; F12 (`market_topology_state`/`market_price_history` dead-writer) → provenance/dead-table owner; F3 (16 missing settlements) → settlement-semantics owner.

---

## 5. Implementation queue

Six packets, ordered by evidence-gated urgency and sized to AGENTS §5 change-control (`live` accepts commits only by hot-fix cherry-pick or milestone PR; STOP-AND-PLAN before `src/state/**` truth paths, schema, control surfaces). The consult's own execution order is the input; adapted here — nothing below is executed in this packet.

**PKT-1 — Runtime-build gate + audit freeze.** *(Investigation + startup gate; hot-fix if a vulnerable build is found.)*
Capture F1's `sqlite_source_id()`/`compile_options` from every production interpreter; gate startup+maintenance on an approved build; freeze heavy live audit / VACUUM / full ANALYZE / manual blocking checkpoints.
**Evidence gate:** source_ids on record and compared to the 3.51.3/3.50.7/3.44.6 fix commits; if vulnerable, runtime upgrade lands (hot-fix) before any other packet touches checkpoint concurrency.

**PKT-2 — Tested external backup + restore drill.** *(Milestone PR — new capability.)*
SQLite backup API → external volume, one DB at a time; retire the `-wal`-omitting `shutil.copy2` path (F2). This clone is also the substrate for all Tier-C perf work.
**Evidence gate:** a separate process opens the restored DB and verifies role/schema/sequence-watermark/business invariants; backup completion, restart count, restore time recorded.

**PKT-3 — Connection-factory correctness.** *(Milestone PR — `src/state/**` truth path, STOP-AND-PLAN.)*
Path-identity fail-closed (`mode=rw` + explicit provisioning, no low-level `mkdir`, application_id/inode/role/schema-hash markers, journal_mode=wal check, reject unexpected siblings) covering F8/F9; ATTACH-caller inventory + unqualified-DML CI gate and root-cause of F4; durability pragmas by data class incl. `fullfsync=ON` for the trade DB (F6).
**Evidence gate:** F4's every emitter classified logging vs money-path; zero unqualified DML in attached connections; forced-power-loss recovery test on the trade DB passes.

**PKT-4 — Writer unification + checkpoint telemetry.** *(Milestone PR — control surface, STOP-AND-PLAN.)*
Complete the WriteCoordinator cutover (one gate per DB, LIVE+BULK shared, in-process mutex, full enrollment, fenced restart removing the split) for F7; contextvar write-class + `Connection.interrupt()` for F8; replace F10's PASSIVE false-green with backlog/slope/oldest-reader/time-to-reserve alerts, one checkpoint owner per DB, forecasts backstop.
**Evidence gate:** one business cycle in report-only enforcement with zero unleased writes; `database is locked` rate drops; macOS two-thread `flock` exclusion test (G13) passes; checkpoint alert fires on synthetic backlog.

**PKT-5 — Ownership/registry truth + cross-file atomicity + deferred routing.** *(Milestone PR — governance + settlement.)*
Re-verify every PR-S4b "ghost drop-after-date" table against runtime writers before any drop and correct the registry (F5/F14); register `day0_hourly_vectors`, audit the 13-table forecasts ghost class, DEAD_DELETE the confirmed-dead artifacts and ~30 stale entries (F21/F22); enumerate the settle/exit multi-DB invariants and root-cause the 16 missing settlements (F3/G8); route F11 and F12 to their owners.
**Evidence gate:** no drop lands without a per-table writer-grep proof; F3 cause (partial-commit vs writer-bug) identified.

**PKT-6 — Clone-based disk & performance program.** *(PR series on the PKT-2 clone — the ONLY tier where VACUUM / ANALYZE / index-rebuild / page-size work is permitted.)*
On the external clone, in order: full detailed census + overflow ratios (G9) → planner-stat rollout one DB at a time with rollback (F16) → drop the 3 redundant indexes ~1.04 GB (F15) → query-landmine fixes (F17) → write-amp index consolidation (F18) → mmap A/B (F20) → decode/compression (F19) → **epoch rotation to arrest growth** for the 6 unbounded lanes + schedule/replace the `opportunity_events` prune + delete `cycle_phase_study.db` + `risk_state` retention (F13/F14/F22). Account for APFS snapshot CoW (W13-F9): freed blocks won't show as reclaimed until local snapshots expire.
**Evidence gate:** each index/stat change proven on the clone (≥15-20% WAL/latency improvement, no p99 read regression) before it touches production; epoch rotation routes new appends under a brief writer gate leaving the historical monolith read-only.

**Ordering rationale:** PKT-1→5 close money-path correctness/recoverability (a vulnerable build, an unrecoverable disk failure, missing settlement truth, a live lock storm, and mislabeled-live-drop hazards are all higher stakes than disk or speed); PKT-6 handles disk-runway + performance + hygiene on a clone so the 87%-full live volume and the WAL-reset window are never stressed. The architectural end-state the consult argues for — small hot state in SQLite, unbounded history in sealed epoch files (Parquet/DuckDB for cold analytics) — is PKT-6's epoch-rotation step, not a separate migration; PostgreSQL/analytical engines are considered only after the hot/append split exposes the residual workload.

---

## Addendum 2026-07-21: blocking gaps G1/G2 closed (main-thread probes, read-only)

### G1 CLOSED — production runtime is SAFE; P0 reorder does NOT trigger

| Interpreter | SQLite | In corruption window? | Role |
|---|---|---|---|
| `.venv/bin/python` → homebrew python3.14 (`/opt/homebrew/Cellar/python@3.14/3.14.6`) | **3.53.2** (source_id 2026-06-03 d6e03d8c…, STAT4=on) | **NO** (fix was 3.51.3) | ALL live daemons — every active `com.zeus.*.plist` names `.venv/bin/python`; running `-m src.main` confirmed on homebrew 3.14 |
| miniconda python | 3.51.2 (b270f833…) | YES | only `tigge_mx2t6_download_resumable.py` (GRIB downloader); rg over the script: **zero sqlite/.db references** — never touches canonical DBs |
| system sqlite3 CLI | 3.51.2 | YES | forbidden against live DBs (already law in SAFETY v2; census driver switched to venv reader after first 19 objects) |

PKT-1's "if vulnerable, upgrade first" branch is moot. Residual PKT-1 value: a startup gate pinning approved source_ids so a future interpreter swap cannot silently regress, + keeping the heavy-maintenance freeze until PKT-2's clone exists.

### G2 CLOSED — NOT logging-only; a money-path repair lane is impaired

Root cause: **dangling FK edge**. `PRAGMA foreign_key_list(trade_decisions)` on zeus_trades.db returns `('ensemble_snapshots','forecast_snapshot_id','snapshot_id')`, but local `ensemble_snapshots` was dropped by v1.F20 (2026-05-18; db.py:2068-2070) — canonical table now lives in zeus-forecasts.db, and **a main-schema FK cannot be satisfied by an ATTACHed schema**. With `foreign_keys=ON`, every INSERT/UPDATE against trade_decisions errors `no such table: main.ensemble_snapshots` at statement compile, NULL FK value or not.

Measured impact — `trade_decisions` is FROZEN since **2026-07-02** (max rowid 4645, last ts 2026-07-02T00:10Z), matching both error families:
- `zeus-live.err` (102×): `update_trade_lifecycle` → synthesizer INSERT fails (db.py:10540-10544, cycle_runtime.py:6120 catch). Day0 canonical persist itself is unaffected (bridge fires only after `canonical_day0_written=True`).
- `zeus-post-trade-capital.err` (55×): `Failed to log trade exit` — exit-audit INSERT (db.py:10490-10508) fail-soft swallowed.

Money-path edge: `command_recovery.py:6500` gates **filled-entry position-lot repair** on `EXISTS (SELECT 1 FROM trade_decisions WHERE runtime_trade_id = cmd.position_id …)`. Any position entered after 2026-07-02 without a pre-freeze row can never satisfy the gate → its missing `position_lots` repair is silently skipped. That is an impaired money-path recovery lane, not logging.

Fix direction (NOT implemented here): rebuild `trade_decisions` without the dead FK edge (12-step ALTER or table rebuild), hot-fix-grade given the impaired repair lane; slot into PKT-3 or land ahead of it. Antibody: a startup/CI check that every FK edge in each canonical DB resolves to an existing same-schema table (would also have caught any other v1.F20-era stragglers).
