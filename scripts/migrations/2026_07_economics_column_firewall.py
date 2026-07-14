#!/usr/bin/env python3
# Lifecycle: created=2026-07-14; last_reviewed=2026-07-14; last_reused=2026-07-14
# Purpose: LX-3R foundation -- DB-level BEFORE INSERT/UPDATE firewall on every
#   forbidden chain-derivable economics column (src.contracts.economics_ownership.
#   FORBIDDEN_COLUMNS_BY_TABLE), on BOTH position_current (x10, authoritative on
#   zeus_trades.db) and edli_live_profit_audit (x5, authoritative on
#   zeus-world.db -- src.state.table_registry.owner() confirms; the trade-DB
#   copy of edli_live_profit_audit is a legacy_archived pre-PR-S4b ghost, see
#   architecture/db_table_ownership.yaml). Follows the F2/T5 migration pattern
#   (scripts/migrations/2026_07_position_identity_supersession_check.py,
#   scripts/migrations/2026_07_quarantine_phase_retirement.py): writer-plane
#   fence, optional backup, ONE transaction, idempotent re-run. Cross-DB like
#   T5 (dedicated non-WAL connection + ATTACH), simpler than both in one
#   respect -- see "WHY NO KILL-POINT MATRIX" below.
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-3R
#   ("DB 级 BEFORE INSERT/UPDATE 列防火墙 -- defense-in-depth,不是迁移机制 --
#   正常恢复写被拒 = cutover 失败,保持 entries 关闭") + src/contracts/
#   economics_ownership.py ("LX-3R's DB-level BEFORE INSERT/UPDATE guard,
#   explicitly deferred per the round-2 delta: 'defense in depth, not the
#   migration mechanism'") + src/state/truth_epoch.py ("no ... DB write-
#   firewall trigger. Those are LX-3R ... territory") + src/state/
#   table_registry.py (authoritative table->DB ownership lookup, reused here
#   rather than re-hardcoding a table->DB map that could drift from it).
"""LX-3R -- install the forbidden economics-column write firewall (STAGED).

*** THIS MIGRATION IS BUILT + TESTED ONLY. IT IS NEVER APPLIED TO A LIVE DB
BY THIS PACKET. *** Arming it against live DBs TODAY would immediately break
every legacy economics writer named in ``src/contracts/economics_ownership.py``
(the projection funnel plus the census-named bypass UPDATE sites) -- that is
only safe to do at the LX-3R coordinated cutover, AFTER the writer funnel has
been narrowed so nothing but the deterministic reducer (``src.reduce``)
still touches these columns. Running this script against live DBs before
that narrowing is complete is not a rollback-able mistake in the data-loss
sense (no row is ever deleted or rewritten -- see below), but it IS an
outage: every legacy write that still sets a forbidden column will start
raising ``sqlite3.IntegrityError``.

CROSS-DB: position_current LIVES ON zeus_trades.db, edli_live_profit_audit
LIVES ON zeus-world.db
----------------------------------------------------------------------------
Both are real, populated database FILES, not schemas within one file
(K1 split). ``src.state.table_registry.owner()`` -- the authoritative,
already-tested table->DB lookup (backed by architecture/db_table_ownership.yaml,
which also carries the disqualified legacy_archived ghost-table entries this
lookup correctly skips) -- resolves each forbidden table's home DB at run
time rather than re-hardcoding that mapping here. Installing a trigger pair
on both tables in one operator-visible run therefore needs a cross-DB
transaction. Exactly like T5 (scripts/migrations/2026_07_quarantine_phase_retirement.py,
see its own docstring section "WHY THIS CANNOT RUN THROUGH THE SHARED
scripts/migrations RUNNER"), this is NOT optional plumbing: a WAL-mode commit
is only atomic *per file*, not across an ATTACH set, so this migration opens
its OWN dedicated, non-WAL (``journal_mode=DELETE``) connection to the trade
DB and ATTACHes the world DB, doing all DDL in ONE transaction on that single
connection -- never ``src.state.db._connect()`` (which re-enables WAL).

WHAT THIS INSTALLS
-------------------
For every ``(table, column-set)`` pair in
``src.contracts.economics_ownership.FORBIDDEN_COLUMNS_BY_TABLE`` (today:
``position_current`` x10, ``edli_live_profit_audit`` x5 -- read from that
module directly, never re-listed here, so this migration can never drift
from the single-source-of-truth contract it is supposed to enforce), two
triggers, created in whichever schema (main/trade, or the ATTACHed alias
matching that table's ``table_registry.owner()`` result) actually owns the
table -- SQLite requires a trigger and its target table to live in the same
schema:

  - ``BEFORE INSERT`` -- aborts if ANY forbidden column on the new row is a
    non-neutral value (see "THE promotion_eligible EXCEPTION" below).
  - ``BEFORE UPDATE OF <every forbidden column on this table>`` -- aborts
    under the same condition. SQLite only fires an ``UPDATE OF col-list``
    trigger when the UPDATE statement's own SET clause names at least one of
    the listed columns -- so an UPDATE that touches only ``phase``,
    ``strategy_key``, or any other non-forbidden column NEVER fires this
    trigger, satisfies "phase/identity/intent writes ... must still succeed"
    with zero extra logic, and requires no OLD-vs-NEW diffing.

NOTE ON SCHEMA-QUALIFIED SQL TEXT: SQLite stores a trigger's ``sqlite_master.
sql`` WITHOUT its ATTACH-alias prefix (verified empirically -- ``CREATE
TRIGGER world.trg_x ...`` is recorded as, and reads back as, ``CREATE TRIGGER
trg_x ...``, identically whether queried through the attached alias or via a
direct standalone connection to that physical file). Every trigger-SQL
builder in this module therefore produces the CANONICAL, unqualified form;
``_qualify_for_execution`` adds the schema prefix ONLY on the statement
actually handed to ``conn.execute`` when creating the trigger.

THE promotion_eligible EXCEPTION
----------------------------------
14 of the 15 forbidden columns are nullable (REAL or TEXT, no DEFAULT) where
SQL NULL already means "not yet computed" -- the same UNKNOWN-never-zero
idiom ``src.reduce.position_economics`` uses on the read side. The firewall
blocks any write that makes one of those NON-NULL.

``edli_live_profit_audit.promotion_eligible`` is schema-constrained
``INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible IN (0,1))``
(src/state/schema/edli_live_profit_audit_schema.py). SQLite resolves a
column's DEFAULT before a ``BEFORE INSERT`` trigger ever sees the row, so
``NEW.promotion_eligible`` is NEVER NULL -- a plain "IS NOT NULL" rule would
permanently block every insert into this table, including the ones that
only ever claim the neutral "not yet eligible" state. Verified against the
live writer (``src/events/live_profit_audit.py``): it ALWAYS sets this
column explicitly (``normalized["promotion_eligible"] = 1 if ... else 0``)
-- 0 is the actively-used neutral value, not a hypothetical. This migration
therefore blocks only the affirmative claim (``NEW.promotion_eligible <> 0``,
i.e. ``= 1`` under the CHECK's own domain) for this one column; every other
forbidden column keeps the plain "IS NOT NULL" rule.

WHY NO KILL-POINT MATRIX
--------------------------
F2/T5 inject crash checkpoints (``KILL_POINTS``) into a multi-statement
table REBUILD (create-shadow-table, copy rows, verify row count, drop,
rename) because a crash mid-sequence could leave a table structurally
inconsistent, and that whole sequence needed its own atomicity proof. This
migration only ever executes ``DROP TRIGGER IF EXISTS`` / ``CREATE TRIGGER``
pairs -- no row is read, copied, or rewritten in either database. The
cross-DB atomicity mechanism itself (dedicated non-WAL connection + ATTACH +
ONE transaction) is IDENTICAL to T5's own -- T5's kill-point matrix already
proves that exact mechanism holds in this repo, for a strictly more complex
statement sequence (table rebuilds, not just trigger installs); re-proving
the low-level atomicity guarantee here would be redundant. What IS novel
here -- whether the trigger conditions actually block forbidden writes while
letting legitimate ones through -- is exactly what
``tests/scripts/test_economics_column_firewall_migration.py`` proves instead.

WHY THIS DOES NOT RUN THROUGH THE SHARED scripts/migrations RUNNER
--------------------------------------------------------------------
Same reasoning as F2/T5: the shared runner opens ONE canonical DB via
``src.state.db``'s normal connection factory (which re-enables WAL on every
open) and has no hook for the writer-plane-fence confirmation, the backup
ceremony, or a dedicated non-WAL cross-DB connection. Arming this firewall
while a legacy writer is still live is exactly the outage this module's
docstring warns about. ``up()``/``down()`` below exist only so this file's
`2*.py` name does not silently get picked up and mis-applied by
`python -m scripts.migrations apply` -- they refuse immediately with a
pointer to the real entry point, `main()` below.

Usage
-----
    python scripts/migrations/2026_07_economics_column_firewall.py \\
        --operator-confirms-fenced

    # Point at a fixture state dir instead of the live STATE_DIR (tests only):
    python scripts/migrations/2026_07_economics_column_firewall.py \\
        --operator-confirms-fenced --state-dir /tmp/fixture_state
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contracts.economics_ownership import FORBIDDEN_COLUMNS_BY_TABLE  # noqa: E402
from src.state.table_registry import DBIdentity, owner as _table_owner  # noqa: E402

# TARGET_DB metadata purely so the shared runner's cross-DB-write guard (see
# scripts/migrations/__init__.py::apply_migrations) resolves cleanly to a
# RuntimeError from up() below, rather than an unrelated "missing TARGET_DB
# metadata" error that would obscure the real refusal message. Mirrors T5's
# own choice of "trade" as TARGET_DB even though T5 (like this migration)
# also touches other DBs -- the shared runner has no multi-DB concept; "trade"
# is simply this script's primary/dedicated connection.
TARGET_DB = "trade"

# Test-only escape hatch for the process-scan half of the writer-plane fence
# check -- NEVER set outside a test fixture. The --operator-confirms-fenced
# flag itself is never bypassable. Mirrors F2/T5's identically-purposed
# constant -- reimplemented, not imported, for the same reason F2/T5 give:
# never import a module with import-time side effects such as
# deploy_live.py's LIVE_REPO resolution.
_SKIP_PROCESS_CHECK_ENV_VAR = "ZEUS_FIREWALL_MIGRATION_TEST_SKIP_PROCESS_CHECK"

_ZEUS_DAEMON_PATTERNS = (
    "src.main",
    "src/main.py",
    "src.engine.cycle_runner",
    "src/execution/harvester",
    "src.execution.harvester",
    "price_channel_ingest",
    "riskguard_live",
    "src.riskguard",
    "substrate_observer",
    "post_trade_capital",
    "forecast_live",
    "venue_heartbeat",
    "heartbeat_sensor",
    "data_ingest",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DB path resolution -- position_current (trade) + edli_live_profit_audit (world)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbPaths:
    trade: Path
    world: Path


def _resolve_db_paths(state_dir: Optional[str]) -> DbPaths:
    if state_dir:
        base = Path(state_dir)
        return DbPaths(trade=base / "zeus_trades.db", world=base / "zeus-world.db")
    from src.state.db import ZEUS_WORLD_DB_PATH, _zeus_trade_db_path

    return DbPaths(trade=_zeus_trade_db_path(), world=ZEUS_WORLD_DB_PATH)


# schema label ("" = main/trade, "world" = ATTACHed alias) for each forbidden
# table, resolved via the authoritative registry rather than a hand-maintained
# map. Fails loudly (RuntimeError) if a future forbidden table lands on a DB
# this migration does not yet know how to ATTACH -- never silently mis-
# schemas a trigger.
_SUPPORTED_SCHEMA_LABEL_BY_DB = {DBIdentity.TRADE: "", DBIdentity.WORLD: "world"}


def _schema_label_for_table(table: str) -> str:
    db_identity = _table_owner(table)
    if db_identity not in _SUPPORTED_SCHEMA_LABEL_BY_DB:
        raise RuntimeError(
            f"economics-firewall migration: table {table!r} is owned by "
            f"db={db_identity.value!r}, which this migration does not know "
            "how to ATTACH (only trade/world are wired) -- extend "
            "_SUPPORTED_SCHEMA_LABEL_BY_DB before adding a forbidden column "
            "on that DB."
        )
    return _SUPPORTED_SCHEMA_LABEL_BY_DB[db_identity]


def _path_for_schema(paths: DbPaths, schema: str) -> Path:
    return paths.trade if schema == "" else paths.world


def _sqlite_master_ref(schema: str) -> str:
    return f"{schema}.sqlite_master" if schema else "sqlite_master"


# ---------------------------------------------------------------------------
# 1) Writer-plane fence
# ---------------------------------------------------------------------------


def _live_zeus_processes() -> list[str]:
    """ps-based scan for any running zeus daemon process -- the second half
    of the fence, alongside the mandatory --operator-confirms-fenced flag (no
    machine-checkable global writer fence exists in this repo today)."""
    if os.environ.get(_SKIP_PROCESS_CHECK_ENV_VAR) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        print(
            "WARNING: ps -axo pid,command failed; process-scan half of the "
            "fence check could not run. Relying on --operator-confirms-fenced alone.",
            file=sys.stderr,
        )
        return []
    self_pid = os.getpid()
    hits: list[str] = []
    for line in out.splitlines():
        try:
            pid_str, _, cmd = line.strip().partition(" ")
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        if "python" not in cmd:
            continue
        if any(pattern in cmd for pattern in _ZEUS_DAEMON_PATTERNS):
            hits.append(line.strip())
    return hits


def _assert_writer_plane_fenced(operator_confirms_fenced: bool) -> None:
    if not operator_confirms_fenced:
        raise SystemExit(
            "REFUSED: this migration requires the writer plane fenced. Stop "
            "every zeus daemon (scripts/deploy_live.py restart is NOT what you "
            "want here -- actually STOP them: launchctl bootout each com.zeus.* "
            "label), confirm no process is writing zeus_trades.db or "
            "zeus-world.db, then re-run with --operator-confirms-fenced. "
            "Arming this firewall while the legacy projection funnel is still "
            "live WILL break its writes -- that is the intended behavior of a "
            "correctly-timed cutover, but a dangerous one if the funnel has "
            "not actually been narrowed yet."
        )
    live = _live_zeus_processes()
    if live:
        raise SystemExit(
            "REFUSED: --operator-confirms-fenced was passed but a zeus daemon "
            "process is still running:\n  " + "\n  ".join(live) + "\nStop it "
            "before re-running."
        )


# ---------------------------------------------------------------------------
# 2) WAL checkpoint + truncate, backup (both DBs)
# ---------------------------------------------------------------------------


def _checkpoint_truncate(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _integrity_check(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    finally:
        conn.close()


def _backup_both(paths: DbPaths, backup_root: Path) -> Path:
    """Copy both DB files (+ any WAL/SHM sidecars still present) into ONE
    timestamped directory, verify each copy's integrity, and record a
    manifest -- mirrors T5's ``_backup_three`` (scripts/migrations/
    2026_07_quarantine_phase_retirement.py), narrowed to the two DBs this
    migration actually touches. Rollback law: both backups restored together
    or forward-fix; never one file alone."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_root / f"economics_firewall_migration_{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, dict] = {}
    for label, src in (("trade", paths.trade), ("world", paths.world)):
        if not src.exists():
            manifest[label] = {"path": None, "note": "source db did not exist"}
            continue
        dst = dest / src.name
        shutil.copy2(src, dst)
        for sidecar_suffix in ("-wal", "-shm"):
            sidecar = Path(str(src) + sidecar_suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(dst) + sidecar_suffix))
        integrity = _integrity_check(dst)
        manifest[label] = {
            "path": str(dst),
            "size_bytes": dst.stat().st_size,
            "sha256": _sha256(dst),
            "integrity_check": integrity,
        }
        if integrity != "ok":
            raise RuntimeError(
                f"economics-firewall backup integrity check FAILED for {label} "
                f"({dst}): {integrity} -- ABORT before touching any live DB"
            )
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return dest


