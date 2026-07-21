# probe_measurements — G3/G4/G5/G6 + posteriors(probe-A 交件 + team-lead spot-check)

来源:子代理 probe-A(sonnet),只读探针,2026-07-21。

## Team-lead spot-check(独立复算,同 mode=ro 安全 pattern)
- `synchronous` = **2 = FULL** ✓(独立确认——这是最意外的纠正,见 G3)
- `executable_market_snapshots max(rowid)` = 10,327,**364**(probe-A 报 10,327,315;差 49 = 24/7 daemon 两读之间活写入,一致非缺陷)✓
→ probe-A 数字可信。

## 更新 FINDINGS 的三条纠正 + 一个新目标(team-lead 记)
1. **G3 durability 纠正(FINDINGS F6/G3 需改)**:此前假设 synchronous=NORMAL(WAL 默认);probe-A 实测 = **FULL**(venv SQLite 3.53.2 未编译 SQLITE_DEFAULT_WAL_SYNCHRONOUS override,落 general FULL)。→ daemon 每次 commit 确实 fsync,对 **OS 崩溃**安全(比想象好)。但 `fullfsync`/`checkpoint_fullfsync` = OFF → macOS 上 plain fsync 不刷盘控制器 volatile cache → **断电仍可丢最近已提交事务**。W1 耐久分级要显式设 `fullfsync=ON`(至少对 trade-authoritative 库)。
2. **G5 纠正(比 critique 说的更强)**:"planner blind 是 per-DB 非普遍" → **实为近乎普遍**。trades=0 stat;forecasts 唯一 stat1 行是**孤儿**(指向不存在的 `ensemble_snapshots_v2`,对现查询计划零效果);world 有 stat1+stat4 但**只覆盖 opportunity_event_processing 一张表(40 中的 1)**,所有巨型热表零统计。→ 三库对本审计关心的大热表全部 planner-blind。
3. **calibration_pairs 有 DELETE(learning-mart 分类的佐证)**:max(rowid)=81.3M 但 cells=51.28M(**反向**,唯一如此的表)——AUTOINCREMENT 不复用 rowid,说明周期性 recalibration/refit **重建表**(删了 ~30M rowid)。真行数 UNMEASURED(≤51.28M 估计,硬上界 81.3M)。这坐实 calibration_pairs 是可重建 mart,不是不可变 ledger。
4. **新 W2 目标(E1-E8 全漏)**:forecast_posteriors 的 `provenance_json` 占行 96-99%,其内 `q_bootstrap_samples_by_bin`(每 bin 400 个原始 float × 7-10 bins)占 90-93%——**非编码病,是原始未压缩 bootstrap draws 塞进"provenance"列**。剥离/外置这一字段 → 3.30GB 表砍 ~90% 到 ~5-8KB/行。干净高值。

**命名陷阱确认**(stray-DB 危险):`zeus-trades.db`/`zeus_world.db`/`zeus_forecasts.db`(错分隔符)是 0 字节诱饵,错拼**静默打开空库**不报错——W4 幽灵库审判的实锤。

---

（以下 probe-A 原始交件，未改动）

# probe_measurements

All queries share the connection preamble (safety law): `sqlite3.connect('file:<path>?mode=ro&cache=private', uri=True, timeout=0.25, isolation_level=None)` → `PRAGMA query_only=ON; busy_timeout=250; cache_size=-16384; mmap_size=0`. Live DB files (mtime today, non-zero): `zeus_trades.db` (underscore, 101,023,313,920 B), `zeus-world.db` (hyphen, 90,076,983,296 B), `zeus-forecasts.db` (hyphen, 42,914,000,896 B). Naming trap: `zeus-trades.db`, `zeus_world.db`, `zeus_forecasts.db` are 0-byte decoys — wrong separator silently opens an empty DB.

## G4 — Corrected row counts (dbstat `cells` vs true `max(rowid)`)

