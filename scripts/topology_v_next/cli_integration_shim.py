# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §1.3, §2, §3, §4, §8
"""
CLI Integration Shim for topology v_next shadow blocking (P3.3).

Wires v_next.admit into topology_doctor.run_navigation() as a transparent shadow.
Current admission remains authoritative; this shim advises only.

Public API:
- maybe_shadow_compare(payload, *, task, files, intent, v_next_shadow, friction_state) -> dict
- format_output(decision: AdmissionDecision) -> dict
- map_old_status_to_severity(old_status: str) -> Severity

Anti-PHRASING_GAME_TAX guard:
- `task` is hashed via sha256[:16] BEFORE passing to v_next.admit as `hint=task_hash`.
- Raw task string is NEVER passed as hint (prevents phrase-sensitive closest_rejected_profile).
- No `phrase`, `task_phrase`, `wording`, or `hint_text` parameter on any public function.
- `hint` in admit() is OUTPUT-ONLY: it feeds only closest_rejected_profile, never routing.

See SCAFFOLD §8.4 for full guard rationale.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, UTC
from typing import Any

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    AdmissionDecision,
    BindingLayer,
    Severity,
    IssueRecord,
)
from scripts.topology_v_next.profile_loader import load_binding_layer
from scripts.topology_v_next.divergence_logger import (
    DivergenceRecord,
    log_divergence,
    classify_divergence,
    OLD_STATUS_TO_NEW_SEVERITY,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def maybe_shadow_compare(
    payload: dict[str, Any],
    *,
    task: str,
    files: list[str],
    intent: str | None,
    v_next_shadow: bool,
    friction_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Transparent wrapper that runs v_next.admit in shadow when v_next_shadow=True.

    Returns payload unchanged when v_next_shadow=False (transparent no-op).

    On v_next failure: logs to stderr, returns payload enriched with
    v_next_shadow={"error": ..., "ok": None, ...}.  Never raises.
    """
    if not v_next_shadow:
        return payload

    try:
        # Anti-PHRASING_GAME_TAX: pass hash, never raw task (SCAFFOLD §8.4)
        task_hash = hashlib.sha256(task.encode()).hexdigest()[:16]

        decision = admit(
            intent=intent,
            files=files,
            hint=task_hash,               # hash, not raw task — see guard above
            binding=_extract_binding(payload),
            friction_state=friction_state if friction_state is not None else {},
        )
        envelope = format_output(decision)
        record = _build_divergence_record(
            payload=payload,
            decision=decision,
            task=task,
            task_hash=task_hash,
            files=files,
            intent=intent,
        )
        log_divergence(record)
    except Exception as exc:  # noqa: BLE001  — must never break admission
        sys.stderr.write(f"[v_next_shadow] failed: {type(exc).__name__}: {exc}\n")
        envelope = {
            "error": f"{type(exc).__name__}: {exc}",
            "ok": None,
            "decision": None,
            "advisory": [],
            "blockers": [],
        }

    return {**payload, "v_next_shadow": envelope}


def format_output(decision: AdmissionDecision) -> dict[str, Any]:
    """
    Derive the SCAFFOLD §2.1 normalized envelope from an AdmissionDecision.

    Pure function — no I/O.  All four mandatory fields (ok, decision,
    advisory, blockers) are always present.  advisory and blockers are
    always lists (never None).
    """
    advisory = [
        issue.to_dict()
        for issue in decision.issues
        if issue.severity == Severity.ADVISORY
    ]
    # SCAFFOLD §2.1: blockers sourced only from decision.issues (not kernel_alerts).
    # Kernel alerts are surfaced separately via the kernel_alerts field below.
    # Callers that need full hard-stop evidence should check both blockers AND kernel_alerts.
    blockers = [
        issue.to_dict()
        for issue in decision.issues
        if issue.severity in {Severity.SOFT_BLOCK, Severity.HARD_STOP}
    ]
    return {
        # Mandatory fields per SCAFFOLD §2.2
        "ok": decision.ok,
        "decision": decision.severity.value,  # ADMIT|ADVISORY|SOFT_BLOCK|HARD_STOP
        "advisory": advisory,
        "blockers": blockers,
        # Pass-through fields from AdmissionDecision (SCAFFOLD §2.1)
        "profile_matched": decision.profile_matched,
        "intent_class": decision.intent_class.value,
        "missing_phrases": list(decision.missing_phrases),
        "closest_rejected_profile": decision.closest_rejected_profile,
        "friction_budget_used": decision.friction_budget_used,
        "companion_files": list(decision.companion_files),
        "diagnosis": decision.diagnosis.to_dict() if decision.diagnosis is not None else None,
        "kernel_alerts": [a.to_dict() for a in decision.kernel_alerts],
    }


