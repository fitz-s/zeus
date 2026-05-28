# Canonical Row Reproducibility Audit — full_transport_v1 (model_bias_ens_v2)

**Date:** 2026-05-27
**Branch:** `feat/ft-ship-invariant-audit` @ `ac40dd33`
**Tool:** `scripts/audit_error_model_row_reproducibility.py`
**CSV:** `docs/operations/ROW_REPRODUCIBILITY_AUDIT_2026-05-27.csv`
**Authority:** operator audit directive 2026-05-27 — canonical row reproducibility gate

---

## TL;DR — Production domain is not canonical

| Status | Count | % |
|---|---|---|
| REPRODUCIBLE | **35** | 44% |
| NON_REPRODUCIBLE | **39** | 49% |
| INSUFFICIENT_PRIOR | 1 | 1% |
| COVERAGE_MISLABELED | 4 | 5% |
| **Total** | **79** | 100% |

**Less than half of stored `full_transport_v1` rows pass canonical reproducibility under
current code + current DB.** Stored HIGH rows were fit at `code_commit=5a3c10dd`
(2026-05-25 18:31 CT) — **32 minutes before** `060540448e` (2026-05-25 19:03 CT) which
introduced the `MIN_PAIRED_N=5` transport gate. Stored LOW rows (`code_commit=eba9bc58`,
2026-05-27 11:52 CT) are post-gate but exhibit residual_sd divergence on small-n live
samples.

**These rows must not be used as live calibration domain.** They violate the math gate
they advertise (`error_model_family='full_transport_v1'`).

---

## Algebraic fingerprint: every NON_REPRODUCIBLE HIGH row matches the same signature

For 35 of 39 NON_REPRODUCIBLE rows:

```
delta_bias = stored_bias_c − recomputed_bias_c ≈ paired_delta_mean
AND n_paired < MIN_PAIRED_N (5)
```

This is the **exact signature of pre-gate fit**:

- Pre-gate code: shifted prior mean by the entire single-date paired delta → spurious large correction.
- Current code: `delta_gated = delta if len(delta) >= MIN_PAIRED_N else []` → prior-only.
- The difference between stored and recomputed equals the ungated delta.

The defect is not random noise. It is structurally identical across all affected rows.

### Worst-magnitude examples (HIGH MAM)

| City | stored_bias_c | recompute_bias_c | n_paired | paired_delta_c | delta_bias |
|---|---:|---:|---:|---:|---:|
| Dallas | **−10.02** | −0.15 | 1 | −9.87 | +9.87 |
| Busan | **+1.11** | −3.92 | 1 | +5.03 | −5.03 |
| Shanghai | **+1.25** | −3.15 | 1 | +4.41 | −4.41 |
| Seattle | **−3.44** | −0.07 | 1 | −3.37 | +3.37 |
| NYC | **−3.47** | −0.21 | 1 | −3.25 | +3.25 |
| Hong Kong | **−2.11** | +0.63 | 1 | −2.74 | +2.74 |
| Austin | **−2.08** | +0.41 | 1 | −2.49 | +2.49 |
| Houston | **−0.41** | −2.63 | 1 | +2.22 | −2.22 |
| Buenos Aires | **+0.62** | −1.59 | 1 | +2.22 | −2.22 |
| Denver | **+0.87** | −1.27 | 1 | +2.14 | −2.14 |
| Jakarta | **−1.75** | −3.80 | 1 | +2.05 | −2.05 |

Magnitude scales linearly with `|paired_delta|`. Every row's recompute is the correct
prior-only estimate; every stored row has the gate-violating shift baked in.

### Sign-direction recap (relevant to prior "East-Asia wrong-direction" report)

The "WRONG-DIRECTION" East-Asia stored values are special cases of this universal
ungated-delta defect:

- Shanghai: stored +1.25 (warm-cooling) → recompute −3.15 (cold) — direction RIGHT, magnitude correct.
- Qingdao: stored +3.31 (warm-cooling) → INSUFFICIENT_PRIOR (n_prior=1 — fit was statistically invalid).
- Busan: stored +1.11 (warm-cooling) → recompute −3.92 (cold) — direction RIGHT.

There is no sign-convention bug. There is no East-Asia-specific bug. There is a
**universal pre-gate transport defect** that happened to manifest most dramatically
in East-Asia rows because their single paired delta was large and positive.

---

## REPRODUCIBLE rows (35) — what they actually mean

Of the 35 REPRODUCIBLE rows:

- **27 have `n_paired = 0`** (e.g. DJF rows, Ankara MAM, Atlanta MAM, Cape Town MAM).
  These never invoked the transport step in the first place, so the pre-gate vs.
  post-gate code is byte-identical for them. They are reproducible by construction,
  not by virtue of being canonical fits.
- **5 have `n_paired = 5+`** (Tokyo MAM HIGH, Kuala Lumpur MAM HIGH, plus 3 borderline).
  These passed the new gate even pre-gate (>= MIN_PAIRED_N), so the result is identical.
- **3 are LOW-family with `n_paired = 0`** that happen to recompute identically
  (Hong Kong/Miami/Shanghai LOW). LOW family `code_commit=eba9bc58` is post-gate; these
  carry less semantic risk than HIGH but their statistical support is thin (n_prior=4–9).

**Implication:** "REPRODUCIBLE" here does not mean "validated by an OOS test." It only
means "stored value matches what the current canonical producer would write today."
For shipping, that is necessary but not sufficient.

