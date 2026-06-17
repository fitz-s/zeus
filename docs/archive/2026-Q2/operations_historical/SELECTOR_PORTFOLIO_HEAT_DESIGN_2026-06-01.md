# SELECTOR + PORTFOLIO-HEAT DESIGN — 2026-06-01

```
Created: 2026-06-01
Last reused or audited: 2026-06-01
Authority basis: DESIGN_CRITIC_2026-06-01.md (binding) — Design-2 CRITICAL-1 (EV-ranker
  double-counts edge → quadratic), MAJOR-1 (concentration/correlation), MAJOR-2 (sequence LAST);
  BEST_ORDER_SELECTION_ROOT_2026-06-01.md (Root A no-selector, Root B trade_score-as-ranker, §4.3);
  CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md §4.2 (admit-rescope, deferred).
Scope: READ-ONLY design. No code edits. HEAD 9b47b5f301196b464fa0eab1bd4985629d560697.
Deliverables: three deferred designs to specify NOW, deploy AFTER #98 (day0 phase-gate) and
  #58 (q-calibration JJA) verify: (1) #102 book-wide EV/$ selector, (2) MAJOR-1 portfolio-heat
  guard, (3) §4.2 admit-rescope precondition chain.
Method: every file:line grep-verified at HEAD within this session.
```

## EXECUTIVE SUMMARY (6 lines)

