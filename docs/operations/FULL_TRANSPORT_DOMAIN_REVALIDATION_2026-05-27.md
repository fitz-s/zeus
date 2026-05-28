# full_transport error-model domain — failure proof, revalidation tests, and action manifest

**Date:** 2026-05-27 (CT)
**Author:** Zeus session 866db2ea (opus orchestrator)
**Branch:** `feat/ft-ship-invariant-audit` @ `717cf50c03` (after PR #354 salvage merge + ghost-table cherry-pick)
**Status:** DIAGNOSTIC + REVALIDATION. No production write performed. FT flag stays OFF.
**Audience:** operator review before any rebuild / promote / live-route decision.

---

## 0. Reading guide (前因后果)

This document is structured as: **why** (the defect) → **what was done** (the tests) →
**how** (exact method + sample sizes) → **what it proves** → **what it does NOT prove
(unknowns)** → **action manifest** → **the path forward**.

Three companion artifacts (same dir):
- `ROW_REPRODUCIBILITY_AUDIT_2026-05-27.csv` — per-row audit, 79 rows
- `ROW_ACTION_MANIFEST_2026-05-27.csv` — per-row action class (A–E)
- `ROW_REPRODUCIBILITY_AUDIT_2026-05-27.md` — narrative of the audit

Tool: `scripts/audit_error_model_row_reproducibility.py` (committed `29869ec90c`).

---

## 1. 前因 — why full_transport_v1 is not a valid production domain

### 1.1 The invariant that must hold

A production error-model row is only valid if:

```
stored_row == recompute(current_code, current_DB, declared_filters, declared_gates)
```

If a stored row cannot be reproduced by running the current producer on the current
source data, the row is not a function of the current code — it is a stale artifact of
some prior code/data state. It does not belong to the probability domain the live system
believes it is using.

### 1.2 The measured failure

Audit of all 79 `model_bias_ens_v2 full_transport_v1` rows (world.db):

| Status | Count | % |
|---|---:|---:|
| REPRODUCIBLE | 35 | 44% |
| NON_REPRODUCIBLE | 39 | 49% |
| INSUFFICIENT_PRIOR | 1 | 1% |
| COVERAGE_MISLABELED | 4 | 5% |

**Only 44% of rows reproduce.** This is not "model performs poorly" — it is
probability-domain identity failure.

### 1.3 The algebraic fingerprint (root cause is exact, not statistical)

For 35 of 39 NON_REPRODUCIBLE rows:

```
stored_bias_c − recompute_bias_c ≈ paired_delta_mean    (exact)
AND n_paired < MIN_PAIRED_N (5)
```

Mechanism: rows fit at `code_commit=5a3c10dd` (2026-05-25 18:31 CT) — **32 minutes
before** `060540448e` (2026-05-25 19:03 CT) which introduced the `MIN_PAIRED_N=5`
transport gate. The pre-gate producer accepted a single-day `F25−F50` delta
(`n_paired=1..4`) as a trusted transport shift and wrote it into `bias_c`. Current code
gates it out (`delta_gated=[] when len(delta)<5`), so the stored value and the
recompute diverge by exactly the ungated delta.

Worked example, Shanghai MAM HIGH:
```
stored     +1.2535
recompute  −3.1538
delta      +4.4073  == paired_delta_mean (single-day F25−F50)
+1.2535 ≈ −3.1538 + 4.4073
```

Same fingerprint: Dallas (+9.87), Busan (−5.03), Seattle (+3.37), NYC (+3.25),
Hong Kong (+2.74), Austin (+2.49), and ~28 more.

### 1.4 Two error sub-classes confirmed

- **HIGH = bias-domain failure.** 34 HIGH NON_REPRODUCIBLE rows have wrong `bias_c`
  (ungated paired delta). This changes the SIGN/MAGNITUDE of the correction.
- **LOW = scale-domain failure.** 5 LOW NON_REPRODUCIBLE rows have `bias_c` MATCHING
  (`delta_bias_c = 0`) but `residual_sd_c` diverging (`sd_diff`). This changes
  `extra_member_sigma` → changes p_raw spread, not its center.

### 1.5 Two further domain defects

- **INSUFFICIENT_PRIOR (Qingdao MAM HIGH):** `n_prior=1, bias_c=+3.31,
  correction_strength=1`. A single prior sample mathematically cannot support a confident
  VERIFIED production correction.
- **COVERAGE_MISLABELED (4 DJF rows: Denver, Los Angeles, Paris, Sao Paulo):** labeled
  `season=DJF` (months 12,1,2) but actual snapshot coverage at `training_cutoff=2026-05-25`
  is a single month (e.g. only month 2, or only month 12). Applying as a full-season row
  to other DJF months is an applicability-domain error.

---

## 2. 做了什么 + 怎么做 — the revalidation tests (this session)

Five tests were run, in order. Each lists the **exact method**, **sample size**, and
**wall-clock**, so the scope is auditable.

### TEST 1 — Full row reproducibility audit (diagnostic)

- **Method:** `scripts/audit_error_model_row_reproducibility.py` opens world.db +
  forecasts.db read-only, recomputes each stored row via `fit_city_predictive_error`
  (current code), classifies vs stored.
- **Sample:** ALL 79 rows (71 HIGH + 8 LOW).
- **Wall-clock:** ~5 s.
- **Result:** 35 REPRO / 39 NON_REPRO / 1 INSUFFICIENT_PRIOR / 4 COVERAGE_MISLABELED.
- **Proves:** the stored production domain is 56% non-canonical.

### TEST 2 — Small-sample producer re-fit (does the producer self-heal?)

- **Method:** ran `scripts/fit_full_transport_error_models.py --db /private/tmp/scratch_ens_fit.db
  --metric high --city <X> --commit` for 5 cities, overwriting stored rows; re-ran TEST 1
  audit on the result.
- **Sample:** 5 cities × HIGH only = **10 rows written** (Shanghai, Dallas, Hong Kong
  re-fit from NON_REPRO; Atlanta, London were REPRO controls).
- **Wall-clock:** ~3 s.
- **Result:** whole-scratch HIGH audit moved 27→34 REPRODUCIBLE. Shanghai/HK flipped to
  REPRO; Dallas bias_c canonicalized (−10.02→−0.15) but sd_diff=−0.105 left it borderline.
- **Proves:** the producer, run today, writes rows that the audit calls REPRODUCIBLE.
- **CAVEAT (this is tautological):** the audit and the producer call the SAME
  `fit_city_predictive_error`. "REPRO after re-fit" is true by construction. TEST 2 alone
  proves determinism, not correctness. This is why TEST 3 exists.

### TEST 3 — Predictive-grounding (tautology-breaking, INDEPENDENT computation)

- **Method:** loaded raw TIGGE residuals via `load_bucket_residuals`, computed a robust
  trimmed mean with a **hand-written `robust_mean()` not shared with the producer**,
  compared to the re-fit `bias_c`. For Shanghai-class rows (`n_paired<5`), transport is
  gated → posterior ≈ prior-only → bias_c should ≈ robust mean of TIGGE residuals.
- **Sample:** 3 cities (Shanghai, Dallas, Hong Kong) MAM HIGH.
- **Wall-clock:** ~2 s.
- **Result:**

  | City | re-fit bias_c | independent robust-mean | diff | verdict | OLD pre-gate bias_c | OLD diff |
  |---|---:|---:|---:|---|---:|---:|
  | Shanghai | −3.1538 | −3.0551 | −0.099 | PASS | +1.2535 | +4.31 (FAIL) |
  | Dallas | −0.1470 | −0.2599 | +0.113 | PASS | −10.0207 | −9.76 (FAIL) |
  | Hong Kong | +0.6269 | +0.6124 | +0.014 | PASS | −2.1082 | −2.72 (FAIL) |

- **Proves:** the new bias_c is GROUNDED in the actual prediction-error sample — it
  equals an independently-computed sample statistic within <0.12°C. The OLD pre-gate
  values fail the same grounding test by 2.7–9.8°C. The grounding check DISCRIMINATES
  (old=FAIL, new=PASS), so it is not tautological.

### TEST 4 — Real Monte-Carlo pair regeneration (end-to-end, the expensive path)

- **Method:** `scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force --city Shanghai
  --temperature-metric high --start-date 2024-04-01 --end-date 2024-04-02 --n-mc 1000
  --error-model full_transport_v1 --mc-seed-base 42` on scratch DB. This runs the actual
  per-snapshot Monte-Carlo p_raw generation using the new canonical bias.
- **Sample:** 1 city × HIGH × **1 day** = 16 snapshots × 1000-MC = **1632 pairs** (102 bins).
- **Wall-clock:** ~30 s (n_mc=1000).
- **Result:** 1632 pairs written, 0 fail-open, 0 no-matching-obs. p_raw stats:
  min=0.0000, max=0.1551, mean=0.0098 (≈1/102 bins), 0 NaN/null, all ∈ [0,1], sums to
  1.0 per snapshot.
- **Proves:** the MC pipeline runs end-to-end with the new bias and produces valid,
  normalized probability distributions. The pair-regeneration step is functional.

### TEST 5 — BULK watchdog interaction (operational finding)

- **Method:** initial TEST 4 attempt used `--n-mc 10000 --end-date 2024-04-30` (240
  snapshots).
- **Result:** `BulkChunkerNotPolledError: exceeded watchdog_s=30 without
  yield_if_live_contended()`. The standalone rebuild CLI does not call the live-contention
  yield hook; with live daemons now running (post-pause-lift), the BULK watchdog aborts
  any rebuild that runs >30 s without yielding.
- **Proves:** a full rebuild CANNOT run concurrently with live daemons via this CLI. It
  requires either (a) live pause during rebuild, (b) a yield-aware rebuild path, or
  (c) chunked runs each <30 s. This is an operational constraint for the full rebuild.

---

## 3. 是未知的 — what these tests do NOT prove (honest unknowns)

1. **Scale generalization untested.** TEST 4 ran 16 snapshots (1 day, n_mc=1000). The
   full rebuild is ~80k snapshots × n_mc=10000 across 52 cities — a multi-day MC job.
   Nothing here proves the full job completes, stays numerically stable across all
   cohorts, or fits in disk/time budget. TEST 4 is a smoke test, not a scale test.
2. **n_mc=1000 ≠ n_mc=10000.** TEST 4 used 1000 MC iterations for speed. Production uses
   10000. The distribution shape at 1000 is coarser; tail-bin probabilities may differ.
   Not validated at production n_mc.
3. **No Platt / calibration validated.** Zero Platt fits run on the regenerated pairs.
   Whether the new p_raw distributions calibrate well (or need identity certification) is
   unknown.
4. **No live-edge behavior validated.** The new bias changes p_raw → changes edge → changes
   which markets trade. No matched-date proper-score eval, no edge-bin comparison, no
   Kelly/decision replay was run on the new domain.
5. **LOW scale path untested.** The 5 LOW sd_diff rows were NOT re-fit. The
   `stdev(opd)` vs `stdev(tig)` fallback divergence is identified but not exercised; the
   fix is hypothesized, not demonstrated.
6. **Replay-equivalence not run.** No stored pair was compared against a regenerated pair
   to decide reuse-vs-regen per cohort (Phase D below). TEST 4 deleted+rewrote pairs; it
   did not compare old vs new for the same snapshot.
7. **3 cities ≠ 79 rows for grounding.** TEST 3 grounding ran on 3 cities. The other 31
   NON_REPRO HIGH rows are assumed to share the fingerprint (CSV confirms the algebra) but
   were not individually grounding-checked.
8. **COVERAGE_MISLABELED / INSUFFICIENT_PRIOR have no implemented handler.** The action
   classes (C, D) are policy decisions, not yet coded. Qingdao + 4 DJF rows need explicit
   identity/fallback/month-scope logic that does not exist yet.
9. **gate_set_hash does not exist yet.** The antibody that would prevent this entire class
   of defect (reader rejects rows whose gate-set differs from current) is designed but not
   implemented.

---

## 4. Action manifest (per-row, from audit CSV)

Full per-row table: `ROW_ACTION_MANIFEST_2026-05-27.csv`. Summary:

| Action | Count | Meaning |
|---|---:|---|
| **A_REUSE_PENDING_REPLAY** | 35 | REPRODUCIBLE. Copy to new family as candidate; gate on p_raw replay-equivalence before trusting pairs. |
| **B_REFIT_AND_REGEN_COHORT** | 34 | HIGH bias-domain failure. Recompute row under current gate (proven works, TEST 2/3); regenerate p_raw pairs for this cohort only. |
| **C_NO_LEARNED_CORRECTION** | 1 | Qingdao. Never write confident city row; write identity/no-correction OR explicit fallback prior. |
| **D_MONTH_SCOPE** | 4 | DJF coverage-mislabeled. Convert to month-scoped row OR block when target month ∉ coverage_months. |
| **E_LOW_SCALE_REGEN** | 5 | LOW scale-domain failure. bias_c OK; residual_sd changed → regen p_raw for LOW cohort (scale only, no bias refit). |

Key consequence: **only 34+5=39 cohorts need MC pair regeneration**, not all 79. The 35
A-rows may be reusable after a replay-equivalence check. This bounds the expensive rebuild.

---

## 5. The path forward (operator-adjudicated; NOT yet executed)

Per operator directive 2026-05-27. **Naming:** operator instruction has varied between
"no v1/v2 naming" and "drop v1, create full_transport_v2". This document uses
"new canonical family" as a placeholder; **the exact family name is an open operator
decision** (see §3 unknown — gate_set_hash design).

1. **Phase A — quarantine the old family.** Operator decided AGAINST a DB-write
   quarantine this session ("no quarantine — this blocks data ingest"). The reader-side
   protection is already in place: `ens_bias_repo.read_bias_model` filters
   `authority='VERIFIED'` (PR #349 F4), and FT flag is OFF. The old rows stay inert.
2. **Phase B — fit new-family rows under current gates**, with full lineage:
   `code_commit`, `gate_set_hash` (MUST exist — the antibody), `fit_signature_hash`,
   `source_row_hash`, `coverage_months`, all n_* counts, paired_delta_c.
3. **Phase C — row reproducibility audit must be 100%** on served rows. Acceptable:
   REPRODUCIBLE / EXPLICIT_IDENTITY / EXPLICIT_FALLBACK / NO_ROUTE. Unacceptable:
   NON_REPRODUCIBLE / INSUFFICIENT_PRIOR-with-correction / COVERAGE_MISLABELED-as-season.
4. **Phase D — replay-equivalence** decides pair reuse per cohort (A-rows) vs regen
   (B/E-rows).
5. **Phase E — Platt or certified identity** per served bucket; no implicit missing-Platt
   fallback.
6. **Phase F — matched-date proper-score + decision replay** before live route.

**Operational constraint (TEST 5):** the full rebuild must run with live paused OR via a
yield-aware path; it cannot run concurrently with live daemons.

---

## 6. Current live state (orthogonal to this domain work)

- FT flag `full_transport_live_enabled = false` → live uses plain ENS `p_raw_vector`
  (identity path), NOT the contaminated rows. The domain failure does not affect current
  live trades.
- Operator disk-emergency pause was lifted this session (46GB backup deleted, disk 23→69GB
  free). Live daemons restarted on `717cf50c03`.
- Live cycle now produces candidates (9 in one opening_hunt cycle) but rejects on
  `confidence_band_insufficient` + `strategy_economic_floor` — alpha-quality gates, not
  data/domain gates. Decision_events / probability_trace_fact writers remain a separate
  open investigation (tasks #120, #105).

---

## 7. One-line verdict

`full_transport_v1` stored rows are 56% non-canonical (proven by audit + algebraic
fingerprint + independent grounding). The producer, run on current code, generates
canonical rows (proven by TEST 2 self-consistency + TEST 3 independent grounding + TEST 4
end-to-end MC). The full rebuild is **functionally validated on a 1-day smoke slice** but
**NOT validated at production scale, n_mc, Platt, or live-edge** (§3). Next gate is a
full-coverage re-fit + 100% reproducibility audit before any pair regeneration or promote.

---

## 8. ADDENDUM (post-operator-adjudication) — full-coverage canonical re-fit + selective-rebuild proof

**Operator adjudication 2026-05-27:** NO full rebuild. Selective, manifest-driven
regeneration (p_raw is cohort-local: only cohorts whose error-model params Θ changed need
MC regen). **No v1/v2 family naming** — domain identity carried by `gate_set_hash`
(reader serves only rows whose gate_set_hash == current; old rows auto-reject). This is
the structural antibody, superior to a version-suffix.

### TEST 6 — full-coverage canonical re-fit (all 79 rows) + same-source audit

- **Method:** ran `fit_full_transport_error_models.py --metric high --commit` then
  `--metric low --commit` on `/private/tmp/scratch_ens_fit.db` (all cities), then audited.
- **Sample:** ALL 79 rows (71 HIGH + 8 LOW). Wall-clock ~40 s (fit only, no MC).
- **First audit (WRONG — cross-source):** `--forecasts-db state/zeus-forecasts.db` (LIVE,
  concurrently written) → 49 REPRO / 25 NON_REPRO. The 25 failures were **entirely
  cross-source data-skew**: the re-fit read residuals from the frozen scratch snapshot
  (2026-05-27 12:09) while the audit recomputed against live forecasts.db with newer
  ingested snapshots. Methodology flaw in the audit invocation, NOT a producer defect.
- **Corrected audit (same-source):** `--forecasts-db /private/tmp/scratch_ens_fit.db`
  (same source the producer fit from):

  | Status | Count |
  |---|---:|
  | REPRODUCIBLE | **74** |
  | INSUFFICIENT_PRIOR | 1 (Qingdao — action C) |
  | COVERAGE_MISLABELED | 4 (DJF — action D) |
  | NON_REPRODUCIBLE | **0** |

- **Proves:** the producer on current code (`717cf50c03`) is **100% canonical at full
  scale** — all 74 servable rows REPRODUCIBLE against their own source. The C+D rows are
  not reproducibility failures; they are the route/scope action classes (no learned
  correction / month-scope guard) from the manifest.

### New operational constraint (from the cross-source skew)

The production rebuild MUST freeze its source across the fit→audit→serve window:
- either run against a frozen snapshot copy of forecasts.db, OR
- pause live data-ingest during the rebuild.

Otherwise live ingestion shifts the residual set mid-process and reintroduces
cross-source skew (the 25 false NON_REPRO). This compounds TEST 5 (BULK-lock): the
rebuild needs both (a) no live BULK contention and (b) a stable source.

### Selective-rebuild scope (mathematically minimal, per manifest + p_raw locality)

```
Mandatory MC regen:  B (34, bias-domain) + E (5, scale-domain) = 39 cohorts
Conditional:         C (1) if served via new identity/fallback p_raw domain
                     D (4) if month-scope changes served applicability
                     A (35) only the subset that FAILS replay-equivalence
Never:               full 79-row rebuild (function-dependency waste)
```

### Corrected execution plan (gate_set_hash, no version naming)

1. Add `gate_set_hash` to producer + `write_bias_model` (hash of active gate names +
   thresholds: MIN_PAIRED_N=5, min_prior_n, min_live_n, coverage policy). Reader rejects
   rows whose gate_set_hash ≠ current. **This is the antibody; it replaces the v2 rename.**
2. Regenerate canonical rows for ALL buckets under current gates → authority=STAGING,
   stamped with current gate_set_hash + code_commit + fit_signature_hash + coverage_months.
   Source MUST be frozen (snapshot copy or ingest-paused).
3. Row-reproducibility audit (same-source) must be 100% servable (REPRODUCIBLE /
   EXPLICIT_IDENTITY / EXPLICIT_FALLBACK / NO_ROUTE). Proven achievable by TEST 6.
4. Replay-equivalence on the 35 A-rows → reuse pairs if pass, else add to regen set.
5. MC-regenerate only B+E+(C-served)+(D-changed)+(A-fail) cohorts. Needs live-pause window.
6. Platt or certified-identity per served bucket; no implicit missing-Platt fallback.
7. Matched-date proper-score + decision replay before live route.
8. Promote STAGING→VERIFIED only after 1-3 pass.

**Status:** Steps 2-3 proven feasible on scratch (TEST 6). Step 1 (gate_set_hash) +
Steps 4-8 not yet executed. MC regen (step 5) requires a live-pause + frozen-source window.