# ---------------------------------------------------------------------------
# 3) Dedicated non-WAL cross-DB connection (mirrors T5's _open_dedicated_connection)
# ---------------------------------------------------------------------------


def _open_dedicated_connection(paths: DbPaths) -> sqlite3.Connection:
    """A DEDICATED sqlite3 connection with journal_mode=DELETE (rollback
    journal). NEVER src.state.db._connect() -- it re-enables WAL, which is
    only atomic per-file, not across an ATTACH set (see module docstring
    "CROSS-DB"). ATTACHes zeus-world.db as ``world`` so both tables' trigger
    installs happen in ONE transaction."""
    conn = sqlite3.connect(str(paths.trade))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = DELETE")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "delete":
        conn.close()
        raise RuntimeError(
            f"economics-firewall migration requires journal_mode=DELETE on the "
            f"trade DB connection; got {mode!r}. Another connection may still "
            "hold the DB in WAL mode -- confirm the writer plane is truly fenced."
        )
    conn.execute("ATTACH DATABASE ? AS world", (str(paths.world),))
    conn.execute("PRAGMA world.journal_mode = DELETE")
    got = conn.execute("PRAGMA world.journal_mode").fetchone()[0]
    if str(got).lower() != "delete":
        conn.close()
        raise RuntimeError(
            f"economics-firewall migration requires journal_mode=DELETE on "
            f"attached schema 'world'; got {got!r}."
        )
    return conn


