# Live Release Proof P0-P3 Analysis

Captured from operator message on 2026-05-21. This is the source analysis for
the `task.md` ledger in this packet. Before starting any finding repair, read
the corresponding original section in this file and cite its section id in the
work log / commit message.

## 0. Executive Verdict

PAPER ONLY.

Highest-risk money-path fracture: current `main` contains many live-money
repairs, but there is not enough proof that the full chain has closed in one
reality:

`latest main -> live root loaded SHA -> current DB schema/state -> source/forecast/evaluator/sizing/venue/reconcile/redeem`

The analysis observed that PR #253 still listed post-merge fast-forward, daemon
restart, loaded-SHA verification, and sustained source/forecast/evaluator/
sizing/venue/reconcile/redeem proof as remaining live proof rather than a
completion claim. The analyzed main merge commit was
`656e73fe5a71893ef7751ac4cac7de6003540ea8`; this packet is opened from the
newer actual `origin/main` commit `1d63ad4450085e6b1c0ef7ab84fa92436768e8d9`.

Allowed: paper/sim/replay-only, and reports must label live eligibility unknown.
Not allowed: normal live. Tiny live only after all P0 release gates pass with
fresh loaded-SHA, DB schema, venue/redeem reconciliation proof.

## 1. Evidence Base

Reviewed surfaces in the source analysis:

| Evidence surface | Status |
| --- | --- |
| Repository | `fitz-s/zeus`, default branch `main` |
| Latest visible main in analysis | merge commit `656e73fe5a71893ef7751ac4cac7de6003540ea8`, PR #253 |
| Recent PRs | GitHub connector metadata for PR #253 down through #173 plus exceptions #251/#243 |
| Current-main files inspected | `src/data/market_scanner.py`, `src/contracts/executable_market_snapshot_v2.py`, `src/execution/settlement_commands.py`, `src/execution/command_recovery.py`, `src/execution/order_truth_reducer.py`, `src/contracts/effective_kelly_context.py`, `src/engine/evaluator.py`, `src/state/decision_events.py`, `src/state/no_trade_events.py`, `src/state/schema/no_trade_events_schema.py`, `src/state/db.py` slices |
| Local execution | Not run in source analysis |
| External docs | Limited; code and PR metadata treated as primary |
| Review-comment enumeration | Partial/UNKNOWN |

## 2. Recent 100 PR Change-Surface Map

The source analysis classified the recent PR window as a live-repair storm
concentrated on execution, order lifecycle, settlement/redeem, schema/state,
market discovery, forecast readiness, and operational health. The compact map
spans PR #253 through #140, with notable out-of-band PRs #251/#159/#155/#154/#153.

Important rows that drive this packet:

| PR | Status/main | Subsystem | Risk class | Notes |
| --- | --- | --- | --- | --- |
| #253 | merged / yes | live contract seams | P0/P1 fixup | latest analyzed main; states live proof still pending |
| #252 | merged / yes | family vector fill | P0/P1 fixup | schema v17; fill authority |
| #251 | open at analysis / later merged in current origin/main | phase3 shoulder scaffold | foundation | scaffold risk must not be treated as live proof |
| #250 | merged / yes | recovery + exit retry | P0 fixup | entry fact recovery, exit snapshot identity |
| #249 | merged / yes | family selection | P1 semantic | older DB fallback noted |
| #247 | merged / yes | decision timing | P1/P0 fixup | live submit/ack timing persisted |
| #246 | merged / yes | pre-Kelly family gate | P0/P1 fixup | Stage-A gate before scalar Kelly |
| #245-#228 | merged / yes | oracle/fill facts/recovery/stale orders/partial/terminal states | P0/P1 fixups | repeated lifecycle repair indicates integration replay is needed |
| #224/#223/#220 | merged / yes | executable substrate/book hash/schema | P1/P0 schema | snapshot/book-hash ownership and transitions |
| #222/#214 | merged / yes | no_trade_events / decision_events | P1 schema/learning | learning surface asymmetry must be resolved |
| #213-#196 | merged / yes | redeem/reseat/serializer/reconciler | P0/P1 | settlement/redeem command truth repaired in slices |
| #200 | merged / yes | EffectiveKellyContext | P0/P1 | later passive/maker changes require dynamic proof |
| #186/#184 | merged / yes | negRisk child/event semantics | P0 | scanner tradeability semantics shifted away from raw active/closed |
| #159 | merged / yes | full pytest advisory | P2 CI | advisory is not sufficient for live release |
| #155 | merged / yes | test isolation/control precedence | P0/P1 | prevents live DB test writes |
| #149 | merged / yes | deployment freshness | P0 ops | stale-code auto-pause/fail |
| #140 | merged / yes | Karachi remediation | P0 mega | structural remediation bundle |

