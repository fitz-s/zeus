Verdict: replace Zeus’s forecast→q→decision path with one settlement-station PredictiveDistribution authority feeding one family-level Arrow-Debreu decision kernel; confidence high on the implementation design, medium only on the exact current live Tokyo lane until Claude dumps a live candidate receipt.

You are right: the prior answer was a review memo. This is the build spec. Claude Code should be able to turn this into modules, tests, and deletions without reconstructing the architecture.

The implementation target is not “fix EDLI” or “fix q_lcb.” The implementation target is this one invariant:

For every live candidate, the receipt must prove:

fresh settlement-station inputs -> one debiased predictive distribution -> one complete Ω -> one normalized joint q -> one coherent joint uncertainty sample -> one executable family book -> one payoff-vector decision -> one liquidation-aware lifecycle

Any branch that cannot produce that receipt is not money-eligible.

The current code proves why this is necessary. The reactor still has an EMOS route that calls build_emos_q from the event snapshot path, while the fallback/day0 branch can still call _maybe_apply_edli_bias_correction; the bias correction subtracts model_bias_ens.effective_bias_c from members when enabled; replacement q bounds are built from raw per-bin percentiles; replacement NO lower bound uses 1 - q_ucb_yes; native executable cost only selects one native side ladder; utility_ranker already contains the correct full-family payoff-matrix objective; exit lifecycle sells the current token directly through place_sell_order. Those are implementation seams, not abstract observations. 
GitHub
+6
GitHub
+6
GitHub
+6

Findings

[BLOCKER] forecast authority split — src/engine/event_reactor_adapter.py:10944-11091,11526-11608 — impact: EMOS and fallback/day0 correction lanes can produce different μ*/σ/q semantics for the same family. — concrete fix: introduce src/forecast/predictive_distribution_builder.py as the only live builder; make event_reactor_adapter call it once and delete live money eligibility for direct _maybe_apply_edli_bias_correction. — verify locally: pytest -q tests/forecast/test_single_predictive_distribution_authority.py::test_every_live_path_returns_same_receipt_contract

[BLOCKER] stale/oversized de-bias can reach members — src/engine/event_reactor_adapter.py:11526-11608 — impact: _maybe_apply_edli_bias_correction subtracts effective_bias_c from members, so a stale negative bias warms the forecast center before q. — concrete fix: move all bias reading into DebiasAuthority, require freshness, settlement-station provenance, product match, walk-forward OOS do-no-harm, and realized-residual magnitude activation before any shift is applied. — verify locally: pytest -q tests/forecast/test_debias_authority.py::test_tokyo_minus_4847_bias_refused_against_realized_residual_band 
GitHub

[BLOCKER] μ* can leave fresh member consensus — src/calibration/emos.py:emos_predictive, src/calibration/emos_q_builder.py:build_emos_q — impact: EMOS computes a calibrated center from a + b*xbar, and offsets can warm/cool it, but the opened q builder does not enforce the fresh debiased member envelope. — concrete fix: after all calibration and before q integration, enforce μ* ∈ [min(debiased_members), max(debiased_members)] unless a day0 observed extreme licenses leaving the envelope. — verify locally: pytest -q tests/forecast/test_center_envelope.py::test_mu_star_cannot_select_tokyo_26_when_fresh_members_are_20_to_23 
GitHub
+1

[BLOCKER] day0 observation is not the authoritative distribution input — src/engine/event_reactor_adapter.py:day0 branch around 11078-11091 — impact: observed running highs/lows can affect a side sampler without proving that μ*, σ, q, and q_lcb were all reconciled to the same observed extreme. — concrete fix: implement Day0ExtremeConditioner inside the predictive distribution and q integration layer; day0 observations become hard support constraints, not a segregated lane. — verify locally: pytest -q tests/forecast/test_day0_extreme_conditioner.py::test_observed_high_makes_lower_bins_impossible_and_clamps_center

[HIGH] EMOS μ-offset lacks consumer-side realized-residual bound — src/calibration/emos_q_builder.py:108 — impact: activated offsets shift μ via mu_c = mu_c - offset, but the live seam must not trust artifact activation alone. — concrete fix: DebiasAuthority validates activated offset rows at read time using the same freshness/OOS/residual-band contract. — verify locally: pytest -q tests/forecast/test_debias_authority.py::test_emos_offset_reader_refuses_overlarge_or_stale_shift 
GitHub

[BLOCKER] σ floor is not one universal live contract — src/data/replacement_forecast_materializer.py:1119, src/calibration/emos_q_builder.py:settlement floor branch — impact: replacement fusion has a hard max(1.0, ...) sigma construction while EMOS/raw builders have their own floor logic; a fallback can serve a narrower distribution than realized settlement error. — concrete fix: implement RealizedSigmaAuthority and require sigma_pred >= realized_walk_forward_rmse(city, metric, season/regime, lead_bucket) on every live q path. — verify locally: pytest -q tests/forecast/test_sigma_authority.py::test_all_live_paths_floor_sigma_by_realized_walk_forward_error 
GitHub
+1

[BLOCKER] q_lcb is not a coherent joint object — src/data/replacement_forecast_materializer.py:1419-1441 — impact: _build_fused_q_bounds computes np.percentile(probs, 5, axis=0) over raw per-bin masses, so the lower-bound vector is not derived from row-normalized joint q draws. — concrete fix: implement JointQBand where each sample row integrates all bins and renormalizes to Σq=1 before any marginal quantile is read. — verify locally: pytest -q tests/probability/test_joint_q_band.py::test_every_band_sample_is_simplex_and_modal_lcb_does_not_collapse 
GitHub

[BLOCKER] replacement NO is a band edge, not a basket — src/engine/event_reactor_adapter.py:9955 — impact: replacement NO lower bound uses 1 - q_ucb_yes, which is only valid if q_ucb_yes came from the same row-normalized joint samples and still says nothing about executable basket routes. — concrete fix: define NO_i as payoff vector 1 - e_i; compute q_no_samples = 1 - q_yes_samples from JointQBand.samples; route NO through direct and synthetic sibling-YES books. — verify locally: pytest -q tests/probability/test_no_basket_semantics.py::test_no_probability_and_lcb_come_from_joint_complement_samples 
GitHub

[HIGH] settlement preimage can be dropped at EMOS seam — src/calibration/emos_q_builder.py:132-140 — impact: build_emos_q calls bin_probability_settlement without passing the city’s rounding rule, while settlement semantics explicitly distinguish WMO half-up from HK oracle truncation. — concrete fix: make OutcomeSpace.resolution.rounding_rule mandatory and pass it into every q integration call; remove default rounding from money-path q builders. — verify locally: pytest -q tests/probability/test_settlement_preimage_threading.py::test_hk_oracle_truncate_reaches_emos_and_band_builders 
GitHub
+1

