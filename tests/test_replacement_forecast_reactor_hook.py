# Created: 2026-06-06
# Last reused/audited: 2026-06-08
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Protect replacement forecast reactor hook placement before final order intent.
# Reuse: Run before wiring replacement shadow/veto logic into event_reactor_adapter.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
#   2026-06-08 STALE-TEST RE-AUTHOR (current law): updated 13 assertions/fixtures to the
#   post-544c5030fc single evidence gate (LIVE_AUTHORITY requires BOTH promotion +
#   capital-objective proofs) and the post-c41f13428c bin-topology + posterior-identity
#   gate (bundle reader requires q_ucb/bin_topology_hash/identity hashes + key-matched
#   q_lcb). Coverage preserved; assertions track the current contract.
"""Replacement forecast reactor hook tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import src.main as main_module
from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID, ReplacementForecastPosteriorBundle
from src.data.replacement_forecast_live_switch_surface import (
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_readiness import ReplacementForecastDependency, build_replacement_forecast_readiness
from src.data.replacement_forecast_refit_gate import REQUIRED_REFIT_EVIDENCE, ReplacementForecastRefitEvidence, evaluate_replacement_forecast_refit_gate
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastPromotionEvidence,
    ReplacementForecastCapitalObjectiveEvidence,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)
from src.engine.replacement_forecast_hook_factory import ReplacementForecastHookFactoryInput, build_replacement_forecast_event_hook
from src.engine.event_reactor_adapter import (
    _resolve_replacement_forecast_adapter_hook,
    event_bound_live_adapter_from_trade_conn,
    event_bound_no_submit_adapter_from_trade_conn,
)
from src.engine.replacement_forecast_reactor_hook import ReplacementForecastCandidateView, apply_replacement_forecast_reactor_hook
from src.events.opportunity_event import OpportunityEvent
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc


# ---------------------------------------------------------------------------
# Current-law shared topology fixtures (post c41f13428c H3/H4 bin-topology gate +
# 544c5030fc single evidence gate). The bundle reader (replacement_forecast_
# bundle_reader.py:442-542) now REQUIRES the stored posterior to carry a
# bin_topology_hash that matches BOTH its provenance_json["bin_topology_hash"]
# AND the current-market topology hash (supplied here via the proof, see
# _current_bin_topology_hash in the hook factory). When the proof-supplied hash
# equals the stored row hash, the market_events comparison is skipped — so these
# tests need NO market_events rows, only a coherent (proof, posterior, provenance)
# triple. The bin_topology also lets _candidate_bin_id resolve the posterior q key
# from canonical bin bounds (never labels).
# ---------------------------------------------------------------------------
_BIN_TOPOLOGY = [
    {"bin_id": "cool", "lower_c": None, "upper_c": 20.0},
    {"bin_id": "warm", "lower_c": 20.0, "upper_c": None},
]


def _json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


# The current-market bin-topology hash, derived the SAME way the hook factory's
# _current_bin_topology_hash reads it off the proof: a single string, matched
# verbatim against the stored posterior row's bin_topology_hash so the reader's
# market-events topology comparison is bypassed.
_BIN_TOPOLOGY_HASH = _json_hash(_BIN_TOPOLOGY)
_PROVENANCE_JSON = {"bin_topology": _BIN_TOPOLOGY, "bin_topology_hash": _BIN_TOPOLOGY_HASH}


def _warm_bin() -> SimpleNamespace:
    """Candidate bin that resolves to the 'warm' posterior key via canonical bounds
    (lower_c=20.0, upper_c=None), matching _BIN_TOPOLOGY's warm entry."""
    return SimpleNamespace(label="warm", low=20.0, high=None, unit="C")


def _cool_bin() -> SimpleNamespace:
    return SimpleNamespace(label="cool", low=None, high=20.0, unit="C")


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _flags(*, shadow: bool = False, veto: bool = False, trade: bool = False, kelly: bool = False, flip: bool = False) -> dict[str, bool]:
    return {
        SHADOW_FLAG: shadow,
        VETO_FLAG: veto,
        TRADE_AUTHORITY_FLAG: trade,
        KELLY_INCREASE_FLAG: kelly,
        DIRECTION_FLIP_FLAG: flip,
    }


def _bundle() -> ReplacementForecastPosteriorBundle:
    return ReplacementForecastPosteriorBundle(
        posterior_id=77,
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        q={"cool": 0.25, "warm": 0.75},
        q_lcb={"cool": 0.20, "warm": 0.65},
        q_ucb={"cool": 0.30, "warm": 0.85},
        bin_topology_hash=_BIN_TOPOLOGY_HASH,
        family_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T03:00:00+00:00",
        computed_at="2026-06-06T03:05:00+00:00",
        baseline_source_run_id="b0-run",
        dependency_json={"source_run_ids": ["b0-run", "aifs-run", "om9-run"]},
        provenance_json=_PROVENANCE_JSON,
        trade_authority_status="SHADOW_VETO_ONLY",
    )


# NOTE: the legacy _bundle_with_directional_no_lcb() helper was removed (2026-06-08).
# It injected a ``buy_no:warm`` key into q_lcb, which the current bundle reader
# __post_init__ rejects ("q_lcb keys must exactly match q keys"). Directional buy_no
# lcb is now carried on the candidate view, not in the bundle's q_lcb keys.


