# Phase 2.75 Design: Robust Lower-Bound Kelly Sizing

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Author:** Claude Opus 4.7
**Authority basis:** may4math.md Finding 5 (`CRITICAL_QUANT_RISK`, `ROBUST_KELLY_NEEDED_NOW`); operator directive 2026-05-04 — comprehensive review must include sizing.

---

## Why this is in the unlock sequence

may4math.md Finding 5 names Kelly as the **amplifier** of any residual probability error. Even with Phases 1+2+2.5 in place, residual uncertainty exists in:

- Calibration parameter uncertainty (Platt A, B, C are point estimates from finite samples)
- Cycle-stratified bucket maturity (12z buckets fresh from 90-day backfill have wide CI)
- Source-transfer residual (validated_transfers OOS metrics have noise)
- Oracle posterior uncertainty (Beta-binomial posterior, may4math.md Finding 3)
- Execution price uncertainty (slippage, partial fills, depth)

Current Kelly uses point posterior `p` and bootstrap CI for `edge`, but applies the **point estimate** to size:
```
f* = (p_posterior - cost_eff) / (1 - cost_eff)
size = f* × kelly_mult × strategy_mult × city_mult × bankroll
```

When `p_posterior` is biased (e.g., residual cycle drift), the size scales the bias.

## Decision

Replace point-Kelly with lower-bound robust Kelly:

```
p_L = lower_5pct_quantile(
   Platt parameter uncertainty
 + decision-group bootstrap
 + source-cycle transfer uncertainty
 + oracle posterior uncertainty
 + execution price uncertainty
)

f_robust = max(0, (p_L - cost_eff) / (1 - cost_eff))

size = bankroll × λ × m_strategy × m_oracle × m_cycle_domain × m_liquidity × f_robust
```

Where:
- `λ` = base fractional-Kelly multiplier (current 0.25)
- `m_*` = uncertainty-aware multipliers, NOT hardcoded strategy or city policies
- `m_cycle_domain` = 0 if `evaluate_calibration_transfer` returned BLOCK, < 1 if SHADOW or recent-validation, 1 if mature
- `m_oracle` = derived from oracle Beta-binomial posterior (may4math.md Finding 3)

## Concrete formula

### Step 1: assemble component uncertainties

```python
@dataclass
class SizingUncertaintyInputs:
    p_point: float                                    # current posterior point
    platt_param_ci: tuple[float, float]               # 90% CI from bootstrap
    decision_group_ci: tuple[float, float]            # within-DG bootstrap
    transfer_ci: tuple[float, float]                  # from validated_transfers metrics
    oracle_posterior_upper: float                     # Beta-binomial posterior 95th
    cost_point: float                                 # mid quote
    cost_eff_upper: float                             # quote + max slippage + tick + fee
```

### Step 2: combine via Bonferroni-corrected lower bound

```python
def compute_p_lower(inputs: SizingUncertaintyInputs) -> float:
    components = [
        inputs.platt_param_ci[0],
        inputs.decision_group_ci[0],
        inputs.transfer_ci[0],
        inputs.p_point - 2 * inputs.oracle_posterior_upper,  # oracle penalty
    ]
    return min(components)  # most conservative
```

### Step 3: robust Kelly

```python
def robust_kelly_size(inputs: SizingUncertaintyInputs, base_lambda: float = 0.25) -> float:
    p_L = compute_p_lower(inputs)
    cost_eff = inputs.cost_eff_upper
    if p_L <= cost_eff:
        return 0.0
    f_robust = (p_L - cost_eff) / (1.0 - cost_eff)
    return base_lambda * f_robust  # subsequent multipliers applied by caller
```

### Step 4: domain-mismatch hard gate

