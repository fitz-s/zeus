# critic-dave Phase 9C Wide Review — Cycle 1

**Commit:** 114a0f5
**Reviewer:** critic-dave cycle 1 (lean re-spawn)
**Date:** 2026-04-18
**Verdict:** **ITERATE** (MAJOR — one checkbox-antibody + one latent bomb)

---

## Top 3 Justifications

1. **R-CC.2 is a checkbox antibody.** The test exercises `_read_v2_snapshot_metadata(conn,...)` and `boundary_ambiguous_refuses_signal(meta)` in isolation, but does NOT exercise the evaluator's actual *wiring* of the helper into the candidate gate (`src/engine/evaluator.py:738-742`). Surgical revert confirms: replacing the wired call with a hardcoded `v2_snapshot_meta = {}` neuters DT#7 in the evaluator, yet R-CC.2 still PASSES. This is exactly the "component works, relationship is broken" failure pattern Fitz Constraint #1 warns about.

2. **L3 CRITICAL fix has a latent pollution bomb in `_fit_from_pairs`.** `src/calibration/manager.py:285` still calls legacy `save_platt_model(conn, bk, ...)` with a metric-blind `bucket_key`. When `get_calibrator(temperature_metric="low")` triggers an on-the-fly refit (line 171 or 189), the fitted model is returned in-session but **persisted into the legacy metric-blind `platt_models` table under `{cluster}_{season}` key** — same key a HIGH refit would use. Subsequent HIGH lookup then hits legacy fallback (L165-168) and may read a LOW-fitted model as HIGH. The L3 fix closes the READ path but leaves the WRITE path open. Not forward-logged in the commit message.

3. **Antibody count discrepancy.** Contract + commit message both claim "13 antibodies" in `tests/test_phase9c_gate_f_prep.py`; file actually contains 12 test functions (all pass). Minor but consequential: documentation of coverage is one row overstated.

---

## Priority A — Regression Math

```
144 failed, 1869 passed, 93 skipped, 7 subtests passed in 39.08s
```

**Matches expected 144/1869/93.** No drift from the forward-logged baseline. Pass.

---

## Priority B — Surgical-Revert Probes

### Probe 1: R-BZ.1 (L3 CRITICAL metric-aware calibrator)

- **Revert action:** Replaced `get_calibrator`'s v2-first-then-legacy-for-HIGH block (manager.py:158-168) with the pre-P9C single-line `model_data = load_platt_model(conn, bk)`.
- **Result under revert:** All 3 R-BZ tests (LOW, HIGH, default) **FAIL** — `cal_low is None`, `cal_high is None`, default `cal is None`. This is because the test DB fixture only populates `platt_models_v2`, so a legacy-only read finds nothing.
- **Restored result:** 3 passed.
- **Verdict:** **Genuine antibody** — catches the L3 regression cleanly. R-BZ paired-positive design (LOW + HIGH + default) is solid and forces the v2-first wiring.

### Probe 2: R-CC.2 (DT#7 boundary-ambiguous gate)

- **Revert action:** Replaced the evaluator wiring `v2_snapshot_meta = _read_v2_snapshot_metadata(conn, city.name, target_date, temperature_metric.temperature_metric)` with `v2_snapshot_meta = {}` — effectively neutering DT#7 at the evaluator level while leaving helper + policy function intact.
- **Result under revert:** **R-CC.1 and R-CC.2 still PASS (2 passed, 10 deselected).**
- **Verdict:** **CHECKBOX ANTIBODY — MAJOR FINDING.** R-CC.2 only probes `helper(conn) == {"boundary_ambiguous": True}` and `refuses_signal(meta) == True`, each called directly by the test. The evaluator's consumption of the helper (the actual S4/A4 wire) is never exercised. To be a true relationship antibody, the test needs to invoke the evaluator path (`evaluate_all_candidates` or the internal function that contains the gate) with a conn that has a flagged row, and assert the returned `EdgeDecision` has `rejection_reasons=["DT7_boundary_day_ambiguous"]`. Without that, a future refactor can safely delete line 738-741 and DT#7 goes silently dormant.