def _readiness():
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(2, 30),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(3),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(3, 5),
            posterior_id=77,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def _live_switch(policy, *, current: bool = True):
    return build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=tuple(REQUIRED_LIVE_READ_FILES),
            forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            trade_tables=tuple(REQUIRED_TRADE_TABLES),
            enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
            source_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE" if current else "STALE_FOR_LIVE",
        )
    )


def _refit(*, live_promotion: bool = False):
    return evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=5,
            official_rows=250,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="soft_anchor_product_specific_nested_refit",
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=20,
            emos_key_includes_product=True,
            emos_key_schema="replacement_product_keyed_v1",
            emos_identity_evidence_status="REPLACEMENT_EMOS_PRODUCT_IDENTITY_READY",
            data_refit_requested=True,
            live_promotion_requested=live_promotion,
        )
    )


def _promotion_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
        official_days=6,
        official_rows=300,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=300,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _capital_objective_evidence() -> ReplacementForecastCapitalObjectiveEvidence:
    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
        replay_status="EMPIRICAL_WINNER",
        after_cost_pnl=1.0,
        source_availability_observed=True,
        source_availability_violations=0,
        anti_lookahead_violations=0,
        same_clob_replay_passed=True,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        product_specific_refit_passed=True,
    )


def _refit_handoff_dict() -> dict[str, object]:
    return {
        "schema_version": "replacement_forecast_refit_handoff_v1",
        "generated_at": "2026-06-06T09:00:00+00:00",
        "status": "REFIT_HANDOFF_READY",
        "reason_codes": ["REPLACEMENT_REFIT_HANDOFF_READY"],
        "city": "Shanghai",
        "season": "JJA",
        "metric": "high",
        "source_family": "derived_posterior",
        "source_id": SOURCE_ID,
        "product_id": PRODUCT_ID,
        "data_version": HIGH_DATA_VERSION,
        "calibration_method": "soft_anchor_product_specific_nested_refit",
        "emos_cell_key": (
            "Shanghai|JJA|high|derived_posterior|"
            f"{SOURCE_ID}|{PRODUCT_ID}|{HIGH_DATA_VERSION}"
        ),
        "emos_key_schema": "replacement_product_keyed_v1",
        "selected_parameter": "w0.80_sigma3.00",
        "mean_holdout_brier": 0.20,
        "mean_holdout_log_loss": 0.50,
        "official_days": 5,
        "official_rows": 250,
        "min_guardrail_bucket_rows": 20,
        "training_scope": "replacement_product_specific_only",
        "baseline_calibration_reused": False,
        "live_promotion_allowed": False,
        "ready_for_product_refit": True,
        "refit_decision": _refit().as_dict(),
    }


def _promotion_evidence_payload_dict() -> dict[str, object]:
    payload = {
        "promotion_evidence": _promotion_evidence().__dict__,
        "refit_evidence": {
            "official_days": 5,
            "official_rows": 250,
            "temperature_metric": "high",
            "source_family": "derived_posterior",
            "product_id": PRODUCT_ID,
            "calibration_method": "soft_anchor_product_specific_nested_refit",
            "enabled_evidence": list(REQUIRED_REFIT_EVIDENCE),
            "min_guardrail_bucket_rows": 20,
            "high_low_mixed": False,
            "baseline_calibration_reused": False,
            "emos_key_includes_product": True,
            "emos_key_schema": "replacement_product_keyed_v1",
            "emos_identity_evidence_status": "REPLACEMENT_EMOS_PRODUCT_IDENTITY_READY",
            "data_refit_requested": True,
            "live_promotion_requested": True,
        },
        "before_after_rows": [],
        "min_before_after_official_days": 1,
        "min_before_after_official_rows": 1,
        "capital_replay": {
            "selected_label": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
            "status": "EMPIRICAL_WINNER",
            "coverage": {
                "source_availability_observed": True,
                "source_availability_violations": 0,
            },
        },
    }
    for day in range(5):
        for _ in range(50):
            payload["before_after_rows"].append(
                {
                    "official_date": f"2026-06-0{day + 1}",
                    "city": "Shanghai",
                    "temperature_metric": "high",
                    "guardrail_bucket": "standard",
                    "baseline_brier": 0.30,
                    "replacement_brier": 0.20,
                    "baseline_log_loss": 0.70,
                    "replacement_log_loss": 0.50,
                    "baseline_after_cost_pnl": 0.0,
                    "replacement_after_cost_pnl": 1.0,
                    "truth_authority": "VERIFIED",
                    "replay_status": "SCORED",
                }
            )
    return payload


