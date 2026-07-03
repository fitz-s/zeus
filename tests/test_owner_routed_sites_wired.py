# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: db-root-mechanism (wf_4acdc7d5) Owner-Routed Writes; atlas §6C.
"""INV-OWNER-WRITE-1: the 8 formerly-unbound bare-write sites route through the ownership kernel.

Each site that used to `INSERT/UPDATE INTO <bare_table>` on "whatever connection it got" (silently writing a
ghost in the wrong DB — the inversion root) now resolves its target through src/state/owner_routed_write.py
(require_owner_main / owner_write_target / owner_qualified_name) or the owner-aware _selection_fact_table_ref.
This antibody pins that wiring: reverting any site to a bare write drops its guard substring and fails CI —
the ratchet that keeps the Owner-Routed Writes mechanism from eroding back into ownership drift.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# (relative file under src/, substring that proves this site is owner-routed)
_WIRED_SITES: list[tuple[str, str]] = [
    ("state/no_trade_events.py", 'owner_qualified_name(conn, "no_trade_events")'),
    ("state/db.py", 'require_owner_main(conn, "execution_fact")'),
    ("state/db.py", 'require_owner_main(conn, "outcome_fact")'),
    ("state/ledger.py", 'require_owner_main(conn, "position_events")'),
    ("state/db.py", 'owner_write_target(conn, "market_price_history")'),
    ("events/triggers/market_channel_ingestor.py", 'owner_write_target(conn, "execution_feasibility_evidence")'),
    # selection_family_fact + selection_hypothesis_fact both route through the owner-aware fallback in
    # _selection_fact_table_ref, keyed on the canonical-DB filename set:
    ("state/db.py", "_KNOWN_DB_FILENAMES"),
]


def test_all_unbound_write_sites_are_owner_routed() -> None:
    regressed: list[str] = []
    for rel, needle in _WIRED_SITES:
        text = (_SRC / rel).read_text(encoding="utf-8")
        if needle not in text:
            regressed.append(f"{rel}: missing {needle!r}")
    assert not regressed, (
        "Owner-Routed Writes regressed — a formerly-wired site lost its ownership guard and can now write a "
        "ghost in the wrong DB:\n  " + "\n  ".join(regressed)
    )