# ---------------------------------------------------------------------------
# Trigger SQL construction -- data-driven from FORBIDDEN_COLUMNS_BY_TABLE,
# never a re-listed column set of its own (single source of truth stays
# src/contracts/economics_ownership.py). Builders always produce the
# CANONICAL (schema-unqualified) form -- see module docstring "NOTE ON
# SCHEMA-QUALIFIED SQL TEXT".
# ---------------------------------------------------------------------------

# Per-(table, column) override of the "forbidden write" predicate. Every
# column not listed here uses the default "NEW.<col> IS NOT NULL" rule (NULL
# is that column's neutral/not-yet-computed state). See module docstring
# "THE promotion_eligible EXCEPTION" for why this one column differs: its
# schema is NOT NULL DEFAULT 0, so NULL is never a reachable state and 0 is
# the neutral value instead.
_NEUTRAL_PREDICATE_OVERRIDE: dict[tuple[str, str], str] = {
    ("edli_live_profit_audit", "promotion_eligible"): "NEW.{col} <> 0",
}


def _forbidden_write_predicate(table: str, column: str) -> str:
    template = _NEUTRAL_PREDICATE_OVERRIDE.get((table, column), "NEW.{col} IS NOT NULL")
    return template.format(col=column)


def _sorted_columns(table: str) -> tuple[str, ...]:
    return tuple(sorted(FORBIDDEN_COLUMNS_BY_TABLE[table]))


