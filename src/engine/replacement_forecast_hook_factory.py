"""DB-backed hook factory for replacement forecast pre-intent admission."""

from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
from src.data.replacement_forecast_live_switch_surface import (
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastReadinessDecision,
)
from src.data.replacement_forecast_refit_gate import ReplacementForecastRefitDecision
from src.data.replacement_forecast_runtime_policy import (
    REQUIRED_FLAGS,
    ReplacementForecastCapitalObjectiveEvidence,
    ReplacementForecastPromotionEvidence,
    ReplacementForecastRuntimePolicy,
    resolve_replacement_forecast_runtime_policy,
)
from src.data.replacement_forecast_switch_decision import (
    ReplacementForecastSwitchDecisionInput,
    evaluate_replacement_forecast_switch_decision,
)
from src.engine.replacement_forecast_reactor_hook import (
    ReplacementForecastCandidateView,
    ReplacementForecastReactorHookResult,
    apply_replacement_forecast_reactor_hook,
)
from src.events.opportunity_event import OpportunityEvent


ReplacementBaselineBundleProvider = Callable[[Any, OpportunityEvent, datetime], object | None]


@dataclass(frozen=True)
class ReplacementForecastHookFactoryInput:
    forecast_conn: sqlite3.Connection
    trade_conn: sqlite3.Connection
    runtime_flags: Mapping[str, object]
    baseline_bundle_provider: ReplacementBaselineBundleProvider | None
    refit_decision: ReplacementForecastRefitDecision | None = None
    promotion_evidence: ReplacementForecastPromotionEvidence | None = None
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None
    available_files: tuple[str, ...] = REQUIRED_LIVE_READ_FILES
    world_tables: tuple[str, ...] = ()
    trade_tables: tuple[str, ...] | None = None
    source_fact_status: str = "STALE_FOR_LIVE"
    data_fact_status: str = "STALE_FOR_LIVE"
    enabled_evidence_gates: tuple[str, ...] = REQUIRED_EVIDENCE_GATES


def _json_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return value
    import json

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be JSON text")
    parsed = json.loads(value)
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{field_name} must decode to an object")
    return parsed


