# Market-Cost Seam + Executable-Uncertainty Architecture Upgrade

**Created:** 2026-05-27
**Last reused or audited:** 2026-05-27
**Authority basis:** operator math/stat upgrade directive 2026-05-27 + 11-claim grep verification against origin/main HEAD cb4541da70 + Fitz §1 (structural decisions > patches) + Fitz §3 (immune system > security guard) + INV-12 (typed-probability seams) + INV-21 (Kelly typed price) + INV-eps-spec-conformance (operator-pinned deviations).

## TL;DR — what is wrong, what changes

Zeus has 22 chain-safety mechanisms. They are 5 structural decisions, incompletely executed:

| K | Structural decision | Current state | Gap |
|---|---|---|---|
| K1 | Market price ≠ epistemic belief | ✅ `MODEL_ONLY_POSTERIOR_MODE` default | None — preserve |
| K2 | Market price = typed executable cost | ⚠️ `BinEdge.entry_price: float` + fabricated `price_type="implied_probability"` at coercion seam | **D5 seam fix + D6 depth walk** |
| K3 | Edge bootstrap covers all uncertainty sources | ⚠️ Only forecast/Platt sampled; market `c_b` fixed; `σ_market` absent | **Bootstrap c_b sampling** |
| K4 | Family bins are partition, not independent assets | ⚠️ Stage A active (single-leg gate); Stage B optimizer present but pinned `max_legs=1` | **Bump max_legs config** |
| K5 | Uncertainty enters Kelly once via LCB, not via N multipliers | ❌ 12+ multiplicative haircuts across `dynamic_kelly_mult` (7) + `phase_aware_kelly_multiplier` (4) + `EffectiveKellyContext.haircut` (1) | **Hard veto / soft σ split** |

K1 already correct → preserve.
K2/K3/K4 partial → activate.
K5 incomplete → unify.

## Locked code-truth deltas (post-grep verification)

### D1 — Multiplicative chain is 12+, not the 10 plan assumed

