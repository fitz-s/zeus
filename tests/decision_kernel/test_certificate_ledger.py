# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §9, §14 PR-B.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateSemanticDriftError, CertificateVerificationError
from src.decision_kernel.ledger import CompileFailure, DecisionCertificateLedger


def _cert(certificate_type: str, semantic_key: str, payload: dict, parents=()):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def test_certificate_ledger_persists_verified_certificate_and_edges():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    parent = _cert("ClockModeCertificate", "clock:event", {"mode": "NO_SUBMIT"})
    child = _cert(
        "CausalEventCertificate",
        "event:e1",
        {"event_id": "e1"},
        (ParentEdge("clock_mode", parent.certificate_hash, parent.certificate_type),),
    )

    ledger.persist_all((parent, child))

    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 2
    edge = conn.execute("SELECT parent_role FROM decision_certificate_edges").fetchone()
    assert edge["parent_role"] == "clock_mode"


def test_compile_failures_persisted():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    failure = CompileFailure(
        event_id="event-1",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        mode="NO_SUBMIT",
        claim_type="no_submit_dry_run_decision",
        stage="FDR",
        reason_code="TESTING_PROTOCOL_MISSING",
        parent_hashes=("abc",),
    )

    ledger.persist_failures((failure,))
    row = conn.execute("SELECT reason_code, parent_hashes_json FROM decision_compile_failures").fetchone()
    assert row["reason_code"] == "TESTING_PROTOCOL_MISSING"
    assert "abc" in row["parent_hashes_json"]


def test_ledger_rejects_no_submit_certificate_with_proof_accepted_false():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"proof_accepted": False})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="proof_accepted=true"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_certificate_with_submitted_true():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"submitted": True})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="submitted=true"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_certificate_missing_forecast_required_parents():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    no_submit = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event-1:intent-1",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=_no_submit_payload(),
        parent_edges=(),
        parent_certificates=(),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="missing parents"):
        ledger.persist_all((no_submit,))


def test_ledger_rejects_no_submit_certificate_with_execution_command_id():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"execution_command_id": "cmd-1"})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="execution command"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_quote_token_candidate_token_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.QUOTE_FEASIBILITY: {"token_id": "other-token", "selected_token_id": "other-token"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="candidate.selected_token_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_forecast_snapshot_causal_snapshot_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.FORECAST_AUTHORITY: {"snapshot_id": "other-snapshot"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="source_truth.snapshot_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_fdr_family_topology_family_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.FDR: {"fdr_family_id": "other-family"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="family_closure.family_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_kelly_cost_basis_cost_model_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.KELLY_DRY_RUN: {"cost_basis_id": "other-cost"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="kelly.cost_basis_id"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_generated_certificate_requires_generated_at_decision_time_payload():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"generated_at_decision_time": False})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="generated_at_decision_time=true"):
        ledger.persist_all(parents + (no_submit,))


def test_non_generated_authority_certificate_cannot_use_generated_at_decision_time_semantics():
    conn = _conn()
    ledger = DecisionCertificateLedger(conn)
    cert = _cert("ClockModeCertificate", "clock:generated", {"mode": "NO_SUBMIT", "generated_at_decision_time": True})

    with pytest.raises(CertificateVerificationError, match="only allowed for generated decision"):
        ledger.persist_all((cert,))


def test_no_submit_rejects_missing_decision_source():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"decision_source": None})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="unsupported no-submit decision_source"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_rejects_unknown_decision_source():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"decision_source": "forcast"})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="unsupported no-submit decision_source"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_rejects_day0_or_other_while_day0_disabled():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"decision_source": "day0_or_other"})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="unsupported no-submit decision_source"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_forecast_always_requires_forecast_parent_set():
    parents, no_submit = _minimal_no_submit_graph()
    weak_parents = tuple(parent for parent in parents if parent.certificate_type != claims.FORECAST_AUTHORITY)
    no_submit = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event-1:intent-1",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=no_submit.header.decision_time,
        source_available_at=no_submit.header.source_available_at,
        agent_received_at=no_submit.header.agent_received_at,
        persisted_at=no_submit.header.persisted_at,
        payload=no_submit.payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in weak_parents),
        parent_certificates=weak_parents,
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="ForecastAuthorityCertificate"):
        ledger.persist_all(weak_parents + (no_submit,))