[BLOCKER] executable cost is native-token only — src/strategy/live_inference/executable_cost.py:_levels_for_direction — impact: cost selection returns only YES ask, NO ask, YES bid, or NO bid for one market, so neg-risk basket and conversion routes cannot be represented. — concrete fix: add FamilyBook and NegRiskRouteSet over all sibling markets; keep native cost as a leaf primitive, not the family routing authority. — verify locally: pytest -q tests/execution/test_negrisk_route_set.py::test_buy_no_uses_cheaper_sibling_yes_basket_when_neg_risk_true 
GitHub

[BLOCKER] scalar trade score is still probability-minus-cost — src/strategy/live_inference/trade_score.py — impact: robust_trade_score computes min(q_5pct - cost, q_point - stress_cost) * p_fill, which cannot represent payoff-vector EV, family exposure, NO baskets, or arbitrage bundles. — concrete fix: demote scalar score to telemetry; selection uses PayoffVectorDecision.edge_lcb = quantile(q_sample · payoff_vector - executable_cost). — verify locally: pytest -q tests/decision/test_payoff_vector_edge.py::test_scalar_q_minus_cost_cannot_select_candidate 
GitHub

[BLOCKER] vector utility exists but live stake can be overwritten by scalar Kelly — src/engine/event_reactor_adapter.py:8625-8632, src/strategy/utility_ranker.py — impact: the repo already has FamilyPayoffMatrix and ΔU, but the reactor code path can set family total from binary f_star. — concrete fix: family total must be global_fractional_kelly * vector_optimal_stake_usd; binary Kelly may only be a diagnostic. — verify locally: pytest -q tests/decision/test_vector_sizing_authority.py::test_family_total_is_vector_argmax_times_global_fractional_kelly 
GitHub
+1

[HIGH] market anchor is a one-sided cap, not a calibration/coherence layer — src/strategy/live_inference/market_anchor.py — impact: the code describes a one-sided NO q_lcb cap using market NO price as probability, which can suppress trades but does not make model/market disagreement auditable as a calibration incident. — concrete fix: implement MarketCoherenceReport from de-frictioned family-implied q; block live money on deep-book order-of-magnitude disagreement and emit a calibration incident, not a silent cap. — verify locally: pytest -q tests/decision/test_market_coherence.py::test_deep_book_tokyo_q_047_vs_ask_0001_blocks_before_scoring 
GitHub

[HIGH] exit is current-token sell, not liquidation value — src/execution/exit_lifecycle.py:1242-1342 — impact: exit builds an intent for the current token and calls place_sell_order; no opened path computes direct-vs-conversion-vs-hold liquidation value. — concrete fix: implement LiquidationValueEngine over the family position vector and choose max of direct sell, conversion/basket sell, and hold-to-redeem. — verify locally: pytest -q tests/execution/test_liquidation_value_engine.py::test_no_position_chooses_conversion_basket_when_direct_bid_is_worse 
GitHub

End-to-end implementation architecture

Build this as a new spine, then route the old adapter into it. Do not keep parallel “legacy but maybe inert” authorities.

The new spine is:

EventResolution
→ OutcomeSpace
→ FreshModelSet
→ DebiasAuthority
→ Day0ExtremeConditioner
→ PredictiveDistribution
→ JointQ
→ JointQBand
→ FamilyBook
→ InstrumentRouteSet
→ PayoffVectorDecision
→ MarketCoherenceReport
→ SizingDecision
→ ExecutionIntent
→ LiquidationValueExit

The event reactor becomes an orchestrator. It should not compute μ*, q_lcb, scalar edge, bias correction, or family route economics inline.

Forecast center μ*

Create src/forecast/types.py:

Python
Run
@dataclass(frozen=True)
class ForecastCase:
    city: str
    city_id: str
    station_id: str
    settlement_source_type: str
    target_local_date: date
    metric: Literal["high", "low"]
    issue_time_utc: datetime
    lead_hours: float
    season: str
    regime_key: str
    unit: Literal["C", "F"]
    resolution: EventResolution
    family_id: str
    source_cycle_time_utc: datetime

@dataclass(frozen=True)
class RawModelMember:
    model_id: str
    product_id: str
    source_run_id: str
    source_cycle_time_utc: datetime
    available_at_utc: datetime
    value_native: float
    station_mapping_id: str
    raw_forecast_artifact_id: str
    data_version: str

@dataclass(frozen=True)
class FreshModelSet:
    case: ForecastCase
    members: tuple[RawModelMember, ...]
    member_values_native: np.ndarray
    min_native: float
    max_native: float
    model_set_hash: str

Create src/forecast/debias_authority.py:

Python
Run
@dataclass(frozen=True)
class BiasArtifact:
    artifact_id: str
    authority: Literal["SETTLEMENT_STATION_WALK_FORWARD_V1"]
    city: str
    station_id: str
    metric: Literal["high", "low"]
    season: str
    regime_key: str
    lead_bucket: str
    product_set_hash: str
    model_id: str | None
    training_start_utc: datetime
    training_cutoff_utc: datetime
    valid_until_utc: datetime
    n: int
    residual_mean_native: float
    residual_std_native: float
    residual_se_native: float
    proposed_shift_native: float
    oos_crps_before: float
    oos_crps_after: float
    oos_logscore_before: float | None
    oos_logscore_after: float | None
    station_mapping_id: str
    source_hash: str

@dataclass(frozen=True)
class AppliedDebias:
    artifact_ids: tuple[str, ...]
    per_member_shift_native: tuple[float, ...]
    aggregate_shift_native: float
    trailing_residual_mean_native: float
    trailing_residual_std_native: float
    activation_status: Literal[
        "APPLIED",
        "NO_ARTIFACT",
        "STALE_REFUSED",
        "PRODUCT_MISMATCH_REFUSED",
        "STATION_MISMATCH_REFUSED",
        "OOS_HARM_REFUSED",
        "MAGNITUDE_REFUSED",
        "LOW_N_REFUSED",
    ]
    reason: str

The only public method is:

Python
Run
class DebiasAuthority:
    def apply(self, case: ForecastCase, models: FreshModelSet) -> tuple[np.ndarray, AppliedDebias]:
        ...

Implementation rules:

