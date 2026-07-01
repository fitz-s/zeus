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
- **`venue_commands` / `venue_order_facts`**: **full-table scans** by the 3-min recovery sweep
  (`command_recovery.py`) and the 2-min exit monitor, for want of a `(state, updated_at)` /
  `(state, venue_order_id)` index.
- **`position_lots`**: all-lots-per-position read every monitor cycle; wants `(position_id, state)` index.
- **Write fan-out**: `position_current` written by **7 modules** (INV-OWNER-1 baseline) — the worst
  amplification; the single-reducer collapse (§12 migration step 7) removes it and the per-cycle write storm
  at once. **This is where 地基稳固 and 运行效率高 are the same fix.**

## 5. Layer 4 — the invariants that keep the atlas solid (the anti-rot antibodies)

| Invariant | Forbids | Enforcement | Status |
|-----------|---------|-------------|--------|
| **INV-CL-1** | branching on raw venue status strings outside ingress normalizers | lexical antibody, baseline shrinks | ✅ `tests/test_inv_cl1_no_raw_venue_status_branching.py` (baseline 9→3) |
| **INV-OWNER-1** | a second SQL writer for a stored mechanism | writer-set antibody, per-mechanism baseline | ✅ `tests/test_single_writer_per_state_mechanism.py` (locks 4, baselines 3; `position_current`=7 writers pinned) |
| **INV-PROJ-PURE** (candidate) | a projection column with an independent writer / a materialized projection that diffs from its reducer | replay-diff antibody (recompute A5/A6 from facts, assert zero-diff) | proposed — see consult round-3 |
| **INV-REDUCE-MONO** (candidate) | a truth reducer regressing a stronger proof to a weaker one | property test over `OrderProofClass` / settlement DAG transitions | proposed |
| **INV-INGRESS-TOTAL** (candidate) | a raw venue string that folds to neither a canonical member nor a loud failure | totality test over each `normalize_*` fold | proposed |

## 6. Physical placement (K1 three-DB split)

All six core vocab tables (`venue_commands`, `venue_order_facts`, `venue_trade_facts`, `position_lots`,
`position_current`, `settlement_commands`) + `settlement_outcomes` are **trade-class in `zeus_trades.db`**
— CHECK-constraint migrations are single-DB (one SAVEPOINT per table). Cross-DB writes touching
forecasts/world/trades together remain INV-37 (ATTACH + SAVEPOINT, never independent connections).

## 7. Three-goal weak-point audit (地基稳固 / 运行效率高 / 符合真实运行逻辑)

*Landing this session from consult round-3 — the concrete file:line violations per goal (multi-writer
fan-out, hot-path redundant I/O, venue-logic mismatches) + the single highest-leverage structural fix.*