```python
def domain_mismatch_multiplier(transfer_decision: CalibrationTransferDecision) -> float:
    if transfer_decision.status == "BLOCK":
        return 0.0
    if transfer_decision.status == "SHADOW_ONLY":
        return 0.0  # no live sizing on un-validated transfer
    if transfer_decision.status == "LIVE_ELIGIBLE":
        # downweight by recency of validation: <30d full weight, 30-90d 0.5, >90d 0.25
        days_since_validation = (now_utc - transfer_decision.validated_at).days
        if days_since_validation <= 30:
            return 1.0
        if days_since_validation <= 90:
            return 0.5
        return 0.25
    return 0.0
```

## Decision evidence

Every sized order writes the full sizing trace:

```python
@dataclass(frozen=True)
class SizingEvidence:
    p_point: float
    p_lower_5pct: float
    cost_point: float
    cost_eff_upper: float
    f_point_kelly: float       # what the OLD point-Kelly would have sized
    f_robust_kelly: float      # what robust returned
    base_lambda: float
    m_strategy: float
    m_oracle: float
    m_cycle_domain: float
    m_liquidity: float
    final_size_units: float
    components_uncertainty: dict[str, float]  # platt_param_ci_low, transfer_ci_low, oracle_post_95, etc.
    sizing_policy_id: str      # 'robust_kelly_v1_2026_05_04'
```

This evidence is stored alongside `opportunity_fact` so post-trade analysis can replay alternative sizing policies.

## Tests

```python
# tests/test_robust_kelly_sizing.py

def test_robust_kelly_returns_zero_when_p_lower_le_cost():
    """If lower bound of p ≤ cost, size = 0."""

def test_robust_kelly_le_point_kelly():
    """For any inputs, robust size ≤ point size (it's strictly more conservative)."""

def test_domain_block_zeros_size():
    """When transfer_decision.status == BLOCK, size = 0 regardless of other inputs."""

def test_domain_shadow_only_zeros_size():
    """SHADOW_ONLY also produces zero live size."""

def test_oracle_uncertainty_reduces_p_lower():
    """Higher oracle posterior_upper → lower p_lower → smaller size."""

def test_sizing_evidence_stored():
    """Every sized order writes a SizingEvidence row joinable to opportunity_fact."""
```

## Sequencing

This Phase 2.75 lands AFTER Phase 2.5 (transfer policy must exist for `m_cycle_domain` to be computed) and BEFORE Phase 3 (live routing fix — robust Kelly must be in place before 12z forecasts can be sized live).

```
Phase 1 → Phase 2 → Phase 2.5 (transfer) → Phase 2.75 (robust Kelly) → Phase 3 (routing) → unlock
```

## Out of scope (deferred per may4math.md tribunal)

- Bayesian Kelly with full posterior integration (`SHADOW_ONLY` per may4math)
- Drawdown-constrained Kelly (separate post-unlock improvement)
- P&L-tuned Kelly multipliers (`HARMFUL_OR_USELESS` per may4math)
- Strategy-specific Kelly by realized P&L (`HARMFUL_OR_USELESS`)

## Risks

- **Conservative lock-in**: lower-bound Kelly will materially shrink position sizes. If the math is correct (i.e., probability calibration has real residual error), this is the **right** behavior. If too conservative, we under-trade.
- **Calibration tax**: validated_transfers without recent OOS evidence get `m_cycle_domain` = 0.5 or 0.25. Operators must keep validation fresh.
- **Multiplier collision**: existing strategy/city multipliers are hardcoded policies. Document explicitly that they STACK on robust Kelly's uncertainty-aware shrinkage. The two are not redundant — robust Kelly addresses *probability error*, strategy/city multipliers address *strategy attribution*.

## Acceptance for unlock

- [ ] `robust_kelly_size()` implemented and tested
- [ ] `SizingEvidence` row written for every order in opportunity_fact
- [ ] Smoke test: with p_point=0.56, cost_eff_upper=0.50, transfer SHADOW_ONLY → size = 0
- [ ] Smoke test: with all uncertainties tight + LIVE_ELIGIBLE → robust size ≤ 0.95 × old point size (i.e., conservative shrinkage observed)
- [ ] Operator approval after reviewing 1-week shadow run (SizingEvidence comparing point vs robust)
