# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  architecture/db_table_ownership.yaml:settlements_v2 (line 1515, db: forecasts)
#                  architecture/db_table_ownership.yaml:uma_resolution (line 1614, db: world)
"""
Consolidated settlement writer with era provenance.

DESIGN CONTRACT (SCAFFOLD):
    This module is the SINGLE write path for settlements_v2 in Phase 0+.
    Both historical write paths (harvester.py L2 and harvester_truth_writer.py L4)
    are consolidated here. After PR 1 implementation:
      - src/execution/harvester.py:1338 calls write_settlement_v2_with_era_provenance()
      - src/ingest/harvester_truth_writer.py:556 calls write_settlement_v2_with_era_provenance()
    Neither site may write harvester_live_uma_vote provenance directly.

INV-37 CONSTRAINT (EXPLICIT):
    ALL writes involving settlements_v2 (forecasts.db) AND uma_resolution or
    era_watermark (world.db) MUST go through get_forecasts_connection_with_world()
    at src/state/db.py:205. No bare sqlite3.connect() of either DB in this module.
    Violation raises Inv37Violation (see src/state/db.py).

ERA AUTHORITY BASIS SINGLETONS:
    Module-level constants document the two known eras at Phase 0.
    These are compile-time snapshots; runtime boundaries are authoritative
    from era_watermark table (world.db).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from src.contracts.resolution_era import EraAuthorityBasis, ResolutionEra


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
# Public API (SCAFFOLD — bodies are pseudocode comments only)
# ---------------------------------------------------------------------------

def dispatch_era_basis(settled_at_utc: date) -> EraAuthorityBasis:
    """Dispatch to the correct EraAuthorityBasis for a given settlement date.

    SCAFFOLD — implementation body not yet written.

    Logic:
        if settled_at_utc >= ERA_CUTOVER_DATE:
            return ERA_BASIS_INTERNAL_RESOLVER
        else:
            return ERA_BASIS_UMA_OO_V2

    Args:
        settled_at_utc: The UTC date of the settlement event. Callers
            must have already resolved local→UTC conversion before calling here.

    Returns:
        The authoritative EraAuthorityBasis for the settlement date.

    Raises:
        ValueError: if settled_at_utc is None or not a date instance.
    """
    ...


def write_settlement_v2_with_era_provenance(
    settlement: dict[str, Any],
    era_basis: EraAuthorityBasis,
    *,
    conn: Any | None = None,
) -> None:
    """Write a settlement row to settlements_v2 with typed era provenance.

    This is the CONSOLIDATED write path for all settlement writes. Both
    src/execution/harvester.py and src/ingest/harvester_truth_writer.py
    route through here after PR 1 implementation.

    SCAFFOLD — implementation body is pseudocode comments only.

    INV-37 ATTACH+SAVEPOINT PATTERN (pseudocode):

        from src.state.db import get_forecasts_connection_with_world

        with get_forecasts_connection_with_world() as _conn:   # ATTACH zeus-world.db AS world
            _conn.execute("SAVEPOINT era_dispatch")
            try:
                if era_basis.era == ResolutionEra.UMA_OO_V2:
                    # 1. Read uma_resolution from world.uma_resolution
                    #    to verify the settlement is backed by an on-chain UMA vote.
                    #    SELECT * FROM world.uma_resolution
                    #    WHERE condition_id = settlement['condition_id']
                    #    AND confirmations_count >= confirmations_required
                    #    -> if not found: raise ValueError(f"no confirmed UMA row for {settlement}")

                    # 2. Build provenance_json with era metadata
                    #    provenance = _build_era_provenance(settlement, era_basis, uma_row)

                elif era_basis.era == ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21:
                    # 1. Internal resolver: eth_call validation path
                    #    No uma_resolution lookup; use era_basis.on_chain_address for audit trail
                    #    provenance = _build_era_provenance(settlement, era_basis, uma_row=None)

                # 3. INSERT OR REPLACE INTO settlements_v2 with provenance_json
                #    including era=era_basis.era.value in the provenance blob.
                #    NEVER write 'harvester_live_uma_vote' in provenance_json after PR 1.

                # 4. Optionally update era_watermark in world.era_watermark
                #    (only on first-write for a new era transition)

                _conn.execute("RELEASE SAVEPOINT era_dispatch")
            except Exception:
                _conn.execute("ROLLBACK TO SAVEPOINT era_dispatch")
                raise

    Args:
        settlement: Dict containing at minimum condition_id, outcome, settled_at,
            and all fields required by settlements_v2 schema.
        era_basis: The EraAuthorityBasis for this settlement. Use
            dispatch_era_basis(settled_at_utc) to obtain it.
        conn: Optional pre-established connection (for testing with monkey-patched
            connections). When None, obtains via get_forecasts_connection_with_world().

    Raises:
        Inv37Violation: if a bare sqlite3.connect() is used (caught at db.py level).
        ValueError: if settlement is missing required fields or era basis is inconsistent.
    """
    ...


def _build_era_provenance(
    settlement: dict[str, Any],
    era_basis: EraAuthorityBasis,
    uma_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the provenance_json dict for a settlement row.

    SCAFFOLD — implementation body not yet written.

    The returned dict is serialised as provenance_json TEXT in settlements_v2.
    Key fields (SCAFFOLD pseudocode):
        {
            "era": era_basis.era.value,
            "era_start_date_utc": era_basis.era_start_date_utc.isoformat(),
            "on_chain_address": era_basis.on_chain_address,
            "on_chain_codehash": era_basis.on_chain_codehash,
            "authority_doc": era_basis.authority_doc,
            "operator_authorization_date": era_basis.operator_authorization_date.isoformat(),
            # For UMA_OO_V2 era only:
            "uma_condition_id": uma_row['condition_id'] if uma_row else None,
            "uma_tx_hash": uma_row['tx_hash'] if uma_row else None,
            "uma_confirmations": uma_row['confirmations_count'] if uma_row else None,
            # Reconstruction method (replaces legacy 'harvester_live_uma_vote' tag):
            "reconstruction_method": f"era_provenance_{era_basis.era.value}",
        }

    NOTE: The string 'harvester_live_uma_vote' MUST NOT appear in any
    provenance_json written after PR 1 merge. Post-PR-1 antibody test
    (tests/test_inv_era_provenance_post_cutover_count.py) asserts COUNT == 0.
    """
    ...
