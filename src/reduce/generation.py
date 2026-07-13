# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a "Read-model 诚实不变量": ``ReadModel[g] = Reducer_v(ImmutableFacts
#   through coverage vector C[g])`` with full lineage and "portfolio
#   publication by complete generation, never per-row latest."
"""Read-model generation contract + table-backed store (LX-2R-a synthetic).

A ``Generation`` is the atomic publication unit the honesty invariant
demands: a reducer version, a coverage vector (per-source watermarks/
completeness the generation was computed against), an input fingerprint
(so two generations over identical inputs are provably identical), and the
closed set of positions it covers. ``GenerationStore.publish`` writes the
generation row and every one of its position-economics rows in ONE
transaction -- a caller can never observe a generation that is half-written
("portfolio 发布按完整 generation,不按 per-row latest").

SCOPE BOUNDARY: this module is NOT wired into any live trade-DB init path,
production reader, or writer. ``GenerationStore`` is a standalone contract +
store with synthetic-fixture tests only, per this packet's explicit
boundary (docs/rebuild/local_ledger_excision_2026-07-12.md packet LX-2R-a:
"NO live publication wiring this packet -- the contract + an in-memory/
table-backed store with tests").
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

from src.reduce.position_economics import REDUCER_VERSION, PositionEconomics
from src.reduce.schema.generation_schema import ensure_tables


@dataclass(frozen=True)
class CoverageVector:
    """Per-source watermarks/completeness a generation was computed against.

    ``fill_sync_watermarks`` maps sync source name -> the ``watermark_ts``
    (or other opaque coverage marker) read from ``fill_sync_watermarks`` at
    computation time. ``payout_observation_complete`` and
    ``supersession_backfill_marker`` are the other two named coverage axes
    from the packet's honesty invariant (payout completeness, supersession-
    backfill marker) -- callers building a real generation are expected to
    have already checked these against the reducer's own refusal conditions
    (see src/reduce/position_economics.py) before assembling this vector.
    """

    fill_sync_watermarks: Mapping[str, str]
    payout_observation_complete: bool
    supersession_backfill_marker: str | None

    def as_dict(self) -> dict:
        return {
            "fill_sync_watermarks": dict(sorted(self.fill_sync_watermarks.items())),
            "payout_observation_complete": self.payout_observation_complete,
            "supersession_backfill_marker": self.supersession_backfill_marker,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class Generation:
    """One published (or publishable) read-model generation."""

    generation_id: str
    reducer_version: str
    coverage: CoverageVector
    computed_at: str
    input_fingerprint: str
    position_ids: tuple[str, ...]


def compute_input_fingerprint(
    position_ids: Sequence[str], coverage: CoverageVector, reducer_version: str
) -> str:
    """Deterministic fingerprint: identical inputs -> identical fingerprint,
    regardless of ``position_ids`` ordering."""
    basis = {
        "position_ids": sorted(position_ids),
        "coverage": coverage.fingerprint(),
        "reducer_version": reducer_version,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()


def build_generation(
    *,
    position_ids: Sequence[str],
    coverage: CoverageVector,
    computed_at: str,
    reducer_version: str = REDUCER_VERSION,
    generation_id: str | None = None,
) -> Generation:
    """Assemble a ``Generation`` record. Does not touch a database."""
    return Generation(
        generation_id=generation_id or str(uuid.uuid4()),
        reducer_version=reducer_version,
        coverage=coverage,
        computed_at=computed_at,
        input_fingerprint=compute_input_fingerprint(position_ids, coverage, reducer_version),
        position_ids=tuple(position_ids),
    )


class GenerationAlreadyPublishedError(Exception):
    """``generation_id`` collides with an already-published generation."""


class GenerationPositionSetMismatchError(Exception):
    """The economics rows handed to ``publish`` don't match
    ``generation.position_ids`` exactly -- refuses a partial or
    over-complete publication rather than writing a generation whose row set
    silently disagrees with its own manifest."""


class GenerationStore:
    """Table-backed generation store. Publication is all-or-nothing.

    Calls ``ensure_tables`` on construction (idempotent) so a fixture conn
    only needs the base trade-DB schema already applied.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        ensure_tables(conn)

    def publish(
        self, generation: Generation, economics: Sequence[PositionEconomics]
    ) -> None:
        existing = self._conn.execute(
            "SELECT 1 FROM reduce_generations WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()
        if existing is not None:
            raise GenerationAlreadyPublishedError(generation.generation_id)

        economics_position_ids = {e.position_id for e in economics}
        if economics_position_ids != set(generation.position_ids):
            raise GenerationPositionSetMismatchError(
                "generation.position_ids does not match the published "
                f"economics set: generation={sorted(generation.position_ids)} "
                f"economics={sorted(economics_position_ids)}"
            )

        with self._conn:  # sqlite3 context manager: commit on success, rollback on raise
            self._conn.execute(
                """
                INSERT INTO reduce_generations (
                    generation_id, reducer_version, computed_at,
                    input_fingerprint, coverage_json, position_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    generation.generation_id,
                    generation.reducer_version,
                    generation.computed_at,
                    generation.input_fingerprint,
                    json.dumps(generation.coverage.as_dict()),
                    json.dumps(list(generation.position_ids)),
                ),
            )
            for econ in economics:
                self._conn.execute(
                    """
                    INSERT INTO reduce_position_economics (
                        generation_id, position_id, keeper_position_id,
                        absorbed_position_ids_json, net_shares, cost_basis_usd,
                        realized_pnl_usd, fees_usd, fill_count, payout_status,
                        payout_pnl_usd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation.generation_id,
                        econ.position_id,
                        econ.keeper_position_id,
                        json.dumps(list(econ.absorbed_position_ids)),
                        econ.net_shares,
                        econ.cost_basis_usd,
                        econ.realized_pnl_usd,
                        econ.fees_usd,
                        econ.fill_count,
                        econ.payout_status,
                        econ.payout_pnl_usd,
                    ),
                )

    def latest(self) -> Generation | None:
        row = self._conn.execute(
            "SELECT generation_id FROM reduce_generations "
            "ORDER BY computed_at DESC, generation_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get(row[0])

    def get(self, generation_id: str) -> Generation | None:
        row = self._conn.execute(
            """
            SELECT generation_id, reducer_version, computed_at, input_fingerprint,
                   coverage_json, position_ids_json
              FROM reduce_generations WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchone()
        if row is None:
            return None
        coverage_raw = json.loads(row[4])
        coverage = CoverageVector(
            fill_sync_watermarks=coverage_raw["fill_sync_watermarks"],
            payout_observation_complete=coverage_raw["payout_observation_complete"],
            supersession_backfill_marker=coverage_raw["supersession_backfill_marker"],
        )
        return Generation(
            generation_id=row[0],
            reducer_version=row[1],
            computed_at=row[2],
            input_fingerprint=row[3],
            coverage=coverage,
            position_ids=tuple(json.loads(row[5])),
        )

    def economics_for(self, generation_id: str) -> list[dict]:
        """Raw rows (as dicts) for one generation -- test/audit convenience."""
        rows = self._conn.execute(
            "SELECT * FROM reduce_position_economics WHERE generation_id = ? "
            "ORDER BY position_id",
            (generation_id,),
        ).fetchall()
        return [dict(row) for row in rows]


__all__ = [
    "CoverageVector",
    "Generation",
    "compute_input_fingerprint",
    "build_generation",
    "GenerationAlreadyPublishedError",
    "GenerationPositionSetMismatchError",
    "GenerationStore",
]