First, all raw members are normalized to the settlement unit. No artifact may apply if its product/station/source mapping differs from the member. The current EDLI branch reads effective_bias_c keyed by season, metric, data version, authority, and error model family, then subtracts it from members; that behavior becomes illegal outside DebiasAuthority. 
GitHub

Second, the activation rule is a model-validity rule:

Python
Run
fresh = artifact.training_cutoff_utc >= case.issue_time_utc - timedelta(days=3)
right_station = artifact.station_id == case.station_id
right_product = artifact.product_set_hash == models.model_set_hash or artifact.model_id in member.model_id
enough_n = artifact.n >= min_n(case)
no_harm = artifact.oos_crps_after <= artifact.oos_crps_before + crps_tolerance(case)
magnitude_ok = (
    abs(artifact.proposed_shift_native - artifact.residual_mean_native)
    <= N_SIGMA_BIAS * max(artifact.residual_std_native, sigma_floor_epsilon)
)

Use N_SIGMA_BIAS = 2.0 for live activation. This is not a downstream cap; it is refusal to serve a model artifact whose claimed correction is not supported by realized settlement residuals. Tokyo −4.847°C against a trailing residual around −0.33°C must fail MAGNITUDE_REFUSED.

Third, de-bias happens once. It can be per-model if artifacts are per-model, or aggregate if the model set only supports aggregate station representativeness, but it must be applied in one place. No EMOS offset, EDLI per-city row, grid-representativeness row, and raw replacement correction may each shift the center independently. If multiple artifacts are available, DebiasAuthority chooses exactly one correction basis using a deterministic priority order:

per_model_station_walk_forward > model_family_station_walk_forward > city_station_representativeness > no_debias

It returns the chosen artifact ID and marks all rejected artifacts in telemetry.

Create src/forecast/center.py:

Python
Run
@dataclass(frozen=True)
class CenterEstimate:
    mu_native: float
    raw_consensus_native: float
    debiased_consensus_native: float
    debiased_member_min_native: float
    debiased_member_max_native: float
    center_method: Literal["WEIGHTED_HUBER_CONSENSUS", "SHRUNK_EMOS", "RAW_FALLBACK"]
    center_status: Literal["OK", "ENVELOPE_FALLBACK", "DAY0_CLAMPED", "REFUSED"]
    weights_by_model: Mapping[str, float]
    reason: str

The center algorithm should be:

Read fresh members for the exact target family.

Apply DebiasAuthority once.

Compute a robust consensus:

Python
Run
weights = walk_forward_model_weights(case, members)  # shrink to equal weights by n/SE
mu_consensus = weighted_huber_location(debiased_values, weights)

Optional EMOS can propose a center, but only as a shrinkage residual around the debiased consensus:

Python
Run
mu_emos = a + b * xbar
mu_candidate = shrink(mu_emos, toward=mu_consensus, strength=emos_oos_strength)

Enforce the envelope:

Python
Run
lo = np.min(debiased_values)
hi = np.max(debiased_values)

if not lo <= mu_candidate <= hi:
    mu_candidate = mu_consensus
    center_status = "ENVELOPE_FALLBACK"

assert lo <= mu_candidate <= hi

The current build_emos_q call accepts raw members and uses EMOS output; the new design does not allow EMOS to directly become live μ unless the envelope proof passes. 
GitHub
+1

Create src/forecast/day0_conditioner.py:

Python
Run
@dataclass(frozen=True)
class Day0ObservationState:
    observed: bool
    station_id: str
    source: str
    samples_count: int
    latest_observed_at_utc: datetime | None
    observed_high_native: float | None
    observed_low_native: float | None
    observed_extreme_native: float | None
    raw_observation_hash: str | None

@dataclass(frozen=True)
class Day0Conditioning:
    active: bool
    observed_extreme_native: float | None
    support_lower_native: float | None
    support_upper_native: float | None
    center_before_native: float
    center_after_native: float
    status: Literal["NO_DAY0", "HIGH_CLAMPED", "LOW_CLAMPED", "OBS_SOURCE_MISSING_REFUSED"]

For high markets:

Python
Run
if obs.observed_high_native is not None:
    mu_after = max(mu_before, obs.observed_high_native)
    support_lower = obs.observed_high_native

For low markets:

Python
Run
if obs.observed_low_native is not None:
    mu_after = min(mu_before, obs.observed_low_native)
    support_upper = obs.observed_low_native

But the q distribution should not just shift μ. It must condition the settlement random variable:

For a high market, settlement Y = max(observed_high_so_far, X_remaining).
For a low market, settlement Y = min(observed_low_so_far, X_remaining).

The q integrator must implement that support transformation. For high:

Python
Run
def probability_high_day0_bin(obs_high, lo, hi, normal_cdf):
    if hi <= obs_high:
        return 0.0
    if lo <= obs_high < hi:
        return normal_cdf(hi)  # all remaining values below hi settle into current observed bin
    return normal_cdf(hi) - normal_cdf(lo)

For low:

Python
Run
def probability_low_day0_bin(obs_low, lo, hi, normal_cdf):
    if lo >= obs_low:
        return 0.0
    if lo < obs_low <= hi:
        return 1.0 - normal_cdf(lo)
    return normal_cdf(hi) - normal_cdf(lo)

This is how observed day0 extremes become ground truth and kill impossible bins. Tokyo high observed around 21 cannot coexist with a 26 center unless fresh future/remaining distribution supports that move.

Create src/forecast/predictive_distribution_builder.py:

Python
Run
@dataclass(frozen=True)
class PredictiveDistribution:
    case: ForecastCase
    mu_native: float
    sigma_native: float
    debiased_members_native: tuple[float, ...]
    member_min_native: float
    member_max_native: float
    center: CenterEstimate
    debias: AppliedDebias
    day0: Day0Conditioning
    sigma_components: SigmaComponents
    distribution_family: Literal["NORMAL", "DAY0_HIGH_MAX_NORMAL", "DAY0_LOW_MIN_NORMAL"]
    live_eligible: bool
    ineligibility_reason: str | None
    identity_hash: str

This object is the only input to q.

Predictive width σ

Create src/forecast/sigma_authority.py:

Python
Run
@dataclass(frozen=True)
class SigmaFloorArtifact:
    artifact_id: str
    authority: Literal["SETTLEMENT_RESIDUAL_WALK_FORWARD_SIGMA_V1"]
    city: str
    station_id: str
    metric: Literal["high", "low"]
    season: str
    regime_key: str
    lead_bucket: str
    training_cutoff_utc: datetime
    valid_until_utc: datetime
    n: int
    rmse_native: float
    mad_sigma_native: float
    crps_calibration_status: str
    source_hash: str

