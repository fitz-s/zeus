# Critic Synthesis — multi-angle adversarial validation of the redesign plan

- Created: 2026-05-29
- Author: session (Opus, max effort)
- Inputs: 3 opus critics (CRITIC_statistical.md, CRITIC_asymmetry.md, CRITIC_consistency.md) + Phase-0 scoper (E_phase0_fixture_scope.md), each grounded against live code/DB + my verification overlay.
- Naming: canonical table is **`ensemble_snapshots`** (B3 rename complete; `v2_schema.py:108`). Prior docs A/B/C + plan docs use the stale `ensemble_snapshots_v2` name (the live DB lags the rename) — to be swept.

---

## 0. Verdict: REVISE (3/3 critics). Spine survives; the correction-adoption machinery does not.

KEEP: ForecastObject/SettlementObject contract; raw-identity default; analytic p_raw (with a stronger equivalence gate); SQL backfill; the phase skeleton.
RESTRUCTURE/BUILD: the bias estimator's shrinkage target; the OOS gate (absent today); the contract's completeness.

The redesign is fixable, not a teardown. But its path to ever *serving a correction* is much harder than Draft 2 specced, and three confirmed SEV-1 mechanisms **compound** into a re-armed sd3 if shipped as written.

---

## 1. The compounding failure (the key insight)

The three SEV-1 clusters are not independent — they multiply:

```
poisoned candidate (shrinks to a harmful TIGGE prior)
  ×  broken gate (the thing meant to reject it is absent + unadjusted + leaky)
  ×  leaky serving/contract (silent fail-open to raw, Platt invalidated)
  =  sd3 renamed
```

Fixing any one alone is insufficient. The plan's **raw-identity default is the only thing standing between the system and re-armed sd3** — vindicated by every critic, but it must be the *enforced* state until the whole chain is built and validated end-to-end.

---

## 2. Confirmed SEV-1s (grounded — with verification overlay)

### 2a. Shrinkage target is wrong → REPLACE, don't extend  [CRITIC_asymmetry, VERIFIED]
`ens_bias_model.py` posterior = `w·ē + (1−w)·(μ_T+δ_g)`; at thin/zero live data `bias=prior_mean` (the TIGGE prior). Verified `:139`. TIGGE→OpenData transfer *hurts* 7/11 buckets (Jeddah raw 2.05→corrected 9.06). At n=12-18 (every current OpenData bucket) the emitted candidate is heavily/fully the harmful prior. 6h-TIGGE and 3h-OpenData are **different random variables that must not share a parent**; the `paired_delta` defense needs ≥5 same-window pairs per fine bucket — which vanish under 28× re-keying.

→ **REPLACE the shrinkage target.** Segregate per product (no shared TIGGE parent for OpenData serving); TIGGE demoted to a *transfer-only candidate that must independently win OpenData OOS*; raw identity is the permanent near-term default for ~10/11 buckets. **This supersedes Draft-2 §3a "extend the EB hierarchy"** — extending a TIGGE-rooted estimator inherits the poison. My own "leaf = raw" framing was insufficient: the *estimator emits* a poisoned value; only a working gate (2b) + a corrected target stops it.

### 2b. The OOS gate is absent + statistically invalid → BUILD, don't compose  [CRITIC_statistical, VERIFIED]
- **S2 (absent):** `improvement_lcb` is an injected parameter to `choose_candidate` (`score_error_model_candidates.py:67`), consumed never computed; the only OOS gate `blocked_oos.py:115` runs `n_bootstrap=0`. **Corrects my "~20 LOC / ~80%-built" claim — improvement-mode is substantially unbuilt.**
- **S1 (no multiple-comparisons control):** ~28 buckets × 6 candidates × 3 scores ≈ 168 `LCB>0` tests, no FDR → ~8 spurious adopts by chance. Predecessor-solved: Zeus already has BH-FDR (`decision_evidence.py:37-55,163-188`; `execution_intent.py:1346`) — reuse the same family for candidate selection. VERIFIED exists.
- **S3 (IID violated):** CIs assume independence; data are daily-autocorrelated and the re-key makes one settlement day recur across lead/cycle groups → inflated n_eff → `ens_bias_model` live weight overstated → posterior over-trusts noise. Fix: settlement-day as the unit, n_eff via AR(1), block bootstrap.
- **S4 (fold leakage):** `audit_refit_proper_scores.py:376` `i % n_folds`, not date-blocked → same target_date splits train/test → inflates the OOS scores feeding the gate.

