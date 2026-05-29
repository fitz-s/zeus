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

### Subsequent commits (operator authorized direct execution in the worktree)

6. **PR E — `src/state/db.py::init_backtest_schema`** — persists `replay_runs`,
   `replay_subjects`, `forecast_probability_vectors`, `settlement_resolution_truth`,
   `replay_skill_results` in `zeus_backtest.db`. VERIFIED live-restart-safe: the
   backtest DB is never fingerprinted (boot guard checks only WORLD+TRADE).
7. **PR C — lead/cycle/product-keyed ENS bias** (`lead_bucket.py`, `ens_bias_repo.py`,
   `fit_full_transport_error_models.py`, `score_error_model_candidates.py`): stops
   pooling lead≤48 (short-lead sign-flip); `error_model_key` + candidate buckets now
   carry lead_bucket+cycle; cross-bucket mixing fail-closed. No gate weakened;
   production fitter behavior-unchanged (passes `lead_max` explicitly).
8. **Finding 8 — reader inter-cycle spread** (`executable_forecast_reader.py`):
   A/B verdict = **ADDITIVE-ONLY** (multi-cycle election not provably superior under
   the env DB crash). Election byte-identical; 4 diagnostic fields added.
9. **PR G — `src/backtest/fill_simulator.py`** (new): pure orderbook fill engine
   (BUY=ask/SELL=bid, FOK/FAK/GTC/GTD, min/tick/fees/hash/resolved). economics.py
   stays tombstoned — no live consumer yet (market_events lacks the YES/NO token pair).
10. **PR H — promotion/learning gates** (`purpose.py`): SKILL/DIAGNOSTIC +
    promotion_authority=True is now UNCONSTRUCTABLE; `assert_promotion_grade` requires
    ForecastObject + promotion_eligible SettlementResolution; `trade_decisions` refused
    as an authority source.

**252 tests pass** across the full surface. All work is in this worktree; the live
daemon (separate checkout) is unaffected until this branch is deployed.

## 2. Already done by the merged PR #361 (plan was stale)

- **Finding 6 — evidence-ledger provenance**: `build_ens_residual_evidence.py` already
  derives `source_kind` via `source_kind_for_data_version` (not hardcoded 'prior').
- **Finding 7 — analytic p_raw CDF**: `analytic_p_raw_vector_from_maxes` replaces 10k MC.
- **PR F — D1 LOW window fix**: mx2t3/mn2t3 are already 3h-native. The "LOW blocking"
  decision is therefore MOOT.

## 3. Genuinely still OUT (true remaining tail)

- **End-to-end replay backfill** (plan §9 Phases 2–4): the PR E tables, the contracts,
  and the scorer all exist, but the pipeline that BUILDS ForecastObject views →
  SettlementResolution rows → group results → writes them into `replay_skill_results`
  is not wired. (Scoring is unit-correct; persistence is empty until a backfill runs.)
- **ECONOMICS run path**: `fill_simulator` exists but `economics.py` stays tombstoned —
  blocked by the `market_events` YES/NO token-identity gap, not by this code.
- **`run_replay.py` CLI** purpose-split report wiring.

## 4. Genuine operator decisions still open

- **Deploy timing**: this branch's PR C changes SERVED calibration once deployed +
  re-fit. Decide when to deploy/re-fit given the live shadow seam-hunt.
- Finish the `data_version → dataset_id` rename on `platt_models` (vs the freeze taken
  here), once a calibration-touching slice is scheduled.

## 5. Known environment issue (pre-existing, not from this work)

`init_schema_forecasts` crashes on a fresh in-memory DB (`no such column:
temperature_metric` in `_create_market_events`) because the ATTACH path copies an old
DDL when a live forecasts DB exists on the machine. Baselined identical on clean
`main@bedff16832`. Tests in this PR use `--noconftest` / direct unit fixtures to avoid
it. Fixing it is a `--write-pin`-gated DDL task, out of scope here.
