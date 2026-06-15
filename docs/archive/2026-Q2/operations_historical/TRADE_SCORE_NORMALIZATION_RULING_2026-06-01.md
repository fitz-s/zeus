# TRADE_SCORE Normalization Ruling — 2026-06-01

```
Created: 2026-06-01
Authority basis: GOAL #36 (live must earn alpha; 0-fill = suppression-until-proven-honest),
  EDLI v1 robust trade-score semantics, MODEL_ONLY_POSTERIOR_MODE normalization contract.
Scope: READ-ONLY adversarial architect ruling. No code edits. HEAD 67e3d74673.
Evidence: state/zeus-world.db no_trade_regret_events (3,594 buy_no rows), source @ HEAD.
```

## VERDICT (1 line)

**REFUTED.** The 90% `q_lcb_5pct > q_live` inversion is NOT a structural defect suppressing
real edge. The inversion term is NEVER the binding (`min`) term — the `q_posterior` branch
binds in **100% (3041/3041)** of inverted rows. The dominant `TRADE_SCORE_NON_POSITIVE`
rejection is **honest no-edge**: the NO side is priced at ~99¢ for ~99.8% events, and the thin
~0.5¢ gross edge is correctly killed by `λ=0.01 + 95th-pct cost stress`. The true first-fill
unlock is the **λ penalty + executable-quote economics on near-certain bins**, not the
normalization domain.

---

## 1. The completeness question (decides A vs B): family IS a complete MECE partition

The brief's case split hinges on whether the traded family is MECE-complete. It is — by
construction, enforced at every probability stage:

- **Family scope** (`src/events/candidate_binding.py:99-126`): `family.candidates` = ALL
  market-topology candidates matching `(city, target_date, metric)`, sorted, not just the one
  traded market. The per-row single-bin appearance in `no_trade_regret_events` is the
  per-candidate **receipt granularity**, not the in-memory family cardinality.
- **Bins include absorbing shoulders** (`src/types/market.py:44-115`): open-ended endpoint bins
  `low=None` ("X or below") / `high=None` ("X or higher") are `is_shoulder=True`, covering the
  real-line tails. Live DB labels confirm presence: "Seoul 14°C **or below**", "Wellington 18°C
  **or higher**", "Seattle 66°F **or higher**". Prior doc `BIN_BIAS_VS_MULTISOURCE_2026-05-27.md`
  shows a real Shanghai family with shoulder bin "≤23°C or below" + interior point bins.
- **Sum=1 enforced 3× over exactly these bins:**
  - `p_raw`: `event_reactor_adapter.py:3388-3392` → `arr / total` (explicit normalize).
  - `p_cal`: `:3473-3486` → `calibrate_and_normalize(...)` / identity `arr/total` fallback.
  - `p_posterior`: `market_analysis.py:227` = `_compute_posterior(p_cal)`; in
    `MODEL_ONLY_POSTERIOR_MODE` this is `raw / total` (`market_fusion.py:294-301`).
- `p_raw_vector_from_maxes` docstring (`ensemble_signal.py:202-213`): "bins ... Must be a
  complete partition (cover the real line including shoulders) for the result to sum to 1.0".

**Conclusion: Case (B) holds.** The family is a complete MECE partition; `Σ(yes-posterior)=1`
is the intended, enforced invariant. `.normalized()` in `evaluate_live_bins` is therefore the
calibration-correct (and on this path, near-no-op) renormalization, NOT a fabricated downward
haircut. Case (A) (incomplete family, absolute-NO-win-prob correction) does **not** apply.

> Important correction to the brief's mechanics: the brief asserts q_live is double-normalized
> down while q_lcb stays un-normalized up, so `.normalized()` "fabricates a downward haircut".
> But `p_posterior` is ALREADY sum=1 before `evaluate_live_bins` (line 2983 feeds the
> already-normalized posterior as `prior`), so the second normalize moves q_live by < 1e-9. The
> inversion is NOT a normalization-domain corruption of q_live. See §2 for the real cause.

---

## 2. Why `q_lcb_5pct > q_live` (the inversion is a benign UPPER saturation, not suppression)

`q_lcb_5pct` and `q_live` are computed on two different code paths, both in the SAME
(normalized) probability domain:

- `q_live` (buy_no) = `1 − yes_q`, `yes_q` = normalized `p_posterior[bin]`
  (`event_reactor_adapter.py:2723, 2991-2994`). Point estimate of NO win-prob.