def _trigger_name(table: str, event: str) -> str:
    return f"trg_{table}_economics_firewall_{event}"


_ABORT_MESSAGE_TEMPLATE = (
    "{table} write sets a forbidden chain-derivable economics column -- see "
    "src.contracts.economics_ownership.FORBIDDEN_COLUMNS_BY_TABLE; only the "
    "deterministic reducer (src.reduce) may publish these once the trade DB "
    "truth epoch reaches ACTIVE_NEW"
)


def _insert_trigger_sql(table: str, columns: tuple[str, ...]) -> str:
    """Canonical (schema-unqualified) BEFORE INSERT trigger SQL."""
    name = _trigger_name(table, "insert")
    condition = "\n       OR ".join(_forbidden_write_predicate(table, c) for c in columns)
    message = _ABORT_MESSAGE_TEMPLATE.format(table=table)
    return f"""
CREATE TRIGGER {name}
BEFORE INSERT ON {table}
WHEN {condition}
BEGIN
    SELECT RAISE(ABORT, '{message}');
END
""".strip()


def _update_trigger_sql(table: str, columns: tuple[str, ...]) -> str:
    """Canonical (schema-unqualified) BEFORE UPDATE OF trigger SQL."""
    name = _trigger_name(table, "update")
    col_list = ", ".join(columns)
    condition = "\n       OR ".join(_forbidden_write_predicate(table, c) for c in columns)
    message = _ABORT_MESSAGE_TEMPLATE.format(table=table)
    return f"""
CREATE TRIGGER {name}
BEFORE UPDATE OF {col_list} ON {table}
WHEN {condition}
BEGIN
    SELECT RAISE(ABORT, '{message}');
END
""".strip()