def _switch_decision(policy, *, readiness=None, current: bool = True, live_promotion: bool = False):
    # BLOCKER 8 fail-closed boundary: a LIVE_AUTHORITY policy must thread its capital-objective
    # evidence OBJECT to the switch input -- the consuming gate now requires it explicitly
    # (REPLACEMENT_SWITCH_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED on None) rather than trusting the
    # resolver. Non-live policies leave it None (the boundary never reads it).
    capital_objective_evidence = (
        _capital_objective_evidence() if policy.status == "LIVE_AUTHORITY" else None
    )
    return evaluate_replacement_forecast_switch_decision(
        ReplacementForecastSwitchDecisionInput(
            runtime_policy=policy,
            live_switch_report=_live_switch(policy, current=current),
            readiness=_readiness() if readiness is None else readiness,
            refit_decision=_refit(live_promotion=live_promotion),
            capital_objective_evidence=capital_objective_evidence,
        )
    )


def _candidate(**overrides) -> ReplacementForecastCandidateView:
    params = {
        "baseline_direction": "buy_yes:warm",
        "baseline_q_posterior": 0.70,
        "baseline_q_lcb": 0.62,
        "baseline_kelly_fraction": 0.04,
        "candidate_direction": "buy_yes:warm",
        "candidate_q_posterior": 0.75,
        "candidate_q_lcb": 0.55,
        "candidate_kelly_fraction": 0.02,
        "market_snapshot_id": "snap-1",
        "condition_id": "cond-1",
        "token_id": "token-yes",
        "decision_time": "2026-06-06T04:00:00+00:00",
    }
    params.update(overrides)
    return ReplacementForecastCandidateView(**params)


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def _event() -> OpportunityEvent:
    return OpportunityEvent(
        event_id="event-1",
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Shanghai:2026-06-07:high",
        source="test",
        observed_at=_dt(4).isoformat(),
        available_at=_dt(4).isoformat(),
        received_at=_dt(4).isoformat(),
        causal_snapshot_id="snapshot-1",
        payload_hash="payload-hash",
        idempotency_key="idem-1",
        priority=0,
        expires_at=None,
        payload_json=json.dumps({"city": "Shanghai", "target_date": "2026-06-07", "metric": "high"}),
        schema_version=1,
        created_at=_dt(4).isoformat(),
    )


def _proof(*, direction: str = "buy_yes:warm", q_lcb: float = 0.62, bin=None):
    # The proof carries bin_topology_hash so the hook factory's
    # _current_bin_topology_hash(proof, event) returns it; it must equal the stored
    # posterior's bin_topology_hash so the reader's market-events comparison is
    # skipped (current-law bin-topology gate, c41f13428c). The candidate bin carries
    # canonical bounds so _candidate_bin_id resolves the posterior q key.
    return SimpleNamespace(
        candidate=SimpleNamespace(
            city="Shanghai",
            target_date="2026-06-07",
            metric="high",
            condition_id="cond-1",
            bin=bin if bin is not None else _warm_bin(),
        ),
        token_id="token-yes",
        direction=direction,
        executable_snapshot_id="snap-1",
        q_posterior=0.70,
        q_lcb_5pct=q_lcb,
        bin_topology_hash=_BIN_TOPOLOGY_HASH,
    )


def test_hook_factory_caps_replacement_q_lcb_only_before_live_authority() -> None:
    from src.engine.replacement_forecast_hook_factory import _candidate_view_from_proof

    shadow_view = _candidate_view_from_proof(
        _proof(q_lcb=0.62),
        _dt(4),
        replacement_bundle=_bundle(),
        cap_replacement_q_lcb_to_baseline=True,
    )
    live_view = _candidate_view_from_proof(
        _proof(q_lcb=0.62),
        _dt(4),
        replacement_bundle=_bundle(),
        cap_replacement_q_lcb_to_baseline=False,
    )

    assert shadow_view.candidate_q_lcb == pytest.approx(0.62)
    assert live_view.candidate_q_posterior == pytest.approx(0.75)
    assert live_view.candidate_q_lcb == pytest.approx(0.65)


def _create_minimal_readiness_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL,
            city TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            source_id TEXT,
            strategy_key TEXT,
            status TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def _ensure_required_switch_tables(conn: sqlite3.Connection, tables: tuple[str, ...]) -> None:
    for table in tables:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)")


