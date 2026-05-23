# Phase 6 — EvidenceLadder + Promotion Gates + Shadow Experiment Registry

## v4 §M scope

Line 1100: "EvidenceLadder promotion"

## Dossier intent

§9 lays out 15 evidence layers; §13.5 names "EvidenceLadder with immutable shadow experiments" as one of the 5 highest-leverage architectural upgrades. The promotion rule (§9 closing paragraphs):
- Tier 0: idea only, no trade
- Tier 1: deterministic semantics pass
- Tier 2: replay pass
- Tier 3: shadow pass with no-trade logging
- Tier 4: paper cohort pass with quote feasibility
- Tier 5: tiny live pilot under hard cap
- Tier 6: limited live with strategy-specific Kelly haircut
- Tier 7: normal live eligible

No strategy uses normal Kelly before Tier 6.

## Why a typed evidence tier (not strings, not booleans)

Current `strategy_profile.live_status: live | shadow | blocked` is 3-state. Real strategy lifecycle has 8 states (or more), with non-trivial promotion gates between adjacent tiers. Coding the tier as a typed `IntEnum` with comparison + ordering operations gives:
- Atomic promotion rule (`if tier >= 6: allow_normal_kelly()`)
- Comparable invariants (`tier_observed >= tier_required_for_strategy`)
- Audit trail (which tier did this trade come from? — comparable across history)

Plus the dossier §9 small-N evidence design wants Bayesian confidence tiers per strategy; an integer tier is the natural input to that prior.

## Object model (target)

### `src/contracts/evidence_tier.py` (NEW)

```python
class EvidenceTier(IntEnum):
    IDEA = 0
    DETERMINISTIC_SEMANTICS = 1
    REPLAY_PASS = 2
    SHADOW_PASS = 3
    PAPER_COHORT = 4
    LIVE_PILOT_TINY = 5
    LIVE_LIMITED_HAIRCUT = 6
    LIVE_NORMAL = 7
```

### `src/state/shadow_experiment_registry.py` (NEW)

```python
@dataclass(frozen=True)
class ShadowExperiment:
    experiment_id: str                     # immutable UUID; hash of (strategy, config, start_date)
    strategy_id: str
    config_hash: str                       # ANY config change → new experiment
    started_at: datetime
    closed_at: Optional[datetime]
    cohort_tag: str                        # for grouping decision_events rows
    immutable: bool                        # True once started; mutation = new experiment

def register_shadow_experiment(strategy_id: str, config: dict) -> str: ...
def lookup_experiment(experiment_id: str) -> ShadowExperiment: ...
def close_experiment(experiment_id: str) -> None: ...
```

The "immutable" property is load-bearing: small-N inference is invalid if experiment definition can mutate mid-run. Per dossier §10 #20.

### `src/analysis/regret_decomposer.py` (NEW)

Decompose per-trade regret into 7 components per dossier §6.6:
- forecast/belief error
- observation/source error
- market quote error
- non-fill error
- fee/spread error
- timing/alpha decay error
- settlement ambiguity error

Inputs: decision_time forecast, decision_time book, actual fill, actual settlement, alternative (counterfactual) realization.

Output: per-component USD-attributed regret + cumulative across cohort.

### `src/analysis/evidence_report.py` (NEW)

Per-strategy report aggregating across cohorts. Reports: tier_observed, n_decisions, n_no_trades (Phase 2 T2 data), tier-promotion candidates, ablation results, negative-control comparisons.

Output: markdown + JSON; consumed by `LiveReadinessTribunal`.

### `LiveReadinessTribunal` (orchestration object)

Adjudicates promotion across tiers. Inputs: evidence report + operator-set per-strategy requirements. Output: PROMOTE / HOLD / DEMOTE verdict with rationale + tier-target.

## Bayesian small-N confidence tiers

Per dossier §9 layer 10. For a strategy with `n` shadow decisions and observed win-rate `ŵ`, posterior distribution under fixed prior (e.g., `Beta(2, 2)` for "weak prior centered on 0.5") gives credible interval on true win-rate. Tier promotion only fires when the lower bound of credible interval exceeds the strategy-specific threshold (e.g., breakeven + cost-of-capital).

This is what makes the promotion gate evidence-driven rather than discretion-driven. The dossier §13.1 #10 names "`SettlementCaptureVerifier`" as the high-leverage promotion-related instrument; that lives in Phase 7.

## Schema impact

- `shadow_experiments` table (world): immutable experiment registry.
- `regret_decompositions` table (forecasts or trades): per-trade regret records.
- `evidence_tier_assignments` table (world): current tier per strategy + transition history.
- 1-2 schema bumps.

## Strategy profile registry extension

`architecture/strategy_profile_registry.yaml` adds:
```yaml
- strategy_id: shoulder_sell
  live_status: shadow
  evidence_tier: SHADOW_PASS                # tier_observed
  evidence_tier_required_for_live: LIVE_LIMITED_HAIRCUT
  promotion_blockers:
    - "shadow_pass requires N=100 decisions; observed N=37"
    - "tail stress scenarios not yet replay-verified across regimes"
```

`StrategyProfile.is_runtime_live()` becomes:
```python
def is_runtime_live(self) -> bool:
    return (
        self.live_status == "live"
        and self.evidence_tier >= EvidenceTier.LIVE_PILOT_TINY
    )
```

## Verifier probes

1. `EvidenceTier` enum defined, 8-member IntEnum.
2. `ShadowExperiment` immutability enforced: attempt to mutate after `started_at` raises.
3. `register_shadow_experiment` returns same experiment_id for same (strategy, config_hash, started_at); different config → different ID.
4. `regret_decomposer` outputs 7 components summing to total realized regret (within floating-point tolerance).
5. `evidence_report` aggregates per-strategy from `decision_events` + `no_trade_events` + `regret_decompositions`.
6. `LiveReadinessTribunal` verdict on a synthetic Tier-3 strategy with `evidence_tier_required_for_live=LIVE_LIMITED_HAIRCUT` returns HOLD with reason "tier 3 < required 6".
7. `StrategyProfile.is_runtime_live` returns False for any strategy with `evidence_tier < LIVE_PILOT_TINY` regardless of `live_status` field.
8. Tags `phase6_track*_landed` + `phase6_landed`.

## What Phase 6 does NOT do

- Move any strategy to a higher tier automatically. Promotion is operator-gated; Phase 6 ships the MECHANISM only.
- Settlement type-gate (Phase 7).
- New strategy implementations (Phase 4 candidates).
