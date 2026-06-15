# Stage 7a — instruments: NO as a joint-complement payoff basket

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (lines 590-617);
docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD; DRIFT-LEDGER
MAJOR — the NO lower bound is NOT `1 - q_ucb_yes` and NOT
`probability_uncertainty.no_side_samples`).

## What was built

`src/probability/instruments.py` — the `Instrument` dataclass plus the YES/NO fair
value and NO lower-bound derivations, all GREENFIELD (no live file touched). The
economic content of an instrument is a PAYOFF VECTOR over the COMPLETE Omega:

- `Instrument` (frozen dataclass, spec lines 594-599) — fields verbatim:
  `instrument_id`, `bin_id`, `side: Literal["YES","NO"]`, `direct_token_id: str|None`.
- `Instrument.payoff_vector(omega) -> np.ndarray` (spec lines 601-609) —
  `YES -> e_i` (1 at bin i, 0 elsewhere); `NO -> 1 - e_i` (1 everywhere, 0 at bin i).
  So a NO is, by construction, a basket of all the OTHER bins' YES: it wins whenever
  any other bin settles.
- `fair_yes(joint_q, bin_id) -> float` — `q[i]` (spec line 615).
- `fair_no(joint_q, bin_id) -> float` — `1 - q[i]` (spec line 616) = `Σ_{j!=i} q[j]`
  exactly, since `Σq = 1`. The basket value of all the other YES, off the single
  normalized point q.
- `no_lcb(band, bin_id, *, alpha=None) -> float` — `np.quantile(1 - band.samples[:, i],
  alpha)` (spec line 617). The joint complement of the SAME row-normalized
  `JointQBand.samples` the YES side uses. `alpha` defaults to `band.alpha` so the NO
  bound is taken at the SAME tail as the YES `q_lcb` the band already carries.
- `InstrumentError` — fail-closed for a `bin_id` not in the partition or a degenerate
  explicit `alpha`.

`tests/probability/test_no_basket_semantics.py` — the two spec-named RED-on-revert
tests plus a fail-closed test.

## Spec lines implemented

| Spec lines | Symbol / behavior |
|---|---|
| 594-599 | `Instrument` dataclass, exact field names |
| 601-609 | `payoff_vector`: `YES = e_i`, `NO = 1 - e_i` |
| 611 | NO probability is a direct consequence of the payoff vector and `Σq = 1`, not a special formula |
| 615 | `fair_yes_i = q[i]` |
| 616 | `fair_no_i = 1 - q[i]` |
| 617 | `no_lcb_i = np.quantile(1 - band.samples[:, i], alpha)` |

## The corrected transformation (operator law: the bad output is unconstructable)

The two defects the spec replaces are made impossible by the design, not caught by a
gate:

- **NOT `1 - q_ucb_yes` (the live defect at `event_reactor_adapter.py:9955`)** — the
  NO bound is read from `band.samples`, the row-normalized joint matrix, as the
  per-draw complement. There is no flipped-ucb path: `no_lcb` has exactly one source.
- **NOT a separately-sampled `probability_uncertainty.no_side_samples`** — there is no
  independent NO sample set. The NO bound is the joint complement of the YES draw
  matrix, so YES and NO bounds are coherent over the same `Σq = 1` rows by
  construction (they cannot disagree about how much mass is "elsewhere").

## Drift resolved (recorded per operator law)

1. **`omega.index(self.bin_id)` does not exist on the live `OutcomeSpace`.** The spec
   (line 603) writes `i = omega.index(self.bin_id)`, but the live
   `src/probability/outcome_space.py` `OutcomeSpace` exposes `bins` (a tuple of
   `OutcomeBin`, each with `bin_id`) and carries NO `index` method. **Resolution
   (toward the live type, per the drift ledger "prefer Actual-live" directive):** the
   bin index is the POSITION of `bin_id` within `omega.bins` — the canonical alignment
   `JointQ.q` and `JointQBand.samples` are already keyed on (`q[i]` / `samples[:, i]`
   is the mass of `omega.bins[i]`). Implemented as a private `_bin_index(omega,
   bin_id)` helper that scans `omega.bins`; a `bin_id` not in the partition fails
   closed with `InstrumentError` rather than silently selecting the wrong bin.

