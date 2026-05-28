# East-Asia Bias Lineage Trace — 2026-05-27

**Investigator:** Executor (feat/ft-ship-invariant-audit)
**Status:** READ-ONLY investigation. No writes to any production DB.
**Starting point:** Invariant audit (INVARIANT_SIGN_PROOF_2026-05-27.md) proved sign/window/transport
invariants PASS. Concluded: "East-Asia wrong-direction defect lives elsewhere (residual lineage,
upstream unit/TZ, per-city data contamination)."

---

## Scope

Cities studied: Shanghai (primary), Qingdao, Wuhan (East-Asia); Austin (Western reference baseline).
Metric: `high` / season: `MAM`.
Live data_version: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1`
Prior data_version: `tigge_mx2t6_local_calendar_day_max_v1`
Error-model family: `full_transport_v1`

---

## Surface 1 — Per-City Pair Distribution

### Observed vs Expected

| City | OPD snapshots (ensemble_snapshots_v2, OPD, MAM training=1) | contributes=1 | contributes=0 | city_timezone |
|------|-------------------------------------------------------------|---------------|---------------|---------------|
| Shanghai | 210 | 182 | 28 | Asia/Shanghai (ALL 210) |
| Qingdao  | 210 | 182 | 28 | Asia/Shanghai (ALL 210) |
| Wuhan    | 210 | 182 | 28 | Asia/Shanghai (ALL 210) |
| Austin   | 194 | 182 | 12 | America/Chicago (ALL 194) |

**Observation:** All OPD rows for East-Asia have `city_timezone` populated (`Asia/Shanghai`). No
NULL timezone rows exist in the OPD data. The `sec3_output.txt` NULL-TZ finding referred to old
calibration_pairs_v2-joined rows from 2024 legacy snapshots, which predate the timezone + window
extraction feature. Those pairs have `city_timezone=NULL`, `forecast_window_start_local=NULL`,
`contributes_to_target_extrema=NULL`.

**Verdict: CONSISTENT** for current OPD snapshots. Legacy calibration_pairs_v2 rows are stale artifacts.

**Implication:** The `contributes_to_target_extrema` flag is correctly populated for all OPD East-Asia
snapshots. The problem is not missing timezone — the legacy pairs are simply irrelevant to the producer.

---

## Surface 2 — Bias_c Reconciliation (Critical Finding)

### Producer pipeline trace

`fit_city_predictive_error` calls:
1. `load_bucket_residuals(TIGGE, contributor_policy="legacy_tigge_null_passthrough")` → n_prior
2. `load_bucket_residuals(OPD, contributor_policy="full_contributor_only")` → n_live  
3. `load_paired_delta(OPD, TIGGE)` → transport delta
4. `fit_bucket(TIGGE, [])` (prior-only; live < min_live_n=20) → f50
5. `transport_bias_prior(b50, sd50, delta_gated=[] since n_paired=1 < MIN_PAIRED_N=5)` → transported
6. `posterior_bias(transported, live=None)` → bias = f50.bias (PURE PRIOR)

### Numerical reconciliation

| | Shanghai | Qingdao | Austin |
|--|----------|---------|--------|
| Stored bias_c | **+1.2535** | **+3.3057** | -2.0815 |
| n_live (stored) | 14 | 13 | 13 |
| n_prior (stored) | 54 | 1 | 42 |
| training_cutoff | 2026-05-25 | 2026-05-25 | 2026-05-25 |
| TIGGE robust_mean (now) | -3.1538 | unknown | unknown |
| OPD dedup residual mean (14 dates) | -1.0971 | unknown | unknown |
| Reproduced bias_c (current state) | **-3.1538** | — | — |
| Delta stored vs reproduced | **+4.41°C** | — | — |
| Verdict | **DEFECT** | **SUSPECT** | DISCREPANT |

**Shanghai reconciliation detail:**
- TIGGE prior (legacy_null_passthrough, MAM, lead<=48): n=54, raw_mean=-3.2078, robust_mean=-3.1538
- OPD strict (full_contributor_only, MAM, lead<=48, cutoff=2026-05-25): n=15 unique dates → 14 after
  `target_date < '2026-05-25'` cutoff (strict less-than), dedup_mean=-1.0971
- n=14 < min_live_n=20 → live=None → posterior = pure prior = -3.1538
- Paired delta: n=1 (only 2026-05-06 in both OPD+TIGGE MAM) → gated → delta_gated=[]
- Full reproduction call: `fit_city_predictive_error(...)` → **bias=-3.1538**, NOT +1.2535

**The stored +1.2535 cannot be reproduced from current DB state at any path through the current code.**

### Root cause hypothesis: contributor_policy at fit time

The stored row records `code_commit='5a3c10dd516a9478e7b415dc0ff5a83571f87844'` and
`recorded_at='2026-05-25'`. The `full_contributor_only` contributor_policy requires
`contributes_to_target_extrema=1`. If the model was fit when `contributes` was being backfilled
or when the policy was different (e.g., `legacy_tigge_null_passthrough` applied to OPD too,
allowing all NULL rows), the n_live would have included the ~259,000 legacy calibration rows, which
have `residual_mean≈-3.44°C` for Shanghai.

However, with that many rows, the live likelihood would dominate (n>>20) and the posterior would
collapse to the live mean ≈-3.44, not +1.2535. So the mismatch is not explained by contributor_policy
change alone.

**Alternative hypothesis (strongest):** The TIGGE prior was computed differently at fit time — either
with a different prior_data_version, a different `full_contributor_only` TIGGE handling, or the
`transport_bias_prior` received a non-empty delta (large positive) that shifted +4.41°C above the
TIGGE mean. The paired delta for 2026-05-06 is +4.4073°C. If at fit time n_paired=1 was NOT gated
(MIN_PAIRED_N was not yet in the code, or was 1), this single delta would have shifted the prior
from -3.15 to -3.15+4.41=+1.26°C ≈ the stored +1.2535.

**This is the likeliest causal path:** MIN_PAIRED_N gate was added in the current codebase but was
NOT present at `code_commit='5a3c10dd'` when the model was fit. The single paired delta (+4.41°C)
was applied without the n>=5 gate, shifting the TIGGE prior by +4.41°C.

---

## Surface 3 — Unit Consistency

| City | members_unit | settlement_unit | Verdict |
|------|-------------|-----------------|---------|
| Shanghai | degC | C | CONSISTENT (both Celsius, different string forms) |
| Qingdao  | degC | C | CONSISTENT |
| Wuhan    | degC | C | CONSISTENT |
| Austin   | degF | F  | CONSISTENT (both Fahrenheit; producer converts via `_to_c`) |

**Verdict: CONSISTENT.** No unit mismatch. `degC` vs `C` are semantically identical; the producer
checks `'f' in mu.lower()` for Fahrenheit conversion — `degC` does not match this, so no spurious
conversion is applied to East-Asia data.

---

## Surface 4 — Timezone Extraction

**Current OPD snapshots:** All East-Asia OPD rows have `city_timezone='Asia/Shanghai'` (210 rows each).
`forecast_window_start_local` and `forecast_window_end_local` are fully populated with correct UTC+8
offsets (e.g., `2026-05-06T08:00:00+08:00`).

**Legacy calibration_pairs_v2-linked snapshots:** Pre-2026 rows joined via calibration_pairs_v2 show
`city_timezone=NULL`, `forecast_window_start_local=NULL`. These rows predate the timezone extractor.
All such rows have `contributes_to_target_extrema=NULL` → rejected by `full_contributor_only` policy
→ do NOT enter the producer's OPD residual list.

**Verdict: CONSISTENT** for all rows that enter the producer. TZ extraction is correct for the data
actually used. Legacy NULL-TZ rows are gated out.

---

## Surface 5 — Period and Data Coverage

| | Shanghai | Qingdao |
|--|----------|---------|
| OPD snapshot year range | 2026 only | 2026 only |
| OPD MAM months present | May (month 5) only | May (month 5) only |
| Unique dates (contributes=1, lead<=48, <cutoff) | 15 (14 with strict cutoff<2026-05-25) | ~same |
| TIGGE year range | 2026 only | 2026 only |
| TIGGE unique MAM dates | 53 (n_prior=54 after dedup) | ~54 |

**Verdict: SUSPECT.** The system claims to be doing MAM seasonal correction but has data from ONLY
May 2026. March and April are completely absent from both OPD and TIGGE snapshots for East-Asia cities.
This means "MAM" is functionally "May-only." The season label is misleading — the correction is fit
on a single month of late-spring data and applied as if it represents all of March-April-May.

For Shanghai in May 2026, temperatures range 19-31°C. March Shanghai (~8-15°C) is a completely
different regime. The correction trained on May is applied to March predictions — this is a
season-coverage defect, not an East-Asia-specific defect (Austin also shows only 2026 data, so this
is universal for newly-ingested cities).

---

## Surface 6 — Qingdao n_prior=1 Anomaly

**Stored:** `n_prior=1`, `bias_c=+3.3057` for Qingdao MAM HIGH.

**Observed:** TIGGE Qingdao has 10 rows (from 2026-05), all with `contributes_to_target_extrema=0`
and `authority='VERIFIED'`, `training_allowed=1`, `causality_status='OK'`. Sample:
`date=2026-05-01 mean=29.47 sv=28.0 contributes=0`.

The `legacy_tigge_null_passthrough` policy allows `contributes=NULL OR 1` — explicitly excludes
`contributes=0`. So for Qingdao TIGGE: all available rows have `contributes=0` → rejected by the
policy → prior has 0 usable rows → should raise `ValueError("no TIGGE prior residuals")`.

Yet `n_prior=1` in the stored row. At fit time (2026-05-25), presumably one Qingdao TIGGE row had
`contributes=NULL` (unset at the time), which the null_passthrough policy accepted. That single row
produced `prior_mean ≈ +3.3°C` (warm). With n=1 < min_live_n=20, live=None → posterior = pure prior
= +3.3°C (WRONG).

**Verdict: DEFECT.** n_prior=1 prior is statistically worthless and likely has `contributes=0` now
(backfilled after the model was fit). The stored Qingdao bias of +3.3057°C is from a single-sample
warm-biased TIGGE snapshot.

---

## Surface 7 — Cluster / Contamination Check

| City | error_model_key | Cluster evidence |
|------|----------------|-----------------|
| Shanghai | Shanghai|high|MAM|full_transport_v1|ecmwf_opendata_... | No cluster column in stored row |
| Qingdao  | Qingdao|high|MAM|full_transport_v1|... | No cluster |
| Wuhan    | Wuhan|high|MAM|full_transport_v1|... | No cluster |

`model_bias_ens_v2` has no `cluster` column in the full schema dump. The per-city correction is not
clustered. No cross-city contamination pathway in the DB schema.

**Verdict: CONSISTENT.** No cluster contamination. Each city has an independent row.

---

## Reconciliation Summary

### Why stored East-Asia bias_c values do not match current residuals

| Root cause | Shanghai | Qingdao |
|-----------|----------|---------|
| MIN_PAIRED_N gate absent at fit time | **PRIMARY (+4.41°C shift)** | possibly |
| n_prior=1 from single null-passthrough TIGGE row | — | **PRIMARY** |
| live=None (n_live < min_live_n=20) | CONTRIBUTING | CONTRIBUTING |
| Model fit from May-only data (not full MAM) | CONTRIBUTING | CONTRIBUTING |

### East-Asia vs Austin comparison

Austin also has `n_live=13 < min_live_n=20` (posterior is pure prior), DISCREPANT stored vs reproduced
(delta=-1.77). Austin shows the same root cause: posterior is dominated by the TIGGE prior, which has
been fit on 42 TIGGE rows — many more than Qingdao's single row. Austin's correction is more stable
but still not from live evidence.

---

## Ranked Root-Cause Hypotheses

1. **MIN_PAIRED_N gate retroactive** (HIGH confidence for Shanghai): At `code_commit='5a3c10dd'` the
   model was fit without the n>=5 gate on paired delta. Single paired sample (+4.41°C) shifted the
   prior by +4.41°C → stored `+1.2535` ≈ `(-3.15 + 4.41)`. The gate was added later. The stored
   row is a snapshot of the pre-gate behavior. **Action: verify `5a3c10dd` diff for `MIN_PAIRED_N`.**

2. **Qingdao single-sample prior** (HIGH confidence): TIGGE `contributes=0` now; at fit time one
   row had `contributes=NULL` → accepted by null_passthrough. Single warm snapshot (+28→mean≈+3.3°C)
   made the prior. **Action: re-fit Qingdao after resolving contributes policy for TIGGE rows.**

3. **May-only MAM coverage** (MEDIUM confidence, universal): East-Asia + Western cities alike have
   only May 2026 data. "MAM" correction is effectively "May-only." March/April performance is
   extrapolated from May, which is a different temperature regime. **Action: wait for full MAM
   ingest coverage, or scope corrections to DJF/MAM/JJA/SON only once each season has ≥30 dates.**

4. **contributor_policy change after fit** (MEDIUM confidence): If `full_contributor_only` was not
   yet the policy at `5a3c10dd`, more OPD rows (including old null-contributes rows with mean≈-3.44°C)
   would have entered the live residual list, but n would still be large — this would have DECREASED
   bias_c toward -3.44, not increased it. So this is not the primary explanation for +1.2535.

5. **Data version change** (LOW confidence): All stored `live_data_version` and `prior_data_version`
   match current DB. No version substitution.

---

## Recommended Next Actions (per surface)

| Surface | Finding | Next action |
|---------|---------|------------|
| bias_c reconciliation | Shanghai stored +1.2535 irrecoverable from current state | `git show 5a3c10dd -- src/calibration/ens_error_model.py` to confirm MIN_PAIRED_N absence |
| Qingdao n_prior=1 | Single-sample, now contributes=0 | Re-fit after fixing TIGGE contributor policy for new cities |
| MAM coverage | May-only | Block seasonal correction unless ≥ 2 calendar months represented in training data |
| MIN_PAIRED_N gate | Gate in current code is correct; pre-gate stored rows are stale | Re-fit all East-Asia cities with current codebase after ≥5 paired samples exist |
| Unit consistency | No issue | None |
| TZ extraction | No issue for current rows | None |
| Cluster | No issue | None |

---

## Appendix: Key Numbers

```
Shanghai MAM HIGH (full_transport_v1, VERIFIED):
  stored: bias_c=+1.2535  n_live=14  n_prior=54  weight_live=0.0
  reproduced (current code+data): bias_c=-3.1538  n_prior=54  n_live=15(→14)
  delta: +4.41°C
  explanation: TIGGE prior=-3.15; single paired_delta=+4.41 was applied ungated at fit time

Qingdao MAM HIGH (full_transport_v1, VERIFIED):
  stored: bias_c=+3.3057  n_live=13  n_prior=1
  TIGGE rows now: all contributes=0 → 0 pass legacy_null_passthrough
  explanation: single null-contributes TIGGE row accepted at fit time, now backfilled to 0

Austin MAM HIGH (full_transport_v1, VERIFIED):
  stored: bias_c=-2.0815  n_live=13  n_prior=42
  recomputed strict: n=15 dedup dates, mean=-1.4°C (approx)
  delta: -1.77°C → DISCREPANT (all cities have stale stored rows)

Paired delta (Shanghai): n=1, value=+4.4073°C (2026-05-06 only)
MIN_PAIRED_N current value: 5  (gate prevents n=1 delta from being applied)
min_live_n: 20  (all East-Asia cities < 20 live dates → live evidence ignored)
```
