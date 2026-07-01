# Market-Structure Code Atlas (谱图) — the complete map of how Polymarket reality lives in Zeus code

# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: state_vocabulary_canonical_redesign_2026-06-29.md §12 (round-2 ideal, consult-confirmed);
#   live reducer trace (2026-06-30); grounded in fitz-s/zeus @ 33e80d10e. Companion enforcement:
#   tests/test_inv_cl1_no_raw_venue_status_branching.py, tests/test_single_writer_per_state_mechanism.py.
# Status: FOUNDATION MAP. Layer 3 (runtime cycle) + §7 (three-goal weak-point audit) land from the
#   runtime trace + consult round-3 this session.

## 0. Purpose & how to read

This atlas is the **stable foundation map**: for the whole market structure, it names — with file:line
anchors — the single owner of every fact, the reducer that produces it, the projection that derives from
it, and the invariant that keeps it from rotting. Three goals it serves (operator, 2026-06-30):
**地基稳固** (solid foundation = one truth-owner per mechanism, no drift), **运行效率高** (efficient runtime
= no redundant reads/writes, no fan-out amplification), **符合真实运行逻辑** (conforms to how the venue
actually behaves — models only what Polymarket exposes, models all of it).

Read top-down: **Layer 0** is the only external reality; **Layer 1** is what Zeus *stores* (append-only
facts + Zeus decisions); **Layer 2** is what Zeus *derives* (never stores as truth); **Layer 3** is the
runtime cycle that reads/writes them; **Layer 4** is the invariant set. If a value is not in Layer 0 and
not a Layer-1 Zeus decision the next cycle needs, it is a projection or a scar — see §12 of the design doc.

## 1. Layer 0 — Polymarket external reality (the ONLY external facts)

A weather-derivatives engine observes exactly five stateful mechanisms on Polymarket. Everything else is
internal and must justify itself.

| # | External mechanism | Real states | Nuances the code must honor |
|---|--------------------|-------------|------------------------------|
| L0.1 | CLOB order | live / matched / canceled / expired | insert responses use `live/matched/delayed/unmatched`; a **delayed** match is real; a **heartbeat miss cancels open orders**; a partial is `matched_size < order_size`, not a separate status |
| L0.2 | Matched trade | matched → mined → confirmed (retry/failed) | post-match on-chain settlement of the trade; distinct from order state |
| L0.3 | Conditional token balance (ERC-1155) | a **quantity** of YES / NO shares | **there is NO on-chain "position status"** — a position is a balance; "closed/quarantined/settled" are Zeus inventions |
| L0.4 | UMA optimistic-oracle resolution | proposed → dispute-liveness → resolved(YES/NO) / void-50-50 | dispute window and 50-50 void are **action-changing** and must stay distinct; do not collapse to one "unresolved" flag |
| L0.5 | Redemption | winning tokens → USDC on-chain | **Zeus never submits** the redeem tx (third-party auto-redeem owns it); Zeus records intent / observes tx / confirms |

## 2. Layer 1 — the STORED mechanisms (append-only facts + Zeus decisions; ONE reducer each)

Each row is a truth-owner. The **reducer** is the single function that decides the stored value from facts;
the **ingress normalizer** is the ONLY sanctioned home for raw venue strings (INV-CL-1). Anchors verified by
live trace 2026-06-30.