## 3. Main-Branch Subsystem Map

The source analysis mapped the money path into these authority surfaces:

1. Contract semantics: settlement semantics, resolution era, temperature types,
   executable snapshot, decision source context.
2. Market discovery: Gamma scanner, CLOB archived/orderbook cross-check,
   source contract checks.
3. Market family/bin/outcome identity: support topology, executable mask,
   `WeatherFamilyKey`, family selection/dedup.
4. Forecast ingest: ECMWF Open Data, live daemon, partial refetch, member floor.
5. Observation ingest: WU/ICAO/HKO/Meteostat/solar, obs v2.
6. Source authority/provenance: source contract, quarantine, timing, freshness.
7. Calibration: Platt/HorizonPlatt and K1 DB ownership.
8. Market prior: executable quote fields and microstructure.
9. Executable snapshot/orderbook: V2 snapshot, persisted snapshots, book hashes.
10. Edge calculation: evaluator, strategy profiles, p_raw/p_cal/edge sizing.
11. Sizing/Kelly/risk: EffectiveKellyContext, allocator, exposure gates.
12. Execution: executor, Polymarket V2 adapter, Safe/NegRisk calldata.
13. Order lifecycle: venue commands/facts/trades, reducer, recovery.
14. Position state: `position_current`, lots, chain reconciliation.
15. Settlement/redeem: command ledger, harvester, Safe wrapper, NegRisk.
16. Monitoring/exit: monitor refresh, lifecycle, riskguard, healthcheck.
17. Replay/backtest/report: replay authority and event tables.
18. Learning: decision/no-trade events and calibration pairs/models.
19. Scheduler/daemon/boot: `main.py`, freshness, auto-resume, mode jobs.
20. DB/schema/migration: world/forecasts/trades split, versions/hashes.
21. Config/env/flags: registry, live/paper flags, kill switch, wrap/redeem.
22. Tests/CI/topology: topology doctor, schema hash, antibody tests.
23. Docs/AGENTS/authority routing: operations packet, architecture registry.

## 4. Risk Concentration Heatmap

Hot surfaces named in the source analysis: `src/engine/evaluator.py`,
`src/engine/cycle_runtime.py`, `src/contracts/executable_market_snapshot_v2.py`,
`src/data/market_scanner.py`, `src/execution/executor.py`,
`src/venue/polymarket_v2_adapter.py`, `src/execution/settlement_commands.py`,
`src/execution/command_recovery.py`, `src/execution/order_truth_reducer.py`,
`src/execution/exchange_reconcile.py`, `src/state/db.py`,
`src/state/no_trade_events.py`, `src/state/decision_events.py`, `src/main.py`,
`architecture/db_table_ownership.yaml`, and `architecture/test_topology.yaml`.

## 5. P0 Findings

### P0-1 — Latest main is not live-authorized by current evidence

Severity: P0 release blocker.

Current surface: `main.py`, scheduler/daemon boot, live root deployment, and
all money-path modules.

Observed behavior: merged code has many local repairs, but there is no
current-main evidence that the integrated live runtime has loaded the latest
SHA and passed end-to-end trading lifecycle proof.

Why wrong: a live-money system is current runtime + DB state + exchange truth,
not Git branch alone.

Required fix: hard release gate proving loaded SHA, DB schema/hash, no pending
unknown side-effect commands, no stuck redeem/operator-required rows except
whitelisted, fresh source/forecast/executable snapshots, and at least one full
dry-run/paper lifecycle proof before live entries.

Required test: release smoke script proving loaded SHA, schema versions,
scanner freshness, forecast readiness, evaluator decision, event persistence,
command repo, reconcile, and redeem reconciler agree before live entries.

### P0-2 — REVIEW_REQUIRED: negRisk scanner admission and executable snapshot closed semantics may still disagree

Severity: P0 if raw closed flag can still block current-builder snapshots;
otherwise REVIEW_REQUIRED.

Files: `src/data/market_scanner.py::_event_has_active_children`,
`src/contracts/executable_market_snapshot_v2.py::assert_snapshot_executable`.

Observed behavior: scanner admits negRisk by ignoring parent active/closed
routing labels and checking child acceptingOrders plus CLOB/orderbook facts.
Executable snapshot validation still treats `snapshot.closed` as an
unconditional submit blocker in the inspected contract.

Required fix: first-class tradeability-status object:
`gamma_parent_closed`, `gamma_parent_active`, `child_closed`, `child_active`,
`accepting_orders`, `clob_archived`, `clob_enable_order_book`,
`executable_allowed`. `assert_snapshot_executable()` must use the same final
authority as the scanner, not raw overloaded Gamma fields.