@dataclass(frozen=True)
class SigmaComponents:
    raw_member_spread_native: float
    model_dispersion_native: float
    center_parameter_se_native: float
    station_representativeness_sigma_native: float
    day0_remaining_process_sigma_native: float
    realized_floor_native: float
    sigma_before_floor_native: float
    sigma_after_floor_native: float
    artifact_id: str

The algorithm:

Python
Run
sigma_ensemble = weighted_spread(debiased_members, weights)
sigma_model = emos_or_walkforward_dispersion(case, debiased_members)
sigma_param = center_parameter_uncertainty(case, debiased_members, debias)
sigma_station = station_representativeness_sigma(case)
sigma_day0 = remaining_day_process_sigma(case, obs_state)

sigma_before_floor = sqrt(
    sigma_model**2
    + sigma_param**2
    + sigma_station**2
    + sigma_day0**2
)

floor = realized_sigma_floor(case)
sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)

No live path may use a constant 1.0 floor as the final authority. The opened replacement materializer computes predictive_sigma_c = max(1.0, ...); that becomes an internal candidate component, not the final served σ. 
GitHub

No soft-anchor path is allowed to serve q without σ. If fusion capture is missing, the builder returns:

live_eligible=False, ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING"

or a conservative fallback distribution with sigma = max(global_lead_bucket_floor, realized_floor) and a receipt proving it. It cannot silently serve member-vote q.

This kills the 47%-single-degree spike. If σ is at least realized day-ahead error, one-degree modal mass is bounded. More importantly, μ* can no longer drift to 26 when the fresh debiased consensus is around 21.

q engine, INV-Q1 through INV-Q8

Create src/probability/event_resolution.py:

Python
Run
@dataclass(frozen=True)
class EventResolution:
    city: str
    station_id: str
    settlement_source_type: str
    resolution_source: str
    target_local_date: date
    settlement_timezone: str
    metric: Literal["high", "low"]
    measurement_unit: Literal["C", "F"]
    settlement_step_native: float
    precision: float
    rounding_rule: Literal["wmo_half_up", "oracle_truncate", "floor", "ceil"]
    finalization_local_time: time
    semantics_version: str

Implementation:

Python
Run
def event_resolution_for_city(city, target_date, metric) -> EventResolution:
    sem = SettlementSemantics.for_city(city)
    station_id = city.wu_station if city.settlement_source_type == "wu_icao" else sem.resolution_source
    if not station_id or station_id == "None":
        raise ResolutionError("STATION_ID_MISSING")
    return EventResolution(...)

The existing SettlementSemantics already defines WMO half-up and oracle_truncate, and settlement_preimage_offsets is the right single source for bin preimages. The q engine must consume that object everywhere instead of defaulting to WMO. 
GitHub

Create src/probability/outcome_space.py:

Python
Run
@dataclass(frozen=True)
class OutcomeBin:
    bin_id: str
    condition_id: str
    label: str
    lower_native: float | None
    upper_native: float | None
    yes_token_id: str | None
    no_token_id: str | None
    executable: bool
    rounding_rule: str

@dataclass(frozen=True)
class OutcomeSpace:
    family_id: str
    resolution: EventResolution
    bins: tuple[OutcomeBin, ...]
    topology_hash: str

    def validate(self) -> None:
        assert len(self.bins) >= 2
        assert all(b.rounding_rule == self.resolution.rounding_rule for b in self.bins)
        assert covers_complete_mece_partition(self.bins)

Rules:

No mass leak.
No executable-subset renormalization.
No “Other” invented after the fact in the decision layer.
If the venue family is incomplete, live eligibility fails closed at OutcomeSpace.

Create src/probability/joint_q.py:

Python
Run
@dataclass(frozen=True)
class JointQ:
    omega: OutcomeSpace
    q: np.ndarray
    q_by_bin_id: Mapping[str, float]
    predictive_distribution_id: str
    q_source: Literal["SETTLEMENT_STATION_NORMAL_V1", "DAY0_HIGH_MAX_NORMAL_V1", "DAY0_LOW_MIN_NORMAL_V1"]
    q_sum: float
    identity_hash: str

    def assert_valid(self) -> None:
        assert np.all(self.q >= 0)
        assert abs(float(self.q.sum()) - 1.0) <= 1e-9

Point q integration:

Python
Run
def build_joint_q(pd: PredictiveDistribution, omega: OutcomeSpace) -> JointQ:
    probs = []
    for bin in omega.bins:
        lo, hi = settlement_preimage(bin, omega.resolution)
        if pd.distribution_family == "NORMAL":
            p = normal_interval(pd.mu_native, pd.sigma_native, lo, hi)
        elif pd.distribution_family == "DAY0_HIGH_MAX_NORMAL":
            p = day0_high_interval(pd.day0.observed_extreme_native, pd.mu_native, pd.sigma_native, lo, hi)
        elif pd.distribution_family == "DAY0_LOW_MIN_NORMAL":
            p = day0_low_interval(pd.day0.observed_extreme_native, pd.mu_native, pd.sigma_native, lo, hi)
        probs.append(p)

    q = np.clip(np.asarray(probs), 0.0, 1.0)
    q = q / q.sum()
    return JointQ(...)

The replacement q builder already has q normalization in places, but the new design makes it one contract rather than three independent sites. The current fused q-bound function integrates per-bin masses but does not row-normalize the draw matrix before percentiles. 
GitHub

Create src/probability/joint_q_band.py:

Python
Run
@dataclass(frozen=True)
class PredictiveParameterDraw:
    mu_native: float
    sigma_native: float
    debias_shift_native: float
    center_error_native: float

@dataclass(frozen=True)
class JointQBand:
    joint_q: JointQ
    samples: np.ndarray          # shape (n_draws, n_bins)
    q_lcb: np.ndarray
    q_ucb: np.ndarray
    alpha: float
    basis: Literal["PARAMETER_POSTERIOR_SIMPLEX_V1"]
    sample_hash: str

    def assert_valid(self) -> None:
        assert self.samples.ndim == 2
        assert np.all(self.samples >= 0)
        assert np.allclose(self.samples.sum(axis=1), 1.0, atol=1e-9)

Algorithm:

Python
Run
for k in range(n_draws):
    mu_k = draw_mu(pd.center, pd.sigma_components)
    sigma_k = draw_sigma(pd.sigma_components)
    pd_k = replace(pd, mu_native=mu_k, sigma_native=sigma_k)
    q_k = integrate_all_bins(pd_k, omega)
    q_k = q_k / q_k.sum()
    samples[k, :] = q_k

