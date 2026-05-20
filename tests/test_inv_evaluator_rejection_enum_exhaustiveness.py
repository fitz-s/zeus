# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Antibody — enforces NoTradeReason enum exhaustiveness and zero raw-string callsites post-migration.
# Reuse: Verify MIGRATION_TABLE coverage, INV-A raw-string guard, INV-B/C enum validity.
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2, §5.3 (sha 00c2399742)

"""Antibody tests: INV-evaluator-rejection-enum-exhaustiveness

Three tests, two RED/xfail antibodies and one structural guard.

INV-A (RED — fails until production pass):
  After T2 production migration, every rejection_reasons=[...] callsite in
  evaluator.py must contain only NoTradeReason.X.value attribute references
  (no raw string literals). This test AST-scans the actual callsites and
  asserts zero raw string literals remain. RED now (69 raw strings exist);
  turns GREEN after 69-callsite migration in PR-T2.

INV-B (SCAFFOLD xfail — turns strict after production):
  Every NoTradeReason member appears in the migration table. No orphan members.
  Fires as xfail until production pass confirms full coverage.

INV-C (structural guard — GREEN now):
  All canonical_member_value entries in MIGRATION_TABLE must be valid
  NoTradeReason.value strings (typo guard). Must be GREEN before any
  production work begins.

Cross-module relationship test (Fitz §3 invariant pattern):
  src/engine/evaluator.py (callsite strings) <-> src/contracts/no_trade_reason.py (enum)
"""

from __future__ import annotations

import ast
import pathlib
from typing import FrozenSet

import pytest