- `q_lcb_5pct` (buy_no) = `hyp.ci_lower + p_market_no` (`:2969`), where
  `ci_lower = percentile₅(bootstrap_edges)` and
  `bootstrap_edges[i] = (1 − p_post_yes_boot[bin]) − c_b` (`market_analysis.py:890`),
  `c_b = clip(N(p_market_no, σ), P_CLAMP_LOW=0.01, P_CLAMP_HIGH=0.99)` (`:875-887`).

So `q_lcb ≈ percentile₅(1 − p_post_yes_boot) − E[c_b] + p_market_no ≈ percentile₅(NO win-prob)`.

The inversion `q_lcb > q_live` arises because:
1. `p_market_no` (added back, fixed) ≠ `E[c_b]` (clipped at `P_CLAMP_HIGH=0.99`). On a ~99.8%
   NO event the true ask is ~0.99–0.995; the bootstrap's `c_b` is **clipped down to 0.99**,
   while the fixed add-back uses the un-clipped `p_market_no`. The clip asymmetry pushes the
   reconstructed `q_lcb` upward, frequently to the `1.0` rail (DB: **3015/3557 = 84.8%** of
   buy_no rows have `q_lcb = 1.0000` exactly).
2. The bootstrap edge distribution is left-skewed and pinned near the top for near-certain
   bins, so `percentile₅` is itself close to the point value, and the clip bias tips it over.

**This is an UPPER-rail saturation of the q_5pct robustness term.** It is benign for safety:

| Fact (DB, buy_no) | Count | Meaning |
|---|---|---|
| `q_lcb_5pct > q_live` (inversion) | 3041 | the reported 90% |
| …of which `q_lcb` is the binding (`min`) term | **0** | inversion NEVER suppresses score |
| …of which `q_posterior` is the binding term | **3041 (100%)** | score set by `q_live − cost − λ` |
| buy_no rows where `q_posterior` term positive after λ | 780 / 3594 (22%) | real edge IS scored |
| buy_no rows where BOTH terms positive | 560 | tradeable when edge exists |
| buy_no rows that DID score > 0 | 2999-class | e.g. Shanghai/Seoul 28°C/27°C @ ~0.3-0.6¢ ask, score +0.049 |

When `q_lcb` saturates to 1.0, the q_5pct branch `(1.0 − cost − λ)` becomes the MORE permissive
branch, so `min()` correctly falls back to `(q_live − cost − λ)`. The bootstrap robustness is
**not bypassed in the dangerous (false-positive) direction** — `min()` still requires the point
posterior itself to clear cost. A saturated lower bound that loses to the point estimate is a
no-op, not a corruption. The score is sub-cost because the **market is efficiently priced**, not
because normalization stole probability mass.

---

## 3. Math proof that q_posterior and q_5pct ARE same-domain (Case B), and the residual defect

Both terms live in normalized NO-win-probability space:

- `q_live = 1 − π_no_point`, where `π_no_point = p_posterior_yes[bin]`, `Σ p_posterior = 1`.
- `q_lcb = percentile₅( (1 − p_post_yes_boot[bin]) − c_b ) + p_market_no`. Each bootstrap draw
  `p_post_yes_boot` is itself `_compute_posterior(...)` → renormalized to sum=1
  (`market_fusion.py:294-301`), so `1 − p_post_yes_boot[bin]` is a valid NO win-prob in [0,1].

They are therefore ALREADY same-domain in their probability semantics. The defect is NOT a
domain mismatch — it is a **cost-clip asymmetry** in q_lcb's reconstruction: the bootstrap uses
`c_b = clip(·, 0.01, 0.99)` but the restore-to-probability step adds back the **un-clipped**
`p_market_no`. When `p_market_no > 0.99`, `q_lcb = percentile₅(p_post_no − c_b) + p_market_no`
is biased high by `(p_market_no − E[c_b_clipped]) ≥ 0`, producing `q_lcb > q_live` and the 1.0
rail. The invariant `q_5pct ≤ q_posterior` does NOT currently hold, and the minimal correct fix
restores it.

**The fix is to make the add-back consistent with the clip (Case B "consistent-normalize"),
NOT to un-normalize q_posterior (which would be Case A and would fabricate edge — forbidden).**

---

## 4. PRECISE minimal code change

