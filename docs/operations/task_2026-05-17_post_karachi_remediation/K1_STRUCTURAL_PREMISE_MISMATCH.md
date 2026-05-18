# K1 STRUCTURAL SWEEP — Premise mismatch receipt

**Date**: 2026-05-17
**Worktree**: `fix-wave2-k1-structural-sweep-2026-05-17`
**Base commit**: `950eba4fa6` (origin/main HEAD at brief intake)
**Verdict**: brief authority docs missing; 9-of-10 cited findings unverifiable; 1-of-10 confirmed real with live evidence.
**Recommendation**: orchestrator decides scope before substantive edits land.

This document is the orchestrator-visible record that the executor did NOT
fabricate findings to fill a slot count, per
`feedback_grep_gate_before_contract_lock` and
`feedback_zeus_plan_citations_rot_fast`.

---

## 1. Authority artifacts missing

| Brief citation                                                       | Probe                                                            | Status     |
| -------------------------------------------------------------------- | ---------------------------------------------------------------- | ---------- |
| `MASS_TRIAGE_2026-05-17.md` at commit `0beff84ca0`                   | `git show 0beff84ca0:docs/operations/.../MASS_TRIAGE_2026-05-17.md` | does not exist in that commit |
| dir `task_2026-05-17_post_karachi_remediation/`                      | `find docs/operations -type d -name '*post_karachi*'`            | absent on main and on this branch (created empty by this receipt) |
| dir `task_2026-05-16_post_pr126_audit/`                              | `find docs/operations -type d -name '*post_pr126*'`              | absent |
| `RUN_14_track_*.md`                                                  | `find . -name 'RUN_14*'`                                         | no matches anywhere |
| Audit corpus is in `task_2026-05-16_deep_alignment_audit/`           | indexed RUN_3/4/5 + REPORT + FINDINGS_REFERENCE                  | exists, ends at **F20** |

## 2. Finding-ID range mismatch

`FINDINGS_REFERENCE.md` (`task_2026-05-16_deep_alignment_audit/`) indexes F1–F20.
Brief references F19, F46, F48, F63, F71, F81, F82, F83, F84, F103.

| Brief ID | In corpus? | Title match? |
| -------- | ---------- | ------------ |
| F19      | yes        | yes — "Cross-DB market_events_v2 asymmetry" ✓ |
| F46      | no         | no doc anywhere in repo references this ID |
| F48      | no         | n/a |
| F63      | no         | n/a |
| F71      | no         | n/a |
| F81      | no         | n/a |
| F82      | no         | n/a |
| F83      | no         | n/a |
| F84      | no         | n/a |
| F103     | no         | n/a |

Repo-wide grep for `F46\|F48\|F63\|F71\|F81\|F82\|F83\|F84\|F103` (md/txt/json/py): **zero hits**.

## 3. The one verified finding — F19 worsening, live shadow writes

Live DB probe at `2026-05-17T~15:00Z`:

| DB                       | `market_events_v2` rows | `settlements` rows | `settlements_v2` rows |
| ------------------------ | ----------------------- | ------------------ | --------------------- |
| `zeus-world.db`          | 2,112 (F4 stranded residue, frozen since 2026-05-13 16:45Z) | 0 | 0 |
| `zeus-forecasts.db`      | **10,552 canonical**    | 5,599              | 4,016                 |
| `zeus_trades.db`         | **7,964 shadow, growing** | 0                | 0                     |

`zeus_trades.db.market_events_v2` row distribution by `recorded_at` date:
```
2026-05-17: 638   2026-05-16: 638   2026-05-15: 638   2026-05-14: 638
2026-05-13: 726   2026-05-12: 561   2026-05-11: 825   2026-05-10: 1298
```
`zeus-forecasts.db.market_events_v2` over the same window: identical 638/day
since 2026-05-15. **The two DBs are receiving the same Gamma-scan substrate
in lockstep — not "residue", not "frozen", but an active dual-write.**

F19 row count in the audit corpus was 7,326. Today it is 7,964 (+638
overnight). The "shadow" is being fed daily, deterministic schedule.

## 4. The single K-decision (verified, file:line)

K-AXIS: **K1 writer split is incomplete on the substrate path**.
`forecasts.market_events_v2` is the canonical class per
`architecture/db_table_ownership.yaml` (post-K1 split 2026-05-11).
The Gamma-scan substrate writer is still routing through the
`get_connection()` legacy surface which binds to `zeus_trades.db` and
relies on `ATTACH forecasts` for cross-DB resolution. Because the INSERT
is unqualified (`INSERT INTO market_events_v2`), SQLite resolves the
table on the **main DB** (trades), not the attached `forecasts.`
schema. Two DBs receive every Gamma scan.

