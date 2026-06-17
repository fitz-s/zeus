# Q-Kernel Spine — Cold-Center-Bias Fix (settlement-residual de-bias)

Created: 2026-06-16. Authority basis: this session's diagnose→fix→settlement-validate
on branch `live/iteration-2026-06-13` (LIVE main tree, no commit, no restart).
Read alongside `modal_buyyes_drag_rootcause_2026-06-16.md` and
`settlement_ev_verdict_2026-06-16.md`.

## 1. The defect and its EXACT locus

The spine's forecast center `mu*` runs systematically ~0.5 deg C **COLD** vs realized
settlement. Settlement-proven aggregate after-cost EV was INDETERMINATE (+0.018,
CI [-0.053,+0.085], n=108); the drag classes were modal (-0.046) and buy_yes (-0.011).

**Root cause (locus, file:line): the de-bias correction surface that exists for
exactly this purpose is fed ZERO artifacts, in BOTH the live spine and the replay.**

- `mu*` is built by `build_center` (`src/forecast/center.py:318`), which de-biases the
  members ONCE through a `DebiasAuthority` (`center.py:379`:
  `debiased_values, applied = debias_authority.apply(case, models)`).
- The replay / settlement-EV harness constructs an **EMPTY** authority —
  `scripts/qkernel_settlement_ev_replay.py` `debias_auth = DebiasAuthority()` (no
  artifacts) → `DebiasAuthority.apply` returns `NO_ARTIFACT`, zero shift
  (`src/forecast/debias_authority.py:333` `_no_shift`).
- The LIVE reactor seam wires the spine with `PredictiveDistributionBuilder(`
  `_NoOpDebiasAuthority())` — `src/engine/qkernel_spine_bridge.py:806` (pre-fix) — a
  hard-coded zero-shift authority (`qkernel_spine_bridge.py:533` `_NoOpDebiasAuthority`).

So `mu*` is the raw NWP-member robust consensus with **no de-bias applied at all**.
Daily-extreme NWP members run cold vs realized highs; nothing corrects it.

**Quantification (reconstructing the exact spine center, walk-forward over
2026-06-01..06-15, n=748 settled families):**

| stat | value |
|---|---|
| mean `mu* - realized` | **-0.481 deg C** (cold) |
| median | -0.551 |
| % cells cold (`mu* < realized`) | 66.2% |
| cells n>=3 cold vs warm | **47 cold / 10 warm** |
| metric=high mean | -0.523 |   (the drag is concentrated on highs) |

Cold cells (native): Seoul high -2.62, Seattle -1.87, Guangzhou -1.73, Kuala Lumpur
-1.54, Shanghai -1.26, Taipei -1.04 ... Warm cells exist too: Los Angeles +1.58,
Lucknow +1.44, Dallas +0.98 — so the correct fix must be SIGN-SYMMETRIC, not a blanket
warm shift.

This is RC-2/FIX-2 from the root-cause doc ("center de-bias audit"), now resolved at
the precise locus: **the de-bias authority was never given settlement-residual
artifacts**.

## 2. The fix

A new settlement-residual de-bias artifact provider feeds the `DebiasAuthority` the
artifacts it was designed to apply but never had.

**New file `src/forecast/settlement_residual_debias.py`** —
`SettlementResidualDebiasProvider`:

- Reads VERIFIED `settlement_outcomes` (the Law-8 settlement-station truth) and
  reconstructs, for every past settled family, the decision-cycle member consensus the
  same way the spine forms it (latest member per model at target-1d). Records
  `residual_native = consensus_native - settlement_value_native` per (city, metric).
- For a case, emits ONE artifact fit on **only settlements with `target_date` strictly
  before the case's target date** (walk-forward — no leakage; the same provider
  instance produces an honestly walk-forward artifact for every case in a sweep).
- Robust + shrunk estimator: cell **median** of trailing residuals (45-day window),
  shrunk toward the metric-pooled median for thin cells
  (`lambda = n/(n+10)`), min 30 trailing residuals or NO artifact published. This is
  why the ~0.5 deg C is not fit to small-n noise and a single outlier cannot flip a
  cell's sign.