Required test: one end-to-end fixture where Gamma parent closed/active false,
child active false, child acceptingOrders true, CLOB archived false and
enable_order_book true; scanner admits, snapshot persists, persisted reader is
executable, snapshot assertion passes, executor sees the same condition/token.
Inverse fixtures: CLOB archived and child acceptingOrders false.

### P0-3 — Live schema/migration sequencing is not proven; semantic fallback can hide truth

Severity: P0 release blocker.

Files/schema: `src/state/schema/no_trade_events_schema.py`,
`src/state/no_trade_events.py`, `src/state/db.py`.

Observed behavior: current no-trade code preserves rows by downgrading
unsupported enum reasons to `UNCATEGORIZED` with details when schema
constraints are older.

Why wrong: in live-money learning/reporting, a no-trade reason is an economic
object. Downgrading family/source/timing blockers fabricates
“instrumented but semantically unavailable” evidence.

Required fix: live mode boot/write must fail if DB schema is older than current
schema expectations. Only non-live/backfill may use compatibility downgrade,
and downgraded rows must be marked `schema_compatibility='degraded'` and
excluded from live learning/report trust.

Required tests: old-schema live writer/boot fails closed before trading;
paper/backfill compatibility writes degraded rows and excludes from live
eligibility.

### P0-4 — No single integrated crash/recovery/redeem replay proves the current order lifecycle

Severity: P0 release blocker for live.

Files: `executor.py`, `venue_command_repo.py`, `command_recovery.py`,
`exchange_reconcile.py`, `order_truth_reducer.py`, `settlement_commands.py`,
`polymarket_v2_adapter.py`.

Observed behavior: targeted tests exist, but no single current-main replay
injects crash points across intent persistence, submit, ack, partial fill,
cancel remainder, exit, settlement, redeem, and chain receipt.

Required fix: deterministic lifecycle replay harness with crash injection at
every boundary.

Required test: table-driven integration covering entry intent persisted,
submit unknown, exchange order present/no trade, partial fill, cancel
remainder, terminal zero remainder, position lots, day0/settlement, redeem
request, Safe/NegRisk dry-run, tx hash, receipt logs, confirmed. Assert command
state, order facts, exposure, position, settlement command, decision/no-trade
events converge.

## 6. P1 Findings

### P1-1 — Trade/no-trade learning surfaces have asymmetric failure policy

`decision_events` enforces live timing requirements while `no_trade_events`
can degrade reason categories for older schema compatibility. Required:
mode-specific policy where live is fail-closed and paper/backfill degrades with
marking.

### P1-2 — CLOB-unreachable fallback in market scanner may be too permissive for live

Discovery may fall back to Gamma continuity, but live executable entry must
require fresh CLOB/orderbook confirmation.

### P1-3 — REDEEM_OPERATOR_REQUIRED is semantically overloaded

The state name conflates true human-required rows with rows eligible for
autonomous reseat after known errors. Required: split into
`REDEEM_AUTORETRYABLE_REVIEW` vs `REDEEM_OPERATOR_REQUIRED`, or add explicit
`autoretry_eligible`.

### P1-4 — Market family authority now exists in multiple layers

Preselection, runtime dedup, family exposure, and no-trade all exist and can
drift. Required: one canonical `WeatherFamilyExposure` reducer.

### P1-5 — EffectiveKellyContext needs dynamic proof across all live entry modes

AST/callsite-count tests are insufficient after passive maker, sub-dollar,
fill-probability, and family vector changes. Required: integration tests for
each live strategy mode and order type: taker, passive maker, sub-dollar
passive, wide spread, low depth, missing context.

### P1-6 — `unknown_legacy` default is correct but report trust must treat it as non-authoritative

Historical rows with `unknown_legacy` must not count as verified anchor
evidence. Required: settlement/redeem reports distinguish `unknown_legacy`,
`gamma_explicit`, CLOB-derived, and chain-derived anchor proof.

### P1-7 — Advisory full pytest sweep is not a live release gate

Required: promote a curated money-path integration suite to required before
live entry, even if full pytest remains advisory.

## 7. P2/P3 Findings

| Severity | Finding | Required action |
| --- | --- | --- |
| P2 | Docs/scaffold header drift remains | Refresh authority headers after runtime verification |
| P2 | Topology registries can lag runtime | Add runtime-to-registry assertion in CI with legacy exclusions |
| P2 | Monitor nowcast fail-soft can hide gaps | Counter + alert when write failures persist |
| P2 | Hardcoded strategy/cluster maps | Move risk metadata into reviewed config with tests |
| P3 | Many antibodies are sed-revert style | Convert key antibodies into relationship/replay tests |
| P3 | PR bodies often list “pre-existing failures” | Track failure IDs centrally; prevent permanent pre-existing failures |

## 8. Cross-PR Contradiction Matrix