| Step | File:Line | Evidence |
| --- | --- | --- |
| 1. Cycle entry binds conn to trades.db | `src/engine/cycle_runner.py:78` | `conn = connect_or_degrade(_zeus_trade_db_path(), write_class="live")` |
| 2. cycle_runner.get_connection() ATTACHes forecasts | `src/engine/cycle_runner.py:87-89` | `conn.execute("ATTACH DATABASE ? AS forecasts", ...)` |
| 3. Same conn passes into discovery phase | `src/engine/cycle_runner.py:933` | `_execute_discovery_phase(conn, clob, portfolio, ...)` |
| 4. Discovery -> log_forward_market_substrate(conn, ...) | `src/engine/cycle_runtime.py:2366-2371` | `result = log_forward_market_substrate(conn, markets=..., scan_authority=authority)` |
| 5. log_forward_market_substrate -> _insert_forward_market_event(conn, ...) | `src/state/db.py:3949` | `event_result = _insert_forward_market_event(conn, event_values)` |
| 6. INSERT INTO market_events_v2 — UNQUALIFIED | `src/state/db.py:3633-3641` | `conn.execute("""INSERT INTO market_events_v2 (...) VALUES (...)""", ...)` — no `forecasts.` prefix |
| 7. Schema on trades.db exists (so insert succeeds, no error) | `src/state/db.py:2879-2886` (kernel DDL applied on every K1 DB) | `CREATE TABLE market_events_v2 ... CREATE INDEX idx_market_events_v2_*` invoked on every connection that calls `init_schema` |

This is the structural decision: in step 6 the INSERT names `market_events_v2`
without the `forecasts.` schema prefix. With the cycle's main DB = trades.db,
the unqualified name resolves to `main.market_events_v2`, NOT
`forecasts.market_events_v2`. The forecasts copy is reached only via a
**separate** writer path (the `market_scanner._persist_market_events`
path at `src/data/market_scanner.py:610` which uses
`ZEUS_FORECASTS_DB_PATH` directly). Both writers run; both receive 638/day.

## 5. Karachi 5/17 deployment safety

This receipt is **read-only documentation**. No code or DB edit. Safe to
land between cycles. Karachi position `c30f28a5-d4e` not touched.

## 6. Out-of-scope per advisor (do not invent)

The 9 missing finding IDs (F46, F48, F63, F71, F81, F82, F83, F84, F103)
are intentionally NOT reconstructed here. Per the anti-rubber-stamp
clause in the brief ("every K-decision identified needs ≥3 finding
citations with file:line evidence") I cannot honestly multi-cite from
findings that do not exist. Orchestrator should either: (a) supply the
corrected authority docs, or (b) explicitly authorize a narrowed scope
of "F19 + writer-locator + structural fix" with the evidence above.

## 7. Excluded files (per brief, F44-just-merged)

Touch-list **not modified** by this receipt:
- `scripts/obs_v2_live_tick.py`
- `src/ingest_main.py`
- `daily_obs_append.py`

The K-axis above does NOT live in any of those files. The decision site
is `src/state/db.py:3633` (unqualified INSERT) plus its caller chain
through `src/engine/cycle_runner.py` and `src/engine/cycle_runtime.py`.

## 8. Per-finding outcome table

| ID | Outcome | Reason |
| --- | --- | --- |
| F19 | **VERIFIED-LIVE-WORSENING** | row count 7,326 → 7,964 since corpus; daily 638 inserts confirmed; root cause located at `src/state/db.py:3633` |
| F46 | **RETRACT-no-such-finding** | grep-zero in repo |
| F48 | **RETRACT-no-such-finding** | grep-zero in repo |
| F63 | **RETRACT-no-such-finding** | grep-zero in repo |
| F71 | **RETRACT-no-such-finding** | grep-zero in repo |
| F81 | **RETRACT-no-such-finding** | grep-zero in repo |
| F82 | **RETRACT-no-such-finding** | grep-zero in repo |
| F83 | **RETRACT-no-such-finding** | grep-zero in repo |
| F84 | **RETRACT-no-such-finding** | grep-zero in repo |
| F103 | **RETRACT-no-such-finding** | grep-zero in repo |

## 9. Suggested next action

Two paths for the orchestrator to choose between:

**PATH A — supply corrected brief**: forward the real MASS_TRIAGE doc or
the actual list of findings the K1 sweep should address. Executor
resumes against verified citations.

**PATH B — narrowed scope authorization**: explicitly authorize the
executor to ship a single-K structural fix targeting F19 only. The fix
shape is one of:

1. Schema-qualify the INSERT at `src/state/db.py:3633` — change
   `INSERT INTO market_events_v2` to `INSERT INTO forecasts.market_events_v2`
   so the substrate writer lands canonical regardless of which K1 DB
   the cycle's main conn is bound to. Risk: any other caller of
   `_insert_forward_market_event` that passes a non-ATTACHed conn would
   raise. Audit: 1 caller (`log_forward_market_substrate`), which only
   runs in `cycle_runner.get_connection()` which ATTACHes forecasts.
2. Tighten `db_table_ownership.yaml` to declare `market_events_v2` as
   db:forecasts-only, drop the table from trades.db (DROP TABLE
   IF EXISTS) and add `assert_db_matches_registry()` enforcement to
   make a repeat impossible.
3. Both (1) + (2) combined as the K-axis structural close.

Antibody test: extend `tests/test_k1_reader_isolation.py` (or sibling)
with a static scan asserting every `INSERT INTO market_events_v2`,
`INSERT INTO settlements`, `INSERT INTO settlements_v2` in `src/` is
schema-qualified (`forecasts.`) OR runs inside a function that uses
`get_forecasts_connection()` directly.

**Until the orchestrator chooses, no code edits land.** This is the entire
output of this dispatch.
