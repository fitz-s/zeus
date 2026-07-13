# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R
#   "契约+激活控制") + src.state.db.assert_schema_epoch_not_mixed /
#   scripts/migrations/2026_07_quarantine_phase_retirement.py (the proven T5
#   schema_epoch pattern this module EXTENDS, not a parallel mechanism).

"""Trade-DB truth-epoch machinery (LX-0R deliverable 2).

Extends the existing schema_epoch mechanism (``src.state.db.assert_schema_epoch_not_mixed``,
stamped by ``scripts/migrations/2026_07_quarantine_phase_retirement.py``) rather than
building a second parallel one: ``truth_epoch`` is a single-row table, same shape idiom
(id=1 singleton, ``CHECK (id = 1)``, ISO timestamp), but it tracks a DIFFERENT axis —
schema_epoch answers "did the T5 DDL/data migration run"; truth_epoch answers "who is
the current economics authority" (docs/rebuild/local_ledger_excision_2026-07-12.md
LEGACY / PREPARE / ACTIVE_NEW). The two are independent and both may be read at boot.

This packet lands the table + read/transition/capability API ONLY. The trade DB's
truth epoch stays LEGACY: nothing here is wired into any live seam, no command
admission gate, no money-read gate, no DB write-firewall trigger. Those are LX-3R
(one fenced activation) territory per the round-2 delta adjudication — this module is
inert plumbing a later activation packet will call.

INV-37: every function takes a caller-supplied ``conn`` (trade DB connection);
nothing here opens or ATTACHes a connection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.contracts.economics_ownership import TruthEpoch, truth_epoch_rank

TABLE_NAME = "truth_epoch"

TRUTH_EPOCH_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS truth_epoch (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    epoch TEXT NOT NULL CHECK (epoch IN ('LEGACY', 'PREPARE', 'ACTIVE_NEW')),
    transitioned_at TEXT NOT NULL,
    transitioned_by TEXT NOT NULL
)
"""


class TruthEpochTransitionError(RuntimeError):
    """Raised when a caller attempts a non-monotonic truth-epoch transition
    (backward, a repeat of the current epoch, or skipping a stage)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_truth_epoch_table(conn: sqlite3.Connection, *, actor: str = "init_schema_trade_only") -> None:
    """Idempotent: create ``truth_epoch`` if absent and seed its single row at
    LEGACY (the default, fail-closed epoch — a fresh or pre-LX-0R DB is always
    LEGACY, never a more-trusting epoch). No-op on an already-seeded table."""
    conn.execute(TRUTH_EPOCH_TABLE_DDL)
    conn.execute(
        "INSERT INTO truth_epoch (id, epoch, transitioned_at, transitioned_by) "
        "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
        (TruthEpoch.LEGACY.value, _now_iso(), actor),
    )


def read_truth_epoch(conn: sqlite3.Connection) -> TruthEpoch:
    """Return the stamped trade-DB truth epoch. LEGACY if the table doesn't
    exist yet or carries no row (pre-LX-0R DB) — never raises, never defaults
    to a MORE-trusting epoch than LEGACY."""
    try:
        row = conn.execute("SELECT epoch FROM truth_epoch WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return TruthEpoch.LEGACY
    if row is None:
        return TruthEpoch.LEGACY
    return TruthEpoch(str(row[0]))


def transition_truth_epoch(
    conn: sqlite3.Connection, *, to: TruthEpoch, actor: str
) -> TruthEpoch:
    """Advance the trade-DB truth epoch exactly one stage forward:
    LEGACY -> PREPARE -> ACTIVE_NEW. Refuses (``TruthEpochTransitionError``)
    on:
      - backward (``to`` ranks below the current epoch),
      - a no-op repeat (``to`` == current epoch),
      - a skip (e.g. LEGACY -> ACTIVE_NEW directly) — LX-3R requires the
        PREPARE fenced window to actually happen, not just be nameable.

    ``actor`` is a free-text identity string (e.g. a script/operator name) —
    recorded for the audit trail, never validated against a fixed vocabulary.
    """
    ensure_truth_epoch_table(conn, actor=actor)
    current = read_truth_epoch(conn)
    current_rank = truth_epoch_rank(current)
    target_rank = truth_epoch_rank(to)
    if target_rank <= current_rank:
        raise TruthEpochTransitionError(
            f"TRUTH_EPOCH_BACKWARD_OR_NOOP_REFUSED: cannot transition "
            f"{current.value} -> {to.value} (truth epoch is monotonic, forward-only)"
        )
    if target_rank != current_rank + 1:
        raise TruthEpochTransitionError(
            f"TRUTH_EPOCH_SKIP_REFUSED: cannot transition {current.value} -> {to.value} "
            "in one step — each stage (LEGACY -> PREPARE -> ACTIVE_NEW) must actually "
            "run, not be skipped"
        )
    conn.execute(
        "UPDATE truth_epoch SET epoch = ?, transitioned_at = ?, transitioned_by = ? WHERE id = 1",
        (TruthEpoch(to).value, _now_iso(), actor),
    )
    return TruthEpoch(to)


# --------------------------------------------------------------------------- #
# Process-capability check (LX-0R deliverable 2, "进程 capability 广告")        #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProcessEpochCapability:
    """A running build's advertised truth-epoch support set. LX-3R: command
    admission and money reads will refuse to serve a process whose capability
    set does not include the trade DB's live truth epoch (so a stale/rolling-
    deploy daemon can never act under an epoch it predates). Inert here — no
    seam consults this in this packet."""

    supported_epochs: frozenset[TruthEpoch]

    def supports(self, epoch: TruthEpoch) -> bool:
        return TruthEpoch(epoch) in self.supported_epochs


def current_build_capability() -> ProcessEpochCapability:
    """The capability of the build this module ships in.

    LOUD FENCE (wave-1.5 repair — the round-2 dual review named this a
    BLOCKER in both passes): a build must never ADVERTISE ACTIVE_NEW support
    before an ACTIVE_NEW reducer / new read-model / ACTIVE_NEW authority
    branch actually exists to serve it. This build has none of those — no
    reducer, no new readers, no ACTIVE_NEW admission or money-read gate — so
    returning the full LEGACY/PREPARE/ACTIVE_NEW set here would let a later
    lease or admission check accept an incapable binary as authoritative
    under an epoch it cannot serve.

    Every build that imports this module understands the enum vocabulary —
    that is NOT the same as being capable of SERVING ACTIVE_NEW. Advertised
    capability is therefore narrowed to {LEGACY, PREPARE} until the LX-2R (or
    later) activation packet lands the reducer/read-model/handlers that make
    ACTIVE_NEW real. THAT packet — not this one — is responsible for widening
    this return value, and only once those handlers exist."""
    return ProcessEpochCapability(supported_epochs=frozenset({TruthEpoch.LEGACY, TruthEpoch.PREPARE}))


def capability_admits_epoch(capability: ProcessEpochCapability, active_epoch: TruthEpoch) -> bool:
    """True iff ``capability`` supports the trade DB's live ``active_epoch``.
    Pure predicate — LX-3R wires this into command admission / money-read
    gates; nothing calls it yet."""
    return capability.supports(active_epoch)


__all__ = [
    "TABLE_NAME",
    "TRUTH_EPOCH_TABLE_DDL",
    "TruthEpochTransitionError",
    "ensure_truth_epoch_table",
    "read_truth_epoch",
    "transition_truth_epoch",
    "ProcessEpochCapability",
    "current_build_capability",
    "capability_admits_epoch",
]
