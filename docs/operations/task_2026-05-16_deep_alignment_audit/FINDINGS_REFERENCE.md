# Zeus Deep Alignment Audit — FINDINGS_REFERENCE

**Purpose**: single-page master index for every audit finding (Run #1 #1–#4, Run #2 #5–#8, Run #3 #9–#13). A fixer should be able to land patches without re-reading the 6 source documents.

**Authoritative entry-point**: [REPORT.md](REPORT.md) for narrative; this file for action routing.

**Date last updated**: 2026-05-16 (post Run #3, includes operator FALSE-POSITIVE override on #9)
**Audit worktree HEAD**: `199a43cbbc` (commits `f65a6abe96` Run-2/Run-3, `40e7709b2d` Phase-3 correction, this consolidation commit)
**Main anchor**: `a924766c8a`

---

## ⚠️ Karachi 2026-05-17 prep checklist (read first)

Single-screen scan before the settlement window. Pulled from [KARACHI_2026_05_17_MANUAL_FALLBACK.md](KARACHI_2026_05_17_MANUAL_FALLBACK.md).

| # | When (UTC) | Action |
|---|---|---|
| 1 | **T-12 h · 2026-05-17 00:00** | Health checks: position still active (shares=1.5873), `ZEUS_HARVESTER_LIVE_ENABLED=1`, settlements_v2 has VERIFIED rows for other cities in last 24h. Escalate immediately if any check fails. |
| 2 | **T-2 h · 2026-05-17 10:00** | Eyeball Wunderground OPKC page; probe Polymarket gamma-api outcomePrices. If YES ≥ 0.95 or ≤ 0.05, direction is essentially known. |
| 3 | **T-0 · 2026-05-17 12:00** | Polymarket endDate. Do nothing — auto cascade window. |
| 4 | **T+1 h · 2026-05-17 13:00** | Probe `settlements_v2` for Karachi/2026-05-17 VERIFIED row + `position_events` for SETTLED event on `c30f28a5-d4e`. If present → done. |
| 5 | **T+3 h · 2026-05-17 15:00** | If no settlement row: investigate harvester ticks, wall-cap warnings, Polymarket UMA resolution status — but do NOT write yet. |
| 6 | **T+9 h · 2026-05-18 04:00** | Manual fallback gate. Four pre-flight checks (Polymarket closed; forecasts.db writable; OPKC observation VERIFIED present; position still active). All four must pass before either §3.A manual observation fetch or §3.B sanctioned backfill. |
| 7 | **Throughout** | Heartbeat-channel disagreement is known live (Finding #10): expect dispatcher to report `degraded` while sensor says RED. Trust neither in isolation; cross-check raw `logs/heartbeat-sensor.err`. |

Dollar exposure: **$0.59**. Runbook value is procedural, not capital-protective.

---

## 🚢 Suggested PR groupings (ship these together)

Grouped by domain/risk/coupling. Each PR should land independently and each closes a coherent slice of findings.

| PR | Title | Findings | Risk | Why grouped |
|---|---|---|---|---|
| **PR-A** | `data-quality-backfill` | #2 (decision_id NULL) + #4-residue purge (2,112 stranded world.market_events_v2 rows) | LOW | Both are read-only data cleanup; high attribution/lineage value; no live-write change |
| **PR-B** | `lock-storm-eradication` | #5 (`CollateralLedger._connect` routing fix) | SEV-1 LIVE | Single-module fix; eliminates ingest-blocking `database is locked` storm; pre-Karachi safe |
| **PR-C** | `harvester-throughput` | #7 (`tag=weather` filter + bounded retry on harvester_truth_writer) | SEV-1 INGEST | Settlement-path correctness; should ship BEFORE Karachi 5/17 if confidence allows, else operator runs manual fallback |
| **PR-D** | `alarm-channel-bridge` | #10 (heartbeat sensor RED → dispatcher signal propagation + APNs/Discord push for RED-≥3-consecutive) | SEV-1 OPERATOR-BLIND | The two-channel disagreement is the root; fix at the dispatcher; test with synthetic RED injection |
| **PR-E** | `audit-protocol-rigor` | #1 (wire `assert_db_matches_registry()` at boot, fail-closed) + #6 (plist stderr/stdout swap or `.log`/`.err` documentation) + #11 (heartbeat-sensor.plist `KeepAlive=true` or delete plist + document cron as canonical) | MIXED SEV-1/2 | All are "the antibody exists but isn't deployed/visible" — same root pattern; coherent owner (ops/launchd + boot-startup) |
| **PR-F** | `schema-ghost-purge` | #12 (drop empty trade-lifecycle shells on world.db) + tighten `db_table_ownership.yaml` to assert `db:trades` tables have no schema on other DBs | SEV-2 | Migration script + registry tightening; coupled because the registry check is what would have caught this |
| **PR-G** | `doctrine-refresh` | #3 (`current_state.md`, `current_data_state.md` factual rewrite + automated freshness check) + #8 (sentinel timestamp `position_events.occurred_at` rejection) | SEV-2 | Both are "stale/sentinel data in operator-facing surfaces"; coherent doc-team owner |
| **PR-H** | `replay-kelly-honesty` | #13 (replay.py docstring + traceability check for downstream consumer; plumb real win-rate/heat/drawdown if any consumer found) | SEV-3 | Standalone; defer until PR-A–G land |
| (none) | — | **#9 WU_API_KEY — FALSE POSITIVE, no PR needed** | INFO | Operator override 2026-05-16: adjacent comment in crontab documents intentional placement. See `RUN_3_findings.md` §Finding #9 FALSE POSITIVE OVERRIDE block. |

---

## 📋 Master findings index

Columns:
- **#**: finding id
- **SEV**: severity (1/2/3/INFO)
- **Title** · **Cat**: short title + LEARNINGS category id
- **Status**: latest verdict (STILL-OPEN / RESOLVED-WITH-CAVEATS / FALSE-POSITIVE / etc.)
- **Evidence**: concise file:line or DB query
- **Deep dive**: link to companion doc (or REPORT.md§)
- **Fix spec**: one line — exact change location or runbook ref
- **Backfill**: data residue cleanup (if any)
- **Owner-hint**: team/surface
- **Karachi 5/17 risk**: LOW / MED / HIGH / NONE
- **PR**: target grouping above

| # | SEV | Title · Cat | Status | Evidence | Deep dive | Fix spec | Backfill | Owner-hint | K-risk | PR |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | Registry-vs-disk drift unenforced; `assert_db_matches_registry()` unwired · **F** | STILL-OPEN | `src/main.py:857-859` comment cites unwiring; `src/state/table_registry.py:283` defines it; only `tests/state/test_table_registry_coherence.py` invokes | [REPORT.md §Finding #1](REPORT.md) | Reconcile `architecture/db_table_ownership.yaml` to disk first; THEN wire 3× assertions in `src/main.py` startup between `init_schema(trade_conn)` and `_startup_world_schema_ready_check()`; fail-closed | — | trading / boot-startup | INDIRECT (future drift) | PR-E |
| 2 | 1 | `selection_hypothesis_fact.decision_id` 100% NULL (506→693 regression) · **A** | STILL-OPEN (worsening) | `src/engine/evaluator.py:1535-1561` omits `decision_id=` kwarg; `src/state/db.py:5314` default `None`; live DB: 693/693 NULL on `zeus_trades.db` | [DEEP_DIVE_2_decision_id_regression.md](DEEP_DIVE_2_decision_id_regression.md) | (a) Make `decision_id` positional-required in `log_selection_hypothesis_fact`, raise `ValueError` on falsy. (b) Add `tests/state/test_lineage_join_keys.py` parametrized over 5 lineage tables × keys | 693 historical NULLs unrecoverable; document as one-time historical hole in `current_data_state.md` | data team / lineage | DIRECT (live position post-mortem) | PR-A |
| 3 | 2 | Doctrine drift: `current_state.md` 1 commit stale; `current_data_state.md` 18d stale with wrong settlement count + wrong harvester status · **H** | STILL-OPEN | `current_state.md` says HEAD=`8b3c3c2c59`, actual=`a924766c8a`; `current_data_state.md` says baseline=1,609, reality=5,570 legacy + 3,987 v2; says harvester DORMANT, reality=3,605 VERIFIED | [REPORT.md §Finding #3](REPORT.md) | Manual factual rewrite of both docs against today's `state/zeus-forecasts.db` row counts + main HEAD; add a `tests/test_doctrine_freshness.py` that parses anchor date and fails if > 14d stale | — | ops / docs | LOW | PR-G |
| 4 | 2 | Harvester truth writer wrote to ghost world.db (`settlements_v2` silent 5d) — **RESOLVED via PR #121** on main; residue persists · **E1** | RESOLVED-WITH-CAVEATS | `src/ingest_main.py:646` now uses `get_forecasts_connection`; residue: 2,112 stranded rows on `world.market_events_v2` (1,386+726), no growth since 2026-05-13 16:45 UTC | [REPORT.md §Finding #4 / Phase-3](REPORT.md); commit `40e7709b2d` | Verify residue purge migration in PR-A; assert no new rows appear on world.market_events_v2 in a CI smoke | 2,112 stranded rows — drop or migrate per migration script | data team / ingest | LOW (residue only) | PR-A |
| 5 | 1 | DB-lock storm in `zeus-ingest.err` — `CollateralLedger._connect` opens wrong DB without WAL/timeout, blocks ingest · **F+G** | STILL-OPEN | `tail -1000 logs/zeus-ingest.err \| grep -c "database is locked"` > 0; deep dive traces it to `_connect` opening `state/zeus_trades.db` without retry | [DEEP_DIVE_5_db_lock_storm.md](DEEP_DIVE_5_db_lock_storm.md) | Route `CollateralLedger._connect` through `get_trade_connection()` (which sets WAL + busy_timeout); deep dive specifies exact patch site | — | trading / risk-daemon | INDIRECT (ingest slowness → stale forecasts pre-Karachi) | PR-B |
| 6 | 1 | `.log` files 0-byte while `.err` is GBs (live daemons appear offline) · **G** | STILL-OPEN | `ls -lt logs/*.log logs/*.err`: `zeus-live.log`=0B vs `zeus-live.err`=89MB+; same for several launchd children | [REPORT.md §Run #2 / Finding #6](REPORT.md) | Plist `StandardOutPath` / `StandardErrorPath` swap or merge to single `.log`; document `.log`/`.err` convention in `docs/operations/launchd_logging.md`; update audit-of-audit to always probe `.err` (already added to LEARNINGS) | — | ops / launchd | INDIRECT (operator misreads as offline) | PR-E |
| 7 | 2 | Harvester filter too coarse — Polymarket weather markets not filtered; bounded retry missing · **E1** | STILL-OPEN | `grep "harvester_truth_writer_tick.*markets_resolved" logs/zeus-ingest.err \| tail -20` shows `settlements_written=0` over multi-day window despite expected settlements; deep dive traces filter omission | [DEEP_DIVE_7_harvester_category_filter.md](DEEP_DIVE_7_harvester_category_filter.md) | Add `tag=weather` filter at category source; add bounded retry (max 3) on resolution-fetch failure; deep dive specifies patch site | — | data team / ingest | MED (could miss Karachi settlement) | PR-C |
| 8 | 2 | Sentinel timestamp on live `position_events.occurred_at` row (non-ISO) · **A+D** | STILL-OPEN | `SELECT occurred_at FROM position_events WHERE occurred_at NOT GLOB '2*'` returns at least one sentinel row | [REPORT.md §Run #2 / Finding #8](REPORT.md) | Tighten `position_events` INSERT to reject non-ISO; backfill known sentinel rows via migration with `NULL` (analyst-overridable) | Sentinel rows on `position_events` — list via probe + replace with NULL or recovered timestamp | trading / event-writer | LOW | PR-G |
| 9 | **INFO** | WU_API_KEY in crontab — **FALSE POSITIVE (operator override 2026-05-16)** · **J** | FALSE-POSITIVE | `crontab -l` line 10:00 UTC oracle snapshot; **adjacent comment documents intentional placement** | [RUN_3_findings.md §Finding #9](RUN_3_findings.md) (with FALSE POSITIVE OVERRIDE block) | **No action — do NOT remove key**. Heuristic update applied to LEARNINGS Cat-J: scan ±3 lines for explanatory comment before flagging | — | — (false positive preserved as LEARNINGS exemplar) | NONE | (none) |
| 10 | 1 | Heartbeat severity-channel disagreement: sensor RED ≥49 consecutive ticks while dispatcher reports `degraded` every 30 min for 3.5+ h · **G+E2** | STILL-OPEN | `logs/heartbeat-sensor.err` last 49 lines `severity=RED root_cause=deep_heartbeat_critical`; `logs/zeus-heartbeat-dispatch.log` 13:30Z onward all `severity=degraded` + `ALERT: zeus degraded (exit code 1)` | [RUN_3_findings.md §Finding #10](RUN_3_findings.md) | (a) Audit `scripts/heartbeat_dispatcher.py` for the RED→degraded downgrade; either propagate RED upward or document the mapping. (b) Add "RED for ≥3 consecutive 30-min ticks → push notification (APNs/Discord/SMS)" regardless of dispatcher classification. (c) Stop emitting exit-code-1 every cycle | — | ops / heartbeat-dispatcher | DIRECT (operator-blind during 5/17 settlement) | PR-D |
| 11 | 2 | `com.zeus.heartbeat-sensor.plist` has no `KeepAlive`; live channel is actually the `*/30 * * * *` cron, not launchd · **E2** | STILL-OPEN | `plutil -extract KeepAlive raw …plist` → No value; `launchctl list \| grep heartbeat-sensor` → PID `-`; `.err` mtime stale 5h+; cron line is real driver | [RUN_3_findings.md §Finding #11](RUN_3_findings.md) | Either (a) add `KeepAlive=true` to plist + remove cron, OR (b) rename plist to `.disabled` + document cron as canonical. Add `tests/test_launchd_plists.py` asserting every `com.zeus.*.plist` has `KeepAlive=true` OR a matching cron line | — | ops / launchd | LOW | PR-E |
| 12 | 2 | Ghost trade-lifecycle tables (6 tables) on `state/zeus-world.db`: schema present, rows=0 while populated copies live in `state/zeus_trades.db` · **F** | STILL-OPEN | Row-count probe: `position_current/position_events/position_lots/collateral_*/venue_order_facts` all rows=0 on world.db, populated on zeus_trades.db | [RUN_3_findings.md §Finding #12](RUN_3_findings.md) | (a) Migration `scripts/migrations/202605_drop_world_trade_lifecycle_tables.py` — DROP empty shells, idempotent (fail loud if rows > 0). (b) Tighten `assert_db_matches_registry()` to also assert `db:trades` tables have no schema on other DBs | — (rows already canonical on zeus_trades.db) | data team / migrations | LOW | PR-F |
| 13 | 3 | `src/engine/replay.py:1664-1671` calls `dynamic_kelly_mult` with 3/5 modulators hardcoded neutral (`rolling_win_rate_20=0.50, portfolio_heat=0.0, drawdown_pct=0.0`) · **B** | STILL-OPEN (cosmetic unless consumer found) | Exact line range as above; `src/strategy/kelly.py` docstring lists all 5 as load-bearing | [RUN_3_findings.md §Finding #13](RUN_3_findings.md) | (a) Docstring at replay.py:1664 explaining neutralization. (b) Trace whether any auto-tuner reads replay's `size_usd` distribution → if yes plumb real values, escalate SEV-3 → SEV-2 | — | trading / sizing | NONE | PR-H |

---

## Cross-references

- Authoritative narrative: [REPORT.md](REPORT.md)
- Run #3 detailed findings: [RUN_3_findings.md](RUN_3_findings.md)
- Karachi 5/17 fallback runbook: [KARACHI_2026_05_17_MANUAL_FALLBACK.md](KARACHI_2026_05_17_MANUAL_FALLBACK.md)
- Deep dives: [DEEP_DIVE_2_decision_id_regression.md](DEEP_DIVE_2_decision_id_regression.md), [DEEP_DIVE_5_db_lock_storm.md](DEEP_DIVE_5_db_lock_storm.md), [DEEP_DIVE_7_harvester_category_filter.md](DEEP_DIVE_7_harvester_category_filter.md)
- Skill evolving brain: [`.claude/skills/zeus-deep-alignment-audit/LEARNINGS.md`](../../../.claude/skills/zeus-deep-alignment-audit/LEARNINGS.md)
- Skill run history: [`.claude/skills/zeus-deep-alignment-audit/AUDIT_HISTORY.md`](../../../.claude/skills/zeus-deep-alignment-audit/AUDIT_HISTORY.md)
