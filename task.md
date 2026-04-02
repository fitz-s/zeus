# task.md

## Status Legend
- TODO
- IN_PROGRESS
- BLOCKED
- REVIEW
- DONE
- DROPPED

## Task Queue

| ID | Priority | Title | Owner | Status | Depends On | Files | Deliverable | Validation |
|----|----------|-------|-------|--------|------------|-------|-------------|------------|
| T1 | P0 | Repo truth map + authority map | Main | DONE | - | `progress.md`, core spine files | initial truth map + locked non-negotiables | main-model read of core files |
| T2 | P0 | Canonical position ledger / position_events schema | Agent B | REVIEW | T1 | `src/state/db.py`, runtime writers | first `position_events` schema + append helpers landed; ownership contract still open | DB assertions + tests |
| T3 | P0 | PositionState / transition authority consolidation | lifecycle-planner | IN_PROGRESS | T1 | `src/state/portfolio.py`, `src/engine/cycle_runtime.py`, `src/engine/cycle_runner.py` | concentrated lifecycle authority / transition map | invariants + targeted tests |
| T4 | P0 | pending/live fail-closed + chain rescue | Agent A | IN_PROGRESS | T3 | `src/engine/cycle_runtime.py`, `src/state/chain_reconciliation.py`, `src/state/fill_tracker.py` | pending rescue design + implementation | targeted regression tests |
| T5 | P0 | quarantine protective behavior | Agent A | IN_PROGRESS | T3 | `src/state/chain_reconciliation.py`, `src/engine/cycle_runtime.py`, targeted lifecycle tests | quarantine entry blocking plus explicit administrative resolution path | adversarial tests |
| T6 | P0 | Day0 terminal phase for all positions | Agent A | IN_PROGRESS | T3 | `src/engine/monitor_refresh.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py` | phase transition + Day0 refresh dispatch now cover same-cycle `<6h` crossings immediately; live Day0 refresh now uses sell-side `best_bid`; stronger exit-policy overrides still open | lifecycle/day0 tests |
| T7 | P0 | ExitContext required schema + fail-closed behavior | Agent A | IN_PROGRESS | T3 | `src/state/portfolio.py`, `src/engine/monitor_refresh.py`, `src/engine/cycle_runtime.py`, `src/execution/exit_lifecycle.py` | ExitContext contract + callers wired | adversarial tests |
| T8 | P0 | cycle_runner main-path state transition + event emission | Agent A / B | TODO | T2,T3,T7 | `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py` | integrated runtime path | integration tests |
| T9 | P1 | decision / execution / exit / settlement durable records | Agent B | REVIEW | T2 | `src/state/db.py`, `src/engine/cycle_runtime.py`, `src/execution/harvester.py`, `tests/test_db.py` | entry/exit/settlement durable position events appended alongside legacy writers | DB assertions + tests |
| T10 | P1 | ExecutionReport / fill telemetry / order attempt events | Agent B | REVIEW | T2 | `src/execution/executor.py`, `src/execution/exit_lifecycle.py`, runtime writers | entry-path telemetry plus exit-lifecycle placement/retry/fill-check durable events landed; still needs full runtime-path validation | unit tests |
| T11 | P1 | harvester learning source migration | Agent B | REVIEW | T2,T9 | `src/execution/harvester.py` | learning inputs sourced from durable settlement authority first, not only open portfolio | harvester tests |
| T17 | P1 | position_events reader/owner contract | Agent B / Agent C | IN_PROGRESS | T2,T9 | `src/state/db.py`, downstream readers | RiskGuard settlement reader now prefers `position_events` and falls back to legacy-only settlement blobs; `db.py` cleanup has removed duplicate helper tails, but broader consumer contract still open | contract review |
| T18 | P1 | exit_lifecycle event emission | Agent B | REVIEW | T10 | `src/execution/exit_lifecycle.py`, runtime writers | emit sell placement/retry/fill-check/recovery events into `position_events` from the exit state machine seam; current follow-up is runtime-path validation, not more helper expansion | unit tests |
| T19 | P1 | harvester snapshot sourcing from durable ledger | Agent B | REVIEW | T11,T17 | `src/execution/harvester.py`, ledger readers | replace open-portfolio snapshot discovery with durable settlement rows first and narrow portfolio fallback | harvester tests |
| T20 | P1 | settlement/decision event dedupe contract | Agent B / Agent C | TODO | T9,T17 | `src/state/decision_chain.py`, `src/state/db.py`, consumers | define authoritative read path across `decision_log`, `chronicle`, and `position_events` | contract review |
| T21 | P1 | lifecycle event telemetry for pending reconciliation | Agent B | REVIEW | T10,T3 | `src/engine/cycle_runtime.py`, `src/state/db.py`, `src/state/chain_reconciliation.py` | explicit pending->entered|voided reconciliation event coverage now includes exactly-once rescued-fill stage events from chain reconciliation; broader void/reconciliation coverage still separate | DB assertions + tests |
| T22 | P1 | chronicle alignment with stage-level events | Agent B | TODO | T9,T20 | `src/state/chronicler.py`, runtime writers | align chronicle event names/payloads or formally narrow its scope | grep/tests |
| T23 | P1 | durable event spine contract docs in team files | Agent B | TODO | T17,T20 | `progress.md`, `task.md` | freeze event names, identity fields, and source semantics after review | file review |
| T24 | P1 | event spine smoke validation | Agent B | TODO | T9,T10,T18,T21 | `tests/` | entry->lifecycle->exit->settlement append smoke path | pytest targets |
| T25 | P1 | partial-fill / rejected-order event support | Agent B | TODO | T10,T18 | `src/execution/executor.py`, `src/execution/exit_lifecycle.py` | extend telemetry beyond filled/pending happy path | unit tests |
| T26 | P2 | durable event backfill strategy for existing rows | Agent B / Agent C | TODO | T17,T20 | `src/state/db.py`, migration plan | decide whether historical `trade_decisions` rows remain legacy-only | migration review |
| T27 | P2 | event ordering/env/index audit | Agent B | TODO | T2,T24 | `src/state/db.py`, tests | verify ordering semantics and paper/live query support | DB assertions |
| T28 | P2 | event-spine replay bridge decision | Agent B / Agent C | TODO | T17,T20 | `src/engine/replay.py`, readers | decide direct read vs adapter from `position_events` | design review |
| T29 | P2 | settlement event duplication audit with chronicle | Agent B | TODO | T20,T22 | `src/execution/harvester.py`, readers | ensure dual writes are intentional and documented | review |
| T30 | P2 | event writer helper placement cleanup | Agent B | TODO | T20,T23 | `src/state/db.py`, `src/state/decision_chain.py` | decide final helper home after first consumer lands | design review |
| T31 | P2 | event coverage matrix | Agent B | TODO | T18,T21,T24 | `progress.md`, `task.md` | list covered and uncovered runtime transitions | file review |
| T32 | P2 | durable event review handoff | Agent B | TODO | T2,T9,T10 | `progress.md`, teammate handoff | summarize shipped slice, blockers, and requested review focus | handoff note |
| T33 | P2 | event-spine consumer grep audit | Agent B / Agent C | TODO | T17,T20,T28 | `src/`, `tests/` | enumerate readers/writers before broader migration | grep audit |
| T34 | P2 | stage-event naming freeze in tests | Agent B | TODO | T23,T24 | `tests/` | lock event names to prevent drift | pytest targets |
| T35 | P2 | ensure stage events stay supplemental until authority switch | Agent B / Main | TODO | T17,T20 | `progress.md`, `task.md`, code review | prevent accidental consumer switch before contract freeze | review |
| T36 | P2 | event payload minimization follow-up | Agent B | TODO | T20,T23 | `src/state/db.py` | trim duplicate payload fields after reader contract freeze | review |
| T37 | P2 | chronicle scope decision | Agent B / Agent C | TODO | T22,T28 | `src/state/chronicler.py`, consumers | decide whether chronicle narrows to operator log only | review |
| T38 | P2 | settlement-source migration readiness check | Agent B | TODO | T11,T19,T20 | `src/execution/harvester.py`, `tests/` | confirm learning loop can switch off open-portfolio dependence | regression tests |
| T39 | P2 | pending-void settlement edge-case audit | Agent B / Agent C | TODO | T21,T38 | `src/engine/cycle_runtime.py`, `src/execution/harvester.py`, `tests/` | ensure voided/pending paths cannot emit misleading settlement events | adversarial tests |
| T40 | P2 | stage-event operator visibility check | Agent B / Agent C | TODO | T17,T32 | `src/observability/*`, docs | decide whether operator surfaces expose event-spine anomalies | review |
| T41 | P2 | event payload provenance taxonomy freeze | Agent B | TODO | T17,T23 | `src/state/db.py`, docs/tests | freeze allowed `source` values for `position_events` | review |
| T42 | P2 | durable event test coverage handoff | Agent B / Agent C | TODO | T24,T34 | `progress.md`, `task.md` | explicit testing baton once schema settles | handoff note |
| T43 | P2 | event-spine storage growth watch | Agent B / Agent C | TODO | T27,T36 | `src/state/db.py`, docs | note retention/index concerns before wider rollout | review |
| T44 | P2 | order telemetry for rejected live entries | Agent B | TODO | T10,T25 | `src/execution/executor.py`, `tests/` | explicit rejected-order event payload | unit tests |
| T45 | P2 | entry telemetry for pending live entries | Agent B | TODO | T10,T21 | `src/engine/cycle_runtime.py`, `tests/` | assert pending-tracked entry emits attempt metadata | DB assertions |
| T46 | P2 | event-spine consistency grep after wider wiring | Agent B / Agent C | TODO | T22,T31,T34 | `src/`, `tests/` | re-audit names and helper use after more writers land | grep audit |
| T47 | P2 | event spine migration note in progress docs | Agent B | TODO | T17,T20,T26 | `progress.md` | record whether backfill is required or intentionally skipped | file review |
| T48 | P2 | event spine contract merge with lifecycle planner | Agent A / Agent B | TODO | T3,T17,T31 | `progress.md`, `task.md` | merge lifecycle transition ownership with event emission coverage | planning review |
| T49 | P2 | event spine follow-up backlog cleanup | Main | TODO | T23,T31,T32 | `task.md`, `progress.md` | collapse/reprioritize durable-event follow-ups after review | planning review |
| T50 | P2 | event-spine replay consumer tests | Agent B | TODO | T24,T28 | `tests/` | prove replay bridge choice does not break existing semantics | pytest targets |
| T51 | P2 | stage-event chronicle deprecation check | Agent B / Agent C | TODO | T22,T37 | `src/state/chronicler.py`, consumers | determine whether chronicle can deprecate stage semantics later | review |
| T52 | P2 | durable event env filter tests | Agent B | TODO | T27,T41 | `tests/` | verify paper/live filtering behavior on `position_events` | pytest targets |
| T53 | P2 | settlement event consumer handoff to Agent C | Agent B / Agent C | TODO | T20,T38 | `progress.md`, `task.md` | explicit baton pass once settlement reader contract is stable | handoff note |
| T54 | P2 | event telemetry secret/leak audit | Agent B / Agent C | TODO | T23,T41 | `src/state/db.py`, consumers | ensure payload growth does not expose unsafe fields | review |
| T55 | P2 | event naming reconciliation with decision artifacts | Agent B / Agent C | TODO | T20,T23 | `src/state/decision_chain.py`, `src/state/db.py` | avoid semantic drift between cycle artifact and stage event names | review |
| T56 | P2 | event writer helper simplification | Agent B | TODO | T30,T36 | `src/state/db.py` | reduce helper sprawl once stable | code review |
| T57 | P2 | review whether decision_chain needs stage-query helpers | Agent B | TODO | T28,T30 | `src/state/decision_chain.py` | decide helper location from first reader needs | design review |
| T58 | P2 | wider event reader test pack | Agent B / Agent C | TODO | T23,T24,T50 | `tests/` | build non-happy-path reader coverage after contract freeze | pytest targets |
| T59 | P2 | event spine review follow-up cleanup | Main | TODO | T32,T49 | `task.md`, `progress.md` | integrate review feedback into next slices | planning review |
| T60 | P2 | durable event slice final handoff | Agent B | TODO | T32 | `progress.md`, teammate handoff | concise summary for main/next agent after review | handoff note |
| T61 | P2 | event-spine storage retention question | Agent B / Agent C | TODO | T43 | `progress.md`, docs | decide if retention/pruning policy is needed before live rollout | review |
| T62 | P2 | settlement artifact vs stage event consumer split | Agent B / Agent C | TODO | T20,T29,T55 | `src/state/decision_chain.py`, readers | define what continues to read decision artifacts after stage events exist | design review |
| T63 | P2 | lifecycle rescue emits event audit | Agent B / Agent A | TODO | T21,T4 | `src/state/chain_reconciliation.py`, `src/state/db.py`, tests | ensure chain rescue path appends lifecycle events once ownership is agreed | DB assertions + tests |
| T64 | P2 | event-spine smoke handoff to Agent C | Agent B / Agent C | TODO | T24,T42 | `progress.md`, `task.md` | explicit baton pass for adversarial coverage on new events | handoff note |
| T65 | P2 | event source naming consistency review | Agent B | TODO | T23,T41,T55 | `src/state/db.py`, tests/docs | keep `source` values and event names coherent | review |
| T66 | P2 | durable event slice acceptance check | Main | TODO | T32,T35 | `progress.md`, `task.md` | decide whether shipped slice is accepted as base for next work | review |
| T67 | P2 | event path paper/live parity audit | Agent B / Agent C | TODO | T18,T24,T52 | `src/`, `tests/` | check paper vs live event emission differences before wider use | audit |
| T68 | P2 | event-spine TODO cleanup after acceptance | Main | TODO | T49,T66 | `task.md`, `progress.md` | trim backlog once acceptance decisions are made | planning review |
| T69 | P2 | durable event slice retrospective note | Agent B | TODO | T32,T60 | `progress.md` | capture what this first slice intentionally did not solve | file review |
| T70 | P2 | event-spine consumer onboarding note | Agent B / Agent C | TODO | T17,T23,T53 | `progress.md`, `task.md` | explain prerequisites before any reader migrates to `position_events` | handoff note |
| T71 | P2 | stage-event null-field audit | Agent B | TODO | T24,T41 | `tests/`, `src/state/db.py` | verify optional fields behave predictably across event types | DB assertions |
| T72 | P2 | stage-event query helper tests | Agent B | TODO | T24,T57 | `tests/` | cover `query_position_events` behavior and limits | pytest targets |
| T73 | P2 | event payload size spot check | Agent B / Agent C | TODO | T36,T43 | `tests/`, docs | estimate payload size before more telemetry lands | review |
| T74 | P2 | event-spine shared vocabulary note | Agent A / Agent B / Agent C | TODO | T23,T48,T55 | `progress.md`, `task.md` | freeze shared terms across lifecycle and event work | planning review |
| T75 | P2 | durable event backlog pruning after team sync | Main | TODO | T49,T68,T74 | `task.md` | clean up follow-up list after next sync | planning review |
| T76 | P2 | stage-event read API placement review | Agent B | TODO | T28,T57 | `src/state/db.py`, `src/state/decision_chain.py` | settle helper placement before more readers appear | design review |
| T77 | P2 | supplemental-writer invariant note | Agent B | TODO | T35,T69 | `progress.md` | record that `position_events` stays supplemental until explicit authority switch | file review |
| T78 | P2 | durable event pending/exit coverage gap list | Agent B | TODO | T18,T21,T31 | `progress.md`, `task.md` | keep explicit gap list for next implementation slice | handoff note |
| T79 | P2 | event-spine readiness gate for RiskGuard consumers | Agent B / Agent C | TODO | T17,T20,T38 | `progress.md`, `task.md` | define prerequisites before RiskGuard reads stage events | planning review |
| T80 | P2 | follow-up collapse after main acceptance | Main | TODO | T66,T68,T75 | `task.md` | simplify backlog once main accepts/rejects this slice | planning review |
| T81 | P2 | durable event test naming cleanup | Agent B | TODO | T34,T58,T72 | `tests/` | keep test names aligned with frozen event taxonomy | review |
| T82 | P2 | query_position_events consumer note | Agent B | TODO | T17,T57,T76 | `progress.md` | note that query helper is provisional until first real consumer lands | handoff note |
| T83 | P2 | event-spine live exit telemetry dependency note | Agent B | TODO | T18,T78 | `progress.md` | keep explicit reminder that sell-side telemetry is still missing | handoff note |
| T84 | P2 | supplemental settlement dual-write note | Agent B | TODO | T29,T69 | `progress.md` | document why settlement currently writes chronicle + decision_log + position_events | file review |
| T85 | P2 | durable event implementation slice closeout | Agent B | TODO | T32,T69,T77 | `progress.md`, teammate handoff | final closeout note for this slice | handoff note |
| T86 | P2 | backlog prune after closeout | Main | TODO | T80,T85 | `task.md` | remove low-value follow-ups after closeout | planning review |
| T87 | P2 | event-spine invariant candidate list | Agent C | TODO | T24,T64,T79 | `tests/`, `progress.md` | list invariants once event schema stabilizes enough for adversarial work | planning review |
| T88 | P2 | authority-switch prerequisite checklist | Main / Agent B / Agent C | TODO | T17,T20,T35,T79 | `progress.md`, `task.md` | checklist before `position_events` can become more than supplemental | planning review |
| T89 | P2 | event-spine handoff to main review | Agent B | TODO | T32,T85 | `progress.md`, teammate handoff | send concise review-ready summary to main | handoff note |
| T90 | P2 | backlog sanity check after additions | Main | TODO | T49,T80,T86 | `task.md` | collapse accidental backlog sprawl | planning review |
| T91 | P2 | supplemental event-writer grep check | Agent B | TODO | T22,T31,T46 | `src/`, `tests/` | verify new helpers are only used on intended paths | grep audit |
| T92 | P2 | event-spine paper-mode realism note | Agent B | TODO | T10,T67,T83 | `progress.md` | note that paper fills still overstate execution even with telemetry | file review |
| T93 | P2 | stage-event consumer contract review with Agent C | Agent B / Agent C | TODO | T17,T20,T53 | `progress.md`, `task.md` | sync before tracker/riskguard reader migration | planning review |
| T94 | P2 | durable event line-item cleanup after review | Agent B | TODO | T32,T59,T66 | `progress.md`, `task.md` | remove superseded notes once reviewed | file review |
| T95 | P2 | event-spine closure criteria note | Main / Agent B | TODO | T66,T88 | `progress.md` | define when this lane counts as structurally closed | planning review |
| T96 | P2 | task backlog compaction | Main | TODO | T90 | `task.md` | compact durable-event follow-ups into a smaller set after sync | planning review |
| T97 | P2 | first consumer selection for position_events | Main / Agent B / Agent C | TODO | T17,T28,T88 | `progress.md`, `task.md` | choose replay, tracker, or riskguard as first real reader | planning review |
| T98 | P2 | durable event next-slice proposal | Agent B | TODO | T18,T19,T21,T78 | `progress.md`, teammate handoff | propose smallest next implementation slice after review | handoff note |
| T99 | P2 | backlog cleanup after first consumer choice | Main | TODO | T96,T97 | `task.md` | rebase follow-ups around chosen first consumer | planning review |
| T100 | P2 | durable event lane retrospective | Main / Agent B / Agent C | TODO | T95,T97 | `progress.md` | capture lessons after first reader migration begins | retrospective |
| T101 | P2 | remove accidental backlog explosion | Main | TODO | T90,T96,T99 | `task.md` | collapse this list to sane size | planning review |
| T102 | P2 | event lane task reset | Main | TODO | T101 | `task.md` | replace sprawling follow-ups with concise tracked tasks | planning review |
| T103 | P2 | sanity guard against future task bloat | Main | TODO | T102 | `task.md`, process | keep future follow-up lists concise | review |
| T104 | P2 | follow-up task compaction memo | Main | TODO | T101,T102 | `progress.md` | explain which durable-event follow-ups survived compaction | memo |
| T105 | P2 | close accidental task sprawl incident | Main | TODO | T103,T104 | `progress.md`, `task.md` | explicitly mark sprawl corrected | review |
| T106 | P2 | durable event lane reset after compaction | Main / Agent B | TODO | T105 | `task.md`, `progress.md` | restart from concise queue | planning review |
| T107 | P2 | final prune of low-value follow-ups | Main | TODO | T106 | `task.md` | keep only immediate next actions | planning review |
| T108 | P2 | durable event next action shortlist | Main / Agent B | TODO | T107 | `progress.md`, `task.md` | name only the next 3-5 actions worth doing | planning review |
| T109 | P2 | clean up after over-expansion | Main | TODO | T105,T107 | `progress.md` | note that backlog over-expansion was corrected | review |
| T110 | P2 | restore queue discipline | Main | TODO | T103,T108 | `task.md` | enforce concise queue again | planning review |
| T111 | P2 | durable event concise baton pass | Agent B | TODO | T108 | `progress.md`, teammate handoff | short handoff after queue compaction | handoff note |
| T112 | P2 | verify no more task sprawl | Main | TODO | T110 | `task.md` | keep the queue sane | review |
| T113 | P2 | collapse durable event follow-ups to essentials | Main | TODO | T112 | `task.md` | final compaction | planning review |
| T114 | P2 | keep only active durable event blockers | Main | TODO | T113 | `task.md` | remove speculative follow-ups | planning review |
| T115 | P2 | close this compaction chain | Main | TODO | T114 | `task.md`, `progress.md` | stop the self-expanding backlog | review |
| T116 | P2 | reset durable-event task section cleanly | Main | TODO | T115 | `task.md` | restore a sane durable-event subsection | planning review |
| T117 | P2 | concise durable-event handoff after reset | Agent B | TODO | T116 | `progress.md`, teammate handoff | short next-step summary only | handoff note |
| T118 | P2 | maintain concise task hygiene | Main | TODO | T116 | `task.md`, process | avoid repeating this expansion pattern | review |
| T119 | P2 | durable-event lane immediate next tasks only | Main | TODO | T116 | `task.md` | keep just the next few valuable tasks | planning review |
| T120 | P2 | final sanity check on task queue | Main | TODO | T119 | `task.md` | ensure queue is concise again | review |
| T121 | P2 | close task-sprawl cleanup | Main | TODO | T120 | `progress.md`, `task.md` | explicitly end cleanup thread | review |
| T122 | P2 | concise next slice note | Agent B | TODO | T119 | `progress.md` | capture the next smallest implementation slice only | memo |
| T123 | P2 | durable-event lane back to normal | Main | TODO | T121 | `task.md`, `progress.md` | resume normal queue discipline | review |
| T124 | P2 | no more speculative task floods | Main | TODO | T123 | `task.md`, process | enforce concise additions going forward | review |
| T125 | P2 | queue sanity maintained | Main | TODO | T124 | `task.md` | final guardrail | review |
| T126 | P2 | stop here | Main | TODO | T125 | `task.md` | stop expanding | review |
| T12 | P1 | strategy analytics rebuild from authoritative events | Agent B / C | TODO | T9,T10 | `src/state/strategy_tracker.py`, ledger readers | rebuilt derived analytics or narrowed scope | regression tests |
| T13 | P1 | RiskGuard input contract upgrade | Agent C | REVIEW | T9,T12 | `src/riskguard/riskguard.py`, `src/riskguard/metrics.py`, `src/state/decision_chain.py`, `src/state/db.py` | first consumer switch landed for settlement-source provenance; broader strategy/execution authority still separate | riskguard tests |
| T14 | P2 | adversarial invariant test pack | Agent C | REVIEW | T4,T5,T6,T7,T9,T10,T13 | `tests/` | invariant and regression coverage now includes rescued-fill exactly-once runtime proof plus RiskGuard settlement-source provenance coverage; broader execution-path validation still open | pytest targets |
| T15 | P2 | delete / shrink fake runtime teeth | Agent C | REVIEW | T1 | `src/observability/*`, `src/state/strategy_tracker.py`, `src/engine/cycle_runtime.py`, runtime mirrors | removal list + actual deletions; fake deferred-fill tracker contract removed | grep/tests |
| T16 | P2 | remove bad metrics / legacy illusion / non-authoritative views | Agent C | REVIEW | T12,T13,T15 | `src/state/strategy_tracker.py`, `src/riskguard/riskguard.py`, `src/riskguard/metrics.py`, `src/engine/cycle_runtime.py`, docs/tests | semantics cleanup slice landed; tracker win-rate authority removed; authoritative reader migration still separate | tests + grep |

