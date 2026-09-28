# Created: 2026-09-27
# Last reused or audited: 2026-09-27
# Authority basis: docs/operations/current/plans/day0_probability_repair_2026-09-25.md
#   (release test needs the all-cut population); correction design review
#   REQ-20260925-223704 §2 (complete cut capture, no IPW repair), §3 (ordered
#   family simplex), §10 (additive migration, idempotent immutable identities).
"""Complete global-auction learning corpus: every cut, every family simplex.

``tier0_candidate_set_provenance`` holds per-candidate rows for full-receipt
winner cuts only, so no-trade days contribute nothing and the calibration law
is unidentified. These tables record every cut, whatever its outcome:

- ``tier0_auction_cut``: one row per cut, with its status, universe counts,
  selection-policy identity and decision time. A cut that ended before its
  receipt carries an explicit status and reason and has no family rows.
- ``tier0_cut_family``: one row per (cut, family): the family's witness
  identity (raw 32-byte digest) and the state it saw. Integer keys keep this,
  the high-volume table, at about 56 bytes per row on disk.
- ``tier0_family_topology``: content-addressed bin/condition/token bindings in
  witness column order, with the native unit. Witness order is not settlement
  order; ``tier0_family_label`` carries the settlement order.
- ``tier0_family_snapshot``: content-addressed family state, which belongs to
  exactly one topology. It holds the raw YES simplex and one book row per
  topology column (``SNAPSHOT_BOOK_FIELDS``), plus the per-leg outcomes
  (``SNAPSHOT_LEG_FIELDS``, bin by column index). A state unchanged across
  cuts is stored once.
- ``tier0_family_label``: the verified settlement per topology, its settlement
  column order and ``label_available_at``. It is written by the post-trade fold.

Blobs are zstd-3 canonical JSON, and their ``payload_encoding`` names the
codec and field layout. Retention (post-trade job) evicts a topology's rows
only 30 days after its label became available; unlabelled rows are never
evicted.
"""

from __future__ import annotations

import sqlite3

CUT_ENCODING = "zstd3+canonical-json-tier0-cut-v1"
SNAPSHOT_ENCODING = "zstd3+canonical-json-tier0-family-snapshot-v1"
TOPOLOGY_ENCODING = "zstd3+canonical-json-tier0-family-topology-v1"
LABEL_ENCODING = "zstd3+canonical-json-tier0-family-label-v1"

# Field order of each ``book`` row (one per topology column) and ``legs`` row
# in a SNAPSHOT_ENCODING payload.
SNAPSHOT_BOOK_FIELDS = (
    "yes_status", "yes_bid", "yes_ask", "yes_quote_captured_at",
    "no_status", "no_bid", "no_ask", "no_quote_captured_at",
    "yes_mid", "yes_mid_unavailable_reason",
)
SNAPSHOT_LEG_FIELDS = (
    "column", "side", "action", "execution_mode",
    "status", "rejection_reason", "q_served",
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS tier0_auction_cut (
        cut_seq INTEGER PRIMARY KEY,
        cut_id TEXT NOT NULL UNIQUE,
        selection_epoch_identity TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('SELECTED', 'NO_TRADE', 'NO_CANDIDATES', 'INCOMPLETE')
        ),
        reason TEXT,
        decision_at_utc TEXT NOT NULL,
        selection_policy_identity TEXT NOT NULL,
        full_scope_family_count INTEGER NOT NULL,
        eligible_family_count INTEGER NOT NULL,
        candidate_count INTEGER NOT NULL,
        winner_candidate_id TEXT,
        decision_log_id INTEGER,
        payload_encoding TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        payload BLOB NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tier0_auction_cut_decision_at
        ON tier0_auction_cut (decision_at_utc)
    """,
    """
    CREATE TABLE IF NOT EXISTS tier0_family_topology (
        topology_seq INTEGER PRIMARY KEY,
        topology_id BLOB NOT NULL UNIQUE,
        family_key TEXT NOT NULL,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        metric TEXT NOT NULL,
        native_unit TEXT,
        bin_count INTEGER NOT NULL,
        payload_encoding TEXT NOT NULL,
        payload BLOB NOT NULL,
        first_seen_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tier0_family_snapshot (
        state_seq INTEGER PRIMARY KEY,
        family_state_id BLOB NOT NULL UNIQUE,
        topology_seq INTEGER NOT NULL,
        witness_kind TEXT NOT NULL,
        simplex_complete INTEGER NOT NULL CHECK (simplex_complete IN (0, 1)),
        market_reference_complete INTEGER NOT NULL
            CHECK (market_reference_complete IN (0, 1)),
        payload_encoding TEXT NOT NULL,
        payload BLOB NOT NULL,
        first_seen_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tier0_family_snapshot_topology
        ON tier0_family_snapshot (topology_seq)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tier0_family_snapshot_first_seen
        ON tier0_family_snapshot (first_seen_at_utc)
    """,
    """
    CREATE TABLE IF NOT EXISTS tier0_cut_family (
        cut_seq INTEGER NOT NULL,
        topology_seq INTEGER NOT NULL,
        state_seq INTEGER NOT NULL,
        probability_witness_identity BLOB NOT NULL,
        PRIMARY KEY (cut_seq, topology_seq)
    ) WITHOUT ROWID
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tier0_cut_family_topology
        ON tier0_cut_family (topology_seq, cut_seq)
    """,
    """
    CREATE TABLE IF NOT EXISTS tier0_family_label (
        topology_seq INTEGER PRIMARY KEY,
        settlement_value REAL NOT NULL,
        settlement_unit TEXT NOT NULL,
        winning_column INTEGER NOT NULL,
        payload_encoding TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        payload BLOB NOT NULL,
        label_available_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tier0_family_label_available
        ON tier0_family_label (label_available_at)
    """,
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create the corpus tables and indexes (idempotent, additive only)."""

    for ddl in _DDL:
        conn.execute(ddl)
