# Per-city EB-shrunk (k,w) calibration — explored, NO-GO (thin-data wall), deferred

**Date:** 2026-06-29
**Money-path stage:** forecast signal → **calibration** (the served settlement-bin q).
**Authority basis:** operator — "根据最新结构探索最新的emos方向" + "利用数据" (use the data) + "consult review".
Hierarchical partial-pooling = the 2025 EMOS frontier, instantiated on Zeus's measured per-city signal.

## FINAL VERDICT (2026-06-29, post-consult + cross-split re-test) — NO-GO, deferred
Per-city is **validated as a real signal but NOT safely shippable on 19 days.** Blanket per-city gains
**+4.92 mean OOS NLL** but **persistently harms ~20 cities** (Madrid −7.65 / negative 6-of-6 splits,
Singapore −6.32 / 6-of-6, Chongqing −5.90, plus Cape Town, Busan, Ankara, Amsterdam, Paris, London, Wuhan
all 6/6) — **signal, not noise**, and an adverse-selection risk (over-confident local q gets traded more,
per [[adverse-selection-overconfidence-thin-data-limit]]). The honest no-look-ahead gate is too noisy on
this window to separate winners (only **3/38** pass strict cross-fitted persistence; captures **+1.23**,
loses the +4.92). The look-ahead test-persistence gate that WOULD remove the losers is unusable live. Same
thin-data wall as the 91-bet city-skill work. A frontier consult (ChatGPT Pro Extended) independently
returned **NO-GO** + named valid robustness blockers: F-family `w` is served C-only (so F per-city `w` is
written-but-unserved), day0 extrapolation (per-city applies to <24h targets with zero settled evidence),
no κ-baseline guard in `_select_kappa_cv` (a future no-signal refit could ship a harmful layer), provenance
hash excludes the city layer, and malformed-`cities` fail-soft to inert instead of family global.
**DECISION:** live artifact **stripped back to global-only** (proven state); defer per-city; re-validate
~4–6 weeks as settled depth grows (the internal gate becomes reliable + the safe gain grows). The fitter
per-city writer + materializer reader remain in the tree, **undeployed**. The "+5.04 realized" framing in
the sections BELOW is the earlier over-claimed read — **superseded by this block.**

## What the data said (executed, not asserted)
1. The served calibration is already a **settlement-MLE-fit `Normal(σ·k) ⊕ Uniform(w)` mixture**
   (`scripts/fit_sigma_scale.py`, operator law 2026-06-12: no hand-set value). Re-fitting on the current
   973/1162-cell settled corpus gives **k=0.698, w=0.146 ≈ the live artifact (0.671, 0.149)** — i.e. it
   is **NOT stale; it is at the global settlement-MLE optimum.** The earlier "σ over-dispersed by 2.2×"
   read used 50%-interval coverage — the wrong metric; the market settles on *which bin wins*, and the
   MLE already optimizes that. (The σ-basis "double-count fix" was therefore reverted — `k` absorbs the
   basis, so the swap is neutral at best and *breaks* the k-calibration if shipped without re-fitting.)
2. Per-**lead** regime: no headroom (both buckets fit identical k=0.698; OOS −0.53, overfits).
3. Per-**climate**/per-**city** UNPOOLED: real signal (k/w genuinely differ — hi-var k=0.724/w=0.189 vs
   lo-var k=0.617/w=0.114) but **overfits OOS** (−4.07) on 19 days of data.
4. Per-**city PARTIALLY POOLED** (EB shrink each city's MLE toward the global pair by λ=n/(n+κ)):
   **beats global out-of-sample on all 6 rolling date-splits, mean +5.04 NLL** (range +3.7…+6.4). The
   signal is real *and* generalizes once shrunk. κ CV-selected = 30.

So the lever was not "more data / wait" and not a re-architecture — it was **using the per-city signal
already in the data, via shrinkage** (the hierarchical frontier).

## Realization (additive, backward-compatible)
- `scripts/fit_sigma_scale.py`: after the global per-unit fit, emits a per-city `cities` layer —
  `_fit_cities_shrunk` (each city's MLE shrunk toward the family pair) with `_select_kappa_cv`
  (rolling-OOS κ, so κ is math-supported, not chosen). 38 C-cities written; κ=30.
- `src/data/replacement_forecast_materializer.py`: `_replacement_sigma_scale_lookup(unit, city=…)` serves
  the per-city `(k,w)` when present, else the global pair; threaded through `_effective_unit_sigma_scale`
  and the q apply-site (`city=request.city`). **An artifact with no `cities` key is byte-identical** to
  prior behavior — so the running (old-code) daemon ignores it.
- Tests: `tests/data/test_per_city_sigma_scale.py` (4/4) — per-city served, absent-city → global,
  no-city → global, no-cities-key → byte-identical. No regression (the 11 failures in
  `test_replacement_sigma_scale_k_c.py` are PRE-EXISTING test↔code path drift — fail without this change
  too; stash-confirmed).

## Hot-city correction (the case naive recalibration hurt)
The global k=0.70 *over-sharpens* hot/tropical cities. Per-city fixes exactly them: **Taipei k=0.913**
(raw 1.108), Hong Kong 0.779, Singapore 0.826, Moscow 0.817 — vs Tokyo 0.649, Lucknow 0.646. Shrinkage
gives each city its own peakedness/tail without the thin-data blow-up that sank unpooled per-city.

## Status / activation
- Live artifact `state/sigma_scale_fit.json` rewritten with the per-city layer (global refreshed
  0.671→0.6977, within CI — the sanctioned weekly refresh). Running daemon reads only the global (its
  deployed materializer has no per-city reader) → **per-city is inert until the materializer code
  deploys** (commit + daemon restart). That code deploy is the money-path activation gate.
- Validation metric = bin-win Bernoulli NLL (the served-q objective), out-of-sample, walk-forward — the
  same proper score the fitter optimizes, not a coverage proxy.

## Deferred / next
- Re-validate κ and the +5.04 magnitude as settled depth grows (19-day window caps it).
- CWA/HKO station sources (加数据) remain the orthogonal lever for the hot-city *bias* (per-city shrinkage
  fixes peakedness/width, not a missing-source center bias) once settled overlap accrues.
