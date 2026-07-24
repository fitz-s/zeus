#!/usr/bin/env python3
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: crash-atomic repair of a bounded, monitor-only corrupt tail in the
#   canonical trade DB position_events B-tree.
# Authority basis: AGENTS.md append-first truth law and the 2026-07-24 live
#   position_events corruption incident (table-scoped integrity evidence).
"""Repair a narrowly fingerprinted corrupt ``position_events`` tail.

The incident this command admits has three independent facts:

* the table B-tree has a dense readable prefix;
* the table's covering indexes expose a short, unreadable tail; and
* every tail row is ``MONITOR_REFRESHED`` evidence, never a money side effect.

The command refuses any other shape.  It defaults to inspection only.  ``--apply``
requires an explicit all-writer fence, no open DB handles, the approved SQLite
source build, and the pinned live schema.  The rebuild is one WAL transaction:
copy the readable prefix with preserved rowids, verify a full-row digest, drop the
corrupt table, rename the replacement, recreate indexes/triggers, and commit.
Before commit, a crash leaves the original table intact.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


TABLE = "position_events"
REPLACEMENT = "position_events_recovered_20260724"
EXPECTED_SCHEMA_SHA256 = (
    "74608320b868e427715ad6513c63fd4f97da2dfe3219dcae8897dff444b32b50"
)
APPROVED_SQLITE_SOURCE_IDS = {
    "2026-06-03 19:12:13 "
    "d6e03d8c777cfa2d35e3b60d8ec3e0187f3e9f99d8e2ee9cac695fd6fcdf1a24"
}
SAFE_LOST_EVENT_TYPES = frozenset({"MONITOR_REFRESHED"})
SAFE_EVENT_ID_MARKERS = (":monitor_refreshed:", ":market_closed_hold:")
REQUIRED_DISABLED_LABELS = (
    "com.zeus.live-trading",
    "com.zeus.price-channel-ingest",
    "com.zeus.post-trade-capital",
    "com.zeus.substrate-observer",
    "com.zeus.riskguard-live",
)
_KILL_ENV = "ZEUS_POSITION_EVENTS_REPAIR_KILL_AT"
_FIXTURE_ENV = "ZEUS_POSITION_EVENTS_REPAIR_ALLOW_FIXTURE_SCHEMA"
_SKIP_FENCE_ENV = "ZEUS_POSITION_EVENTS_REPAIR_SKIP_FENCE"


@dataclass(frozen=True)
class LostIndexRow:
    rowid: int
    event_id: str
    position_id: str
    sequence_no: int
    event_type: str


@dataclass(frozen=True)
class Inspection:
    db_path: str
    sqlite_source_id: str
    schema_sha256: str
    indexed_count: int
    indexed_max_rowid: int
    range_copy_end_rowid: int
    point_copy_rowids: tuple[int, ...]
    last_readable_rowid: int
    readable_prefix_count: int
    lost_index_rows: tuple[LostIndexRow, ...]

    @property
    def lost_count(self) -> int:
        return len(self.lost_index_rows)


def _fixture_schema_allowed() -> bool:
    return (
        os.environ.get(_FIXTURE_ENV) == "1"
        and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _maybe_crash(point: str) -> None:
    if os.environ.get(_KILL_ENV) == point:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(91)


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    mode = "ro" if read_only else "rw"
    conn = sqlite3.connect(
        f"file:{path.resolve()}?mode={mode}",
        uri=True,
        timeout=0.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=0")
    if read_only:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _schema(conn: sqlite3.Connection) -> tuple[str, tuple[str, ...]]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    if row is None or not row["sql"]:
        raise RuntimeError(f"{TABLE} table is missing")
    ddl = str(row["sql"])
    columns = tuple(str(r["name"]) for r in conn.execute(f"PRAGMA table_info({TABLE})"))
    if not columns:
        raise RuntimeError(f"{TABLE} has no columns")
    return ddl, columns


def _assert_runtime_identity(
    conn: sqlite3.Connection, ddl: str
) -> tuple[str, str]:
    source_id = str(conn.execute("SELECT sqlite_source_id()").fetchone()[0])
    if source_id not in APPROVED_SQLITE_SOURCE_IDS:
        raise RuntimeError(
            "REFUSED: SQLite source build is not approved for this repair: "
            f"{source_id}"
        )
    schema_sha = hashlib.sha256(ddl.encode()).hexdigest()
    if schema_sha != EXPECTED_SCHEMA_SHA256 and not _fixture_schema_allowed():
        raise RuntimeError(
            "REFUSED: position_events schema drifted; "
            f"expected={EXPECTED_SCHEMA_SHA256} actual={schema_sha}"
        )
    return source_id, schema_sha


def _indexed_tail(
    conn: sqlite3.Connection, start: int, end: int
) -> tuple[LostIndexRow, ...]:
    event_ids = {
        int(row["rowid"]): str(row["event_id"])
        for row in conn.execute(
            """
            SELECT rowid, event_id
              FROM position_events INDEXED BY sqlite_autoindex_position_events_1
             WHERE rowid BETWEEN ? AND ?
             ORDER BY rowid
            """,
            (start, end),
        )
    }
    identities = {
        int(row["rowid"]): (str(row["position_id"]), int(row["sequence_no"]))
        for row in conn.execute(
            """
            SELECT rowid, position_id, sequence_no
              FROM position_events INDEXED BY sqlite_autoindex_position_events_3
             WHERE rowid BETWEEN ? AND ?
             ORDER BY rowid
            """,
            (start, end),
        )
    }
    event_types = {
        int(row["rowid"]): str(row["event_type"])
        for row in conn.execute(
            """
            SELECT rowid, event_type
              FROM position_events INDEXED BY idx_position_events_position_type_sequence
             WHERE rowid BETWEEN ? AND ?
             ORDER BY rowid
            """,
            (start, end),
        )
    }
    expected = set(range(start, end + 1))
    if set(event_ids) != expected or set(identities) != expected or set(event_types) != expected:
        raise RuntimeError(
            "REFUSED: covering indexes disagree on the corrupt tail identity"
        )
    return tuple(
        LostIndexRow(
            rowid=rowid,
            event_id=event_ids[rowid],
            position_id=identities[rowid][0],
            sequence_no=identities[rowid][1],
            event_type=event_types[rowid],
        )
        for rowid in range(start, end + 1)
    )


def inspect(path: Path, *, max_orphan_tail: int = 64) -> Inspection:
    if not path.is_file():
        raise RuntimeError(f"REFUSED: DB path is not a file: {path}")
    conn = _connect(path, read_only=True)
    try:
        ddl, _columns = _schema(conn)
        source_id, schema_sha = _assert_runtime_identity(conn, ddl)
        indexed = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS max_rowid
              FROM position_events INDEXED BY sqlite_autoindex_position_events_1
            """
        ).fetchone()
        indexed_count = int(indexed["n"])
        indexed_max = int(indexed["max_rowid"])
        if indexed_count <= 0 or indexed_count != indexed_max:
            raise RuntimeError(
                "REFUSED: position_events indexed rowids are not dense from 1"
            )

        last_readable = 0
        for rowid in range(indexed_max, max(-1, indexed_max - max_orphan_tail - 1), -1):
            try:
                row = conn.execute(
                    "SELECT rowid FROM position_events NOT INDEXED WHERE rowid=?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if row is not None:
                last_readable = int(row["rowid"])
                break
        if last_readable <= 0 or last_readable >= indexed_max:
            raise RuntimeError(
                "REFUSED: no bounded unreadable indexed tail was found"
            )

        lost_count = indexed_max - last_readable
        if lost_count > max_orphan_tail:
            raise RuntimeError(
                f"REFUSED: corrupt tail exceeds bound {max_orphan_tail}: {lost_count}"
            )
        range_end = last_readable
        prefix_count = 0
        while range_end > max(0, indexed_max - max_orphan_tail):
            try:
                prefix = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                      FROM position_events NOT INDEXED
                     WHERE rowid BETWEEN 1 AND ?
                    """,
                    (range_end,),
                ).fetchone()
            except sqlite3.DatabaseError:
                range_end -= 1
                continue
            prefix_count = int(prefix["n"])
            if prefix_count != range_end:
                raise RuntimeError(
                    "REFUSED: range-readable position_events prefix is not dense"
                )
            break
        if range_end <= 0 or prefix_count != range_end:
            raise RuntimeError("REFUSED: no bounded range-readable prefix was found")
        point_rows = tuple(range(range_end + 1, last_readable + 1))
        for rowid in point_rows:
            try:
                direct = conn.execute(
                    "SELECT rowid FROM position_events NOT INDEXED WHERE rowid=?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise RuntimeError(
                    f"REFUSED: point-readable bridge row {rowid} failed"
                ) from exc
            if direct is None or int(direct["rowid"]) != rowid:
                raise RuntimeError(
                    f"REFUSED: point-readable bridge row {rowid} is missing"
                )
        preserved_count = range_end + len(point_rows)
        if preserved_count != last_readable:
            raise RuntimeError("REFUSED: readable prefix and point bridge are not dense")
        lost = _indexed_tail(conn, last_readable + 1, indexed_max)
        unsafe = [
            row
            for row in lost
            if row.event_type not in SAFE_LOST_EVENT_TYPES
            or not any(marker in row.event_id for marker in SAFE_EVENT_ID_MARKERS)
        ]
        if unsafe:
            raise RuntimeError(
                "REFUSED: corrupt tail contains non-monitor or unrecognized events: "
                + json.dumps([asdict(row) for row in unsafe], sort_keys=True)
            )
        for row in lost:
            try:
                direct = conn.execute(
                    "SELECT rowid FROM position_events NOT INDEXED WHERE rowid=?",
                    (row.rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if direct is not None:
                raise RuntimeError(
                    f"REFUSED: indexed tail row {row.rowid} is directly readable"
                )
        return Inspection(
            db_path=str(path.resolve()),
            sqlite_source_id=source_id,
            schema_sha256=schema_sha,
            indexed_count=indexed_count,
            indexed_max_rowid=indexed_max,
            range_copy_end_rowid=range_end,
            point_copy_rowids=point_rows,
            last_readable_rowid=last_readable,
            readable_prefix_count=preserved_count,
            lost_index_rows=lost,
        )
    finally:
        conn.close()


def _hash_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    range_end_rowid: int,
    point_rowids: tuple[int, ...],
) -> str:
    quoted = ", ".join(f'"{column}"' for column in columns)
    digest = hashlib.sha256()
    cursor = conn.execute(
        f"""
        SELECT rowid, {quoted}
         FROM "{table}" NOT INDEXED
         WHERE rowid BETWEEN 1 AND ?
         ORDER BY rowid
        """,
        (range_end_rowid,),
    )
    while True:
        batch = cursor.fetchmany(512)
        if not batch:
            break
        for row in batch:
            for value in row:
                if value is None:
                    raw = b""
                    tag = b"N"
                elif isinstance(value, bytes):
                    raw = value
                    tag = b"B"
                else:
                    raw = str(value).encode("utf-8")
                    tag = b"T"
                digest.update(tag)
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
    for rowid in point_rowids:
        row = conn.execute(
            f'SELECT rowid, {quoted} FROM "{table}" NOT INDEXED WHERE rowid=?',
            (rowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"row digest point row missing: {rowid}")
        for value in row:
            if value is None:
                raw = b""
                tag = b"N"
            elif isinstance(value, bytes):
                raw = value
                tag = b"B"
            else:
                raw = str(value).encode("utf-8")
                tag = b"T"
            digest.update(tag)
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return digest.hexdigest()


def _open_handles(path: Path) -> list[str]:
    targets = [path, Path(f"{path}-wal"), Path(f"{path}-shm")]
    result = subprocess.run(
        ["lsof", "-Fpc", "--", *(str(target) for target in targets)],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def _assert_writer_fence(path: Path, operator_confirms_fenced: bool) -> None:
    if os.environ.get(_SKIP_FENCE_ENV) == "1" and os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not operator_confirms_fenced:
        raise RuntimeError(
            "REFUSED: --apply requires --operator-confirms-fenced"
        )
    handles = _open_handles(path)
    if handles:
        raise RuntimeError(
            "REFUSED: trade DB still has open handles: " + " ".join(handles)
        )
    if sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "print-disabled", f"gui/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
        )
        missing = [
            label
            for label in REQUIRED_DISABLED_LABELS
            if f'"{label}" => disabled' not in result.stdout
        ]
        if missing:
            raise RuntimeError(
                "REFUSED: required trade-writer labels are not disabled: "
                + ", ".join(missing)
            )


def _replacement_ddl(ddl: str) -> str:
    replaced, count = re.subn(
        rf'^CREATE TABLE\s+"?{TABLE}\b"?',
        f"CREATE TABLE {REPLACEMENT}",
        ddl,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise RuntimeError("REFUSED: could not derive replacement table DDL")
    return replaced


def _explicit_schema_objects(
    conn: sqlite3.Connection,
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row["type"]), str(row["name"]), str(row["sql"]))
        for row in conn.execute(
            """
            SELECT type, name, sql
              FROM sqlite_master
             WHERE tbl_name=?
               AND type IN ('index', 'trigger')
               AND sql IS NOT NULL
             ORDER BY type, name
            """,
            (TABLE,),
        )
    )


def _table_integrity(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in conn.execute(f"PRAGMA integrity_check('{table}')"))


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        byte = data[offset + index]
        if index == 8:
            return (value << 8) | byte, 9
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, index + 1
    raise RuntimeError("invalid SQLite varint")


def _interior_cells(
    page: bytes, *, header_offset: int
) -> tuple[list[tuple[int, int, int]], int]:
    if page[header_offset] != 0x05:
        raise RuntimeError("expected an interior table B-tree page")
    count = struct.unpack(">H", page[header_offset + 3 : header_offset + 5])[0]
    cells: list[tuple[int, int, int]] = []
    for index in range(count):
        pointer_offset = header_offset + 12 + 2 * index
        cell_offset = struct.unpack(
            ">H", page[pointer_offset : pointer_offset + 2]
        )[0]
        child = struct.unpack(">I", page[cell_offset : cell_offset + 4])[0]
        key, _length = _varint(page, cell_offset + 4)
        cells.append((child, key, cell_offset))
    rightmost = struct.unpack(
        ">I", page[header_offset + 8 : header_offset + 12]
    )[0]
    return cells, rightmost


def _tail_parent_patch(
    path: Path, root_page: int, last_readable_rowid: int
) -> tuple[int, int, tuple[int, ...]]:
    with path.open("rb", buffering=0) as handle:
        header = handle.read(100)
        page_size_raw = int.from_bytes(header[16:18], "big")
        page_size = 65_536 if page_size_raw == 1 else page_size_raw
        if page_size <= 0:
            raise RuntimeError("invalid SQLite page size")

        page_no = root_page
        patch_candidate: tuple[int, int, tuple[int, ...]] | None = None
        while True:
            handle.seek((page_no - 1) * page_size)
            page = handle.read(page_size)
            header_offset = 100 if page_no == 1 else 0
            cells, rightmost = _interior_cells(page, header_offset=header_offset)
            selected_index = len(cells)
            selected_child = rightmost
            for index, (child, key, _cell_offset) in enumerate(cells):
                if last_readable_rowid <= key:
                    selected_index = index
                    selected_child = child
                    break
            if (
                selected_index < len(cells)
                and cells[selected_index][1] == last_readable_rowid
            ):
                patch_candidate = (
                    page_no,
                    header_offset,
                    tuple(
                        cell_offset
                        for _child, _key, cell_offset
                        in cells[selected_index + 1 :]
                    ),
                )
            handle.seek((selected_child - 1) * page_size)
            child_page = handle.read(page_size)
            child_offset = 100 if selected_child == 1 else 0
            child_type = child_page[child_offset]
            if child_type == 0x05:
                page_no = selected_child
                continue
            if child_type != 0x0D:
                raise RuntimeError(
                    "last readable row path does not terminate at a table leaf"
                )
            if patch_candidate is None:
                raise RuntimeError(
                    "no interior table page has bounded child slots after the "
                    "last readable row"
                )
            return patch_candidate


def _prepare_drop_bridge(path: Path, inspection: Inspection) -> dict[str, object]:
    conn = _connect(path, read_only=False)
    try:
        root = int(
            conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name=?", (TABLE,)
            ).fetchone()[0]
        )
    finally:
        conn.close()
    parent_page, header_offset, tail_cell_offsets = _tail_parent_patch(
        path, root, inspection.last_readable_rowid
    )
    bridge_count = len(tail_cell_offsets) + 1
    bridge_names = tuple(
        f"__position_events_repair_bridge_{index}"
        for index in range(bridge_count)
    )

    conn = _connect(path, read_only=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name in bridge_names:
            conn.execute(f'CREATE TABLE "{name}" (sentinel INTEGER)')
        conn.execute("COMMIT")
        bridge_pages = tuple(
            int(
                conn.execute(
                    "SELECT rootpage FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()[0]
            )
            for name in bridge_names
        )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    with path.open("r+b", buffering=0) as handle:
        header = handle.read(100)
        page_size_raw = int.from_bytes(header[16:18], "big")
        page_size = 65_536 if page_size_raw == 1 else page_size_raw
        handle.seek((parent_page - 1) * page_size)
        page = bytearray(handle.read(page_size))
        for cell_offset, bridge_page in zip(
            tail_cell_offsets, bridge_pages[:-1], strict=True
        ):
            page[cell_offset : cell_offset + 4] = struct.pack(">I", bridge_page)
        page[header_offset + 8 : header_offset + 12] = struct.pack(
            ">I", bridge_pages[-1]
        )
        handle.seek((parent_page - 1) * page_size)
        handle.write(page)
        handle.flush()
        os.fsync(handle.fileno())

    conn = _connect(path, read_only=False)
    try:
        schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "DELETE FROM sqlite_schema WHERE name=?",
            ((name,) for name in bridge_names),
        )
        conn.execute("COMMIT")
        conn.execute(f"PRAGMA schema_version={schema_version + 1}")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return {
        "parent_page": parent_page,
        "tail_child_slots_replaced": bridge_count,
        "bridge_pages": bridge_pages,
    }


def apply_repair(
    path: Path,
    inspection: Inspection,
    *,
    operator_confirms_fenced: bool,
    candidate_clone: bool,
    copy_chunk: int = 5_000,
) -> dict[str, object]:
    if not candidate_clone:
        raise RuntimeError(
            "REFUSED: raw B-tree bridge repair is allowed only on a disposable "
            "APFS candidate clone (--candidate-clone), never directly on canonical DB"
        )
    _assert_writer_fence(path, operator_confirms_fenced)
    if inspect(path, max_orphan_tail=max(64, inspection.lost_count)) != inspection:
        raise RuntimeError("REFUSED: corruption fingerprint changed before apply")
    bridge = _prepare_drop_bridge(path, inspection)

    conn = _connect(path, read_only=False)
    committed = False
    try:
        ddl, columns = _schema(conn)
        _assert_runtime_identity(conn, ddl)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise RuntimeError("REFUSED: repair requires the existing WAL journal mode")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA fullfsync=ON")
        conn.execute("PRAGMA foreign_keys=OFF")
        legacy_alter_table = bool(
            conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        )
        conn.execute("PRAGMA legacy_alter_table=ON")
        # The live trade DB intentionally retains legacy_archived views whose
        # source tables moved to another DB.  SQLite otherwise revalidates those
        # unrelated views during ALTER TABLE RENAME and aborts this candidate-only
        # rebuild. writable_schema makes ALTER ignore malformed unrelated rows;
        # no sqlite_schema DML occurs in this transaction.
        conn.execute("PRAGMA writable_schema=ON")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (REPLACEMENT,)
        ).fetchone():
            raise RuntimeError(f"REFUSED: stale replacement object exists: {REPLACEMENT}")
        schema_objects = _explicit_schema_objects(conn)
        source_hash = _hash_rows(
            conn,
            TABLE,
            columns,
            inspection.range_copy_end_rowid,
            inspection.point_copy_rowids,
        )

        conn.execute("BEGIN IMMEDIATE")
        _maybe_crash("after_begin")
        conn.execute(_replacement_ddl(ddl))
        _maybe_crash("after_create")

        quoted = ", ".join(f'"{column}"' for column in columns)
        insert_sql = f"""
            INSERT INTO "{REPLACEMENT}" (rowid, {quoted})
            SELECT rowid, {quoted}
              FROM "{TABLE}" NOT INDEXED
             WHERE rowid BETWEEN ? AND ?
             ORDER BY rowid
        """
        for start in range(1, inspection.range_copy_end_rowid + 1, copy_chunk):
            end = min(start + copy_chunk - 1, inspection.range_copy_end_rowid)
            conn.execute(insert_sql, (start, end))
        point_insert_sql = f"""
            INSERT INTO "{REPLACEMENT}" (rowid, {quoted})
            SELECT rowid, {quoted}
              FROM "{TABLE}" NOT INDEXED
             WHERE rowid=?
        """
        for rowid in inspection.point_copy_rowids:
            conn.execute(point_insert_sql, (rowid,))
        _maybe_crash("after_copy")

        copied = conn.execute(
            f'SELECT COUNT(*) AS n, MAX(rowid) AS max_rowid FROM "{REPLACEMENT}"'
        ).fetchone()
        if (
            int(copied["n"]) != inspection.readable_prefix_count
            or int(copied["max_rowid"]) != inspection.last_readable_rowid
        ):
            raise RuntimeError("replacement row count/high-water mismatch")
        replacement_hash = _hash_rows(
            conn,
            REPLACEMENT,
            columns,
            inspection.range_copy_end_rowid,
            inspection.point_copy_rowids,
        )
        if replacement_hash != source_hash:
            raise RuntimeError("replacement full-row digest mismatch")
        if _table_integrity(conn, REPLACEMENT) != ("ok",):
            raise RuntimeError("replacement table-scoped integrity check failed")

        conn.execute(f'DROP TABLE "{TABLE}"')
        _maybe_crash("after_drop")
        conn.execute(f'ALTER TABLE "{REPLACEMENT}" RENAME TO "{TABLE}"')
        _maybe_crash("after_rename")
        for _kind, _name, sql in schema_objects:
            conn.execute(sql)
        _maybe_crash("after_schema")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute(
            f"PRAGMA legacy_alter_table={'ON' if legacy_alter_table else 'OFF'}"
        )
        if _table_integrity(conn, TABLE) != ("ok",):
            raise RuntimeError("rebuilt position_events integrity check failed")
        if conn.execute(
            "SELECT COUNT(*) FROM position_events"
        ).fetchone()[0] != inspection.readable_prefix_count:
            raise RuntimeError("rebuilt position_events count changed")
        _maybe_crash("before_commit")
        conn.execute("COMMIT")
        committed = True
    except BaseException:
        if not committed and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("PRAGMA writable_schema=OFF")
        conn.close()

    verify = _connect(path, read_only=True)
    try:
        post_integrity = _table_integrity(verify, TABLE)
        post_count = int(
            verify.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        )
        post_max = int(
            verify.execute("SELECT MAX(rowid) FROM position_events").fetchone()[0]
        )
    finally:
        verify.close()
    if post_integrity != ("ok",):
        raise RuntimeError(f"post-commit integrity failed: {post_integrity}")
    return {
        "status": "repaired",
        "db_path": str(path.resolve()),
        "preserved_rows": post_count,
        "preserved_max_rowid": post_max,
        "preserved_full_row_sha256": source_hash,
        "drop_bridge": bridge,
        "dropped_unreadable_monitor_index_rows": [
            asdict(row) for row in inspection.lost_index_rows
        ],
        "position_events_integrity": list(post_integrity),
    }


def _inspection_json(inspection: Inspection) -> dict[str, object]:
    result = asdict(inspection)
    result["lost_count"] = inspection.lost_count
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-confirms-fenced", action="store_true")
    parser.add_argument("--candidate-clone", action="store_true")
    parser.add_argument("--max-orphan-tail", type=int, default=64)
    parser.add_argument("--copy-chunk", type=int, default=5_000)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inspection = inspect(
            args.db, max_orphan_tail=max(1, args.max_orphan_tail)
        )
        if not args.apply:
            print(
                json.dumps(
                    {"status": "repairable", **_inspection_json(inspection)},
                    sort_keys=True,
                )
            )
            return 0
        result = apply_repair(
            args.db,
            inspection,
            operator_confirms_fenced=args.operator_confirms_fenced,
            candidate_clone=args.candidate_clone,
            copy_chunk=max(1, args.copy_chunk),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - operator command must fail closed
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