q_lcb = np.quantile(samples, alpha, axis=0)
q_ucb = np.quantile(samples, 1 - alpha, axis=0)

This fixes the exact bad pattern in _build_fused_q_bounds, where probs is (draws × bins) and percentiles are taken per bin before any per-draw simplex normalization. 
GitHub

Create src/probability/instruments.py:

Python
Run
@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    bin_id: str
    side: Literal["YES", "NO"]
    direct_token_id: str | None

    def payoff_vector(self, omega: OutcomeSpace) -> np.ndarray:
        e = np.zeros(len(omega.bins))
        i = omega.index(self.bin_id)
        if self.side == "YES":
            e[i] = 1.0
        else:
            e[:] = 1.0
            e[i] = 0.0
        return e

NO probability is not a special formula in decision code. It is a direct consequence of the payoff vector and Σq=1:

Python
Run
fair_yes_i = q[i]
fair_no_i = 1.0 - q[i]
no_lcb_i = np.quantile(1.0 - band.samples[:, i], alpha)

Create src/execution/family_book.py:

Python
Run
@dataclass(frozen=True)
class ExecutableLadder:
    levels: tuple[QuoteLevel, ...]
    side: Literal["ask", "bid"]
    fee_rate: float
    min_tick_size: Decimal
    min_order_size: Decimal

@dataclass(frozen=True)
class MarketBook:
    condition_id: str
    bin_id: str
    yes_token_id: str
    no_token_id: str
    yes_asks: ExecutableLadder
    yes_bids: ExecutableLadder
    no_asks: ExecutableLadder
    no_bids: ExecutableLadder
    neg_risk: bool

@dataclass(frozen=True)
class FamilyBook:
    omega: OutcomeSpace
    markets: Mapping[str, MarketBook]
    captured_at_utc: datetime
    book_hash: str
    complete_book: bool

The existing executable_cost.py should remain as a native ladder walker. It correctly forbids midpoint/last/complement cost and walks the selected native ladder. But _levels_for_direction is not a family route engine; it returns only one side’s ladder. 
GitHub

Create src/execution/negrisk_routes.py:

Python
Run
@dataclass(frozen=True)
class RouteCost:
    route_id: str
    route_type: Literal[
        "DIRECT_YES",
        "DIRECT_NO",
        "SYNTHETIC_NOT_I_YES_BASKET",
        "PAIR_ARB",
        "FULL_YES_BASKET_ARB",
        "CONVERSION_SELL_BASKET",
    ]
    instrument: Instrument
    shares: Decimal
    avg_cost: ExecutionPrice
    max_shares: Decimal
    legs: tuple[RouteLeg, ...]
    executable: bool
    reason: str | None

@dataclass(frozen=True)
class NegRiskRouteSet:
    direct_yes: Mapping[str, RouteCost]
    direct_no: Mapping[str, RouteCost]
    synthetic_not_i: Mapping[str, RouteCost]
    pair_arbs: tuple[RouteCost, ...]
    full_basket_arbs: tuple[RouteCost, ...]
    conversion_routes: tuple[RouteCost, ...]

Route rules:

For YES_i buy:

route = direct YES_i ask.

For NO_i buy:

If negRisk=False, only direct NO_i can be used.
If negRisk=True, compare:

direct_no_cost(i, s) vs sum_{j≠i} yes_ask_cost(j, s)

and choose the lower executable route. The synthetic route buys equal shares of every sibling YES. Its max shares is the minimum depth-supported shares across siblings.

Arbitrage checks:

Pair:

Python
Run
ask_yes_i(s) + ask_no_i(s) + fees < 1.0

Full YES basket:

Python
Run
sum_i ask_yes_i(s) + fees < 1.0

Conversion:

Python
Run
cost_or_value(NO_i -> YES_j basket) must be executable by venue primitive
ask_no_i(s) + conversion_friction < sum_{j != i} bid_yes_j(s)

Route dominance:

Python
Run
not_i_cost = min(direct_no_cost(i, s), synthetic_yes_basket_cost(i, s))

Before implementing conversion routes live, Claude must verify venue primitives:

grep -R "NegRiskAdapter\\|convert\\|merge\\|split\\|redeemPositions\\|splitPosition\\|mergePositions" -n src

If not wired, conversion routes remain shadow and direct/synthetic routes proceed only where no conversion is required.

Create src/decision/payoff_vector.py:

Python
Run
@dataclass(frozen=True)
class CandidateRoute:
    candidate_id: str
    instrument: Instrument
    route_cost: RouteCost
    payoff_vector: np.ndarray
    side: Literal["YES", "NO"]
    bin_id: str

@dataclass(frozen=True)
class CandidateEconomics:
    candidate_id: str
    point_ev: float
    edge_lcb: float
    delta_u_at_min: float
    optimal_stake_usd: Decimal
    optimal_delta_u: float
    q_dot_payoff: float
    cost: ExecutionPrice
    route_id: str

Edge calculation:

Python
Run
payoff = route.payoff_vector  # values in $1 payoff units per share before cost
point_fair_value = float(joint_q.q @ payoff)

# Cost is all-in executable cost per $1 payoff share.
point_edge = point_fair_value - route.avg_cost.value

sample_edges = band.samples @ payoff - route.avg_cost.value
edge_lcb = np.quantile(sample_edges, alpha)

For YES_i, this reduces to q_i - ask_yes_i.
For direct NO_i or synthetic NOT_i, it reduces to (1 - q_i) - cost_not_i.
For baskets/arbs, it is the actual payoff vector of the bundle.

Sizing:

Use FamilyPayoffMatrix or a replacement with the same semantics. The existing module already states the right objective: ΔU_j(s) = Σ_y π_y^rob [log(A_y + R_y,j(s)) - log(A_y)], with YES and NO payoff geometry over all outcomes. 
GitHub

Implement robust utility with q samples:

Python
Run
def robust_delta_u(candidate, stake):
    values = []
    for q_k in band.samples:
        values.append(delta_u(candidate, stake, q_k, exposure))
    return np.quantile(values, alpha)

s_star = argmax_s robust_delta_u(candidate, s)

Live candidate pass:

Python
Run
candidate.edge_lcb > 0
candidate.delta_u_at_min > 0
candidate.optimal_delta_u > 0
executable route available
direction law proof present
market coherence accepted

Create src/decision/market_coherence.py:

Python
Run
@dataclass(frozen=True)
class MarketImpliedQ:
    q: np.ndarray
    basis: Literal["DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"]
    depth_score: float
    spread_score: float
    projection_error: float
    book_hash: str