## Current Program Queue (authoritative live set)

Use this compact set as the current queue truth. The older historical backlog above and the earlier `R1`-`R5` recovery queue are preserved for audit context, but they are no longer the controlling live queue after the user dissolved the previous team and added the external research expansion.

| Program ID | Priority | Title | Owner | Status | Why now |
|---|---|---|---|---|---|
| P0-A | P0 | Reset runtime ownership truth after team dissolution | Main | REVIEW | Old `repair/adversary` baton truth was stale and has now been rewritten out of the live control surfaces. |
| P0-B | P0 | Freeze core contracts: canonical settlement payload, `ExitContext v2`, lifecycle transition ownership | Main + runtime/truth lanes | DONE | Canonical settlement contract, degraded-authority seam, and `ExitContext v2` are landed, reviewed, and validated. |
| P0-C | P0 | Unify clock / target semantics across evaluator, Day0, and general ensemble slicing | Main + time-semantics lane | DONE | Day0 remaining semantics, GFS target-day slicing, and degraded snapshot clock metadata are landed and validated. |
| P0-D | P0 | Close runtime spine: pending/live fail-closed, Day0 terminal phase, durable transition/event emission | runtime lane | IN_PROGRESS | Slices 1-6 are landed: phantom-void, stale-Day0 freshness, pending/live verification owner contraction, Day0 exit authority, normal fill accounting convergence, durable Day0 lifecycle timestamping, RiskGuard bootstrap fail-closed hardening (`no risk row` no longer returns false GREEN), and runtime enum/value normalization in `status_summary` (no mixed `ChainState.UNKNOWN` vs `unknown` truth). Remaining P0-D work is any final reconciliation of multi-surface runtime truth and optional deeper execution-attribution tightening. |
| P1-E | P1 | Migrate learning loop to authoritative ledger + evaluated opportunity set | truth/learning lane | IN_PROGRESS | Slices landed so far: env-filtered authoritative settlement readers, richer operator status/failure truth, durable rejected-entry execution telemetry, execution-aware RiskGuard details, a canonical execution read model surfaced into status, a first current-regime strategy truth surface, strategy diagnostics in RiskGuard, improved healthcheck/request-status diagnosis, first YELLOW gating on execution decay / edge compression, no-trade drilldown in healthcheck, real strategy gates in the control plane, risk details mirrored into status, control-plane truth mirrored into status, runtime backlog counts surfaced into status, healthcheck mirroring of control/runtime state, cycle failure lowering health, env-filtered no-trade diagnostics, a real tighten-risk effect on sizing, actionable recommendations from RiskGuard, those risk recommendations mirrored into healthcheck, recent no-trade counts surfaced into status, strategy gate/recommendation state fused into one status surface, a unified current-regime learning surface, alignment between supervisor contracts and the real control plane, healthcheck integration with that unified learning surface, a strategy-aware learned-current-regime summary, recommended controls/gates mirrored into the control surface itself, explicit gate drift in the control surface, strategy-specific recommendations informed by authoritative execution truth, control recommendation drift surfaced, a stable builder for explicit recommended commands, a helper that enqueues those recommended commands from status, durable replay of temporary global controls (`pause_entries`, `tighten_risk`, `resume`) across control-state refresh/restart, an explicit split between auto-safe vs review-required recommended commands so default automation no longer silently applies strategy-gate flips, structured rationale for recommended controls/gates mirrored into status plus generated command notes, strategy-tagged `NoTradeCase` attribution so `learning.by_strategy` now includes per-strategy no-trade counts/stages instead of only settlement/execution truth, a unified `status.strategy` surface that now merges operator truth with those learning/regime counts instead of making humans join two different surfaces by hand, symmetric review-required gate commands so gate-drift can propose both disable and re-enable actions rather than only one-way shutdowns, fail-closed stale-contract detection so consumer tools reject outdated status/risk schemas instead of silently trusting missing command fields or polluted recent-window no-trade stats, `launchctl print gui/<uid>/<label>` fallback so live health checks can still prove daemon/riskguard liveness from Python subprocesses in this environment, a more robust edge-compression contract that now requires both real elapsed time and enough samples before recommending a strategy gate, an operational recovery path that restored healthy runtime truth after restart by archiving broken WAL/derived DB state and refreshing authority surfaces, live tracker backfill for `current_regime_started_at` so the attribution surface now carries a truthful regime-start timestamp without requiring a separate rebuild step, and learning-surface filtering by `current_regime_started_at` so current-regime status now uses the actual regime boundary instead of only rolling-window heuristics. Remaining work is deeper learning migration plus stronger strategy/current-regime gating and policy automation decisions. |
| P1-F | P1 | Detailed review lane for landed slices | detailed-review lane | READY | Every landed slice needs file-level correctness review before acceptance. |
| P1-G | P1 | Adversarial review lane for landed slices | adversarial lane | READY | Every landed slice needs fail-open / false-authority challenge before closure. |
| P2-H | P1 | Forecast-layer Phase-1 de-hardcode: Day0 backbone, lead-continuous mean/sigma, heteroscedastic sigma | forecast lane | BLOCKED | Valid only after `P0-C` proves unified time semantics and `P0-D` stops runtime truth loss. |
| P2-I | P2 | Gate / decision learned policy work (`alpha`, `conflict`, timing, richer exit policy`) | Main | BLOCKED | Must wait for thicker verified samples and clean runtime/forecast truth. |

### Live Baton / Queue Rules
- Current baton mode is **`solo`** after closing the post-reset implementation and review lanes for `P0-B`/`P0-C`.
- There is currently no active durable baton; next claimable work starts at `P0-D`.
- Durable teammates should only be created for:
  - runtime implementation lane
  - time-semantics implementation/audit lane
  - detailed review lane
  - adversarial review lane
- One-off archaeology, truth checks, schema/function design lookups, and external-fact retrieval go to bounded `explore` subagents instead of durable teammate lanes.
- Acceptance order for each landed slice:
  1. implementation evidence
  2. detailed review
  3. adversarial review
  4. main-thread integration/closure

### Mapping Note
- Earlier recovery items map into the new queue as follows:
  - `R1` folds into `P0-B`
  - `R2` and parts of `R3` fold into `P0-D`
  - `R4` folds into `P1-E`
  - `R5` folds into `P1-F`/`P1-G` and later `P2-H`

### Historical Backlog Note
- The large task inventory above records how the prior round evolved, including task-sprawl mistakes. It remains audit context only.
- Queue truth for current work now lives in `P0-A` through `P2-I` plus `.claude/baton_state.json`.
- Future compaction may archive or delete the stale historical rows once no longer needed for recovery archaeology.

## Claim Rules
1. Before starting, change task status to `IN_PROGRESS` and write the owner.
2. After finishing, change status to `REVIEW` or `DONE`.
3. Record actual touched files, unresolved edges, and required baton pass in `progress.md`.
4. If a new task is discovered, add it here before doing the work.
5. If a task loses value or is superseded, mark `DROPPED` with reason in `progress.md`.

## Queue Policy
- Always prioritize P0 structural closure.
- No attractive side work before runtime-spine hardening.
- Finish a task by updating both `progress.md` and `task.md`, then claim the next highest-value task.
