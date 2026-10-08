# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: isolated exact-entry policy handoff repair, 2026-10-08.
"""Exact entry seals policy metadata; statistical redecision needs current proof."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from src.calibration import market_anchored_live_fit as fit
from src.contracts.payoff_q_correction import (
    CalibrationFitScope, CanonicalTrainingManifest, ExactPayoffEntryPolicy,
    SourceIdentityBaseline, PayoffQCorrectionUnavailable,
)
from src.decision_kernel.canonicalization import stable_hash
from src.engine import global_batch_runtime as runtime
from src.solve import solver as solver
from tests.calibration.test_market_anchored_live_fit import _held_entry_reader_fixture
from tests.solve.test_solver_properties import (
    _joint_exact_fixture, _exact_capacity_candidate, _global_candidate,
    _GLOBAL_PROBABILITY_WITNESSES,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def _policy(*, side="NO", mode="TAKER_LIMIT", metric="high"):
    provider = fit.CanonicalMarketAnchoredFitProvider(
        lambda: pytest.fail("metadata must not borrow DB handles"),
        city_timezones={"Warsaw": "Europe/Warsaw"},
    )
    return ExactPayoffEntryPolicy(
        family_key="Warsaw|2026-08-27|high", bin_id="bin-a", side=side,
        token_id="no-token" if side == "NO" else "yes-token", raw_q=1., p0=.51,
        fit_scope=CalibrationFitScope(metric, mode, "MAKER_REST" if mode == "MAKER_REST" else "FOK_FULL_OR_ZERO", "raw-v1"),
        calibration_policy=provider.calibration_policy, q_version="entry-q",
        probability_witness_identity="entry-witness", probability_content_identity="entry-content",
        source_truth_identity="entry-source", sample_matrix_identity="entry-samples",
        exact_payoff_witness_identity="exact-witness", exact_payoff_content_identity="exact-content",
        decision_at_utc=NOW.isoformat(),
    )


def _binding(policy):
    return fit.HeldExactPayoffEntryBinding(policy, "position-a", 7, "cert-a")


def _current(policy, *, captured=NOW):
    return SimpleNamespace(
        family_key=policy.family_key, q_version="current-q", witness_identity="current-witness",
        probability_content_identity="current-content", source_truth_identity="current-source",
        sample_matrix_identity="current-samples", captured_at_utc=captured,
        max_age=timedelta(seconds=2),
        bindings=(SimpleNamespace(bin_id=policy.bin_id, yes_token_id="yes-token", no_token_id="no-token"),),
    )


class _Provider:
    def __init__(self, policy, *, insufficient=False, artifact=None):
        self.calibration_policy = policy.calibration_policy
        self.insufficient = insufficient
        self.current_artifact = artifact
        self.calls = []

    def artifact(self, **kwargs):
        self.calls.append(("artifact", kwargs))
        return self.current_artifact

    def insufficient_support(self, **kwargs):
        self.calls.append(("insufficient", kwargs))
        return self.insufficient


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_explicit_exact_policy_roundtrip_and_no_baseline_claim(side):
    policy = _policy(side=side)
    assert not isinstance(policy, SourceIdentityBaseline)
    assert policy.corrected_q == 1
    assert ExactPayoffEntryPolicy.from_payload(policy.as_cert_fields()) == policy
    for key, value in (("policy", "UNKNOWN"), ("raw_q", .99), ("token_id", "other"),
                       ("statistical_redecision", "RAW_FOREVER"), ("applied", True)):
        bad = dict(policy.as_cert_fields(), **{key: value})
        with pytest.raises(ValueError):
            ExactPayoffEntryPolicy.from_payload(bad)


def test_exact_solver_uses_metadata_route_without_consulting_fit(monkeypatch):
    witness, _child, bindings = _joint_exact_fixture()
    candidate = _exact_capacity_candidate(witness, bindings[0], side="NO")
    metadata_provider = fit.CanonicalMarketAnchoredFitProvider
    class NoCorpusProvider(metadata_provider):
        def artifact(self, **kwargs):
            pytest.fail("proved payoff must never enter calibration")
        def insufficient_support(self, **kwargs):
            pytest.fail("exact entry cannot claim insufficient support")
    monkeypatch.setattr(fit, "CanonicalMarketAnchoredFitProvider", NoCorpusProvider)
    audit = {}
    resolver = runtime._market_anchored_correction_resolver(
        object(), trade_conn=object(), forecast_conn=object(),
        target_context_by_family={},
        prepared_by_family={witness.family_key: SimpleNamespace(probability_witness=witness)},
        calibration_scope_resolver=lambda *_args: CalibrationFitScope("high", "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "raw-v1"),
        market_anchored_fit_artifact_audit=audit,
    )
    policy = solver.resolve_candidate_payoff_q_correction(
        candidate, raw_q=1., witness=witness, resolver=resolver,
        decision_at_utc=witness.captured_at_utc,
    )
    assert isinstance(policy, ExactPayoffEntryPolicy)
    assert policy.matches_witness(witness)
    assert audit["exact_entry_policies"][candidate.candidate_id] == policy.as_cert_fields()
    assert audit["consulted_scopes"] == {}
    assert "source_identity_baselines" not in audit


@pytest.mark.parametrize("invalid", [None, "baseline"])
def test_exact_metadata_route_cannot_return_missing_or_nonexact_policy(invalid):
    witness, _child, bindings = _joint_exact_fixture()
    candidate = _exact_capacity_candidate(witness, bindings[0], side="NO")
    def resolver(*_args):
        pytest.fail("the statistical resolver must not receive exact payoff")
    resolver.exact_entry_policy = lambda *_args: invalid
    with pytest.raises(PayoffQCorrectionUnavailable, match="exact"):
        solver.resolve_candidate_payoff_q_correction(
            candidate, raw_q=1., witness=witness, resolver=resolver,
            decision_at_utc=witness.captured_at_utc,
        )


@pytest.mark.parametrize("q", [0., 1.])
def test_statistical_endpoint_cannot_call_exact_metadata_route(q):
    candidate = _global_candidate(candidate_id="unproved-endpoint", family="statistical-family", side="YES", q=q)
    witness = _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]
    calls = []
    def resolver(*_args):
        calls.append("statistical")
        return None
    resolver.exact_entry_policy = lambda *_args: pytest.fail("an endpoint is not exact authority")
    assert solver.resolve_candidate_payoff_q_correction(
        candidate, raw_q=q, witness=witness, resolver=resolver,
        decision_at_utc=witness.captured_at_utc,
    ) is None
    assert calls == ["statistical"]


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("metric", ["high", "low"])
def test_exact_entry_needs_current_insufficiency_and_fresh_identity(side, metric):
    policy = _policy(side=side, metric=metric)
    provider = _Provider(policy, insufficient=True)
    binding = _binding(policy).at_decision(provider, decision_at=NOW, current_raw_revision="raw-v2")
    assert binding.source_only
    current = binding.bind_current(witness=_current(policy), raw_revision="raw-v2", raw_q=.025, p0=.50)
    assert isinstance(current, SourceIdentityBaseline)
    assert current.raw_q == current.corrected_q == .025
    assert current.raw_probability_revision == "raw-v2"
    assert current.probability_witness_identity != policy.probability_witness_identity
    assert all(call[1]["scope"] == replace(policy.fit_scope, raw_probability_revision="raw-v2") for call in provider.calls)
    assert [call[0] for call in provider.calls] == ["artifact", "insufficient"]
    with pytest.raises(PayoffQCorrectionUnavailable, match="CURRENT_SOURCE_IDENTITY_EXPIRED"):
        binding.bind_current(witness=_current(policy, captured=NOW-timedelta(seconds=3)), raw_revision="raw-v2", raw_q=.025, p0=.50)
    with pytest.raises(PayoffQCorrectionUnavailable, match="CURRENT_SOURCE_IDENTITY_UNAVAILABLE"):
        binding.bind_current(witness=None, raw_revision="raw-v2", raw_q=.025, p0=.50)
    wrong = _current(policy)
    wrong.bindings = (SimpleNamespace(bin_id=policy.bin_id, yes_token_id="other", no_token_id="other"),)
    with pytest.raises(PayoffQCorrectionUnavailable, match="CURRENT_SOURCE_IDENTITY_MISMATCH"):
        binding.bind_current(witness=wrong, raw_revision="raw-v2", raw_q=.025, p0=.50)


def test_exact_entry_unavailable_corpus_is_not_insufficient_or_old_q():
    policy = _policy()
    with pytest.raises(PayoffQCorrectionUnavailable, match="CURRENT_FIT_UNAVAILABLE"):
        _binding(policy).at_decision(_Provider(policy), decision_at=NOW, current_raw_revision="raw-v2")
    provider = _Provider(policy, insufficient=True)
    provider.calibration_policy = replace(provider.calibration_policy, lambda_=provider.calibration_policy.lambda_+1)
    with pytest.raises(PayoffQCorrectionUnavailable, match="CURRENT_POLICY_MISMATCH"):
        _binding(policy).at_decision(provider, decision_at=NOW, current_raw_revision="raw-v2")
    assert provider.calls == []


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("mode,anchor", [("TAKER_LIMIT", .42), ("MAKER_REST", .40)])
def test_exact_entry_current_fit_keeps_entry_price_feature(monkeypatch, side, mode, anchor):
    trade, world, artifact, *_ = _held_entry_reader_fixture(monkeypatch, side=side)
    try:
        policy = _policy(side=side, mode=mode)
        scope = replace(policy.fit_scope, raw_probability_revision="raw-v2")
        manifest = CanonicalTrainingManifest.build(
            scope_hash=scope.as_payload()["scope_hash"], corpus_revision=fit.CANONICAL_CORPUS_REVISION,
            training_cutoff=NOW.isoformat(), row_count=20, event_count=20, weight_sum=20.,
            max_fill_available_at=(NOW-timedelta(days=1)).isoformat(),
            max_label_available_at=(NOW-timedelta(days=1)).isoformat(), input_hash="a"*64,
        )
        artifact = replace(artifact, training_cutoff=NOW.isoformat(), training_manifest=manifest)
        provider = _Provider(policy, artifact=artifact)
        binding = _binding(policy).at_decision(provider, decision_at=NOW, current_raw_revision="raw-v2")
        assert not binding.source_only
        p0 = binding.fit_scope.current_buy_price_anchor(best_bid=.39, best_ask=.42, min_tick=.01)
        assert p0 == anchor and p0 != policy.p0
        corrected = binding.corrected_probability(
            family_key=policy.family_key, bin_id=policy.bin_id, token_id=policy.token_id,
            side=side, raw_q=.025, p0=p0, city="Warsaw", target_date=date(2026,8,27), decision_at=NOW,
        )
        assert corrected.raw_q == .025 and corrected.p0 == anchor
        assert corrected.calibration_policy == policy.calibration_policy
        assert corrected.fit_scope == scope and corrected.corrected_q != policy.raw_q
        assert [call[0] for call in provider.calls] == ["artifact"]
    finally:
        trade.close(); world.close()


def _persist_exact_fixture(monkeypatch):
    trade, world, *_ = _held_entry_reader_fixture(monkeypatch)
    policy = _policy()
    cert = json.loads(world.execute("SELECT payload_json FROM decision_certificates").fetchone()[0])
    cert.update(
        market_anchored_correction=policy.as_cert_fields(), global_execution_mode="TAKER_LIMIT",
        q_version=policy.q_version, payoff_q_point=1., payoff_q_action=1.,
        global_probability_witness_identity=policy.probability_witness_identity,
        probability_semantics_revision=policy.fit_scope.raw_probability_revision,
        temperature_metric="high", global_selection_decision_at=NOW.isoformat(),
        raw_calibration_input=dict(
            probability_input_kind="TYPED_EXACT_PAYOFF", correction_applied=False,
            p0_basis="GROSS_NATIVE_TOKEN_PRICE", schema_version=1,
            capture_basis="GLOBAL_CERTIFICATE_INPUT", exact_payoff=1,
            raw_q_held=1., p0_held=.51, family_key=policy.family_key, bin_id=policy.bin_id,
            side=policy.side, token_id=policy.token_id, probability_witness_identity=policy.probability_witness_identity,
            exact_payoff_witness_identity=policy.exact_payoff_witness_identity,
            exact_payoff_content_identity=policy.exact_payoff_content_identity,
            sample_hash=policy.sample_matrix_identity, execution_mode="TAKER_LIMIT", candidate_id="candidate-a",
        ),
    )
    audit = {"probability_manifest": [[policy.family_key, policy.probability_witness_identity]],
             "market_anchored_fit_artifact_audit": {"revision": "canonical_entry_fit_artifact_audit_v1",
                 "exact_entry_policies": {"candidate-a": policy.as_cert_fields()}}}
    monkeypatch.setattr(fit, "_load_held_audit_context", lambda *_args, **_kwargs: audit)
    monkeypatch.setattr(fit, "_receipt_summary", lambda *_args, **_kwargs: cert["global_auction_receipt"])
    _save(world, cert)
    return trade, world, cert, policy, audit


def _save(world, cert):
    world.execute("UPDATE decision_certificates SET payload_json=?, payload_hash=?", (json.dumps(cert), stable_hash(cert)))


def _load(trade, world, policy):
    return fit.load_held_entry_calibration(trade, position_id="position-a", token_id=policy.token_id,
                                         side=policy.side, world_conn=world)


def test_exact_entry_reader_reloads_identical_policy_and_rejects_legacy(monkeypatch):
    trade, world, cert, policy, _audit = _persist_exact_fixture(monkeypatch)
    try:
        assert _load(trade, world, policy).entry_policy == policy
        assert _load(trade, world, policy) == _load(trade, world, policy)
        cert["market_anchored_correction"] = {"applied": False}
        _save(world, cert)
        with pytest.raises(PayoffQCorrectionUnavailable, match="EXACT_ENTRY_POLICY_MISSING"):
            _load(trade, world, policy)
    finally:
        trade.close(); world.close()


@pytest.mark.parametrize("mutation", ["policy", "token", "side", "witness", "audit", "manifest", "clock"])
def test_exact_entry_reader_rejects_mismatched_sealed_proof(monkeypatch, mutation):
    trade, world, cert, policy, audit = _persist_exact_fixture(monkeypatch)
    try:
        if mutation == "policy": cert["market_anchored_correction"]["policy"] = "UNKNOWN"
        elif mutation == "token": cert["raw_calibration_input"]["token_id"] = "other"
        elif mutation == "side": cert["raw_calibration_input"]["side"] = "YES"
        elif mutation == "witness": cert["raw_calibration_input"]["exact_payoff_witness_identity"] = "other"
        elif mutation == "audit": audit["market_anchored_fit_artifact_audit"]["exact_entry_policies"] = {}
        elif mutation == "manifest": audit["probability_manifest"] = []
        elif mutation == "clock": cert["global_selection_decision_at"] = (NOW-timedelta(seconds=1)).isoformat()
        _save(world, cert)
        with pytest.raises(PayoffQCorrectionUnavailable, match="EXACT_ENTRY_POLICY_"):
            _load(trade, world, policy)
    finally:
        trade.close(); world.close()