`dynamic_kelly_mult` (`kelly.py:372`) applies in order:
1. base
2. × 0.7 if `ci_width > 0.10`
3. × 0.5 if `ci_width > 0.15` (cumulative with #2 → 0.35)
4. × 0.6 if `lead_days >= 5` (or 0.8 if >= 3)
5. × {0.5, 0.7} if `rolling_win_rate_20 < {0.40, 0.45}`
6. × `max(0.1, 1-heat)` if `portfolio_heat > 0.40`
7. × `max(0.0, 1 - drawdown_pct/max_drawdown)`
8. × `strategy_kelly_multiplier(strategy_key)`
9. × `city_kelly_multiplier(city)` (Denver/Paris 0.7)

`phase_aware_kelly_multiplier` (`kelly.py:198`) applies:
10. × `kelly_for_phase(market_phase)`
11. × `oracle_penalty.penalty_multiplier`
12. × `_observed_fraction_multiplier` (settlement_capture only; floor 0.3)
13. × `FALLBACK_F1_HAIRCUT` if `phase_source == "fallback_f1"` (0.7)
14. Shoulder clamp [0.05, 0.20] for shadow shoulder strategies

`EffectiveKellyContext.haircut` (`contracts/effective_kelly_context.py:98`) applies:
15. × FOK/FAK haircut from {(TIGHT, MID, WIDE) × (DEEP, SHALLOW)} table

**Total: 15 multiplicative gates.** `0.8^10 ≈ 0.107` understated. Real cascade can collapse a valid edge to near-zero with no diagnostic.

### D2 — Primary entry path is VWMP, not best-ask

`_buy_entry_price_from_clob` → `_buy_entry_price_from_orderbook` (`evaluator.py:300, :265`) returns:

```python
"price": float(vwmp(bid, ask, bid_size, ask_size))   # primary
"price": float(ask)                                   # ask-only fallback when bid missing
```

VWMP at top-of-book ≈ depth-weighted top-level price, NOT all-in fill cost for orders larger than `ask_size`.

### D3 — VWMP from TOP-OF-BOOK ONLY

`_buy_entry_price_from_orderbook` uses `_top_book_level_decimal(orderbook, "asks")` — single level. Does NOT walk depth. For orders > `ask_size`, system has no slippage knowledge. Polymarket `calculateMarketPrice` exists for depth-walk; Zeus does not call it.

### D4 — Stage B optimizer is REAL, pinned by config

`optimize_exclusive_outcome_portfolio` (`family_exclusive_dedup.py:909`) is a working combinatorial optimizer over `expected_log_growth` of payoff matrix. Default `max_legs=1` makes it behaviorally identical to Stage A single-leg gate. **Activation = config bump, not implementation.**

### D5 — Coercion seam fabricates type provenance

`evaluator.py:1550-1557` (`_size_at_execution_price_boundary`):

```python
raw_entry_price = float(entry_price)                   # bare float from BinEdge
ep = ExecutionPrice(
    value=raw_entry_price,
    price_type="implied_probability",   # ← FABRICATED at boundary, ignores upstream VWMP provenance
    fee_deducted=False,
    currency="probability_units",
)
ep_fee_adjusted = ep.with_taker_fee(fee_rate)          # → price_type="fee_adjusted"
ep_fee_adjusted.assert_kelly_safe()                    # PASSES because fee_adjusted now
```

`ExecutionPrice` docstring (`contracts/execution_price.py:2-13`) explicitly identifies this as the D3 defect it was created to fix. The fix is incomplete — the seam itself launders implied_probability into fee_adjusted, defeating the contract.

### D6 — Bootstrap subtracts FIXED `p_market` per iteration

`_bootstrap_bin` (`market_analysis.py:559+`) samples forecast members + Platt parameter set + transfer_logit_sigma per bootstrap iteration. `p_market[i]` is captured at scan time and subtracted unchanged across all bootstrap iterations. `σ_market` does not enter `edge_ci_lower`.

## Refined wave plan

Reordered for dependency + risk. Each wave = single coherent PR, ≥ 300 self-authored LOC, single-purpose.

### Wave 0 — This plan + spec amendment (one PR)

**Deliverable:**
- `architecture/market_cost_seam_executable_uncertainty_2026_05_27.md` (this file)
- `docs/reference/zeus_math_spec.md` §15.7 new section
- `architecture/invariants.yaml` 3 new entries (INV-38, INV-39, INV-40)

**Acceptance:** Plan reviewed; spec amendment merged; relationship test invariants registered.

### Wave 1 — Forensic audit + RED relationship tests (one PR)

**Deliverable:**
- `scripts/audit_market_price_semantics.py` — read-only audit traces live cycle through edge_yes → BinEdge → coercion seam → kelly. Outputs per-bin table: `bin | direction | p_posterior | p_market | bin_edge.entry_price | ep.price_type_at_seam | ep.value | kelly_size_input`. ~150 LOC.
- `tests/test_R1_edge_kelly_entry_price_identity.py` — assert BinEdge.entry_price.value === kelly call entry_price.value (passes today by accident; codifies invariant).
- `tests/test_R2_bin_edge_executable_provenance.py` — assert BinEdge.entry_price.price_type ∈ {"vwmp","ask","fee_adjusted"}, NEVER "implied_probability". **RED today.**
- `tests/test_R3_effective_kelly_haircut_not_in_dynamic_mult.py` — assert spread/depth contribution appears ONCE in size chain. **RED today.**
- `tests/test_R4_family_optimizer_dominates_stage_a_when_max_legs_gt_1.py` — assert ELG(2-leg) ≥ ELG(1-leg) on a constructed YES/NO partition. Smoke for D4.
- `tests/test_R5_bootstrap_c_b_uncertainty_widens_ci.py` — assert `σ_market > 0 → ci_lower < legacy_ci_lower`. **RED today (no c_b sampling).**
- `tests/test_R6_model_only_posterior_blocks_market_blending.py` — assert MODEL_ONLY mode raises on `market_prior` arg. Preservation test.

All RED tests marked `@pytest.mark.xfail(reason="waves N", strict=True)` until their phase lands. Antibody convention: removing xfail = phase complete.

**Acceptance:** Audit script runnable; all R-tests collect + xfail per phase tag.

### Wave 2 — D5 coercion seam fix (one PR) — HIGHEST LEVERAGE

**Scope:** Make `BinEdge.entry_price` carry true upstream provenance. Kill the fabrication at `evaluator.py:1551`.

**Changes:**
1. `BinEdge.entry_price: ExecutionPrice` (was `float`). One field type bump in `types/market.py`.
2. `MarketAnalysis.__init__` accepts `entry_cost_yes: list[ExecutionPrice] | None` alongside legacy `p_market: np.ndarray`. When provided, `find_edges` uses `entry_cost_yes[i].value` for `edge_yes` and stamps `entry_price=entry_cost_yes[i]` on BinEdge.
3. Legacy `p_market`-only path: synthesize `ExecutionPrice(value=p_market[i], price_type="implied_probability", fee_deducted=False, currency="probability_units")` and mark with new `entry_price_provenance="legacy_implied_probability"` field on BinEdge. Live path MUST receive `entry_cost_yes`; shadow/test path tolerates legacy.
4. `evaluator.py:_size_at_execution_price_boundary` removes the fabrication block. Passes `edge.entry_price` (already typed) directly to `with_taker_fee` if not already fee_adjusted, else direct to kelly_size.
5. `_buy_entry_price_from_clob` callers in evaluator construct `ExecutionPrice(value=vwmp_or_ask, price_type="vwmp"|"ask", fee_deducted=False, currency="probability_units")` and pass to MarketAnalysis.

**Tests:** R1 + R2 flip GREEN. New unit tests:
- `test_evaluator_passes_typed_entry_price_to_market_analysis`
- `test_bin_edge_entry_price_carries_vwmp_provenance`
- `test_kelly_boundary_no_longer_fabricates_implied_probability`
- `test_legacy_p_market_path_marks_provenance_legacy_implied_probability`

**Risk:** Touches every site that constructs BinEdge or reads `bin_edge.entry_price` as a float. Grep-audit before commit.

**Acceptance:** R1 + R2 GREEN; full test suite green; live replay byte-identical on existing live decisions (same VWMP value flows through, only type carries).

### Wave 3 — Depth walk + EntryQuoteEvidence (one PR)

**Scope:** Replace top-of-book VWMP with depth-walked fill estimate for proposed size. Encode quote freshness + depth as evidence.

**Changes:**
1. `_buy_entry_price_from_orderbook` reads full asks ladder, computes `fill_price_walk(asks, target_size)` returning average fill price across consumed levels + `slippage_bps`. Polymarket `calculateMarketPrice` reference implementation. ~80 LOC pure function.
2. New `src/contracts/entry_quote_evidence.py`:

```python
@dataclass(frozen=True)
class EntryQuoteEvidence:
    token_id: str
    side: Literal["yes", "no"]
    best_bid: float | None
    best_ask: float
    spread_usd: float
    top_of_book_size: float
    depth_at_target_size: float
    fill_price_walk: float           # depth-walked average fill at target size
    slippage_bps: float              # (fill_price_walk - best_ask) / best_ask × 10000
    quote_age_ms: int
    book_hash: str
    fee_rate: float                  # from market.fee_rate_bps or default
    fee_per_share: float             # polymarket_fee(fill_price_walk, fee_rate)
    all_in_entry_price: float        # fill_price_walk + fee_per_share
    cost_uncertainty: float          # σ_market for bootstrap (Wave 5)
    reliability_status: Literal["LIVE_OK","STALE","THIN_BOOK","ASK_ONLY","CROSSED"]

    def to_execution_price(self) -> ExecutionPrice:
        return ExecutionPrice(
            value=self.all_in_entry_price,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )
```

3. Evaluator constructs `EntryQuoteEvidence` once per market×side, passes to MarketAnalysis as `entry_cost_yes`/`entry_cost_no`. BinEdge gains `entry_quote_evidence: EntryQuoteEvidence | None`.
4. `cost_uncertainty` formula (Wave 3 conservative): `max(spread_usd/2, slippage_bps/10000)`. Refined in Wave 5.

**Tests:**
- `test_depth_walk_consumes_multiple_levels_when_size_exceeds_top`
- `test_thin_book_marks_reliability_thin_book`
- `test_stale_quote_marks_reliability_stale`
- `test_entry_quote_evidence_to_execution_price_is_kelly_safe`

**Risk:** Live evaluator construction changes shape. Requires per-market `target_size` estimate at scan time — bootstrap with `min_order_usd / current_p_market` as proxy.

**Acceptance:** Depth walk active; EntryQuoteEvidence carried through scan→Kelly; live VWMP-only path deprecated with WARNING.

### Wave 4 — Stage B family optimizer activation (one PR)

**Scope:** Bump `max_legs` to allow YES+NO simultaneous within one family when payoff matrix justifies. Stage A remains as fallback.

**Changes:**
1. `evaluator.py` calls `preselect_single_family_edge_before_kelly` with `max_legs` from config (default still 1 for safety).
2. New config flag `ZEUS_FAMILY_OPTIMIZER_MAX_LEGS` (default 1; shadow at 2; live promotion gated).
3. `optimize_exclusive_outcome_portfolio` already handles multi-leg correctly. Verify ELG computation against partition payoff structure.
4. New tests verifying ELG optimality on synthetic 2-bin families.

**Tests:** R4 GREEN; new `test_stage_b_dominates_stage_a_on_two_sided_family`.

**Risk:** Multi-leg sizing can exceed per-family loss cap. Add explicit `max_family_loss_usd` cap to portfolio before sizing.

**Acceptance:** R4 GREEN; shadow replay shows ELG improvement on family-rich days; live gate stays at `max_legs=1` until shadow signs off.

### Wave 5 — Bootstrap market-cost uncertainty (one PR)

**Scope:** Sample `c_b` per bootstrap iteration so `edge_ci_lower` reflects market-cost uncertainty.

**Changes:**
1. `MarketAnalysis._bootstrap_bin` adds:

```python
if self._entry_quote_evidence_yes is not None:
    sigma_c = self._entry_quote_evidence_yes[bin_idx].cost_uncertainty
    c_b = entry_cost_value + rng.normal(0.0, sigma_c)
else:
    c_b = entry_cost_value  # legacy
```

2. `σ_market` refined: `sqrt(spread_variance + slippage_variance + fee_variance + quote_age_penalty)`. Detailed formula in math spec §15.7.
3. Bootstrap retains forecast σ, Platt σ, transfer σ; market σ becomes 4th axis.

**Tests:** R5 GREEN. New:
- `test_zero_market_sigma_preserves_legacy_ci`
- `test_market_sigma_widens_ci_monotonically`
- `test_market_sigma_reduces_edge_count_when_quotes_thin`

**Acceptance:** R5 GREEN; replay shows fewer marginal edges accepted, no impact on tight-quote edges.

### Wave 6 — Unified uncertainty + multiplier collapse (one PR) — HIGH RISK

**Scope:** Remove duplicate soft uncertainty from multiplicative chain. Preserve hard vetoes. Behavior preservation tested via replay.

**Changes:**
1. `dynamic_kelly_mult`: REMOVE ci_width haircuts (#2, #3 in D1 list). Already captured in edge_LCB.
2. `EffectiveKellyContext.haircut` → SOFT input to `σ_market`, not multiplier. Remove from `_size_at_execution_price_boundary` chain.
3. Hard vetoes stay multiplicative {0, 1}: oracle_penalty=0, strategy_phase=0, executable_mask=0.
4. `phase_aware_kelly_multiplier.observed_fraction` STAYS — strategy-specific opening behavior, not generic uncertainty.
5. New `src/strategy/kelly_uncertainty_budget.py` aggregates σ contributions; consumed by edge bootstrap (Wave 5).

**Tests:** R3 GREEN. New:
- `test_no_double_count_ci_width`
- `test_no_double_count_spread_depth`
- `test_hard_veto_preserved_at_zero`
- `test_replay_behavior_preservation_within_5pct_size_delta`

**Risk:** Touches live sizing. Mandatory 30-day shadow + paper replay before live promotion. Operator gate required.

**Acceptance:** R3 GREEN; replay sizes within 5% delta on existing live decisions (allow 5% because c_b sampling itself shifts CI); operator sign-off before live promotion.

### Wave 7 — Verification + INV docs (one PR)

**Scope:** Phase 0 audit script re-runs against post-upgrade code; deltas documented; INVs hardened.

**Deliverable:**
- Re-run audit script; output appended to plan doc
- Replay results: before/after table per city/strategy
- INV-38, INV-39, INV-40 antibody tests
- Memory updates: feedback file naming the predecessor-existing fixes (D4 Stage B already existed → bump config not invent)

## New invariants

### INV-38 — bin_edge_entry_price_typed

`BinEdge.entry_price` MUST be `ExecutionPrice`. Float construction at this seam is forbidden. Antibody test: `test_R2_bin_edge_executable_provenance`.

### INV-39 — kelly_boundary_no_fabrication

`_size_at_execution_price_boundary` MUST NOT construct `ExecutionPrice(price_type="implied_probability")`. Upstream provenance carries through. Antibody test: `test_kelly_boundary_no_longer_fabricates_implied_probability`.

### INV-40 — uncertainty_single_count

Every uncertainty source contributes to size reduction EXACTLY ONCE — either via `edge_LCB` (soft σ) or via {0,1} hard veto multiplier. No double-counting. Antibody test: `test_no_double_count_*` suite.

## Risk tracking

| Risk | Mitigation |
|---|---|
| Wave 2 type bump breaks downstream `bin_edge.entry_price` float consumers | Grep-audit before commit; coerce at consumers if needed |
| Wave 3 depth walk hits stale orderbook cache | EntryQuoteEvidence.quote_age_ms gate; STALE marks reliability |
| Wave 4 multi-leg exceeds family loss cap | Explicit `max_family_loss_usd` cap in portfolio |
| Wave 5 σ_market formula too aggressive → no trades | Replay validation; σ_market start conservative (½ spread) |
| Wave 6 net behavior change → silent live size shift | Mandatory shadow + paper replay; operator gate |
| Predecessor work overlap (D4 Stage B already exists) | Documented in plan; Wave 4 = config bump not invention |

## Predecessor inventory (Fitz §3 immune check)

| Component | Predecessor | Verdict |
|---|---|---|
| Typed price at Kelly boundary | `ExecutionPrice` class | EXISTS — extend, do not replace |
| Microstructure haircut object | `EffectiveKellyContext` | EXISTS but mis-located in mult chain (Wave 6 moves) |
| Family portfolio optimizer | `optimize_exclusive_outcome_portfolio` | EXISTS, working — config-pinned only |
| Bootstrap with transfer σ | `transfer_logit_sigma` + `_bootstrap_bin` | EXISTS — add market σ alongside |
| MODEL_ONLY default posterior | `MODEL_ONLY_POSTERIOR_MODE` | EXISTS — preserve |
| Polymarket fee formula | `polymarket_fee` | EXISTS — reuse |
| MarketPriorDistribution contract for shadow blending | `MarketPriorDistribution` | EXISTS — preserve, future shadow work uses it |

## Execution order

```
Wave 0  ──► Wave 1  ──► Wave 2 ──► Wave 3 ──► Wave 5 ──► Wave 6 ──► Wave 7
                  │                              │
                  └──► Wave 4 ────────────── ────┘
```

Wave 4 (Stage B activation) is independent of Waves 2/3 boundary work and can land in parallel.

## Acceptance for declaring upgrade complete

1. All 6 R-tests GREEN.
2. INV-38, INV-39, INV-40 antibody tests in `tests/` and `architecture/invariants.yaml`.
3. Replay before/after: edge_count delta documented per city/strategy; size delta within 5% on existing live decisions (or operator-explicit deviation).
4. Math spec §15.7 documents new market-cost executable seam.
5. Memory feedback files for predecessor-existing fixes (Stage B config bump, EffectiveKellyContext relocation).
6. Audit script re-run on post-upgrade code shows no `price_type="implied_probability"` at Kelly boundary, no top-of-book-only VWMP, no fixed `c_b` in bootstrap.