---

## INSUFFICIENT_PRIOR — Qingdao MAM HIGH

Stored: `bias_c = +3.31°C, n_prior = 1`.
Recompute: `n_prior = 1` → **statistical-fit floor violated** (need n_prior ≥ 2).

The stored row encodes a confident +3.31°C cooling correction off **one** TIGGE
snapshot. Any production correction with n_prior=1 is mathematically degenerate.
A canonical producer today would either refuse to write the row or write an
identity/no-correction placeholder. The stored row should not be in the live domain.

---

## COVERAGE_MISLABELED — 4 DJF rows (Denver, Los Angeles, Paris, Sao Paulo)

Each row is labeled `season=DJF` but the actual snapshot coverage for that
`(city, prior_data_version, settled_before=2026-05-25)` slice is a single month from
the DJF triple (e.g. only month=2 or only month=12) — not the full DJF coverage the
label advertises. Reason: `training_cutoff=2026-05-25` is mid-MAM; only the most
recent DJF months (Dec, Jan, Feb of the past winter) are available, and for some
cities only one of those three had data.

**Risk:** if a downstream consumer applies this row to a target_date in a different
DJF month with a meaningfully different climatology, the row is misapplied. Current
production uses the row label as-if it described the whole season.

Mitigation: writer must record `coverage_months` in the row and the reader must check
target-date month membership before applying.

---

## Mechanism (concrete: Shanghai MAM HIGH)

Producer at `code_commit=5a3c10dd` (pre-gate):
```
delta = load_paired_delta(...) → [+4.4073]  (n=1)
# NO GATE — single-sample delta accepted
transported = transport_bias_prior(prior=−3.15, delta_samples=[+4.4073], kappa=1.0)
posterior_bias_c = stored = +1.25
```

Producer at `code_commit=ac40dd33` (current, post-gate):
```
delta = load_paired_delta(...) → [+4.4073]  (n=1)
delta_gated = delta if len(delta) >= MIN_PAIRED_N else []  → []
transported = transport_bias_prior(prior=−3.15, delta_samples=[], kappa=1.0) → −3.15
posterior_bias_c = recomputed = −3.15
```

Stored − recompute = +1.25 − (−3.15) = **+4.41 = paired_delta_mean**.

This is exact, not approximate. The defect has zero noise floor.

---

## What this implies for the FT-ship plan

The plan in `transient-nibbling-beaver.md` (Phase B → D → E) assumed the staging DB's
`model_bias_ens_v2 full_transport_v1` rows were canonical. **They are not.** The 71
HIGH rows already promoted to `state/zeus-world.db` carry the pre-gate defect.

**Required before any live ship of `full_transport_v1`:**

1. **Halt promotion / shadow / unshadow of `full_transport_v1` until rows are canonical.**
   Specifically: `config/settings.json::full_transport_live_enabled` must stay `false`,
   and the world.db `model_bias_ens_v2 full_transport_v1` rows must be quarantined
   (move to a `_pregate` shadow table or mark `authority='QUARANTINED_PREGATE'`).
2. **Refit all `full_transport_v1` rows on current HEAD** (the audit script already
   verifies the math by recomputing). Decision: refit in place vs. rename family to
   `full_transport_v2`.
3. **Rebuild `calibration_pairs_v2 family=full_transport_v1` rows** that were generated
   off the pre-gate posteriors (their p_raw inputs depend on the broken bias domain).
   Use the matched-cohort audit to identify which Platt rows must regenerate.
4. **Recommendation: bump family to `full_transport_v2`.** Mixing pre-gate and post-gate
   rows under the same family name pollutes the probability domain identity. A clean
   `_v2` namespace separates the audited canonical rows from the staging artifacts and
   prevents silent reuse via the family filter.

---

## Antibody — what stops this category forever

Add to CI / ship-readiness gate:

```python
# scripts/check_full_transport_ship_readiness.py
def check_row_reproducibility():
    """Every ft_v1+ row in production world.db must recompute exactly via current code."""
    rc = subprocess.call([
        sys.executable, "scripts/audit_error_model_row_reproducibility.py",
        "--world-db", "state/zeus-world.db",
        "--forecasts-db", "state/zeus-forecasts.db",
        "--family", current_family_name(),
    ])
    assert rc == 0, "row reproducibility FAIL — see audit CSV"
```

And per-row: persist `code_commit` (already done), `fit_signature_hash` (already done),
**and a `gate_set_hash`** (new) recording the active math-gate names at fit time.
Reader rejects rows whose `gate_set_hash` differs from current.

---

## Verdict

- **Code (`ens_error_model.py`) — sound.** All three invariants (sign / window /
  transport) pass by fixture proof (see `INVARIANT_SIGN_PROOF_2026-05-27.md`).
- **Data lineage (stored rows) — broken.** 49% of HIGH ft_v1 rows violate the
  MIN_PAIRED_N gate. The defect has an algebraic fingerprint (delta_bias ≈
  paired_delta_mean). One row (Qingdao) violates n_prior≥2. Four DJF rows mis-label
  coverage scope.
- **Production gate — must hold.** No live ship of `full_transport_v1` until canonical
  refit completes. Recommendation: refit + family rename to `full_transport_v2`,
  re-run audit to 100% REPRODUCIBLE.

The SAFE-16/HOLD-22 tier classification was built on top of these broken rows. It is
not a valid gate. The valid gate is **row reproducibility under current code**, which
is now mechanized in the audit script and CSV.