def map_old_status_to_severity(old_status: str) -> Severity:
    """
    Map the current run_navigation admission status string to a v_next Severity.

    Raises KeyError when old_status is not in the mapping table (e.g. when
    old admission returns a status that has no v_next equivalent).  Callers
    should handle KeyError when processing HARD_STOP-adjacent values.

    See SCAFFOLD §4.4 for the full mapping table and rationale.
    """
    return OLD_STATUS_TO_NEW_SEVERITY[old_status]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_binding(payload: dict[str, Any]) -> BindingLayer | None:
    """
    Extract a BindingLayer from the payload's route_card if present.

    Returns None when no resolved binding is available — admit() will then
    load from the default YAML path (architecture/topology_v_next_binding.yaml).
    """
    route_card = payload.get("route_card") or {}
    binding_raw = route_card.get("binding")
    if binding_raw is None:
        return None
    # binding is stored as a dict in route_card; re-load from canonical YAML
    # because we cannot reconstruct a BindingLayer from a raw dict cheaply.
    # The re-load cost (~10ms) is acceptable in shadow mode.
    try:
        return load_binding_layer("architecture/topology_v_next_binding.yaml")
    except Exception:  # noqa: BLE001
        return None


def _extract_old_admission(payload: dict[str, Any]) -> tuple[str, str | None]:
    """
    Extract (old_status, old_profile_resolved) from the payload.

    old_status: one of {admitted, advisory_only, blocked, scope_expansion_required,
                         route_contract_conflict, ambiguous}.
    old_profile_resolved: the profile_id resolved by current admission, or None.
    """
    admission = payload.get("admission") or {}
    old_status = admission.get("status", "admitted")
    route_card = payload.get("route_card") or {}
    old_profile_resolved = route_card.get("profile_id") or route_card.get("profile") or None
    return old_status, old_profile_resolved


def _build_divergence_record(
    *,
    payload: dict[str, Any],
    decision: AdmissionDecision,
    task: str,
    task_hash: str,
    files: list[str],
    intent: str | None,
) -> DivergenceRecord:
    """
    Assemble a DivergenceRecord from the current payload and v_next decision.

    task_hash MUST be sha256(task)[:16].  Passing the raw task string would
    risk logging it in the JSONL record — the hash prevents that (SCAFFOLD §4.1).
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + \
         f"{datetime.now(UTC).microsecond // 1000:03d}Z"

    old_status, old_profile_resolved = _extract_old_admission(payload)

    # Derive intent_typed from the decision (post intent_resolver normalization)
    intent_typed = decision.intent_class.value

    # P2 carry-forward: scan for MISSING_COMPANION and companion_skip_token_used issues
    missing_companion = [
        issue.path or ""
        for issue in decision.issues
        if getattr(issue, "code", None) == "missing_companion"
    ]
    companion_skip_used = any(
        getattr(issue, "code", None) == "companion_skip_token_used"
        for issue in decision.issues
    )

    # Detect friction pattern hit (first one wins — admission engine sets at most one)
    friction_pattern_hit: str | None = None
    from scripts.topology_v_next.dataclasses import FrictionPattern  # noqa: PLC0415
    for issue in decision.issues:
        code = getattr(issue, "code", None)
        if code and code.startswith("friction_"):
            friction_pattern_hit = code
            break
    # Also check diagnosis
    if friction_pattern_hit is None and decision.diagnosis is not None:
        diag_code = getattr(decision.diagnosis, "friction_pattern", None)
        if diag_code is not None:
            friction_pattern_hit = (
                diag_code.value if isinstance(diag_code, FrictionPattern) else str(diag_code)
            )

    record = DivergenceRecord(
        ts=ts,
        schema_version="1",
        event_type="agree",   # placeholder; overwritten by classify below
        profile_resolved_old=old_profile_resolved,
        profile_resolved_new=decision.profile_matched,
        intent_typed=intent_typed,
        intent_supplied=intent,
        files=list(files),
        old_admit_status=old_status,
        new_admit_severity=decision.severity.value,
        new_admit_ok=decision.ok,
        agreement_class="",   # computed next
        friction_pattern_hit=friction_pattern_hit,
        missing_companion=missing_companion,
        companion_skip_used=companion_skip_used,
        closest_rejected_profile=decision.closest_rejected_profile,
        kernel_alert_count=len(decision.kernel_alerts),
        friction_budget_used=decision.friction_budget_used,
        task_hash=task_hash,
        error=None,
    )

    # Classify agreement and rebuild (frozen dataclass requires replace)
    import dataclasses as _dc  # noqa: PLC0415
    agreement_class = classify_divergence(record)
    # Determine event_type from agreement_class
    if agreement_class == "SKIP_HONORED":
        event_type = "companion_skip_honored"
    elif agreement_class == "AGREE":
        event_type = "agree"
    else:
        event_type = "divergence_observation"

    return _dc.replace(record, agreement_class=agreement_class, event_type=event_type)
