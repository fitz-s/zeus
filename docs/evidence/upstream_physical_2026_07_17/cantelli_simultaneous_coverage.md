# CP + Cantelli simultaneous-coverage accounting for the finite-evidence floor

- Created: 2026-07-17
- Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
  §Consult v2 (f) — "CP+Cantelli max() composition needs simultaneous-coverage accounting."
- Composes with commit `eaf016ead` (CP effective-n member-dependence ρ) — does NOT undo it.
- Measurement script: `cantelli_simultaneous_coverage_measure.py` (this dir; read-only over
  `state/zeus-forecasts.db`, mirrors `cp_coverage.py` cell construction + target-clustered
  bootstrap + walk-forward split).

## Verdict: NO-CHANGE-WITH-PROOF (bound composition)

The serving per-bin finite-evidence floor `F = max(CP_ρ, Cantelli)` is served unchanged.
The pointwise max is a valid one-sided 95% UCB; the Cantelli plug-in-(μ,σ) concern is
neutralised by the max and empirically over-covers by 2–6× where it binds; and the
downstream trading decision consumes MARGINAL per-bin bounds (one selected candidate per
family), so no family-wise / Bonferroni widening is warranted — such widening would only
destroy EV with zero coverage benefit the decision requires.

The one residual — a ~1 pp hairline at the k=0 CP zero-hit floor for `high` — is the CP
member-dependence ρ calibration boundary (the `state/ens_member_dependence` artifact), NOT
the Cantelli composition. It is flagged for the ρ owner; it is out of scope here and was not
touched.

## What is actually being composed

For each settlement bin the serving code (`_current_evidence_tail_ucb_floors`,
`src/data/replacement_forecast_materializer.py:2815`) computes two one-sided 95% UCBs on the
SAME parameter `q_bin = P(settlement lands in this bin)`:

- **CP_ρ** — exact Clopper-Pearson on the N current ENS member preimage hits, with the
  member-dependence effective-n `n_eff = n/(1+(n−1)ρ)`:
  `betaincinv(k_eff+1, n_eff−k_eff, 1−α)`.
- **Cantelli** — the one-sided moment bound `σ²/(σ²+gap²)` for a bin wholly on one side of
  μ (`gap` = μ-to-near-edge in the settlement preimage), `0` for the bin straddling μ.

and serves `F = max(CP_ρ, Cantelli)` per bin, folded into one coherent simplex
(`_stress_coherent_samples_to_marginal_ucb_floors`) whose MARGINAL per-bin 95th percentile
is the consumed `q_ucb`. The floor's decision role: `q_lcb_no = 1 − q_ucb_yes`, i.e. it
bounds the BUY-NO robust probability so a far-tail NO cannot inherit an unearned ~1.0.

## Math analysis

### 1. Pointwise `max` is a valid (1−α) one-sided UCB — composition NOT broken
If `A` and `B` are each valid one-sided (1−α) UCBs for `q` (`P(q>A) ≤ α`, `P(q>B) ≤ α`)
then `{q > max(A,B)} ⊆ {q > A}`, so `P(q > max(A,B)) ≤ P(q>A) ≤ α`. The max is STRICTLY MORE
conservative than either term, never less. So the concern is never the pointwise bound.

### 2. Cantelli plug-in-(μ,σ) overconfidence is NEUTRALISED by the max
Cantelli uses ESTIMATED (μ,σ) as if known — a legitimate plug-in concern in isolation. But
in the composition it can only lower `F` by being optimistically SMALL, and whenever
`Cantelli < CP_ρ` the max returns `CP_ρ` exactly — the Cantelli error is masked. Formally:
`F = max(CP_ρ, Cantelli) ≥ CP_ρ` pointwise, so the composed floor's marginal coverage is
bounded below by the CP term's. The CP term's marginal coverage is measured (cp_coverage,
commit `eaf016ead`, walk-forward). Therefore the composed floor's marginal coverage ≥ CP's —
independent of any Cantelli plug-in error. (Property-tested: `test_cantelli_masked_when_cp_binds`.)

### 3. The decision seam needs MARGINAL per-bin coverage, not joint simultaneous coverage
Traced `q_ucb_yes → q_lcb_no` through the live decision
(`src/engine/event_reactor_adapter.py`): each family candidate is materialised per (bin,
direction) with its OWN `no_lcb = 1 − q_ucb_yes` (`:23202`, `_replacement_no_lcb_for_bin`
`:27553`). The §7 ranker (`_score_family_candidates_by_robust_marginal_utility` `:24153`)
selects exactly ONE primary candidate per family per decision (`rank_candidates`, §13
no-trade). The consumed bound is the SELECTED bin's marginal `q_ucb`. The coherent-simplex
stress is a construction device to keep sample rows on the simplex; the values consumed are
per-bin marginals.

