# Replay / backtest redesign — audit verdict + shipped scope

- Created: 2026-05-29
- Authority basis: TRIBUNAL replay/backtest redesign plan (operator) + live-code audit
  of `main@bedff16832`. This doc is the provenance record: it states what the plan
  assumed, what the code already had, and what this PR actually changed — so the
  next session skips re-auditing.

## 0. Audit verdict — the plan was written against an older tree

The redesign plan called for "new" files that **already exist on mainline**. Per
the Code Provenance rule (trust code over docs), the plan's PR slicing was
re-scoped against ground truth:

| Plan assumed "new"            | Reality at HEAD                                                              | Verdict          |
| ----------------------------- | --------------------------------------------------------------------------- | ---------------- |
| `ForecastObject`              | `src/contracts/forecast_object.py` exists (TRIBUNAL P1, 2026-05-29), tested | CURRENT_REUSABLE |
| settlement_value→winner deriv | `CanonicalBinGrid.bin_for_value()` already does this                        | CURRENT_REUSABLE |
| `scoring.py`                  | exists with `log_score` / `brier_score` (index-based)                       | EXTEND           |
| `SettlementObject`            | name TAKEN by `residual_key.py` (residual pairing)                          | RENAME → new     |
| `SettlementOutcome` states    | `settlement_outcome.py` 10-state enum + classifier exists                   | CURRENT_REUSABLE |

### §13 schema-fact resolutions (gated the safe sequence)

1. **Platt model table = `platt_models`** (not in plan vocab). It still uses
   `data_version`; forecast tables (`ensemble_snapshots`, `calibration_pairs`) use
   `dataset_id`. **Decision: FREEZE the vocab split.** Do NOT rename `platt_models`
   columns — that touches live calibration and is out of scope for a replay
   redesign. New code uses `dataset_id` for forecast tables and maps explicitly at
   any `platt_models` join.
2. `venue_order_facts` exists as-named (append-only) → ECONOMICS order-truth ready.
3. `executable_market_snapshots` stores depth as one `orderbook_depth_json` blob
   (not split bids/asks) → a future fill simulator must parse that blob.
4. **`market_events` lacks a YES/NO token pair + bin identity** (single `token_id`,
   no `winning_asset_id`). Categorical outcome-set token identity cannot come from
   `market_events` alone — only bites ECONOMICS (tombstoned). SKILL scoring takes
   bins from `ensemble_snapshots` ordered labels + winner from `settlement_outcomes`.
5. **`bin_grid_id` / `bin_schema_id` are schema-present but writer-UNPOPULATED**
   (NULL on all live evaluator rows). Any contract MUST treat them as
   passthrough/diagnostic, never a hard promotion filter, or zero live rows qualify.
6. `trade_decisions` is `schema_class: legacy_archived`; `log_trade_entry()` is a
   no-op stub. Canonical entry truth is `position_events` / `position_current`.

## 1. What this PR ships (the §1-verdict triad, completed)

The plan's core verdict: *make ForecastObject + SettlementObject structurally
unambiguous, then score SKILL replay as categorical outcome-set scoring.*
`ForecastObject` already existed, so this PR completes the triad:

1. **`src/calibration/scoring.py`** — categorical group-scoring layer added atop
   the existing proper rules (which are untouched, callers preserved):
   `validate_probability_group`, `p_winner`, `categorical_log_loss` (clamped),
   `multiclass_brier`, `ranked_probability_score` (ordered RPS), `winner_rank`,
   `reciprocal_rank`, `top_k_hit`.
2. **`src/contracts/settlement_resolution.py`** — `SettlementResolution` (renamed
   from the plan's "SettlementObject" to avoid the `residual_key` collision):
   derives the winning bin from `settlement_value` (TRUTH) via `bin_for_value`;
   stored `winning_bin` is EVIDENCE only; exceptional outcomes (50/50, disputed,
   unresolved, venue-unresolved) → `promotion_eligible = False`.
3. **`src/backtest/skill.py::score_forecast_vector`** — pure group-level SKILL
   result: ONE categorical result per (vector × settlement × grid), emits proper
   metrics + `group_integrity_status` only, NO PnL. `promotion_authority` hard
   False (gating not yet wired). `run_skill` wrap unchanged.
4. **`src/engine/replay_selection_coverage.py`** — Finding-2 fix: score against the
   `settlement_value`-derived winner, not the stored `winning_bin` string.
5. **`src/backtest/economics.py`** — readiness modernized: `trade_decisions`
   removed (legacy_archived), `venue_order_facts` / `position_events` /
   `position_current` added. Tombstone preserved.

Tests: `test_multiclass_skill_scoring.py`, `test_settlement_resolution_contract.py`,
`test_selection_coverage_settlement_truth.py` (+ economics fixture updated).

## 2. OUT OF SCOPE (the 30h tail — deferred, NOT in this PR)

- **replay_* schema persistence** (`replay_runs`/`replay_subjects`/
  `forecast_probability_vectors`/`replay_skill_results`): scoring math needs no
  persistence layer to be correct; add when a run-store is actually consumed.
- **ECONOMICS kernel + fill simulator** (parse `orderbook_depth_json`, bid/ask/
  depth/FOK/FAK/fees/tick parity): stays tombstoned until executable parity exists.
- **Promotion / learning gates** (TRIBUNAL PR H): `promotion_authority` is hard
  False everywhere here; wiring the gate is a separate, well-tested slice.
- **Evidence-ledger provenance lineage** (PR B) and **model_bias_ens lead/cycle/
  product re-keying** (PR C): PR C touches LIVE calibration — HIGH risk while the
  live daemon runs in shadow; must not be done casually.
- **D1 LOW source/window fix** (PR F): needs an operator verdict on whether LOW
  promotion-grade replay is blocked on it, plus a data rebuild.

## 3. Genuine operator decisions still open

- Finish the `data_version → dataset_id` rename on `platt_models` (vs the freeze
  taken here), once a calibration-touching slice is scheduled.
- LOW D1 materiality: is LOW promotion-grade replay blocked on the mx2t3/mn2t3
  window fix? (PR F gating.)
