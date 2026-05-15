# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.5, §4, §5.3, §5.4
#                  docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §3, §3.1, §3.2
"""
Admission Engine for topology v_next.

Orchestrates Universal §4 Profile Matching Algorithm steps 1-8.
Sole public entry: admit().

CRITICAL ANTI-SIDECAR PROPERTIES (SCAFFOLD §5.3 / §5.4):
- `task` / `task_phrase` is NOT a parameter. Only `intent`, `files`, `hint`.
- hint flows ONLY into composition_rules.explain_rejected() and
  closest_rejected_profile; it CANNOT influence profile matching.
- No call to topology_doctor_digest.build_digest() or any existing kernel.
- No import of cli_integration_shim or divergence_logger (P2 packet).

Codex-importable: stdlib + PyYAML only.
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import PurePosixPath
from typing import Any

from scripts.topology_v_next.dataclasses import (
    AdmissionDecision,
    BindingLayer,
    DiagnosisEntry,
    FrictionPattern,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.profile_loader import load_binding_layer
from scripts.topology_v_next.intent_resolver import resolve_intent
from scripts.topology_v_next.hard_safety_kernel import kernel_check, is_hard_stopped
from scripts.topology_v_next.coverage_map import resolve_candidates, coverage_gaps
from scripts.topology_v_next.composition_rules import apply_composition, explain_rejected
from scripts.topology_v_next.companion_loop_break import companion_loop_break
from scripts.topology_v_next.severity_overrides import (
    apply_overrides as _apply_severity_overrides_impl,
    effective_severity as _effective_severity_impl,
)
# P2.1: companion_skip_logger imported lazily inside _check_companion_required
# to allow the module to be added in a subsequent commit without breaking P1.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def admit(
    intent: str | Intent | None,
    files: list[str],
    hint: str = "",
    *,
    binding: BindingLayer | None = None,
    friction_state: dict[str, Any] | None = None,
) -> AdmissionDecision:
    """
    Run the full Universal §4 admission algorithm and return an AdmissionDecision.

    Parameters
    ----------
    intent:
        Caller-supplied intent (typed enum, string, or None).
        NOT derived from any phrase or free text.
    files:
        List of file paths in the submitted change set.
    hint:
        Optional free-text hint. Flows ONLY into closest_rejected_profile
        diagnostic — cannot influence profile matching.
    binding:
        Loaded BindingLayer. When None, loads from the default YAML path.
        Raises FileNotFoundError naming the path if absent.
    friction_state:
        Optional dict from CLI shim (P2 packet). When None (all P1 calls),
        friction_budget_used defaults to 1 and SLICING_PRESSURE detection
        does NOT run.

    Returns
    -------
    AdmissionDecision — fully populated per Universal §2.3 / §11.
    """
    # Step 0: Load binding if not supplied
    if binding is None:
        binding = load_binding_layer("architecture/topology_v_next_binding.yaml")

    # Step 1: Resolve and validate intent
    resolved_intent, intent_issues = _resolve_intent(intent, binding)

    # Step 2: Run Hard Safety Kernel (Universal §15 G1 — runs regardless)
    kernel_alerts = _run_kernel(files, binding)

    # Step 3: Hard-stop short-circuit (d7: use kernel_alerts list, not a second iteration)
    if kernel_alerts:
        return _build_decision(
            ok=False,
            profile_matched=None,
            intent_class=resolved_intent,
            severity=Severity.HARD_STOP,
            issues=tuple(intent_issues),
            companion_files=(),
            missing_phrases=(),
            closest_rejected_profile=None,
            friction_budget_used=_increment_friction_budget(friction_state),
            diagnosis=DiagnosisEntry(
                pattern=FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING,
                evidence=f"Hard-stop path detected in submitted files: {files}",
                resolution_path=(
                    "Remove the protected path from the change set. "
                    "Hard-stop paths require explicit governance override."
                ),
            ),
            kernel_alerts=tuple(kernel_alerts),
        )

    # Step 4: Resolve candidate profiles via Coverage Map
    candidates = _resolve_candidates(files, binding)

    # Step 5: Accumulate issues (coverage gaps + intent issues)
    all_issues: list[IssueRecord] = list(intent_issues)
    all_issues.extend(coverage_gaps(candidates, binding.coverage_map))

    # Step 6: Check authority status for touched files
    authority_issues = _check_authority_status(files, binding.artifact_authority_status)
    all_issues.extend(authority_issues)

    # Step 7: Apply companion loop break check
    companion_admit, missing_companion, companion_issue = _apply_companion_loop(
        resolved_intent, files, binding
    )
    companion_files: tuple[str, ...] = ()
    if companion_issue is not None:
        all_issues.append(companion_issue)
    if companion_admit:
        # Companion pair complete — find the cohort profile
        from scripts.topology_v_next.composition_rules import cohort_admit
        matched_cohort = cohort_admit(resolved_intent, files, binding.cohorts)
        if matched_cohort is not None:
            companion_files = matched_cohort.files
            profile_matched = matched_cohort.profile
            all_issues = _apply_severity_overrides(all_issues, binding.severity_overrides)
            effective_sev = _effective_severity(all_issues)
            ok = effective_sev in (Severity.ADMIT, Severity.ADVISORY)
            return _build_decision(
                ok=ok,
                profile_matched=profile_matched,
                intent_class=resolved_intent,
                severity=effective_sev,
                issues=tuple(all_issues),
                companion_files=companion_files,
                missing_phrases=(),
                closest_rejected_profile=None,
                friction_budget_used=_increment_friction_budget(friction_state),
                diagnosis=_assemble_diagnosis(all_issues),
                kernel_alerts=tuple(kernel_alerts),
            )

    # Step 8: Apply composition rules C1-C4
    profile_matched, composition_issues = _apply_composition(
        resolved_intent, files, candidates, binding
    )
    all_issues.extend(composition_issues)

    # Step 8b: Check companion_required (P2.1 — authority-doc gate, SCAFFOLD §3.1)
    # Runs AFTER profile resolution so it knows the profile_id;
    # runs BEFORE severity overrides so binding can promote missing_companion advisory.
    companion_req_issues = _check_companion_required(profile_matched, files, binding)
    all_issues.extend(companion_req_issues)

    # Step 9: Closest rejected profile (hint-driven diagnostic only)
    closest_rejected = explain_rejected(candidates, binding, hint) if profile_matched is None else None

    # Step 10: Apply severity overrides and compute effective severity
    all_issues = _apply_severity_overrides(all_issues, binding.severity_overrides)
    effective_sev = _effective_severity(all_issues)

    # When profile resolved with no blocking issues, effective_sev may be ADMIT
    if profile_matched is not None and not all_issues:
        effective_sev = Severity.ADMIT

    ok = effective_sev in (Severity.ADMIT, Severity.ADVISORY)

    return _build_decision(
        ok=ok,
        profile_matched=profile_matched,
        intent_class=resolved_intent,
        severity=effective_sev,
        issues=tuple(all_issues),
        companion_files=companion_files,
        missing_phrases=(),
        closest_rejected_profile=closest_rejected,
        friction_budget_used=_increment_friction_budget(friction_state),
        diagnosis=_assemble_diagnosis(all_issues),
        kernel_alerts=tuple(kernel_alerts),
    )


# ---------------------------------------------------------------------------
# Internal helpers — SCAFFOLD §1.5 named helpers
# ---------------------------------------------------------------------------

def _run_kernel(files: list[str], binding: BindingLayer) -> list[IssueRecord]:
    """Run Hard Safety Kernel on all submitted files."""
    return kernel_check(files, binding=binding)


def _resolve_intent(
    intent_value: str | Intent | None,
    binding: BindingLayer,
) -> tuple[Intent, list[IssueRecord]]:
    """Validate and normalise the caller-supplied intent."""
    return resolve_intent(intent_value, binding=binding)


def _resolve_candidates(
    files: list[str],
    binding: BindingLayer,
) -> dict[str, set[str]]:
    """Resolve each file to its set of candidate profiles."""
    return resolve_candidates(files, binding.coverage_map)


def _apply_composition(
    intent: Intent,
    files: list[str],
    candidates: dict[str, set[str]],
    binding: BindingLayer,
) -> tuple[str | None, list[IssueRecord]]:
    """Apply composition rules C1-C4 to resolve a single profile."""
    return apply_composition(intent, files, candidates, binding)


def _apply_companion_loop(
    intent: Intent,
    files: list[str],
    binding: BindingLayer,
) -> tuple[bool, str | None, IssueRecord | None]:
    """Check companion-pair cohort declarations (Universal §9)."""
    return companion_loop_break(intent, files, binding)


def _check_companion_required(
    profile_id: str | None,
    files: list[str],
    binding: BindingLayer,
) -> list[IssueRecord]:
    """
    Check that every authority-doc declared in binding.companion_required[profile_id]
    is present in the submitted files set (P2.1 SCAFFOLD §3.2).

    Returns an empty list when:
    - profile_id is None (no profile resolved; composition already flagged it)
    - profile_id has no companion_required entries
    - all required companion docs are present in files

    Returns advisory IssueRecords (MISSING_COMPANION format) for each missing doc.

    Skip-token short-circuit: if binding.companion_skip_tokens[profile_id] is set
    AND the environment matches the token exactly, emits a companion_skip_token_used
    ADVISORY and logs to the skip-token JSONL via companion_skip_logger.

    Codex-safe: reads only binding and os.environ. No mutation of admission state.
    Single optional side-effect (skip log) delegated to companion_skip_logger.
    """
    if profile_id is None:
        return []

    required_docs: tuple[str, ...] = binding.companion_required.get(profile_id, ())
    if not required_docs:
        return []

    # Skip-token short-circuit: literal env-var match, no fuzzy (SCAFFOLD §3.2)
    skip_token: str = binding.companion_skip_tokens.get(profile_id, "")
    if skip_token:
        token_key, _, token_value = skip_token.partition("=")
        env_value = os.environ.get(token_key)
        if env_value is not None and env_value == (token_value or "1"):
            # Skip authorized — log to skip-token log, emit ADVISORY for visibility
            try:
                from scripts.topology_v_next import companion_skip_logger
                companion_skip_logger.write_skip_event(
                    profile=profile_id,
                    source_files=files,
                    expected_companions=list(required_docs),
                    token_value=skip_token,
                )
            except ImportError:
                pass  # companion_skip_logger not yet available (Commit 3)
            return [IssueRecord(
                code="companion_skip_token_used",
                path=files[0] if files else "",
                severity=Severity.ADVISORY,
                message=(
                    f"COMPANION_SKIP token used for profile '{profile_id}'; "
                    f"skipped companions: {','.join(required_docs)}; logged for review."
                ),
                metadata={"profile": profile_id, "skipped": list(required_docs)},
            )]

    # Default path: files must include every required companion doc.
    files_norm: set[str] = {PurePosixPath(f).as_posix() for f in files}
    missing: list[str] = [
        doc for doc in required_docs
        if PurePosixPath(doc).as_posix() not in files_norm
    ]
    if not missing:
        return []

    # Identify source trigger: first file already assigned to profile_id
    source_trigger = _find_source_trigger(profile_id, files, binding)

    return [
        IssueRecord(
            code="missing_companion",
            path=doc,
            severity=Severity.ADVISORY,  # P2.a default; binding promotes at P2.b
            message=(
                f"MISSING_COMPANION profile={profile_id} "
                f"missing_companion={doc} "
                f"triggered_by={source_trigger}"
            ),
            metadata={
                "profile": profile_id,
                "missing_companion": doc,
                "triggered_by": source_trigger,
                "all_required": list(required_docs),
            },
        )
        for doc in missing
    ]


def _find_source_trigger(
    profile_id: str,
    files: list[str],
    binding: BindingLayer,
) -> str:
    """
    Return the first file in *files* that is a source file for *profile_id*.

    A file is a source file for profile_id when profile_id appears in the set
    of candidate profiles resolved for that file by the coverage map.
    Falls back to files[0] when no match, or '<no-source>' when files is empty.
    """
    for f in files:
        candidates_for_file = resolve_candidates([f], binding.coverage_map)
        if profile_id in candidates_for_file.get(f, set()):
            return f
    return files[0] if files else "<no-source>"


def _apply_severity_overrides(
    issues: list[IssueRecord],
    overrides: dict[str, Severity],
) -> list[IssueRecord]:
    """
    Apply binding severity_overrides to the issue list.

    Delegates to severity_overrides.apply_overrides (P1.3 extraction, d10).
    Returns a new list with remapped severities. No mutation.
    """
    return _apply_severity_overrides_impl(issues, overrides)


def _check_authority_status(
    file_paths: list[str],
    artifact_authority_status: dict[str, dict[str, Any]],
) -> list[IssueRecord]:
    """
    Check each touched file against artifact_authority_status.

    Per SCAFFOLD §1.5 (INCONSISTENCY-5 fix):
    - Emits `authority_status_stale` ADVISORY when last_confirmed exceeds
      confirmation_ttl_days.
    - Emits `closed_packet_authority` ADVISORY when status == "CURRENT_HISTORICAL".

    Unit-testable in P1; no production caller until P2 packet wire-up.
    """
    issues: list[IssueRecord] = []
    today_ts = time.time()

    for file_path in file_paths:
        row = artifact_authority_status.get(file_path)
        if row is None:
            continue

        status = row.get("status", "")
        last_confirmed_str = row.get("last_confirmed", "")
        ttl_days = row.get("confirmation_ttl_days", 0)

        # Check CURRENT_HISTORICAL status
        if status == "CURRENT_HISTORICAL":
            issues.append(IssueRecord(
                code="closed_packet_authority",
                path=file_path,
                severity=Severity.ADVISORY,
                message=(
                    f"File '{file_path}' has authority status 'CURRENT_HISTORICAL'. "
                    "This artifact is from a closed packet that may still be load-bearing. "
                    "Verify the artifact is still needed before modification."
                ),
                metadata={"status": status, "file": file_path},
            ))

        # Check TTL staleness
        if last_confirmed_str and ttl_days > 0:
            try:
                stale = _is_ttl_exceeded(last_confirmed_str, ttl_days, today_ts)
            except (ValueError, TypeError):
                stale = False

            if stale:
                issues.append(IssueRecord(
                    code="authority_status_stale",
                    path=file_path,
                    severity=Severity.ADVISORY,
                    message=(
                        f"File '{file_path}' authority confirmation is stale. "
                        f"Last confirmed: {last_confirmed_str}, TTL: {ttl_days} days. "
                        "Re-confirm authority status before modifying."
                    ),
                    metadata={
                        "last_confirmed": last_confirmed_str,
                        "confirmation_ttl_days": ttl_days,
                        "file": file_path,
                    },
                ))

    return issues


def _assemble_diagnosis(issues: list[IssueRecord]) -> DiagnosisEntry | None:
    """
    Produce a DiagnosisEntry from the highest-severity issue, if any.

    Maps issue codes to friction patterns for Universal §12 failure-as-diagnosis.
    Returns None when issues list is empty.
    """
    if not issues:
        return None

    # Map issue codes to friction patterns
    code_to_pattern: dict[str, FrictionPattern] = {
        "coverage_gap": FrictionPattern.LEXICAL_PROFILE_MISS,
        "composition_conflict": FrictionPattern.UNION_SCOPE_EXPANSION,
        "intent_enum_unknown": FrictionPattern.INTENT_ENUM_TOO_NARROW,
        "intent_unspecified": FrictionPattern.INTENT_ENUM_TOO_NARROW,
        "intent_extension_unregistered": FrictionPattern.INTENT_ENUM_TOO_NARROW,
        "closed_packet_authority": FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING,
        "authority_status_stale": FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING,
        "companion_missing": FrictionPattern.UNION_SCOPE_EXPANSION,
        "missing_companion": FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING,
        "companion_skip_token_used": FrictionPattern.ADVISORY_OUTPUT_INVISIBILITY,
        "hard_stop_path": FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING,
    }

    _sev_order = {
        Severity.HARD_STOP: 3,
        Severity.SOFT_BLOCK: 2,
        Severity.ADVISORY: 1,
        Severity.ADMIT: 0,
    }

    # Pick the highest-severity issue
    top = max(issues, key=lambda i: _sev_order.get(i.severity, 0))
    pattern = code_to_pattern.get(top.code, FrictionPattern.ADVISORY_OUTPUT_INVISIBILITY)

    return DiagnosisEntry(
        pattern=pattern,
        evidence=top.message,
        resolution_path=_resolution_path(top.code),
    )


def _increment_friction_budget(friction_state: dict[str, Any] | None) -> int:
    """
    Read and increment friction budget from caller-supplied state.

    When friction_state is None (all P1 invocations), returns 1 unconditionally.
    State is held by the CALLER (P2 packet shim), not by a v_next service.
    SLICING_PRESSURE detection does NOT run in P1.
    """
    if friction_state is None:
        return 1

    current = int(friction_state.get("attempts_this_session", 0))
    new_val = current + 1
    friction_state["attempts_this_session"] = new_val
    return new_val


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _effective_severity(issues: list[IssueRecord]) -> Severity:
    """
    Return the maximum severity across all issues.

    Delegates to severity_overrides.effective_severity (P1.3 extraction, d10).
    """
    return _effective_severity_impl(issues)


def _is_ttl_exceeded(
    last_confirmed_str: str,
    ttl_days: int,
    now_ts: float,
) -> bool:
    """
    Return True if *last_confirmed_str* + *ttl_days* is in the past.

    Accepts ISO 8601 date strings (YYYY-MM-DD) or datetime strings.
    """
    # Try full datetime first, then date-only
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(last_confirmed_str, fmt)
            expiry = dt + datetime.timedelta(days=ttl_days)
            return expiry.timestamp() < now_ts
        except ValueError:
            continue

    return False


def _resolution_path(code: str) -> str:
    """Return a human-readable resolution suggestion for a given issue code."""
    _paths: dict[str, str] = {
        "coverage_gap": (
            "Add a profile pattern for this file in architecture/topology_v_next_binding.yaml, "
            "or declare the file in the orphaned list."
        ),
        "composition_conflict": (
            "Declare a cohort in the binding YAML that covers this intent + file set, "
            "or submit the files as separate, single-profile changes."
        ),
        "intent_enum_unknown": (
            "Add the intent value to intent_extensions in the binding YAML, "
            "or use one of the canonical Intent enum values."
        ),
        "intent_unspecified": (
            "Supply a typed intent value. Available: create_new, modify_existing, "
            "refactor, audit, hygiene, hotfix, rebase_keepup, other, and zeus.* extensions."
        ),
        "intent_extension_unregistered": (
            "Add the zeus.* intent to intent_extensions in the binding YAML "
            "(architecture/topology_v_next_binding.yaml), or use a canonical universal intent."
        ),
        "closed_packet_authority": (
            "Verify the artifact is still needed. If so, re-confirm authority status "
            "and update last_confirmed in the binding YAML."
        ),
        "authority_status_stale": (
            "Re-confirm the artifact authority status and update last_confirmed "
            "in architecture/topology_v_next_binding.yaml."
        ),
        "companion_missing": (
            "Add the missing companion file to the change set, or declare a cohort "
            "override in the binding YAML."
        ),
        "missing_companion": (
            "Add the named authority-doc path to the change set. The exact POSIX path "
            "is named in the MISSING_COMPANION message. Alternatively, if the profile "
            "declares a companion_skip_acknowledge_token, set the named env var to "
            "the declared value to skip with audit-trail logging."
        ),
        "companion_skip_token_used": (
            "The companion skip token was honored. A row has been appended to "
            "state/companion_skip_token_log.jsonl for human review in the next "
            "weekly digest. Ensure the authority doc is updated within 7 days."
        ),
        "hard_stop_path": (
            "Remove the protected path from the change set. "
            "Hard-stop paths require explicit governance override."
        ),
    }
    return _paths.get(code, "Consult SCAFFOLD §5.2 for resolution guidance.")


def _build_decision(
    *,
    ok: bool,
    profile_matched: str | None,
    intent_class: Intent,
    severity: Severity,
    issues: tuple[IssueRecord, ...],
    companion_files: tuple[str, ...],
    missing_phrases: tuple[str, ...],
    closest_rejected_profile: str | None,
    friction_budget_used: int,
    diagnosis: DiagnosisEntry | None,
    kernel_alerts: tuple[IssueRecord, ...],
) -> AdmissionDecision:
    """Construct and return the final AdmissionDecision."""
    return AdmissionDecision(
        ok=ok,
        profile_matched=profile_matched,
        intent_class=intent_class,
        severity=severity,
        issues=issues,
        companion_files=companion_files,
        missing_phrases=missing_phrases,
        closest_rejected_profile=closest_rejected_profile,
        friction_budget_used=friction_budget_used,
        diagnosis=diagnosis,
        kernel_alerts=kernel_alerts,
    )