@dataclass(frozen=True)
class MarketCoherenceReport:
    status: Literal["COHERENT", "INCOHERENT_BLOCK_LIVE", "INSUFFICIENT_MARKET_DEPTH", "NO_MARKET_Q"]
    max_abs_logit_gap: float
    kl_model_to_market: float
    kl_market_to_model: float
    offending_bins: tuple[str, ...]
    reason: str

Algorithm:

Build a de-frictioned implied family distribution from the book.

Project to the simplex.

Require enough depth/recency before using it.

Compare model q to market-implied q.

For candidate i:

Python
Run
logit_gap_i = abs(logit(clamp(q_model_i)) - logit(clamp(q_market_i)))

Block if:

Python
Run
depth_score >= min_depth
and spread_score <= max_spread
and logit_gap_i >= 2.5
and no licensed_model_superiority_class(case, bin_i)

Tokyo q=0.47 vs ask=0.001 has a logit gap around 6.8, so it dies here before trade score. This is a calibration incident, not a one-sided cap. The existing market_anchor.py explicitly implements a one-sided NO cap; replace it with this typed report. 
GitHub

Create src/decision/family_decision_engine.py:

Python
Run
@dataclass(frozen=True)
class FamilyDecision:
    decision_id: str
    case: ForecastCase
    predictive: PredictiveDistribution
    omega: OutcomeSpace
    joint_q: JointQ
    band: JointQBand
    family_book: FamilyBook
    market_coherence: MarketCoherenceReport
    candidates: tuple[CandidateEconomics, ...]
    selected: CandidateEconomics | None
    no_trade_reason: str | None
    receipt_hash: str

Decision algorithm:

Python
Run
def decide(case, family, snapshots, portfolio):
    resolution = event_resolution_for_city(...)
    omega = outcome_space_from_family(family, resolution)
    models = fresh_model_reader.read(case)
    obs = day0_reader.read(case)
    predictive = predictive_builder.build(case, models, obs)

    if not predictive.live_eligible:
        return no_trade("PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE")

    q = joint_q_builder.build(predictive, omega)
    band = q_band_builder.build(predictive, omega, q)
    family_book = family_book_builder.build(omega, snapshots)
    market_q = market_implied_q_builder.build(family_book)
    coherence = market_coherence.evaluate(q, market_q)

    routes = route_builder.build(omega, family_book)
    candidates = payoff_decision_builder.score(q, band, routes, portfolio)

    candidates = [c for c in candidates if direction_law_ok(c)]
    candidates = [c for c in candidates if coherence_allows(c)]
    candidates = [c for c in candidates if c.edge_lcb > 0 and c.optimal_delta_u > 0]

    selected = argmax(candidates, key=lambda c: c.optimal_delta_u)
    return FamilyDecision(...)

The old robust_trade_score remains only as a compatibility telemetry adapter until deleted. Its scalar formula is not allowed to select trades. 
GitHub

Create src/execution/liquidation_value.py:

Python
Run
@dataclass(frozen=True)
class PositionVector:
    family_id: str
    quantities_by_instrument: Mapping[str, Decimal]
    payoff_vector_by_instrument: Mapping[str, np.ndarray]

@dataclass(frozen=True)
class LiquidationRoute:
    route_type: Literal["DIRECT_SELL", "CONVERT_TO_BASKET_SELL", "HOLD_TO_REDEEM"]
    value_usd: Decimal
    executable: bool
    legs: tuple[RouteLeg, ...]
    reason: str | None

@dataclass(frozen=True)
class LiquidationDecision:
    chosen: LiquidationRoute
    alternatives: tuple[LiquidationRoute, ...]
    position_vector_hash: str

Exit algorithm:

Python
Run
direct = direct_sell_value(position, family_book)
convert = conversion_basket_sell_value(position, family_book, venue_primitives)
hold = hold_to_redeem_value(position, joint_q, time_to_resolution, risk_policy)

chosen = max([direct, convert, hold], key=lambda r: r.value_usd if r.executable else -inf)

The current exit code builds an ExitIntent for the current token and calls place_sell_order; that becomes one route under LiquidationValueEngine, not the exit authority. 
GitHub

Decision/coherence layer

The decision layer has four gates, and all are model/economic validity checks rather than kill-only patches:

Direction law:
YES_i is legal only when buying the forecast bin.
NO_i is legal only when the payoff vector is “not forecast bin.”
This remains unchanged.

Market coherence:
A deep-book q-vs-market contradiction means “our model is probably wrong or stale,” not “infinite edge.” The output is a calibration incident with full receipt fields. Tokyo 0.47-vs-0.001 must die here.

Payoff-vector robust edge:
Do not pass a candidate because scalar q_i - ask_i is positive. Pass only if the lower quantile of q_sample · payoff_vector - executable_route_cost is positive.

Vector sizing:
Single-Kelly means one global bankroll haircut:

Python
Run
stake = global_fractional_kelly * vector_argmax_stake

It does not mean replacing vector argmax with binary Kelly. The current code path has a scalar family_total = bankroll_usd * mult * f_star; that should be deleted from live authority once vector sizing is wired. 
GitHub

The real alpha question

Given no latency/data-speed edge, the only durable edge classes are:

Station representativeness:
Airport settlement station vs grid/city model differences. This is real only when station-specific, source-specific, and walk-forward validated. A stale per-city regime bias is not this.

Model blend superiority:
AIFS/ECMWF/GFS/open-data products may have stable conditional strengths by city, season, metric, lead, and regime. This must be measured by proper scoring and after-cost EV, not win rate.

Distribution/σ calibration:
If Zeus estimates tails and modal-bin mass better than market, it can trade. This is the most dangerous edge class because under-dispersed σ manufactures phantom edge. It requires reliability diagrams, PIT/histogram checks, CRPS/log-score improvement, and settlement-graded q bucket calibration.

Settlement-semantics edge:
HK truncation and local-day/station quirks can be real if the market systematically prices WMO while settlement truncates. But Zeus must first be byte-identical to settlement; otherwise it is just self-pollution.

Neg-risk execution:
Pair/basket/route arbitrage is real alpha if executable after fees, ticks, depth, and conversion friction. It does not require forecast alpha. It does require confirmed venue primitives.

What is not alpha:

Buying near-center favorites because they often win.
Trading day0 observed extremes faster than the market when Zeus has no speed edge.
Treating market disagreement as proof that the model is right.
Treating an extremely cheap ask on a deep book as “free money” when model q is stale or incoherent.

