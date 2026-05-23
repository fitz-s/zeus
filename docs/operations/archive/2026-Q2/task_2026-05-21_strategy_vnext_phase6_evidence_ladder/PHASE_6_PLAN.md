# Phase 6 Plan — EvidenceLadder + Promotion Gates + ShadowExperimentRegistry

Created: 2026-05-21  Last revised: 2026-05-21 (v2 — 9 critic fixes)
Authority: 07_PHASE_6_EVIDENCE_LADDER.md + maturity_model.yaml (no overlap)

---

## Context

**Schema bumps are dispatch-time-dynamic.** Phases 3 and 5 land before Phase 6; executor
MUST grep `src/state/db.py` ~line 852 for current `SCHEMA_VERSION = N`, then write N→N+1.
CI antibody `scripts/check_schema_version.py` blocks PRs with hardcoded bump values.

`StrategyProfile.is_runtime_live()` currently returns `live_status == "live"` (line 120).

---

## T1 — EvidenceTier IntEnum + StrategyProfile Extension

- NEW `src/contracts/evidence_tier.py`: `EvidenceTier(IntEnum)` 8 members IDEA=0…LIVE_NORMAL=7.
- MODIFY `src/strategy/strategy_profile.py`: add `evidence_tier` field; extend `is_runtime_live()`
  to require `evidence_tier >= LIVE_PILOT_TINY` AND `live_status == "live"`.
- MODIFY `architecture/strategy_profile_registry.yaml` — initial tier assignments:
  - `settlement_capture` → LIVE_NORMAL (kelly=1.0)
  - `center_buy` → LIVE_NORMAL (kelly=1.0)
  - `imminent_open_capture` → LIVE_LIMITED_HAIRCUT (kelly=0.5, newly live)
  - `opening_inertia` → LIVE_LIMITED_HAIRCUT (kelly=0.5)
  - `shoulder_sell` → SHADOW_PASS (live_status=shadow)
  - `shoulder_buy`, `center_sell` → IDEA (blocked)
  - Do NOT downgrade LIVE_NORMAL without operator approval.
  - Add `evidence_tier_required_for_live` + `promotion_blockers` to each entry.
- Loader: unknown `evidence_tier` string → ValueError (fail-closed).

**Tier→Kelly mapping** (resolved at strategy_profile level, NOT inside Tribunal):
- Tier 5 LIVE_PILOT_TINY: hard position cap via tribunal-issued `tier_target`.
- Tier 6 LIVE_LIMITED_HAIRCUT: `kelly_default_multiplier` from strategy_profile registry.
- Tier 7 LIVE_NORMAL: `kelly_haircut = 1.0`.

**Test invariants**: ordering preserved; `is_runtime_live` False for SHADOW_PASS+live_status=live;
True for LIVE_PILOT_TINY+live; ValueError on unknown tier string.

---

## T2 — ShadowExperimentRegistry + evidence_tier_assignments (own PR)

- NEW `src/state/shadow_experiment_registry.py`: frozen `ShadowExperiment` dataclass;
  `register_shadow_experiment` / `lookup_experiment` / `close_experiment`.
  `immutable: bool` is **audit-trail metadata only** (enforcement = frozen dataclass + close_experiment).
  Mutation of started experiment → ValueError.
- MODIFY `src/state/db.py`: +1 schema bump N→N+1 (grep first); add to world DB:
  ```sql
  CREATE TABLE shadow_experiments (
      experiment_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
      config_hash TEXT NOT NULL, started_at TEXT NOT NULL,
      closed_at TEXT, cohort_tag TEXT NOT NULL, immutable INTEGER NOT NULL DEFAULT 1);
  CREATE TABLE evidence_tier_assignments (
      strategy_id TEXT NOT NULL, tier INTEGER NOT NULL, assigned_at TEXT NOT NULL,
      rationale TEXT, operator_ref TEXT, verdict_reason TEXT);
  CREATE INDEX idx_eta_strategy_assigned ON evidence_tier_assignments (strategy_id, assigned_at DESC);
  ```
  "Latest tier" = MAX(assigned_at) row; index gives O(log n) lookup.
