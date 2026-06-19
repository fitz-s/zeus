# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K2.1
"""K2.1 antibody: every rejection-reason emit site uses a registered base.

The disease: no_trade_regret_events.rejection_reason was free text — 3042
distinct strings in the live table, including raw exception text ("UNIQUE
constraint failed: ...", "database is locked"). Funnel analysis was substring
prose-matching. These tests make the free-text category die in CI:

1. AST scan: every EventSubmissionReceipt(reason=<string literal>) emit site in
   src/ has a base registered in src/contracts/rejection_reasons.py.
2. Registry hygiene: member values are single-token bases (no ':' detail, no
   spaces/prose), each with a category and docstring.
3. classify() golden cases incl. the unregistered->ARTIFICIAL_SUSPECT default
   (raw exception text classifies as suspect, never as honest).
"""

import ast
import pathlib

import pytest

from src.contracts.rejection_reasons import (
    RejectionCategory,
    RejectionReason,
    base_reason,
    classify_rejection_reason,
    is_registered_rejection_reason,
    lookup_rejection_reason,
)


def _receipt_reason_literal_sites() -> list[tuple[str, str]]:
    """(site, literal-base) for every EventSubmissionReceipt(reason=...) string
    literal (also covers 'BASE:' + dynamic and f'BASE:{...}' prefixes)."""
    sites: list[tuple[str, str]] = []
    for path in pathlib.Path("src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None
            )
            if name != "EventSubmissionReceipt":
                continue
            for kw in node.keywords or []:
                if kw.arg != "reason":
                    continue
                v = kw.value
                lit = None
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    lit = v.value
                elif (
                    isinstance(v, ast.BinOp)
                    and isinstance(v.left, ast.Constant)
                    and isinstance(v.left.value, str)
                ):
                    lit = v.left.value
                elif (
                    isinstance(v, ast.JoinedStr)
                    and v.values
                    and isinstance(v.values[0], ast.Constant)
                ):
                    lit = str(v.values[0].value)
                if lit is not None:
                    sites.append((f"{path}:{node.lineno}", base_reason(lit)))
    return sites


def test_every_receipt_reason_literal_base_is_registered():
    sites = _receipt_reason_literal_sites()
    assert len(sites) >= 25, (
        f"AST scan found only {len(sites)} EventSubmissionReceipt reason literals — "
        "the scan itself regressed (emit sites moved?); fix the scanner before "
        "trusting this antibody"
    )
    unregistered = [
        (site, base) for site, base in sites if not is_registered_rejection_reason(base)
    ]
    assert not unregistered, (
        "Emit sites using rejection-reason bases NOT in the typed registry "
        "(src/contracts/rejection_reasons.py) — declare the member with a category "
        f"and docstring before emitting it: {unregistered}"
    )


def test_registry_member_values_are_single_token_bases():
    for member in RejectionReason:
        assert ":" not in member.value, f"{member.name} value carries detail: {member.value!r}"
        assert " " not in member.value, f"{member.name} value is prose: {member.value!r}"
        assert member.category in RejectionCategory
        assert member.__doc__ and len(member.__doc__) > 10, f"{member.name} missing docstring"


def test_registry_lookup_strips_detail_suffix():
    raw = "EVENT_BOUND_MARKET_PHASE_CLOSED:settlement_day:verified_gamma"
    assert lookup_rejection_reason(raw) is RejectionReason.EVENT_BOUND_MARKET_PHASE_CLOSED
    assert classify_rejection_reason(raw) is RejectionCategory.HONEST_MARKET


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("RISK_GUARD_BLOCKED", RejectionCategory.DESIGNED_GATE),
        ("TRADE_SCORE_NON_POSITIVE", RejectionCategory.HONEST_MARKET),
        ("LIVE_INFERENCE_INPUTS_MISSING:READINESS_EXPIRED", RejectionCategory.HONEST_DATA),
        ("DAY0_SCOPE_SHADOW_ONLY", RejectionCategory.DESIGNED_GATE),
        ("entry_cooldown:same_token_entry_cooling_down", RejectionCategory.DESIGNED_GATE),
        ("EDLI_LIVE_CERTIFICATE_BUILD_FAILED:cost_basis_hash missing", RejectionCategory.ARTIFICIAL_SUSPECT),
        # The exception-leak class: NEVER classifies as honest.
        ("UNIQUE constraint failed: platt_models.x", RejectionCategory.ARTIFICIAL_SUSPECT),
        ("database is locked", RejectionCategory.ARTIFICIAL_SUSPECT),
        ("name '_snapshot_rows' is not defined", RejectionCategory.ARTIFICIAL_SUSPECT),
        ("SOME_FUTURE_UNDECLARED_REASON", RejectionCategory.ARTIFICIAL_SUSPECT),
    ],
)
def test_classification_golden_cases(raw, expected):
    assert classify_rejection_reason(raw) is expected


def test_known_db_history_bases_are_registered():
    """Every legitimate SCREAMING_SNAKE base observed in the live table (audit
    2026-06-10, 245k rows) is registered. Exception leaks intentionally NOT."""
    observed_legit = [
        "EVENT_BOUND_MARKET_PHASE_CLOSED",
        "RISK_GUARD_BLOCKED",
        "TRADE_SCORE_NON_POSITIVE",
        "LIVE_INFERENCE_INPUTS_MISSING",
        "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE",
        "KELLY_REJECTED",
        "EXECUTABLE_SNAPSHOT_BLOCKED",
        "EXECUTABLE_SNAPSHOT_STALE",
        "EVENT_BOUND_SELECTED_CANDIDATE_MISSING",
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED",
        "CALIBRATION_AUTHORITY_EVIDENCE_MISSING",
        "FDR_REJECTED",
        "FSR_SOURCE_RUN_NOT_COMPLETE",
        "SOURCE_TRUTH_BLOCKED",
        "EXECUTABLE_NATIVE_ASK_MISSING",
        "FSR_WINDOW_AUTHORITY_NOT_LIVE_ELIGIBLE",
        "DAY0_SCOPE_SHADOW_ONLY",
        "entry_cooldown",
        "REPLACEMENT_FORECAST_HOOK_BLOCKED",
        "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED",
        "SUBMIT_ABORTED_EDGE_REVERSED",
        "KELLY_PROOF_MISSING",
        "NO_SUBMIT_CERTIFICATE_REJECTED",
        "EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED",
        "REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING",
        "SUBMIT_ABORTED_FAMILY_REVERSED",
        "SUBMIT_ABORTED_PRICE_MOVED",
        "SUBMIT_ABORTED_BELOW_MIN_ORDER",
        "SUBMIT_ABORTED_MODE_FLIPPED",
        "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING",
        "TOPOLOGY_CLOCK_MISSING",
        "EVENT_BOUND_MARKET_TOPOLOGY_INVALID",
        "DAY0_HARD_FACT_AUTHORITY_BLOCKED",
        "UNKNOWN_REVIEW_REQUIRED",
    ]
    missing = [b for b in observed_legit if not is_registered_rejection_reason(b)]
    assert not missing, f"DB-history bases missing from registry: {missing}"
