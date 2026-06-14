# P1_S5 — Gate / Licensing-Implication Strategy: Is ANY Gate Actually in the No-Correct-Fill Path?

**Date:** 2026-06-14
**Lens:** Gate / licensing-implication strategist (refutation-aware). The synthesis blamed the licensing layer; verification refuted it. My mandate is to independently decide whether ANY gate / licensing / submit-path layer is actually implicated in the no-correct-fill, or whether it is purely upstream bin-belief — and to recommend gate-layer COLLAPSE / DELETE only where the evidence implicates a gate, otherwise recommend simplification.
**Mode:** PLAN-MAKING. Read-only over DBs (`file:state/<db>.db?mode=ro`). No production edits, no deploy, no live touch.
**Authority basis read in full:** `diagnosis_confirmation.md` (authoritative target), `synthesis.md` (central claim REFUTED; mined for keep_invariants + the 5 contradictions), `b2_capital_efficiency_audit.md`, `live_state_tracker.md`, `AGENTS.md`, `docs/authority/replacement_final_form_2026_06_09.md`; source: `live_admission.py`, `event_reactor_adapter.py` (q_lcb seam + selection-shrinkage + EMOS override sites), `probability_uncertainty.py`, `selection_shrinkage.py`, `market_fusion.py`, `market_analysis.py`, `money_path_adapters.py`, `state/sigma_scale_fit.json`, `config/settings.json`.

---

## 0. One-paragraph verdict (the answer to my lens)

**No gate, no licensing layer, and no submit-path layer is the binding constraint on a CORRECT-bin fill. The gate layer is, in aggregate, doing its job correctly — it is the only reason the system has not bled capital.** The settlement record proves it: of the candidates the system itself ADMITTED (`proof_accepted=1`) on the buy_yes side, **0 of 1,619 won at settlement**; on the live strategy of record (`probability_authority='replacement_0_1'`), **4 won / 46 lost = 8.0%**; the cheap-tail buy_yes class the synthesis wanted to unblock settled **0 of 453**. The `coverage_unlicensed_tail` and `capital_efficiency` gates that block these are fail-CLOSED antibodies performing exactly their contract; loosening or "reconnecting a licensed source" so these candidates could fill would have produced a stream of losing trades, not alpha. The defect is entirely UPSTREAM in the bin-belief foundation (operator law 8): the model's q/q_lcb confidently disagrees with the market on cheap bins and is wrong every time settlement adjudicates it. **Therefore my gate-layer recommendation is DELETE / COLLAPSE, never loosen.** Three gate-adjacent structures are dead weight and should be removed under the K<<N law: (1) the EMOS-CI live-license override lane (`edli_emos_ci_live_enabled`, `state/emos_ci_license.json`) — dead by flag-default and operator law; (2) the C2/C3 selection-shrinkage apparatus pinned to `authority_on=False` (computed, imported, never decides) — dead shadow; (3) the condemned BH/FDR selection gate that the C2/C3 work was built to REPLACE but which still silently IS the live selection authority — a one-authority-too-many that must be resolved by choosing ONE selection gate, not running the dead replacement alongside the condemned incumbent.

---

## 1. Re-derivation from the evidence (I rebuilt the chain myself, not "see the diagnosis")

### 1.1 Where the live q_lcb actually comes from (the foundation the gates consume)

The synthesis and the diagnosis both gestured at "q_lcb collapses to 0" without nailing the live construction path. I traced it end-to-end:

1. The live candidate proof is built in `_canonical_probability_and_fdr_proof` (`event_reactor_adapter.py:10228`). For each bin it reads the YES **probability** bootstrap samples via `analysis.bin_yes_probability_samples(index, edge_n_bootstrap())` (`:10322`).
2. Those samples are produced in `MarketAnalysis.forecast_yes_probability_samples` (`market_analysis.py:896-964`): per draw, resample the 51 ENS member daily-maxes (`_bootstrap_p_raw_all`), apply the **current/MAP** Platt `(A,B,C)` (BUG #129 fix — no random historical-param triple), compute the posterior `p_post[bin_idx]`. The sample vector is `q_yes^(b)` for that bin.
3. `q_lcb_yes = lower_quantile(q_yes^(b), α=0.05)` — the 5th percentile — computed in the one seam `_side_q_lcb_from_yes_samples` (`:10148-10191`) via `probability_uncertainty.probability_uncertainty_from_samples`. Clamped `q_lcb ≤ q_point`.
4. `q_lcb_no = lower_quantile(1 − q_yes^(b)) = 1 − q_ucb_yes` (native-NO authority, Hidden #3), same seam.

**Consequence (confirmed against `b2_capital_efficiency_audit.md` §4):** for a far-OTM / cheap bin where few or zero resampled ensemble members land in the bin, the 5th percentile of `p_post[bin_idx]` is **structurally 0**. `q_lcb ≈ 0 → conservative_ev = (q_lcb − price)/price ≈ −1 →` honest `capital_efficiency` reject. This is NOT a calibration floor crushing real mass; it is the ensemble genuinely placing near-zero mass on that bin. The `probability_uncertainty.py` module header still says "DEFAULT-OFF / SHADOW … not wired into the live decision path" (`:17-19`) — that docstring is **STALE**: `event_reactor_adapter.py:67-77` and `:10174-10181` import and call it on the canonical live path. (Provenance note for whoever edits it: the shadow claim is false; the module is live.)

### 1.2 The decisive law-8 fork, resolved by settlement (my own re-run)

The diagnosis left the true fork open: is `q_lcb≈0` on cheap bins a CORRECT belief (honest no-edge) or a BROKEN calibration crushing real probability? I resolved it by joining stored decision-time belief (`edli_no_submit_receipts.receipt_json`) to realized settlement (`zeus-forecasts.db.settlement_outcomes`, `authority='VERIFIED'`, 7,010 rows). The `zeus-world.db.settlements` table is empty; settlement truth lives only in forecasts.db (this matches sibling S4).

| Population (buy_yes) | n matched | won | lost | win-rate | avg PnL / \$1 if filled |
|---|---:|---:|---:|---:|---:|
| cheap tail (`c_cost_95pct < 0.05`) | 453 | **0** | 453 | **0.000** | **−0.0206** |
| ALL `proof_accepted=1` (passed every gate) | 1,619 | **0** | 1,619 | **0.000** | — |
| live strategy of record (`probability_authority='replacement_0_1'`) | 50 | 4 | 46 | **0.080** | — |

Worked examples from the cheap-tail join (decision-time belief → realized winner):
- Tel Aviv high 2026-06-03, bin "28°C", **q_lcb=0.471**, price 0.025 → settled **32°C** (LOST; winning bin 4°C away).
- Warsaw high 2026-06-03, bin "20°C or below", q_lcb=0.078, price 0.016 → settled **23°C** (LOST).

**Verdict on the fork:** the cheap-bin "edge" is NOT suppressed alpha. It is the model's own **bin-belief being confidently wrong** — exactly the operator-law-8 failure (wrong bin-identity/center makes every downstream q/q_lcb confidently wrong). The 47%-claimed Tel Aviv 28°C bin losing to a 32°C settlement is a forecast-CENTER error, not an LCB-floor error. The gates that rejected these were the system's only correct actor.

### 1.3 The other direction (buy_no) confirms operator law #4

| Population (buy_no) | n | won | lost | win-rate | avg PnL / \$1 |
|---|---:|---:|---:|---:|---:|
| cheap (`<0.05`) | 2,317 | 2,317 | 0 | 1.000 | +0.9775 |
| `0.6–0.8` | 65 | 42 | 23 | 0.646 | (thin) |
| `≥0.8` | 238 | 232 | 6 | 0.975 | (thin) |

The 1.000 win-rates are **base-rate survivorship**: a buy_no on a longshot YES bin is buying a near-certain favorite already priced 95-98¢; "+0.9775 PnL per \$1" means risking 98¢ to make 2¢ on outcomes that almost always go NO. This is precisely the "buy_no ~90% win and cost>0.6 favorite-buying are BASE RATE already in the price — NOT alpha" the operator contract forbids re-enabling. Unblocking submission re-enables THIS, not correct-bin alpha.

**Net foundation finding:** there is no cheap-longshot alpha to unblock, and the favorite-NO lane is base-rate. The binding constraint is the bin-belief, full stop. Everything below is about what that means for the GATE layer specifically (my lens).

---

## 2. Is ANY gate / licensing / submit layer implicated? — gate-by-gate adjudication

I examined every gate on the admission→submit path and asked one question of each: *does this gate ever block a candidate that settlement proves would have WON?* If never, the gate is not implicated in the no-correct-fill and is a KEEP (or a DELETE-for-deadness, never a loosen).

### 2.1 `capital_efficiency_lcb_ev` — `live_admission.py:87-119` — **KEEP, not implicated**

The gate is one line: `conservative_ev = (q_lcb − price)/price; reject iff ≤ 0` (`:113-114`). It is the honest q_lcb>price arbiter. It fires ~88% (18,966 in the recent 2,000-cycle window) because `q_lcb≈0` on the bins the market prices cheap. Every cheap buy_yes it rejected settled at 0% (§1.2). **It has never blocked a settlement-winner; it has blocked 453+ settlement-losers.** Synthesis keep-invariant #2 is correct. Do NOT loosen. This is not a gate defect; it is the gate faithfully reporting a broken upstream belief.

### 2.2 `coverage_unlicensed_tail` — `live_admission.py:141-180` — **KEEP, not implicated, ~0.6% tail**

Fires iff `price < 0.05` AND `q_lcb > 2×price` AND `source ∉ {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}`. This is the fail-CLOSED dual of the Milan-24C fail-open incident (`2026_06_10_milan_24c_first_fill_rootcause.md`). It fires on ~0.6% of rejections (128 in the recent window; **0 of 62,874 receipts** ever carry it as a persisted reason). The synthesis made this the headline; the diagnosis refuted that ~150:1. My settlement evidence closes the case: the exact intersection it blocks (cheap + material-YES-disagreement + unlicensed) is the **0/453 losing class**. The gate is RIGHT to block it. Synthesis keep-invariant #3 is correct. The ONLY thing wrong with this gate is that its licensed-source set can never be reached by a real cheap-tail candidate (see §3) — but since the candidates it would admit are settlement-losers, that unreachability is a FEATURE, not the bug the synthesis claimed.

### 2.3 `direction_law` — **KEEP, not implicated, ~3%**

Rejects bins outside the ensemble forecast-adjacent range (BIN_FORECAST_MISMATCH). 686 in the recent window. Inviolable per operator contract law #6. Not implicated in suppressing a winner; it is the structural guard that the candidate bin is even in the model's support. Keep.

### 2.4 BH/FDR family gate (`evaluate_fdr_full_family`, `money_path_adapters.py:60-105`) — **IMPLICATED as a one-authority-too-many — see §4**

This is the live selection authority (`event_reactor_adapter.py:2866-2871`: "the BH/FDR pass is the unconditional live selection authority"). It runs Benjamini-Hochberg over the family's edge-space MC p-values (`apply_familywise_fdr`). It is NOT implicated in suppressing correct-bin alpha (the candidates it passes still lose at settlement, §1.2). But it IS implicated structurally: the C2/C3 authority (`selection_shrinkage.py`, task #60, operator-ratified) was built specifically because **"BH/FDR on the trading path [is] CONDEMNED"** (module header) — BH consumes degenerate {0,1} p-values on the buy_no leg (a literal no-op), and even with continuous p-values, sum-to-one bins violate PRDS so BH is invalid, and FDR controls E[V/R] not bankroll log-growth (wrong objective). The replacement was authored, imported, and is COMPUTED every cycle — then pinned `authority_on=False` so the **condemned gate it was meant to replace is still the live arbiter**. This is the gate-layer's real defect: not that a gate blocks a winner, but that the live selection gate is the one the project already condemned, with its sanctioned replacement sitting dead beside it.

### 2.5 Submit-path: `real_order_submit_disabled` / `SUBMIT_ABORTED_MODE_FLIPPED` / M5 latch — **NOT the binding constraint NOW**

The #2 "secondary blocker" in the diagnosis. Adjudication:
- `real_order_submit_enabled = True` in live config (`config/settings.json` edli). The arm IS on. The 23 `real_order_submit_disabled` cheap-tail receipts are **historical** (pre-arm). This is a flag, and it is in the intended ARMED state — it is NOT a gate blocking current candidates.
- `SUBMIT_ABORTED_MODE_FLIPPED` (`event_reactor_adapter.py`, task #7): a fresh-snapshot mode flip between proof and submit — a correctness guard (don't submit a TAKER proof when the book flipped to MAKER), not a suppression of correct-bin alpha. Keep.
- M5 WS-gap submit latch: self-cleared `2026-06-14T01:06` (diagnosis GAP 5), 0 unresolved findings. The settled-class external-close absorber (#31) self-heals it. Keep the absorber; the latch is transient, not binding.

**The submit path is unblocked and idle for lack of an admitted candidate** — which is correct, because the admitted candidates would lose. The submit layer is not implicated.

### 2.6 EMOS-CI live-license override (`event_reactor_adapter.py:11990-12090`) — **DEAD, DELETE (see §3)**

Distinct from the live EMOS sole-calibrator (§2.7). This is the `EMOS_ANALYTIC`-stamping override, triple-gated (`edli_emos_ci_live_enabled` default-False; HIGH-metric; per-city `emos_ci_k_cov` which returns None because `state/emos_ci_license.json` never existed). Fires 0×. Even if armed, `emos_q_lcb_no = 0.0` hardcoded (`:12065`) kills buy_no. This is the only "licensed source" path `coverage_unlicensed_tail` would accept, and it is unreachable — but per §1.2 that unreachability blocks losers, so reaching it is undesirable. It is pure dead code behind a default-off flag + a never-built operator artifact. DELETE per K<<N.

### 2.7 EMOS sole-calibrator (`edli_emos_sole_calibrator_enabled = True`, `build_emos_q`) — **LIVE, NOT a gate — flagged for the bin-belief lens, not mine**

This IS live (config True). For served=emos cells the traded q is the analytic EMOS predictive N(μ,σ) — its note claims it "fixes the point under-dispersion that manufactures the far-OTM buy_no flood" and the "structural cold-shift." It is a q-CONSTRUCTION regime, not a gate. It is in scope for the bin-belief/calibration strategist, not the gate lens — but I flag that the 15 `q_source='emos'` proof_accepted buy_yes candidates ALSO lost at settlement (§1.2 q_source split), so EMOS-sole-calibrator is not yet a settlement-proven foundation either. (Handed to the foundation lens.)

---

## 3. The licensing layer: two vocabularies, one of them dead

The synthesis's K1 was "reconnect a licensed source." There are exactly two licensed-source vocabularies in the codebase, and the diagnosis + my evidence show neither should be the fix:

**Vocabulary A — source-allow-list** (`live_admission.py:138` `COVERAGE_LICENSED_LCB_SOURCES = {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}`). The membership test in `coverage_unlicensed_tail`. `EMOS_ANALYTIC` is stamped only by the dead override (§2.6). `SETTLEMENT_ISOTONIC` is stamped only when the settlement-backward coverage shrink fires, which needs ≥30 settled obs per (city,metric,season,bin) — tail bins on near-future dates never have it. So **0 of 62,874 receipts ever carry either licensed source** (diagnosis GAP 3). Every live cheap-tail candidate is `FORECAST_BOOTSTRAP` or NULL.

**Vocabulary B — settlement-backward-coverage VERDICT** (`live_admission.py:40` `SETTLEMENT_COVERAGE_LICENSING_STATUSES = {LICENSED, UNLICENSED}`). Already used by the buy_no conservative-evidence gate (`:251-253`) and the cert credential. The synthesis's preferred K1(b) was to swap `coverage_unlicensed_tail`'s membership test to read THIS verdict instead, "collapsing two vocabularies into one."

**My adjudication (refutation-aware):** the synthesis's own rank-2 kill-criterion was "if zero families carry a LICENSED verdict on any cheap tail bin, the edge is unproven — do NOT force it." My settlement evidence answers that pre-emptively: the cheap-tail class settles 0/453, so even a LICENSED verdict on it would be licensing a loser. **Do NOT implement K1(b).** BUT — the two-vocabulary duplication is real and is a legitimate K<<N collapse target *independent of the no-fill question*: Vocabulary A (the static source-allow-list) is the weaker, less-honest one (a brand string, not evidence), and its only writer (the EMOS override) is being deleted. So the collapse is: **delete Vocabulary A and the EMOS override that feeds it; `coverage_unlicensed_tail` keeps its price/disagreement conditions but reads the settlement-VERDICT (Vocabulary B) for the licensed-escape, matching the buy_no gate.** This is a simplification (one licensing authority, the evidence-based one), NOT a loosening — the disagreement+price block stays fail-closed; only the unreachable brand-string escape hatch is replaced by the evidence escape hatch already in use elsewhere. Net gate count on the cheap-tail path: unchanged; net licensing VOCABULARIES: 2→1; net dead code deleted: the entire EMOS-CI override + license-file lane.

---

## 4. The dead-replacement-beside-condemned-incumbent problem (the gate layer's one real internal defect)

This is the most important gate-layer finding and the cleanest K<<N win in my lens. Three selection authorities currently coexist where there should be ONE:

1. **BH/FDR family gate** — LIVE arbiter (`event_reactor_adapter.py:2869` `_gate_passed = fdr.passed`). Project-CONDEMNED (selection_shrinkage.py header: degenerate {0,1} p-values, PRDS violation, wrong objective).
2. **C2/C3 selection-shrinkage** (`_compute_selection_shrinkage` `:2075`, `select_license`/`eb_shrink_edges`/`lfsr` from `selection_shrinkage.py`) — the operator-ratified REPLACEMENT (task #60, authority A2). Imported and COMPUTED every cycle, stamped to receipts as `lfsr/edge_shrunk/edge_shrunk_posterior_sd/selection_authority`. Pinned `authority_on=False` (`:2811`) → "never influences the live decision" (`:2057`). **Pure dead shadow.**
3. **capital_efficiency** + **coverage_unlicensed_tail** + **direction_law** — the honest admission gates (KEEP, §2).

Receipt evidence the shadow is dead: every recent receipt has `selection_authority=NULL` (not even "BH_FDR") and `lfsr=NULL`, `edge_shrunk=NULL` — the FDR_REJECTED early-return (`:2872`) doesn't even call the shadow-stamp `_with_shrink`, so the shadow telemetry it was kept for is itself not landing. The C2/C3 work is **inert in both roles**: it doesn't decide, and its diagnostic stamp is mostly NULL.

**The defect is not "a gate blocks a winner." It is "the live selection gate is the one we condemned, and its sanctioned replacement runs dead beside it, producing neither decisions nor usable telemetry."** That is gate-MASS (operator law: the gate mass itself is the disease), and it is exactly what K<<N targets.

---

## 5. Two-to-three weighed alternatives per decision + my opinionated pick

### Decision A — What to do about the licensing layer (`coverage_unlicensed_tail` + EMOS-CI lane)

- **A1 — Implement synthesis K1(b)** (swap the tail gate to read the settlement VERDICT so backed cheap tails admit). *Rejected.* My settlement evidence (0/453) shows the admitted candidates would lose; this licenses losers. Violates contract law (a fix that just fills an order is failure). The synthesis's own kill-criterion fires.
- **A2 — Build the EMOS license file** (synthesis K1a). *Rejected hard.* Operator memory law (no shadow/gate-mass; never build a new license artifact + flag); diagnosis GAP 6 (never-built by design); and it would license HIGH-metric cheap tails that still lose.
- **A3 — DELETE Vocabulary A + the EMOS-CI override; collapse the tail gate's licensed-escape onto the settlement VERDICT (Vocabulary B) already used by the buy_no gate. Keep the price/disagreement fail-closed block unchanged.** **← PICK.** Why: removes ~100 lines of dead override + a never-built artifact + a default-off flag (K<<N); unifies two licensing vocabularies into the one evidence-based authority; loosens NOTHING (the block stays, the escape is the honest verdict not a dead brand); and is orthogonal to the no-fill (it does not try to manufacture a fill the settlement record refuses). This is the only option consistent with all three of: operator anti-gate-mass law, the refutation of the licensing-cause story, and the settlement evidence.

### Decision B — What to do about the dead C2/C3 vs condemned BH/FDR selection gate

- **B1 — Leave it (status quo): BH/FDR decides, C2/C3 computes dead, selection_authority mostly NULL.** *Rejected.* Three selection authorities, the live one condemned, the replacement inert — maximal gate-mass, the disease itself.
- **B2 — Promote C2/C3 to the live selection authority** (`authority_on=True`), retire BH/FDR. *Tempting but DEFERRED, not now.* It is the operator-ratified replacement and is the *correct* objective (posterior log-growth, lfsr, EB winner's-curse shrinkage). BUT promoting a selection gate while the FOUNDATION produces 8%-win admitted candidates would just select among losers more honestly — it cannot manufacture edge a wrong bin-belief lacks (contract law 8). Promote only AFTER the foundation is settlement-proven, else it is motion without alpha. Also: it needs its own shadow-prove (does EB-licensed selection beat BH on settled win-rate?) before it can be the live arbiter.
- **B3 — Resolve to exactly ONE selection authority now by DELETING the dead C2/C3 live-path apparatus and keeping BH/FDR as the sole (interim) gate, OR by demoting BOTH to a single honest admission test.** **← PICK (collapse, deferred promotion).** Concretely for THIS plan: (i) DELETE the dead `_compute_selection_shrinkage` live-path call + the NULL-producing `_with_shrink` shadow stamp from the decision path — it decides nothing and its telemetry is NULL, so it is pure mass; preserve the `selection_shrinkage.py` MODULE (pure math, tested) for the future promotion in B2, but stop importing it into the live cycle. (ii) Leave BH/FDR as the interim selection gate with an explicit `# CONDEMNED-INTERIM` provenance note pointing at the foundation fix as the unblock for B2. Rationale: collapses 3 selection authorities → 1, deletes inert mass now, and does NOT prematurely promote a gate that can't help until the foundation is fixed. The full BH/FDR→C2/C3 swap is a foundation-gated follow-up, not a no-fill fix.

### Decision C — Is the submit path worth any change?

- **C1 — Harden/loosen the submit latch or mode-flip guard.** *Rejected.* `real_order_submit_enabled=True`, latch self-clears, mode-flip is a correctness guard. Nothing here blocks a correct-bin fill; the path is idle for lack of an admitted WINNER, which is correct.
- **C2 — No submit-path change.** **← PICK.** The submit layer is not implicated. Keep the external-close absorber (#31) and the mode-flip guard (#7); both are antibodies, not blockers.

---

## 6. The causal chain to a settlement-proven CORRECT-bin fill (and where the gate layer sits in it)

The honest causal chain — and the explicit statement that the gate layer is NOT on the critical path:

```
[FOUNDATION — owned by the bin-belief / calibration lens, NOT this plan]
  correct bin identity + center (μ*) + honest spread (σ_pred)
    → q that honestly disagrees with the market AND settlement backs it
    → q_lcb that is non-zero on the bins the model is RIGHT about
        |
        |  (only when the above produces a candidate whose q_lcb > price
        |   AND whose bin actually wins at settlement)
        v
[GATE LAYER — this plan; already CORRECT, must stay fail-closed]
  capital_efficiency (q_lcb>price)         → KEEP (honest arbiter)
  coverage_unlicensed_tail (price/disagree) → KEEP block; DELETE dead brand-escape, use verdict
  direction_law                            → KEEP (law #6)
  ONE selection gate (BH/FDR interim; C2/C3 after foundation) → COLLAPSE 3→1
        v
[SUBMIT LAYER — unblocked, idle, correct]
  real_order_submit_enabled=True; mode-flip guard; M5 absorber → KEEP, no change
        v
  FILL → SETTLEMENT → >51% after-cost win-rate (the only DONE)
```

**The load-bearing arrow is the first one.** No gate-layer change anywhere downstream can produce a winning fill while the foundation emits 8%-win candidates. My plan's job is to (a) prove the gates are not the blocker so the foundation lens gets the mandate, and (b) DELETE the gate-mass that is masquerading as relevant (EMOS-CI lane, dead C2/C3 live-path, the licensing-vocabulary duplication) so the post-foundation system is honest and minimal. **A gate-layer fix that "unblocks a fill" here is, by the settlement evidence, a fix that fills a LOSER — the exact failure the operator contract names.**

---

## 7. Invariants (what my changes must preserve)

- **INV-G1 (keep_invariant #2):** `capital_efficiency` stays `(q_lcb−price)/price ≤ 0 → reject`. Never loosened, never thresholded, never given a floor exception.
- **INV-G2 (keep_invariant #3):** `coverage_unlicensed_tail`'s price<0.05 + disagreement>2× fail-CLOSED block survives. Only the *licensed-escape membership test* changes (brand-list → settlement verdict), and only in the direction of MORE evidence, never less.
- **INV-G3:** direction law (#6) and settlement-preimage q spine (#5) untouched.
- **INV-G4 (operator anti-gate-mass):** net selection authorities after the change = 1, not 3. Net new gates/caps/flags introduced = 0. Net dead code deleted > 0.
- **INV-G5 (CI-honesty, keep_invariant #6):** no licensing change may shrink σ below MC or license a LOW-metric EMOS cell. (Deleting the EMOS-CI override trivially satisfies this — it removes the only code that touched k_cov·σ.)
- **INV-G6 (INV-37 / keep_invariant #4):** any DB write touched by a deletion still uses ATTACH+SAVEPOINT cross-DB discipline; the receipt schema columns `lfsr/edge_shrunk/edge_shrunk_posterior_sd/selection_authority` may be left in place (nullable, harmless) even after the live stamp is removed — a column drop is a separate migration, not required for the gate-mass collapse.
- **INV-G7 (preserve the math module):** `selection_shrinkage.py` (pure, tested, operator-ratified) is NOT deleted — only its inert live-cycle import is removed. It is the B2 promotion substrate once the foundation is settlement-proven.

---

## 8. Failure modes + the verification that catches each

| # | Failure mode | How it manifests | Verification that catches it |
|---|---|---|---|
| F1 | Deleting the EMOS-CI override silently changes a LIVE q_lcb (if the override ever fired) | a city's q_lcb shifts post-deletion | Pre-deletion grep proof: `grep -c 'EMOS-CI override' logs/zeus-live.log` = 0 (diagnosis GAP 2/D1). Property test: with `edli_emos_ci_live_enabled=False` (default) the override is already a no-op, so deletion is byte-identical. Snapshot q_lcb on 100 recent families before/after = identical. |
| F2 | Swapping the tail gate's licensed-escape to the verdict accidentally ADMITS a cheap loser | a `price<0.05` candidate becomes `proof_accepted=1` | Replay the 453 cheap-tail receipts through the new gate: assert admitted-count stays 0 unless the family carries a LICENSED settlement verdict; and even then, the settlement join (0/453) means the *block* must remain on the unbacked majority. Kill-criterion: if admitted-count > 0, the verdict authority is too loose — halt and re-audit. |
| F3 | Removing the dead C2/C3 live-path import breaks the receipt schema / a downstream reader | NULL columns become missing-attribute errors | The columns are already NULL on 100% of recent receipts; readers must already tolerate NULL. Run the receipt-reader path (`_with_shrink` removal) against a recent receipt; assert no KeyError. Keep columns nullable (INV-G6). |
| F4 | Leaving BH/FDR as interim selection gate masks the foundation problem again | "no-edge" gets re-attributed to FDR | Add the `# CONDEMNED-INTERIM` provenance note + a cycle-summary line that, when ALL candidates are rejected, prints the SETTLEMENT-grade of the best displayed candidate's bin if known — so the log never again reads "honest no-edge" without the settlement context. (Observability, zero runtime risk — the one piece of the synthesis's K3 that survives, because it is honest.) |
| F5 | The foundation lens "fixes" calibration and the gate layer over-blocks the now-correct cheap bins | real winners start getting rejected by `coverage_unlicensed_tail` | This is the GOOD problem. When the foundation produces a cheap bin whose settlement VERDICT is LICENSED, the verdict-escape (Decision A3) admits it. Verification: a continuous out-of-sample monitor (sibling S4 §6) — if the foundation-fixed model's cheap-bin win-rate rises above price-implied AND a LICENSED verdict exists, the gate must admit; assert admitted-count rises with realized win-rate, not before it. |

---

## 9. What to KEEP / what to DELETE (the deliverable list)

**KEEP (not implicated; load-bearing antibodies):**
- `capital_efficiency_lcb_ev` (`live_admission.py:87-119`) — honest q_lcb>price arbiter.
- `coverage_unlicensed_tail` price/disagreement fail-closed block (`live_admission.py:141-180`) — its INTENT.
- `direction_law` — law #6.
- `live_buy_no_conservative_evidence` gate (`live_admission.py:183-267`) — already reads the settlement verdict (the GOOD pattern A3 generalizes).
- Submit-path: `real_order_submit_enabled` arm (intended ARMED state), `SUBMIT_ABORTED_MODE_FLIPPED` guard (#7), M5 external-close absorber (#31).
- `selection_shrinkage.py` MODULE (pure math; B2 substrate).
- Settlement-preimage q spine, σ-shape floor, time-semantics contract (keep_invariant #5).

**DELETE / COLLAPSE (gate-mass; K<<N):**
- The EMOS-CI live-license **override** lane (`event_reactor_adapter.py:11990-12090`), the `edli_emos_ci_live_enabled` flag, the `state/emos_ci_license.json` reader (`emos_ci_license.py` load + `main.py:998-1029` boot guard), and Vocabulary A's `COVERAGE_LICENSED_LCB_SOURCES` brand-list — replaced by the settlement-VERDICT escape (A3). Net: ~100+ lines, 1 flag, 1 never-built artifact gone.
- The dead **C2/C3 live-cycle import** (`_compute_selection_shrinkage` call + `_with_shrink` shadow stamp on the decision path, `event_reactor_adapter.py:2806-2827`) — decides nothing, stamps NULL. Collapse 3 selection authorities → 1 (B3). Keep the module.
- (Decision deferred, NOT this plan) the BH/FDR→C2/C3 promotion (B2) — foundation-gated follow-up.

**FIX PROVENANCE (stale docstring, zero behavior):**
- `probability_uncertainty.py:17-19` "DEFAULT-OFF / SHADOW … not wired into the live decision path" — it IS wired live (`event_reactor_adapter.py:10174`). Correct the header so the next session does not mis-audit it as dead.

---

## 10. Honest scope boundary (what this plan is NOT)

This is the GATE lens. It proves the gate/licensing/submit layer is **not** the binding constraint and prescribes the gate-mass deletions that the K<<N law mandates regardless. It does **not** fix the foundation — the 8%-win admitted-candidate problem and the cheap-bin center error (Tel Aviv 28°C@47% → settled 32°C) are the bin-belief / calibration lens's mandate (and sibling S4 locates the one thin real edge at the near-center ring bin). The honest sequencing: the foundation lens fixes WHERE the model is right; my deletions make the gate layer minimal and honest so that, once the foundation emits correct-bin candidates, exactly one selection authority and one evidence-based licensing escape decide them — with no dead lane, no condemned-incumbent, and no brand-string licensing left to mislead the next investigator into the same refuted licensing story.

*End of P1_S5. Read-only; no code or daemon changes made. Every numeric claim is reproducible from `edli_no_submit_receipts.receipt_json` ⋈ `settlement_outcomes(authority='VERIFIED')` and the cited file:line.*
