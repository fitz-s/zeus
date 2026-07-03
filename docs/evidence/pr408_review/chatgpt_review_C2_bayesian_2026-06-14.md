Verdict: DO NOT MERGE at ef421f1fd0

Confidence: high for the blocking code-path findings, with the usual caveat that Claude Code must confirm reachability in the local test/runtime graph. I read the pinned raw/blob contents for every reachable file in the supplied list. The src/strategy/bayes_alert.py and src/strategy/candidates/** paths are absent/404 at the pinned head, not merely unreachable through the browser.

The two most important blockers are:

K3’s intended status semantics are contradicted downstream. settlement_backward_coverage.py says INSUFFICIENT_DATA must license-by-default and UNLICENSED blocks proven overconfidence; live_admission.py and the adapter credential instead license LICENSED + UNLICENSED and reject INSUFFICIENT_DATA. That inverts the rebuild’s operator intent.
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py L2-L7, L18-L28; https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/live_inference/live_admission.py L3-L5, L15-L18; https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L650-L664.
GitHub
+3
GitHub
+3
GitHub
+3

A live one-sided q_lcb market-anchor cap is enabled in production settings. The settings file admits it “only lowers q_lcb” and “can never create a trade,” while real_order_submit_enabled and operator authorization are true. That is a direct Law 3 violation: it is a new one-sided cap/gate that can only kill trades.
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.json L18-L28, L24-L27.
GitHub

I would not merge until the CRITICAL/HIGH items below are fixed and regression-tested.

Findings

Line numbers below refer to the GitHub/raw rendering of the pinned SHA. Several files are minified into long physical lines in the raw view, so I cite both file/symbol and rendered line spans.

CRITICAL — K3 status semantics are inverted in live admission / replacement credential

Dimension: calibration honesty/leakage; gate-discipline/K-cut; runtime correctness/regression
Location:
src/calibration/settlement_backward_coverage.py::arm_gate_coverage_blocks, settlement_backward_coverage_check — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py L2-L7, L18-L28
src/strategy/live_inference/live_admission.py::SETTLEMENT_COVERAGE_LICENSING_STATUSES, live_buy_no_conservative_evidence_rejection_reason — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/live_inference/live_admission.py L3-L5, L15-L18
src/engine/event_reactor_adapter.py::_replacement_calibration_payload_from_credential — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L650-L664

Evidence: The K3 module explicitly says INSUFFICIENT_DATA is licensed-by-default, does not shrink, and does not ARM-block; only UNLICENSED is proven overconfidence and blocks. But live_admission.py defines SETTLEMENT_COVERAGE_LICENSING_STATUSES = frozenset({"LICENSED", "UNLICENSED"}), and both buy-NO admission and the replacement credential use that set. Comments in the adapter still describe the old rule: INSUFFICIENT_DATA / coverage-ratio missing is rejected, and UNLICENSED licenses the credential.
GitHub
+3
GitHub
+3
GitHub
+3

Impact: This defeats the K3 rebuild. Thin/no-history cases are suppressed despite the operator’s “license-by-default” rule, while proven-overconfident cases can be credentialed as live-backed. In live trading this can both kill valid concentrated edges and admit record-refuted q_lcb provenance. This is not a cosmetic comment drift; the exported set is consumed by the live credential and buy-NO admission path.

Concrete fix: Replace the shared set with explicit predicates, not a status-name allowlist:

Python
Run
def settlement_coverage_allows_arm(status: str | None) -> bool:
    return status in {"LICENSED", "INSUFFICIENT_DATA"}

def settlement_coverage_refutes_claim(status: str | None) -> bool:
    return status == "UNLICENSED"

Then wire:

Python
Run
SETTLEMENT_COVERAGE_LICENSING_STATUSES = frozenset({"LICENSED", "INSUFFICIENT_DATA"})

or, better, delete the set entirely and import the predicates from settlement_backward_coverage.py. Add a matrix test that asserts:

status	q_lcb shrink	ARM block	replacement credential	buy_no admission fallback
LICENSED	no	no	yes	yes
INSUFFICIENT_DATA	no	no	yes / neutral, not default-deny	yes / neutral, not default-deny
UNLICENSED	yes, if shrink flag on	yes	no live credential unless an explicit post-shrink policy is defined	no
CRITICAL — Live one-sided q_lcb market-anchor cap is enabled

Dimension: gate-discipline/K-cut; runtime correctness/regression
Location:
config/settings.json::edli.replacement_q_market_anchor_enabled — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.json L24-L28
src/engine/event_reactor_adapter.py::_replacement_q_market_anchor_enabled — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L660-L667

Evidence: Settings have real_order_submit_enabled: true, edli_live_operator_authorized: true, and replacement_q_market_anchor_enabled: true. The note says the cap is “ONE-SIDED,” “only lowers q_lcb,” and “can never create a trade.”
GitHub

Impact: This is exactly the forbidden shape under Operator Law 3. It can only suppress trades and cannot improve the calibrated probability object; it also risks hiding model errors behind market deference instead of making q/q_lcb honest at the calibration seam. The settings note says to flip only after forward fills license it, yet the flag is already true.

Concrete fix: Turn it off immediately:

JSON
"replacement_q_market_anchor_enabled": false

Then remove or quarantine the live cap path. If market information is useful, it should enter as a calibrated posterior/fusion component with settlement-graded evidence, not as a one-way min(q_lcb, market_anchor) veto. Add a release-gate assertion that no setting with a docstring containing “only lowers,” “cap,” “never creates a trade,” or “one-sided” is enabled in live-money mode without an explicit operator-law exemption artifact.

HIGH — K3 coverage observation stream is not walk-forward-safe and structurally fails open

Dimension: calibration honesty/leakage; law-8 metadata; runtime correctness/regression
Location:
src/engine/event_reactor_adapter.py::_per_day_claimed_qlcb_by_date, _settlement_coverage_observations, _maybe_apply_settlement_coverage_to_lcb — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L873-L886, L888-L894

Evidence: _per_day_claimed_qlcb_by_date reads all edli_no_submit_receipts for a direction, orders by created_at, but ignores created_at after fetching. It keeps the last receipt per target_date with no decision_time cutoff. _settlement_coverage_observations reads all verified settlement_outcomes for the city/metric with no cutoff by current target date or decision time. The helper comments explicitly say any error/unavailable history returns an empty dict/list, which becomes INSUFFICIENT_DATA and now should be non-blocking.
GitHub

Impact: In live current time this may often read only past facts, but in historical replay, backtests, promotion evidence, or daemon reruns it can leak future receipts and future verified outcomes into an earlier decision’s licensing. Worse, schema/DB/read errors are indistinguishable from true thin data: a structural authority fault can collapse into INSUFFICIENT_DATA and license-by-default. That is not “fail-closed”; it is fail-open disguised as thin history.

Concrete fix: Make the coverage API explicitly temporal and typed:

Python
Run
@dataclass(frozen=True)
class ClaimHistoryResult:
    ok: bool
    observations: list[CoverageObservation]
    fault_reason: str | None = None

Require decision_time and current_target_date arguments. Filter:

SQL
edli_no_submit_receipts.created_at <= :decision_time
receipt.target_date < :current_target_date
settlement_outcomes.target_date < :current_target_date
settlement_outcomes.verified_at <= :decision_time  -- if available

Only return INSUFFICIENT_DATA when the queries succeeded and the successful observation count is below min_n. On structural read/parse/schema failure, raise QLCB_COVERAGE_AUTHORITY_FAULT and fail closed while the live safety gate is enabled. Add a replay test with a future receipt and future settlement row proving they are excluded.

HIGH — K3 band identity is a fragile free-text regex, not contract/bin metadata

Dimension: law-8 metadata; calibration honesty/leakage
Location:
src/engine/event_reactor_adapter.py::_coverage_band_template, _per_day_claimed_qlcb_by_date — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L873-L881

Evidence: The per-day q_lcb history is matched by stripping a trailing English " on Month day?" pattern from bin_label and comparing the resulting text. It does not use bin kind, low/high boundary, unit, condition/event identity, settlement station, or canonical market support identity.
GitHub

Impact: This violates the spirit of Law 8. Any label wording drift, non-English month, year inclusion, punctuation change, city spelling change, or bin-label reuse can silently split or merge calibration cohorts. Because this is a settlement-coverage licensing input, wrong band identity makes downstream q_lcb shrink/license decisions confidently wrong.

Concrete fix: Persist and join on a canonical band_identity in edli_no_submit_receipts.receipt_json or columns:

JSON
{
  "city": "...",
  "target_date": "...",
  "metric": "high",
  "settlement_station": "...",
  "bin_kind": "point/range/shoulder",
  "bin_low": ...,
  "bin_high": ...,
  "unit": "C/F",
  "rounding_rule": "wmo_half_up",
  "condition_id": "...",
  "market_slug": "..."
}

Derive the coverage cohort from (city, metric, settlement_station, bin_kind, low, high, unit, rounding_rule, direction) and use target date only for the per-day claim key. Keep the text regex only as diagnostic fallback, never authority.

HIGH — EMOS μ-offset one-signed guarantee is documented but not enforced

Dimension: calibration honesty/leakage; runtime correctness/regression
Location:
src/calibration/emos.py::emos_mu_offset — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos.py L27-L30
scripts/fit_emos_mu_offset.py::gate_cell — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_emos_mu_offset.py L13-L16
src/calibration/emos_q_builder.py::build_emos_q — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos_q_builder.py L9-L14

Evidence: The docs say the fitter only activates cold cells and offset_c < 0 so mu_corr = mu* - offset_c warms the center. But the fitter’s activation condition checks mean_res < -0.5, OOS residual/CRPS improvement, and abs(res_after) < abs(res_before); it does not require the stored all-history offset = median(res_all) to be negative, nor does it require walk-forward deltas to be negative. The loader returns any finite offset_c for an activated cell. The q builder blindly applies mu_c = mu_c - offset.
GitHub
+4
GitHub
+4
GitHub
+4

Impact: A skewed cell can be cold by mean but have a positive median. If activated, the live q seam will subtract a positive offset and cool the center, exactly the class the comments say is impossible. This can over-warm/over-cool EMOS-absorbed or warm-overshoot cells depending on residual distribution shape.

Concrete fix: Enforce the one-signed contract in both producer and consumer.

Producer:

Python
Run
if offset >= 0.0:
    return out
...
if any(train_delta >= 0.0 for train_delta in deltas_used_for_oos):
    return out
...
out["activated"] = bool(
    offset < 0.0
    and mean_res < COLD_THRESHOLD
    and improves_res
    and improves_crps
    and not_overcorrected
)

Consumer:

Python
Run
if off >= 0.0:
    if required:
        raise EmosMuOffsetError(...)
    return None

Also persist offset_sign_ok, median_residual_c, and train_delta_sign_violations in the artifact. Add a regression fixture where mean residual is cold but median residual is positive and assert no activation and no correction.

HIGH — EMOS-CI live override kills buy-NO q_lcb by setting it to zero

Dimension: direction-law; gate-discipline/K-cut; runtime correctness/regression
Location:
src/engine/event_reactor_adapter.py::_maybe_apply_emos_ci_lcb_override — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L864-L872
src/strategy/probability_uncertainty.py::no_side_samples — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/probability_uncertainty.py L0-L13

Evidence: The EMOS-CI override computes an analytic YES q_lcb, then states “Buy-NO requires an explicit NO-side posterior/LCB, not a YES complement” and sets emos_q_lcb_no = 0.0, writing that as EMOS_ANALYTIC provenance for buy-NO keys. But the probability uncertainty contract says native NO is not an independent forecast; it is the per-sample YES complement, and the correct NO lower bound is lower_quantile(1 - q_yes_samples) / 1 - upper_quantile(q_yes_samples).
GitHub
+1

Impact: This is a one-sided kill of buy-NO trades under the EMOS-CI override. It does not invert buy-NO into a wrong-side trade, but it violates the direction law’s constructive side: buy_no on non-forecast bins must be buildable from the correct settlement preimage and conservative NO probability. Setting it to zero is another K≪N suppressor.

Concrete fix: Compute an actual conservative NO lower bound from the same EMOS Normal and settlement preimage:

Python
Run
q_yes_point = P(bin)
q_yes_upper = conservative_upper_bound_for_yes_same_uncertainty(...)
q_no_lcb = max(0.0, 1.0 - q_yes_upper)

or draw EMOS samples and call no_side_samples() plus lower_quantile(). Add tests:

non-modal bin with low YES upper bound yields positive q_no_lcb;

modal/bin-near-forecast buy-NO is still vetoed by the direction-law admission layer;

EMOS-CI override never writes a hardcoded zero when a native NO side exists.

HIGH — Legacy MarketAnalysis NO-side path is contradictory and effectively dead

Dimension: direction-law; runtime correctness/regression; test adequacy
Location:
src/strategy/market_analysis.py::find_edges_with_trace — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/market_analysis.py L37-L44
src/strategy/probability_uncertainty.py::no_side_samples — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/probability_uncertainty.py L0-L13

Evidence: If native buy-NO quotes exist, MarketAnalysis emits a trace decision buy_no_independent_no_posterior_missing and continues before the direction-law/modal-bin logic can build a non-modal buy-NO. The code below then sets p_model_no = 0.0 and p_post_no = 0.0, so even if reached it cannot emit a NO edge. This contradicts the canonical probability contract: NO is the sample-level YES complement, not an independent model.
GitHub
+1

Impact: If this class is still reachable in live, replay, or shadow promotion evidence, it creates a hidden YES-only universe and can make buy-NO settlement-grade evidence disappear. If it is truly legacy-only, it still misleads future reviewers and tests because its comments sound like a safety property while the canonical path says the opposite.

Concrete fix: Either delete/quarantine this legacy path or implement native NO properly:

Python
Run
q_yes_samples = self.bin_yes_probability_samples(i, n_bootstrap)
q_no_samples = no_side_samples(q_yes_samples)
p_post_no = 1.0 - self.p_posterior[i]
q_no_lcb = lower_quantile(q_no_samples)
edge_no = p_post_no - entry_cost_mean_no

Keep the modal-bin veto. Add a unit test where a non-modal bin has a cheap native NO quote and must produce a buy-NO candidate, while the modal bin never does.

HIGH — Post-peak harvester is shaped around a single live fill and fixed caps/gates

Dimension: gate-discipline/K-cut; settlement/position truth; test adequacy
Location:
src/strategy/post_peak_harvester.py — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/post_peak_harvester.py L0-L12

Evidence: The module is justified by a named 2026-06-13 London fill, says the “paranoid guard” separated London from Paris/Munich, uses fixed thresholds (NEAR_IMPOSSIBLE_P_MAX = 0.05, PARANOID_FREE_RISE = 1.0, PARANOID_SIGMA_MULT = 2.0), and clamps fractional Kelly sizing into a $25-$40 envelope. It says it is only a scanner and does not submit orders, but also says the operator’s separate verified execution path consumes it.
GitHub

Impact: This is exactly the shape of “a fix just to fill one order” unless it is strictly shadow/read-only and settlement-graded forward. The fixed size envelope is a cap. The opportunity condition leans on near-certain NO/favorite economics, so base-rate favorite buying must be proven after cost, not inferred from high win probability.

Concrete fix: Keep this module non-authoritative until a forward, settlement-graded report shows >51% after-cost profitability and stable EV. Remove fixed size clamps from any live path; sizing should come from q_lcb, executable cost, bankroll/free-cash, and portfolio utility. Add a test or release gate proving no HarvestOpportunity can enter order submission unless an explicit live-promotion artifact exists and is checked at runtime.

MEDIUM — Settlement unit null/unknown handling can corrupt calibration artifacts

Dimension: law-8 metadata; calibration honesty/leakage
Location:
scripts/fit_emos_mu_offset.py::to_c, _SETTLE_SQL — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_emos_mu_offset.py L12-L13
scripts/fit_anchor_representativeness_debias.py::_settlement_to_celsius, _gather_residuals — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_anchor_representativeness_debias.py L9-L11

Evidence: The EMOS fitter’s settlement query requires settlement_value IS NOT NULL but not settlement_unit IS NOT NULL; to_c() treats null/unknown units as already Celsius. The anchor debias fitter has the same pattern: only "F" converts; everything else becomes float(value) in °C.
GitHub
+1

Impact: A null/unknown Fahrenheit settlement value treated as Celsius creates residuals off by tens of degrees and can activate or suppress correction artifacts incorrectly. This is an artifact-generation Law 8 breach.

Fix direction: Require settlement_unit IN ('C','F','DEGC','DEGF','K') and skip/log all unknown/null units. Persist skipped counts in artifact metadata. Add a fixture with settlement_unit NULL and assert the row is excluded.

MEDIUM — Sigma-kernel holdout replay uses held-out outcomes to build the held-out price proxy

Dimension: calibration honesty/leakage; test adequacy
Location:
scripts/sigma_kernel_holdout_replay.py — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/sigma_kernel_holdout_replay.py L0-L23

Evidence: The script is described as temporal holdout, but it computes realized-by-distance statistics from the candidate/test set and then uses that realized frequency as a market-free NO price proxy for replaying trades on the same test rows.
GitHub

Impact: It is acceptable as a diagnostic but not as live promotion evidence. It leaks held-out outcomes into the acceptance economics, which can overstate the stability of sigma-shape improvements.

Fix direction: Estimate the distance price proxy from training data only, or use a nested prequential estimate where each held-out date is priced only from prior observations. Label the current script output “diagnostic only; not a live-fill license.”

MEDIUM — Bias-decay Kelly haircut and shoulder cluster gate remain one-sided suppressors

Dimension: gate-discipline/K-cut
Location:
src/engine/event_reactor_adapter.py::_maybe_bias_decay_kelly_haircut — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py L809-L814
config/settings.json::bias_decay_kelly_haircut_enabled — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.json L18-L23
src/strategy/shoulder_cluster_cap.py — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/shoulder_cluster_cap.py L0-L8

Evidence: Bias-decay is enabled and halves Kelly on missing/high bias rows. The shoulder cluster module removed the notional cap but keeps a hard cross-city same-direction refusal gate.
GitHub
+2
GitHub
+2

Impact: These may be pre-existing/inert in some regimes, but they are still one-way trade killers unless fully justified as portfolio-risk constraints rather than alpha gates. The bias haircut is particularly suspect because missing data triggers a size reduction rather than feeding uncertainty into q_lcb/σ.

Fix direction: Represent bias uncertainty inside the probability/dispersion model, not as an external Kelly multiplier. For correlated shoulders, use portfolio covariance / payoff-matrix exposure in the utility ranker, not a hard presence veto, unless the operator explicitly preserves this as a risk-law exception.

MEDIUM — EMOS μ-offset scripts are missing from architecture registries/tests

Dimension: governance; test adequacy
Location:
architecture/script_manifest.yaml — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/script_manifest.yaml
architecture/test_topology.yaml — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/test_topology.yaml
scripts/fit_emos_mu_offset.py — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_emos_mu_offset.py

Evidence: The EMOS μ-offset fitter writes state/emos_mu_offset.json, and the live q seam consumes that artifact, but fit_emos_mu_offset, probe_emos_mu_correction_D4, and scan_emos_mu_residual_all_cities are absent from script_manifest.yaml; emos_mu_offset is absent from test_topology.yaml.
GitHub
+4
GitHub
+4
GitHub
+4

Impact: A live-affecting calibration artifact can be regenerated/promoted without the repo’s governance registry knowing its read/write targets, promotion barrier, or required tests.

Fix direction: Add manifest entries for the three EMOS μ-offset scripts with read targets, write targets, dangerous-if-run classification, promotion barrier, and required tests. Add topology tests covering artifact schema, missing artifact fail-closed behavior, positive offset rejection, walk-forward no-leak, and unit-null exclusion.

LOW — settings.example.json and src/config.py do not reflect the live edli contract

Dimension: runtime correctness/regression; governance
Location:
config/settings.example.json — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.example.json
src/config.py — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/config.py
config/settings.json — https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.json

Evidence: The live settings file has a large edli block with live-money flags, but the example is sparse and src/config.py does not appear to enforce edli as a required top-level contract.
GitHub
+2
GitHub
+2

Impact: Fresh/dev configs can pass early loading and then fail later in runtime paths that assume settings["edli"] exists. This is not the biggest money-path risk, but it weakens reproducibility.

Fix direction: Update settings.example.json with a minimal safe edli block and add config validation for required live-mode keys.

LOW / coverage note — src/strategy/bayes_alert.py and src/strategy/candidates/** are absent at this SHA

Dimension: coverage; architecture consistency
Location:
src/strategy/bayes_alert.py and all supplied src/strategy/candidates/*.py URLs

Evidence: Every raw/blob URL for those paths returned 404 at ef421f1fd0, and the src/strategy tree at the pinned SHA contains no candidates directory. The PR files view also indicates candidate/shadow strategy deletion work in this PR.
Tree: https://github.com/fitz-s/zeus/tree/ef421f1fd0/src/strategy

Impact: This is not an access failure; those files do not exist in the pinned source snapshot. If the prior review expected to inspect candidate direction-law code, the actual PR state appears to have deleted that framework. Claude Code should confirm locally with git ls-tree -r ef421f1fd0 src/strategy | grep -E 'bayes_alert|candidates'.

K3 rebuild assessment

The core K3 module itself is mostly aligned with the stated rebuild:

It consumes CoverageObservation(q_lcb, won) rather than a constant claimed q_lcb.

It shrinks only on UNLICENSED.

It treats INSUFFICIENT_DATA as no-shrink/no-block.

grade_receipt is the intended direction-law truth source.

Evidence: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py L0-L7, L15-L28.
GitHub

But the integration is not honest yet:

The live credential/admission status set contradicts the K3 module.

Claim history and settlements are not cut off by decision time / current target date.

Structural read failures can become INSUFFICIENT_DATA.

Band identity is free text rather than contract metadata.

Family-level credential uses the first buy-YES candidate with q_lcb as representative, which is not obviously equivalent to every direction/bin candidate in the family.

Claude Code should treat K3 as not mergeable until those are corrected.

EMOS μ-offset assessment

The intended sign in build_emos_q is correct if and only if offset_c < 0: mu_corr = mu* - offset_c warms a cold EMOS center. The no-artifact/no-cell behavior is fail-closed in the sense that it leaves today’s center unchanged. Evidence: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos_q_builder.py L9-L14 and https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos.py L22-L30.
GitHub
+1

The weak points are:

the one-signed condition is not enforced in code;

null/unknown settlement units are treated as Celsius in fitters;

registry/test topology do not yet govern the new artifact;

historical ensemble snapshot availability is inferred from target_date and lead window, not from an explicit available_at <= asof predicate.

This is fixable, but it should not be merged as a live-affecting artifact path until those checks exist.

INV-37 assessment

I did not find a clear cross-DB write violation in the files I could read. The concerning K3 path performs cross-DB reads: receipts from zeus-world.db and settlements from zeus-forecasts.db; INV-37 is specifically about cross-DB writes. retired_day0_no_submit_enrichment.py appears to use a single connection/attach-style pattern for enrichment work, and the architecture registry says forecast/trade/world split writes are expected to use the declared attach/savepoint helpers. Evidence: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/db_table_ownership.yaml L7-L20, L38-L40; https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/analysis/retired_day0_no_submit_enrichment.py.
GitHub
+1

Claude Code should still grep for writes in this PR that touch more than one DB handle, but this batch’s main defects are calibration/gate semantics rather than INV-37.

Local verification checklist for Claude Code

Run these against exactly ef421f1fd0 or the candidate fix branch:

Confirm absent files:

Bash
git ls-tree -r ef421f1fd0 src/strategy | grep -E 'bayes_alert|candidates' || true

K3 status matrix tests:

LICENSED → no shrink, no ARM block, credential/admission allowed.

INSUFFICIENT_DATA → no shrink, no ARM block, not converted to FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED.

UNLICENSED → shrink when shrink flag on; ARM/admission not licensed unless a consciously designed post-shrink policy is added and tested.

K3 no-leak tests:

Seed receipts with created_at > decision_time; assert excluded.

Seed verified settlements with target_date >= current_target_date; assert excluded.

Seed duplicate receipts for a target date; assert “last before decision_time,” not global last.

K3 structural-fault tests:

Break edli_no_submit_receipts schema or inject a DB exception.

Assert live coverage gate raises/returns QLCB_COVERAGE_AUTHORITY_FAULT, not INSUFFICIENT_DATA.

EMOS μ-offset tests:

Missing artifact → no correction.

Unactivated cell → no correction.

Activated offset_c < 0 → warms by subtracting negative offset.

Activated offset_c >= 0 → rejected by loader/fitter.

Mean-cold but median-positive residual fixture → no activation.

Null settlement unit fixture → excluded and counted.

EMOS-CI buy-NO tests:

A non-modal bin with low YES upper bound produces positive NO q_lcb from 1 - q_yes_ucb.

Modal bin buy-NO still vetoes.

No live path writes hardcoded q_lcb_no = 0.0 as EMOS_ANALYTIC.

Market-analysis NO regression:

Native NO quote available + non-modal bin + positive conservative edge emits a buy-NO candidate.

Modal bin buy-NO remains unconstructable.

Config/release gate:

Assert live settings reject enabled one-sided caps/haircuts unless whitelisted by explicit operator-law artifact.

Specifically fail on replacement_q_market_anchor_enabled: true.

Post-peak harvester reachability:

Confirm whether HarvestOpportunity is consumed by any submit path.

If yes, fail until forward settlement-graded evidence and live-promotion artifact are enforced.

Governance topology:

Add and run tests that scripts/fit_emos_mu_offset.py, scripts/probe_emos_mu_correction_D4.py, and scripts/scan_emos_mu_residual_all_cities.py are registered in architecture/script_manifest.yaml.

Add topology coverage for state/emos_mu_offset.json.

Per-file coverage list
Read successfully

architecture/_schema_fingerprint.txt — read; fingerprint only. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/_schema_fingerprint.txt

architecture/db_table_ownership.yaml — read; no direct INV-37 violation found in this batch. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/db_table_ownership.yaml

architecture/naming_conventions.yaml — read; no direct blocker. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/naming_conventions.yaml

architecture/script_manifest.yaml — read; missing EMOS μ-offset script registrations. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/script_manifest.yaml

architecture/source_rationale.yaml — read; no EMOS μ-offset registration found. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/source_rationale.yaml

architecture/test_topology.yaml — read; no emos_mu_offset topology coverage found. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/architecture/test_topology.yaml

config/settings.example.json — read; stale/minimal relative to live edli. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.example.json

config/settings.json — read; live one-sided cap enabled. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/config/settings.json

scripts/agent_worktree_merge.py — read; no money-path finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/agent_worktree_merge.py

scripts/automation_analysis.py — read; no money-path finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/automation_analysis.py

scripts/check_live_release_gate.py — read; does not appear to catch the new one-sided-cap/K3 mismatch class. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/check_live_release_gate.py

scripts/data_collection_inventory.py — read; no money-path finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/data_collection_inventory.py

scripts/fit_anchor_representativeness_debias.py — read; settlement unit-null issue. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_anchor_representativeness_debias.py

scripts/fit_emos_mu_offset.py — read; one-signed and unit-null issues. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_emos_mu_offset.py

scripts/fit_opportunity_growth_rate.py — read; no blocking finding in this batch. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_opportunity_growth_rate.py

scripts/fit_sigma_shape_kernel.py — read; candidate-only, verify promotion evidence before use. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/fit_sigma_shape_kernel.py

scripts/install_codegraph_hooks.sh — read; no money-path finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/install_codegraph_hooks.sh

scripts/probe_emos_mu_correction_D4.py — read; diagnostic/probe. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/probe_emos_mu_correction_D4.py

scripts/scan_emos_mu_residual_all_cities.py — read; diagnostic, same unit hygiene concern. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/scan_emos_mu_residual_all_cities.py

scripts/sigma_kernel_holdout_replay.py — read; held-out price-proxy leakage. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/sigma_kernel_holdout_replay.py

scripts/zeus_status.py — read; no money-path finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/scripts/zeus_status.py

src/analysis/retired_day0_no_submit_enrichment.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/analysis/retired_day0_no_submit_enrichment.py

src/analysis/deterministic_edge_report.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/analysis/deterministic_edge_report.py

src/analysis/market_analysis_vnext.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/analysis/market_analysis_vnext.py

src/calibration/anchor_representativeness_debias.py — read; loader shape appears fail-soft/activation-gated. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/anchor_representativeness_debias.py

src/calibration/emos.py — read; μ-offset loader does not enforce negative offset. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos.py

src/calibration/emos_q_builder.py — read; sign application correct only if artifact guarantees offset_c < 0. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/emos_q_builder.py

src/calibration/settlement_backward_coverage.py — read; module semantics good, downstream integration broken. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py

src/config.py — read; config contract drift noted. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/config.py

src/events/no_submit_receipts.py — read; K3 does read q_lcb_5pct receipts, but adapter’s history lookup lacks temporal cutoff. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/events/no_submit_receipts.py

src/events/reactor.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/events/reactor.py

src/signal/ensemble_signal.py — read; settlement preimage/rounding path appears correctly centralized. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/signal/ensemble_signal.py

src/strategy/market_analysis.py — read; NO-side path contradictory/dead. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/market_analysis.py

src/strategy/market_phase.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/market_phase.py

src/strategy/post_peak_backtest.py — read; no blocking finding by itself. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/post_peak_backtest.py

src/strategy/post_peak_harvester.py — read; high-risk one-fill/cap-shaped scanner. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/post_peak_harvester.py

src/strategy/probability_uncertainty.py — read; canonical NO complement contract is correct and useful. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/probability_uncertainty.py

src/strategy/selection_shrinkage.py — read; no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/selection_shrinkage.py

src/strategy/shoulder_cluster_cap.py — read; residual hard gate noted. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/shoulder_cluster_cap.py

src/strategy/stress_scenarios.py — read; diagnostic, no blocking finding. https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/stress_scenarios.py

Could not read because file is absent/404 at ef421f1fd0

These were attempted and are not present in the pinned source tree:

src/strategy/bayes_alert.py

src/strategy/candidates/__init__.py

src/strategy/candidates/c1_joint_tail_bayes.py

src/strategy/candidates/c2_opening_stale_fok.py

src/strategy/candidates/center_buy_calibrated_shadow.py

src/strategy/candidates/center_sell_model_no.py

src/strategy/candidates/center_sell_parity.py

src/strategy/candidates/cross_market_correlation_hedge.py

src/strategy/candidates/imminent_open_capture_posterior_collapse.py

src/strategy/candidates/liquidity_provision_with_heartbeat.py

src/strategy/candidates/neg_risk_basket.py

src/strategy/candidates/opening_inertia_relaxation.py

src/strategy/candidates/resolution_window_maker.py

src/strategy/candidates/settlement_capture_shadow.py

src/strategy/candidates/shoulder_buy_evt.py

src/strategy/candidates/shoulder_impossible_tail_capture.py

src/strategy/candidates/stale_quote_detector.py

src/strategy/candidates/weather_event_arbitrage.py

This is the complete access gap list.

Single biggest correctness risk

The biggest correctness risk is K3 becoming a false authority layer: the new settlement_backward_coverage.py module is trying to fix the climatology-collapse bug, but the live adapter/admission layer still encodes the opposite status semantics and the observation builder is not temporally sealed. That can make the system either suppress thin but valid edges or credential proven-overconfident ones, with real money on. This is worse than a local model error because it contaminates the authority signal that other gates trust.

What the prior partial review likely missed

The src/strategy/candidates/** files are not merely hard to fetch; they are absent at the pinned head.

The K3 module’s semantics and live_admission.SETTLEMENT_COVERAGE_LICENSING_STATUSES are inconsistent.

INSUFFICIENT_DATA is supposed to be non-blocking, but the replacement credential path still treats it as unevaluated/rejected.

The EMOS μ-offset path documents “one-signed-honest” but does not enforce negative offsets.

The EMOS-CI override hardcodes buy-NO q_lcb to zero despite the canonical no_side_samples contract.

The live settings enable a documented one-sided q_lcb cap.

The sigma holdout replay’s price proxy is not clean holdout evidence.

The EMOS μ-offset artifact path is not registered in architecture governance/test topology.