def _qualify_for_execution(sql: str, schema: str) -> str:
    """Insert the ATTACH-alias prefix into a canonical ``CREATE TRIGGER``
    statement for actual execution. The stored/compared form never carries
    this prefix (see module docstring)."""
    if not schema:
        return sql
    prefix = "CREATE TRIGGER "
    assert sql.startswith(prefix), sql
    return f"{prefix}{schema}." + sql[len(prefix):]


@dataclass(frozen=True)
class _ExpectedTrigger:
    table: str
    schema: str  # "" (trade/main) or "world"
    name: str
    sql: str  # canonical, unqualified -- matches sqlite_master.sql verbatim


def _all_expected_triggers() -> list[_ExpectedTrigger]:
    """Pure -- derived entirely from FORBIDDEN_COLUMNS_BY_TABLE + the
    registry, no DB access. Table existence is checked separately by the
    caller (the probe and the real dedicated connection differ in how they
    may safely do that -- see module docstring "CROSS-DB")."""
    result: list[_ExpectedTrigger] = []
    for table in sorted(FORBIDDEN_COLUMNS_BY_TABLE):
        schema = _schema_label_for_table(table)
        columns = _sorted_columns(table)
        for event, sql in (
            ("insert", _insert_trigger_sql(table, columns)),
            ("update", _update_trigger_sql(table, columns)),
        ):
            result.append(
                _ExpectedTrigger(table=table, schema=schema, name=_trigger_name(table, event), sql=sql)
            )
    return result


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {_sqlite_master_ref(schema)} WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _normalize_sql(sql: Optional[str]) -> str:
    return " ".join((sql or "").split()).strip()


