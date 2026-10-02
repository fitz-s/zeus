# Created: 2026-10-01
# Last reused or audited: 2026-10-01
# Authority basis: operator speed-first directive 2026-10-01 (candidate_score_s
#   4.7-11 s per ~2,100-candidate cut); memoization must leave every score,
#   rejection and winner identical.
"""Family-level memoization in candidate scoring changes no decision.

The per-candidate cost was family- and scope-level work repeated for every
leg: hashing the family sample matrix, parsing the ~200 KB posterior
provenance, and re-asking the fit provider about one of at most four scopes.
Each memo is keyed on the exact input it replaces, so a cut scored with the
memo must equal the same cut scored with no memo, evaluation for evaluation.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.calibration.market_anchored_residual import (
    CLIP_D, LEAD_BUCKETS, P_CLIP_HI, P_CLIP_LO, ResidualCalibratorArtifact,
)
from src.contracts.payoff_q_correction import (
    CalibrationFitScope, PayoffQCorrectionUnavailable,
)
from src.engine import event_reactor_adapter as adapter
from src.engine import global_batch_runtime as runtime
from src.solve import solver as S
from tests.solve import test_solver_properties as T


def _family(rng, family, *, bins=5, draws=400):
    bindings = tuple(
        S.OutcomeTokenBinding(
            bin_id=f"{family}-b{j}", condition_id=f"{family}-c{j}",
            yes_token_id=f"{family}-y{j}", no_token_id=f"{family}-n{j}",
        )
        for j in range(bins)
    )
    samples = rng.dirichlet(np.ones(bins), size=draws)
    point = samples.mean(axis=0)
    fields = dict(
        family_key=family, bindings=bindings, q_version=f"qv-{family}",
        resolution_identity=f"res-{family}", topology_identity=f"topo-{family}",
        posterior_identity_hash=f"post-{family}", source_truth_identity=f"src-{family}",
        authority_certificate_hash=f"cert-{family}", band_alpha=0.05,
        band_basis="joint_q_band_samples", yes_point_q=point / point.sum(),
        yes_q_samples=samples,
        captured_at_utc=T._DECISION_AT - timedelta(milliseconds=100),
    )
    identity = S.joint_probability_witness_identity(**fields)
    return S.JointOutcomeProbabilityWitness(
        **fields, max_age=timedelta(seconds=1), witness_identity=identity,
    )


def _cut(seed, *, families=6):
    rng = np.random.default_rng(seed)
    witnesses, candidates = {}, []
    for f in range(families):
        witness = _family(rng, f"fam{f}")
        witnesses[witness.family_key] = witness
        for j, binding in enumerate(witness.bindings):
            for side in ("YES", "NO"):
                q = witness.yes_point_q[j] if side == "YES" else 1 - witness.yes_point_q[j]
                base = min(0.97, max(0.02, q + rng.normal(0.0, 0.06)))
                token = binding.yes_token_id if side == "YES" else binding.no_token_id
                curve = T._global_curve(
                    side=side, token=token, fee="0.02", min_order="5",
                    levels=tuple(
                        (f"{min(0.999, base + 0.01 * k):.3f}", str(int(rng.integers(5, 300))))
                        for k in range(3)
                    ),
                )
                candidates.append(S.GlobalSingleOrderCandidate(
                    candidate_id=f"{binding.bin_id}-{side}", family_key=witness.family_key,
                    bin_id=binding.bin_id, condition_id=binding.condition_id, side=side,
                    token_id=token, probability_witness_identity=witness.witness_identity,
                    book_snapshot_id=curve.snapshot_id,
                    book_captured_at_utc=witness.captured_at_utc,
                    execution_curve_identity=S.executable_curve_identity(curve),
                    ledger_snapshot_id="ledger-current", executable_cost_curve=curve,
                    resolution_identity=witness.resolution_identity, neg_risk=False,
                ))
    return witnesses, candidates


def _artifact():
    return ResidualCalibratorArtifact(
        alpha={"day0": 0.15, "day1": 0.25, "day2": 0.35}, beta=0.4, lambda_=1.0,
        clip_d=CLIP_D, p_clip=(P_CLIP_LO, P_CLIP_HI), lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-07-01T00:00:00Z", n_train=40, n_excluded=0,
        excluded_reasons={}, param_hash="memo-fixture",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("Tokyo", "Asia/Tokyo"),),
    )


class _CountingProvider:
    """HIGH scopes have a fit; LOW scopes are insufficient (source baseline)."""

    calls: list[tuple[str, object]] = []
    calibration_policy = None

    def __init__(self, *_args, **_kwargs):
        pass

    def artifact(self, *, scope, now, deadline_monotonic):
        type(self).calls.append(("artifact", scope))
        return _artifact() if scope.metric == "high" else None

    def insufficient_support(self, *, scope, now, deadline_monotonic):
        type(self).calls.append(("insufficient", scope))
        return True


def _resolver(witnesses, *, deadline_monotonic=None):
    prepared = {
        family: SimpleNamespace(probability_witness=witness)
        for family, witness in witnesses.items()
    }
    return runtime._market_anchored_correction_resolver(
        object(), trade_conn=object(), forecast_conn=object(),
        target_context_by_family={family: ("Tokyo", date(2026, 7, 11)) for family in witnesses},
        prepared_by_family=prepared,
        calibration_scope_resolver=lambda candidate, prep: adapter._global_entry_calibration_fit_scope(
            candidate,
            metric="high" if int(candidate.family_key[3:]) % 2 == 0 else "low",
            raw_probability_revision="raw-v1",
        ),
        deadline_monotonic=deadline_monotonic,
    )


@pytest.fixture
def counting_provider(monkeypatch):
    _CountingProvider.calls = []
    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.CanonicalMarketAnchoredFitProvider",
        _CountingProvider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")},
    )
    monkeypatch.setattr(
        adapter, "_prepared_global_probability_semantics_revision", lambda *_: "raw-v1",
    )
    return _CountingProvider


def _canonical(decision):
    """Every evaluation field and the whole decision, in a stable order."""

    return (
        repr(decision),
        sorted(repr(sorted(vars(row).items())) for row in decision.candidate_evaluations),
    )


def _select(candidates, witnesses, resolver):
    return T._global_select(
        candidates, cap="50", cash="1000", floor="1000", ceiling="1000",
        probability_witnesses=witnesses, payoff_q_correction_resolver=resolver,
    )


@pytest.mark.parametrize("seed", [3, 17, 29, 41])
def test_memoized_cut_equals_unmemoized_cut(counting_provider, seed):
    witnesses, candidates = _cut(seed)
    memoized = _select(candidates, witnesses, _resolver(witnesses))
    memo_calls = len(counting_provider.calls)

    # Reference: a fresh resolver per leg starts with an empty scope memo, and a
    # freshly built cut has no cached witness identity, so nothing is reused.
    fresh_witnesses, fresh_candidates = _cut(seed)

    def unmemoized(candidate, raw_q, p0, decision_at):
        return _resolver(fresh_witnesses)(candidate, raw_q, p0, decision_at)

    counting_provider.calls = []
    reference = _select(fresh_candidates, fresh_witnesses, unmemoized)
    reference_calls = len(counting_provider.calls)

    assert _canonical(memoized) == _canonical(reference)
    assert memoized.candidate is not None
    assert {"SELECTED", "SCORED", "REJECTED"} <= {
        row.status for row in memoized.candidate_evaluations
    }
    assert reference_calls > memo_calls
    # high/low metric x one execution contract, each asked once per kind.
    assert memo_calls <= 3


def test_scope_answer_is_asked_once_per_cut(counting_provider):
    witnesses, candidates = _cut(5)
    _select(candidates, witnesses, _resolver(witnesses))
    asked = {}
    for kind, scope in counting_provider.calls:
        asked[(kind, scope)] = asked.get((kind, scope), 0) + 1
    assert asked and set(asked.values()) == {1}


def test_answer_at_or_past_deadline_is_never_reused(counting_provider):
    witnesses, candidates = _cut(5)
    resolver = _resolver(witnesses, deadline_monotonic=time.monotonic() - 1.0)
    # fam0 is a HIGH scope; this fake fits regardless of the deadline, so what
    # is proved is only that the provider (which owns the timeout) is asked
    # for every leg rather than served a remembered answer.
    for candidate in [c for c in candidates if c.family_key == "fam0"][:3]:
        try:
            resolver(candidate, 0.3, 0.3, T._DECISION_AT)
        except PayoffQCorrectionUnavailable:
            pass
    assert [kind for kind, _ in counting_provider.calls] == ["artifact"] * 3


def test_witness_identities_are_cached_exactly_and_frozen():
    rng = np.random.default_rng(9)
    witness = _family(rng, "famX")
    assert witness.sample_matrix_identity == S.probability_sample_matrix_identity(
        witness.yes_q_samples
    )
    assert witness.probability_content_identity == S.joint_probability_content_identity(
        family_key=witness.family_key, bindings=witness.bindings,
        q_version=witness.q_version, resolution_identity=witness.resolution_identity,
        topology_identity=witness.topology_identity,
        posterior_identity_hash=witness.posterior_identity_hash,
        source_truth_identity=witness.source_truth_identity,
        band_alpha=witness.band_alpha, band_basis=witness.band_basis,
        yes_point_q=witness.yes_point_q, yes_q_samples=witness.yes_q_samples,
    )
    with pytest.raises(ValueError):
        witness.yes_q_samples[0, 0] = 0.5
    with pytest.raises(ValueError):
        witness.yes_point_q[0] = 0.5


def test_witness_owns_its_arrays_so_a_caller_write_cannot_stale_the_cache():
    rng = np.random.default_rng(11)
    samples = rng.dirichlet(np.ones(3), size=400)
    point = samples.mean(axis=0)
    bindings = tuple(
        S.OutcomeTokenBinding(bin_id=f"b{j}", condition_id=f"c{j}",
                              yes_token_id=f"y{j}", no_token_id=f"n{j}")
        for j in range(3)
    )
    fields = dict(
        family_key="fam", bindings=bindings, q_version="qv", resolution_identity="r",
        topology_identity="t", posterior_identity_hash="p", source_truth_identity="s",
        authority_certificate_hash="a", band_alpha=0.05, band_basis="joint_q_band_samples",
        yes_point_q=point, yes_q_samples=samples,
        captured_at_utc=T._DECISION_AT,
    )
    witness = S.JointOutcomeProbabilityWitness(
        **fields, max_age=timedelta(seconds=1),
        witness_identity=S.joint_probability_witness_identity(**fields),
    )
    before = witness.sample_matrix_identity
    samples[0] = samples[0][::-1]  # the caller keeps a writable array
    assert samples.flags.writeable
    assert witness.sample_matrix_identity == before
    assert S.probability_sample_matrix_identity(witness.yes_q_samples) == before
    # A rebound witness shares its parent's frozen array instead of copying it.
    rebound_fields = {**fields, "yes_point_q": witness.yes_point_q,
                      "yes_q_samples": witness.yes_q_samples}
    rebound = S.JointOutcomeProbabilityWitness(
        **rebound_fields, max_age=timedelta(seconds=1),
        witness_identity=S.joint_probability_witness_identity(**rebound_fields),
    )
    assert rebound.yes_q_samples is witness.yes_q_samples
    # A read-only view of a writable base is still copied.
    view = rng.dirichlet(np.ones(3), size=400)
    frozen_view = view.view()
    frozen_view.flags.writeable = False
    view_fields = {**fields, "yes_point_q": view.mean(axis=0), "yes_q_samples": frozen_view}
    viewed = S.JointOutcomeProbabilityWitness(
        **view_fields, max_age=timedelta(seconds=1),
        witness_identity=S.joint_probability_witness_identity(**view_fields),
    )
    assert viewed.yes_q_samples is not frozen_view
    assert not np.shares_memory(viewed.yes_q_samples, view)


def test_posterior_revision_memo_is_keyed_on_exact_provenance_bytes(monkeypatch):
    parsed = []
    real = adapter._current_evidence_shape

    def counting_shape(provenance):
        parsed.append(provenance)
        return real(provenance)

    monkeypatch.setattr(adapter, "_current_evidence_shape", counting_shape)
    monkeypatch.setattr(adapter, "_POSTERIOR_REVISION_BY_DIGEST", {})

    def provenance(revision, pad=""):
        return json.dumps({
            "pad": pad,
            "bayes_precision_fusion": {"current_evidence_shape": {"semantics_revision": revision}},
        }, sort_keys=True)

    first = provenance("rev-a")
    assert adapter._posterior_semantics_revision(first) == "rev-a"
    assert adapter._posterior_semantics_revision(first) == "rev-a"
    assert len(parsed) == 1
    # A rewritten row (new bytes) is parsed again, never served a stale answer.
    assert adapter._posterior_semantics_revision(provenance("rev-b")) == "rev-b"
    assert adapter._posterior_semantics_revision(provenance("rev-a", pad="x")) == "rev-a"
    assert len(parsed) == 3
    assert adapter._posterior_semantics_revision("{}") is None
    assert adapter._posterior_semantics_revision("not json") is None


def test_posterior_revision_reads_the_row_bound_to_both_identities(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute(
        "CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, "
        "posterior_identity_hash TEXT, provenance_json TEXT)"
    )
    shape = {"bayes_precision_fusion": {"current_evidence_shape": {"semantics_revision": "rev-z"}}}
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (7, 'hash-7', ?)", (json.dumps(shape),)
    )
    prepared = SimpleNamespace(
        posterior_id=7,
        probability_witness=SimpleNamespace(q_version="plain", posterior_identity_hash="hash-7"),
    )
    assert adapter._prepared_global_probability_semantics_revision(prepared, conn) == "rev-z"
    prepared.probability_witness.posterior_identity_hash = "other"
    assert adapter._prepared_global_probability_semantics_revision(prepared, conn) is None


def test_decimal_capital_inputs_are_unchanged_by_memo(counting_provider):
    # Same cut, two capital limits: the memo is per cut and never carries an
    # answer across caps (each resolver is built per cut).
    witnesses, candidates = _cut(23)
    small = T._global_select(
        candidates, cap="5", cash="1000", floor="1000", ceiling="1000",
        probability_witnesses=witnesses, payoff_q_correction_resolver=_resolver(witnesses),
    )
    large = T._global_select(
        candidates, cap="500", cash="1000", floor="1000", ceiling="1000",
        probability_witnesses=witnesses, payoff_q_correction_resolver=_resolver(witnesses),
    )
    assert small.cost_usd <= Decimal("5")
    assert large.cost_usd >= small.cost_usd


def test_keep_valuation_outside_any_cut_equals_the_in_cut_correction(counting_provider, monkeypatch):
    """A keep valuation runs outside every cut (the C3 tick, a wake pass): no
    cut memo exists, so it calls the selector's public resolver entry, which
    computes fully. The correction it acts on equals the one an in-cut score
    of the same inputs uses."""

    witnesses, candidates = _cut(5, families=2)
    resolver = _resolver(witnesses)
    # In-cut: the selector's own scoring warms the cut-local scope memo.
    in_cut_decision = _select(candidates, witnesses, resolver)
    assert in_cut_decision.candidate_evaluations
    for candidate in candidates[:4]:
        witness = witnesses[candidate.family_key]
        raw_q = S.family_payoff_point_q(witness, bin_id=candidate.bin_id, side=candidate.side)
        in_cut = S.resolve_candidate_payoff_q_correction(
            candidate, raw_q=raw_q, witness=witness, resolver=resolver,
            decision_at_utc=T._DECISION_AT,
        )
        # Outside any cut: a fresh resolver, no memo, the same public entry.
        counting_provider.calls = []
        outside = S.resolve_candidate_payoff_q_correction(
            candidate, raw_q=raw_q, witness=witness, resolver=_resolver(witnesses),
            decision_at_utc=T._DECISION_AT,
        )
        assert counting_provider.calls, "outside a cut the provider is asked, never a stale memo"
        assert repr(outside) == repr(in_cut)


def test_keep_valuation_calls_the_selectors_public_resolver_entry(monkeypatch):
    """The C3 valuation reaches the correction only through the selector's
    public entry (``resolve_candidate_payoff_q_correction``) and the resolver
    factory (``_market_anchored_correction_resolver``); it never reads a
    cut-local memo."""

    import inspect

    import src.execution.staleness_cancel as C

    value_source = inspect.getsource(C.value_standing_entry)
    capture_source = inspect.getsource(C._capture_standing_entry_values)
    assert "resolve_candidate_payoff_q_correction(" in value_source
    assert "runtime._market_anchored_correction_resolver(" in capture_source
    for memo in ("scoped_answers", "_POSTERIOR_REVISION_BY_DIGEST", "scoped_fit"):
        assert memo not in value_source and memo not in capture_source