---

## Priority C — Hunt: `_fit_from_pairs` legacy-save path

**Hunt target:** Does on-the-fly Platt refit under `temperature_metric="low"` pollute the legacy `platt_models` table?

**Evidence (manager.py:238-294):** `_fit_from_pairs(conn, cluster, season, *, unit)` accepts only `cluster` and `season` — no `temperature_metric` parameter. Line 285 calls `save_platt_model(conn, bk, ...)` where `bk = bucket_key(cluster, season)` — metric-blind. Pairs source (`get_pairs_for_bucket`) is also metric-blind.

**Call sites under P9C L3:**
- `get_calibrator` L171 (primary bucket refit when stored model has stale input_space)
- `get_calibrator` L189 (on-the-fly fit when pair count crosses level3)
- `maybe_refit_bucket` L322 (explicit refit API)

**Consequence:** For `temperature_metric="low"`, a refit triggered by any of the above writes the resulting model into legacy `platt_models` under `{cluster}_{season}`. The next HIGH call for same bucket:
1. Tries v2 first (metric-aware) — may miss.
2. Falls back to legacy at line 167-168 — and reads the LOW-fitted model as HIGH.

**This is a partial rollback of the L3 CRITICAL fix via a different code path.** The READ path is metric-safe; the ON-THE-FLY PERSISTENCE path silently cross-contaminates. Because pair data is metric-blind (no `temperature_metric` in `platt_pairs`), even the pair pool itself is probably mixed, meaning refits produce models that are neither cleanly HIGH nor cleanly LOW — a different structural bug below the one this commit closed.

**Severity:** Latent. Would not manifest until (a) LOW pair data accumulates enough to trigger level3 refit AND (b) a HIGH request misses v2 for the same bucket. Under current Golden-Window state (v2 largely empty, LOW pair flow just starting), this is dormant. But once data starts flowing in Phase 10, it becomes active.

**Recommended antibody:** Add a test that calls `get_calibrator(temperature_metric="low")` with pair data forcing an on-the-fly refit, then reads legacy `platt_models` directly and asserts the legacy table is NOT populated with LOW rows (or, alternatively, that `save_platt_model` is replaced by `save_platt_model_v2` inside `_fit_from_pairs` for metric-safe persistence).

---

## Is dual-track main line actually closed?

**No — not yet structurally closed, even after P9C.** Two gaps remain:

1. **R-CC.2 checkbox** means DT#7's evaluator wiring is unguarded; it depends on developer discipline to keep the call-site intact. An immune system with gaps.
2. **`_fit_from_pairs` legacy persistence** means the L3 fix is a read-side fence with no corresponding write-side fence. Once LOW pair data flows at scale, cross-metric contamination returns via the refit path.

The READ surface looks closed (L3 fix, v2-first, metric-aware fallback). The WRITE surface via on-the-fly refit is still metric-blind. Per Fitz Constraint #4 (data provenance > code correctness): the code path is correct for the already-stored v2 case, but the provenance of newly-fitted models is lost when they hit the legacy table.

**What would truly close dual-track main line:**
- Promote `_fit_from_pairs` to take `temperature_metric` and call `save_platt_model_v2`, OR
- Add an assertion/antibody that legacy `platt_models` is immutable going forward (any write raises).
- Strengthen R-CC.2 to invoke the evaluator candidate gate end-to-end with a flagged v2 row, not just the helper in isolation.
- Reconcile the 13-vs-12 antibody count (either add the missing one or correct the claim).

---

**Verdict: ITERATE.** Two MAJOR findings (R-CC.2 checkbox, `_fit_from_pairs` latent pollution) plus one MINOR (antibody count miscounts). P9C is a substantial structural advance — the L3 READ fix is real and the R-BZ antibody set catches it — but the main-line closure claim is premature. One more small iteration (P9C.1) to fix the write-side metric-blindness in `_fit_from_pairs` and to upgrade R-CC.2 from component-probe to relationship-probe would genuinely close the spine.