def test_ledger_rejects_no_submit_forecast_missing_required_steps():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"required_steps": ()}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="required_steps"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_forecast_missing_applied_validations():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"applied_validations": ()}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="applied_validations"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_forecast_observed_members_below_expected_members():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"observed_members": 50}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="observed_members"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_calibration_missing_authority():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.CALIBRATION: {"authority": None}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="calibration.authority"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_calibration_missing_input_space():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.CALIBRATION: {"input_space": None}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="calibration.input_space"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_unit_mismatch():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.BELIEF: {"unit": "C"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="belief.unit"):
        ledger.persist_all(parents + (no_submit,))


def test_persist_all_rejects_forecast_parent_missing_coverage_readiness():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"coverage_readiness_status": None}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="coverage_readiness_status"):
        ledger.persist_all(parents + (no_submit,))


def test_persist_all_rejects_forecast_parent_missing_required_validation():
    validations = tuple(item for item in _required_forecast_validations() if item != "authority_verified")
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"applied_validations": validations}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="authority_verified"):
        ledger.persist_all(parents + (no_submit,))


def test_persist_all_rejects_forecast_parent_metric_identity_mismatch():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"members_extrema_metric_identity": "low"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="members_extrema_metric_identity"):
        ledger.persist_all(parents + (no_submit,))


def test_forecast_certificate_members_json_hash_required():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.FORECAST_AUTHORITY: {"members_json_hash": None}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="members_json_hash"):
        ledger.persist_all(parents + (no_submit,))


def test_belief_members_json_hash_matches_forecast():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.BELIEF: {"members_json_hash": "different-members"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="belief.members_json_hash"):
        ledger.persist_all(parents + (no_submit,))


def test_low_metric_requires_daily_min_transform():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={
            claims.FAMILY_CLOSURE: {"metric": "low"},
            claims.FORECAST_AUTHORITY: {
                "temperature_metric": "low",
                "members_extrema_metric_identity": "low",
                "members_extrema_transform": "daily_max",
            },
        }
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="members_extrema_transform"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_rejects_projection_hash_mismatch():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"projection_hash": "bad-hash"})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="projection_hash mismatch"):
        ledger.persist_all(parents + (no_submit,))


def test_projection_payload_matches_no_submit_payload():
    parents, no_submit = _minimal_no_submit_graph()
    expected_projection = {
        "event_id": no_submit.payload["event_id"],
        "final_intent_id": no_submit.payload["final_intent_id"],
        "side_effect_status": no_submit.payload["side_effect_status"],
        "proof_accepted": no_submit.payload["proof_accepted"],
        "submitted": no_submit.payload["submitted"],
        "executable_snapshot_id": no_submit.payload["executable_snapshot_id"],
    }

    assert no_submit.payload["projection_hash"] == stable_hash(expected_projection)
    DecisionCertificateLedger(_conn()).persist_all(parents + (no_submit,))


def test_insert_idempotent_detects_existing_payload_hash_corruption():
    conn = _conn()
    ledger = DecisionCertificateLedger(conn)
    cert = _cert("ClockModeCertificate", "clock:event", {"mode": "NO_SUBMIT"})
    ledger.persist_all((cert,))
    conn.execute(
        "UPDATE decision_certificates SET payload_json = ? WHERE certificate_id = ?",
        ('{"mode":"CORRUPT"}', cert.certificate_id),
    )

    with pytest.raises(CertificateSemanticDriftError, match="PAYLOAD_HASH_CORRUPT"):
        ledger.persist_all((cert,))


def test_certificate_ledger_audit_detects_payload_hash_mismatch():
    conn = _conn()
    ledger = DecisionCertificateLedger(conn)
    cert = _cert("ClockModeCertificate", "clock:event", {"mode": "NO_SUBMIT"})
    ledger.persist_all((cert,))
    conn.execute(
        "UPDATE decision_certificates SET payload_json = ? WHERE certificate_id = ?",
        ('{"mode":"CORRUPT"}', cert.certificate_id),
    )

    with pytest.raises(CertificateSemanticDriftError):
        ledger.insert_idempotent(cert)


