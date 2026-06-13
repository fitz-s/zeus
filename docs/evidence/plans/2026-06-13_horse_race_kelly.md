# Plan evidence — Horse-race Kelly (consult-3 Q2/P1, task #63)

<!-- Created: 2026-06-13
     Last reused or audited: 2026-06-13
     Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md P1-P2,P5;
       docs/authority/consult3_exit_portfolio_execution_2026-06-13_raw.txt Q2(a)/(b)/(e) -->

## Task
Build the closed-form K-bin mutually-exclusive **horse-race Kelly** allocation that
REPLACES per-candidate "edge > threshold" sizing. **SHADOW-ONLY this pass**: compute the
portfolio-correct allocation and log it beside the live per-candidate size; never change
live sizing while the flag is OFF.

## Authority (governing math, boxed closed form)
`docs/authority/exit_portfolio_execution_authority_2026-06-13.md` §P1:
- Allocate f_k ≥ 0 + cash s, s+Σf_k=1, max Σ_j q_j·log(s + f_j/p_j).
- Not overround (Σp_k ≤ 1): **f_k\* = q_k, s\*=0**.
- Overround: active set `A(s)={k: q_k/p_k > s}`, `s\* = (1−Σ_A q_k)/(1−Σ_A p_k)`, `f_k\* = (q_k − p_k·s\*)_+`.
- No-bet region: `max_k q_k/p_k ≤ 1 → all f_k\*=0, s\*=1`.
Reference implementation: consult3 raw §"Q2 reference implementation" (active-set scan by q/p).
§P2 cross-family QP (binary-moment Cov construction + Fréchet clip + license checks).
§P5 YES(a)+NO(b) dominance/arbitrage LP pre-check.

## Current sizing-path map (where family bin-competition is LOST)
1. `src/strategy/kelly.py::kelly_size` — per-bin Kelly `f*=(p_post−price)/(1−price)` × stacked
   multiplier (`dynamic_kelly_mult`: base 0.25, ci_width haircut 0.10/0.7 + 0.15/0.5,
   lead/heat/strategy/city). **Each bin sized in ISOLATION.** `config/settings.json::edli.kelly_multiplier=0.125`.
2. `src/events/candidate_evaluation.py::CandidateEvaluation` — per-bin record carrying
   `execution_price` (= p_k), `q_lcb_5pct` (= q_k via q_lcb), `q_posterior`, `direction`.
   `robust_kelly_fraction_lcb` property = `(q_lcb−p)/(1−p)` per bin, **isolation Kelly**.
   `live_capital_efficiency_admissible` = the per-bin `capital_efficiency_lcb_ev` gate the
   horse-race supersedes.
3. `src/engine/event_reactor_adapter.py::_select_proof_by_robust_marginal_utility` (L8399) —
   the single live ΔU ranker (`utility_ranker.rank_candidates`) picks ONE winning leg;
   `RobustCandidateScore.optimal_stake_usd` is the live stake on that leg.
4. **`_opportunity_book_from_proofs` (L7228)** — the SINGLE point where the full family's
   `evaluations` tuple (every `{(p_k, q_k)}`) is assembled together, then handed to
   `build_family_opportunity_book`. **This is where horse-race plugs in** (the family
   {(p_k,q_k)} vector is all in scope; bin competition has just been collapsed to one
   ΔU winner per family).

**Where competition is lost:** at the per-bin `robust_kelly_fraction_lcb` / capital-efficiency
gate in `CandidateEvaluation` (each bin scored against price independently), and at the
ΔU ranker picking one leg without solving the joint K-bin water-filling. The endogenous cash
threshold s\* (bins compete for capital) never exists in the current path; `kelly_multiplier`
is a FIXED per-candidate λ, not a portfolio-valid fraction (authority §K2: LCB-Kelly ≡
fractional only for ONE isolated bet → per-candidate λ stacking is NOT portfolio-valid).

## Build (flag-gated, default = current sizing, shadow-compute when off; mirror C2/C3)
- **NEW** `src/strategy/horse_race_kelly.py`: `horse_race_allocation(p, q) -> (f, s_cash)`
  closed form + active-set fixed-point; `portfolio_qp_allocation` (P2); `dominance_lp_check`
  (P5). Pure, fully unit-tested. q input = **q_lcb** (conservative posterior, q_lcb+Kelly law).
- **WIRE** in `event_reactor_adapter._opportunity_book_from_proofs`: behind
  `settings.edli.replacement_horse_race_kelly_enabled` (default false). When OFF: compute the
  horse-race allocation for the family and SHADOW-LOG per-bin f_k\* + s_cash on
  `zeus.replacement_qlcb_shadow` next to the live per-candidate sizes; live sizing untouched,
  byte-identical. Mirror `_apply_james_stein_blend_family` (L8053).

## Registries touched
- `config/settings.json::edli.replacement_horse_race_kelly_enabled` (+ note) → check_settings_consumers
  (reactor reader is the consumer).
- `architecture/source_rationale.yaml` — new `src/strategy/horse_race_kelly.py`.
- `architecture/test_topology.yaml` — new `tests/strategy/test_horse_race_kelly.py`.
- No new receipt/DB columns this pass (shadow attaches to `cache_summary` dict + logger only;
  no schema change).

## Laws honored
NO caps (s\* is endogenous math, not a throttle — no max-exposure cap added); sizing stays
q_lcb+Kelly-family; never change live sizing while flag off (golden byte-identity test);
live DBs read-only; cross-family QP / dominance LP gated behind their own flags / optional
this pass (family horse-race is the priority).
