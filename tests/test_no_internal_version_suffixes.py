# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator-locked rename task Phase 4 (.omc/research/rename_inventory.md
#   PART 1 KEEP set + Part 3 Tier 1 "Ban _v[0-9] Internal Churn via Test").
"""Antibody: ban NEW internal-churn ``_v[0-9]`` suffixes on def/class names.

Fitz Constraint #3 (immune system, not security guard): the rename task dropped
13 internal-churn ``_v2`` symbols (Phase 3). This test makes the *category* of
"internal software-version suffix on a symbol name" unconstructable going
forward — a new ``def foo_v2`` / ``class BarV3`` in src/ or scripts/ fails CI
unless it is an explicitly-blessed external/schema/persisted-identity version.

Scope: ``src/`` and ``scripts/`` (production + operational code). Tests are NOT
scanned — test names legitimately mirror the table/fixture under test.

The ALLOWLIST is the set of ``_v[0-9]`` names that are NOT internal churn:
  * external protocol/API versions (Polymarket CLOB v2, the ``gh pr view`` CLI),
  * persisted-identity / hash-schema versions (renaming changes a wire/row hash
    contract — data provenance, Fitz #4),
  * helpers coupled to a live ``*_v2`` table whose consolidation is deferred
    (rename_inventory.md line 27: calibration_pairs_v2 / observation_instants_v2
    "RENAME when consolidation complete" — the table still exists, so the
    table-named helpers stay in lockstep with it until the table is dropped).

The 13 Phase-3 names (refit_v2, rebuild_v2, _read_v2_snapshot_metadata, …) are
deliberately ABSENT from the allowlist: if any reappears, this test fails.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("src", "scripts")

# def/class name containing an internal-version-looking suffix anywhere in the
# identifier (e.g. foo_v2, _build_v2_row, BarV3, project_accumulator_to_v2).
_DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_VERSION_TOKEN_RE = re.compile(r"_v[0-9]|[a-z]V[0-9]|_v[0-9]_")


# ---------------------------------------------------------------------------
# ALLOWLIST — names that may legitimately carry a _v[0-9] / V[0-9] token.
# Each entry is justified by category. Adding to this set is a deliberate act
# that a reviewer must approve; the default answer for a NEW name is "rename it".
# ---------------------------------------------------------------------------
ALLOWLIST: frozenset[str] = frozenset(
    {
        # --- External protocol / API version (Polymarket CLOB v2) ---
        "_ensure_v2_adapter",
        "_resolve_clob_v2_signature_type",
        "PolymarketV2AdapterProtocol",
        "PolymarketV2Adapter",
        # --- External CLI tool version (`gh pr view` v2 JSON surface) ---
        "gh_pr_view_v2",
        # --- Persisted-identity / hash-schema version: renaming alters a row /
        #     wire hash contract (Fitz #4 data provenance). The _v1 here is the
        #     versioned identity scheme, not internal churn. ---
        "apply_orderbook_event_v1",
        "decision_event_id_v1_hash",
        "decision_group_id_v1_hash",
        "nowcast_event_id_v1_hash",
        # --- Live-table-coupled, consolidation DEFERRED (rename_inventory.md
        #     line 27). These helpers are named after a *_v2 table that still
        #     exists; they must stay in lockstep with the table name until the
        #     table is consolidated/dropped. Renaming the helper without the
        #     table would split the name<->table coupling. ---
        # calibration_pairs_v2 introspection / rebuild path:
        "_v2_table_has_stratification",
        "_v2_pairs_table_has_stratification",
        "_v2_pairs_table_has_error_model_family",
        "_delete_canonical_v2_slice",
        "_dry_run_evaluate_snapshot_v2",
        "_fetch_eligible_snapshots_v2",
        "_pre_compute_snapshot_v2",
        "_print_rebuild_estimate_v2",
        "_process_snapshot_v2",
        "_write_snapshot_pairs_v2",
        # observation_instants_v2 ETL / migration helpers (the table whose
        # DST-correctness consolidation antibody is still live):
        "_accumulator_rows_missing_from_v2",
        "_build_v2_row",
        "project_accumulator_to_v2",
        "_hourly_obs_to_v2_row",
        "_migrate_market_events_v2",
        "_obs_instants_v2_extra_args",
        "_obs_v2_provenance_identity_missing_sql",
        "_ens_backfill_v2_extra_args",
        "_run_fit_ens_bias_v2",
        # no_trade_events rebuild migration (phase3_t2) v2-table DDL builder:
        "_build_create_v2_sql",
        # observation_instants_v2 row types (file consolidated 2026-05-29 from
        # observation_instants_v2_writer; the v2 suffix stays in lockstep with
        # the still-live observation_instants_v2 table — same deferral as above):
        "ObsV2Row",
        "InvalidObsV2RowError",
        # --- Internal-churn stats dataclasses NOT in the operator's Phase-3
        #     18-symbol scope. They are the return types of the (already
        #     suffix-dropped) refit()/rebuild() scripts and are pinned
        #     by name in phase{1,5} test fixtures. Candidate for a future
        #     cleanup pass; allowlisted now to keep this antibody truthful
        #     (they are NOT external versions) without exceeding the locked
        #     Phase-3 scope. BackfillStatsV2 is now BackfillStats (dropped here). ---
        "RefitStatsV2",
        "RebuildStatsV2",
    }
)

# Names that MUST stay banned — if any of these reappears as a def/class in
# src/ or scripts/, the rename has regressed. (Phase-3 dropped-suffix set.)
BANNED_REGRESSION_GUARD: frozenset[str] = frozenset(
    {
        "refit_v2",
        "refit_all_v2",
        "rebuild_v2",
        "rebuild_all_v2",
        "_read_v2_snapshot_metadata",
        "_get_v2_row_counts",
        "read_day0_observed_extrema_v2",
        "_ensure_v2_forecast_indexes",
        "_calibration_bin_source_v2_fit_enabled",
        "_emit_v2_tigge_fallback_rescue_warning",
        "_emit_v2_legacy_fallback_warning",
        "_parse_v2_model_key_domain",
        "as_v2_outcome_row",
    }
)


def _iter_def_class_names() -> list[tuple[str, str, int]]:
    """Yield (name, relpath, lineno) for every def/class in the scan dirs."""
    out: list[tuple[str, str, int]] = []
    for d in _SCAN_DIRS:
        base = _REPO_ROOT / d
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                m = _DEF_RE.match(line)
                if not m:
                    continue
                out.append((m.group(1), str(py.relative_to(_REPO_ROOT)), lineno))
    return out


def test_no_internal_version_suffix_in_symbols() -> None:
    """Fail on any def/class name with an internal _v[0-9] suffix not allowlisted."""
    offenders: list[str] = []
    for name, rel, lineno in _iter_def_class_names():
        if not _VERSION_TOKEN_RE.search(name):
            continue
        if name in ALLOWLIST:
            continue
        offenders.append(f"{rel}:{lineno}  {name}")

    assert not offenders, (
        "Internal _v[0-9] version-suffix symbol(s) found in src/ or scripts/.\n"
        "Drop the suffix (the canonical version IS the base name), or — only if "
        "this is a genuine external/protocol/schema/persisted-identity version — "
        "add the name to ALLOWLIST in this test with a one-line rationale.\n"
        "Offenders:\n  " + "\n  ".join(sorted(offenders))
    )


def test_phase3_dropped_suffixes_stay_dropped() -> None:
    """Regression guard: none of the 13 Phase-3 renamed symbols may reappear."""
    present = {name for name, _, _ in _iter_def_class_names()}
    regressed = sorted(BANNED_REGRESSION_GUARD & present)
    assert not regressed, (
        "Phase-3 _v2 suffixes were re-introduced as def/class names: "
        f"{regressed}. These were renamed (drop _v2); do not bring them back."
    )


def test_allowlist_has_no_dead_entries() -> None:
    """Hygiene: every ALLOWLIST entry must still exist (no rotting exemptions)."""
    present = {name for name, _, _ in _iter_def_class_names()}
    dead = sorted(n for n in ALLOWLIST if n not in present)
    assert not dead, (
        "ALLOWLIST contains names no longer defined in src/ or scripts/; "
        f"remove these stale exemptions: {dead}"
    )
