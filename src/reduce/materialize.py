# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-b -- "reducer/read-model 建成+证明" (build + prove the reducer /
#   read-model): wires the EXISTING position_economics reducer and the
#   EXISTING generation/coverage-vector contract into one whole-corpus
#   materialization pass. This module reimplements NEITHER: the fold lives
#   in src.reduce.position_economics, the condition/outcome mapping lives in
#   src.reduce.condition_resolver, the all-or-nothing publish lives in
#   src.reduce.generation. This is pure orchestration.
"""Materialize one published Generation covering every real Zeus position.

SCOPE BOUNDARY -- same as src/reduce/position_economics.py and
src/reduce/generation.py: nothing outside tests/reduce/ and this packet's own
validation harness imports this module yet. It is read-mostly (one write:
the Generation + PositionEconomics rows it publishes via GenerationStore) and
never touches position_current, projection.py, or any live reader.

IDENTITY-SUPERSESSION DEDUP (why this isn't just a loop + publish)
--------------------------------------------------------------------
``reduce_position_economics`` resolves ANY position_id in an identity group
(POSITION_IDENTITY_SUPERSEDED facts) to the SAME keeper-folded economics --
that's correct and by design (src/reduce/position_economics.py "Identity
resolution" section). But ``position_current`` still carries a separate row
per raw identity (a keeper's own row plus every absorbed row it swallowed --
verified against the live trade DB: the one supersession event on file today
still has a live position_current row for its keeper AND both its absorbed
identities). Enumerating position_current naively and publishing one
PositionEconomics row per raw position_id would therefore publish the SAME
chain reality two or three times under different position_ids -- exactly the
double-counted-parallel-ledger disease this packet exists to kill, just
reintroduced one layer up.

The fix needs no new fact-reading: ``reduce_position_economics`` already
returns ``keeper_position_id`` for every call (a documented, public field).
This module calls the reducer once per raw position_id (uniform, no
special-casing), groups the results by that field, and keeps exactly one
representative row per group -- the row whose own position_id equals the
keeper_position_id when it was itself successfully reduced (the writer's
invariant guarantees a keeper's own row is never itself absorbed elsewhere:
"an absorbed row is voided and can never become a keeper itself"), else the
lexicographically-least member id, deterministically. The dropped raw ids
are recorded as ``absorbed_duplicate_position_ids`` -- not an error, not
independently published, fully accounted for in the coverage report.

CONDITION RESOLUTION IS BEST-EFFORT, NOT A GATE
-------------------------------------------------
For each raw position_id this module tries
``src.reduce.condition_resolver.resolve_condition_outcome`` and passes
whatever it returns (or ``None``/``None`` on refusal) straight through to
``reduce_position_economics``. The reducer itself is the sole authority on
whether a position actually needed that attribution
(``ConditionAttributionMissingError`` fires only when ``net_shares > 0`` --
see its module docstring): a position resolvable to zero net shares (no
commands, or fully closed via fills) materializes cleanly even when this
module's own condition resolution failed for it. This module does not
duplicate that "do we actually need it" judgment.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.reduce.condition_resolver import (
    ConditionResolutionRefusal,
    resolve_condition_outcome,
)
from src.reduce.generation import CoverageVector, Generation, GenerationStore, build_generation
from src.reduce.position_economics import (
    REDUCER_VERSION,
    PositionEconomics,
    ReducerRefusal,
    reduce_position_economics,
)

# LX-2R-b convention (docs/rebuild/local_ledger_excision_2026-07-12.md):
# synthetic chain-only inventory stubs (src/contracts/position_truth.py
# "Synthetic chain-only inventory MUST NOT enter LocalIntent") are not real
# Zeus positions -- same exclusion scripts/repair_settled_clobbered_pnl.py
# already applies for the same reason.
_CHAIN_ONLY_PREFIX_PATTERN = "chain-only%"


@dataclass(frozen=True)
class PositionRefusal:
    """One position the reducer refused to fold, and why -- named, never
    silently dropped."""

    position_id: str
    refusal_type: str
    message: str


@dataclass(frozen=True)
class MaterializationResult:
    """Everything a caller needs to audit one materialize_generation() run.

    Reconciliation invariant: ``len(economics) + len(refusals) +
    len(absorbed_duplicate_position_ids) == total_enumerated`` -- every
    enumerated real position lands in exactly one bucket.
    """

    generation: Generation
    economics: tuple[PositionEconomics, ...]
    refusals: tuple[PositionRefusal, ...]
    absorbed_duplicate_position_ids: tuple[str, ...]
    total_enumerated: int

    @property
    def refusal_counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for refusal in self.refusals:
            counts[refusal.refusal_type] = counts.get(refusal.refusal_type, 0) + 1
        return counts


def _real_position_ids(conn) -> list[str]:
    rows = conn.execute(
        "SELECT position_id FROM position_current "
        "WHERE position_id NOT LIKE ? ORDER BY position_id",
        (_CHAIN_ONLY_PREFIX_PATTERN,),
    ).fetchall()
    return [row[0] for row in rows]


def _fill_sync_watermark_ts(conn, source: str) -> str | None:
    row = conn.execute(
        "SELECT watermark_ts FROM fill_sync_watermarks WHERE source = ?", (source,)
    ).fetchone()
    return row[0] if row is not None else None


def _dedupe_by_keeper(
    raw_results: dict[str, PositionEconomics],
) -> tuple[list[PositionEconomics], list[str]]:
    """Collapse one-row-per-raw-identity down to one-row-per-keeper-group.

    Returns ``(kept_economics, absorbed_duplicate_position_ids)``, both
    sorted by position_id for deterministic output.
    """
    by_keeper: dict[str, list[str]] = {}
    for position_id, econ in raw_results.items():
        by_keeper.setdefault(econ.keeper_position_id, []).append(position_id)

    kept: list[PositionEconomics] = []
    absorbed_duplicates: list[str] = []
    for keeper_id, member_ids in by_keeper.items():
        representative = keeper_id if keeper_id in member_ids else min(member_ids)
        kept.append(raw_results[representative])
        absorbed_duplicates.extend(m for m in member_ids if m != representative)

    kept.sort(key=lambda e: e.position_id)
    absorbed_duplicates.sort()
    return kept, absorbed_duplicates


def materialize_generation(
    conn,
    *,
    computed_at: str,
    fill_sync_source: str = "polymarket_v2",
    reducer_version: str = REDUCER_VERSION,
    generation_id: str | None = None,
) -> MaterializationResult:
    """Fold every real Zeus position through the existing reducer and
    publish ONE Generation covering the successfully-reduced set.

    ``conn`` supplies the coverage vector's own truth (the actual
    ``fill_sync_watermarks`` row for ``fill_sync_source``) -- this function
    never fabricates a watermark; if none exists, every position refuses
    with ``MissingFillSyncWatermarkError`` (the reducer's own gate) and the
    published generation covers zero positions with that source's coverage
    entry absent.

    Refusals are named and counted, never folded (see
    ``MaterializationResult.refusal_counts_by_type``). Positions that fold
    into an already-kept keeper's economics are recorded as
    ``absorbed_duplicate_position_ids``, never double-published (see module
    docstring). Raises whatever ``GenerationStore.publish`` raises
    (``GenerationPositionSetMismatchError`` etc.) -- construction here always
    keeps the manifest and the economics list in lockstep, so that should
    never trigger in practice; it is not caught, by design (a mismatch here
    would mean this function's own bookkeeping is broken, not a fact-data
    problem worth degrading past).
    """
    position_ids = _real_position_ids(conn)

    raw_results: dict[str, PositionEconomics] = {}
    refusals: list[PositionRefusal] = []

    for position_id in position_ids:
        try:
            resolution = resolve_condition_outcome(conn, position_id)
            condition_id = resolution.condition_id
            outcome_index = resolution.outcome_index
        except ConditionResolutionRefusal:
            condition_id = None
            outcome_index = None

        try:
            econ = reduce_position_economics(
                conn,
                position_id,
                condition_id=condition_id,
                outcome_index=outcome_index,
                fill_sync_source=fill_sync_source,
                reducer_version=reducer_version,
            )
        except ReducerRefusal as exc:
            refusals.append(
                PositionRefusal(
                    position_id=position_id,
                    refusal_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            continue

        raw_results[position_id] = econ

    kept_economics, absorbed_duplicate_ids = _dedupe_by_keeper(raw_results)

    watermark_ts = _fill_sync_watermark_ts(conn, fill_sync_source)
    coverage = CoverageVector(
        fill_sync_watermarks=(
            {fill_sync_source: watermark_ts} if watermark_ts is not None else {}
        ),
        payout_observation_complete=False,
        supersession_backfill_marker=None,
    )

    generation = build_generation(
        position_ids=[econ.position_id for econ in kept_economics],
        coverage=coverage,
        computed_at=computed_at,
        reducer_version=reducer_version,
        generation_id=generation_id,
    )

    GenerationStore(conn).publish(generation, kept_economics)

    return MaterializationResult(
        generation=generation,
        economics=tuple(kept_economics),
        refusals=tuple(refusals),
        absorbed_duplicate_position_ids=tuple(absorbed_duplicate_ids),
        total_enumerated=len(position_ids),
    )


__all__ = [
    "PositionRefusal",
    "MaterializationResult",
    "materialize_generation",
]