def test_no_submit_rejects_quote_cost_source_midpoint():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.QUOTE_FEASIBILITY: {"cost_source": "midpoint"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_rejects_cost_model_cost_source_complement():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.COST_MODEL: {"cost_source": "complement_price"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="cost.cost_source"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_rejects_quote_source_kind_last_trade():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.QUOTE_FEASIBILITY: {"quote_source_kind": "last_trade"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="quote.quote_source_kind"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_requires_forbidden_cost_source_false():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.COST_MODEL: {"forbidden_cost_source": True}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="cost.forbidden_cost_source"):
        ledger.persist_all(parents + (no_submit,))


def test_buy_yes_requires_native_orderbook_ask_cost_source():
    parents, no_submit = _minimal_no_submit_graph(parent_payload_overrides={claims.COST_MODEL: {"cost_source": "native_orderbook_bid"}})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="cost.cost_source"):
        ledger.persist_all(parents + (no_submit,))


def test_buy_no_requires_native_orderbook_ask_cost_source():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "buy_no"},
            claims.QUOTE_FEASIBILITY: {"cost_source": "native_orderbook_ask"},
            claims.COST_MODEL: {"cost_source": "native_orderbook_ask"},
        }
    )

    DecisionCertificateLedger(_conn()).persist_all(parents + (no_submit,))


def test_sell_yes_requires_native_orderbook_bid_cost_source():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "sell_yes"},
            claims.QUOTE_FEASIBILITY: {"cost_source": "native_orderbook_bid"},
            claims.COST_MODEL: {"cost_source": "native_orderbook_bid"},
        }
    )

    DecisionCertificateLedger(_conn()).persist_all(parents + (no_submit,))


def test_buy_no_rejects_native_orderbook_bid_cost_source():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "buy_no"},
            claims.QUOTE_FEASIBILITY: {"cost_source": "native_orderbook_bid"},
            claims.COST_MODEL: {"cost_source": "native_orderbook_bid"},
        }
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_actionable_with_generic_verifier_only_path():
    cert = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:forged",
        {
            "event_id": "event-1",
            "event_type": "FORECAST_SNAPSHOT_READY",
            "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
            "action_score": -1.0,
            "trade_score": -1.0,
            "native_quote_available": False,
        },
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="LIVE mode|action_score|native_quote"):
        ledger.insert_idempotent(cert)