# ---------------------------------------------------------------------------
# Migration plan: 69 callsites -> canonical NoTradeReason member value
# Source: T2_NO_TRADE_EVENTS_SCAFFOLD.md §3
# All lines verified against git show origin/main:src/engine/evaluator.py
# StrEnum uses auto() so values are lowercased member names.
# ---------------------------------------------------------------------------
MIGRATION_TABLE: list[tuple[int, str, str]] = [
    (1888, "Day0 observation unavailable", "day0_observation_unavailable"),
    (1901, "[source_rejection_reason]", "observation_source_unauthorized"),
    (1921, "[observation_quality_rejection]", "observation_quality_rejected"),
    (1935, "[live_entry_forecast_blocker]", "entry_forecast_rollout_blocked"),
    (1960, "invalid support_index for", "invalid_support_index"),
    (1967, "support_index mismatch for", "support_index_mismatch"),
    (2001, "< 3 parseable bins", "insufficient_bins"),
    (2011, "bin topology:", "bin_topology_invalid"),
    (2020, "support topology has no executable bins", "no_executable_bins"),
    (2095, "ENTRY_FORECAST_READER_DB_UNAVAILABLE", "entry_forecast_reader_db_unavailable"),
    (2125, "[reader_result.reason_code]", "entry_forecast_reader_rejected"),
    (2144, "[str(e)] SourceNotEnabled", "ens_source_not_enabled"),
    (2152, "[str(e)] generic Exception", "ens_fetch_failed"),
    (2160, "ENS fetch failed or < 51 members", "ens_fetch_insufficient_members"),
    (2170, "ENS fetch failed or < 51 members", "ens_fetch_insufficient_members"),
    (2178, "DEGRADED_FORECAST_FALLBACK", "forecast_source_degraded"),
    (2191, "Forecast source evidence incomplete", "forecast_evidence_incomplete"),
    (2207, "[str(e)] KeyError/TypeError ens_times", "ens_times_parse_error"),
    (2227, "Solar/DST context unavailable for Day0", "solar_dst_context_unavailable"),
    (2242, "No Day0 forecast hours remain for target date", "day0_no_forecast_hours_remain"),
    (2253, "ENS fetch failed, < 51 members, or insufficient finite required-hour members", "ens_insufficient_required_hour_members"),
    (2277, "[str(e)] ValueError EnsembleSignal ctor", "ens_signal_construction_failed"),
    (2300, "No Day0 forecast hours remain for target date", "day0_no_forecast_hours_remain"),
    (2309, "Day0 low observation unavailable", "day0_low_observation_unavailable"),
    (2340, "Day0 low slot rejected: causality_status=", "day0_low_causality_rejected"),
    (2361, "Day0 current observation became unavailable before signal routing", "day0_current_obs_unavailable"),
    (2451, "Day0 forecast has insufficient finite remaining ensemble members", "day0_forecast_insufficient_members"),
    (2470, "EXECUTABLE_FORECAST_MEMBERS_UNIT_MISMATCH", "executable_forecast_members_unit_mismatch"),
    (2484, "EXECUTABLE_FORECAST_MEMBER_EXTREMA_INVALID", "executable_forecast_member_extrema_invalid"),
    (2519, "P_raw is non-finite", "p_raw_invalid"),
    (2536, "ENS snapshot persistence failed: decision_snapshot_id unavailable", "ens_snapshot_persistence_failed"),
    (2555, "DT7_boundary_day_ambiguous", "dt7_boundary_day_ambiguous"),
    (2581, "ENS snapshot p_raw persistence failed: canonical p_raw unavailable", "ens_snapshot_p_raw_persistence_failed"),
    (2623, "authority gate failed due to DB query fault", "authority_gate_db_fault"),
    (2634, "insufficient_verified_calibration:", "insufficient_verified_calibration"),
    (2684, "does not resolve to a registered source_family", "unknown_forecast_source_family"),
    (2715, "was present without source_id", "forecast_provenance_incomplete"),
    (2733, "disagrees with data_version=", "forecast_provenance_inconsistent"),
    (2764, "has no registered calibration bucket source_id", "unsupported_calibration_source_id"),
    (2811, "P_cal is non-finite", "p_cal_invalid"),
    (2827, "invalid calibration maturity level", "calibration_maturity_invalid"),
    (2845, "calibration_level=4 has no Platt model", "calibration_immature_no_platt"),
    (2871, "[str(exc)] ValueError native_multibin_buy_no", "native_multibin_buy_no_flag_invalid"),
    (2950, "[str(e)] EmptyOrderbookError", "market_empty_orderbook"),
    (2963, "[str(e)] generic exception clob loop", "market_liquidity_error"),
    (2989, "crosscheck unavailable:", "crosscheck_unavailable"),
    (3007, "crosscheck unavailable", "crosscheck_unavailable"),
    (3032, "GFS crosscheck unavailable", "gfs_crosscheck_unavailable"),
    (3067, "crosscheck unavailable:", "crosscheck_unavailable"),
    (3083, "CONFLICT", "model_conflict"),
    (3111, "ALPHA_TARGET_MISMATCH:", "alpha_target_mismatch"),
    (3126, "AUTHORITY_VIOLATION:", "authority_violation"),
    (3385, "selected edge is missing canonical support_index", "selected_edge_missing_support_index"),
    (3398, "has no executable token payload", "selected_edge_no_token_payload"),
    (3413, "strategy_key_unclassified", "strategy_key_unclassified"),
    (3427, "[ci_rejection_reason]", "confidence_band_insufficient"),
    (3463, "[ultra_low_price_reason]", "center_buy_ultra_low_price"),
    (3479, "REENTRY_BLOCKED", "reentry_blocked"),
    (3494, "TOKEN_COOLDOWN", "token_cooldown"),
    (3511, "ALREADY_HELD_SAME_TOKEN", "already_held_same_token"),
    (3529, "oracle_error_rate=", "oracle_blacklisted"),
    (3578, "[str(exc)] DDDFailClosed", "ddd_fail_closed"),
    (3593, "DDD Rail 1 HALT:", "ddd_rail1_halt"),
    (3660, "[str(exc)] ValueError dynamic_kelly_mult", "kelly_sizing_error"),
    (3677, "[reason] POLICY_GATED/POLICY_EXIT_ONLY", "policy_gated"),
    (3772, "[str(exc)] FeeRateUnavailableError", "execution_price_fee_rate_unavailable"),
    (3804, "[str(exc)] ValueError _size_at_execution_price_boundary", "execution_price_sizing_error"),
    (3822, "< $", "size_below_minimum"),
    (3850, "[reason] check_position_allowed", "risk_limits_exceeded"),
]

assert len(MIGRATION_TABLE) == 69, f"Migration table must have 69 rows, got {len(MIGRATION_TABLE)}"


def _get_evaluator_path() -> pathlib.Path:
    here = pathlib.Path(__file__).parent
    repo_root = here.parent
    candidate = repo_root / "src" / "engine" / "evaluator.py"
    if not candidate.exists():
        raise FileNotFoundError(f"evaluator.py not found at {candidate}")
    return candidate


