# CI Honesty & Score-Gate Ruling — 2026-06-01

```
Created: 2026-06-01
Authority basis: GOAL #36 (earn alpha on genuine edge), EDLI v1 robust trade-score,
  bias-correction live-activation (edli_bias_correction_enabled=True, 2026-05-31),
  Fitz Constraint #2 (translation/train-serve loss) + relationship-test doctrine.
Scope: READ-ONLY adversarial architect ruling. No code edits. HEAD 67e3d74673.
Evidence: state/zeus-world.db no_trade_regret_events (live, last hour), source @ HEAD,
  numerical reproduction (§3), config/settings.json.
Supersedes the over-narrow conclusion in TRADE_SCORE_NORMALIZATION_RULING_2026-06-01.md
  ("where real edge exists buy_no already scores positive") for the CONTESTED-bin regime.
```

## VERDICT (1 line)

**INFLATED — root cause (a), a train/serve mean-split.** The q-CI is NOT honest forecast
uncertainty. The POINT posterior is computed from **bias-corrected** ensemble members, but the
EDGE BOOTSTRAP that produces `q_lcb_5pct` resamples the **UNCORRECTED** (raw, cold) members.
The bootstrap is centered ~`|eff_bias_c|` degrees away from the point, injecting a spurious
~15-19¢ CI width that drags the 5th-percentile edge below the flat 1¢ λ and suppresses genuine
+20¢-EV trades. This is a Fitz-class cross-module relationship defect at the
`_snapshot_p_raw` → `MarketAnalysis(member_maxes=...)` boundary. Secondary finding: even after
the CI fix, `robust_trade_score`'s 5pct-minus-flat-λ gate **double-counts variance** that
fractional Kelly already prices — a principled rescope is warranted but is SECONDARY to the CI
fix.

---

## 1. The counterexample is real and the binding term is the ROBUST (5pct) term

Tokyo "highest temp 23°C June 3" buy_no (live):
`q_live=0.9498, q_lcb_5pct=0.7647, c_fee=0.7497, c_95=0.7597, p_fill=0.05, score=-0.00025`.

- Binding term = `q_5pct − c_95 − λ = 0.7647 − 0.7597 − 0.01 = −0.005` → the ROBUST branch binds
  (unlike the near-certain 99¢ bins in the prior ruling, where the point term bound).
- POINT edge = `q_live − c_fee = 0.9498 − 0.7497 = +0.20` (twenty cents).
- 5pct edge = `+0.005` (half a cent), killed by `λ_edge=0.01`.
- This is a CONTESTED bin (NO priced 0.75 with real depth: 157@0.99…137@0.94…5@0.76), not a rail.
- **284 such candidates/hour** (point-edge > 5¢, avg 0.17, max 0.20) rejected at
  TRADE_SCORE_NON_POSITIVE. This is the live alpha leak.

Everything reduces to: is the 19¢ q-CI (q_live=0.95 vs q_lcb=0.76) HONEST or INFLATED?

---

## 2. Empirical CI-vs-spread/lead/bias evidence → INFLATED (artifact)

### 2.1 CI width tracks BIAS MAGNITUDE, not just lead

`model_bias_ens` (VERIFIED, weight_live>0, edli_per_city_v1) vs live contested-bin CI width:

| City | eff_bias_c | unit | avg CI width | n |
|---|---|---|---|---|
| Tokyo | **−3.447** | C (1° bins) | **0.1851** | 225 |
| Wellington | −1.149 | C | 0.0975 | 1 |
| San Francisco | −4.682 | C→F (2° bins) | 0.0588 | 30 |
| Seoul | +1.339 | C | 0.0506 | 12 |
| Warsaw | **−0.229** | C | **0.0434** | 15 |

Tokyo (largest 1°-bin C bias) has the widest CI by 2-4×. Near-zero-bias Warsaw collapses to
~0.04 — the **honest floor**. (SF's large bias is in °C but settles in 2°-wide °F bins, so the
per-bin mass shift is diluted ~2×, explaining its narrower-than-Tokyo CI — consistent with the
mechanism, not a counterexample.)

### 2.2 Lead-widening is real but SUBORDINATE

Contested-bin CI width by target_date (lead):