1. **Rank key = `(q−cost)/cost` (EV-per-dollar / ROC), NOT `kelly_size×edge` and NOT `f*·edge`.** The LiveCapLedger binds on **notional dollars** (`live_cap.py:20` `max_notional_usd`), so this is a fractional-knapsack problem: greedy by value/weight = EV/cost is bankroll-optimal. `f*·edge` (log-growth) is the wrong objective here — it re-creates the critic's whale over-weighting (math §1.3: a 99.5%/98.5¢ whale ties up 98.5¢ to earn 1.4¢, ROC 0.014, and MUST rank below a thin 50¢ bin at ROC 0.100; both `kelly×edge` and `f*·edge` wrongly rank the whale higher). Tiebreak by `kelly_size_usd`.
2. **Selector seam = a two-phase reactor cycle.** Phase-1 keeps the existing per-event NO_SUBMIT proof writes + commits unchanged (`reactor.py:165-172`); Phase-2 collects the cycle's admission-eligible proofs, ranks by ROC, and moves ONLY the *submit* step behind the ranking — the per-event proof/commit model is preserved (§2.3).
3. **Portfolio-heat guard is GREENFIELD for EDLI.** `portfolio_heat`/`max_portfolio_heat_pct` exist but are wired ONLY into the evaluator/cycle_runner path (`evaluator.py:5451,6001`, `cycle_runner.py:833`), never the event-reactor. The LiveCapLedger caps notional+count but has NO correlation dimension (`live_cap.py:15-26`). Caps are ALREADY lifted ($5→$185, 1/day→1000/day: `settings.json:122-123`, commit `0d0939a480`) — the guard is the missing precondition (§3).
4. **Correlation model = shared `(cluster, season)` bias-correction factor.** Cities in the same JJA/cluster bucket share ONE `model_bias_ens` correction (`blocked_oos.py:38,44`, `drift_refit_arm.py:29`); if that correction is wrong, their NOs lose together. The notional cap treats them as independent and understates true bankroll-at-risk ~2× for 4 correlated positions (§3.3).
5. **§4.2 admit-rescope precondition chain: #58 (calibration) → #103 (CI-aware Kelly) → §4.2 → #102 (selector).** §4.2 removes the q_5pct gate — the ONLY live variance control, because EDLI `evaluate_kelly` takes a FLAT multiplier with no CI term (`money_path_adapters.py:83-101`, `event_reactor_adapter.py:724-737`). FDR is NOT a false-confidence guard (it resamples the same point distribution). §4.2 before #103 = wide-CI bins sized at full flat Kelly (§4).
6. **Hard sequencing (critic FIRM):** selector + EV-admit turn on LAST, only after the wrong-side q-error is gated (#98) and calibration is verified (#58) — otherwise the selector fires the verified-wrong Paris/cold-bias trade #1 with confidence.

---

## 1. BOOK-WIDE EV-PER-DOLLAR SELECTOR (#102)

### 1.1 The defect being fixed (verified, not summarized)

There is no global selector. `fetch_pending` (`event_store.py:107-122`) orders by tier → `priority DESC, available_at ASC, received_at ASC, event_id ASC` — **no quality/edge/EV term**; order is purely arrival. `process_pending` (`reactor.py:165-172`) iterates per-event, each a self-contained claim→proof→mark→commit unit, with no collect-then-rank step. The only cross-candidate `max()` (`event_reactor_adapter.py:2850-2853`) ranks within ONE family's ≤2 tokens by `(trade_score, q_lcb_5pct)`, and is bypassed entirely when the event carries a pre-named `token_id` (`:2842-2849`) — which the redecision/FSR emission path always does (`continuous_redecision.py:289` emits one `EnqueuedRedecision` per qualifying (family,bin,direction)). Net firing rule: **first-qualifying-in-arrival-order**, quality ignored.

`robust_trade_score` (`trade_score.py:48-52`) is `p_fill_lcb × min(q_5pct − c_95 − λ_edge, q_posterior − c_stress − λ_stress)` — a binary admission gate (is the robust edge clear?) mis-used as a continuous ranker. As a ranker it buries near-sure-wins (uses q_lcb not EV, subtracts 95th-pct stress cost, flat λ, ×p_fill).

### 1.2 Resolving CRITICAL-1: the ranker must NOT be `kelly_size × edge`

The root doc's headline (`BEST_ORDER_SELECTION_ROOT §4 line 226-227`) proposes `expected_PnL = kelly_size × (q − cost)`. The critic's CRITICAL-1 is correct that this **double-counts edge**:

```
kelly_size ∝ f* · bankroll,   f* = (q − c)/(1 − c)        [kelly.py:62]
⇒ kelly_size × edge ∝ [(q−c)/(1−c)] · (q−c) = (q−c)² / (1−c)
```

Quadratic in edge, and the `1/(1−c)` blows up as price → 1, so it over-weights near-certain high-price whales. The root doc itself offers the correct alternative at **§4.3 lines 234-235**: `rank_key = (q−cost)/cost` with `tiebreak = kelly_size_usd`. The headline contradicts §4.3; **resolve to §4.3.**

### 1.3 WHICH §4.3 key is correct, and why — the math

Two §4.3-class candidates survive removing the quadratic: **(A) ROC = `(q−cost)/cost`** and **(B) `f*·edge`** (marginal log-growth, the 2nd-order Kelly contribution). They are NOT equivalent. The discriminator is the binding constraint:

- **The EDLI canary cap binds on NOTIONAL DOLLARS** (`LiveCapLedger.max_notional_usd`, `live_cap.py:20`; `tiny_live_max_notional_usd: 185.0`, `settings.json:122`). A trader filling a fixed dollar budget B with divisible positions faces the **fractional knapsack**: maximize Σ(value) s.t. Σ(weight) ≤ B, optimal greedy order = value/weight = **EV per dollar = `(q−c)/c`** (buy NO at cost c, win pays 1, per-dollar return = (q−c)/c). ROC is bankroll-optimal.
- `f*·edge` is the right objective only when the binding constraint is a **variance / risk budget** (Kelly log-wealth growth), not a hard dollar cap. EDLI's cap is dollars, not variance.

Numerical proof (reproduced this session — the whale test is the decider):

| cand | q | c | EV/share | **ROC=(q−c)/c** | f* | kelly×edge |
|---|---|---|---|---|---|---|
| Paris | 0.997 | 0.010 | 0.987 | **98.70** | 0.997 | 246.0 |
| Shanghai-28 | 0.995 | 0.891 | 0.104 | **0.117** | 0.954 | 24.81 |
| Thin-spec | 0.55 | 0.50 | 0.050 | **0.100** | 0.100 | 1.25 |
| Whale-99c | 0.999 | 0.985 | 0.014 | **0.014** | 0.933 | 3.27 |

- **ROC order:** Paris ▸ Shanghai ▸ Thin-spec ▸ **Whale** ✅ (the whale correctly ranks LAST — 98.5¢ tied up for 1.4¢).
- **`kelly×edge` AND `f*·edge` order:** Paris ▸ Shanghai ▸ **Whale ▸ Thin-spec** ✗ (both wrongly rank the whale above the thin bin).

So `f*·edge` does NOT fix CRITICAL-1; it re-creates the whale pathology because `f* → 0.93` even at 98.5¢ (Kelly fraction is high for near-certain bets regardless of capital efficiency). **Only `(q−cost)/cost` makes the whale rank below a higher-ROC thin bin.** This is the bankroll-constrained correct objective.

**FINAL rank key:**
```
rank_key  = (q_posterior − cost_fee_adjusted) / cost_fee_adjusted     # ROC; bankroll-optimal
tiebreak  = kelly_size_usd                                            # deploy more capital when ROC ties
admission = SEPARATE (FDR ∧ point-EV>0 ∧ confidence-floor) — §3/§4, NOT folded into the rank key
```
`q_posterior` and `cost_fee_adjusted` are already on every proof (`event_reactor_adapter.py:704-707` `q_live`, `c_fee_adjusted`); `kelly_size_usd` is on the receipt (`reactor.py:629`). No new computation needed — the selector is pure reordering of existing proof fields.

**Caveat the operator must accept:** ROC ranks cheap-NO whole-dollar-return bins (Paris at 1¢ cost) far above expensive near-sure-wins (Shanghai at 89¢). That is *correct* for a bankroll-constrained trader (Paris returns 98× per dollar) but inverts the operator's "Shanghai is the best, all forecasts agree" intuition. The intuition is the **confidence floor** (a gate), not the ordering. See Open Question Q1.

### 1.4 WHERE it plugs in — the two-phase seam (preserving per-event commit)

The obstacle (correctly identified in the brief): `process_pending` commits each event before looking at the next, so there is no point where a book-wide candidate set exists. The seam, per root-doc §4.3 lines 266-275:

- **Phase 1 — admission (UNCHANGED):** `process_pending` (`reactor.py:165-172`) processes every pending event to a NO_SUBMIT proof exactly as today. `_process_event_unit` keeps its per-event mutex, claim, proof-write, mark, and commit (`reactor.py:174+`). The proof-write path is untouched. Crucially: **the existing path already runs in NO_SUBMIT/shadow** (`real_order_submit_enabled: false`, `settings.json:112`), so Phase-1 proofs are written WITHOUT submitting. The two-phase split is natural — Phase-1 already produces proofs-without-submits today.
- **Accumulation:** Phase-1 appends each admission-eligible proof (those that clear `_receipt_money_path_blocker` returning `(None, "")` — `reactor.py:633`) to an in-cycle `admitted: list[(proof, receipt, event)]`. This is an in-memory list within ONE `process_pending` call; it does not cross cycle boundaries and does not change any DB write.
- **Phase 2 — selection + submit (NEW, gated):** after the Phase-1 loop, if `real_order_submit_enabled ∧ live_canary_enabled`, rank `admitted` by `rank_key` desc (tiebreak `kelly_size_usd`), then iterate top-down calling the EXISTING live-submit path (`event_reactor_adapter.py:295-357` — `executor_submit(final_intent, command)` at `:357`) under the LiveCapLedger K (`reactor.py:163`) and the §3 portfolio-heat ceiling. The LiveCapLedger reservation/consume discipline (`live_cap.py:53,152`) already serializes the cap; it becomes the K in "fire top-K".

`_selected_candidate_proof` (`event_reactor_adapter.py:2839-2853`) **stays unchanged** as the per-family tiebreak; the new selector sits ABOVE it, across families. The redecision token-id bake (`continuous_redecision.py:289`) is fine — each event still resolves to its one proof in Phase-1; the selector ranks across those one-per-event proofs in Phase-2.

**Why this does not break the per-event commit model:** Phase-1 commits proofs per-event as today (proofs are belief artifacts, idempotent, safe to over-produce). Only the irreversible *submit* (real capital) moves behind the ranking. A crash between Phase-1 and Phase-2 loses no proof (committed) and submits nothing (Phase-2 never ran) — fail-closed. The submit step itself keeps its own LiveCapLedger SAVEPOINT/reserve→consume atomicity (`live_cap.py:53,152`).

**Open seam question (Q2):** Phase-1 currently `mark_processed`'s each event inside `_process_event_unit`. If an event is marked processed but its proof loses Phase-2 selection (a better order consumed the cap), the event must NOT re-enqueue as un-acted. Design: the proof is still written + the event still marked processed (the belief WAS evaluated); only the *submit* was deferred to a better order. The redecision `acted_state` (`continuous_redecision.py:284-287`) keys on edge-improvement, so a non-selected-but-evaluated bin re-screens next cycle only if its edge improves — acceptable. Confirm no double-submit: the LiveCapLedger `usage_id` is per-`(event_id, cap_scope)` (`live_cap.py:184`), so re-evaluation of the same event cannot double-reserve.

### 1.5 RED relationship test (per root-doc §5)

`tests/engine/test_best_order_selection_ranks_by_expected_pnl.py` (do NOT author until #98+#58 verify). Asserts the cross-module property: the order that fires = `argmax over admitted set of (q−cost)/cost` (tiebreak kelly_size), NOT first-qualifying-in-arrival. Pre-fix RED: two admitted events in arrival order (thin bin first, Shanghai second, cap=1) → HEAD submits the thin bin. Post-fix: Shanghai fires. Plus the §1.3 whale invariant: a 98.5¢ whale (ROC 0.014) must rank BELOW a 50¢ thin bin (ROC 0.100) — this is the cell that distinguishes ROC from `f*·edge` and must be asserted explicitly.

---

## 2. PORTFOLIO-HEAT / CORRELATION GUARD (critic MAJOR-1)

### 2.1 Is there an existing portfolio_heat feed in the live EDLI path? — NO (greenfield)

Verified: `portfolio_heat` / `portfolio_heat_for_bankroll` / `max_portfolio_heat_pct` exist but are consumed ONLY in the evaluator/cycle path:
- `src/state/portfolio.py:2418` `portfolio_heat_for_bankroll` (the heat computation),
- `src/engine/evaluator.py:5451,6001` (heat → `dynamic_kelly_mult(portfolio_heat=...)`),
- `src/engine/cycle_runner.py:833-835` + `exposure_gate_hit` at `:835`,
- `src/strategy/risk_limits.py:29-55` (`current_portfolio_heat` exposure ceiling),
- `src/engine/replay.py:1715-1720` (replay, `portfolio_heat=0.0`).

The EDLI event-reactor path (`event_reactor_adapter.py` submit `:295-410`, `reactor.py:165-172`) contains **zero** portfolio_heat references. `evaluate_kelly` (`money_path_adapters.py:83-101`) takes only `(p_posterior, execution_price, bankroll_usd, kelly_multiplier)` — no heat, no `dynamic_kelly_mult`. `dynamic_kelly_mult` HAS a `portfolio_heat` arg (`kelly.py:438,499-500`) but is never called from EDLI. **The correlation guard is greenfield for EDLI** — confirmed against the critic's Open Question (line 72).

The LiveCapLedger (`live_cap.py:15-26`) bounds `max_notional_usd` + `max_orders_per_day` — a per-day notional+count cap with NO correlation/heat/variance dimension. It cannot see that 4 admitted $44 NOs are the same regime bet.

### 2.2 Why the guard MUST gate before lifted caps deploy bankroll

Caps are ALREADY lifted: `tiny_live_max_notional_usd: 185.0` (was $5), `tiny_live_max_orders_per_day: 1000` (was 1, un-hardcoded in commit `0d0939a480`) — `settings.json:122-123`. With the #102 selector ranking by ROC and these caps, a single cycle can fire ~4 orders at $44 (≈$176 < $185 cap). If those 4 top-ROC bins are all cold-biased NOs in the same JJA cluster, the LiveCapLedger admits them as 4 independent notional draws while they are in fact **one correlated $176 regime bet**. The selector AMPLIFIES this: ROC ranking will cluster the top of the book with the same-regime cheap-NO winners (they share the bias-correction warm-shift that lifts q and lowers cost together). **The guard is the precondition; without it, the lifted caps + selector deploy the whole canary bankroll into one correlated direction.**

### 2.3 Correlation model — shared (cluster, season) bias factor

Verified shared-factor source: the live bias correction is keyed by `(cluster, season)` buckets, season ∈ {DJF, MAM, **JJA**, SON} (`src/calibration/blocked_oos.py:38` `season`, `:44` `bucket_key(cluster, season)`; `src/calibration/drift_refit_arm.py:29` `_SEASONS`, `:36` `(city, season)` pairs). `edli_bias_correction_enabled=True` live (`settings.json:92` note). Every city in the same JJA/cluster bucket subtracts the SAME `eff_bias_c` (CI_HONESTY ruling §2.3, `event_reactor_adapter.py:3399`). Therefore:

- **Correlation rule (conservative, structural):** two admitted NOs are CORRELATED if they share `(season_bucket, direction=buy_no)` AND both carry a non-zero `_edli_bias_corrected` flag (i.e. both depend on the same regime correction). Same-cluster cities are maximally correlated; cross-cluster same-season are partially correlated. The guard treats same-`(cluster, season)` buy_no as correlation ρ≈1 (worst-case), other same-season as a configurable ρ (default 0.5), cross-season/cross-direction as independent.
- **Why this is the right factor:** the cold-bias/JJA correction is the single shared input that, if wrong (the #58 risk), moves all those bins' q in the same direction simultaneously. Settlement is a shared regime draw, not 4 independent coin flips. This is the Fitz "data provenance" failure mode — correct code, one shared upstream correction whose error correlates the outputs.

### 2.4 Per-cycle bankroll-at-risk ceiling

Define, per `process_pending` cycle, a **bankroll-at-risk (BAR)** ceiling that the Phase-2 submit loop checks BEFORE each `executor_submit`:

```
BAR_correlated(cycle) = Σ_over_correlation_groups  max_loss(group)
  where for a group g of admitted NO bins sharing (cluster, season):
    max_loss(g) = Σ_{i in g} reserved_notional_i            # ρ≈1 → losses add linearly, NOT in quadrature
  and for partially-correlated same-season groups:
    max_loss(g) = sqrt( Σ var_i + 2·ρ·Σ_{i<j} sqrt(var_i·var_j) )   # ρ=0.5 default
ADMIT order k only if  BAR_correlated(cycle so far + k) ≤ max_bankroll_at_risk_pct · canary_bankroll
```

- `max_bankroll_at_risk_pct`: NEW config under a dedicated EDLI section (default conservative, e.g. 0.50 of the canary notional cap, operator-tunable — mirror the existing `max_portfolio_heat_pct: 0.5` at `settings.json:326` but as an INDEPENDENT EDLI key so it does not couple to the evaluator path). The ceiling is on the canary bankroll ($185 cap), not the full on-chain bankroll, during canary.
- **Independent-risk illusion quantified** (reproduced this session): 4 correlated $44 NOs have true stdev ~4σ (linear) vs the ~2σ (=√4·σ) the notional cap implicitly assumes — the cap **understates bankroll-at-risk ~2×** for 4 same-regime positions. The guard closes that gap.
- The guard is a Phase-2 gate (between ranking and submit), evaluated incrementally as the top-K loop reserves cap. It composes with — does not replace — the LiveCapLedger notional/count cap (both must pass).

### 2.5 Greenfield wiring note

Because EDLI has no portfolio state feed, the guard's group membership comes from the in-cycle `admitted` list (§1.4) — it does NOT need the evaluator's PortfolioState. It reads `(family.city → cluster, season)` from the same bias-bucket resolver the correction uses (`blocked_oos.bucket_key`), and `reserved_notional_i` from each receipt's Kelly size. This keeps the guard self-contained to the reactor cycle (no cross-DB read of open positions for v1). v2 may add open-position correlation against the live book, but v1's per-cycle ceiling is the MAJOR-1 precondition the critic requires.

---

## 3. §4.2 ADMIT-RESCOPE SEQUENCING (relative to #103 and #58)

### 3.1 The exact precondition chain (critic FIRM, lines 6-16)

```
#58  q-calibration (June/JJA)        ─ BLOCKS everything below; re-measure CI trustworthiness AFTER §4.1
#103 CI-aware Kelly multiplier        ─ wire dynamic_kelly_mult (CI/lead term) into EDLI evaluate_kelly
§4.2 admit-rescope (EV-gate)          ─ ONLY after #58 + #103
#102 selector (ROC ranking)           ─ LAST, only after #98 gate + #58 verified
```

§4.1 (CI-honesty: one corrected member surface) is already committed (critic line 8, HEAD-of-record `69bee9b752`); §4.2 is the SECONDARY rescope, deferred.

### 3.2 Why §4.2 is UNSAFE before CI-aware sizing (#103) exists

§4.2 replaces the binary `robust_trade_score > 0` admission gate (`reactor.py:621-622`, the q_5pct−c_95−λ term) with `point-EV > 0 ∧ FDR p < α`, delegating variance to Kelly SIZE. This is only safe if Kelly actually carries a variance penalty. **It does not, in the live EDLI path:**

- `evaluate_kelly` (`money_path_adapters.py:83-101`) → `kelly_size(p_posterior, price, bankroll, kelly_mult)` (`kelly.py:31-63`) = `f* · kelly_mult · bankroll`. **No CI term, no lead term, no `dynamic_kelly_mult`.**
- The live multiplier is FLAT: `_runtime_kelly_multiplier()` reads `settings["sizing"]["kelly_multiplier"]` (`event_reactor_adapter.py:4234-4241`), then a coarse `_maybe_bias_decay_kelly_haircut` (`:730`) — a bias-decay haircut, NOT a CI-width term. Path confirmed at `:724-737`.
- `dynamic_kelly_mult` (which HAS the `ci_width` haircut, `kelly.py:481-484`) is called ONLY in `evaluator.py:5997` and `replay.py:1715` — **never** in the EDLI reactor.

So today the q_5pct gate (`trade_score.py:48-49`) is the **ONLY** variance control on the live path. Remove it via §4.2 before #103 wires a CI-aware multiplier, and every wide-CI bin gets sized at full flat Kelly with zero variance penalty — the critic's CRITICAL-1 (Design 3). #103 must wire `dynamic_kelly_mult`'s CI/lead term (or an equivalent `q_5pct`-as-sizing-input) into `evaluate_kelly` FIRST, so that "real edge, high variance → small size" is expressed in SIZE before the gate stops expressing it in ADMISSION.

### 3.3 Why FDR is NOT the false-confidence guard (critic CRITICAL-2)

§4.2's proposed gate leans on `p_value < α_FDR` as the "edge is statistically real" proof. But the FDR p_value = `mean(bootstrap_edge ≤ 0)` resampled from the SAME (possibly miscalibrated) point distribution (`market_analysis.py` bootstrap, CI_HONESTY §2.3). A confidently-WRONG point (tight forecast at the wrong temperature — verified Paris 12-13°C → P(14°C)=0 but settles 14°C) yields a LOW p_value → FDR PASS → wrong-side admitted. `fdr_alpha=0.1` is permissive. FDR detects "is the edge distinguishable from zero given THIS distribution," not "is THIS distribution calibrated to reality." **Do not represent FDR as the false-confidence floor.** The real false-confidence guard is #58 (calibration verified against SETTLED truth) + the #98 day0/phase gate — which is exactly why §4.2 sequences after both.

### 3.4 The §4.1-makes-§4.2-more-dangerous interaction (critic MAJOR-1)

§4.1 narrows the CI (removes the bias-split artifact) → raises q_lcb → lowers the FDR p_value → makes FDR MORE permissive precisely on the cold-bias cities whose correction (#58) may still be wrong. So §4.1 (already shipped) increases the cost of a premature §4.2: re-measure calibration trustworthiness AFTER §4.1 and AFTER #58 before admitting on the narrowed CI. This is the structural reason #58 BLOCKS §4.2, not merely precedes it.

### 3.5 Interaction with #102

The selector (#102) ranks the ADMITTED set. If §4.2 widens admission (more bins admitted on EV>0) before #58 verifies calibration, the selector then ranks a larger contaminated pool and fires the top-ROC member — which, for an uncorrected cold-bias city, is a cheap-NO that looks like high ROC precisely BECAUSE the q is inflated. **The selector amplifies any admission error into capital deployment.** Hence #102 is LAST: it must rank a pool that is both correctly-admitted (§4.2 after #58/#103) and wrong-side-gated (#98). Turning the selector on over today's pool fires the verified-wrong Paris/cold-bias trade #1 (critic line 15-16, HARD NO).

---

## 4. CONSENSUS ADDENDUM (adversarial)

- **Antithesis (steelman against ROC):** `f*·edge` is the textbook Kelly objective — it maximizes long-run log-wealth growth, which is the *theoretically* correct goal for a repeated-betting trader, and it naturally down-weights the over-priced whale via the edge term. Why prefer ROC? Because the BINDING constraint here is a hard notional cap (`live_cap.py:20`), not a variance budget — under a dollar cap the problem is fractional-knapsack and value/weight (ROC) is provably optimal, whereas `f*·edge` still over-weights the whale (§1.3 math: f*=0.93 at 98.5¢). The log-growth objective becomes correct again ONLY once the cap is variance-based rather than notional — a future regime, flagged in Q3.
- **Tradeoff tension (cannot be ignored):** ROC ranking systematically prefers cheap-NO bins (1¢ cost, 98× return) over expensive near-sure-wins (89¢ cost, 12% return). This is *capital-efficiency optimal* but *contradicts the operator's stated intuition* that Shanghai-89¢ is "the best order." Both cannot be the top simultaneously. The resolution (confidence-floor-as-gate, ROC-as-order) is principled but the operator must explicitly bless that the canary will preferentially fire cheap longshots-that-are-near-certain over expensive near-certain whales (Q1).
- **Synthesis:** Keep the operator's "best" intuition as the **confidence floor / admission gate** (high q, tight CI — the #98+#58 verified surface), and let ROC do the *ordering within* that already-trustworthy set. The whale and the wrong-side Paris are excluded by the gate; among survivors, ROC fills the bankroll optimally. This preserves both objectives by assigning each to its correct stage (gate vs order) — exactly the §4.2(admit)+§4.3(order) orthogonality the root doc identifies.
- **Principle-violation flags:** §4.2-before-#103 violates "Kelly must carry variance if the gate stops doing so" (SEV-1: removes the only live variance control). Representing FDR as the false-confidence guard violates data-provenance (SEV-1: FDR cannot see calibration error). Lifted caps before the §3 guard violates "make the category impossible" (SEV-2: notional cap structurally cannot see correlation).

---

## 5. OPEN QUESTIONS FOR OPERATOR

- **Q1 (ROC vs intuition):** Confirm the canary should rank by capital efficiency (ROC) — i.e. fire a 1¢-cost near-certain NO ahead of an 89¢-cost near-certain NO — rather than by the operator's "all forecasts agree, Shanghai is best" framing (which ROC treats as a *gate*, not the *order*). If the operator wants size-weighting to dominate, the tiebreak (`kelly_size_usd`) can be promoted to a blended primary key, but that re-introduces a (weaker) version of the whale over-weighting.
- **Q2 (mark_processed semantics):** Is it acceptable that a Phase-1-evaluated bin which LOSES Phase-2 selection (cap consumed by a better order) is marked processed and only re-screens next cycle if its edge materially improves (`continuous_redecision.py:284-287`)? Or must non-selected-but-eligible bins remain eagerly re-presented every cycle until the cap frees?
- **Q3 (variance-cap regime):** Should the canary cap eventually migrate from notional ($185) to a variance/bankroll-at-risk budget? If yes, `f*·edge` becomes the correct ordering and the §2.4 BAR ceiling becomes the primary constraint rather than a secondary gate — a larger redesign to schedule post-canary.
- **Q4 (correlation ρ defaults):** Are the proposed correlation defaults acceptable — same-`(cluster, season)` buy_no → ρ≈1 (linear loss add), other same-season → ρ=0.5, cross-season/direction → independent? And should `max_bankroll_at_risk_pct` default to 0.5 of the canary notional cap, or tighter for first arming?
- **Q5 (interim variance control):** Pending #103, is the existing bias-decay 0.5× haircut (`event_reactor_adapter.py:730`) a sufficient interim variance floor for a PARTIAL §4.2 (EV-gate with the haircut as the only sizing penalty), or must §4.2 wait for the full CI-aware multiplier? (Critic Open Question, line 70 — needs the #58 calibration session to answer.)

---

## 6. REFERENCES (grep-verified at HEAD 9b47b5f3)

- `src/strategy/live_inference/trade_score.py:48-52` — `robust_trade_score = p_fill × min(q_5pct−c_95−λ_edge, q_posterior−c_stress−λ_stress)`; binary gate mis-used as ranker.
- `src/strategy/kelly.py:31-63` — `kelly_size`; `:62` `f* = (p−price)/(1−price)`; `:433-530` `dynamic_kelly_mult` (CI/lead/heat haircuts), `:481-484` ci_width, `:499-500` portfolio_heat — NOT called from EDLI.
- `src/events/money_path_adapters.py:83-101` — EDLI `evaluate_kelly`: flat `kelly_multiplier`, no CI/heat term.
- `src/events/event_store.py:107-122` — `fetch_pending` ORDER BY tier→priority→arrival; no quality term.
- `src/events/reactor.py:165-172` — `process_pending` per-event commit, no collect-then-rank; `:163` LiveCapLedger; `:618-633` `_receipt_money_path_blocker` admit gate (TRADE_SCORE→FDR→KELLY).
- `src/engine/event_reactor_adapter.py:2839-2853` — `_selected_candidate_proof`: within-family max, token-id bypass; `:295-357` live submit (`executor_submit` :357); `:704-707` proof q_live/c_fee_adjusted; `:724-737` flat-Kelly live path; `:730` bias-decay haircut; `:4234-4241` `_runtime_kelly_multiplier`.
- `src/events/live_cap.py:15-26` — LiveCapReservation: max_notional_usd + max_orders_per_day (no correlation dim); `:53` reserve, `:152` consume, `:184` per-(event_id,cap_scope) usage_id.
- `src/events/continuous_redecision.py:253-289` — `enqueue_live_redecisions`: one event per (family,bin,direction) on flat min_edge; `:284-287` acted_state edge-improve re-fire.
- `src/engine/evaluator.py:5451,5997,6001` + `src/engine/cycle_runner.py:833-835` + `src/strategy/risk_limits.py:29-55` + `src/state/portfolio.py:2418` — the ONLY portfolio_heat wiring (evaluator/cycle path, NOT EDLI).
- `src/calibration/blocked_oos.py:38,44` + `src/calibration/drift_refit_arm.py:29,36` — `(cluster, season)` bias buckets incl JJA = the shared correlation factor.
- `config/settings.json:92` (edli_bias_correction_enabled note), `:112` real_order_submit_enabled=false, `:122-123` tiny_live caps ($185 / 1000/day), `:326` max_portfolio_heat_pct=0.5 (evaluator-only).
- commit `0d0939a480` — un-hardcoded max_orders_per_day (the lifted 1/day cap critic MAJOR-1 flags).
- Binding docs: `docs/operations/DESIGN_CRITIC_2026-06-01.md`, `docs/operations/BEST_ORDER_SELECTION_ROOT_2026-06-01.md` §4.3, `docs/operations/CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md` §4.2.
```