→ **BUILD the gate in order:** S4 date-block folds → S2 real block-bootstrap LCB with S3 n_eff → S1 wrap in BH-FDR family → S5 single primary proper score (the "2-of-3" rule is anticonservative on correlated scores) → **coverage-validate by simulation at n=12-18 before any adoption is enabled.**

### 2c. Contract not airtight → close it  [CRITIC_consistency, mixed-verified]
- **Cons-D (Platt invalidated) — reasoning sound:** Platt was trained on 10k-MC p_raw; retiring MC for analytic p_raw while the equivalence gate scores **p_raw only** lets p_cal silently regress (logit is tail-sensitive; post-rounding analytic CDF is a staircase). Fix: gate equivalence on **p_cal + logit(p_raw)**, not p_raw alone; refit Platt or keep MC until p_cal equivalence holds.
- **Cons-SEV-3-H (table name) — CORRECT; I wrongly rejected it:** canonical table is `ensemble_snapshots`; my plan/verification docs use the stale `_v2`. Sweep the naming. (Root cause of my error: queried the stale live DB, not the code DDL.)
- **Cons-B (bypass writers) — PARTIAL:** `backfill_ens.py:132` has its own `INSERT` — a writer outside the contract chokepoint (category valid) — but it targets the (now-canonical) `ensemble_snapshots`; live-risk low, `--apply`-gated. Fix: inventory ALL writers; one funnel or explicit quarantine + CI grep.
- **Cons-A (third/fourth chokepoint) — CONFIRMED (I wrongly dismissed it; see §4):** the serving-side FT bias resolution is a byte-twin PAIR the plan never named: `_resolve_ft_error_model_for_entry` (`src/engine/evaluator.py:3296`, entry path, called :4112) + `_resolve_ft_error_model` (`src/engine/monitor_refresh.py:343`, monitor path, called :559/:604). They are inlined duplicates (circular-import; divergence warned at evaluator.py:43-45/:3309 but UNENFORCED). Both are lead-blind. FT is dormant now (`error_model_family` NULL), so these are the seam that WOULD serve a promoted correction. **Fix: the contract has FOUR seams (writer, reader, + these two twins); after the lead re-key both twins must thread `forecast_lead_bucket` and RAISE on miss (fail-closed, not fail-open to raw); add a shared-helper or CI-grep antibody for the twin-divergence.**

---

## 3. Phase-0 reality — sharpens everything (VERIFIED, n is brutal)

- OpenData mx2t3 ingest began ~2026-05-06 → validation window is **~23 days**.
- **n ≈ 801 independent settled HIGH outcomes** (50 cities, ~16/city; 6,167 multi-lead rows — do NOT read power off 6,167). Re-keyed by product×cycle×lead → near-empty cells.
- **No FT branch is live** (`error_model_family` NULL on all rows) and **no OpenData Platt exists** (`platt_models_v2` has only `tigge_mars`) → **p_cal = p_raw for all 801**; the live system already serves raw-identity on OpenData.
- Serving entry: `read_executable_forecast()` (`executable_forecast_reader.py:1099`) → `p_raw_vector_from_maxes()` → `calibrate_and_normalize()` + `get_calibrator()`. members_json = 51 daily-maxes in °C, 100% populated (no tz re-extraction needed).