Staged rebuild plan

Stage 0 — receipt spine before behavior change.

Goal: make every current live candidate reconstructable from source inputs to decision.

Files:
src/engine/event_reactor_adapter.py
src/events/no_trade_events_schema.py
new src/decision/decision_receipt.py

Implement:
Add receipt fields:

predictive_distribution_id
q_source
mu_native
sigma_native
member_min_native
member_max_native
debiased_member_min_native
debiased_member_max_native
applied_debias_native
debias_artifact_id
day0_observed_extreme_native
rounding_rule
q_sum
q_band_basis
market_implied_q
route_id
payoff_vector_hash
edge_lcb
delta_u
sizing_authority

RED-on-revert test:
tests/decision/test_live_receipt_contract.py::test_candidate_receipt_reconstructs_forecast_q_route_and_size

Live verification signal:
No candidate receipt lacks μ/σ/member envelope/q_source/route.

Stage 1 — create EventResolution and OutcomeSpace.

Goal:
One settlement identity and complete Ω before q.

Files:
new src/probability/event_resolution.py
new src/probability/outcome_space.py
modify src/contracts/settlement_semantics.py
modify event_reactor_adapter.py family binding

RED-on-revert:
tests/probability/test_outcome_space_contract.py::test_incomplete_family_fails_closed_and_complete_family_sums_mass
tests/probability/test_settlement_preimage_threading.py::test_hk_oracle_truncate_reaches_every_q_builder

Live signal:
Every family receipt has topology_hash, semantics_version, rounding_rule, station_id.

Stage 2 — build DebiasAuthority.

Goal:
Kill parallel contaminated de-bias.

Files:
new src/forecast/debias_authority.py
new src/forecast/types.py
modify event_reactor_adapter.py:_maybe_apply_edli_bias_correction
modify emos_q_builder.py offset read path

RED-on-revert:
tests/forecast/test_debias_authority.py::test_bias_row_must_be_fresh_product_matched_station_matched
tests/forecast/test_debias_authority.py::test_tokyo_minus_4847_bias_refused_against_realized_residual_band
tests/forecast/test_debias_authority.py::test_only_one_temperature_mean_shift_can_apply

Live signal:
Applied bias histogram bounded by realized residuals; stale artifact count visible; no candidate applies both EMOS offset and EDLI bias independently.

Stage 3 — implement center builder and envelope invariant.

Goal:
μ* tracks fresh debiased consensus.

Files:
new src/forecast/center.py
new src/forecast/predictive_distribution_builder.py
modify src/calibration/emos.py usage
modify src/calibration/emos_q_builder.py

RED-on-revert:
tests/forecast/test_center_envelope.py::test_mu_star_inside_debiased_member_envelope
tests/forecast/test_center_envelope.py::test_emos_slope_cannot_push_mu_outside_envelope
tests/forecast/test_center_envelope.py::test_tokyo_26_impossible_when_members_are_20_to_23

Live signal:
For every receipt, member_min <= mu <= member_max unless day0_observed_extreme exceeds the envelope.

Stage 4 — day0 conditioner.

Goal:
Observed running extreme is ground truth.

Files:
new src/forecast/day0_conditioner.py
modify predictive_distribution_builder.py
modify q integration to use DAY0_HIGH_MAX_NORMAL and DAY0_LOW_MIN_NORMAL

RED-on-revert:
tests/forecast/test_day0_extreme_conditioner.py::test_high_bins_below_observed_high_have_zero_probability
tests/forecast/test_day0_extreme_conditioner.py::test_low_bins_above_observed_low_have_zero_probability
tests/forecast/test_day0_extreme_conditioner.py::test_observed_extreme_clamps_center

Live signal:
On active day, impossible bins below/above observed extreme have q=0 after settlement preimage.

Stage 5 — sigma authority.

Goal:
No sub-realized σ.

Files:
new src/forecast/sigma_authority.py
modify replacement_forecast_materializer.py
modify emos_q_builder.py
modify any soft-anchor fallback path

RED-on-revert:
tests/forecast/test_sigma_authority.py::test_sigma_never_below_realized_floor_on_emos_raw_replacement_day0
tests/forecast/test_sigma_authority.py::test_soft_anchor_without_sigma_is_not_live_eligible

Live signal:
No receipt has sigma_native < realized_floor_native; modal one-degree bin mass is bounded by that σ.

Stage 6 — JointQ point and JointQBand.

Goal:
One normalized joint distribution and coherent q_lcb.

Files:
new src/probability/joint_q.py
new src/probability/joint_q_band.py
replace replacement_forecast_materializer.py:_build_fused_q_bounds
replace event_reactor_adapter.py:_side_q_lcb_from_yes_samples usage for live

RED-on-revert:
tests/probability/test_joint_q.py::test_q_sum_one_for_every_family
tests/probability/test_joint_q_band.py::test_every_band_sample_row_sums_to_one
tests/probability/test_joint_q_band.py::test_modal_lcb_does_not_collapse_from_raw_bin_percentile

Live signal:
q point sums to 1; q band receipts include sample hash and row-sum stats.

Stage 7 — NO-as-basket and route set.

Goal:
NO is payoff vector plus executable route, not UI complement.

Files:
new src/probability/instruments.py
new src/execution/family_book.py
new src/execution/negrisk_routes.py
modify src/strategy/live_inference/executable_cost.py to stay leaf-only
modify reactor candidate generation

RED-on-revert:
tests/probability/test_no_basket_semantics.py::test_no_payoff_vector_wins_on_every_other_bin
tests/execution/test_negrisk_route_set.py::test_synthetic_yes_basket_dominates_expensive_direct_no
tests/execution/test_negrisk_route_set.py::test_negrisk_routes_disabled_when_flag_false

Live signal:
Every NO candidate receipt lists direct NO cost, synthetic sibling basket cost, chosen route, and negRisk flag.

Stage 8 — payoff-vector decision and vector sizing.

Goal:
Use Arrow-Debreu economics for edge and size.

Files:
new src/decision/payoff_vector.py
new src/decision/family_decision_engine.py
modify utility_ranker.py or wrap it as implementation
modify trade_score.py to telemetry-only
modify reactor sizing block around scalar Kelly

RED-on-revert:
tests/decision/test_payoff_vector_edge.py::test_edge_is_q_dot_payoff_minus_route_cost
tests/decision/test_vector_sizing_authority.py::test_family_total_uses_vector_argmax_not_binary_kelly
tests/decision/test_existing_exposure.py::test_correlated_existing_position_reduces_delta_u_size