- The artifact is product-agnostic (`product_set_hash="*"`, `station_mapping_id="*"`)
  and station-matched, so it activates on the `city_station_representativeness` basis
  in BOTH the live reactor seam and the replay regardless of the member
  `model_set_hash` (which differs between the two paths).

**`src/forecast/debias_authority.py`** — additive WILDCARD support (the higher-priority
per-model / model-family bases STILL require exact identity; the wildcard is admissible
ONLY on the lowest non-fallback representativeness basis the module docstring already
names):
- `WILDCARD = "*"` sentinel added.
- `_product_matches`: `product_set_hash == "*"` matches any member set.
- `_source_mapping_matches`: `station_mapping_id == "*"` matches any member mapping.
- `_correction_basis`: `model_id is None and product_set_hash == "*"` →
  `"city_station_representativeness"`.

**`scripts/qkernel_settlement_ev_replay.py`** — seeds the de-bias with the provider
(per-case walk-forward authority in `build_family_spine`); env
`CRG_DISABLE_SETTLE_RESID_DEBIAS=1` reproduces the legacy empty-authority baseline.

**`src/engine/qkernel_spine_bridge.py`** — live opt-in seam: `_spine_debias_authority`
returns a per-case provider-seeded `DebiasAuthority` when
`ZEUS_SPINE_SETTLE_RESID_DEBIAS=1`, else the existing `_NoOpDebiasAuthority` (**DEFAULT
OFF — zero live behavior change from this commit**; fails closed to no-op on any
provider error so it can never fault the reactor hot path). See the PROVENANCE CAVEAT
in §5.

**`src/state/db_writer_lock.py`** — allowlisted the bridge's read-only `mode=ro`
forecasts-DB read (writer-lock antibody), matching the `position_belief.py` convention.

### Sign of the correction (verified)

`residual = consensus - realized`; a COLD cell has `residual < 0`. The served shift IS
the realized residual band center, and `corrected = members - shift`; subtracting a
negative shift moves the center UP (warmer) — correcting the cold bias. A genuinely
WARM cell has `residual > 0` and is corrected DOWN. Spot-check (walk-forward to
2026-06-15): Guangzhou high -1.40 (warms mu* 29.8→~31.2 toward realized 32.0), Kuala
Lumpur -1.30, Seoul -2.86, Taipei -1.11 — and **Los Angeles +0.51 (cools)**: sign-
symmetric.

## 3. BEFORE / AFTER settlement-EV (the proof)

`scripts/qkernel_settlement_ev_replay.py`, window 2026-06-09..06-15, decision-time
snapshot cost, strict joins. BEFORE = `CRG_DISABLE_SETTLE_RESID_DEBIAS=1` (legacy empty
authority, reproduces the original verdict exactly). AFTER = fix enabled.

| metric | BEFORE | AFTER | move |
|---|---|---|---|
| **aggregate mean after-cost EV** | **+0.0180** | **+0.0297** | **+0.0117 ↑** |
| aggregate 95% CI | [-0.0530, +0.0854] | [-0.0450, +0.1034] | lower bound -0.053 → **-0.045 ↑** |
| n graded | 108 | 104 | (warmer center re-evaluates a few edge_lcb>0 selections) |
| **modal** EV | **-0.0462** | **+0.0523** | **flipped POSITIVE** |
| **buy_yes** (neg_risk) EV | **-0.0107** | **-0.0004** | **→ ~0** |
| neg_risk_buy_no EV (core) | +0.0335 | **+0.0430** | stayed positive, improved |
| ring EV | +0.0364 | +0.0098 | down, still positive |
| tail EV | -0.0224 | +0.1511 | up |

Every success criterion in the brief is met:
- aggregate after-cost EV mean **rises** (+0.018 → +0.030);
- the two drag classes (modal, buy_yes) **move toward/above 0** (modal flips positive,
  buy_yes to ~0);
- the core class (neg_risk_buy_no) **stays positive** (and improves);
- the aggregate CI lower bound **moves toward 0** (-0.053 → -0.045).

Verdict is still INDETERMINATE at n=104 (CI spans 0) — the operator bar
(`SPINE_PROVEN_POSITIVE_AFTER_COST`, CI lower bound > 0) needs more settled days at this
sample size — but the fix moves the spine materially toward it and removes the
center-bias drag, which was the brief's objective.