| target_date | lead | Tokyo+all avg CI | Warsaw-only CI |
|---|---|---|---|
| 2026-05-31 | ~0d | 0.0166 | 0.0439 |
| 2026-06-03 | ~3d | 0.1647 | (n too small) |

There IS an honest lead component (~0.02→0.04 for low-bias Warsaw). But the Tokyo jump to 0.165
at 3-day lead is dominated by the bias-split, not lead: low-bias cities at the same lead stay
near the ~0.04 honest floor. **The excess ~0.13-0.15 is artifact.**

### 2.3 The code path proves the split

- Bias correction is **ON live**: `edli_v1.edli_bias_correction_enabled = True`.
- `event_reactor_adapter.py:3133`: `members = _snapshot_members(snapshot)` — **uncorrected**.
- `:3134`: `_snapshot_p_raw(..., members=members, ...)`. INSIDE that function
  (`:3399` `members, _ = _maybe_apply_edli_bias_correction(members, ...)`), the correction
  returns a **NEW array** (`:3365` `corrected = members − eff_native`; `:3371` `return corrected, True`)
  and **rebinds the LOCAL parameter only**. The point `p_raw`/`p_cal`/`p_posterior` are built
  from corrected (warm) members.
- `:3178`: `MarketAnalysis(member_maxes=members, ...)` receives the **OUTER, UNCORRECTED** array
  (line 3133), and `:3185` `bias_corrected=False`.
- The bootstrap (`market_analysis.py:749/833`) resamples `self._member_maxes`
  (`:230` = `analysis_member_maxes(member_maxes=...)`, whose mean-offset is identity/no-op per
  `forecast_uncertainty.py:113-116,186-190`). So the bootstrap NEVER sees the correction.
- **Forecast-path-specific:** the FORECAST_SNAPSHOT_READY path has `sampler=None`
  (`:3161`) → bootstrap uses raw members. The DAY0 path injects `_static_sampler`
  returning corrected `p_cal` (`:3162-3168`) so it is incidentally protected — but day0 is
  out-of-scope (`main.py:494` pins `forecast_only`). The live scope IS the buggy path.

**Net:** point posterior ≈ corrected (warm) distribution; bootstrap ≈ raw (cold) distribution.
The two are offset by `eff_bias_c`. `q_lcb = percentile₅(NO win-prob from COLD members)`, which
for a cold-biased city sits ~`|eff_bias_c|`° colder → far below the warm point `q_live`.

---

## 3. Numerical reproduction (faithful synthetic, Tokyo HIGH 23°C, eff_bias_c=−3.447)

Cold ENS ~N(24.5°C, 1.6), warm-corrected = raw + 3.447 (≈28°C), settle = floor(x+0.5),
n_mc per p_raw, 500 bootstrap iterations (matching `edge.n_bootstrap=500`):

```
P(23C) POINT  corrected(warm) → q_live_NO ≈ 1.00
BOOTSTRAP(uncorrected/cold)   → NO win-prob mean=0.85, 5pct=0.789  → q_lcb ≈ 0.79
  CI WIDTH from the split     = 0.21   (matches live Tokyo ~0.185-0.21)
BOOTSTRAP(corrected = THE FIX)→ NO win-prob mean=1.00, 5pct=1.00
  CI WIDTH post-fix           = 0.00   (spurious width COLLAPSES)
```

The ~19-21¢ CI width is almost entirely the mean-split, NOT honest dispersion. Fixing the
bootstrap to resample the SAME corrected members the point uses collapses the spurious CI to the
honest floor. (Synthetic params slightly more extreme than live — live q_live=0.95 not 1.0 — but
the mechanism and magnitude match.)

---

## 4. UNIVERSAL FIX

### 4.1 PRIMARY (INFLATED root): make point and bootstrap share ONE corrected member surface

Hoist the bias correction to the single point where `members` is sourced, so BOTH the point path
(`_snapshot_p_raw`) and the bootstrap (`MarketAnalysis.member_maxes`) consume the corrected
array. This restores the train/serve invariant: **the distribution whose 5th percentile we gate
on must be the same distribution whose point we trade on.**

#### File: `src/engine/event_reactor_adapter.py` — `_market_analysis_from_event_snapshot`