Schema check: none of the 8 tables are `WITHOUT ROWID` (5 use TEXT/`*_id` PK not aliasing rowid; `decision_log`+`calibration_pairs` are `INTEGER PRIMARY KEY AUTOINCREMENT`, aliasing rowid). `max(rowid)` valid for all 8. EXPLAIN QUERY PLAN confirmed `SEARCH` (b-tree descent), never `SCAN` — O(log n) even on the 43 GiB table.

| DB | table | census `cells` | true `max(rowid)` | overcount | query |
|---|---|---:|---:|---:|---|
| trades | executable_market_snapshots | 17,989,157 | **10,327,315** | 1.74× | `SELECT max(rowid) FROM executable_market_snapshots` |
| trades | execution_feasibility_evidence | 30,534,122 | **25,582,518** | 1.19× | `SELECT max(rowid) FROM execution_feasibility_evidence` |
| trades | decision_log | 190,032 | **116,969** | 1.62× | `SELECT max(rowid) FROM decision_log` |
| world | opportunity_events | 24,737,537 | **17,902,718** | 1.38× | `SELECT max(rowid) FROM opportunity_events` |
| world | no_trade_regret_events | 1,002,301 | **712,980** | 1.41× | `SELECT max(rowid) FROM no_trade_regret_events` |
| world | execution_feasibility_evidence | 15,770,351 | **12,975,290** | 1.22× | `SELECT max(rowid) FROM execution_feasibility_evidence` |
| world | decision_certificates | 2,093,719 | **1,346,474** | 1.55× | `SELECT max(rowid) FROM decision_certificates` |

(7 above = 2 combined invocations.) `decision_log` AUTOINCREMENT: sqlite_sequence.seq=116,970 vs max(rowid)=116,969 — 1-row live-INSERT drift, expected.

**calibration_pairs — breaks the assumption, UNMEASURED exactly.** max(rowid)=**81,314,490** (corroborated by sqlite_sequence.seq=81,314,490). Census cells=51,282,798 — **lower** than max(rowid), opposite direction. AUTOINCREMENT so deleted rowids never reused; gap = 0..~30M rowids issued then deleted (periodic recalibration/refit rebuild, consistent with `causality_status`, `authority ∈ {VERIFIED,UNVERIFIED,QUARANTINED}` columns). count(*) forbidden at 11.96 GiB; can't verify whether census cells is leaf-only proxy. **Both bounds: true rows ∈ (≤51.28M best estimate, hard upper bound 81,314,490), UNMEASURED exactly.**

## G3 — Durability truth

`rg -i 'synchronous|fullfsync|wal_autocheckpoint|mmap_size' src/state/` → only "synchronous" hit is a docstring false-positive at `db_writer_lock.py:623`. **Zero hits** for `PRAGMA synchronous`, `fullfsync`, `checkpoint_fullfsync`, `wal_autocheckpoint` in `src/state/`. Repo-wide, `PRAGMA synchronous` set only in 3 one-off ops scripts (`build_ft_staging_db.py:138`, `promote_platt.py:121,124`, `promote_calibration.py:128,131`, all NORMAL, all outside the daemon path). `db.py` sets `journal_mode=WAL` at 262/1689, never sets `synchronous` in any of `_connect()` (234), `_connect_read_only()` (298), `get_connection()` (~1680).

**Measured, not assumed:** `SELECT (SELECT * FROM pragma_journal_mode()),(SELECT * FROM pragma_synchronous()),sqlite_version()` on a `_connect_read_only()`-mirror → trades `["wal", 2, "3.53.2"]`, world `["wal", 2]`. **synchronous = 2 = FULL, not NORMAL.** venv SQLite 3.53.2 not compiled with SQLITE_DEFAULT_WAL_SYNCHRONOUS override → falls to SQLITE_DEFAULT_SYNCHRONOUS=FULL. db.py never touches it, so the live daemon runs FULL. (venv 3.53.2 ≠ system CLI 3.51.2 corruption-window build.)