2. **The NO bound's defect is the SOURCE MATRIX, not a per-column flip — a
   mathematical finding that reshaped test #2.** During implementation I verified that
   for a single column, `np.quantile(1 - x, alpha) == 1 - np.quantile(x, 1 - alpha)`
   is an *exact algebraic identity* (quantile reflection). Therefore the spec's
   joint-complement form `quantile(1 - samples[:, i], alpha)` and the live
   `1 - q_ucb_yes_i` are the **same number on a given sample matrix** — and for a
   dominant modal bin like b25 they coincide to machine precision (verified: both
   `0.533847`). The genuine correction the spec encodes is therefore NOT "replace a
   flip with a complement"; it is **which matrix the NO bound is read from**: only on
   the row-normalized `band.samples` is `1 - samples[k, i]` equal to the summed mass of
   all the other bins on draw k (`Σ_{j!=i} samples[k, j]` — the NO basket payoff). On
   the un-row-normalized `_build_fused_q_bounds` draws (the source behind the live
   `1 - q_ucb_yes`) that basket identity FAILS. The RED-on-revert test was rewritten to
   assert this basket-coherence invariant (holds on `band.samples`, fails on an
   un-normalized window matrix built from the same seeded draws) and the source
   provenance (read bit-for-bit from `band.samples`, not a separate NO sample set) —
   rather than a numeric divergence between `no_lcb` and `1 - q_ucb_yes` on the same
   band, which is algebraically impossible and would have been a fragile/false test.

3. **`np.clip(probs, 0.0, 1.0)` vs the live point-q `np.clip(probs, 0.0, None)`.** The
   spec snippet (line 539) clips masses to `[0, 1]`; the already-built
   `src/probability/joint_q.py::build_joint_q` clips to `[0, +inf)` then normalizes.
   This is in the point-q module (already built/tested, not this module) — noted only
   for completeness; `instruments.py` consumes the built `JointQ.q` / `JointQBand`
   unchanged and adds no clip of its own.

## Test design — RED-on-revert proof

- `test_no_payoff_vector_wins_on_every_other_bin` (spec lines 601-609): asserts the NO
  payoff is exactly `1 - e_i` (wins on n-1 bins, loses on bin i), that `yes + no ==
  all-ones`, and that `payoff @ q` equals `q[i]` (YES) / `1 - q[i]` (NO) = the basket
  value `Σ_{j!=i} q[j]`. Verified RED: reverting NO to a single-bin scalar complement
  (`e[i] = 1.0` like YES) fails the test.
- `test_no_probability_and_lcb_come_from_joint_complement_samples` (spec lines
  611-617): asserts `fair_no == 1 - q[i]`, `no_lcb == quantile(1 - band.samples[:, i],
  alpha)` bit-for-bit, the basket-coherence invariant `1 - band.samples[:, i] ==
  Σ_{j!=i} band.samples[:, j]` (holds on the simplex band; shown to FAIL on an
  un-normalized window matrix), and source provenance (read from `band.samples`).
  Verified RED: reverting `no_lcb` to a separately-drawn NO sample set fails the test.
- `test_no_lcb_rejects_degenerate_alpha`: fail-closed on `alpha` ∉ (0,1) and on an
  unknown `bin_id`.

## Test results

```
$ /Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/probability/test_no_basket_semantics.py
...                                                                      [100%]
3 passed in 3.93s
```

Full probability suite (no regressions in the sibling q-core modules):

```
$ /Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/probability/
..................                                                       [100%]
18 passed in 7.59s
```

Money-path + live-inference unaffected:

```
$ /Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/money_path tests/strategy/live_inference
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.13s
```

RED-on-revert verification (each broken transform was temporarily injected, the test
confirmed to fail, then the module restored byte-for-byte):

- NO payoff -> single-bin `e_i` complement: `test_no_payoff_vector_wins_on_every_other_bin` FAILED (as required).
- `no_lcb` -> separately-sampled NO belief: `test_no_probability_and_lcb_come_from_joint_complement_samples` FAILED (as required).
- Module restored: `diff` clean; full probability suite green.

## Files written

- `src/probability/instruments.py` (new)
- `tests/probability/test_no_basket_semantics.py` (new)
- `docs/rebuild/impl_stage7_instruments.md` (this report)