- **Rollback**: additive tables only; daemon falls back to live_status-only gate (T1 guard).

**Test invariants**: idempotent register; different config → different ID; mutation raises;
roundtrip; index present via `PRAGMA index_list`.

---

## T3 — RegretDecomposer 7-Component + regret_decompositions (own PR)

- NEW `src/analysis/regret_decomposer.py`: `RegretComponents` (7 fields + total);
  `decompose_regret(decision_event, fill, settlement, counterfactual) -> RegretComponents`.
  7th component: **`settlement_ambiguity_error`** = regret from settlement source/oracle
  ambiguity at DECISION TIME (not general settlement deviation). Column: `settlement_ambiguity_error_usd`.
  Sum of 7 components = realized_pnl − counterfactual_pnl within 1e-9.
- MODIFY `src/state/db.py`: +1 bump N→N+1 (after T2 bump); add `regret_decompositions` world DB:
  ```sql
  CREATE TABLE regret_decompositions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      experiment_id TEXT NOT NULL REFERENCES shadow_experiments(experiment_id),
      decision_event_id TEXT NOT NULL,
      forecast_error_usd REAL, observation_error_usd REAL, quote_error_usd REAL,
      non_fill_error_usd REAL, fee_error_usd REAL, timing_error_usd REAL,
      settlement_ambiguity_error_usd REAL, total_regret_usd REAL NOT NULL,
      computed_at TEXT NOT NULL);
  ```
- **Rollback**: additive; T1–T2 intact if T3 fails; regret data absent from T4 aggregates.

**Test invariants**: 7-sum within 1e-9; zero-alpha → near-zero; non-fill → non_fill_error_usd≠0;
column named `settlement_ambiguity_error_usd`.

---

## T4 — EvidenceReport + LiveReadinessTribunal

- NEW `src/analysis/evidence_report.py`: per-strategy aggregation from decision_events +
  no_trade_events + regret_decompositions + shadow_experiments; Bayesian Beta(2,2) credible interval.
- NEW `src/analysis/live_readiness_tribunal.py`: inputs = EvidenceReport list + per-strategy
  requirements; outputs `TribunalVerdict` (PROMOTE / HOLD / DEMOTE + rationale + tier_target).
  - Promotion: credible interval lower bound > breakeven + cost_of_capital.
  - **DEMOTE**: Tribunal writes new row to `evidence_tier_assignments` with `verdict_reason`
    populated — DB write, NOT advisory. PROMOTE likewise writes to table.
  - Tribunal emits `tier_target` only; Kelly multiplier resolved downstream by caller.
- **Rollback**: analysis-only except DB write on verdict; T1–T3 intact if T4 fails; manual
  operator writes to evidence_tier_assignments as fallback.

**Test invariants**: HOLD for Tier-3 vs required LIVE_LIMITED_HAIRCUT; PROMOTE only when
CI lower bound > threshold; DEMOTE writes row with verdict_reason; JSON schema-stable;
is_runtime_live False for tier < LIVE_PILOT_TINY.

---

## Schema Bump Summary (dynamic — grep db.py at dispatch)

| PR  | Delta   | Tables                                      | DB    |
|-----|---------|---------------------------------------------|-------|
| T2  | N → N+1 | shadow_experiments, evidence_tier_assignments | world |
| T3  | N+1→N+2 | regret_decompositions                       | world |

T2+T3 in same PR (operator decision only): single bump N→N+1.

---

## Tag Sequence

`phase6_track1_landed` → `phase6_track2_landed` → `phase6_track3_landed` →
`phase6_track4_landed` → `phase6_landed`

---

## What Phase 6 Does NOT Do

- Auto-promote any strategy (mechanism only; operator-gated).
- Downgrade any LIVE_NORMAL strategy without operator approval.
- Implement SettlementCaptureVerifier (Phase 7).