**Implication:** improvement-mode is **non-exercisable for months** (no correction fitted, no power to gate one). The honest near-term deliverable is: contract + provenance ledger + **raw-identity serving** + harness *scaffolding* (equivalence live now; improvement dormant). The "before" baseline = raw-identity; the near-term "after" = raw-identity behind the contract → equivalence is the live test; improvement waits for data + a fitted candidate + a built gate.

---

## 4. Verification-accuracy record (the critics were right; I slipped twice)

The 3 opus critics were accurate and detailed. The two findings I initially "caught as critic errors" were BOTH my own verification mistakes, now corrected:
- **Cons-A (third/fourth chokepoint) — CONFIRMED real.** I grepped `src/strategy/evaluator.py`; the file is `src/engine/evaluator.py`. A wrong-path negative grep I misread as "hallucinated." The twins exist exactly as cited (evaluator.py:3296 + monitor_refresh.py:343).
- **Cons-SEV-3-H (table name) — CONFIRMED right.** I queried the stale live DB (still physically `_v2`); canonical code is `ensemble_snapshots` (`v2_schema.py:108`). I wrongly rejected a correct finding on a stale artifact.

Only genuine critic imprecision: `backfill_ens.py` bypass is low active-risk (--apply-gated, header "packet approval only", last_reused 2026-04-25) — which the consistency critic itself disclosed in its Open Questions. Category valid (writer outside the chokepoint); active blast radius small.

**Methodology note (both my errors share a root cause):** for design/structure questions, verify against the CODE at the build base — correct path, correct surface — NOT a negative grep on an unverified path and NOT the running DB (which lags merged renames). A negative result is only evidence if the path/surface is confirmed first. This is the same provenance trap that produced the original audit-doc errors; I repeated it twice and the operator + critics caught it.

---

## 5. Revised plan (deltas from Draft-2)

| Element | Draft-2 | Revised |
|---|---|---|
| Bias estimator | extend TIGGE-rooted EB hierarchy with lead/cycle/product levels | **REPLACE shrinkage target**: product-segregated, lead-respecting models; TIGGE = transfer-only-on-proof candidate; raw permanent default |
| OOS gate | "compose / ~20 LOC" | **BUILD**: date-block folds → block-bootstrap LCB(n_eff) → BH-FDR (reuse predecessor) → single primary score → simulation-coverage-validated |
| Equivalence test | p_raw \|Δ\|≈0 | **p_cal + logit(p_raw)**; \|Δ\|≤derived-rounding-bound (not ≈0 across the staircase); don't retire MC until p_cal equivalence holds |
| Contract target tuple | city/metric/date/product/cycle/lead/window | + **unit, settlement station/source/authority, bin_grid_id**; DST-correct local-day (not `floor(lead_hours/24)`) |
| Chokepoint | writer + reader | + **serving bias-lookup site (locate), lead-aware fail-closed**; writer inventory + quarantine stale (`backfill_ens`) |
| Naming | `ensemble_snapshots` | `ensemble_snapshots` (canonical) — sweep all docs |
| Near-term scope | contract + ledger + raw serving + selector dormant | same, but selector is dormant **by data necessity** (n≈801, no fitted candidate), not just policy |

HONEST LIMIT (elevated to a hard gate label per stat-critic + Draft-2 §5): until the gate is built and coverage-validated, **the only defensible served model is raw identity.** Improvement-mode certifies *not-worse + structurally-correct*, never profit, for the foreseeable data horizon.

---

## 6. Net
The reshape's spine is sound and the critics validate the direction. But Draft-2's correction-adoption machinery — extend-the-hierarchy + compose-the-gate — would re-arm sd3 via the compounding chain in §1. The fix is concrete and grounded: replace the shrinkage target, build the gate correctly, close the contract, and let raw-identity be the enforced near-term state. None of this blocks starting Phases 0/2/3 (contract + ledger + raw serving + equivalence harness); it blocks ever turning a correction ON until §2b is built and §2a is segregated.
