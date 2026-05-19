# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  architecture/db_table_ownership.yaml:settlements_v2 (line 1515, db: forecasts)
#                  architecture/db_table_ownership.yaml:uma_resolution (line 1614, db: world)
"""
Consolidated settlement writer with era provenance.

DESIGN CONTRACT:
    This module is the SINGLE write path for settlements_v2 in Phase 0+.
    Both historical write paths (harvester.py L2 and harvester_truth_writer.py L4)
    are consolidated here. After PR 1 implementation:
      - src/execution/harvester.py calls write_settlement_v2_with_era_provenance()
      - src/ingest/harvester_truth_writer.py calls write_settlement_v2_with_era_provenance()
    Neither site may write harvester_live_uma_vote provenance directly.

INV-37 CONSTRAINT (EXPLICIT):
    ALL writes involving settlements_v2 (forecasts.db) AND uma_resolution or
    era_watermark (world.db) MUST go through get_forecasts_connection_with_world()
    at src/state/db.py:205. No bare sqlite3.connect() of either DB in this module.

ERA AUTHORITY BASIS SINGLETONS:
    Module-level constants document the two known eras at Phase 0.
    These are compile-time snapshots; runtime boundaries are authoritative
    from era_watermark table (world.db).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from src.contracts.resolution_era import (
    EraAuthorityBasis,
    EraDispatchOutcome,
    EraDispatchResult,
    ResolutionEra,
)


# ---------------------------------------------------------------------------
# Era authority basis singletons — compile-time snapshots
# ---------------------------------------------------------------------------

ERA_BASIS_UMA_OO_V2 = EraAuthorityBasis(
    era=ResolutionEra.UMA_OO_V2,
    era_start_date_utc=date(2020, 1, 1),        # contract inception (approximate lower bound)
    era_end_date_utc=date(2026, 2, 21),          # exclusive end; first day of next era
    on_chain_address="",                          # UMA OO v2 address not tracked here; see uma_resolution table
    on_chain_codehash="",                         # N/A for historical era
    authority_doc="architecture/db_table_ownership.yaml",
    operator_authorization_date=date(2026, 5, 19),
)

ERA_BASIS_INTERNAL_RESOLVER = EraAuthorityBasis(
    era=ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21,
    era_start_date_utc=date(2026, 2, 21),         # inclusive start
    era_end_date_utc=None,                         # open era — currently active
    on_chain_address="0x69c47De9D4D3Dad79590d61b9e05918E03775f24",
    on_chain_codehash="0x76a83a5e6b6e30a6fefe5ca6af94dcfed92cea8e8ea739abbc8d4a663c876be1",
    authority_doc="architecture/db_table_ownership.yaml",
    operator_authorization_date=date(2026, 5, 19),
)

# ERA_CUTOVER_DATE is the canonical boundary. Any settled_at >= this date
# and era == UMA_OO_V2 is anomalous (INV-ERA-1).
ERA_CUTOVER_DATE = date(2026, 2, 21)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch_era_basis(settled_at_utc: date) -> EraDispatchResult:
    """Dispatch to the correct EraAuthorityBasis for a given settlement date.

    Returns EraDispatchResult with typed outcome. Callers MUST check
    result.outcome before accessing result.era_basis (None on non-ERA_RESOLVED).

    Args:
        settled_at_utc: The UTC date of the settlement event. Callers
            must have already resolved local->UTC conversion before calling here.

    Returns:
        EraDispatchResult with outcome=ERA_RESOLVED and era_basis populated on
        success; outcome=ERA_DEAD with era_basis=None when no era matches.

    Raises:
        ValueError: if settled_at_utc is None or not a date instance.
    """
    if settled_at_utc is None or not isinstance(settled_at_utc, date):
        raise ValueError(f"settled_at_utc must be a date, got {type(settled_at_utc)}")

    if settled_at_utc >= ERA_CUTOVER_DATE:
        return EraDispatchResult(
            outcome=EraDispatchOutcome.ERA_RESOLVED,
            era_basis=ERA_BASIS_INTERNAL_RESOLVER,
            reason_code="post_cutover_internal_resolver",
            reason_message=f"settled_at {settled_at_utc} >= cutover {ERA_CUTOVER_DATE}",
        )
    elif settled_at_utc >= ERA_BASIS_UMA_OO_V2.era_start_date_utc:
        return EraDispatchResult(
            outcome=EraDispatchOutcome.ERA_RESOLVED,
            era_basis=ERA_BASIS_UMA_OO_V2,
            reason_code="pre_cutover_uma_oo_v2",
            reason_message=f"settled_at {settled_at_utc} < cutover {ERA_CUTOVER_DATE}",
        )
    else:
        # No era covers this date — fail-closed, never silent (Critic P4)
        return EraDispatchResult(
            outcome=EraDispatchOutcome.ERA_DEAD,
            era_basis=None,
            reason_code="no_era_match",
            reason_message=(
                f"settled_at {settled_at_utc} predates all known era starts. "
                "Dispatcher must raise or quarantine; never write settlement row."
            ),
        )


def write_settlement_v2_with_era_provenance(
    settlement: dict[str, Any],
    era_basis: EraAuthorityBasis,
    *,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Write a settlement row to settlements_v2 with typed era provenance.

    This is the CONSOLIDATED write path for all settlement writes. Both
    src/execution/harvester.py and src/ingest/harvester_truth_writer.py
    route through here after PR 1 implementation.

    INV-37 ATTACH+SAVEPOINT PATTERN:
        When conn=None, obtains connection via get_forecasts_connection_with_world()
        and wraps the write in a SAVEPOINT era_dispatch for atomicity.
        When conn is caller-provided, the caller owns the transaction boundary;
        no SAVEPOINT is added (avoids with-conn + SAVEPOINT atomicity collision,
        MEMORY: feedback_with_conn_nested_savepoint_audit).

    Args:
        settlement: Dict containing at minimum city, target_date, temperature_metric,
            market_slug, and all fields required by settlements_v2 schema.
        era_basis: The EraAuthorityBasis for this settlement. Use
            dispatch_era_basis(settled_at_utc) to obtain it.
        conn: Optional pre-established connection (for testing with monkey-patched
            connections or caller-owned transactions). When None, obtains via
            get_forecasts_connection_with_world().

    Returns:
        The dict returned by log_settlement_v2 (e.g. {"status": "written", ...} or
        {"status": "refused_missing_identity", ...}). Callers MUST check status.

    Raises:
        ValueError: if settlement is missing required fields or era basis is inconsistent.
    """
    from src.state.db import get_forecasts_connection_with_world, log_settlement_v2

    def _execute(active_conn: Any) -> dict[str, Any]:
        uma_row: dict[str, Any] | None = None
        if era_basis.era == ResolutionEra.UMA_OO_V2:
            condition_id = settlement.get("condition_id")
            if condition_id:
                row = active_conn.execute(
                    """
                    SELECT condition_id, tx_hash, block_number, resolved_value,
                           resolved_at_utc, raw_log_json, observed_at_utc
                    FROM uma_resolution
                    WHERE condition_id = ?
                    ORDER BY block_number DESC
                    LIMIT 1
                    """,
                    (condition_id,),
                ).fetchone()
                if row:
                    keys = (
                        "condition_id", "tx_hash", "block_number", "resolved_value",
                        "resolved_at_utc", "raw_log_json", "observed_at_utc",
                    )
                    uma_row = dict(zip(keys, row))

        provenance = _build_era_provenance(settlement, era_basis, uma_row)
        # Merge caller-supplied provenance fields (writer tag, obs_source, etc.) so
        # era provenance fields are added rather than replacing existing evidence.
        merged_provenance = dict(settlement.get("provenance", {}))
        merged_provenance.update(provenance)

        return log_settlement_v2(
            active_conn,
            city=settlement["city"],
            target_date=settlement["target_date"],
            temperature_metric=settlement["temperature_metric"],
            market_slug=settlement.get("market_slug"),
            winning_bin=settlement.get("winning_bin"),
            settlement_value=settlement.get("settlement_value"),
            settlement_source=settlement.get("settlement_source"),
            settled_at=settlement.get("settled_at"),
            authority=settlement.get("authority", "QUARANTINED"),
            provenance=merged_provenance,
            recorded_at=settlement.get("recorded_at"),
        )

    if conn is not None:
        # Caller owns the transaction; no SAVEPOINT wrapping here.
        return _execute(conn)
    else:
        with get_forecasts_connection_with_world() as _conn:
            _conn.execute("SAVEPOINT era_dispatch")
            try:
                result = _execute(_conn)
                _conn.execute("RELEASE SAVEPOINT era_dispatch")
                return result
            except Exception:
                _conn.execute("ROLLBACK TO SAVEPOINT era_dispatch")
                raise