**BEFORE (≈ 3132-3134, 3178):**
```python
    bins = list(family.bins)
    members = _snapshot_members(snapshot)
    p_raw = _snapshot_p_raw(snapshot, family=family, bins=bins, members=members, payload=payload)
    ...
        member_maxes=members,
```

**AFTER:**
```python
    bins = list(family.bins)
    raw_members = _snapshot_members(snapshot)
    # Bias correction must flow into BOTH the point posterior (via p_raw) AND the edge
    # bootstrap (via member_maxes). Apply it ONCE here so the 5th-percentile we gate on is
    # a percentile of the SAME (corrected) distribution we trade on. Previously the
    # correction was applied only inside _snapshot_p_raw (local rebind), leaving the
    # bootstrap to resample the uncorrected/cold members — a ~|eff_bias_c|° mean-split that
    # inflated q-CI and suppressed genuine edge on cold-biased cities (Tokyo 2026-06-01).
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"city config missing for event-bound forecast inference: {family.city}")
    members, _bias_corrected = _maybe_apply_edli_bias_correction(
        raw_members, snapshot=snapshot, family=family, city=city, payload=payload
    )
    if _bias_corrected:
        payload["_edli_bias_corrected"] = True
    # _snapshot_p_raw is now passed ALREADY-corrected members; it must NOT correct again.
    p_raw = _snapshot_p_raw(
        snapshot, family=family, bins=bins, members=members, payload=payload,
        members_already_corrected=True,
    )
    ...
        member_maxes=members,
        bias_corrected=_bias_corrected,
```

#### File: same — `_snapshot_p_raw` (≈ 3383-3392): make correction idempotent / guarded

**BEFORE (≈ 3399-3406):**
```python
    members, _bias_corrected = _maybe_apply_edli_bias_correction(
        members, snapshot=snapshot, family=family, city=city, payload=payload
    )
    if _bias_corrected:
        payload["_edli_bias_corrected"] = True
    arr = p_raw_vector_from_maxes(members, city, semantics, bins)
```

**AFTER:**
```python
    # When the caller already applied the correction (the canonical event path), do NOT
    # double-correct. members_already_corrected defaults False so any other caller keeps
    # the legacy behavior (correction applied here, exactly once).
    if not members_already_corrected:
        members, _bias_corrected = _maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload=payload
        )
        if _bias_corrected:
            payload["_edli_bias_corrected"] = True
    arr = p_raw_vector_from_maxes(members, city, semantics, bins)
```
(Add `members_already_corrected: bool = False` to the `_snapshot_p_raw` signature.)

**Why safe / no double-count / no false confidence:**
- The `_edli_bias_corrected` payload flag still drives the identity-Platt lockstep in
  `_snapshot_p_cal` (`:3413-3418`) — unchanged, because the flag is still set exactly once.
- The bootstrap now resamples corrected members → its 5th percentile is a true lower bound of
  the SAME belief we trade on. It NARROWS the CI only by removing the artifact; it does NOT widen
  it, so it cannot manufacture confidence. Genuinely-uncertain bins (wide ensemble spread, long
  lead) keep their honest CI (Warsaw-floor behavior preserved).
- `bias_corrected=_bias_corrected` is now passed truthfully to MarketAnalysis (was hardcoded
  False), keeping the sigma/mean seam internally consistent for future lead-continuous work.

This is the Fitz "make the category impossible" fix: a single corrected member surface means
point and bootstrap can NEVER diverge again.

### 4.2 SECONDARY (variance double-count): rescope the admission gate to EV, let Kelly carry variance

Independent of the CI bug, the gate design itself double-counts variance:

- `robust_trade_score` admits only if `q_5pct − cost − λ > 0` (5th-percentile edge clears a flat
  1¢ penalty) — a variance penalty at the ADMISSION gate.
- Downstream, `kelly_size` (`kelly.py:62`) sizes `f* = (p_posterior − price)/(1 − price)` then
  multiplies by `kelly_mult` (fractional, ≤0.25) AND the **dynamic multiplier that already
  reduces for wide CI and long lead** (`kelly.py:7-12`). Variance is priced TWICE: once as a
  hard admission threshold, once as continuous sizing shrinkage.