def test_ledger_rejects_execution_command_with_generic_verifier_only_path():
    cert = _cert(
        claims.EXECUTION_COMMAND,
        "execution-command:forged",
        {
            "event_id": "event-1",
            "execution_command_id": "cmd-1",
            "submitted": True,
        },
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="LIVE mode|submitted=false|ActionableTradeCertificate"):
        ledger.insert_idempotent(cert)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _minimal_no_submit_graph(
    *,
    no_submit_payload: dict | None = None,
    parent_payload_overrides: dict[str, dict] | None = None,
):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    parent_payload_overrides = parent_payload_overrides or {}
    parent_payloads = {
        claims.CLOCK_MODE: {"mode": "NO_SUBMIT"},
        claims.CAUSAL_EVENT: {"event_id": "event-1", "causal_snapshot_id": "snap-1", "payload_hash": "payload-hash-1", "source": "forecast_live"},
        claims.SOURCE_TRUTH: {
            "event_id": "event-1",
            "event_source": "forecast_live",
            "source_status": "LIVE_ELIGIBLE",
            "source_authority_id": "read_executable_forecast",
            "source_reason_code": None,
            "source_id": "opendata",
            "source_run_id": "run-1",
            "snapshot_id": "snap-1",
            "causal_snapshot_id": "snap-1",
            "payload_hash": "payload-hash-1",
            "derived_from_certificate_type": claims.FORECAST_AUTHORITY,
            "derived_from_snapshot_id": "snap-1",
            "derived_from_reader_status": "LIVE_ELIGIBLE",
        },
        claims.MARKET_TOPOLOGY: {"family_id": "family-1"},
        claims.FAMILY_CLOSURE: {
            "family_id": "family-1",
            "bin_labels_hash": "bins-hash-1",
            "bin_units": ("F",),
            "metric": "high",
            "target_date": "2026-05-25",
        },
        claims.FORECAST_AUTHORITY: {
            "snapshot_id": "snap-1",
            "reader_authority": "read_executable_forecast",
            "reader_status": "LIVE_ELIGIBLE",
            "forecast_source_id": "opendata",
            "source_run_id": "run-1",
            "temperature_metric": "high",
            "horizon_profile": "default",
            "coverage_readiness_status": "LIVE_ELIGIBLE",
            "coverage_completeness_status": "COMPLETE",
            "source_run_completeness_status": "COMPLETE",
            "required_steps": (0,),
            "observed_steps": (0,),
            "expected_members": 51,
            "observed_members": 51,
            "applied_validations": _required_forecast_validations(),
            "members_extrema_metric_identity": "high",
            "members_extrema_transform": "daily_max",
            "members_json_source": "ensemble_snapshots_v2.daily_extrema",
            "members_json_hash": "members-hash-1",
            "target_local_date": "2026-05-25",
            "city_timezone": "America/Chicago",
            "local_date_window_hash": "window-hash-1",
            "bin_labels_hash": "bins-hash-1",
            "unit": "F",
            "unit_authority_source": "ensemble_snapshots_v2.settlement_unit",
        },
        claims.CALIBRATION: {
            "calibrator_model_key": "model-1",
            "model_hash": "model-hash-1",
            "authority": "VERIFIED",
            "maturity_level": 1,
            "input_space": "width_normalized_density",
            "horizon_profile": "default",
            "training_cutoff": "2026-05-01T00:00:00+00:00",
            "model_available_at": "2026-05-01T00:00:00+00:00",
        },
        claims.MODEL_CONFIG: {
            "calibrator_model_key": "model-1",
            "calibrator_model_hash": "model-hash-1",
            "calibration_input_space": "width_normalized_density",
        },
        claims.BELIEF: {
            "forecast_snapshot_id": "snap-1",
            "calibrator_model_key": "model-1",
            "calibrator_model_hash": "model-hash-1",
            "p_cal_vector_hash": "pcal-vector-hash",
            "p_live_vector_hash": "plive-vector-hash",
            "p_cal_hash": "pcal-vector-hash",
            "p_live_hash": "plive-vector-hash",
            "bin_labels_hash": "bins-hash-1",
            "members_json_hash": "members-hash-1",
            "unit": "F",
            "unit_authority_source": "ensemble_snapshots_v2.settlement_unit",
        },
        claims.EXECUTABLE_SNAPSHOT: {"selected_snapshot_id": "exec-snap-1", "condition_id": "condition-1", "token_id": "yes-1"},
        claims.QUOTE_FEASIBILITY: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "selected_token_id": "yes-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.COST_MODEL: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "cost_basis_id": "cost-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.PRE_TRADE_EVIDENCE: {"actionable_trade_score": 0.0},
        claims.CANDIDATE_EVIDENCE: {
            "family_id": "family-1",
            "condition_id": "condition-1",
            "selected_token_id": "yes-1",
            "hypothesis_id": "family-1:yes-1",
            "direction": "buy_yes",
        },
        claims.TESTING_PROTOCOL: {"family_id": "family-1"},
        claims.FDR: {"fdr_family_id": "family-1", "selected_hypotheses": ("family-1:yes-1",)},
        claims.KELLY_DRY_RUN: {"cost_basis_id": "cost-1"},
        claims.RISK_LEVEL: {"risk_level": "GREEN", "final_intent_id": "intent-1"},
        claims.NO_SUBMIT_MODE: {"side_effect_status": "NO_SUBMIT"},
    }
    parents = []
    for certificate_type, payload in parent_payloads.items():
        payload = {**payload, **parent_payload_overrides.get(certificate_type, {})}
        parents.append(_cert(certificate_type, f"{certificate_type}:event-1", payload))
    parent_tuple = tuple(parents)
    payload = {**_no_submit_payload(), **(no_submit_payload or {})}
    no_submit = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event-1:intent-1",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in parent_tuple),
        parent_certificates=parent_tuple,
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    return parent_tuple, no_submit


def _no_submit_payload() -> dict:
    payload = {
        "event_id": "event-1",
        "decision_source": "forecast",
        "final_intent_id": "intent-1",
        "side_effect_status": "NO_SUBMIT",
        "proof_accepted": True,
        "submitted": False,
        "executable_snapshot_id": "exec-snap-1",
        "actionable_trade_score": 0.0,
        "generated_at_decision_time": True,
        "header_persisted_at_semantics": "decision_kernel_generated_at_decision_time",
        "db_created_at_may_follow_header_persisted_at": True,
    }
    payload["projection_hash"] = stable_hash(
        {
            "event_id": payload["event_id"],
            "final_intent_id": payload["final_intent_id"],
            "side_effect_status": payload["side_effect_status"],
            "proof_accepted": payload["proof_accepted"],
            "submitted": payload["submitted"],
            "executable_snapshot_id": payload["executable_snapshot_id"],
        }
    )
    return payload


def _role(certificate_type: str) -> str:
    return certificate_type.removesuffix("Certificate").replace("Evidence", "").lower()


def _required_forecast_validations() -> tuple[str, ...]:
    return (
        "source_run_completeness_status",
        "coverage_completeness_status",
        "coverage_readiness_status",
        "required_steps_observed",
        "expected_members_observed",
        "causality_status_ok",
        "authority_verified",
        "available_at_not_future",
    )