def _build_era_provenance(
    settlement: dict[str, Any],
    era_basis: EraAuthorityBasis,
    uma_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the provenance_json dict for a settlement row.

    The returned dict is merged into the caller's provenance dict and
    serialised as provenance_json TEXT in settlements_v2.

    NOTE: The string 'harvester_live_uma_vote' MUST NOT appear in any
    provenance_json written after PR 1 merge. Post-PR-1 antibody test
    (tests/test_inv_era_provenance_post_cutover_count.py) asserts COUNT == 0.
    """
    provenance: dict[str, Any] = {
        "era": era_basis.era.value,
        "era_start_date_utc": era_basis.era_start_date_utc.isoformat(),
        "on_chain_address": era_basis.on_chain_address,
        "on_chain_codehash": era_basis.on_chain_codehash,
        "authority_doc": era_basis.authority_doc,
        "operator_authorization_date": era_basis.operator_authorization_date.isoformat(),
        # Reconstruction method — replaces legacy tag
        "reconstruction_method": f"era_provenance_{era_basis.era.value}",
    }
    if uma_row is not None:
        provenance["uma_condition_id"] = uma_row.get("condition_id")
        provenance["uma_tx_hash"] = uma_row.get("tx_hash")
        provenance["uma_confirmations"] = uma_row.get("confirmations_count")
    else:
        provenance["uma_condition_id"] = None
        provenance["uma_tx_hash"] = None
        provenance["uma_confirmations"] = None
    return provenance