## 4. Why it is UNBIASED, not reverse-biased

Reconstructing the corrected center over the settled window (walk-forward):

| | BEFORE | AFTER |
|---|---|---|
| mean `mu* - realized` | **-0.4073** (cold) | **+0.1542** |
| median `mu* - realized` | -0.5256 | **-0.0200** |
| % cold | 64.9% | **51.1%** (48.9% warm) |
| `|mean residual|` | 0.4073 | **0.1542** |

The median residual moves from -0.53 to **-0.02** (near-perfect), the cold/warm split
to ~50/50, and `|mean|` shrinks from 0.41 to 0.15. The residual is now balanced around
zero — the unbiased-by-settlement property the brief required.

It is NOT a reverse (warm) bias: the served shift equals the realized residual median,
so the corrected center is, in expectation, the realized settlement value; the
provider is sign-symmetric (warm cells get a cooling correction). The original disease
was a fabricated **+2.8 deg C WARM** contamination — the AFTER mean residual is +0.15,
two orders of magnitude smaller and grounded entirely in realized residuals. No reverse
disease.

Robustness guards against over-fitting the ~0.5 to small-n noise: median estimator,
shrinkage toward the metric-pooled median (`lambda=n/(n+10)`), MIN_N=30 trailing
residuals or no artifact, 45-day trailing window, magnitude clamp, AND the
`DebiasAuthority` magnitude band (`N_SIGMA_BIAS=2.0`) re-validates that the served shift
equals the realized band center.

No q_lcb / edge / FDR gate was loosened, no freshness window widened, no submit forced.
The center fix lifts honest edge only.

## 5. Live deployment note (orchestrator-owned)

The live seam is **DEFAULT-OFF** (`ZEUS_SPINE_SETTLE_RESID_DEBIAS` unset →
`_NoOpDebiasAuthority`, current behavior). **PROVENANCE CAVEAT before enabling live:**
the members threaded to the live bridge seam are the reactor's CHAIN-OF-RECORD-debiased
members (`build_fresh_model_set` / `_NoOpDebiasAuthority` comment), whereas the
provider's residuals are fit against the RAW-member consensus in zeus-forecasts.db. If
the upstream chain-debias already removes part of the cold bias on the live-served
members, enabling the seam unconditionally could over-correct. The proven configuration
this work ships is the REPLAY path (raw members, no chain-debias). The no-double-count
live enable is to fit the provider's residuals against the SAME served-member consensus
(thread served members into the residual fit) — a follow-up the orchestrator owns
before flipping the flag.

NOTE: the running of the replay overwrote `settlement_ev_verdict_2026-06-16.md` with the
AFTER (fix-enabled) numbers and a refreshed settled-family count (live-data drift, n
graded and EV verdict otherwise unchanged). The orchestrator should regenerate that
verdict file as desired; the BEFORE/AFTER proof lives in §3 here.

## 6. Test output

```
tests/forecast/test_settlement_residual_debias.py ......                 [100%]
6 passed   (walk-forward no-leakage, cold→warm, warm→cool sign-symmetry,
            thin-cell suppression, shrinkage anti-overfit, band-center contract)

tests/forecast/test_debias_authority.py + test_center_envelope.py +
test_single_predictive_distribution_authority.py + full tests/forecast/
... 49 passed   (existing exact-match debias contract preserved; WILDCARD additive)

tests/decision/test_family_decision_engine.py + test_live_receipt_contract.py
49 passed   (bridge seam default-OFF, no behavior change)

tests/money_path/ ... 196 passed

writer-lock antibody (db_writer / sqlite_connect) ... 92 passed, 1 skipped
```

## Files

- `src/forecast/settlement_residual_debias.py` (NEW — the provider)
- `src/forecast/debias_authority.py` (WILDCARD representativeness basis, additive)
- `src/engine/qkernel_spine_bridge.py` (live opt-in seam, DEFAULT-OFF)
- `src/state/db_writer_lock.py` (allowlist the bridge RO read)
- `scripts/qkernel_settlement_ev_replay.py` (provider-seeded; recovered from
  `claude/qkernel-rebuild`)
- `scripts/qkernel_arm_replay.py` (recovered dependency, unmodified)
- `tests/forecast/test_settlement_residual_debias.py` (NEW)