A +20¢-EV / +0.5¢-5pct trade that fractional Kelly would size TINY (because f*·0.25·CI-haircut
is small) is plausibly a trade we SHOULD take in small size — GOAL #36 is to earn alpha on
genuine edge, and Kelly is the correct instrument for "real edge, high variance → small size",
NOT a binary reject. The flat-λ 5pct gate converts a sizing decision into an admission decision
and silently drops positive-EV alpha.

**Recommended rescope (principled, keeps false-confidence out):**
- Gate on **point EV > 0 with a confidence floor**, not on 5pct-edge > flat-λ. Concretely:
  admit if `(q_posterior − cost − λ_edge) > 0` AND `p_value < α_FDR` (the bootstrap p-value /
  BH-FDR ALREADY proves the edge is statistically real — that is the false-confidence guard),
  then let fractional Kelly + the dynamic CI/lead multiplier carry ALL the variance penalty into
  SIZE. Keep `q_5pct` as a SIZING input to the Kelly dynamic multiplier (wider CI → smaller
  size), not as a binary admission threshold.
- If a hard robustness floor is still wanted, replace flat λ on the 5pct edge with a
  **variance-proportional** floor (e.g. require `q_posterior − cost > k·(q_posterior − q_5pct)`),
  which scales with actual CI width instead of a flat 1¢ — so a tight-CI 20¢ edge is admitted and
  a wide-CI 20¢ edge is admitted only if the point margin dominates the spread.

**Sequencing:** Do §4.1 FIRST. The CI bug corrupts BOTH the gate AND the Kelly dynamic
multiplier (which reads CI width), so any gate-rescope on top of an inflated CI would mis-size.
After §4.1, re-measure: many of the 284/hour contested rejects will clear once the CI collapses
to its honest floor (Tokyo 5pct-edge ≈ +0.005 → after fix, q_lcb rises toward q_live, 5pct-edge
becomes solidly positive). §4.2 is the residual-alpha capture for genuinely-wide-but-real-edge
bins and a separate operator decision.

### 4.3 λ_edge disposition

**Keep λ_edge = 0.01 IF §4.2 rescope is adopted** (it becomes a small EV cushion on the point
margin, not a variance double-count). **If §4.2 is deferred**, λ_edge is currently mis-placed (it
penalizes the already-variance-discounted 5pct edge) — but do NOT remove it standalone, because
on an inflated CI it is the only thing stopping noise admission. Net: λ_edge is not independently
defective; its appropriateness is contingent on the gate's variance-accounting being fixed
(§4.2). Resolve λ and §4.2 together, after §4.1.

---

## 5. RED relationship test (complements test_trade_score_direction_semantics.py)

New file `tests/engine/test_bootstrap_bias_correction_lockstep.py`. This asserts a CROSS-MODULE
invariant the existing direction-semantics file does NOT cover: **the edge bootstrap and the
point posterior must consume the SAME (bias-corrected) member surface.**

Header:
```
# Created: 2026-06-01
# Authority basis: CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md §2-4 — the q-CI 5th
#   percentile must be a percentile of the SAME bias-corrected belief whose point is traded.
#   Guards the _snapshot_p_raw -> MarketAnalysis(member_maxes=...) train/serve boundary.
```

Drive through `_market_analysis_from_event_snapshot` with a fake snapshot/family + a VERIFIED
bias row (eff_bias_c=−3.447, weight_live=1.0) for a cold-biased city, `edli_bias_correction_enabled=True`.

- **(i) Pre-fix RED — mean-split present.** Build the analysis, then assert
  `analysis.member_maxes.mean()` differs from `mean(corrected_members)` by ≈ `|eff_bias_c|`
  (i.e. the bootstrap members are UNcorrected on current HEAD). Concrete: raw members mean ≈ 24.5,
  corrected ≈ 27.95; assert `abs(analysis.member_maxes.mean() − 24.5) < 0.1` (RED today),
  and the FIX flips it to `abs(analysis.member_maxes.mean() − 27.95) < 0.1`.
