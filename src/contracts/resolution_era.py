# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  preflight/eth_call_resolution_authority.json (codehash verified 2026-05-19)
"""
Resolution era types for Zeus settlement provenance.

OPEN ENUM POLICY:
    ResolutionEra is intentionally open (str, Enum). Future era cutovers are
    recorded via the era_watermark table in zeus-world.db — they do NOT require
    editing this file. The enum documents the two known eras at Phase 0; new
    members may be appended as era transitions are ratified by the operator.

AUTHORITY HIERARCHY:
    1. era_watermark table (zeus-world.db) — live runtime source of era boundaries
    2. EraAuthorityBasis instances — compile-time documented authority snapshot
    3. This file — structural contract only

INVARIANTS (SCAFFOLD — bodies not yet implemented):
    INV-ERA-1: A market with settled_at >= 2026-02-21 AND era == UMA_OO_V2
               is an anomaly. The dispatcher must raise or quarantine, not silently accept.
    INV-ERA-2: on_chain_codehash in EraAuthorityBasis must match the value recorded
               at eth_call_resolution_authority.json at time of operator authorisation.
    INV-ERA-3: era_end_date_utc == None means the era is currently open. Only one
               era may have None at any time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ResolutionEra(str, Enum):
    """Open enum of known settlement resolution eras.

    Values are stable string identifiers stored in provenance_json blobs
    and in the era_watermark table. Never rename existing members.

    UMA_OO_V2:
        Polymarket UMA Optimistic Oracle v2 resolved markets.
        Active from contract inception through 2026-02-21 (exclusive).
        Reference: architecture/db_table_ownership.yaml:uma_resolution (line 1614).

    INTERNAL_RESOLVER_POST_2026_02_21:
        Internal Gamma resolver (on-chain contract
        0x69c47De9D4D3Dad79590d61b9e05918E03775f24, Polygon mainnet).
        Active from 2026-02-21 (inclusive) onward.
        Codehash (keccak256): 0x76a83a5e6b6e30a6fefe5ca6af94dcfed92cea8e8ea739abbc8d4a663c876be1.
        Reference: preflight/eth_call_resolution_authority.json, final_verdict=PASSIVE_TRUTH_SOURCE.
    """

    UMA_OO_V2 = "uma_oo_v2"
    INTERNAL_RESOLVER_POST_2026_02_21 = "internal_resolver_post_2026_02_21"
    # OPEN enum — future cutovers add via era_watermark table, not by editing this file.


class EraDispatchOutcome(str, Enum):
    """Typed outcome for era dispatch operations.

    Replaces the informal "ERA_DEAD" string sentinel with a machine-readable
    enum. Callers must branch on outcome before accessing era_basis.

    ERA_RESOLVED:
        The market's settled_at date matched a known era. era_basis is populated.
        The settlement row is safe to write via write_settlement_v2_with_era_provenance().

    ERA_DEAD:
        No era matches this market — fail-closed, never silent.
        This is NOT the same as an empty observation window.
        Causes: settled_at before all known era starts, or a gap in era coverage.
        era_basis is None. Dispatcher must raise or quarantine; silent fallthrough
        is a correctness violation (Critic P4).

    ERA_EMPTY_OBSERVATION:
        The uma_resolution_listener returned an empty log window for this market.
        This is a legitimate transient state — the on-chain event may not have
        been indexed yet, or the window predates observable logs.
        era_basis is None. Caller should retry or defer, not settle.
    """

    ERA_RESOLVED = "era_resolved"
    ERA_DEAD = "era_dead"
    ERA_EMPTY_OBSERVATION = "era_empty_observation"


@dataclass(frozen=True)
class EraDispatchResult:
    """Typed result returned by dispatch_era_basis().

    SCAFFOLD: dispatch_era_basis() in settlement_writers.py returns this type.
    Callers must check outcome before accessing era_basis.

    Fields:
        outcome: The dispatch result classification.
        era_basis: Populated when outcome == ERA_RESOLVED; None otherwise.
        reason_code: Short machine-readable code for logging/metrics
            (e.g. "post_cutover_internal", "pre_cutover_uma", "no_era_match").
        reason_message: Operator-readable explanation of the dispatch decision.

    Usage pattern (SCAFFOLD pseudocode):
        result = dispatch_era_basis(settled_at_utc)
        if result.outcome != EraDispatchOutcome.ERA_RESOLVED:
            raise EraDispatchError(result.reason_code, result.reason_message)
        write_settlement_v2_with_era_provenance(settlement, result.era_basis)
    """

    outcome: EraDispatchOutcome
    era_basis: EraAuthorityBasis | None    # None iff outcome != ERA_RESOLVED
    reason_code: str                        # short machine-readable label
    reason_message: str                     # operator-readable explanation


@dataclass(frozen=True)
class EraAuthorityBasis:
    """Compile-time snapshot of authority evidence for one resolution era.

    Instances are constructed once at module level and used by the dispatcher
    to populate era_provenance fields in provenance_json blobs. They are
    NOT mutated at runtime.

    Fields:
        era: The ResolutionEra this basis documents.
        era_start_date_utc: Inclusive start date (UTC) for this era.
        era_end_date_utc: Exclusive end date (UTC), or None for the open (current) era.
        on_chain_address: The Ethereum/Polygon contract address that emits
            authoritative resolution events for this era. Empty string for
            eras resolved off-chain.
        on_chain_codehash: keccak256 hash of the contract bytecode at the
            address above, captured by pre-flight eth_call. Used to detect
            contract upgrades that would invalidate this basis.
        authority_doc: Repo-relative path to the architecture YAML that
            governs this era's ownership and settlement semantics.
        operator_authorization_date: Date the operator explicitly ratified
            this era transition.

    SCAFFOLD NOTE:
        Authority basis singletons are declared in settlement_writers.py.
        This dataclass is the type contract only.
    """

    era: ResolutionEra
    era_start_date_utc: date
    era_end_date_utc: date | None          # None = open era (currently active)
    on_chain_address: str                  # 0x… contract; "" for off-chain eras
    on_chain_codehash: str                 # keccak256 from pre-flight eth_call; "" if N/A
    authority_doc: str                     # path to architecture yaml
    operator_authorization_date: date
