# Created: 2026-07-14
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-3R ("单次 fenced activation" -- "此界后:所有 money reader 用新契约,
#   业务停写旧经济学"). This module is the ACCESSOR SHAPE that future
#   money-path readers (bankroll/riskguard/monitor/exit) will switch to at
#   the coordinated cutover -- it is the read half of the "cutover unit =
#   ownership of the forbidden columns themselves" verdict
#   (src/contracts/economics_ownership.py module docstring). Nothing in this
#   repo imports this module yet; wiring a live reader onto it is a future,
#   separate, coordinated deploy step this packet explicitly does not take.
"""Read-model accessor: latest published chain-truth economics for a position.

This module answers exactly one question -- "what does the current published
Generation say about position P's economics?" -- by querying the EXISTING
``reduce_generations`` / ``reduce_position_economics`` tables
(``src.reduce.generation.GenerationStore``) and the EXISTING identity-
supersession resolver (``src.reduce.position_economics._resolve_identity_group``).
It reimplements neither: no fold arithmetic, no coverage/refusal logic, no
new table. Pure read, pure orchestration, matching the idiom already
established by ``src.reduce.materialize``.

WHOLE-GENERATION SEMANTICS -- NOT PER-ROW LATEST
--------------------------------------------------
``generation.py``'s honesty invariant is explicit: "portfolio 发布按完整
generation,不按 per-row latest" (publication is by complete generation,
never per-row latest). ``latest_position_economics`` honors this literally:
it first pins the single latest PUBLISHED generation (by
``computed_at DESC, generation_id DESC`` -- the same ordering
``GenerationStore.latest()`` uses), then looks up this position's row WITHIN
that one generation only. It never falls back to an older generation that
happened to cover this position -- that would silently resurrect per-row-
latest semantics through a side door and reintroduce the double-counted-
parallel-ledger disease this whole packet exists to kill, one layer up.

*** CRITICAL MONEY-PATH SEMANTIC -- READ THIS BEFORE CALLING EITHER FUNCTION ***
-----------------------------------------------------------------------------
``latest_position_economics`` returns ``None`` for THREE distinct situations,
deliberately collapsed into one signal:

  1. No generation has ever been published (``reduce_generations`` absent or
     empty -- e.g. a pre-cutover trade DB, or the daemon job that calls
     ``src.reduce.materialize.materialize_and_publish_cycle`` has not run
     yet).
  2. The latest published generation exists, but this position (after
     resolving to its supersession keeper) is not among its rows -- either
     because the reducer REFUSED it at materialization time
     (``src.reduce.materialize.PositionRefusal`` -- e.g. missing condition
     attribution, missing fill-sync coverage) or because the position simply
     did not exist yet when that generation was computed.
  3. ``position_id`` is truthy but resolves (via identity-supersession) to a
     keeper this generation never covered.

``None`` means UNKNOWN / DEGRADED. It is NEVER a synonym for zero economics.
A caller that does ``economics = latest_position_economics(conn, pid) or {}``
or reads ``.get("realized_pnl_usd", 0)`` off a ``None`` has reintroduced the
EXACT clobber bug this excision exists to kill (see
src/contracts/economics_ownership.py: "realized_pnl_usd -- ... Exhibit A
settled-clobber bug lives on this column"). The correct pattern is fail-
closed: treat ``None`` as "this reader cannot act on this position right
now" (skip, hold, or route to a review queue) -- the same fold-never-a-guess
discipline ``src.reduce.position_economics.ReducerRefusal`` already enforces
on the write/compute side. See
``tests/reduce/test_read_model.py::TestNoneNeverZero`` for the explicit
regression test this invariant demands.

SCOPE BOUNDARY
--------------
Same boundary as every other ``src.reduce`` module: nothing outside
``tests/reduce/`` imports this yet. It does not touch ``position_current``,
``projection.py``, or any live reader. Read-only -- writes nothing, opens
nothing (``conn`` is always caller-supplied, per INV-37).
"""
from __future__ import annotations

import sqlite3

from src.reduce.position_economics import _resolve_identity_group

_GENERATIONS_TABLE = "reduce_generations"
_ECONOMICS_TABLE = "reduce_position_economics"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _generation_tables_exist(conn: sqlite3.Connection) -> bool:
    """Both generation-store tables are created together, in the same
    ``ensure_tables`` call, and only ever populated together inside
    ``GenerationStore.publish``'s single transaction (src/reduce/generation.py)
    -- checking the parent table's presence is sufficient; there is no code
    path in this repo that creates one without the other."""
    return _table_exists(conn, _GENERATIONS_TABLE)


def latest_generation_id(conn: sqlite3.Connection) -> str | None:
    """The ``generation_id`` of the most recently computed published
    generation, or ``None`` if none has ever been published (including the
    pre-cutover case where ``reduce_generations`` does not exist yet).

    Ordering mirrors ``src.reduce.generation.GenerationStore.latest()``
    exactly (``computed_at DESC, generation_id DESC``) -- this function does
    not go through ``GenerationStore`` itself because its constructor calls
    ``ensure_tables`` (idempotent DDL, but a write side effect this pure-read
    accessor must never trigger against a live connection it does not own).
    """
    if not _generation_tables_exist(conn):
        return None
    row = conn.execute(
        f"SELECT generation_id FROM {_GENERATIONS_TABLE} "
        "ORDER BY computed_at DESC, generation_id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row is not None else None


def latest_position_economics(conn: sqlite3.Connection, position_id: str) -> dict | None:
    """The chain-truth economics row for ``position_id`` from the LATEST
    published generation, or ``None`` -- see the module docstring's
    "CRITICAL MONEY-PATH SEMANTIC" section before treating ``None`` as
    anything other than fail-closed UNKNOWN.

    ``position_id`` may be a keeper or an absorbed identity -- both resolve
    to the same keeper-scoped row (``_resolve_identity_group``, reused
    verbatim from ``src.reduce.position_economics`` -- the identical
    resolution the reducer itself applies at materialization time, so a
    caller here can never observe a different keeper mapping than the one
    the published row was actually computed under).

    Returns a plain ``dict`` (one row of ``reduce_position_economics``,
    including its ``generation_id`` / ``keeper_position_id`` /
    ``absorbed_position_ids_json`` columns) rather than a
    ``PositionEconomics`` dataclass -- this is a raw read-model lookup, not a
    fold; see ``src.reduce.generation.GenerationStore.economics_for`` for the
    same dict-row convention this mirrors. Requires ``conn.row_factory =
    sqlite3.Row`` (the convention every ``src.reduce`` caller already
    follows -- see tests/reduce/conftest.py).
    """
    if not position_id:
        raise ValueError("position_id is required")

    generation_id = latest_generation_id(conn)
    if generation_id is None:
        return None

    keeper_id, _absorbed = _resolve_identity_group(conn, position_id)

    row = conn.execute(
        f"""
        SELECT rpe.*
          FROM {_ECONOMICS_TABLE} AS rpe
         WHERE rpe.generation_id = ?
           AND rpe.position_id = ?
        """,
        (generation_id, keeper_id),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


__all__ = [
    "latest_generation_id",
    "latest_position_economics",
]
