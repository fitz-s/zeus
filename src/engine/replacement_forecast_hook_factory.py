"""DB-backed hook factory for replacement forecast pre-intent admission."""

from __future__ import annotations

import sqlite3
import json
import math
import hashlib
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
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastReadinessDecision,
    normalize_replacement_readiness_status,
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
            HIGH_DATA_VERSION,
            LOW_DATA_VERSION,
            city,
            target_date,
            temperature_metric,
        ),
    ).fetchone()
    if row is None:
        return None
    row_map = dict(row)
    status = normalize_replacement_readiness_status(str(row_map.get("status") or "BLOCKED"))
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


def _replacement_unavailable_result(
    candidate: ReplacementForecastCandidateView,
    *,
    reason_codes: tuple[str, ...],
) -> ReplacementForecastReactorHookResult:
    """Fail closed when replacement live authority is unavailable."""

    return ReplacementForecastReactorHookResult(
        status="BLOCKED",
        reason_codes=reason_codes,
        effective_direction=candidate.baseline_direction,
        effective_q_posterior=candidate.baseline_q_posterior,
        effective_q_lcb=candidate.baseline_q_lcb,
        effective_kelly_fraction=candidate.baseline_kelly_fraction,
    )


def _temperature_bound_to_c(value: object, *, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("candidate bin bounds must be finite")
    normalized = unit.strip().upper()
    if normalized == "C":
        return number
    if normalized == "F":
        return (number - 32.0) * 5.0 / 9.0
    raise ValueError("candidate bin unit must be C or F")


def _same_optional_float(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _current_bin_topology_hash(proof: Any, event: OpportunityEvent | None = None) -> str | None:
    for owner in (
        proof,
        getattr(proof, "candidate", None),
        getattr(getattr(proof, "candidate", None), "family", None),
        getattr(proof, "family", None),
    ):
        raw = getattr(owner, "bin_topology_hash", None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if event is not None:
        try:
            payload = json.loads(event.payload_json or "{}")
        except Exception:
            payload = {}
        if isinstance(payload, Mapping):
            for key in ("bin_topology_hash", "current_bin_topology_hash", "market_family_hash"):
                raw = payload.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
    return None


def _candidate_bin_id(proof: Any, *, replacement_bundle: object | None = None) -> str | None:
    """Resolve the replacement q key from canonical bin bounds, never labels.

    Human labels and direction suffixes are display/provenance text; they are
    not stable enough to bind a replacement posterior vector. The only accepted
    mapping here is candidate Bin(low/high/unit) -> posterior provenance
    bin_topology lower_c/upper_c -> bin_id.
    """

    if replacement_bundle is None:
        return None
    provenance = getattr(replacement_bundle, "provenance_json", None) or {}
    if not isinstance(provenance, Mapping):
        return None
    topology = provenance.get("bin_topology")
    if not isinstance(topology, list) or not topology:
        return None
    candidate = getattr(proof, "candidate", None)
    bin_obj = getattr(candidate, "bin", None)
    unit = str(getattr(bin_obj, "unit", "") or "")
    if not unit:
        return None
    lower_c = _temperature_bound_to_c(getattr(bin_obj, "low", None), unit=unit)
    upper_c = _temperature_bound_to_c(getattr(bin_obj, "high", None), unit=unit)
    matches: list[str] = []
    for item in topology:
        if not isinstance(item, Mapping):
            continue
        bin_id = str(item.get("bin_id") or "").strip()
        if not bin_id:
            continue
        if _same_optional_float(item.get("lower_c"), lower_c) and _same_optional_float(item.get("upper_c"), upper_c):
            matches.append(bin_id)
    if len(matches) != 1:
        return None
    return matches[0]


def _h3_selected_bin_id(replacement_bundle: object | None) -> str | None:
    if replacement_bundle is None:
        return None
    q = getattr(replacement_bundle, "q", None) or {}
    if not isinstance(q, Mapping) or not q:
        return None
    return max((str(key) for key in q), key=lambda key: (float(q[key]), key))


def _h3_direction_for_candidate_bin(*, candidate_bin_id: str | None, replacement_bundle: object | None) -> str | None:
    selected = _h3_selected_bin_id(replacement_bundle)
    if not selected or not candidate_bin_id:
        return None
    side = "buy_yes" if selected == candidate_bin_id else "buy_no"
    return f"{side}:{candidate_bin_id}"


def _replacement_direction_for_candidate(
    proof: Any,
    *,
    replacement_bundle: object | None,
    bin_id: str | None,
) -> str:
    replacement_direction = _h3_direction_for_candidate_bin(
        candidate_bin_id=bin_id,
        replacement_bundle=replacement_bundle,
    )
    if replacement_direction is not None:
        return replacement_direction
    return str(getattr(proof, "direction", "") or "")


def _replacement_yes_point_for_bin(
    replacement_bundle: object | None,
    *,
    bin_id: str | None,
) -> float | None:
    if replacement_bundle is None or not bin_id:
        return None
    q = getattr(replacement_bundle, "q", {}) or {}
    if not isinstance(q, Mapping):
        return None
    raw = q.get(bin_id)
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), 1.0)
    except (TypeError, ValueError):
        return None


def _replacement_q_lcb_for_candidate(
    proof: Any,
    *,
    replacement_bundle: object | None,
) -> float:
    # Wave-2 item 1 (2026-06-12): SINGLE q AUTHORITY. The replacement chain owns the live
    # q_lcb; the legacy baseline LCB no longer CAPS it. When a replacement q_lcb exists for
    # the candidate bin it is used directly (uncapped). The baseline is retained ONLY as an
    # honest fail-soft default: when there is no replacement data for the bin (bundle absent,
    # no bin binding, no q_lcb entry) the candidate falls back to baseline_q_lcb — that is a
    # legacy strategy genuinely running on baseline q, not a cap on the replacement value.
    # Any non-live replacement artifact is unavailable to this execution hook.
    baseline_q_lcb = float(getattr(proof, "q_lcb_5pct"))
    if replacement_bundle is None:
        return baseline_q_lcb
    bin_id = _candidate_bin_id(proof, replacement_bundle=replacement_bundle)
    if not bin_id:
        return baseline_q_lcb
    q_yes = _replacement_yes_point_for_bin(replacement_bundle, bin_id=bin_id)
    if q_yes is None:
        return baseline_q_lcb
    direction = _replacement_direction_for_candidate(
        proof,
        replacement_bundle=replacement_bundle,
        bin_id=bin_id,
    )
    q_lcb = getattr(replacement_bundle, "q_lcb", None) or {}
    q_ucb = getattr(replacement_bundle, "q_ucb", None) or {}
    if direction.startswith("buy_yes"):
        raw = q_lcb.get(bin_id)
        if raw is None:
            return baseline_q_lcb
        return min(max(float(raw), 0.0), q_yes)
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
        if raw is not None:
            return min(max(float(raw), 0.0), 1.0 - q_yes)
        if isinstance(q_ucb, Mapping) and bin_id in q_ucb:
            try:
                q_ucb_yes = min(max(float(q_ucb[bin_id]), 0.0), 1.0)
            except (TypeError, ValueError):
                return 0.0
            return min(max(1.0 - q_ucb_yes, 0.0), 1.0 - q_yes)
        return 0.0
    else:
        raw = q_lcb.get(bin_id)
    if raw is None:
        return baseline_q_lcb
    return min(max(float(raw), 0.0), 1.0)


def _replacement_q_posterior_for_candidate(
    proof: Any,
    *,
    replacement_bundle: object | None,
) -> float:
    baseline_q = float(getattr(proof, "q_posterior", getattr(proof, "q_lcb_5pct", 0.0)))
    if replacement_bundle is None:
        return min(max(baseline_q, 0.0), 1.0)
    bin_id = _candidate_bin_id(proof, replacement_bundle=replacement_bundle)
    if not bin_id:
        return min(max(baseline_q, 0.0), 1.0)
    q_yes = _replacement_yes_point_for_bin(replacement_bundle, bin_id=bin_id)
    if q_yes is None:
        return min(max(baseline_q, 0.0), 1.0)
    q = getattr(replacement_bundle, "q", {}) or {}
    direction = _replacement_direction_for_candidate(
        proof,
        replacement_bundle=replacement_bundle,
        bin_id=bin_id,
    )
    if direction.startswith("buy_no"):
        for key in (
            f"buy_no:{bin_id}",
            f"no:{bin_id}",
            f"{bin_id}:buy_no",
            f"{bin_id}:no",
        ):
            if key in q:
                return min(max(float(q[key]), 0.0), 1.0)
        return 1.0 - q_yes
    return q_yes


def _candidate_view_from_proof(
    proof: Any,
    decision_time: datetime,
    *,
    replacement_bundle: object | None = None,
) -> ReplacementForecastCandidateView:
    candidate = getattr(proof, "candidate")
    bin_id = _candidate_bin_id(proof, replacement_bundle=replacement_bundle)
    candidate_direction = _h3_direction_for_candidate_bin(candidate_bin_id=bin_id, replacement_bundle=replacement_bundle)
    if candidate_direction is None:
        candidate_direction = str(getattr(proof, "direction"))
    return ReplacementForecastCandidateView(
        baseline_direction=str(getattr(proof, "direction")),
        baseline_q_posterior=float(getattr(proof, "q_posterior", getattr(proof, "q_lcb_5pct", 0.0))),
        baseline_q_lcb=float(getattr(proof, "q_lcb_5pct")),
        baseline_kelly_fraction=0.0,
        candidate_direction=candidate_direction,
        candidate_q_posterior=_replacement_q_posterior_for_candidate(
            proof,
            replacement_bundle=replacement_bundle,
        ),
        candidate_q_lcb=_replacement_q_lcb_for_candidate(
            proof,
            replacement_bundle=replacement_bundle,
        ),
        candidate_kelly_fraction=0.0,
        market_snapshot_id=str(getattr(proof, "executable_snapshot_id", None) or ""),
        condition_id=str(getattr(candidate, "condition_id", "") or ""),
        token_id=str(getattr(proof, "token_id", "") or ""),
        decision_time=_to_utc(decision_time).isoformat(),
    )


def build_replacement_forecast_event_hook(
    request: ReplacementForecastHookFactoryInput,
) -> Callable[[Any, OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None]:
    """Build the DB-backed replacement hook for the real event reactor path.

    The underlying reactor hook is pure. This factory wrapper reads only
    live-authority posterior/readiness rows; non-live replacement artifacts do
    not write audit rows or participate in execution decisions.
    """

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
            if not policy.can_initiate_trade:
                return _replacement_unavailable_result(
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
            if not policy.can_initiate_trade:
                return _replacement_unavailable_result(
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
                current_bin_topology_hash=_current_bin_topology_hash(proof, event),
                require_baseline_bundle=not policy.can_initiate_trade,
            )
        if bundle_result is None or not bundle_result.ok:
            reason_code = bundle_result.reason_code if bundle_result is not None else "REPLACEMENT_HOOK_READINESS_MISSING"
            if not policy.can_initiate_trade:
                return _replacement_unavailable_result(
                    candidate_view,
                    reason_codes=(reason_code,),
                )
            return _blocked_result(
                candidate_view,
                reason_code=reason_code,
            )
        replacement_bundle = bundle_result.bundle if bundle_result is not None and bundle_result.ok else None
        # Wave-2 item 1: single q authority — the replacement q_lcb is used directly
        # only when the runtime policy is live.
        candidate_view = _candidate_view_from_proof(
            proof,
            decision_time,
            replacement_bundle=replacement_bundle,
        )
        hook_result = apply_replacement_forecast_reactor_hook(
            policy=policy,
            switch_decision=switch_decision,
            candidate=candidate_view,
            replacement_bundle=replacement_bundle,
            readiness=readiness,
        )
        return hook_result

    return _hook
