# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: db-root-mechanism design (wf_4acdc7d5) — Owner-Routed Writes;
#   atlas §6. Generalizes _is_verified_trade_connection (db.py:7832) to every domain.
"""Root-mechanism antibody: assert_owner_conn makes a wrong-DB write structurally impossible.

The inversion root (8 unbound write sites: log_execution_fact db.py:9050, append_many_and_project
ledger.py:613, log_selection_*_fact, forward-market, ...) is a bare `INSERT INTO <table>` executed on a
passed connection whose MAIN is NOT guaranteed to be the table's owner — SQLite resolves the bare name to
whatever file the conn is rooted in, and because ghost copies exist in multiple DBs, an inverted conn
silently writes the ghost. assert_owner_conn is the runtime guard: a write to a table on a connection whose
MAIN is a non-owning file (and the owner is not ATTACHed) fail-closes. Ownership comes from the single
source src/state/domains.py; the guard compares by DB FILENAME so it also catches the hyphen/underscore
naming-schism decoys.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


def _conn_named(dirpath: str, filename: str) -> sqlite3.Connection:
    c = sqlite3.connect(str(Path(dirpath) / filename))
    c.row_factory = sqlite3.Row
    return c


def test_wrong_db_write_raises() -> None:
    from src.state import domains
    from src.state.owner_routed_write import assert_owner_conn, WrongDomainWrite

    assert domains.owner_domain("execution_fact") is domains.Domain.TRADE
    with tempfile.TemporaryDirectory() as d:
        wc = _conn_named(d, "zeus-world.db")  # conn rooted at WORLD, writing a TRADE-owned table
        with pytest.raises(WrongDomainWrite):
            assert_owner_conn(wc, "execution_fact")
        wc.close()


def test_owner_main_passes() -> None:
    from src.state.owner_routed_write import assert_owner_conn

    with tempfile.TemporaryDirectory() as d:
        tc = _conn_named(d, "zeus_trades.db")  # MAIN IS the owner
        assert_owner_conn(tc, "execution_fact")
        tc.close()


def test_owner_attached_passes() -> None:
    from src.state.owner_routed_write import assert_owner_conn

    with tempfile.TemporaryDirectory() as d:
        wc = _conn_named(d, "zeus-world.db")
        wc.execute(f"ATTACH DATABASE '{Path(d) / 'zeus_trades.db'}' AS trades")  # owner ATTACHed
        assert_owner_conn(wc, "execution_fact")  # caller will schema-qualify to trades.
        wc.close()


def test_unknown_table_fails_open() -> None:
    from src.state.owner_routed_write import assert_owner_conn

    with tempfile.TemporaryDirectory() as d:
        wc = _conn_named(d, "zeus-world.db")
        assert_owner_conn(wc, "some_table_not_owned_by_the_kernel")  # not owned -> no raise
        wc.close()


def test_naming_schism_decoy_raises() -> None:
    # A conn on the DECOY zeus-trades.db (hyphen) writing a trade table -> raise (filename mismatch).
    from src.state.owner_routed_write import assert_owner_conn, WrongDomainWrite

    with tempfile.TemporaryDirectory() as d:
        dc = _conn_named(d, "zeus-trades.db")  # WRONG separator (owner is zeus_trades.db)
        with pytest.raises(WrongDomainWrite):
            assert_owner_conn(dc, "execution_fact")
        dc.close()