The clamp on `c_b` is correct (keeps the bootstrap edge bounded). The bug is that the
**restore-to-probability add-back** in the adapter uses `cost_by_direction[direction]`
(= un-clipped `p_market`), while the bootstrap subtracted a CLIPPED `c_b`. Restore the SAME
clipped quantity, then clamp the resulting probability to `≤ q_posterior` so the lower bound
can never exceed the point estimate (the algebraic guarantee that the inversion is impossible).

### File: `src/engine/event_reactor_adapter.py`  (`_canonical_probability_and_fdr_proof`)

**BEFORE (≈ lines 2961-2969):**
```python
        cost_by_direction = {
            "buy_yes": float(p_market_yes_vec[index]),
            "buy_no": float(p_market_no_vec[index]),
        }
        for direction in ("buy_yes", "buy_no"):
            hyp = hypothesis_by_label_direction.get((range_label, direction))
            if hyp is not None and hyp.p_value is not None and hyp.ci_lower is not None:
                p_values[(condition_id, direction)] = float(hyp.p_value)
                lcb_by_direction[(condition_id, direction)] = float(hyp.ci_lower) + cost_by_direction[direction]
                prefilter[(condition_id, direction)] = bool(hyp.passed_prefilter)
```

**AFTER:**
```python
        from src.calibration.platt import P_CLAMP_LOW, P_CLAMP_HIGH
        # The bootstrap subtracts c_b = clip(p_market, P_CLAMP_LOW, P_CLAMP_HIGH) from each draw
        # (market_analysis._bootstrap_bin / _bootstrap_bin_no). Restore probability space with
        # the SAME clipped quantity so q_lcb stays in the bootstrap's domain. Adding back the
        # un-clipped p_market over-restores on near-certain bins and pushes q_lcb above the point
        # estimate (the 2026-06-01 inversion). q_point is the normalized posterior used for the
        # q_posterior branch; clamp q_lcb to it so the 5th-percentile LOWER bound can never exceed
        # the point belief — a hard same-domain invariant (q_5pct <= q_posterior, always).
        cost_clipped = {
            "buy_yes": min(max(float(p_market_yes_vec[index]), P_CLAMP_LOW), P_CLAMP_HIGH),
            "buy_no": min(max(float(p_market_no_vec[index]), P_CLAMP_LOW), P_CLAMP_HIGH),
        }
        q_point_by_direction = {
            "buy_yes": yes_posterior,
            "buy_no": 1.0 - yes_posterior,
        }
        for direction in ("buy_yes", "buy_no"):
            hyp = hypothesis_by_label_direction.get((range_label, direction))
            if hyp is not None and hyp.p_value is not None and hyp.ci_lower is not None:
                p_values[(condition_id, direction)] = float(hyp.p_value)
                q_lcb_restored = float(hyp.ci_lower) + cost_clipped[direction]
                # Hard invariant: lower bound <= point estimate (and within [0,1]).
                lcb_by_direction[(condition_id, direction)] = max(
                    0.0, min(q_lcb_restored, q_point_by_direction[direction])
                )
                prefilter[(condition_id, direction)] = bool(hyp.passed_prefilter)
```

Why this is safe in BOTH directions and fabricates NO edge:
- **buy_yes** is symmetric: the same over-restore can push the YES lower bound above the YES
  point; the clamp `min(q_lcb, q_point_yes)` fixes it identically. DB shows 0 buy_yes inversions
  today, but the clamp guards the category for free (the YES rail is just not currently hit).
- **No edge fabricated:** the change only ever LOWERS `q_lcb` (clip ≤ un-clipped add-back; clamp
  ≤ point). A lower q_5pct can only make `min(q_5pct − c − λ, q_post − c − λ)` smaller or equal,
  never larger. Score is monotonically non-increasing in this fix → it cannot manufacture a
  positive score. It only corrects spuriously-high lower bounds.
- **No wrong-side enabled:** the q_posterior branch (`1 − yes_q`) is untouched; buy_no on a
  near-certain-YES bin still scores strongly negative (q_post ≈ 0).
- **buy_yes path stays correct:** `q_posterior=yes_q`, `q_5pct=yes_lcb` unchanged in semantics;
  only the clip/clamp consistency is added.

This is Case (B) executed precisely: renormalize/restore the bootstrap CONSISTENTLY with the
clip, and DO NOT touch q_posterior. Un-normalizing q_posterior (Case A) is explicitly rejected —
the family is MECE-complete, so `1 − yes_q` IS the correct absolute NO win-prob already.

---