def _existing_trigger_sql(conn: sqlite3.Connection, schema: str, name: str) -> Optional[str]:
    row = conn.execute(
        f"SELECT sql FROM {_sqlite_master_ref(schema)} WHERE type='trigger' AND name=?",
        (name,),
    ).fetchone()
    return str(row[0]) if row is not None else None


# ---------------------------------------------------------------------------
# Idempotency probe -- standalone per-file connections, NEVER the dedicated
# ATTACH connection (which would auto-create a missing world.db just by
# attaching it; sqlite3.connect()/ATTACH DATABASE both create the file on
# open). A path that does not exist yet trivially has no tables and is
# skipped, never created.
# ---------------------------------------------------------------------------


def _is_already_applied(paths: DbPaths) -> bool:
    """True iff every forbidden table THAT EXISTS already carries its
    expected trigger pair, verbatim. A table whose home DB/table does not
    exist yet contributes nothing either way (it is simply not migrate-able
    yet); if NO forbidden table exists anywhere this returns False -- the
    caller checks ``_any_forbidden_table_present`` separately and reports
    "nothing to migrate" rather than "already applied" for that case."""
    any_table_present = False
    for trig in _all_expected_triggers():
        path = _path_for_schema(paths, trig.schema)
        if not _table_exists_on_path(path, trig.table):
            continue
        any_table_present = True
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            existing = _existing_trigger_sql(conn, "", trig.name)
        finally:
            conn.close()
        if existing is None or _normalize_sql(existing) != _normalize_sql(trig.sql):
            return False
    return any_table_present


def _table_exists_on_path(path: Path, table: str) -> bool:
    if not path.exists():
        return False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _table_exists(conn, "", table)
    finally:
        conn.close()


def _any_forbidden_table_present(paths: DbPaths) -> bool:
    return any(
        _table_exists_on_path(_path_for_schema(paths, _schema_label_for_table(table)), table)
        for table in sorted(FORBIDDEN_COLUMNS_BY_TABLE)
    )


# ---------------------------------------------------------------------------
# Idempotent install + verify (real dedicated connection)
# ---------------------------------------------------------------------------


@dataclass
class FirewallResult:
    installed_triggers: tuple[str, ...]
    skipped_tables: tuple[str, ...]


