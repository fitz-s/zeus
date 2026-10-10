# Dense observations: executable finite-state oracle

This directory supplies a mathematical reference and synthetic checks. It does
**not** enable a live probability path. The accompanying authority document
specifies the continuous-state production model and the remaining acceptance work:
[theory and implementation contract](../../docs/authority/day0_dense_observation_probability.md).

## Run

From the repository root, using Python 3.10 or later with NumPy and SciPy:

```bash
python artifacts/dense_obs_theory/test_reference.py --json-out artifacts/dense_obs_theory/synthetic_results.json
```

The runner uses `unittest` directly, so the repository's pytest fixtures and live
database setup are not imported. It makes no network calls or database writes.
The checked-in JSON records the seeds, package versions, measurements and scope.

## What is exact

`reference.py` computes the full-day HIGH or LOW distribution for a **declared
finite hidden-state model with a finite, exhaustive list of candidate event
instants**. Each state can include temperature and a weather/sensor regime. Each
event has a state-dependent distribution over report marks, including no report,
an ineligible report, or an eligible integer settlement value. A mark is not
assumed to occur just because its temperature might be extreme. Arbitrary
time-dependent transitions are supported.

The forward state is `(hidden_state, sampled_extremum)`. Its transition sums over
the previous hidden state and every possible report mark, updating the extremum
only for an eligible source mark. `None` denotes a day with no eligible row; that
outcome is assigned to the contractual no-data winning bracket by `bin_vector`.
This is separate from an integer minimum, including for LOW. A failed source
request is not proof that this outcome occurred.

The sum is mathematically exact for that finite model; actual calculations use
floating-point `logsumexp` and `logaddexp`. An independent Boolean reachability
recursion tracks structural support. Exhaustive complete-path enumeration checks
the calculation for both metrics. There is no Monte Carlo approximation in
`infer`.

One slot currently carries one mark. Multiple reports at exactly the same instant
must be represented by an augmented mark/state containing their joint effect.
History-dependent SPECI triggering requires sufficient weather/report history in
the state. A Bernoulli grid is not an exact representation of arbitrary
continuous-time SPECI arrival times. Zero SPECI probability is supported.

## Main interfaces

| Interface | Purpose |
| --- | --- |
| `FiniteMarkedModel` | Frozen decision-model inputs: event times, states, prior, transitions, report kernels and snapshot availability clock. |
| `report_fact` | An immutable/final-authority source mark; attaches the only hard settlement-boundary certificate. |
| `interval_evidence` | Integer-censored predictor observation; does not claim the predictor is the final source product. |
| `dense_evidence` | Full-support Gaussian or Gaussian/Student-t emission, optionally integrated over a dense measurement's quantization interval. |
| `nonreceipt_evidence` | Optional state/mark-dependent exponential-lag survival factor for a missing report arrival. |
| `receipt_density_evidence` | Corresponding arrival-density factor for an actually received report. |
| `infer` | Full-day sampled HIGH/LOW distribution conditioned on causal evidence. |
| `infer_mixture` | Member likelihoods update the member prior weights before the mixture is formed. |
| `infer_metar_only`, `infer_optional_dense` | Bit-identical inference when the selected evidence contains no dense observation and all other model inputs match. |
| `dispatch_optional_dense` | Literal legacy-callback branch when no dense evidence is admitted; returns the same legacy object without copying or normalization. |
| `fit_interval_pairs` | Joint maximum likelihood for dense bias, dense noise and latent residual scale under an anchored iid paired-data model. |
| `fit_finite_persistence` | Censored-observation marginal-likelihood fit of persistence in a finite reset-process HMM with anchored sensor parameters. |
| `ou_grid_transition` | Explicitly approximate OU transition between representative temperature cells, with infinite tail cells. |

Evidence is selected with strict `receipt_at < decision_at`. An observation cannot
arrive before its observation instant. Repeated immutable lineage IDs count once;
conflicting duplicate payloads are rejected. A full-day recursion includes past
eligible rows whose values have not yet arrived. Omitting them because their
observation time is before the decision would change the settlement variable.

Arrival likelihoods are optional because ordinary receipt selection is sufficient
only when the omitted arrival mechanism is conditionally ignorable. If latency is
informative, an arrival or survival likelihood is part of the model. The code
rejects multiplying two lifecycle observations of the same arrival variable as
though they were independent. A recorded arrival supersedes earlier nonreceipt
snapshots. The supplied exponential distribution is a synthetic example; it does
not claim to fit the observed bimodal AWC delays.

## Settlement semantics and certificates