| Mechanism | Table.column | Reducer (single owner) | Emits enum | Ingress normalizer |
|-----------|--------------|------------------------|------------|--------------------|
| A1 Command/outbox truth | `venue_commands.state` | `append_event()` — `state/venue_command_repo.py:1080` (via `_TRANSITIONS`) | `CommandTruthState` `command_bus.py:45` | `normalize_command_truth_state()` `canonical_lifecycle.py:264` |
| A2 Venue order fact | `venue_order_facts.state` | `VenueOrderTruthReducer.reduce()` — `execution/order_truth_reducer.py:85` | `VenueOrderStatus`/`OrderProofClass` `canonical_lifecycle.py:57,106` | `normalize_venue_order_status()` `canonical_lifecycle.py:204` |
| A3 Venue trade fact | `venue_trade_facts.state` | *(none — append-only base fact; correct)* | `VenueTradeStatus` `canonical_lifecycle.py:68` | `normalize_venue_trade_status()` `canonical_lifecycle.py:246` |
| A4 Exposure claim | `position_lots.state` | *(none — 2-value direct claim; correct)* | `ExposureState` `canonical_lifecycle.py:98` | schema CHECK only (optimistic/confirmed) |
| A7 Chain visibility | *(per-cycle classifier)* | `classify_chain_state()` — `state/chain_state.py:71` | `ChainSnapshotCompleteness` `chain_state.py:49` | none (pure classifier) |
| A8 Settlement resolution | `settlement_outcomes.resolution_state` | `settlement_resolution_state_from_row()` — `contracts/settlement_axes.py:186` | `SettlementResolutionState` `settlement_axes.py:130` | `classify_settlement_outcome()` `settlement_outcome.py:164` |
| A10 Redemption accounting | `settlement_commands.state` | `redemption_accounting_phase()` — `contracts/settlement_axes.py:289` | `RedemptionAccountingPhase` `settlement_axes.py:265` | dict map `_SETTLEMENT_STATE_TO_REDEMPTION_PHASE` `settlement_axes.py:277` |

**A3/A4 have no reducer by design** — trade facts are immutable append-only rows and exposure is a direct
optimistic-vs-confirmed claim; there is nothing to reduce. This is the fact-log ideal, not a gap.

## 3. Layer 2 — the PURE PROJECTIONS (derived on read; NEVER stored as independent truth)

| Projection | Function | Source facts | Verified pure? |
|------------|----------|--------------|----------------|
| A5 Position phase | `derive_position_phase()` `state/canonical_projections.py:214` | admin/void/settlement/economic-close/quarantine flags + exposure + entry-intent + chain/exit fallbacks | writes = returns only, 0 conditional reads ✅ (but a materialized `position_current.phase` cache has 7 writers — §7 / INV-OWNER-1) |
| A6 Exit progress | `derive_exit_progress()` `state/canonical_projections.py:76` | exit-command presence + order proof class + retry/backoff | pure ✅ |
| A9 Economic outcome | `economic_outcome_for_position()` `settlement_axes.py:239` (Direction Law) | winning bin × position direction | pure ✅ (audit receipt only) |
| A10 view / redemption phase | `redemption_accounting_phase()` | `settlement_commands.state` | pure ✅ |

## 4. Layer 3 — the runtime cycle (real read/write flow per money-path stage)

The daemon is a **mesh of scheduled jobs** (APScheduler), not one loop. Each stage reads/writes a specific
subset of the Layer-1 mechanisms; cadences and owners are verified against `src/main.py` @ 33e80d10e.

| Stage | Job (cadence) | Reads | Writes | Entry point |
|-------|---------------|-------|--------|-------------|
| 1 Entry select + submit | `edli_event_reactor` (60s) | settlement_commands (in-flight gate) | venue_commands, position_lots, venue_order_facts | `_edli_event_reactor_cycle` `main.py:4230` |
| 2 Fill realization | P4 user-channel (async WSS) | — | venue_order_facts, venue_trade_facts, position_lots | `polymarket_user_channel.py:167,364` |
| 3 Monitor + exit decision | `exit_monitor` (2 min) | position_current, position_lots, venue_order_facts, venue_trade_facts, settlement_commands | position_current (exit_state) | `_exit_monitor_cycle` `main.py:9989` → `evaluate_exit` `portfolio.py:897` |
| 4 Exit submit + sell fill | (cont. of 3) + P4 | — | venue_commands (SELL), venue_order_facts, venue_trade_facts, position_lots, position_current | `execution/exit_lifecycle.py` |
| 5 Settlement / resolution | harvester (P3 sidecar) + `skill_attribution` (30 min) | settlement_outcomes, position_lots, position_current, venue_trade_facts | settlement_outcomes, position_current (settled) | `write_settlement_with_era_provenance` `settlement_writers.py:121`; `_settlement_skill_attribution_tick` `main.py:1369` |
| 6 Redemption accounting | `wrap_proceeds` (settlement tick); redeem submitter **PR-I.5 pending** | settlement_outcomes, settlement_commands, position_current | settlement_commands (REDEEM_INTENT_CREATED) | `_wrap_proceeds_same_tick` `main.py:1439` |