def _json_reasons(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    import json

    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError("reason_codes_json must decode to a list")
    return tuple(str(item) for item in parsed)


def _latest_replacement_readiness(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> ReplacementForecastReadinessDecision | None:
    row = conn.execute(
        """
        SELECT *
        FROM readiness_state
        WHERE strategy_key = ?
          AND source_id = ?
          AND data_version IN (?, ?)
          AND city = ?
          AND target_local_date = ?
          AND temperature_metric = ?
        ORDER BY computed_at DESC
        LIMIT 1
        """,
        (
            STRATEGY_KEY,
            SOURCE_ID,
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1",
            city,
            target_date,
            temperature_metric,
        ),
    ).fetchone()
    if row is None:
        return None
    row_map = dict(row)
    status = str(row_map.get("status") or "BLOCKED")
    return ReplacementForecastReadinessDecision(
        readiness_id=str(row_map["readiness_id"]),
        status=status if status in {READY_STATUS, "BLOCKED"} else "BLOCKED",
        reason_codes=_json_reasons(row_map.get("reason_codes_json")) or ("REPLACEMENT_READINESS_STATE_LOADED",),
        dependency_json=_json_mapping(row_map.get("dependency_json"), field_name="dependency_json"),
        provenance_json=_json_mapping(row_map.get("provenance_json"), field_name="provenance_json"),
        expires_at=row_map.get("expires_at") if status == READY_STATUS else None,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        strategy_key=STRATEGY_KEY,
    )


def _available_tables(conn: sqlite3.Connection, required: tuple[str, ...] | None = None) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    available = {str(row[0] if not isinstance(row, sqlite3.Row) else row["name"]) for row in rows}
    if required is None:
        return tuple(sorted(available))
    return tuple(table for table in required if table in available)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _blocked_result(
    candidate: ReplacementForecastCandidateView,
    *,
    reason_code: str,
) -> ReplacementForecastReactorHookResult:
    return ReplacementForecastReactorHookResult(
        status="BLOCKED",
        reason_codes=(reason_code,),
        effective_direction=candidate.baseline_direction,
        effective_q_posterior=candidate.baseline_q_posterior,
        effective_q_lcb=candidate.baseline_q_lcb,
        effective_kelly_fraction=candidate.baseline_kelly_fraction,
    )


def _shadow_unavailable_result(
    candidate: ReplacementForecastCandidateView,
    *,
    reason_codes: tuple[str, ...],
) -> ReplacementForecastReactorHookResult:
    """Keep replacement shadow/veto advisory when its own evidence is unavailable."""

    return ReplacementForecastReactorHookResult(
        status="SHADOW_ONLY",
        reason_codes=reason_codes,
        effective_direction=candidate.baseline_direction,
        effective_q_posterior=candidate.baseline_q_posterior,
        effective_q_lcb=candidate.baseline_q_lcb,
        effective_kelly_fraction=candidate.baseline_kelly_fraction,
    )


def _candidate_bin_id(proof: Any) -> str | None:
    direction = str(getattr(proof, "direction", "") or "")
    if ":" in direction:
        suffix = direction.rsplit(":", 1)[-1].strip()
        if suffix:
            return suffix
    candidate = getattr(proof, "candidate", None)
    bin_obj = getattr(candidate, "bin", None)
    label = getattr(bin_obj, "label", None)
    if isinstance(label, str) and label:
        return label
    for attr in ("range_label", "label", "bin_label"):
        value = getattr(candidate, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _replacement_q_lcb_for_candidate(
    proof: Any,
    *,
    replacement_bundle: object | None,
    cap_to_baseline: bool = True,
) -> float:
    baseline_q_lcb = float(getattr(proof, "q_lcb_5pct"))
    if replacement_bundle is None:
        return baseline_q_lcb
    bin_id = _candidate_bin_id(proof)
    if not bin_id:
        return baseline_q_lcb
    direction = str(getattr(proof, "direction", "") or "")
    q = getattr(replacement_bundle, "q", {}) or {}
    q_lcb = getattr(replacement_bundle, "q_lcb", None) or {}
    q_ucb = getattr(replacement_bundle, "q_ucb", None) or {}
    if direction.startswith("buy_yes"):
        raw = q_lcb.get(bin_id)
    elif direction.startswith("buy_no"):
        raw = None
        for key in (
            f"buy_no:{bin_id}",
            f"no:{bin_id}",
            f"{bin_id}:buy_no",
            f"{bin_id}:no",
        ):
            if key in q_lcb:
                raw = q_lcb[key]
                break
        if raw is None:
            for key in (bin_id, f"yes:{bin_id}", f"buy_yes:{bin_id}"):
                if key in q_ucb:
                    raw = 1.0 - float(q_ucb[key])
                    break
    else:
        raw = q_lcb.get(bin_id)
    if raw is None:
        return baseline_q_lcb
    replacement_q_lcb = min(max(float(raw), 0.0), 1.0)
    if cap_to_baseline:
        return min(replacement_q_lcb, baseline_q_lcb)
    return replacement_q_lcb


def _replacement_q_posterior_for_candidate(
    proof: Any,
    *,
    replacement_bundle: object | None,
) -> float:
    baseline_q = float(getattr(proof, "q_posterior", getattr(proof, "q_lcb_5pct", 0.0)))
    if replacement_bundle is None:
        return min(max(baseline_q, 0.0), 1.0)
    bin_id = _candidate_bin_id(proof)
    if not bin_id:
        return min(max(baseline_q, 0.0), 1.0)
    q = getattr(replacement_bundle, "q", {}) or {}
    direction = str(getattr(proof, "direction", "") or "")
    if direction.startswith("buy_no"):
        for key in (
            f"buy_no:{bin_id}",
            f"no:{bin_id}",
            f"{bin_id}:buy_no",
            f"{bin_id}:no",
        ):
            if key in q:
                return min(max(float(q[key]), 0.0), 1.0)
        if bin_id in q:
            return 1.0 - min(max(float(q[bin_id]), 0.0), 1.0)
        return min(max(baseline_q, 0.0), 1.0)
    raw = q.get(bin_id)
    if raw is None:
        return min(max(baseline_q, 0.0), 1.0)
    return min(max(float(raw), 0.0), 1.0)


def _candidate_view_from_proof(
    proof: Any,
    decision_time: datetime,
    *,
    replacement_bundle: object | None = None,
    cap_replacement_q_lcb_to_baseline: bool = True,
) -> ReplacementForecastCandidateView:
    candidate = getattr(proof, "candidate")
    return ReplacementForecastCandidateView(
        baseline_direction=str(getattr(proof, "direction")),
        baseline_q_posterior=float(getattr(proof, "q_posterior", getattr(proof, "q_lcb_5pct", 0.0))),
        baseline_q_lcb=float(getattr(proof, "q_lcb_5pct")),
        baseline_kelly_fraction=0.0,
        candidate_direction=str(getattr(proof, "direction")),
        candidate_q_posterior=_replacement_q_posterior_for_candidate(
            proof,
            replacement_bundle=replacement_bundle,
        ),
        candidate_q_lcb=_replacement_q_lcb_for_candidate(
            proof,
            replacement_bundle=replacement_bundle,
            cap_to_baseline=cap_replacement_q_lcb_to_baseline,
        ),
        candidate_kelly_fraction=0.0,
        market_snapshot_id=str(getattr(proof, "executable_snapshot_id", None) or ""),
        condition_id=str(getattr(candidate, "condition_id", "") or ""),
        token_id=str(getattr(proof, "token_id", "") or ""),
        decision_time=_to_utc(decision_time).isoformat(),
    )


def _write_replacement_shadow_decision(
    conn: sqlite3.Connection,
    result: ReplacementForecastReactorHookResult,
) -> None:
    decision = result.veto_decision
    if decision is None:
        return
    row = decision.as_shadow_decision_row()
    baseline_source_run_id = None
    dependencies = row["dependency_source_run_ids_json"]
    if isinstance(dependencies, Mapping):
        raw_baseline = dependencies.get("baseline_b0")
        baseline_source_run_id = str(raw_baseline) if raw_baseline is not None else None
    conn.execute(
        """
        INSERT INTO replacement_shadow_decisions (
            posterior_id, baseline_source_run_id, market_snapshot_id,
            condition_id, token_id, decision_time, baseline_direction,
            candidate_direction, allowed_direction, baseline_q_lcb,
            candidate_q_lcb, allowed_q_lcb, baseline_kelly_fraction,
            candidate_kelly_fraction, allowed_kelly_fraction, veto,
            veto_reason, dependency_source_run_ids_json, provenance_json,
            trade_authority_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(posterior_id, market_snapshot_id, condition_id, token_id, decision_time)
        DO UPDATE SET
            baseline_source_run_id = excluded.baseline_source_run_id,
            baseline_direction = excluded.baseline_direction,
            candidate_direction = excluded.candidate_direction,
            allowed_direction = excluded.allowed_direction,
            baseline_q_lcb = excluded.baseline_q_lcb,
            candidate_q_lcb = excluded.candidate_q_lcb,
            allowed_q_lcb = excluded.allowed_q_lcb,
            baseline_kelly_fraction = excluded.baseline_kelly_fraction,
            candidate_kelly_fraction = excluded.candidate_kelly_fraction,
            allowed_kelly_fraction = excluded.allowed_kelly_fraction,
            veto = excluded.veto,
            veto_reason = excluded.veto_reason,
            dependency_source_run_ids_json = excluded.dependency_source_run_ids_json,
            provenance_json = excluded.provenance_json,
            trade_authority_status = excluded.trade_authority_status
        """,
        (
            row["posterior_id"],
            baseline_source_run_id,
            row["market_snapshot_id"],
            row["condition_id"],
            row["token_id"],
            row["decision_time"],
            row["baseline_direction"],
            row["candidate_direction"],
            row["allowed_direction"],
            row["baseline_q_lcb"],
            row["candidate_q_lcb"],
            row["allowed_q_lcb"],
            row["baseline_kelly_fraction"],
            row["candidate_kelly_fraction"],
            row["allowed_kelly_fraction"],
            row["veto"],
            row["veto_reason"],
            json.dumps(row["dependency_source_run_ids_json"], sort_keys=True, separators=(",", ":"), default=str),
            json.dumps(row["provenance_json"], sort_keys=True, separators=(",", ":"), default=str),
            row["trade_authority_status"],
        ),
    )


def build_replacement_forecast_event_hook(
    request: ReplacementForecastHookFactoryInput,
) -> Callable[[Any, OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None]:
    """Build a read-only replacement hook for the real event reactor path."""

    if not isinstance(request, ReplacementForecastHookFactoryInput):
        raise TypeError("request must be ReplacementForecastHookFactoryInput")
    policy = resolve_replacement_forecast_runtime_policy(
        request.runtime_flags,
        promotion_evidence=request.promotion_evidence,
        capital_objective_evidence=request.capital_objective_evidence,
    )

    def _hook(
        proof: Any,
        event: OpportunityEvent,
        decision_time: datetime,
    ) -> ReplacementForecastReactorHookResult | None:
        if policy.status == "DISABLED":
            return None
        candidate = proof.candidate
        city = str(getattr(candidate, "city", "") or "")
        target_date = str(getattr(candidate, "target_date", "") or "")
        temperature_metric = str(getattr(candidate, "metric", "") or "")
        live_switch = build_replacement_forecast_live_switch_report(
            ReplacementForecastLiveSwitchInput(
                runtime_policy=policy,
                available_files=request.available_files,
                forecast_tables=_available_tables(
                    request.forecast_conn,
                    REQUIRED_FORECAST_TABLES,
                ),
                world_tables=request.world_tables,
                trade_tables=request.trade_tables
                if request.trade_tables is not None
                else _available_tables(request.trade_conn, REQUIRED_TRADE_TABLES),
                enabled_evidence_gates=request.enabled_evidence_gates,
                source_fact_status=request.source_fact_status,
                data_fact_status=request.data_fact_status,
            )
        )
        readiness = _latest_replacement_readiness(
            request.forecast_conn,
            city=city,
            target_date=target_date,
            temperature_metric=temperature_metric,
        )
        switch_decision = evaluate_replacement_forecast_switch_decision(
            ReplacementForecastSwitchDecisionInput(
                runtime_policy=policy,
                live_switch_report=live_switch,
                readiness=readiness,
                refit_decision=request.refit_decision,
                capital_objective_evidence=request.capital_objective_evidence,
            )
        )
        candidate_view = _candidate_view_from_proof(proof, decision_time)
        if switch_decision.blocked:
            if policy.status != "LIVE_AUTHORITY":
                return _shadow_unavailable_result(
                    candidate_view,
                    reason_codes=switch_decision.reason_codes,
                )
            return ReplacementForecastReactorHookResult(
                status="BLOCKED",
                reason_codes=switch_decision.reason_codes,
                effective_direction=candidate_view.baseline_direction,
                effective_q_posterior=candidate_view.baseline_q_posterior,
                effective_q_lcb=candidate_view.baseline_q_lcb,
                effective_kelly_fraction=candidate_view.baseline_kelly_fraction,
            )
        baseline_bundle = (
            request.baseline_bundle_provider(proof, event, decision_time)
            if request.baseline_bundle_provider is not None
            else None
        )
        if baseline_bundle is None:
            if policy.status != "LIVE_AUTHORITY":
                return _shadow_unavailable_result(
                    candidate_view,
                    reason_codes=("REPLACEMENT_HOOK_BASELINE_BUNDLE_MISSING",),
                )
        bundle_result = None
        if readiness is not None:
            bundle_result = read_replacement_forecast_bundle(
                request.forecast_conn,
                baseline_bundle=baseline_bundle,
                readiness=readiness,
                city=city,
                target_date=target_date,
                temperature_metric=temperature_metric,
                decision_time=decision_time,
                require_baseline_bundle=policy.status != "LIVE_AUTHORITY",
            )
        if bundle_result is None or not bundle_result.ok:
            reason_code = bundle_result.reason_code if bundle_result is not None else "REPLACEMENT_HOOK_READINESS_MISSING"
            if policy.status != "LIVE_AUTHORITY":
                return _shadow_unavailable_result(
                    candidate_view,
                    reason_codes=(reason_code,),
                )
            return _blocked_result(
                candidate_view,
                reason_code=reason_code,
            )
        replacement_bundle = bundle_result.bundle if bundle_result is not None and bundle_result.ok else None
        candidate_view = _candidate_view_from_proof(
            proof,
            decision_time,
            replacement_bundle=replacement_bundle,
            cap_replacement_q_lcb_to_baseline=policy.status != "LIVE_AUTHORITY",
        )
        hook_result = apply_replacement_forecast_reactor_hook(
            policy=policy,
            switch_decision=switch_decision,
            candidate=candidate_view,
            replacement_bundle=replacement_bundle,
            readiness=readiness,
        )
        if hook_result.status == "SHADOW_VETO_ONLY":
            try:
                _write_replacement_shadow_decision(request.forecast_conn, hook_result)
            except Exception:
                if policy.status != "LIVE_AUTHORITY":
                    return _shadow_unavailable_result(
                        candidate_view,
                        reason_codes=("REPLACEMENT_SHADOW_DECISION_WRITE_FAILED",),
                    )
                return _blocked_result(candidate_view, reason_code="REPLACEMENT_SHADOW_DECISION_WRITE_FAILED")
        return hook_result

    return _hook