Live signal:
Selected candidate has edge_lcb, point_ev, delta_u, optimal_stake, payoff_vector_hash; scalar q-price is logged but not selected on.

Stage 9 — market coherence.

Goal:
Kill q-vs-market contradictions before scoring.

Files:
new src/decision/market_coherence.py
replace market_anchor.py live use
modify family_decision_engine.py

RED-on-revert:
tests/decision/test_market_coherence.py::test_tokyo_q_047_vs_deep_ask_0001_blocks_before_scoring
tests/decision/test_market_coherence.py::test_insufficient_depth_does_not_fabricate_market_gate
tests/decision/test_market_coherence.py::test_licensed_model_superiority_class_can_override_with_receipt

Live signal:
Incoherence incidents grouped by city/metric/source/regime; no deep-book order-of-magnitude contradiction reaches submit.

Stage 10 — liquidation-value exit.

Goal:
Exit position vector by max liquidation value.

Files:
new src/execution/liquidation_value.py
modify src/execution/exit_lifecycle.py
possibly resurrect/replace src/strategy/exit_family_optimizer.py

RED-on-revert:
tests/execution/test_liquidation_value_engine.py::test_direct_sell_is_one_route_not_authority
tests/execution/test_liquidation_value_engine.py::test_no_position_chooses_conversion_basket_when_more_valuable
tests/execution/test_liquidation_value_engine.py::test_hold_to_redeem_selected_when_all_sell_routes_worse

Live signal:
Exit receipt lists direct sell, conversion/basket, hold-to-redeem values and chosen route.

Stage 11 — delete old authority surfaces.

Goal:
Collapse K≪N, not accrete flags.

Delete or demote:
live use of _maybe_apply_edli_bias_correction
replacement raw percentile q_lcb builder
replacement 1 - q_ucb_yes map path outside JointQBand
scalar robust_trade_score as selector
scalar binary Kelly family total
market-anchor cap as live q mutator
current-token-only exit authority

RED-on-revert:
tests/architecture/test_no_parallel_authorities.py::test_no_live_code_calls_deleted_legacy_authorities

Live signal:
One decision receipt schema, one q builder, one sizing authority.

What must not change:

Direction law stays.
Bid/ask/depth/taker-fee/min-tick execution stays.
Midpoint/last/complement cost bans stay.
Settlement truth stays; HK oracle truncation must be threaded everywhere.
MECE fail-closed stays.
No one-sided caps as alpha substitutes.
No permanent shadow flags.
No loosening q_lcb/FDR/coverage to fill orders.
No “one order” success criterion.

Biggest risk and ARM method

The biggest risk is that fixing center and q_lcb makes Zeus start buying near-center favorites at base rate. Today’s broken q_lcb can suppress trades. A coherent q_lcb will raise conservative probability on modal bins. That is correct only if settlement coverage validates it.

ARM must require:

Point-q calibration:
For bins forecast at 0.05, 0.10, …, 0.95, realized frequency matches within confidence bands.

q_lcb coverage:
For candidates with q_lcb=x, realized win rate should exceed x at the intended conservative coverage rate.

After-cost EV:
Grouped by city, metric, lead, regime, side, route, and market-coherence class.

Market disagreement audit:
If model beats market in a class, it must be pre-registered and settlement-graded. Otherwise deep disagreement blocks.

Width reliability:
PIT/histogram by lead bucket; modal-bin realized/expected ratio near 1.

No-trade counterfactual:
Every no-trade reason must have a settlement outcome. If blocked by coherence, see whether the market or model was right.

The smallest fact that could change tactical priority is a live candidate receipt proving the current Tokyo-like path is EMOS primary, day0 fallback, replacement, or EDLI. It does not change the architecture. The single-authority center and family q kernel eliminate all four failure modes.

Highest-value local checks

Run these first:

Bash
pytest -q tests/forecast/test_debias_authority.py::test_tokyo_minus_4847_bias_refused_against_realized_residual_band
pytest -q tests/forecast/test_center_envelope.py::test_tokyo_26_impossible_when_members_are_20_to_23
pytest -q tests/forecast/test_day0_extreme_conditioner.py
pytest -q tests/forecast/test_sigma_authority.py
pytest -q tests/probability/test_joint_q.py tests/probability/test_joint_q_band.py
pytest -q tests/probability/test_settlement_preimage_threading.py::test_hk_oracle_truncate_reaches_every_q_builder
pytest -q tests/probability/test_no_basket_semantics.py
pytest -q tests/execution/test_negrisk_route_set.py
pytest -q tests/decision/test_payoff_vector_edge.py tests/decision/test_vector_sizing_authority.py
pytest -q tests/decision/test_market_coherence.py::test_deep_book_tokyo_q_047_vs_ask_0001_blocks_before_scoring
pytest -q tests/execution/test_liquidation_value_engine.py
pytest -q tests/architecture/test_no_parallel_authorities.py

Run this live probe before coding priority decisions:

Bash
python scripts/dump_latest_candidate_receipt.py \
  --city Tokyo \
  --metric high \
  --fields q_source,mu_native,sigma_native,member_min_native,member_max_native,\
debiased_member_min_native,debiased_member_max_native,applied_debias_native,\
debias_artifact_id,day0_observed_extreme_native,rounding_rule,q_sum,q_lcb_basis,\
best_yes_ask,best_no_ask,market_implied_q,route_id,edge_lcb,delta_u,sizing_authority

Sources opened and used

src/engine/event_reactor_adapter.py for EMOS route, EDLI bias correction, replacement NO lcb, and scalar Kelly family total. 
GitHub
+3
GitHub
+3
GitHub
+3

src/data/replacement_forecast_materializer.py for fused q bounds and sigma construction. 
GitHub
+1

src/calibration/emos.py and src/calibration/emos_q_builder.py for EMOS center, μ-offset, settlement σ floor, q normalization, and missing rounding-rule argument at the EMOS q seam. 
GitHub
+1

src/contracts/settlement_semantics.py for WMO half-up, HK oracle truncation, and settlement preimage offsets. 
GitHub

src/strategy/live_inference/executable_cost.py, trade_score.py, market_anchor.py, utility_ranker.py, and src/execution/exit_lifecycle.py for native executable cost, scalar score, one-sided market cap, family payoff-vector utility, and direct-token exit. 
GitHub
+4
GitHub
+4
GitHub
+4

Material still unknown until local verification:

live settings.json, live DB rows, live logs, state JSON artifacts, exact current Tokyo q_source, and whether venue convert/merge/split primitives are wired. Those affect rollout priority, not the design.