**Real-logic facts the cycle already honors (符合真实运行逻辑):** fill is an ASYNC sidecar — no push to the
order daemon; it polls `venue_order_facts` (Layer 0 has no fill push). The world-write mutex never spans a
venue HTTP call (PR#404 P0-2). Settlement writes are cross-DB atomic (INV-37 ATTACH+SAVEPOINT). Redemption
*submit* is deliberately NOT live (PR-I.5) — consistent with "Zeus never submits" (L0.5).

**Efficiency antipatterns (运行效率高 lever) — from the live trace:**
- **`position_current`**: full-table load every 2-min exit cycle (O(positions×lots)); **60+ row writes per
  cycle** on transitions; the write-lock is held for the ENTIRE monitoring phase; `evaluate_exit` runs
  per-position, not batched. → in-cycle cached snapshot + write-through only on exit-state change.
- **Repeated per-cycle reads**: the EDLI redecision screen opens multiple read connections and recomputes
  overlapping candidate/rest/held-family scopes in one tick — the real efficiency lever is a single memoized
  `CycleMarketStateSnapshot` per tick, NOT indexes (the `(state,…)` indexes `idx_order_facts_state`,
  `idx_position_lots_state`, `idx_venue_commands_state` already exist in `db.py` — the trace's "missing index"
  note was wrong, corrected in §7B).
- **Command-recovery breadth**: 54 writes / 319 reads across 5 axes on a tight cadence — split into
  fact-specific reducers.
- **Write fan-out**: `position_current` written by **7 modules** (INV-OWNER-1 baseline) — the worst
  amplification; the single-reducer collapse (§7D) removes it and the per-cycle write storm at once.
  **This is where 地基稳固 and 运行效率高 are the same fix.**

## 5. Layer 4 — the invariants that keep the atlas solid (the anti-rot antibodies)

Consult round-3 finalized the set. Two invariants + the atlas-anchor guard are landed; four are proposed with
cheap enforcement defined.

| Invariant | Forbids | Enforcement | Status |
|-----------|---------|-------------|--------|
| **INV-CL-1** | branching on raw venue status strings outside ingress normalizers | lexical antibody, baseline shrinks | ✅ `tests/test_inv_cl1_no_raw_venue_status_branching.py` (baseline 9→3) |
| **INV-OWNER-1** | a second SQL writer for a stored mechanism | writer-set antibody, per-mechanism baseline | ✅ `tests/test_single_writer_per_state_mechanism.py` (locks 4, baselines 3; `position_current`=7 pinned) |
| **INV-ATLAS-1** | an atlas-named reducer/normalizer/cycle owner silently moving/renaming (map rot) | anchor-existence antibody | ✅ `tests/test_market_structure_atlas_anchors.py` (20 anchors) |
| **INV-REVIEW-1** | new `*_quarantined/*_wiped/*_suspected/*_failed` scar members in a **lifecycle** enum/CHECK | lexical baseline over the position/chain lifecycle vocabularies (may only shrink) | ✅ landed this session (`tests/test_no_new_scar_state.py`) |
| **INV-PROJ-1** | a projection phase that no event ever produced (a writer bypassing the event log) | replay-diff: every `position_current.phase` must be event-SOURCED — produced by some `position_events.phase_after` (`scripts/dev/replay_position_phase.py --assert-no-diff`) + fixture CI test | ✅ landed (`tests/test_inv_proj1_phase_projection_recomputable.py`). **Correction 2026-06-30:** a first naive cut ("== latest event phase_after") false-positived on 2 positions where observational MONITOR_REFRESHED/REVIEW_REQUIRED events carry a stale phase_after the A5 authority-aware reducer correctly overrides (voided-then-active-monitor; economically_closed-then-quarantine). The corrected event-SOURCING check finds **0 live drift** — every stored phase is event-sourced, so the phase projection is consistent across all live positions. Still the **§7D enabler**: gate each writer-removal on `--assert-no-diff`. |
| **INV-REDUCER-1** | a weaker fact regressing a stronger proof (chain-absence voiding a confirmed fill; `VENUE_WIPED` over positive trade proof; stale terminal reopening state) | property test over the proof lattice: CONFIRMED fill > MATCHED/MINED > partial > open > absence | proposed |
| **INV-INGRESS-2** | an unknown venue status silently defaulting instead of fail-loud; missing `delayed` support | golden fixtures from captured payloads → canonical member or typed `UnmappedVenueStatus` | proposed (normalizers already fail-loud; add `delayed` model) |
| **INV-CYCLE-1** | hot-cycle modules each doing their own full scan for the same mechanism | query-count instrument; assert cycle consumes `CycleMarketStateSnapshot` | proposed |

## 6. Physical placement (K1 three-DB split) — the db种类 map (VERIFIED 2026-06-30 by row-probe + registry)

**Partition principle: split by WRITE-TRANSACTION class, not by domain noun.** A table lives in the DB whose
writer owns its atomic transaction — so `settlement_outcomes`/`settlements` are FORECAST-class (harvester
co-transaction), `settlement_commands` is TRADE-class (redeem/wrap accounting), `observations` is
forecast-class but `hourly_observations` is world-class. Canonical authority = `architecture/db_table_ownership.yaml`
(PK `(name, db)`, INV-05 fail-closed set-equality; loader `state/table_registry.py`; K1 split commit `eba80d2b9d`).

| DB file (separator) | Size | Class (live tables) | Owns (verified max-rowid) |
|---|---|---|---|
| `state/zeus-world.db` (hyphen) | 73 GB | world_class (71) | hourly_observations (26.3M), decision_certificates (1.3M), settlement_attribution (177), model_bias, forecast_skill, calibration/learning history |
| `state/zeus-forecasts.db` (hyphen) | 39 GB | forecast_class (21) | raw_model_forecasts (634k), ensemble_snapshots (1.19M), forecast_posteriors (16k), **settlement_outcomes (832k)**, settlements (1.25M), observations (118k), platt_models |
| `state/zeus_trades.db` (**underscore**) | 76 GB | trade_class (22) | position_current/lots/events (713/201/95k), venue_commands/order_facts/trade_facts (845/17992/534), settlement_commands (83), executable_market_snapshots (9.1M), execution_fact, trade_decisions (4643) |
| `state/risk_state.db` | 242 MB | risk_class | risk_actions/state (live-only) |
| `state/zeus_backtest.db` | 58 MB | backtest_class | derived audit — NEVER runtime authority |

**§6 CORRECTION (2026-06-30):** an earlier draft claimed `settlement_outcomes` is "trade-class in `zeus_trades.db`".
That is **WRONG** — `settlement_outcomes` is **forecast-class** (832k rows in `zeus-forecasts.db`; ABSENT in
`zeus_trades.db`; every cross-DB read is `FROM forecasts.settlement_outcomes`; the registry `cutover_evidence`
trade-class list names `settlement_commands`, not `settlement_outcomes`). Only the six core vocab tables +
`settlement_commands` are trade-class. CHECK-constraint migrations stay single-DB (one SAVEPOINT per table).

**Redundancy on this axis (verified):**
- **Shadow ghosts — 144 of 259 registered (name,db) pairs (56%) are `schema_class: legacy_archived`**: a
  non-owner DB physically carries the table as a 0-row (or straggler) shell. MANAGED: excluded from set-equality,
  **scheduled DROP 2026-08-09** (K1 2026-05-11 + 90-day retention) via `scripts/drop_world_ghost_tables.py`.
- **Straggler rows** in a few ghost copies (world.venue_commands=4, world.venue_order_facts=8,
  world.trade_decisions=4, world.observations=164) — pre-cutover residue; reading the ghost = silent split-brain
  (partial data instead of the owner's full set).
- **Naming schism (UNMANAGED footgun):** world/forecasts use a HYPHEN, trades uses an UNDERSCORE. Wrong-separator
  opens created zero-byte DECOY files (`zeus_world.db`, `zeus_forecasts.db`, `zeus-trades.db`; inert since
  2026-06-18). A wrong-separator open silently makes a NEW empty DB instead of failing.
- **Ghost-masks-a-bug (concrete):** `execution/settlement_commands.py` `submit_redeem` (~:869) reads
  `FROM world.executable_market_snapshots` — the empty world shadow — instead of the 9.1M-row trades owner, so it
  always misses and falls back to the Gamma network API. Had the ghost not existed, K1 cutover would have raised
  "no such table" and forced the fix. (Sibling `_lookup_market_neg_risk_authoritative` :1252 already has the
  trades fallback; submit_redeem was missed. Flagged for TDD fix.)

### 6A. Cross-DB wiring (接线) — VERIFIED
- **Canonical factories in `state/db.py` are correct:** `get_world_connection_with_trades_required`,
  `get_trade_connection_with_world_{required,optional}`, `get_forecasts_connection_with_world`,
  `forecasts_connection_with_trades_flocked` — each ATTACHes the second DB + takes `fcntl.flock` writer locks in
  ALPHABETICAL order (**forecasts < world < trades**) for deadlock-freedom. This IS INV-37 done right.
- **But the ATTACH surface is sprawling: 37 `ATTACH DATABASE` sites across 27 files**, many ad-hoc leaf ATTACHes
  OUTSIDE the factories (settlement_commands.py:756/1275, command_recovery.py:687/707, executor.py:424,
  portfolio.py:2598, cycle_runner.py:83/87, chain_reconciliation.py:1387, substrate_observer.py:2399/2531,
  main.py:4279…). Each bypasses the factory's lock-order discipline → deadlock + INV-37 drift surface.
- **The hot money-path crosses a DB boundary nearly every tick:** exit/monitor reads
  `forecasts.settlement_outcomes` + `forecasts.forecast_posteriors`; skill-attribution JOINs
  `trades.position_current ⋈ forecasts.settlement_outcomes`; portfolio reads `world.decision_certificates`.

### 6B. First-principles ideal (this axis) — proposed, pending consult validation
1. **One separator.** Converge on a single convention + a CI antibody that the only `*.db` paths opened are the
   5 canonical ones — kills the decoy footgun class.
2. **Ghosts drop on schedule (2026-08-09).** Then every DB carries ONLY its owned tables; a wrong-DB read becomes
   a loud "no such table", not a silent empty read that masks bugs.
3. **One ATTACH surface.** Route ALL cross-DB access through the `state/db.py` factories; antibody forbidding
   ad-hoc `ATTACH DATABASE` outside them (analogous to INV-CL-1's ingress-only rule). 37→~6.
4. **Question the hot-path crossing.** A 60s/2min job reading `forecasts.settlement_outcomes` every tick is the
   efficiency + coupling cost of the split. Either the per-cycle `CycleMarketStateSnapshot` (§7B) reads the
   cross-DB tables ONCE per tick, or redraw the boundary so the execution hot-path never crosses.

## 7. Three-goal weak-point audit (地基稳固 / 运行效率高 / 符合真实运行逻辑)

Consult round-3 (thread `6a42bc3d`, web-grounded in Polymarket/UMA docs) — third independent convergence.

### 7A. 地基稳固 — structural correctness
| # | Weak point | Root defect | Fix |
|---|-----------|-------------|-----|
| S1 | **`position_current` 7-writer projection** (the worst) | a materialized projection with 7 writers can diverge from the fact logs | `state/projection.py` becomes the SOLE writer; the other 6 append facts/events + call the projector. Legit-layer vs true-2nd-writer: projection.py=materializer; ledger.py=append-then-project (shouldn't own SQL shape); edli_position_bridge/consolidator/command_recovery/exchange_reconcile/main=repair/orchestration → must emit facts |
| S1 | command-state cross-axis write | `venue_commands.state` stores venue terminals (FILLED/CANCELLED/EXPIRED) that are A2/A3 facts | command repo owns local side-effect state; project venue terminals from A2/A3 (`project_legacy_command_display`) |
| S1 | phantom-void scar | `command_recovery.py:repair_confirmed_phantom_voids` — a chain-absence pass outranks a stronger positive-fill proof | **monotonic reducer**: positive trade proof > absence; the repair path becomes unreachable |
| S1 | chain-state enum accretion | `CHAIN_CONFIRMED_ZERO` was added after its first live firing crashed `load_portfolio` (loader not total) | store balance facts + review items; writer-set-subset antibody |
| S1 | A6 double-owner | exit progress stored in `ExitState` AND `position.order_status` AND exit-command facts | `derive_exit_progress()` the only view; sell truth = exit command + venue facts |
| S2 | A8/A9 forward-prep not retired | legacy `outcome_type` remains a semantic trap (NULL live) | keep legacy-only; populate `resolution_state` only same-transaction with every revision writer |

### 7B. 运行效率高 — runtime efficiency
| # | Weak point | Fix |
|---|-----------|-----|
| S2 | repeated per-cycle DB reads (EDLI redecision opens multiple read conns, recomputes overlapping scopes) | build ONE typed `CycleMarketStateSnapshot` per tick (beliefs, open rests, held families, exposure, chain completeness, settlement eligibility) |
| S2 | command-recovery broad scan (**54 writes / 319 reads across 5 axes** on a tight cadence) | split into fact-specific reducers; memoize the unresolved-command set |
| S2 | `position_current` write amplification (repairs UPDATE the row directly + a companion event) | append one fact/event; projector materializes once at end of txn |
| S3 | **exemplars to copy**: `wrap_unwrap_commands.py` CAS predecessor + terminal absorption; `chain_reconciliation.py` immutable per-cycle `ChainPositionView` | replicate these patterns into the other reducers |

*Correction to §4: the trace's "missing index" note was wrong — `idx_order_facts_state`, `idx_position_lots_state`, `idx_venue_commands_state` all exist (`db.py`). The real efficiency lever is per-cycle read memoization + killing the write fan-out, not new indexes.*

### 7C. 符合真实运行逻辑 — conforms to real market logic
| # | Weak point | Fix |
|---|-----------|-----|
| S1 | "position status" over-modeling (`ChainState`/phase/quarantine model a lifecycle Polymarket never exposes — a position is a token balance) | external truth = token balance; local phase = projection; review = work item (`chain_reconciliation.py` already emits `ChainOnlyFact`, no synthetic Position — finish it) |
| S1 | `VENUE_WIPED` as a venue order state | it's an **absence inference**, not a CLOB state → `ReconcileFinding.ORDER_ABSENT_UNKNOWN` (preserve projection until replay proves 0 behavior change; only 3 live rows) |
| S1 | **`DELAYED` order state UNDER-modeled** (a real *missing* state, not redundancy) | Polymarket `delayed` is real + not-cancellable; `normalize_venue_order_status` has no `DELAYED` (a raw `delayed` fails loud today). Model it: `VenueOrderStatus.DELAYED_PENDING_MATCH`, reduced separately from LIVE, non-cancellable |
| S2 | heartbeat-cancel modeled as health/gate, not fact | heartbeat loss → `ReconcileFinding.HEARTBEAT_CANCEL_WINDOW` + open-order poll, never a stored venue state |
| S2 | UMA liveness/dispute distinction | **keep distinct** (`DISPUTED`/`VOID_50_50`/`SOURCE_REVISION` are action-changing) — do not collapse to one unresolved flag |

### 7D. The single highest-leverage fix (improves all three goals at once)
**Make `state/projection.py` the sole `position_current` writer + introduce a `ReviewWorkItem` owner.** Define
`PositionProjectionReducer.reduce(position_id, facts) -> PositionCurrentProjection`; move the 6 non-owner writers
to append-only source facts/events; `ledger.py` = event-append orchestration only; add a `ReviewWorkItem`
table (OPEN/EXPIRED/RESOLVED + reason codes) that absorbs chain-only/size-mismatch/entry-authority/phantom/stale
debt; replay-diff zero-diff, then tighten INV-OWNER-1 `position_current` 7→1. This removes the root class behind
phantom-void repair, stale-terminal ignore-filters, chain-enum crashes, and quarantine drift (地基稳固), kills the
repair write-amplification + gives one memoization point (运行效率高), and replaces invented "position statuses"
with real external facts (符合真实运行逻辑).

**Next tactical cut (consult, lowest-risk first):** move ONE repair writer — the EDLI bridge or the
duplicate-consolidator — from a direct `position_current` UPDATE to appending a typed event the projector folds,
gated by a replay-diff (INV-PROJ-1). That locks the first step toward the single-writer boundary the rest of the
atlas depends on. **Prerequisite: build INV-PROJ-1 (replay-diff harness) first — it is the verifier every
writer-removal needs.** ✅ INV-PROJ-1 landed; corrected 2026-06-30 to event-SOURCING (the naive "latest event" cut false-positived on monitor/review noise the A5 reducer overrides) — live run now shows **0 drift**: position_current.phase is fully event-sourced, so the phase projection is consistent live (the §7D fix is architectural hygiene + efficiency, not an active-bug fix).

### 7D.1 Per-writer removal audit (2026-06-30, grounded in the code) — the migration roadmap
Each of the 7 `position_current` writers audited for removal path + difficulty (INV-PROJ-1 gates every cut):

| # | Writer | What it writes / liveness | Removal path | Difficulty |
|---|--------|---------------------------|--------------|------------|
| 1 | `state/projection.py` `upsert_position_current` | **the sole sanctioned materializer** (full-row upsert + F109/condition_id guards) | — TARGET (keep) | — |
| 2 | `state/ledger.py` | main write routes through the projector (`:622`); only bypass = `backfill_fill_authority` (`:546`), a CORRECT + tested but **unwired** F3 helper (called only by its test — no live caller, so **not a live drift source**). Its `legacy_unknown` output is **intentional, not stale** — the harvester (`harvester.py:733`) distinguishes it from NULL-`unmigrated` (`:724`) per F3's design (findings_2026_05_28 §F3). | LOW priority — unwired ⇒ no drift. A purely-lexical INV-OWNER-1 removal would route the single-column `fill_authority` patch through a projector-owned setter; not worth it now. | LOW |
| 3 | `state/position_duplicate_consolidator.py` | F109 duplicate-open-row merge (`:186,:370`) — a row-merge, not a materialization | emit a consolidation **event** the projector folds | MEDIUM |
| 4 | `events/edli_position_bridge.py` | 1493 lines — fill aggregation + duplicate-fill absorption + chain/size-authority preservation (2 UPDATE sites) | append the fill **fact/event**, projector materializes (the bridge docstring names this as its own long-term fix) | HIGH |
| 5 | `execution/command_recovery.py` | **co-tenant-hot**; broadest surface (54 w / 319 r across 5 axes) | split into fact-specific reducers; emit facts/review events | HIGH + co-tenant coord |
| 6 | `execution/exchange_reconcile.py` | **co-tenant-hot**; reconcile repairs | emit `ReconcileFinding`s, not projection patches | MEDIUM + co-tenant coord |
| 7 | `src/main.py` | **co-tenant-hot** | route through ledger/projector | MEDIUM + co-tenant coord |

**Order:** #3 (consolidation event) → #4 (bridge fill event) → #5–7 last (co-tenant coordination). #2 (ledger)
is unwired ⇒ deprioritized. No cut is byte-trivial — each is a real refactor the INV-PROJ-1 gate de-risks.

**558-NULL `fill_authority` — investigated 2026-06-30 and CLOSED (not a gap).** Correcting an earlier
over-statement: all 558 rows are **terminal** (531 voided / 18 settled / 6 admin_closed / 3 economically_closed).
A read-only dry-run of the backfill classifies them 542→`legacy_unknown` (harvester blocks, unchanged from
NULL) + 16→`venue_confirmed_full` (all voided/admin_closed, **zero settled**) — so running it changes **zero
training-eligibility** (no settled+filled position is unblocked; the 18 settled rows have no linked fills). The
projector already sets `fill_authority` on live positions (the 149 `venue_confirmed_full`), so there is **no
durable gap**. **Decision: no live-DB mutation, no projector change** — classifying dead terminal rows for
cosmetic completeness is not warranted (no-over-engineering). The F3 helper stays correct-but-unwired.