Key contradictions to repair:

| PRs | Object | Contradiction | Required resolution |
| --- | --- | --- | --- |
| #184/#186/#199 vs #252/#253 | scanner vs snapshot | active/closed routing-label semantics can diverge | unified tradeability object + E2E negRisk test |
| #220 vs #222/#249/#252 | schema/no_trade_events | older live DB can degrade or reject reasons | boot fail-closed on schema mismatch |
| #201 vs #213 | anchor source | fabricated historical authority | audit/reports exclude unknown/fabricated anchors |
| #238 vs #239 | T4 merge date | placeholder allowed vacuous tests | non-vacuous post-merge fixture |
| #207 vs #215 | redeem misroute classifier | earlier antibody false-positive | classifier keyed on adapter emitter and payout evidence |
| #246 vs #249/#252 | family selection | dual authorities can diverge | canonical family exposure reducer |
| #200 vs #231/#249/#253 | EffectiveKellyContext | later passive paths can miss context | dynamic mode/order-type integration suite |
| #214/#247 vs #222/#249 | decision vs no-trade events | strict vs downgrade asymmetry | same live-mode fail-closed policy |
| #159 vs many | CI sweep | advisory CI cannot guard live main | required money-path gate |

## 9. Money-Path Integrity Table

All major money-path segments were rated PARTIAL in the source analysis:
contract semantics, source truth, forecast signal, calibration, market prior,
executable edge, sizing, execution, monitoring, settlement, and learning.

## 10. Schema / State / Migration Audit

Risk is merge-order and runtime DB alignment, not absence of schema discipline.
Schema versions have moved densely; `unknown_legacy` is correct but reports
must treat it as non-authoritative; compatibility fallback can fabricate
category availability; cross-DB ownership has changed repeatedly; pinned schema
hash proves DDL bytes, not migration semantics; rollback after v17 writes is
limited.

Required gate: live boot verifies schema version/hash, required columns, and
semantic migration sentinels before trading or autonomous redeem.

## 11. Execution / Settlement / Redeem Lifecycle Audit

The command/redeem state model is stronger, but release proof is missing. The
analysis rated command persistence, venue ack, partial fill, unknown-after-
submit, order truth, pending exposure, exit retry, redeem pending, adapter,
receipt persistence, JSON serialization, stuck-state recovery, and chain-vs-
local truth as PASS/PARTIAL, not live-proven.

## 12. Forecast / Observation / Source-Truth Audit

Forecast/source surfaces improved but still require replay over city-local date,
issue/valid/captured/run times, partial vs complete cycles, Open Data vs
archive/backfill identity, ensemble floor, HIGH/LOW independence, local-day
mapping, forecast freshness, false MAX(timestamp) freshness, day0 causality, and
provider_reported_time labeling.

## 13. Kelly / Risk / Executable Cost Audit

EffectiveKellyContext is strong but needs all-path dynamic proof. Required
Kelly test set: fixture per live mode with bid/ask/depth/freshness/fill
probability/fees asserting final order notional and max loss match executable
cost basis.

## 14. Test-Gap Matrix

Required tests:

1. negRisk market discovery -> snapshot -> submit E2E tradeability.
2. old-schema live fail-closed; paper compatibility degraded.
3. full crash/recovery/redeem lifecycle replay.
4. no_trade vs decision_events completeness parity.
5. EffectiveKellyContext dynamic all-mode matrix.
6. redeem misroute receipt classifier regression.
7. stale scanner/forecast/status_summary cannot pass live readiness.
8. reports exclude/label unknown anchors and downgraded no-trade reasons.
9. required `money_path_replay` CI job.

## 15. Release Gate

Allowed now: paper only; normal live no; tiny live no until P0 gates pass; report
trust partial only.

Before any further live-impact PR: required money-path suite, schema ownership
and migration plan, no open scaffold object referenced as landed.

Before paper: isolated schema init, scanner -> forecast -> evaluator -> event
writers without side effects, reports label live eligibility UNKNOWN.

Before tiny live: P0-2 proven, live DB schema/hash current, loaded SHA equals
latest main, no stuck unknown/ghost/autoretryable states, dry-run Safe/NegRisk
proof current, Kelly executable-cost suite green.

Before normal live: sustained business-plane liveness, fresh source/forecast/
scanner, exposure matches exchange/chain, no stuck redeem rows except true
manual, crash/recovery replay green.

## 16. Repair Packet

Steps:

1. Unify negRisk tradeability semantics.
2. Enforce current schema in live mode.
3. Add full lifecycle crash/recovery replay.
4. Split redeem operator-required states.
5. Prove EffectiveKellyContext dynamically.
6. Add business-plane liveness gate.
7. Promote money-path CI from advisory to required.
8. Clean authority/docs drift last.