- **(ii) Post-fix — CI collapses on a genuine-edge cold-biased bin.** With corrected members,
  run `scan_full_hypothesis_family`; for the contested NO bin assert
  `(q_posterior − q_5pct) < 0.05` (honest floor) where pre-fix it was `> 0.15`. Assert the
  resulting `robust_trade_score > 0` for a +20¢-point-edge / cost≈0.75 candidate
  (the Tokyo case is admitted post-fix).
- **(iii) No false confidence — genuinely-uncertain bin STILL declines / sizes tiny.** Construct
  a bin with WIDE ensemble spread (members N(μ, 4.0), short of the bin) but NO bias
  (eff_bias_c≈0): assert post-fix `(q_posterior − q_5pct) > 0.10` (CI stays honestly wide) AND
  `robust_trade_score ≤ 0` OR (under §4.2) Kelly size < a small ε. The fix must NOT collapse
  honest uncertainty — only the bias-split artifact.
- **(iv) No double-correction.** Assert `_snapshot_p_raw` is NOT applying the correction a second
  time when called from the patched caller: `analysis.member_maxes.mean()` equals
  `mean(corrected)` exactly (not `corrected − eff_native` twice). RED if a refactor re-introduces
  double correction.
- **(v) INVARIANT property test (antibody).** Over a grid of `(eff_bias_c ∈ {−5..+2},
  ensemble_spread ∈ {0.5..4.0}, lead ∈ {0..3})`: assert `mean(analysis.member_maxes)` equals the
  POINT member mean (corrected) to < 1e-6 for ALL cells — making the train/serve split
  structurally unconstructable. Fails RED on current HEAD wherever `eff_bias_c ≠ 0`.

RED proof on HEAD: (i) and (v) fail today (bootstrap members uncorrected whenever a VERIFIED bias
row exists). Post-§4.1 all pass. (iii) is the false-confidence guard and must pass BOTH pre and
post (honest wide CI is preserved).

---

## 6. Twelve-line decision summary

1. VERDICT: INFLATED — the q-CI is an artifact, not honest forecast uncertainty.
2. Root cause (a): point posterior uses BIAS-CORRECTED members; edge bootstrap resamples
   UNCORRECTED members (`event_reactor_adapter.py:3133-3134,3178` — local rebind drops the fix).
3. The bias correction is ON live (`edli_bias_correction_enabled=True`); bug is forecast-path
   (sampler=None); day0 incidentally protected but out-of-scope.
4. Evidence: CI width tracks BIAS magnitude (Tokyo −3.447°C → 0.185; Warsaw −0.23°C → 0.043),
   not just lead; numerical repro reproduces the 0.21 split and collapses it to 0.00 when the
   bootstrap uses corrected members.
5. NOT (b/c/d): n_bootstrap=500 and MC noise are not the driver — the split is a mean offset, not
   variance inflation; ruling out honest-uncertainty case (1).
6. PRIMARY FIX: hoist `_maybe_apply_edli_bias_correction` into
   `_market_analysis_from_event_snapshot` so point AND bootstrap share one corrected member
   surface; pass `members_already_corrected=True` to `_snapshot_p_raw` (no double-correct);
   pass `bias_corrected=_bias_corrected` truthfully.
7. Fix narrows CI only by removing the artifact — cannot widen → no manufactured confidence;
   honest-wide bins preserved.
8. SECONDARY: the 5pct-minus-flat-λ admission gate double-counts variance that fractional Kelly
   (f*·≤0.25·dynamic CI/lead multiplier, `kelly.py:7-12,62`) already prices.
9. Recommended rescope: admit on point-EV>0 + FDR p_value<α (the real false-confidence guard),
   let Kelly carry variance into SIZE; keep q_5pct as a SIZING input, not a binary gate. Optional
   variance-proportional floor instead of flat λ.
10. Sequencing: §4.1 FIRST (inflated CI corrupts both gate and Kelly multiplier); re-measure —
    many of the 284/hr contested rejects clear once CI collapses to floor.
11. λ_edge: not independently defective; resolve with §4.2 after §4.1. Do not remove standalone.
12. RED test: assert bootstrap member-mean == corrected point mean (RED today wherever
    eff_bias_c≠0), CI collapses on genuine-edge bins, honest-wide bins still decline (no false
    confidence), no double-correction; property-test antibody makes the split unconstructable.
```
