# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md §KEEP-spine
#   完备性补遗 ("归属图+歧义证据 — foreign/ambiguous 留 observation 不丢") + the
#   external wave-1 review's T4 foreign-fill blocker + the local wave-1 verifier's
#   divergence note (rated MINOR because get_trades() re-serves history today —
#   but provider history visibility is not guaranteed forever, and the
#   adjudication's KEEP-spine requires durable retention regardless).

"""Schema owner for wallet_fill_observations — the durable, append-only,
wallet-level (shared-wallet) fill observation log (packet I, wave-1.5).

``src.ingest.fill_synchronizer`` sweeps ``get_trades()`` for the WHOLE wallet
(shared with the operator's manual co-trading — census_chain_sources.md).
Today it attributes each raw trade via the order_id -> venue_commands join and
appends ONLY the Zeus-attributed half to ``venue_trade_facts`` (which
structurally requires a non-empty ``command_id``); a fill whose order_id does
not resolve to a local command is counted (``foreign_fill_count``) and
DROPPED — never durably retained anywhere. This table closes that gap: every
swept fill, Zeus-attributed or not, lands here FIRST, before the existing
Zeus-attributed lane runs. Mirrors the ``payout_observations_schema.py`` shape
(bare-``conn`` ``ensure_table``, idempotent, trade-DB-owned).

``disposition`` is one of:
  - ZEUS_ATTRIBUTED: the trade's order_id resolved to a local venue_commands
    row (this fill also lands in venue_trade_facts via the existing lane).
  - FOREIGN: the trade carried at least one order_id, and none resolved to a
    local command (operator co-trading on the shared wallet).
  - AMBIGUOUS: attribution could not even be attempted (no order_id candidate
    on the raw trade at all) — neither confirmed Zeus nor confirmed foreign.

Disposition is written ONCE, at observation time, from the order_id join
available then. A later attribution correction (e.g. an alias-graph fix
resolves a previously-FOREIGN trade to a Zeus command after the fact) never
edits the original row — it appends a NEW superseding row and sets the prior
row's ``superseded_by`` pointer, exactly the payout_observations pattern: a
one-time NULL -> non-NULL transition, enforced by
``wallet_fill_observations_guarded_update`` (BEFORE UPDATE trigger); every
other column is frozen once written. Rows are never deleted, enforced by
``wallet_fill_observations_no_delete`` (BEFORE DELETE trigger).

Idempotent on (trade_id, raw_payload_hash): re-sweeping the same venue
response must not duplicate the observation. The synchronizer checks before
insert (mirrors ``_fact_already_recorded`` in fill_synchronizer.py); the
UNIQUE index below is defense-in-depth at the DB level, not the primary
enforcement mechanism.

``token_id``/``side``/``size``/``price``/``fee_rate_bps``/``fee_paid_micro``/
``tx_hash``/``venue_timestamp`` are best-effort extractions from the raw
venue payload ("as available" — the LX packet spec's own phrasing): a field
absent on a given venue response is simply NULL here, never fabricated.
``raw_payload_json`` retains the complete raw trade regardless, so no
information is lost even when a convenience column comes back NULL.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

TABLE_NAME = "wallet_fill_observations"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wallet_fill_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            TEXT NOT NULL,
    order_ids           TEXT NOT NULL DEFAULT '[]',
    token_id            TEXT,
    side                TEXT,
    size                TEXT,
    price               TEXT,
    fee_rate_bps        INTEGER,
    fee_paid_micro      INTEGER,
    tx_hash             TEXT,
    venue_timestamp     TEXT,
    observed_at         TEXT NOT NULL,
    raw_payload_hash    TEXT NOT NULL,
    raw_payload_json    TEXT,
    disposition         TEXT NOT NULL CHECK (disposition IN (
        'ZEUS_ATTRIBUTED', 'FOREIGN', 'AMBIGUOUS'
    )),
    superseded_by       INTEGER REFERENCES wallet_fill_observations(id)
)
"""

# Idempotency defense-in-depth (see module docstring: the synchronizer's
# check-before-insert is the primary mechanism; this index makes a duplicate
# INSERT impossible even if a caller bypasses that check).
CREATE_IDEMPOTENCY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_fill_observations_idempotent
    ON wallet_fill_observations(trade_id, raw_payload_hash)
"""

CREATE_TRADE_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wallet_fill_observations_trade
    ON wallet_fill_observations(trade_id, id)
"""

CREATE_DISPOSITION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wallet_fill_observations_disposition
    ON wallet_fill_observations(disposition, observed_at)
"""

# Only a one-time NULL -> non-NULL superseded_by transition is legal; every
# other column is frozen once written (payout_observations_schema.py precedent).
CREATE_GUARDED_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS wallet_fill_observations_guarded_update
BEFORE UPDATE ON wallet_fill_observations
FOR EACH ROW
WHEN NOT (
    OLD.superseded_by IS NULL
    AND NEW.superseded_by IS NOT NULL
    AND NEW.trade_id IS OLD.trade_id
    AND NEW.order_ids IS OLD.order_ids
    AND NEW.token_id IS OLD.token_id
    AND NEW.side IS OLD.side
    AND NEW.size IS OLD.size
    AND NEW.price IS OLD.price
    AND NEW.fee_rate_bps IS OLD.fee_rate_bps
    AND NEW.fee_paid_micro IS OLD.fee_paid_micro
    AND NEW.tx_hash IS OLD.tx_hash
    AND NEW.venue_timestamp IS OLD.venue_timestamp
    AND NEW.observed_at = OLD.observed_at
    AND NEW.raw_payload_hash = OLD.raw_payload_hash
    AND NEW.raw_payload_json IS OLD.raw_payload_json
    AND NEW.disposition IS OLD.disposition
)
BEGIN
    SELECT RAISE(ABORT, 'wallet_fill_observations rows are immutable except a one-time superseded_by transition');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS wallet_fill_observations_no_delete
BEFORE DELETE ON wallet_fill_observations
BEGIN
    SELECT RAISE(ABORT, 'wallet_fill_observations is append-only (delete forbidden)');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for wallet_fill_observations. Callable against any trade-DB conn.

    INV-37: caller supplies conn; never auto-opens.
    """

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_IDEMPOTENCY_INDEX_SQL)
    conn.execute(CREATE_TRADE_LOOKUP_INDEX_SQL)
    conn.execute(CREATE_DISPOSITION_INDEX_SQL)
    conn.execute(CREATE_GUARDED_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
