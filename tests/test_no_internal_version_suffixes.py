# Created: 2026-06-09
# Last reused or audited: 2026-06-10
# Authority basis: operator-locked version-drop task (PR #405, branch
#   rename/u0r-bayes-precision-fusion). "LAST version-drop task: eliminate ALL
#   internal _v[0-9] suffixes and make recurrence IMPOSSIBLE."
r"""Antibody (ratchet): ban NEW internal ``_v[0-9]`` version suffixes on EVERY
surface, not just ``def``/``class`` symbol names.

Fitz Constraint #3 (immune system, not security guard) + #1 (structural decision,
not N patches): the version-drop task is collapsing every internal software-version
suffix to its bare canonical name. This test makes the *category* "internal
``_v[0-9]`` token" unconstructable going forward. A new ``_v2`` anything — symbol,
filename, ``CREATE TABLE``, config key, or identity string — in ``src/``,
``scripts/``, or ``config/`` fails CI unless it is either:

  * ALLOWLIST  — a genuine EXTERNAL protocol / API / CLI version (Polymarket CLOB
    v2, UMA Optimistic-Oracle v2, the ``py_clob_client_v2`` package, the
    ``gh pr view`` v2 JSON surface). These are NOT internal churn; the ``v2`` is
    someone else's wire contract and must not be renamed.

  * GRANDFATHER — a CURRENT internal ``_v[0-9]`` occurrence that THIS PR is not
    yet migrating (the C-dead table-name refs that survive in frozen one-shot
    scripts, and the whole D-category provenance-identity surface whose rename is
    a paused-daemon data-retag, not a code edit). This frozenset MUST SHRINK TO
    ZERO — operator-gated migration in progress 2026-06-10. As each migration
    lands, its token disappears from the code AND from this set; the
    ``no_dead_grandfather_entries`` hygiene test forces the shrink (a grandfather
    entry that no longer appears in the tree is a hard failure).

The ratchet: ``offenders = found_tokens - ALLOWLIST - GRANDFATHER``. The test is
GREEN today (every token is allowlisted or grandfathered) and RED the instant a
NEW ``_v[0-9]`` token appears anywhere in the scanned tree. New ones fail loudly;
grandfathered ones are tracked and decrement to zero; allowlisted ones are the
small, reviewer-blessed external set.

Surfaces covered (all unified under one content+filename token scan):
  1. Python symbols:    ``(def|class) \w*_v[0-9]``
  2. Filenames:         any tracked ``src/`` ``scripts/`` file with ``_v[0-9]``
  3. Table DDL:         ``CREATE TABLE ... \w*_v[0-9]``
  4. Config keys:       ``"\w*_v[0-9]" :`` in ``config/*.json``
  5. Identity literals: ``"\w*_v[0-9]\w*"`` in ``src/`` ``scripts/``
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("src", "scripts", "config")

# A token that LOOKS like an internal version suffix: an identifier-ish run that
# contains ``_v<digit>`` somewhere (foo_v2, _build_v2_row, idx_calibration_pairs_v2,
# obs_v2_dst_gap_fill_ogimet_v2, ...). This single regex catches all five surfaces
# because every surface is, at bottom, this token embedded in text (or a filename).
_VERSION_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*_v[0-9][A-Za-z0-9_]*")

# ---------------------------------------------------------------------------
# ALLOWLIST — genuine EXTERNAL protocol / API / package / CLI versions.
# The ``v2`` belongs to someone else's wire contract; renaming it would break
# interop. Adding to this set is a deliberate, reviewer-approved act. The default
# answer for a NEW token is "drop the suffix", NOT "allowlist it".
# ---------------------------------------------------------------------------
ALLOWLIST: frozenset[str] = frozenset(
    {
        # Polymarket CLOB v2 adapter surface (external order-book protocol).
        "polymarket_v2",
        "polymarket_v2_adapter",
        "polymarket_v2_preflight",
        "py_clob_client_v2",            # external pip package name
        "_ensure_v2_adapter",
        "_resolve_clob_v2_signature_type",
        # Polymarket UMA Optimistic-Oracle v2 (external settlement oracle protocol).
        "uma_oo_v2",
        "polymarket_uma_oo_v2",
        "pre_cutover_uma_oo_v2",        # era label for the external-oracle cutover
        # External CLI tool version (`gh pr view` v2 JSON surface).
        "gh_pr_view_v2",
    }
)

# ---------------------------------------------------------------------------
# GRANDFATHER — MUST SHRINK TO ZERO — operator-gated migration in progress
# 2026-06-10. Every entry is a CURRENT internal ``_v[0-9]`` token that this PR is
# NOT yet migrating:
#   * C-dead: table-name refs (settlements_v2, calibration_pairs_v2,
#     ensemble_snapshots_v2, market_events_v2, platt_models_v2,
#     observation_instants_v2, no_trade_events_v2, position_events_v3, their
#     idx_* indexes and *_distribution / *_empty derivatives) that survive ONLY
#     inside frozen one-shot backfill/audit/migration scripts and rename-history
#     comments — the live tables were already dropped/renamed to their bare names.
#   * D-category: provenance / hash / config-flag identities (full_transport_v1,
#     openmeteo_*_v1, ecmwf_*_v1, edli_*_v1, deid_v1_/dgid_v1_/nei_v1_ hash
#     prefixes, *_event_id_v1_hash, ddd_v2_*,
#     calibration_bin_source_v2_fit_enabled, canonical_v1/canonical_v2, ...) whose
#     value is STORED IN DATA (decision rows, provenance_json, config). Renaming
#     these is a data-retag on a paused-daemon window (Fitz #4), not a code edit,
#     so they are inventoried in .omc/research/version_suffix_migration_plan.md
#     and deferred — NOT touched by this PR.
# As migrations land, delete the migrated token here. Do NOT add new tokens; a new
# token must instead be born WITHOUT a ``_v[0-9]`` suffix (that is the whole point).
# ---------------------------------------------------------------------------
GRANDFATHER: frozenset[str] = frozenset(
    {
        # --- Carried in from base (fix/opportunity-book-selector) at the PR#405 merge
        #     2026-06-12. These internal _v[0-9] tokens were introduced by base commits
        #     AFTER this PR was cut; they are NOT this rename's churn and are the SAME
        #     two deferred categories the rest of GRANDFATHER documents:
        #       * C-dead table-name refs in frozen one-shot drop/audit scripts
        #         (scripts/task_2026-06-09_drop_dead_tables.py) — the live tables are gone.
        #       * D-category provenance / hash / authority identities stored in DATA
        #         (d0hv_req_v1 request-hash prefix, fused_bootstrap_settlement_coverage_v1
        #         hash prefix, settlement_sigma_floor_v2_residual authority string) — a
        #         paused-daemon data-retag, not a code edit. Must shrink to zero via the
        #         operator-gated migration, never grow.
        "calibration_pairs_v2_archived_2026_05_11",
        "d0hv_req_v1",
        "ensemble_snapshots_v2_archived_2026_05_11",
        "fused_bootstrap_settlement_coverage_v1",
        "historical_forecasts_v2",
        "market_events_v2_archived_2026_05_11",
        "rescue_events_v2",
        "settlement_sigma_floor_v2_residual",
        "settlements_v2_archived_2026_05_11",
        # --- end PR#405 merge carry-in ---
        "C_canonical_v1",
        "F_canonical_v1",
        "OOS_BRIER_DIFF_v1",
        "PLAN_v3",
        "PLAN_v4",
        "RERUN_PLAN_v2",
        "TIGGE_DOWNLOAD_SPEC_v3",
        "TIGGE_DOWNLOAD_SPEC_v3_2026_05_07",
        "_accumulator_rows_missing_from_v2",
        "_backfill_settlements_v2",
        "_bin_source_v2",
        "_build_create_v2_sql",
        "_build_v2_row",
        "_calibration_bin_source_v2_fit_enabled_note",
        "_check_calibration_pairs_v2_metric_",
        "_consolidate_observation_instants_v2",
        "_contract_window_v2",
        "_ddd_v2_enabled_note",
        "_delete_canonical_v2_slice",
        "_dry_run_evaluate_snapshot_v2",
        "_ens_backfill_v2_extra_args",
        "_fetch_eligible_snapshots_v2",
        "_hourly_obs_to_v2_row",
        "_k2_obs_v2_tick",
        "_local_calendar_day_max_v1",
        "_local_calendar_day_min_v1",
        "_migrate_market_events_v2",
        "_min_contract_window_v2",
        "_obs_instants_v2_extra_args",
        "_obs_v2_provenance_identity_missing_sql",
        "_polymarket_clob_v2_migration",
        "_pre_compute_snapshot_v2",
        "_print_rebuild_estimate_v2",
        "_process_snapshot_v2",
        "_run_fit_ens_bias_v2",
        "_settlements_v2_identity_incomplete",
        "_snapshot_v2_table",
        "_write_snapshot_pairs_v2",
        "add_calibration_pair_v2",
        "after_v2",
        "already_in_v2",
        "audit_observation_instants_v2",
        "backfill_manifest_obs_v2",
        "backfill_v1",
        "build_obs_v2_row_kwargs",
        "cal_v2_metric_violations",
        "calibration_bin_source_v2_fit_enabled",
        "calibration_pairs_v2",
        "calibration_pairs_v2_rebuild_complete",
        "calibration_v2_tigge_fallback_rescue",
        "canonical_bin_grid_v1",
        "canonical_observation_instants_v2",
        "canonical_snapshot_v2",
        "canonical_v1",
        "canonical_v2",
        "contract_window_v2",
        "corrected_executable_cost_v1",
        "cp_v2_table",
        "create_v2_sql",
        "cwa_no_collector_v0",
        "ddd_v2_enabled",
        "ddd_v2_fail_closed",
        "ddd_v2_halt",
        "deactivate_model_v2",
        "decision_event_id_v1_hash",
        "decision_group_id_v1_hash",
        "deid_v1_",
        "deid_v1_3f8a2b1c4e5d6789",
        "deid_v1_BACKSTOP_NULL_WRITER_BYPASS",
        "dgid_v1_",
        "dgid_v1_3f8a2b1c4e5d6789",
        "dgid_v2_",
        "ecmwf_aifs_ens_sampled_2t_6h_v1",
        "ecmwf_ens_v2",
        "ecmwf_open_data_uses_tigge_localday_cal_v1",
        "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
        "ecmwf_opendata_mn2t6_local_calendar_day_min_v1",
        "ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        "edli_arm_gate_v1",
        "edli_event_bound_no_submit_v1",
        "edli_live_decision_audit_v1",
        "edli_live_promotion_v1",
        "edli_per_city_v1",
        "edli_reactor_v1",
        "edli_v1",
        "emos_ngr_v1",
        "empirical_p05_from_current_obs_v2_2026",
        "empty_v2",
        "empty_v2_table",
        "ens_backfill_v2",
        "ens_error_model_v1",
        "ensemble_snapshots_v2",
        "event_bound_no_submit_v1",
        "exchange_v2",
        "existing_executor_passive_limit_v1",
        "fit_ens_bias_v2",
        "ft_v1",
        "full_transport_v1",
        "full_transport_v1_rows",
        "full_transport_v2",
        "gamma_source_contract_v1",
        "grid_point_representativeness_offset_v1",
        "grid_point_representativeness_recency_v2",
        "hko_daily_api_v1",
        "hko_daily_backfill_v2",
        "hko_hourly_accumulator_projection_v2",
        "hko_opendata_v1_2026",
        "hko_rhrread_accumulated_v1",
        "hko_xml_backfill_v1",
        "hko_xml_v1_2026",
        "hpf_v1",
        "hpf_v1_identity_conservative_v1",
        "identity_fallback_no_platt_bucket_v1",
        "identity_full_transport_v1",
        "identity_missing_platt_bucket_v1",
        "idx_calibration_pairs_v2_bucket",
        "idx_calibration_pairs_v2_city_date_metric",
        "idx_calibration_pairs_v2_refit_core",
        "idx_ens_v2_entry_lookup",
        "idx_ens_v2_source_run",
        "idx_observation_instants_v2_city_ts",
        "idx_observation_revisions_obs_v2_lookup",
        "include_v1",
        "ingest_k2_obs_v2_tick",
        "l2_settlement_attribution_v1",
        "l2_v1",
        "legacy_v0",
        "legacy_v1",
        "legacy_vwmp_prior_v0",
        "live_v1",
        "market_events_v2",
        "model_bias_ens_v2",
        "model_domain_polygons_v1",
        "model_only_v1",
        "mx2t6_local_calendar_day_max_v1",
        "neg_risk_basket_v1",
        "neg_risk_exchange_v2",
        "nei_v1_",
        "nei_v1_BACKSTOP_NULL_WRITER_BYPASS",
        "nei_v1_a3b2c1d4e5f60718",
        "no_trade_events_v2",
        "now_v2",
        "nowcast_event_id_v1_hash",
        "obs_instants_v2",
        "obs_v2",
        "obs_v2_",
        "obs_v2_backfill_hourly_extremum_v2",
        "obs_v2_backfill_log",
        "obs_v2_consolidation",
        "obs_v2_dst_fill_log",
        "obs_v2_dst_gap_fill_ogimet_v2",
        "obs_v2_dst_gap_hour_bucket_source_identity",
        "obs_v2_hour_bucket_source_identity",
        "obs_v2_live_tick",
        "obs_v2_live_tick_log",
        "obs_v2_live_tick_v1",
        "obs_v2_metric_layer_decision",
        "obs_v2_row",
        "observation_hourly_extrema_v2",
        "observation_instant_v2",
        "observation_instants_v2",
        "observation_instants_v2_writer",
        "ogimet_live_v1",
        "ogimet_metar_v1",
        "ogimet_v1_2026_04_14",
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_low_v1",
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1",
        "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        "operator_retrain_candidate_v1",
        "oracle_time_snapshot_v1",
        "outcomes_v2",
        "overlap_keys_v2_wins",
        "p2_consolidated_v2",
        "p4_market_events_v2_empty",
        "p4_settlements_v2_empty",
        "platt_models_v2",
        "platt_models_v2_archived_2026_06_03",
        "position_events_pre_d0_v1",
        "position_events_pre_day0_v1",
        "position_events_pre_f4_v1",
        "position_events_v3",
        "pre_delete_v2_pairs",
        "prev_v2",
        "primary_v2_domain_mismatch",
        "producer_readiness_v1",
        "project_accumulator_to_v2",
        "promote_model_bias_ens_v2",
        "rebuild_calibration_pairs_v2",
        "recency_v2",
        "replacement_forecast_before_after_v1",
        "replacement_forecast_go_live_readiness_v1",
        "replacement_forecast_live_authority_switch_receipt_v1",
        "replacement_forecast_refit_handoff_v1",
        "replacement_forecast_shadow_veto_switch_receipt_v1",
        "replacement_product_keyed_v1",
        "replacement_soft_anchor_finetune_artifact_v1",
        "settlements_v2",
        "settlements_v2_distribution",
        "sp_obs_v2_insert_rows_",
        "sqlite_contention_structural_design_v4_2026_05_07",
        "test_b070_control_overrides_history_v2",
        "test_obs_v2_writer",
        "test_phase4_platt_v2",
        "the_path_bayes_precision_fusion_v1",
        "tigge_mn2t6_local_calendar_day_min_v1",
        "tigge_mx2t6_local_calendar_day_max_v1",
        "tigge_mx2t6_local_peak_window_max_v1",
        "tigge_param167_v3",
        "tigge_step024_v1_near_peak",
        "tigge_step024_v1_overnight_snapshot",
        "tigge_step024_v2_",
        "tigge_v3",
        "unknown_v0",
        "wu_daily_v2",
        "wu_icao_daily_backfill_v2",
        "wu_icao_history_v1",
        "wu_icao_v1_2026",
        "zeus_dual_track_refactor_package_v2_2026",
        "zeus_live_release_paper_proof_v1",
        "zeus_v2",
    }
)

def _iter_scanned_tokens() -> list[tuple[str, str]]:
    """Yield (token, relpath) for every ``_v[0-9]`` token in scanned file CONTENT
    and in scanned FILE NAMES (the filename surface). One pass, all five surfaces.
    """
    out: list[tuple[str, str]] = []
    tracked = subprocess.run(
        ["git", "ls-files", *_SCAN_DIRS],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for rel in tracked:
        p = _REPO_ROOT / rel
        # Filename surface.
        for tok in _VERSION_TOKEN_RE.findall(Path(rel).name):
            out.append((tok, rel))
        # Content surface (symbols / DDL / config keys / identity literals).
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for tok in _VERSION_TOKEN_RE.findall(text):
            out.append((tok, rel))
    return out

def _offending_tokens(scanned: list[tuple[str, str]]) -> dict[str, str]:
    """Return {token: first_relpath} for tokens neither allowlisted nor grandfathered."""
    offenders: dict[str, str] = {}
    for tok, rel in scanned:
        if tok in ALLOWLIST or tok in GRANDFATHER:
            continue
        offenders.setdefault(tok, rel)
    return offenders

def test_no_new_internal_version_suffix_any_surface() -> None:
    """RATCHET: any ``_v[0-9]`` token not in ALLOWLIST/GRANDFATHER fails CI.

    Covers all five surfaces (symbols, filenames, table DDL, config keys, identity
    string literals) in src/, scripts/, config/.
    """
    offenders = _offending_tokens(_iter_scanned_tokens())
    assert not offenders, (
        "NEW internal _v[0-9] version-suffix token(s) found in src/scripts/config.\n"
        "Drop the suffix (the canonical version IS the bare name). Only if this is "
        "a genuine EXTERNAL protocol/API/package/CLI version may you add it to "
        "ALLOWLIST (with a one-line rationale). Do NOT add it to GRANDFATHER — that "
        "set only shrinks.\n"
        "Offenders (token -> first file):\n  "
        + "\n  ".join(f"{t}  ({r})" for t, r in sorted(offenders.items()))
    )

def test_injected_v9_symbol_is_caught() -> None:
    """Self-test: a synthetic NEW ``_v9`` token must be flagged by the ratchet.

    Proves the antibody actually bites — feed it content that the live tree does
    not contain and confirm it surfaces as an offender.
    """
    synthetic = [
        ("brand_new_helper_v9", "src/fake/injected.py"),     # symbol surface
        ("widgets_v9", "scripts/migrations/fake_create_v9.py"),  # DDL/filename
        ("ddd_v9_enabled", "config/settings.json"),          # config-key surface
    ]
    offenders = _offending_tokens(synthetic)
    assert "brand_new_helper_v9" in offenders, (
        "Ratchet failed to catch an injected _v9 symbol — the antibody is inert."
    )
    assert "widgets_v9" in offenders and "ddd_v9_enabled" in offenders, (
        "Ratchet failed to catch injected _v9 DDL/config tokens."
    )

def test_allowlisted_token_is_not_an_offender() -> None:
    """A known external token (polymarket_v2_adapter) must NOT be flagged."""
    offenders = _offending_tokens([("polymarket_v2_adapter", "src/venue/x.py")])
    assert not offenders, "ALLOWLIST entry was incorrectly flagged as an offender."

def test_no_dead_grandfather_entries() -> None:
    """Hygiene + RATCHET-SHRINK: every GRANDFATHER token must still appear in the
    tree. A grandfather entry that no longer occurs means a migration landed (or
    the token was a typo) — remove it. This is what forces the set toward zero.
    """
    present = {tok for tok, _ in _iter_scanned_tokens()}
    dead = sorted(GRANDFATHER - present)
    assert not dead, (
        "GRANDFATHER contains tokens no longer present in src/scripts/config — a "
        "migration landed or the entry is stale. Remove these (the set only "
        f"shrinks):\n  {dead}"
    )

def test_allowlist_grandfather_disjoint() -> None:
    """A token may not be in both sets (would mask an external version as churn)."""
    overlap = sorted(ALLOWLIST & GRANDFATHER)
    assert not overlap, f"ALLOWLIST and GRANDFATHER overlap: {overlap}"