## 5. RED relationship-test spec (complements, does not duplicate, test_trade_score_direction_semantics.py)

The existing file tests the adapter→kernel CONTRACT with hand-built triples. This new file must
test the ADAPTER's q_lcb RECONSTRUCTION (clip/clamp consistency), which the existing file does
not exercise. Place at `tests/engine/test_trade_score_lcb_domain_consistency.py`.

Header:
```
# Created: 2026-06-01
# Authority basis: TRADE_SCORE_NORMALIZATION_RULING_2026-06-01.md §3-4 — q_5pct (bootstrap
#   restore) and q_posterior must share the normalized NO-win-prob domain; q_5pct <= q_posterior
#   must hold for BOTH directions (clip/clamp consistency in _canonical_probability_and_fdr_proof).
```

Drive each assertion through a minimal `FullFamilyHypothesis` + the adapter's reconstruction
arithmetic (extract the q_lcb formula into the test, mirroring the existing file's `_score`
pattern), OR — preferred — call `_canonical_probability_and_fdr_proof` with a fake family/analysis
stub whose `_bootstrap_bin_no` returns a known `ci_lower`. Concrete cases:

- **(i) Pre-fix inversion → suppressed score, post-fix correct.**
  Near-certain NO bin: `yes_posterior = 0.002` → `q_post_no = 0.998`; `p_market_no = 0.995`;
  bootstrap `ci_lower_no` such that un-clipped restore gives `q_lcb = 0.998 + (0.995 − E[c_b])`.
  Choose `ci_lower_no = 0.006` so un-clipped `q_lcb = 0.006 + 0.995 = 1.001 → rails to 1.0`
  (the inversion). ASSERT: pre-fix `q_lcb (1.0) > q_post (0.998)`; post-fix
  `q_lcb = min(0.006 + clip(0.995,·,0.99)=0.996, 0.998) = 0.996 ≤ 0.998`. ASSERT post-fix
  `q_5pct ≤ q_posterior`. The SCORE is unchanged here (q_post term still binds) — this proves
  the inversion was cosmetic, AND the fix restores the invariant.

- **(ii) Genuine-edge buy_no scores correctly post-fix.**
  Cheap NO on near-certain bin: `yes_posterior = 0.0001` → `q_post_no = 0.9999`;
  `p_market_no = 0.05`; `ci_lower_no = 0.90` → `q_lcb = 0.90 + clip(0.05)=0.05 = 0.95`,
  `min(0.95, 0.9999)=0.95 ≤ q_post`. With λ=0.01: `min(0.95−0.05−0.01, 0.9999−0.05−0.01) =
  min(0.89, 0.9399)=0.89 > 0`. ASSERT score `> 0` (real edge survives the fix).

- **(iii) Genuinely-no-edge buy_no STILL ≤ 0 (no fabricated edge).**
  Efficiently-priced near-certain NO: `q_post_no = 0.998`, `p_market_no = 0.995`,
  `ci_lower_no = 0.004` → post-fix `q_lcb = min(0.004 + 0.99, 0.998) = 0.994`.
  Score = `min(0.994 − 0.995 − 0.01, 0.998 − 0.995 − 0.01) × p_fill = min(−0.011, −0.007) × pf
  < 0`. ASSERT `score < 0`. (This is the live 99¢-NO honest-no-edge case.)

- **(iv) buy_no on near-certain-YES bin → strongly negative (no wrong-side).**
  `yes_posterior = 0.999` → `q_post_no = 0.001`; `p_market_no = 0.05`; any `ci_lower_no`.
  Post-fix `q_lcb = min(ci_lower_no + clip(0.05), 0.001) ≤ 0.001`. Score
  `≤ (0.001 − 0.05 − 0.01) × pf < 0`, strongly negative. ASSERT `score ≤ −0.5·pf·0.04`.

- **(v) INVARIANT property test:** for a grid of `(yes_posterior ∈ {0.0001..0.9999},
  p_market ∈ {0.01..0.99}, ci_lower ∈ {−0.5..0.5})`, post-fix `q_5pct ≤ q_posterior + 1e-12`
  AND `q_5pct ∈ [0,1]` for BOTH directions. This is the antibody: it makes the inversion
  category unconstructable, and would fail RED on current HEAD (q_lcb hits 1.0 > q_post).

RED proof on current HEAD: case (i) pre-fix and case (v) both fail today (q_lcb=1.0 > q_post in
84.8% of live buy_no). Post-fix both pass.