**macOS verdict.** fullfsync/checkpoint_fullfsync 0 hits → OFF. synchronous=FULL in WAL does fsync() the WAL after every commit — not lazy. But on Darwin plain fsync() only pushes to the drive controller, does NOT flush the drive's volatile write cache (Apple fsync(2) man page recommends F_FULLFSYNC). fullfsync/checkpoint_fullfsync opt into F_FULLFSYNC; both OFF → every "durable" commit only as durable as drive cache retention across power loss. Net: FULL protects against OS-level crash (same power cycle) but **not** power loss — recently-committed trade-authoritative transactions can still vanish on power loss, purely from unset fullfsync.

## G5 — Planner statistics reality

`SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sqlite_stat%'`: trades → `[]` (zero, planner-blind confirmed); world → `sqlite_stat1`+`sqlite_stat4`; forecasts → `sqlite_stat1` only.

world `SELECT tbl,idx,stat FROM sqlite_stat1` → only 2 rows, both `opportunity_event_processing`. `sqlite_stat4` GROUP BY tbl → `[["opportunity_event_processing", 48]]`. World's ~40 objects, **exactly one** (2.34 GiB) has stats; the giants (opportunity_events 30GB, no_trade_regret 11.2GB, execution_feasibility 10.8GB, decision_certificates 3.1GB) have zero.

forecasts `count(*) FROM sqlite_stat1` → 1, but that row is `tbl='ensemble_snapshots_v2'`, `idx='sqlite_autoindex_ensemble_snapshots_v2_1'` — and `SELECT name FROM sqlite_master WHERE name='ensemble_snapshots_v2'` → `[]`, table does NOT exist. **Orphaned stat1 row** (pre-rename leftover), zero effect on any current plan.

**Correction:** planner-blindness is closer to UNIVERSAL than per-DB. trades blind (0), forecasts blind-in-practice (1 orphaned row), world blind for 39 of ~40 objects. No DB gives usable cardinality for any large hot table.

## G6 — mmap mapping

`rg -i mmap_size src/state/db.py` → set at 281/321/1703 from `mmap_bytes = int(os.environ.get("ZEUS_DB_MMAP_BYTES", str(32*1024**3)))`. Default **32 GiB** (nothing sets the env var). SQLite mmaps file offset 0..mmap_size (front of file). Append-heavy b-tree: low offsets ≈ old/cold, high offsets ≈ new/hot. Past 32 GiB the map covers only the early cold portion; the hot append tail uses ordinary pager pread/pwrite.

| DB | file size | % mapped by 32 GiB |
|---|---:|---:|
| trades | 94.08 GiB | 34.0% |
| world | 83.89 GiB | 38.1% |
| forecasts | 39.96 GiB | 80.1% |

trades & world have the majority of bytes — and essentially all hot activity — outside the mapped window.

## Posteriors anatomy (forecast_posteriors)

`PRAGMA table_info` → 26 columns, 5 JSON: q_json, q_lcb_json, q_ucb_json, dependency_source_run_ids_json, provenance_json. Last-3-row length():

| rowid | q_json | q_lcb | q_ucb | provenance_json | dep_run_ids |
|---:|---:|---:|---:|---:|---:|
| 54085 | 921 | 927 | 915 | **70,010** | 276 |
| 54086 | 964 | 963 | 962 | **100,444** | 273 |
| 54087 | 972 | 983 | 960 | **103,088** | 273 |

provenance_json = 96-99% of row JSON. Decoded locally: `q_bootstrap_samples_by_bin` dominates — 62,890/93,536/96,146 B = **89.8/93.1/93.3%** of provenance_json; next key (`bayes_precision_fusion`) only ~4KB. It's a dict keyed by market-bin question strings, each value a list of **exactly 400 raw floats** (bootstrap draws), 7-10 bins/row, ~8.3-9.3KB/bin as plain JSON numbers. Double-encode check (json.loads on ≥20B `{`/`[` strings + base64 heuristic on ≥100B): **no hits** — not an encoding pathology, raw uncompressed volume.

**W2 sizing:** everything except q_bootstrap_samples_by_bin totals ~5-8KB/row. Externalizing/dropping it cuts ~70.8 KiB/row → ~5-8 KiB/row ≈ **90% reduction** on the 3.30 GiB table.