def _scan_rejection_callsites(source: str) -> list[dict]:
    """AST-scan evaluator.py; return one dict per rejection_reasons=[...] kwarg.

    Returns list of dicts with keys:
      lineno: int
      has_non_enum: bool   -- True if any element is a raw string, f-string, or
                              str() call rather than a NoTradeReason.X.value ref.
                              Post-migration: must be False for all callsites.
      elements: list[str]  -- string representation of each list element
    """
    tree = ast.parse(source)
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "rejection_reasons" and isinstance(kw.value, ast.List):
                    elts = kw.value.elts
                    # Non-enum elements: raw string literals or f-strings.
                    # Post-migration: every element should be a Name/Attribute ref
                    # (NoTradeReason.X.value) or a Call (str(exc)) wrapping a dynamic.
                    # We flag str constants and JoinedStr (f-strings) as non-enum.
                    has_non_enum = any(
                        isinstance(e, (ast.Constant, ast.JoinedStr))
                        and not (isinstance(e, ast.Constant) and not isinstance(e.value, str))
                        for e in elts
                    )
                    element_reprs = [ast.dump(e) for e in elts]
                    results.append({
                        "lineno": node.lineno,
                        "has_non_enum": has_non_enum,
                        "elements": element_reprs,
                    })
    return results


def test_inv_evaluator_no_raw_string_literals_post_migration() -> None:
    """INV-A (RED): after T2 production migration, zero rejection_reasons=[...]
    callsites may contain raw string literals or f-strings.

    Post-migration contract: every callsite element must be a Name/Attribute
    reference (NoTradeReason.X.value) or a str() / dynamic Call. Neither
    string Constant nor JoinedStr (f-string) may remain.

    This test intentionally fails RED at SCAFFOLD time (49 non-enum sites:
    27 raw strings + 22 f-strings). Turns GREEN after PR-T2 migration.

    This is a stage-1 antibody (Fitz immune-system methodology):
    Do NOT add xfail -- RED is the desired state pre-migration.
    """
    evaluator_path = _get_evaluator_path()
    source = evaluator_path.read_text(encoding="utf-8")
    callsites = _scan_rejection_callsites(source)

    non_enum_sites = [
        (cs["lineno"], cs["elements"])
        for cs in callsites
        if cs["has_non_enum"]
    ]

    assert not non_enum_sites, (
        f"INV-A: {len(non_enum_sites)} rejection_reasons=[...] callsites still contain "
        f"raw string literals or f-strings (expected 0 post-migration).\n"
        f"First 5 offenders:\n"
        + "\n".join(
            f"  line {lineno}: {elts[:3]}"
            for lineno, elts in non_enum_sites[:5]
        )
        + "\n\nRun PR-T2 callsite migration to replace with NoTradeReason.X.value refs."
    )


def test_inv_evaluator_callsite_count() -> None:
    """Structural: AST-counted rejection_reasons=[...] callsites == 69.

    Fails if callsites are added or removed without updating the migration plan.
    No xfail -- count is a structural invariant.
    """
    evaluator_path = _get_evaluator_path()
    source = evaluator_path.read_text(encoding="utf-8")
    callsites = _scan_rejection_callsites(source)

    assert len(callsites) == len(MIGRATION_TABLE), (
        f"evaluator.py has {len(callsites)} rejection_reasons=[...] callsites; "
        f"migration table has {len(MIGRATION_TABLE)} rows -- must match"
    )


def test_inv_no_orphan_enum_members() -> None:
    """INV-B: every NoTradeReason member must appear in the migration table.

    Orphan members = enum values with no callsite plan = incomplete migration.
    UNCATEGORIZED is exempt (it is the section 13 fallback, not a migration target).
    xfail until T2 production pass verifies full coverage.
    """
    from src.contracts.no_trade_reason import NoTradeReason

    planned_members: FrozenSet[str] = frozenset(
        row[2] for row in MIGRATION_TABLE
    )
    # UNCATEGORIZED is intentionally exempt (section 13 fallback)
    all_members: FrozenSet[str] = frozenset(
        r.value for r in NoTradeReason if r != NoTradeReason.UNCATEGORIZED
    )

    orphan_members = all_members - planned_members
    assert not orphan_members, (
        f"INV-B: orphan NoTradeReason members (no callsite in migration plan): "
        f"{sorted(orphan_members)}"
    )


def test_inv_migration_table_member_values_valid() -> None:
    """INV-C (GREEN now): all canonical_member_value entries in MIGRATION_TABLE
    must be valid NoTradeReason.value strings (typo guard).

    No xfail -- this must pass before production work begins.
    """
    from src.contracts.no_trade_reason import NoTradeReason

    valid_values = {r.value for r in NoTradeReason}
    invalid_entries = [
        (line, raw, val)
        for line, raw, val in MIGRATION_TABLE
        if val not in valid_values
    ]
    assert not invalid_entries, (
        "MIGRATION_TABLE entries with invalid NoTradeReason values:\n"
        + "\n".join(f"  line {l}: {raw!r} -> {val!r}" for l, raw, val in invalid_entries)
    )