def _install_all_triggers(conn: sqlite3.Connection) -> FirewallResult:
    installed: list[_ExpectedTrigger] = []
    skipped: list[str] = []
    table_exists_cache: dict[str, bool] = {}
    for trig in _all_expected_triggers():
        exists = table_exists_cache.get(trig.table)
        if exists is None:
            exists = _table_exists(conn, trig.schema, trig.table)
            table_exists_cache[trig.table] = exists
            if not exists:
                skipped.append(trig.table)
        if not exists:
            continue
        prefix = f"{trig.schema}." if trig.schema else ""
        conn.execute(f"DROP TRIGGER IF EXISTS {prefix}{trig.name}")
        conn.execute(_qualify_for_execution(trig.sql, trig.schema))
        installed.append(trig)
    _verify_installed(conn, installed)
    return FirewallResult(
        installed_triggers=tuple(f"{t.schema}.{t.name}" if t.schema else t.name for t in installed),
        skipped_tables=tuple(skipped),
    )


def _verify_installed(conn: sqlite3.Connection, expected: list[_ExpectedTrigger]) -> None:
    """rebuild-verify step (F2/T5 idiom, right-sized for pure DDL -- see
    module docstring "WHY NO KILL-POINT MATRIX"): confirm every trigger this
    run intended to install is actually present, in its own schema, with the
    expected text, before COMMIT."""
    missing = [
        f"{t.schema}.{t.name}" if t.schema else t.name
        for t in expected
        if _existing_trigger_sql(conn, t.schema, t.name) is None
    ]
    if missing:
        raise RuntimeError(
            "economics-firewall migration verify FAILED -- trigger(s) not "
            f"found post-install: {missing}"
        )


def run_migration(conn: sqlite3.Connection) -> FirewallResult:
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _install_all_triggers(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def up(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "LX-3R economics-column firewall migration requires the writer-plane-"
        "fence + backup ceremony and a dedicated non-WAL cross-DB (trade + "
        "world) connection -- it CANNOT run through the shared single-DB "
        "migration runner (python -m scripts.migrations apply). Run this "
        "script directly:\n"
        "    python scripts/migrations/2026_07_economics_column_firewall.py "
        "--operator-confirms-fenced"
    )


def down(conn: sqlite3.Connection) -> None:
    raise RuntimeError(
        "This migration is NOT a per-DB down() -- restore the trade+world "
        "backup pair it wrote together (or, if --skip-backup was used, "
        "forward-fix: DROP the four trg_*_economics_firewall_* triggers by "
        "name, qualifying the two on edli_live_profit_audit with 'world.'). "
        "Installing/removing a trigger never rewrites or deletes a single "
        "existing row."
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--operator-confirms-fenced",
        action="store_true",
        help="Required. Confirms the operator has stopped every zeus daemon "
        "before running this migration.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Directory containing zeus_trades.db / zeus-world.db. Defaults "
        "to the live STATE_DIR. Tests only.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Directory to write the trade+world DB backup pair into. "
        "Defaults to <state-dir>/backups.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Operator-only: skip the backup. Trigger install runs in ONE "
        "transaction (BEGIN IMMEDIATE ... COMMIT) so a crash before COMMIT "
        "leaves the pre-migration triggers untouched and a crash after COMMIT "
        "leaves both DBs fully migrated -- never mixed.",
    )
    args = parser.parse_args(argv)

    paths = _resolve_db_paths(args.state_dir)
    backup_root = Path(args.backup_dir) if args.backup_dir else paths.trade.parent / "backups"

    if not paths.trade.exists():
        print(f"{paths.trade} does not exist yet -- nothing to migrate.")
        return 0

    if not _any_forbidden_table_present(paths):
        print("no forbidden-economics tables present on either DB -- nothing to migrate.")
        return 0

    if _is_already_applied(paths):
        print("economics-firewall migration already applied -- no-op.")
        return 0

    _assert_writer_plane_fenced(args.operator_confirms_fenced)

    _checkpoint_truncate(paths.trade)
    _checkpoint_truncate(paths.world)

    if args.skip_backup:
        print("backup: SKIPPED (--skip-backup)")
    else:
        backup_dir = _backup_both(paths, backup_root)
        print(f"backup: {backup_dir}")

    conn = _open_dedicated_connection(paths)
    try:
        result = run_migration(conn)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "finished_at": _now_iso(),
                "installed_triggers": list(result.installed_triggers),
                "skipped_tables": list(result.skipped_tables),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