`round_half_up` implements `floor(x + 0.5)`, including negative ties. `wrh_row_value`
reproduces the pinned WRH native feed value and hourly visibility predicate:
non-null sea-level pressure **or** raw report text beginning with the station ID.
Therefore a qualifying SPECI can appear in the hourly view. The feed temperature,
already in the contract's requested unit, wins over any differing T-group-derived
predictor value. Unit mismatches are rejected. The explicit raw-report predictor
decoder is a separate function and never overrides the native feed.

`report_fact` assumes the observed source mark is the immutable mark that enters
the terminal settlement tape. A report version that can later be overwritten is
not such a fact. Revisions require an augmented terminal-tape state; the simple
extremum accumulator here does not implement that model. Local-day membership,
station identity, source edition, revision authority and completion are explicit
upstream inputs, not inferred from a temperature reading. Helpers cover half-open
local calendar days and recurring routine minutes across both DST folds.

`InferenceResult` returns log probabilities, a Boolean model-support mask, and
`serving_authorized=False`. `bin_probability` returns both the log probability and
the log complement. It separately identifies zeros/ones forced by a received
source boundary: for example, a known HIGH of 20 excludes bins ending below 20,
and forces the open shoulder `>=20` when that is a listed bin.
The implemented semantic certifier is deliberately conservative and boundary-only;
it is not a complete decision procedure for all settlement-completion proofs.

Other zeros can arise from the toy model's finite support. They are labeled
**model-only**, never certified as settlement-forced. A floating-point display of
1.0 with a finite log complement is not a logical certainty. Dense likelihoods
must remain strictly positive over every declared state; numerical loss of that
support is an error. Positive noise scale is a model-domain assumption, not a
hand-set probability cap.

## Missing, old and biased data

No dense observation gives exactly the same answer as `infer_metar_only` with the
same model. An old but already received informative reading remains evidence.
There is deliberately no timer that erases it. Exact erasure requires a genuine
conditional-independence boundary in the model. A change of sensor can be modeled
through a sensor regime in the state and state-dependent `bias_c`; this reference
does not estimate a real sensor-change process or implement a live drift detector.

Dense readings are conditionally independent given the declared hidden path and
sensor regime in these examples. Correlated errors or one-minute averaging need
additional state and their correct observation functionals. Treating averaged
temperature as an exact instantaneous observation is not justified by these tests.
Positivity must hold for the **joint** conditional likelihood. Positive marginal
noise variances alone do not establish it: a singular shared-noise covariance can
make the joint likelihood vanish on some paths even when each marginal density is
positive. Such an observation model must not be replaced by a product of marginal
likelihoods. Model-only consequences still do not become authority certificates.

The example likelihoods condition on an externally specified forecast/model.
If a real forecast has assimilated an observation being supplied again, its
likelihood must be conditional on that forecast information and shared error.
Assimilation lineage or a fitted joint conditional model is required; these
synthetic examples do not validate independence from a real forecasting system.

## Synthetic evidence and its limits

The test suite checks exact finite path enumeration, weather-driven discretionary
marks, absence and no-data handling for both HIGH and LOW, source rounding and
native-feed precedence, strict receipts and pending reports, informative latency,
lineage deduplication, member evidence weights, no-dense identity, noncoincident
dense grids, structural support, DST boundaries and remote-tail log arithmetic.

Statistical experiments are deliberately separated:

- Exact finite enumeration verifies expected log-loss improvement equals
  conditional mutual information for a correctly specified observation model.
- A seeded iid interval-pair experiment jointly recovers bias, measurement noise
  and latent residual scale. A separate censored/missing-data HMM experiment fits
  the persistence parameter of a finite reset process at irregular time gaps, with
  sensor bias and noise anchored. Neither experiment verifies real sensor identity
  or continuous OU/two-timescale parameter recovery.
- Separate heldout finite-Markov days use oracle parameters fixed independently of
  the evaluation sample. The test reports paired log losses and randomized PIT
  summaries. It is not a real-station or live Zeus A/B result.
- A deliberate unmodeled sensor bias makes the dense-informed forecast worse.
  Adding observations does not protect an incorrectly specified likelihood.

The synthetic count of wrong predictions above `q >= 0.99` is descriptive. When
the baseline has no predictions above that threshold its conditional error rate
is undefined, so this experiment does not prove the proposed live false-certainty
gate. No continuous-process numerical error certificate, empirical SPECI law,
real-station walk-forward calibration or source-revision model is verified here.

`ou_grid_transition` integrates each destination cell exactly from a selected
source-cell representative, then collapses the destination to its representative
on later steps. This is a finite-grid approximation to an OU process. Retaining
tail cells prevents simple tail truncation but does not remove within-cell error.
Grid refinement agreement is not a proof of a numerical error bound.