def _forecast_conn_with_replacement_rows(*, dependency_source_run_ids: dict[str, str] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_minimal_readiness_state(conn)
    _ensure_required_switch_tables(conn, tuple(REQUIRED_FORECAST_TABLES))
    # Current-law posterior identity columns (c41f13428c H4 settlement-identity):
    # the reader requires q_ucb_json, bin_topology_hash, posterior_identity_hash,
    # dependency_hash, posterior_config_hash to be present and non-empty, and the
    # row's bin_topology_hash to match provenance_json["bin_topology_hash"]. The
    # provenance carries the bin_topology so the q-key resolution + topology hash
    # are coherent with the proof-supplied current hash.
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            bin_topology_hash, posterior_identity_hash, dependency_hash,
            posterior_config_hash, trade_authority_status, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            "2026-06-06T00:00:00+00:00",
            _dt(3).isoformat(),
            _dt(3, 5).isoformat(),
            json.dumps({"cool": 0.25, "warm": 0.75}),
            json.dumps({"cool": 0.20, "warm": 0.55}),
            json.dumps({"cool": 0.30, "warm": 0.85}),
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps(
                dependency_source_run_ids
                or {
                    "baseline_b0": "b0-run",
                    "aifs_sampled_2t": "aifs-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                }
            ),
            json.dumps(_PROVENANCE_JSON),
            _BIN_TOPOLOGY_HASH,
            "posterior-identity-hash",
            "dependency-hash",
            "posterior-config-hash",
            "SHADOW_VETO_ONLY",
            0,
        ),
    )
    posterior_id = int(conn.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()["posterior_id"])
    readiness = _readiness()
    dependency_payload = dict(readiness.dependency_json)
    dependencies = [dict(item) for item in dependency_payload["dependencies"]]
    for dependency in dependencies:
        if dependency["role"] == "soft_anchor_posterior":
            dependency["posterior_id"] = posterior_id
    dependency_payload["dependencies"] = dependencies
    conn.execute(
        """
        INSERT INTO readiness_state (
            readiness_id, scope_key, scope_type, city, target_local_date,
            temperature_metric, data_version, source_id, strategy_key, status,
            reason_codes_json, computed_at, expires_at, dependency_json, provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            readiness.readiness_id,
            "replacement:Shanghai:2026-06-07:high",
            "strategy",
            "Shanghai",
            "2026-06-07",
            "high",
            HIGH_DATA_VERSION,
            SOURCE_ID,
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            "SHADOW_ONLY",
            json.dumps(list(readiness.reason_codes)),
            _dt(4).isoformat(),
            _dt(6).isoformat(),
            json.dumps(dependency_payload),
            json.dumps(readiness.provenance_json),
        ),
    )
    return conn


def _trade_conn_with_required_tables() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_required_switch_tables(conn, tuple(REQUIRED_TRADE_TABLES))
    return conn


def test_reactor_hook_disabled_is_noop_without_replacement_dependencies() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags())
    candidate = _candidate()

    result = apply_replacement_forecast_reactor_hook(policy=policy, candidate=candidate)

    assert result.status == "DISABLED"
    assert result.effective_values() == candidate.baseline_values()
    assert result.veto_decision is None
    assert result.as_receipt_tag() is None
    assert result.changed_baseline is False


def test_reactor_hook_shadow_only_observes_without_mutating_candidate() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True))
    candidate = _candidate(candidate_q_lcb=0.10, candidate_kelly_fraction=0.00)

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy),
        candidate=candidate,
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "SHADOW_ONLY"
    assert result.effective_values() == candidate.baseline_values()
    assert result.veto_decision is None
    assert result.as_receipt_tag() is None


def test_reactor_hook_veto_only_can_reduce_confidence_before_intent() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy),
        candidate=_candidate(),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "SHADOW_VETO_ONLY"
    assert result.effective_direction == "buy_yes:warm"
    assert result.effective_q_lcb == pytest.approx(0.55)
    assert result.effective_kelly_fraction == pytest.approx(0.02)
    assert result.changed_baseline is True
    assert result.veto_decision is not None
    assert result.veto_decision.veto is True
    receipt_tag = result.as_receipt_tag()
    assert receipt_tag is not None
    assert receipt_tag["receipt_role"] == "forecast_attribution_only"
    assert receipt_tag["settlement_authority_status"] == "NO_SETTLEMENT_AUTHORITY"
    assert receipt_tag["training_allowed"] is False
    assert receipt_tag["promotion_allowed"] is False


def test_db_backed_replacement_hook_reads_posterior_and_reduces_q_lcb_before_intent() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_VETO_ONLY"
    assert result.effective_direction == "buy_yes:warm"
    assert result.effective_q_lcb == pytest.approx(0.55)
    assert result.effective_kelly_fraction == pytest.approx(0.0)
    assert result.as_receipt_tag() is not None
    rows = forecast_conn.execute("SELECT * FROM replacement_shadow_decisions").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["posterior_id"] == result.veto_decision.posterior_id
    assert row["baseline_source_run_id"] == "b0-run"
    assert row["market_snapshot_id"] == "snap-1"
    assert row["condition_id"] == "cond-1"
    assert row["token_id"] == "token-yes"
    assert row["baseline_direction"] == "buy_yes:warm"
    assert row["allowed_direction"] == "buy_yes:warm"
    assert row["allowed_q_lcb"] == pytest.approx(0.55)
    assert row["trade_authority_status"] == "SHADOW_VETO_ONLY"
    assert json.loads(row["dependency_source_run_ids_json"]) == {
        "baseline_b0": "b0-run",
        "aifs_sampled_2t": "aifs-run",
        "openmeteo_ifs9_anchor": "om9-run",
    }


def test_db_backed_replacement_hook_shadow_decision_write_is_idempotent() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    first = hook(_proof(q_lcb=0.62), _event(), _dt(4))
    second = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert first.status == "SHADOW_VETO_ONLY"
    assert second.status == "SHADOW_VETO_ONLY"
    assert forecast_conn.execute("SELECT COUNT(*) FROM replacement_shadow_decisions").fetchone()[0] == 1


def test_db_backed_replacement_hook_degrades_to_baseline_if_shadow_decision_write_fails() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    forecast_conn.execute("DROP TABLE replacement_shadow_decisions")
    forecast_conn.execute("CREATE TABLE replacement_shadow_decisions (malformed TEXT)")
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_ONLY"
    assert result.reason_codes == ("REPLACEMENT_SHADOW_DECISION_WRITE_FAILED",)
    assert result.effective_q_lcb == pytest.approx(0.62)


def test_db_backed_replacement_hook_allows_veto_without_product_specific_refit_decision() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_VETO_ONLY"
    assert result.reason_codes == ("SOFT_ANCHOR_LOWER_Q_LCB",)
    assert result.effective_q_lcb <= 0.62


def test_db_backed_replacement_hook_buy_no_without_directional_lcb_keeps_baseline() -> None:
    """Current law (post c41f13428c / 544c5030fc): a replacement posterior bundle's
    q_lcb keys must EXACTLY match its q keys (bundle reader __post_init__), so the
    legacy directional ``buy_no:{bin}`` q_lcb entries can no longer exist in a valid
    bundle. The factory's buy_no branch therefore finds no directional lcb and the
    candidate q_lcb defaults to the baseline; the shadow-veto only ever LOWERS q_lcb
    (apply_shadow_veto_guardrail: allowed = min(baseline, candidate)), so with no
    reduction the baseline q_lcb is preserved and no SOFT_ANCHOR_LOWER_Q_LCB fires.
    The candidate bin is the NON-argmax 'cool' bin so the posterior-derived lawful
    side is buy_no (selected argmax = 'warm'); the hook keeps that lawful direction.
    """
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(direction="buy_no:cool", q_lcb=0.62, bin=_cool_bin()), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_VETO_ONLY"
    assert result.effective_direction == "buy_no:cool"
    # No directional buy_no q_lcb in a valid bundle -> candidate q_lcb defaults to the
    # baseline -> veto cannot lower it -> baseline preserved, no LOWER_Q_LCB reason.
    assert result.effective_q_lcb == pytest.approx(0.62)
    assert result.veto_decision is not None
    assert "SOFT_ANCHOR_LOWER_Q_LCB" not in result.veto_decision.reasons


def test_db_backed_replacement_hook_blocks_dependency_source_run_drift() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows(
        dependency_source_run_ids={
            "baseline_b0": "b0-run",
            "aifs_sampled_2t": "wrong-aifs-run",
            "openmeteo_ifs9_anchor": "om9-run",
        }
    )
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_ONLY"
    assert result.reason_codes == ("REPLACEMENT_DEPENDENCY_SOURCE_RUN_MISMATCH",)


def test_reactor_hook_uses_explicit_candidate_buy_no_q_lcb() -> None:
    """Current law: the pure reactor hook's veto reads the candidate q_lcb straight
    off the candidate view (apply_replacement_forecast_shadow_veto -> guardrail
    allowed = min(baseline, candidate)). A buy_no candidate with an EXPLICIT lower
    candidate q_lcb (0.18 < baseline 0.62) is honored verbatim. (The legacy path
    that read a directional ``buy_no:warm`` key off the bundle's q_lcb is gone: such
    a key is now unconstructable because q_lcb keys must exactly match q keys.)"""
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy),
        candidate=_candidate(
            baseline_direction="buy_no:warm",
            candidate_direction="buy_no:warm",
            candidate_q_lcb=0.18,
        ),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "SHADOW_VETO_ONLY"
    assert result.effective_direction == "buy_no:warm"
    assert result.effective_q_lcb == pytest.approx(0.18)


def test_db_backed_replacement_hook_degrades_to_baseline_when_shadow_inventory_is_not_explicit() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            refit_decision=_refit(),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_ONLY"
    assert "REPLACEMENT_SWITCH_MISSING_READ_TABLES" in result.reason_codes


def test_db_backed_replacement_hook_degrades_to_baseline_without_shadow_baseline_bundle() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True),
            baseline_bundle_provider=None,
            refit_decision=_refit(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "SHADOW_ONLY"
    assert result.reason_codes == ("REPLACEMENT_HOOK_BASELINE_BUNDLE_MISSING",)


def test_event_adapter_builds_replacement_hook_from_runtime_flags_without_manual_hook() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()

    hook = _resolve_replacement_forecast_adapter_hook(
        replacement_forecast_hook=None,
        replacement_forecast_runtime_flags=_flags(shadow=True, veto=True),
        replacement_forecast_baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
        replacement_forecast_world_tables=tuple(REQUIRED_WORLD_TABLES),
        replacement_forecast_source_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_data_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_refit_decision=_refit(),
        replacement_forecast_promotion_evidence=_promotion_evidence(),
        replacement_forecast_capital_objective_evidence=_capital_objective_evidence(),
        forecast_conn=forecast_conn,
        trade_conn=trade_conn,
    )

    assert hook is not None
    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result.status == "SHADOW_VETO_ONLY"
    assert result.effective_q_lcb == pytest.approx(0.55)


def test_db_backed_replacement_hook_reaches_live_authority_only_with_both_evidence() -> None:
    # Current law (544c5030fc single evidence gate): LIVE_AUTHORITY requires the flag
    # ladder AND BOTH passing proofs (promotion + capital-objective). Capital-objective
    # ALONE is no longer sufficient. Supplying both lets the DB-backed hook reach
    # LIVE_AUTHORITY; the q_posterior/q_lcb come from the stored posterior (uncapped).
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True, trade=True),
            baseline_bundle_provider=lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run")),
            # Switch admission also requires the refit handoff to authorize live
            # promotion (REPLACEMENT_SWITCH_REFIT_LIVE_PROMOTION_REQUIRED).
            refit_decision=_refit(live_promotion=True),
            promotion_evidence=_promotion_evidence(),
            capital_objective_evidence=_capital_objective_evidence(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "LIVE_AUTHORITY"
    assert result.reason_codes == ("REPLACEMENT_LIVE_AUTHORITY_APPLIED",)
    assert result.effective_direction == "buy_yes:warm"
    assert result.effective_q_posterior == pytest.approx(0.75)
    assert result.effective_q_lcb == pytest.approx(0.55)
    assert result.effective_kelly_fraction == pytest.approx(0.0)


def test_db_backed_live_authority_uses_readiness_baseline_when_baseline_bundle_lookup_misses() -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    hook = build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=_flags(shadow=True, veto=True, trade=True),
            baseline_bundle_provider=None,
            # Current law: LIVE_AUTHORITY needs BOTH proofs (544c5030fc evidence gate)
            # AND a refit handoff that authorizes live promotion.
            refit_decision=_refit(live_promotion=True),
            promotion_evidence=_promotion_evidence(),
            capital_objective_evidence=_capital_objective_evidence(),
            world_tables=tuple(REQUIRED_WORLD_TABLES),
            source_fact_status="CURRENT_FOR_LIVE",
            data_fact_status="CURRENT_FOR_LIVE",
        )
    )

    result = hook(_proof(q_lcb=0.62), _event(), _dt(4))

    assert result is not None
    assert result.status == "LIVE_AUTHORITY"
    assert result.reason_codes == ("REPLACEMENT_LIVE_AUTHORITY_APPLIED",)
    assert result.effective_q_posterior == pytest.approx(0.75)
    assert result.effective_q_lcb == pytest.approx(0.55)
    assert result.receipt_provenance is not None
    assert result.receipt_provenance.payload["dependency_source_run_ids"]["baseline_b0"] == "b0-run"


def test_no_submit_adapter_wires_replacement_switch_inputs_into_real_hook(monkeypatch) -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    baseline_provider = lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run"))  # noqa: E731
    captured: dict[str, object] = {}

    def _spy_resolver(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("src.engine.event_reactor_adapter._resolve_replacement_forecast_adapter_hook", _spy_resolver)

    submit = event_bound_no_submit_adapter_from_trade_conn(
        trade_conn,
        forecast_conn=forecast_conn,
        get_current_level=lambda: None,
        replacement_forecast_runtime_flags=_flags(shadow=True, veto=True),
        replacement_forecast_baseline_bundle_provider=baseline_provider,
        replacement_forecast_world_tables=tuple(REQUIRED_WORLD_TABLES),
        replacement_forecast_source_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_data_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_refit_decision=_refit(),
        replacement_forecast_promotion_evidence=_promotion_evidence(),
        replacement_forecast_capital_objective_evidence=_capital_objective_evidence(),
    )

    assert callable(submit)
    assert captured["forecast_conn"] is forecast_conn
    assert captured["trade_conn"] is trade_conn
    assert captured["replacement_forecast_runtime_flags"] == _flags(shadow=True, veto=True)
    assert captured["replacement_forecast_baseline_bundle_provider"] is baseline_provider
    assert captured["replacement_forecast_world_tables"] == tuple(REQUIRED_WORLD_TABLES)
    assert captured["replacement_forecast_source_fact_status"] == "CURRENT_FOR_LIVE"
    assert captured["replacement_forecast_data_fact_status"] == "CURRENT_FOR_LIVE"
    assert captured["replacement_forecast_refit_decision"] == _refit()
    assert captured["replacement_forecast_promotion_evidence"] == _promotion_evidence()
    assert captured["replacement_forecast_capital_objective_evidence"] == _capital_objective_evidence()


def test_live_adapter_wires_replacement_switch_inputs_into_real_hook(monkeypatch) -> None:
    forecast_conn = _forecast_conn_with_replacement_rows()
    trade_conn = _trade_conn_with_required_tables()
    baseline_provider = lambda proof, event, decision_time: _BaselineBundle(_Evidence("b0-run"))  # noqa: E731
    captured: dict[str, object] = {}

    def _spy_resolver(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("src.engine.event_reactor_adapter._resolve_replacement_forecast_adapter_hook", _spy_resolver)

    submit = event_bound_live_adapter_from_trade_conn(
        trade_conn,
        forecast_conn=forecast_conn,
        get_current_level=lambda: None,
        replacement_forecast_runtime_flags=_flags(shadow=True, veto=True),
        replacement_forecast_baseline_bundle_provider=baseline_provider,
        replacement_forecast_world_tables=tuple(REQUIRED_WORLD_TABLES),
        replacement_forecast_source_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_data_fact_status="CURRENT_FOR_LIVE",
        replacement_forecast_refit_decision=_refit(),
        replacement_forecast_promotion_evidence=_promotion_evidence(),
        replacement_forecast_capital_objective_evidence=_capital_objective_evidence(),
        real_order_submit_enabled=False,
    )

    assert callable(submit)
    assert captured["forecast_conn"] is forecast_conn
    assert captured["trade_conn"] is trade_conn
    assert captured["replacement_forecast_runtime_flags"] == _flags(shadow=True, veto=True)
    assert captured["replacement_forecast_baseline_bundle_provider"] is baseline_provider
    assert captured["replacement_forecast_world_tables"] == tuple(REQUIRED_WORLD_TABLES)
    assert captured["replacement_forecast_source_fact_status"] == "CURRENT_FOR_LIVE"
    assert captured["replacement_forecast_data_fact_status"] == "CURRENT_FOR_LIVE"
    assert captured["replacement_forecast_refit_decision"] == _refit()
    assert captured["replacement_forecast_promotion_evidence"] == _promotion_evidence()
    assert captured["replacement_forecast_capital_objective_evidence"] == _capital_objective_evidence()


def test_replacement_runtime_flags_are_read_from_feature_flags(monkeypatch) -> None:
    flags = dict(main_module.settings["feature_flags"])
    flags.update(
        {
            SHADOW_FLAG: True,
            VETO_FLAG: True,
            TRADE_AUTHORITY_FLAG: False,
            KELLY_INCREASE_FLAG: False,
            DIRECTION_FLIP_FLAG: False,
        }
    )
    monkeypatch.setitem(main_module.settings._data, "feature_flags", flags)

    resolved = main_module._replacement_forecast_runtime_flags_from_settings()

    assert resolved == _flags(shadow=True, veto=True)


def test_replacement_refit_decision_is_read_from_settings_handoff(monkeypatch, tmp_path) -> None:
    handoff_path = tmp_path / "refit_handoff.json"
    handoff_path.write_text(json.dumps(_refit_handoff_dict()), encoding="utf-8")
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {"refit_handoff_path": str(handoff_path)},
    )

    decision = main_module._replacement_forecast_refit_decision_from_settings()

    assert decision == _refit()


def test_replacement_refit_decision_missing_handoff_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {"refit_handoff_path": str(tmp_path / "missing.json")},
    )

    assert main_module._replacement_forecast_refit_decision_from_settings() is None


def test_replacement_promotion_evidence_is_read_from_settings_payload(monkeypatch, tmp_path) -> None:
    evidence_path = tmp_path / "promotion_evidence.json"
    evidence_path.write_text(json.dumps(_promotion_evidence_payload_dict()), encoding="utf-8")
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {"promotion_evidence_path": str(evidence_path)},
    )

    evidence = main_module._replacement_forecast_promotion_evidence_from_settings()
    # FIX-1 AND (ITEM B): LIVE_AUTHORITY now requires BOTH proofs. This test's load-
    # bearing subject is that the promotion evidence is READ from settings (the
    # `evidence == _promotion_evidence()` assertion); resolve with both evidence
    # objects so the incidental LIVE_AUTHORITY assertion reflects the conjunction law.
    capital_evidence = main_module._replacement_forecast_capital_objective_evidence_from_settings()
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True),
        promotion_evidence=evidence,
        capital_objective_evidence=capital_evidence,
    )

    assert evidence == _promotion_evidence()
    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_replacement_promotion_evidence_missing_payload_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {"promotion_evidence_path": str(tmp_path / "missing.json")},
    )

    assert main_module._replacement_forecast_promotion_evidence_from_settings() is None


def test_replacement_capital_objective_evidence_is_read_from_settings_payload(monkeypatch, tmp_path) -> None:
    evidence_path = tmp_path / "promotion_evidence.json"
    evidence_path.write_text(json.dumps(_promotion_evidence_payload_dict()), encoding="utf-8")
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {"promotion_evidence_path": str(evidence_path)},
    )

    evidence = main_module._replacement_forecast_capital_objective_evidence_from_settings()
    # FIX-1 AND (ITEM B): this test's load-bearing subject is that the capital-
    # objective evidence is READ from settings (the `evidence == _capital_objective_
    # evidence()` assertion); resolve with both evidence objects so the incidental
    # LIVE_AUTHORITY assertion reflects the conjunction law (single proof => BLOCKED).
    promotion_evidence = main_module._replacement_forecast_promotion_evidence_from_settings()
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True),
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=evidence,
    )

    assert evidence == _capital_objective_evidence()
    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_reactor_hook_veto_only_never_flips_direction_or_raises_values() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy),
        candidate=_candidate(candidate_direction="buy_yes:cool", candidate_q_lcb=0.95, candidate_kelly_fraction=0.20),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.effective_direction == "buy_yes:warm"
    assert result.effective_q_lcb == pytest.approx(0.62)
    assert result.effective_kelly_fraction == pytest.approx(0.04)
    assert result.veto_decision is not None
    assert result.veto_decision.reasons == ("SOFT_ANCHOR_DIRECTION_DISAGREEMENT",)


def test_reactor_hook_veto_only_fails_closed_when_switch_decision_missing() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    candidate = _candidate()

    result = apply_replacement_forecast_reactor_hook(policy=policy, candidate=candidate)

    assert result.status == "BLOCKED"
    assert result.reason_codes == ("REPLACEMENT_REACTOR_SWITCH_DECISION_MISSING",)
    assert result.effective_values() == candidate.baseline_values()
    assert result.as_receipt_tag() is None


def test_reactor_hook_veto_only_fails_closed_when_dependencies_missing_after_switch_admission() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    candidate = _candidate()

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy),
        candidate=candidate,
    )

    assert result.status == "BLOCKED"
    assert result.reason_codes == ("REPLACEMENT_REACTOR_HOOK_DEPENDENCY_MISSING",)
    assert result.effective_values() == candidate.baseline_values()
    assert result.as_receipt_tag() is None


def test_reactor_hook_blocks_stale_switch_decision_before_veto_logic() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(shadow=True, veto=True))
    candidate = _candidate()

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, current=False),
        candidate=candidate,
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in result.reason_codes
    assert result.effective_values() == candidate.baseline_values()
    assert result.as_receipt_tag() is None


def test_reactor_hook_live_authority_can_apply_same_direction_replacement_q_lcb() -> None:
    # Current law (544c5030fc): LIVE_AUTHORITY needs BOTH promotion + capital-objective
    # evidence; a single proof resolves to BLOCKED.
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True),
        promotion_evidence=_promotion_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    candidate = _candidate(candidate_q_lcb=0.95, candidate_kelly_fraction=0.04)

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, live_promotion=True),
        candidate=candidate,
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "LIVE_AUTHORITY"
    assert result.reason_codes == ("REPLACEMENT_LIVE_AUTHORITY_APPLIED",)
    assert result.effective_direction == "buy_yes:warm"
    assert result.effective_q_posterior == pytest.approx(0.75)
    assert result.effective_q_lcb == pytest.approx(0.95)
    assert result.effective_kelly_fraction == pytest.approx(0.04)
    receipt_tag = result.as_receipt_tag()
    assert receipt_tag is not None
    assert receipt_tag["trade_authority_status"] == "LIVE_AUTHORITY"
    assert receipt_tag["authority_limits"]["can_initiate_trade"] is True
    assert receipt_tag["settlement_authority_status"] == "NO_SETTLEMENT_AUTHORITY"


def test_reactor_hook_live_authority_blocks_unauthorized_kelly_increase_and_direction_flip() -> None:
    # Current law (544c5030fc): LIVE_AUTHORITY needs BOTH proofs. With LIVE_AUTHORITY
    # reached, the per-action authority gates (kelly-increase / direction-flip) still
    # require their own flags, which are OFF here -> each action is BLOCKED.
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True),
        promotion_evidence=_promotion_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    kelly = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, live_promotion=True),
        candidate=_candidate(candidate_kelly_fraction=0.20),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )
    flip = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, live_promotion=True),
        candidate=_candidate(candidate_direction="buy_yes:cool"),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert kelly.status == "BLOCKED"
    assert "REPLACEMENT_REACTOR_KELLY_INCREASE_NOT_AUTHORIZED" in kelly.reason_codes
    assert flip.status == "BLOCKED"
    assert "REPLACEMENT_REACTOR_DIRECTION_FLIP_NOT_AUTHORIZED" in flip.reason_codes


def test_reactor_hook_live_authority_refuses_direction_law_violating_candidate() -> None:
    """FIX-3 (§0.5): under LIVE_AUTHORITY the flip site re-derives the lawful
    direction from the replacement posterior (selected bin vs argmax(q)) and
    refuses any candidate whose claimed side disagrees, with the typed
    REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION receipt. The bug this guards
    against: trusting the upstream candidate_direction string verbatim at the
    consuming boundary (Fitz #2 cross-module semantic drop).

    Bundle q = {cool: 0.25, warm: 0.75} -> argmax = warm. A candidate on the
    'cool' bin is lawfully buy_no:cool; claiming buy_yes:cool is unlawful.
    Both baseline and candidate are buy_yes:cool so the flip-vs-baseline gate is
    NOT what blocks it -- the posterior-derived law is.
    """

    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True, kelly=True, flip=True),
        promotion_evidence=_promotion_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, live_promotion=True),
        candidate=_candidate(
            baseline_direction="buy_yes:cool",
            candidate_direction="buy_yes:cool",
        ),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "BLOCKED"
    assert "REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION" in result.reason_codes
    # Fail-safe: a law-violating candidate never mutates the baseline.
    assert result.effective_direction == "buy_yes:cool"


def test_reactor_hook_live_authority_admits_direction_law_consistent_candidate() -> None:
    """FIX-3 positive case: a candidate whose claimed side agrees with the
    posterior-derived lawful direction (buy_no on a non-argmax bin) is admitted.
    """

    policy = resolve_replacement_forecast_runtime_policy(
        _flags(shadow=True, veto=True, trade=True, kelly=True, flip=True),
        promotion_evidence=_promotion_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    result = apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=_switch_decision(policy, live_promotion=True),
        candidate=_candidate(
            baseline_direction="buy_no:cool",
            candidate_direction="buy_no:cool",
        ),
        replacement_bundle=_bundle(),
        readiness=_readiness(),
    )

    assert result.status == "LIVE_AUTHORITY"
    assert "REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION" not in result.reason_codes
    assert result.effective_direction == "buy_no:cool"