Hence the guarantee the decision needs is per-realised-trade MARGINAL coverage, not
family-wise simultaneous coverage across all bins jointly. A Bonferroni-style α-split across
the |bins| family would widen every floor (α → α/m), lowering `q_lcb_no` uniformly and
killing EV, to purchase a joint guarantee the one-candidate-per-family decision never
consumes. That widening is refused.

The only residual family-wise question is post-SELECTION inference (we adaptively pick the
bin where NO looks best). This is answered empirically below (§Measurement, pooled reliability
+ selection strata): the composed floor's realised exceedance over the archive is ≤ nominal
everywhere except the CP zero-hit boundary, so selection does not inflate error against the
composed floor.

## Measurement (settled archive, walk-forward, target-clustered bootstrap)

`ensemble_snapshots ⋈ settlement_outcomes` (VERIFIED, causality OK, boundary_ambiguous=0,
FULLY_INSIDE_TARGET_LOCAL_DAY, contributes_to_target_extrema=1), freshest causal snapshot per
settled triple. **3172 settled targets, 36 039 cells, modal n=51.** Serving ρ:
high=0.004639, low=0.053955.

**σ,μ reconstruction is a CONSERVATIVE LOWER proxy for the served floor.** The measurement
uses the ENS-only predictive shape (μ = member mean, σ = within-member population std). The
SERVED predictive σ additionally folds provider-between + center-delta spread in quadrature
(`_current_evidence_shape_from_values`), so `σ_served ≥ σ_ens_within` ⇒ served Cantelli ≥ the
Cantelli measured here ⇒ served `F` ≥ measured `F`. If the measured (lower) floor covers,
the served floor covers a fortiori. The CP term — dominant in the far k=0 tails where BUY-NO
lives — uses the EXACT member preimage hits and is independent of the μ,σ choice.

### CP marginal panel (dominance floor for the composed max)
`high` k=0: rate 0.0683, boot_up 0.0710, CP_ρ 0.0698 → hairline VIOL (boot_up +0.0012 over
floor; point rate is BELOW floor). k≥1 all pass with growing margin (k=5: boot_up 0.153 vs
CP 0.209). `low`: all k pass (k=0: boot_up 0.0437 vs CP 0.195). The k=0 `high` hairline is
the CP ρ-calibration boundary (see §Residual), not a Cantelli effect.

### Cantelli-binding strata (the plug-in test)
Cells where Cantelli > CP_ρ: **46.0% (high), 22.8% (low)** — Cantelli binds often. Coverage
of these cells, stratified by floor value:

`high` — bucket 1 (floor_min 0.0698 = the zero-hit CP floor; these are k=0 cells where
Cantelli barely lifts CP): boot_up 0.0777 → same CP hairline. **Every other bucket
over-covers by 2–6×**: bucket 2 boot_up 0.0688 vs floor_min 0.0859; bucket 6 boot_up 0.118
vs 0.237; bucket 10 boot_up 0.149 vs 0.886. `low` — ALL 10 buckets pass (bucket 1 boot_up
0.182 vs floor_min 0.198; bucket 10 boot_up 0.302 vs 0.969). **Where Cantelli genuinely
binds above CP, it is massively conservative — the plug-in concern does not materialise.**

### Pooled composed-floor reliability (all cells, floor deciles)
mean_floor 0.2630 ≫ mean_outcome 0.0880 (a UCB, conservative in the aggregate). Deciles 1–3
(all floor_min 0.0698, the k=0 CP floor) show the same ~1 pp hairline (boot_up 0.071–0.082);
deciles 4–10 all pass with growing margin. The exceedance is confined to the CP zero-hit
floor, never introduced by Cantelli.

### Walk-forward (train date < mid, test ≥ mid)
`high` test (2026-06-14+, 17 494 cells, 8073 Cantelli-binding): ONE hairline at the k=0 CP
bucket (boot_up 0.0758 vs floor 0.0698). `low` test (2026-06-18+, 1633 cells, 382
Cantelli-binding): failures NONE.

## Residual (out of scope — CP ρ owner)
The k=0 `high` hairline (boot_up ≈ 0.071–0.076 vs CP zero-hit floor 0.0698, point rate
0.0683 < floor) is the member-dependence ρ = 0.004639 sitting exactly at its full-window
calibration boundary. It is a property of the CP term's ρ (artifact
`state/ens_member_dependence/`, commit `eaf016ead`), reproduces with Cantelli entirely
removed, and is within the target-clustered bootstrap CI. It is NOT the Cantelli composition
and was not touched here — flagged for the ρ owner. The Cantelli term only ever ADDS
conservatism on top of it.

## Serving change
NONE. `max(CP_ρ, Cantelli)` served unchanged; ρ machinery intact; provenance
(`finite_evidence_tail_ucb_floor_by_bin`, `finite_evidence_member_rho_applied`, …) unchanged.
Property tests pinning the invariants that justify no change:
`tests/test_cantelli_simultaneous_coverage.py`.