---

## 6. λ_edge = λ_stress = 0.01 — appropriate, NOT co-defective, but operator-tunable

The λ penalty is doing exactly its job and is independent of the q_lcb bug:

- It is a fixed 1¢ robustness haircut on the edge. On these markets the gross edge on
  near-certain NO bins is ~0.5¢ (q_live − cost ≈ 0.005), so λ legitimately rejects them. This is
  the documented true driver (`test_thin_sub_haircut_edge_is_rejected_by_lambda_edge`,
  2026-05-30). It is correct that a 0.5¢ edge with 95th-pct cost stress is NOT worth taking — the
  bid-ask/fee noise on a 99¢ instrument exceeds the edge.
- The fix in §4 does NOT change which term binds (q_posterior still binds), so λ's role is
  unchanged. λ and the q_lcb bug are orthogonal — fixing one does not co-require the other.
- **Operator lever (separate decision, NOT a defect fix):** if GOAL #36 wants to capture the
  thin-but-real edge on near-certain bins, the lever is NOT to remove λ (that re-admits noise)
  but to **lower the executable-cost / widen the eligible bin set** — i.e., trade bins where the
  NO ask is < 0.97 (genuine edge ≥ 3¢), of which the DB shows real positive-scoring examples
  (Shanghai/Seoul 28°C/27°C @ ~0.3-0.6¢ ask, score +0.049). Those ALREADY pass. The system is
  not starved of buy_no edge; it is starved of buy_no edge ON THE SPECIFIC near-rail bins where
  the market is efficient. Lowering λ to chase those is −EV.

Recommendation: keep λ=0.01. Do not couple it to the q_lcb fix.

---

## 7. Alternative-hypothesis comparison & the true first-fill unlock

The brief's filed alternative (day0 obs-collapse, hard-blocked by `_assert_edli_live_scope`
`main.py:494-501`) is a SEPARATE, currently out-of-scope path: `edli_live_scope` is pinned to
`forecast_only` and day0 triggers raise `DAY0_OUT_OF_SCOPE_FOR_PR332`. That gate is a deliberate
scope lock, not a defect. It is NOT the first-fill unlock for the forecast path under audit.

**True first-fill unlock (ranked by evidence weight):**

1. **Rejection-mix reality (dominant):** `TRADE_SCORE_NON_POSITIVE` is only the 2nd-largest
   reason (3,236). The LARGEST is `MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE` (5,879), and a
   large block is certificate/quote-feasibility failures
   (`executable_snapshot_hash missing` 1,438; `QUOTE_FEASIBILITY_BID_ASK_REQUIRED` 1,142;
   `EXECUTABLE_SNAPSHOT_BLOCKED` 647; `cost_basis_hash missing` 307;
   `bankroll_provider_unavailable` 233; `EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED` 152). **A majority
   of no-trades are PLUMBING (quote/cert/bankroll wiring), not score.** The first fill is far more
   likely gated by the certificate/quote path than by trade-score math.

2. **Score path is HONEST:** where a quote exists and the edge is real (≥3¢ NO ask discount),
   buy_no already scores positive (560 rows both-terms-positive). The system is capturing the
   edge it has; it is not suppressing it.

3. **The q_lcb fix (§4) is correctness hygiene, not an alpha unlock.** It removes a cosmetic
   inversion and installs the `q_5pct ≤ q_posterior` antibody, but it does NOT change any live
   reject→accept verdict (q_posterior binds either way). Ship it for invariant safety, but DO
   NOT expect it to produce the first fill.

**Therefore the operator's next probe should be the certificate/quote-feasibility chain**
(`EDLI_LIVE_CERTIFICATE_BUILD_FAILED:*` = 1438+1142+368+307 ≈ 3,255 rejects) — that is where the
fills are being lost, on candidates that may well have cleared the (honest) score.

---

## Final answer to the deciding question

Polymarket weather temperature families, as constructed in this codebase, ARE a complete MECE
partition (shoulders + interior bins, `Σ p_posterior = 1` enforced 3×). Therefore Case **(B)**.
`q_live = 1 − yes_q` is the correct absolute NO win-probability and must NOT be un-normalized.
The inversion is a benign cost-clip asymmetry in `q_lcb`'s reconstruction that never binds the
score. Fix = clip-consistent restore + `q_5pct ≤ q_posterior` clamp (§4). The first-fill unlock
is the certificate/quote plumbing, not the trade-score normalization.
```
