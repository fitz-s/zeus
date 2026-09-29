# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: docs/operations/current/plans/edge_program_2026-09-25.md goal 4
#   (disk stops growing without bound; nothing valuable is lost; raw kept only while a
#   decode can still need it; one universal rule). INV-37: single-DB writes to
#   zeus-forecasts.db under db_writer_lock(LIVE); trade DB is read mode=ro.
"""Forecast retention by family reachability.

One rule for every forecast store: an item belongs to one forecast family
``(city, target_date, metric)`` and is reachable while that family can still be
decoded, materialized, traded, monitored or read by id:

* its target local day has not ended everywhere (``target_date`` is within
  ``REACHABLE_TARGET_LAG_DAYS`` of the UTC date: the last local day, UTC-12,
  ends at ``target_date + 1`` 12:00Z, so no materialization, Day0 or entry reader
  reaches it after that), or
* a position on that family is not terminal (``position_current.phase`` outside
  settled/voided/admin_closed): held-belief and settlement readers dereference its
  posterior and seeds by id until the position closes. ``economically_closed`` is
  deliberately non-terminal (still settleable), so its family is kept, or
* an ENTRY rest is still open on the venue (the Day0 admission's exposure families,
  resolved by the reactor's own ``_open_rest_family_rows_for_refresh``, which can name
  a family from the market slug before any position row exists).

Day0 readers are covered: the observation-instant scan floors target dates at UTC
today-1 (``day0_extreme_updated._local_target_date_scan_floor``), inside
``REACHABLE_TARGET_LAG_DAYS``; the authority-row catch-up scan has no date floor but
its only caller admits just current-local-day market families plus held and
open-rest families, all of which are reachable above.

Unknown reachability evicts nothing: a trade-DB read failure, or a non-terminal
position with NULL city/target_date/metric, makes the whole pass a no-op.

Every other item is unreachable and evicted. Stores and what eviction removes:

* ``replacement_forecast_live`` terminal queue files (seed_processed, seed_failed,
  failed, *_latest and the seed_receipts index): whole files. Their only readers are
  by-name checks for current families (fusion-upgrade reclaim, held-belief seed
  lookup, starvation enrichment).
* ``raw_manifests``: Open-Meteo payload/precision JSON in each cycle directory and the
  manifest that points at them. Readers: seed discovery and cycle-advance (current
  families only) and the anchor cross-check, which re-reads a stored payload by cycle
  until its receipt is terminal -- such cycles are kept. The raw_forecast_artifacts
  DB row (provenance, read by source_run_id) is kept.
* ``forecast_posteriors.provenance_json``: only the Monte-Carlo sample arrays
  (``EVICTABLE_POSTERIOR_KEYS``), which only live readers of current families use.
  q/q_lcb/q_ucb, every hash (including ``q_bootstrap_samples_hash``), the carrier
  vectors that settlement-graded refits read and all identity fields stay; the row
  records which keys were evicted so a reader can tell "evicted" from "never had".

SQLite does not return freed pages to the OS (auto_vacuum=NONE): posterior eviction
moves pages to the freelist, which new posteriors reuse, so the file stops growing.
File eviction returns bytes immediately.

Work per call is bounded (file unlinks, posterior rows, WAL size) and the call is
idempotent: an evicted item is simply absent next time.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)

REACHABLE_TARGET_LAG_DAYS = 2
TERMINAL_PHASES = frozenset({"settled", "voided", "admin_closed"})
EVICTABLE_POSTERIOR_KEYS = (
    "q_bootstrap_samples_by_bin",
    "day0_remaining_carrier_probability_samples",
)
EVICTED_KEYS_FIELD = "retention_evicted_keys"
# Family-named terminal/pointer directories under replacement_forecast_live. The live
# queue (seeds/, requests/, inflight/) is never touched.
QUEUE_FAMILY_DIRS = (
    "seed_processed",
    "seed_failed",
    "seeds_failed",
    "failed",
    "blocked_latest",
    "succeeded_latest",
    "superseded_latest",
    "success_coalesced_latest",
    "seeds_latest",
)
SEED_RECEIPT_INDEX_DIR = "seed_receipts"
RAW_MANIFEST_DIR = "raw_manifests"
CURSOR_FILE = "forecast_retention_cursor.json"

DEFAULT_FILE_BUDGET = 100_000
DEFAULT_SCAN_BUDGET = 2_000_000
DEFAULT_ROW_BUDGET = 5_000
DEFAULT_BATCH_ROWS = 100
DEFAULT_WAL_LIMIT_BYTES = 1 << 30
LOCK_WAIT_SECONDS = 2.0

_QUEUE_NAME = re.compile(r"^(?P<city>.+?)\.(?P<date>\d{4}-\d{2}-\d{2})\.(?P<metric>high|low)\.")
_PAYLOAD_NAME = re.compile(
    r"^openmeteo_(?:precision_)?(?P<city>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<metric>high|low)[_.]"
)
_MANIFEST_NAME = re.compile(r"\.(?P<cycle>\d{8}T\d{6}Z)\.[0-9a-f]{12}\.[^.]+\.manifest\.json$")
_CYCLE_DIR = re.compile(r"^\d{8}T\d{6}Z$")

Family = tuple[str, str, str]


def _norm_city(city: str) -> str:
    return str(city).strip().replace(" ", "_")


def _family(city: str, target_date: str, metric: str) -> Family:
    return (_norm_city(city), str(target_date), str(metric).lower())


@dataclass(frozen=True)
class Reachability:
    """The universal predicate: which families any reader can still reach."""

    oldest_reachable_date: str
    open_families: frozenset[Family]

    def reachable(self, family: Family) -> bool:
        return family[1] >= self.oldest_reachable_date or family in self.open_families


def _read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    from src.state.db import _connect_read_only  # noqa: PLC0415

    return _connect_read_only(db_path)


class ReachabilityUnknown(RuntimeError):
    """A family some reader can reach cannot be named; evict nothing."""


def _open_rest_families(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    # Probe first: the reactor resolver swallows read errors as "no rests".
    conn.execute("SELECT count(*) FROM venue_commands").fetchone()
    from src.events.reactor import _open_rest_family_rows_for_refresh  # noqa: PLC0415

    return _open_rest_family_rows_for_refresh(conn)


def open_position_families(trade_db: Path) -> frozenset[Family]:
    """Families with a non-terminal position or an open ENTRY rest.

    Raises on any read failure and ``ReachabilityUnknown`` on a non-terminal position
    whose family is not fully named (fail closed).
    """

    conn = _read_only(Path(trade_db))
    try:
        placeholders = ",".join("?" for _ in TERMINAL_PHASES)
        rows = conn.execute(
            f"""
            SELECT DISTINCT city, target_date, temperature_metric
              FROM position_current
             WHERE phase NOT IN ({placeholders})
            """,
            tuple(sorted(TERMINAL_PHASES)),
        ).fetchall()
        rests = _open_rest_families(conn)
    finally:
        conn.close()
    unnamed = [row for row in rows if any(v is None or str(v).strip() == "" for v in row)]
    if unnamed:
        raise ReachabilityUnknown(f"{len(unnamed)} non-terminal position family(ies) not named")
    return frozenset(_family(*row) for row in [*rows, *rests])


def build_reachability(*, now: datetime, trade_db: Path | None = None) -> Reachability:
    """The one family-reachability law for every store and queue.

    ``trade_db`` defaults to the canonical trade DB. Raises when reachability is
    unknown (read failure or an unnamed open family); callers keep everything.
    """

    if trade_db is None:
        from src.state.db import _zeus_trade_db_path  # noqa: PLC0415

        trade_db = _zeus_trade_db_path()
    oldest = (now.astimezone(timezone.utc).date() - timedelta(days=REACHABLE_TARGET_LAG_DAYS))
    return Reachability(oldest.isoformat(), open_position_families(Path(trade_db)))


@dataclass
class StoreReport:
    evicted: int = 0
    bytes: int = 0
    kept_reachable: int = 0
    unclassified: int = 0
    scanned: int = 0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "evicted": self.evicted,
            "bytes": self.bytes,
            "kept_reachable": self.kept_reachable,
            "unclassified": self.unclassified,
            "scanned": self.scanned,
            "stopped": self.stopped,
            "errors": self.errors[:10],
        }


class _Budget:
    def __init__(self, limit: int) -> None:
        self.left = int(limit)

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _unlink(path: Path, report: StoreReport, *, apply: bool) -> None:
    try:
        size = path.stat().st_blocks * 512
        if apply:
            path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        report.errors.append(f"{path.name}: {exc}")
        return
    report.evicted += 1
    report.bytes += size


def _scan(directory: Path, scan_budget: _Budget) -> Iterator[os.DirEntry]:
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not scan_budget.take():
                    return
                yield entry
    except FileNotFoundError:
        return


def evict_queue_files(
    queue_root: Path,
    reach: Reachability,
    *,
    apply: bool,
    files: _Budget,
    scan: _Budget,
) -> StoreReport:
    """Terminal/pointer files named ``City.YYYY-MM-DD.metric.*`` plus the receipt index."""

    report = StoreReport()
    for name in QUEUE_FAMILY_DIRS:
        for entry in _scan(queue_root / name, scan):
            if not entry.is_file(follow_symlinks=False):
                continue
            report.scanned += 1
            match = _QUEUE_NAME.match(entry.name)
            if match is None:
                report.unclassified += 1
                continue
            if reach.reachable(_family(match["city"], match["date"], match["metric"])):
                report.kept_reachable += 1
                continue
            if not files.take():
                report.stopped = "file_budget"
                return report
            _unlink(Path(entry.path), report, apply=apply)
    index_root = queue_root / SEED_RECEIPT_INDEX_DIR
    for shard in _scan(index_root, scan):
        if not shard.is_dir(follow_symlinks=False):
            continue
        for entry in _scan(Path(shard.path), scan):
            if not entry.is_file(follow_symlinks=False):
                continue
            report.scanned += 1
            try:
                seed_file = json.loads(Path(entry.path).read_text(encoding="utf-8"))["seed_file"]
                match = _QUEUE_NAME.match(Path(str(seed_file)).name)
            except (OSError, ValueError, KeyError, TypeError):
                match = None
            if match is None:
                report.unclassified += 1
                continue
            if reach.reachable(_family(match["city"], match["date"], match["metric"])):
                report.kept_reachable += 1
                continue
            if not files.take():
                report.stopped = "file_budget"
                return report
            _unlink(Path(entry.path), report, apply=apply)
    if scan.left <= 0:
        report.stopped = report.stopped or "scan_budget"
    return report


def pending_cross_check_cycles(forecast_db: Path, receipt_path: Path) -> frozenset[str]:
    """Cycle directory names the anchor cross-check will still read a payload from.

    Mirrors ``src.data.anchor_cross_check``: a marker row whose receipt key is not
    terminal is re-read every pass. Raises on DB failure (fail closed).
    """

    from src.data.anchor_cross_check import _cross_check_receipt_is_terminal  # noqa: PLC0415

    try:
        receipts = json.loads(receipt_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipts = {}
    conn = _read_only(Path(forecast_db))
    try:
        rows = conn.execute(
            """
            SELECT source_cycle_time,
                   json_extract(artifact_metadata_json, '$.city'),
                   artifact_metadata_json LIKE '%provider_meta_declared%',
                   artifact_metadata_json LIKE '%bucket_partial_run_unverified%',
                   artifact_metadata_json LIKE '%bucket_partial_run_downscaled_unverified%'
              FROM raw_forecast_artifacts
             WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
               AND (artifact_metadata_json LIKE '%provider_meta_declared%'
                    OR artifact_metadata_json LIKE '%bucket_partial_run_%unverified%')
            """
        ).fetchall()
    finally:
        conn.close()
    pending: set[str] = set()
    for cycle_iso, city, meta_stamped, bucket, downscaled in rows:
        # Same three LIKE selections and receipt keys as each cross-check regime.
        cycle_iso = str(cycle_iso)
        keys = []
        if meta_stamped:
            keys.append(cycle_iso)
        if bucket:
            keys.append(f"{cycle_iso}::bucket::{city}" if city else f"{cycle_iso}::bucket")
        if downscaled:
            keys.append(
                f"{cycle_iso}::bucket_downscaled::{city}" if city else f"{cycle_iso}::bucket_downscaled"
            )
        if any(not _cross_check_receipt_is_terminal(receipts.get(key)) for key in keys):
            cycle = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
            pending.add(cycle.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return frozenset(pending)


def _manifest_families(path: Path) -> tuple[list[Family], str] | None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        meta = manifest["product_metadata"]
        dates = meta.get("target_dates") or [meta["target_date"]]
        city, metric = meta["city"], meta["metric"]
        cycle = datetime.fromisoformat(str(manifest["source_cycle_time"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    families = [_family(city, str(d), metric) for d in dates]
    return families, cycle.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def evict_raw_manifests(
    raw_root: Path,
    reach: Reachability,
    *,
    pending_cycles: frozenset[str],
    apply: bool,
    files: _Budget,
    scan: _Budget,
) -> StoreReport:
    """Manifests first (no reader ever sees a manifest without its payload), then payloads."""

    report = StoreReport()
    cycle_dirs: list[Path] = []
    for entry in _scan(raw_root, scan):
        if entry.is_dir(follow_symlinks=False):
            if _CYCLE_DIR.match(entry.name):
                cycle_dirs.append(Path(entry.path))
            continue
        if not entry.name.endswith(".manifest.json"):
            continue
        report.scanned += 1
        name_cycle = _MANIFEST_NAME.search(entry.name)
        # A manifest's target dates are at or after its cycle date; a cycle still
        # inside the reachable window cannot be evicted, so skip the read.
        if name_cycle and name_cycle["cycle"][:8] >= reach.oldest_reachable_date.replace("-", ""):
            report.kept_reachable += 1
            continue
        parsed = _manifest_families(Path(entry.path))
        if parsed is None:
            report.unclassified += 1
            continue
        families, cycle = parsed
        if cycle in pending_cycles or any(reach.reachable(f) for f in families):
            report.kept_reachable += 1
            continue
        if not files.take():
            report.stopped = "file_budget"
            return report
        _unlink(Path(entry.path), report, apply=apply)
    for cycle_dir in sorted(cycle_dirs):
        if cycle_dir.name in pending_cycles:
            continue
        remaining = 0
        for entry in _scan(cycle_dir, scan):
            report.scanned += 1
            match = _PAYLOAD_NAME.match(entry.name)
            if match is None:
                report.unclassified += 1
                remaining += 1
                continue
            if reach.reachable(_family(match["city"], match["date"], match["metric"])):
                report.kept_reachable += 1
                remaining += 1
                continue
            if not files.take():
                report.stopped = "file_budget"
                return report
            _unlink(Path(entry.path), report, apply=apply)
        if apply and remaining == 0:
            try:
                cycle_dir.rmdir()
            except OSError:
                pass
    if scan.left <= 0:
        report.stopped = report.stopped or "scan_budget"
    return report


def _read_cursor(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["posterior_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _write_cursor(path: Path, posterior_id: int) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"posterior_id": int(posterior_id)}), encoding="utf-8")
    os.replace(tmp, path)


def _compact_provenance(text: str) -> tuple[str, list[str]] | None:
    """Return (compacted JSON, evicted keys), or None when nothing is evictable."""

    provenance = json.loads(text)
    if not isinstance(provenance, dict):
        return None
    evicted = [key for key in EVICTABLE_POSTERIOR_KEYS if provenance.get(key) is not None]
    if not evicted:
        return None
    for key in evicted:
        provenance.pop(key)
    provenance[EVICTED_KEYS_FIELD] = sorted(set(provenance.get(EVICTED_KEYS_FIELD) or []) | set(evicted))
    # Same canonical form the materializer writes (_json: sorted keys, compact).
    return json.dumps(provenance, sort_keys=True, separators=(",", ":"), default=str), evicted


def _wal_bytes(db_path: Path) -> int:
    try:
        return db_path.with_name(db_path.name + "-wal").stat().st_size
    except FileNotFoundError:
        return 0


def _acquire_forecast_lock(db_path: Path):
    from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: PLC0415

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        lock = db_writer_lock(db_path, WriteClass.LIVE, blocking=False)
        try:
            lock.__enter__()
            return lock
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


def evict_posterior_samples(
    forecast_db: Path,
    cursor_path: Path,
    reach: Reachability,
    *,
    apply: bool,
    row_budget: int,
    batch_rows: int,
    wal_limit_bytes: int,
) -> StoreReport:
    """Strip Monte-Carlo sample arrays from unreachable posteriors.

    Scans ``row_budget`` rows by posterior_id from a persisted cursor (wraps at the
    end) so every row is revisited as families age out; restarts resume there.
    """

    report = StoreReport()
    cursor = _read_cursor(cursor_path)
    ro = _read_only(Path(forecast_db))
    try:
        ids = ro.execute(
            """
            SELECT posterior_id, city, target_date, temperature_metric
              FROM forecast_posteriors
             WHERE posterior_id > ?
             ORDER BY posterior_id
             LIMIT ?
            """,
            (cursor, int(row_budget)),
        ).fetchall()
        if not ids and cursor:
            cursor = 0
            if apply:
                _write_cursor(cursor_path, 0)
            report.stopped = "wrapped"
            return report
        candidates = []
        for posterior_id, city, target_date, metric in ids:
            report.scanned += 1
            if reach.reachable(_family(city, target_date, metric)):
                report.kept_reachable += 1
            else:
                candidates.append(int(posterior_id))
        last_id = int(ids[-1][0]) if ids else cursor
        writer = None
        try:
            for start in range(0, len(candidates), batch_rows):
                batch = candidates[start : start + batch_rows]
                placeholders = ",".join("?" for _ in batch)
                updates = []
                for posterior_id, text in ro.execute(
                    f"SELECT posterior_id, provenance_json FROM forecast_posteriors "
                    f"WHERE posterior_id IN ({placeholders})",
                    batch,
                ):
                    try:
                        compacted = _compact_provenance(str(text or "{}"))
                    except ValueError:
                        report.unclassified += 1
                        continue
                    if compacted is None:
                        continue
                    updates.append((compacted[0], int(posterior_id)))
                    report.bytes += len(text) - len(compacted[0])
                if not updates:
                    continue
                if not apply:
                    report.evicted += len(updates)
                    continue
                if _wal_bytes(forecast_db) > wal_limit_bytes:
                    report.stopped = "wal_limit"
                    last_id = batch[0] - 1
                    break
                if writer is None:
                    from src.state.db import _connect  # noqa: PLC0415

                    writer = _connect(Path(forecast_db), busy_timeout_ms=2_000)
                lock = _acquire_forecast_lock(Path(forecast_db))
                if lock is None:
                    report.stopped = "lock_contended"
                    last_id = batch[0] - 1
                    break
                try:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.executemany(
                        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
                        updates,
                    )
                    writer.execute("COMMIT")
                except Exception:
                    try:
                        writer.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
                finally:
                    lock.__exit__(None, None, None)
                report.evicted += len(updates)
        finally:
            if writer is not None:
                writer.close()
        if apply:
            _write_cursor(cursor_path, max(last_id, cursor))
    finally:
        ro.close()
    return report


def run_forecast_retention(
    *,
    apply: bool,
    now: datetime | None = None,
    state_dir: Path | None = None,
    forecast_db: Path | None = None,
    trade_db: Path | None = None,
    file_budget: int = DEFAULT_FILE_BUDGET,
    scan_budget: int = DEFAULT_SCAN_BUDGET,
    row_budget: int = DEFAULT_ROW_BUDGET,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    wal_limit_bytes: int = DEFAULT_WAL_LIMIT_BYTES,
) -> dict[str, object]:
    """One bounded retention pass over every forecast store. Never raises."""

    started = time.monotonic()
    if state_dir is None or forecast_db is None or trade_db is None:
        from src.state.db import (  # noqa: PLC0415
            ZEUS_FORECASTS_DB_PATH,
            _zeus_trade_db_path,
        )
        from src.config import STATE_DIR  # noqa: PLC0415

        state_dir = state_dir or STATE_DIR
        forecast_db = forecast_db or ZEUS_FORECASTS_DB_PATH
        trade_db = trade_db or _zeus_trade_db_path()
    now = now or datetime.now(timezone.utc)
    summary: dict[str, object] = {"apply": apply, "stores": {}}
    try:
        reach = build_reachability(now=now, trade_db=Path(trade_db))
    except Exception as exc:  # noqa: BLE001 - unknown reachability evicts nothing
        summary["status"] = "REACHABILITY_UNAVAILABLE"
        summary["error"] = str(exc)
        return summary
    summary["oldest_reachable_date"] = reach.oldest_reachable_date
    summary["open_families"] = len(reach.open_families)
    stores: dict[str, object] = {}
    files = _Budget(file_budget)
    queue_root = Path(state_dir) / "replacement_forecast_live"
    try:
        stores["queue_files"] = evict_queue_files(
            queue_root, reach, apply=apply, files=files, scan=_Budget(scan_budget)
        ).as_dict()
    except Exception as exc:  # noqa: BLE001 - one store's fault never blocks another
        stores["queue_files"] = {"error": str(exc)}
    try:
        pending = pending_cross_check_cycles(
            Path(forecast_db), Path(state_dir) / "anchor_cross_check.json"
        )
        stores["raw_manifests"] = evict_raw_manifests(
            queue_root / RAW_MANIFEST_DIR,
            reach,
            pending_cycles=pending,
            apply=apply,
            files=files,
            scan=_Budget(scan_budget),
        ).as_dict() | {"pending_cross_check_cycles": len(pending)}
    except Exception as exc:  # noqa: BLE001
        stores["raw_manifests"] = {"error": str(exc)}
    try:
        stores["posterior_samples"] = evict_posterior_samples(
            Path(forecast_db),
            Path(state_dir) / CURSOR_FILE,
            reach,
            apply=apply,
            row_budget=row_budget,
            batch_rows=batch_rows,
            wal_limit_bytes=wal_limit_bytes,
        ).as_dict()
    except Exception as exc:  # noqa: BLE001
        stores["posterior_samples"] = {"error": str(exc)}
    summary["stores"] = stores
    summary["status"] = "APPLIED" if apply else "DRY_RUN"
    summary["elapsed_s"] = round(time.monotonic() - started, 3)
    return summary


def _main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--file-budget", type=int, default=DEFAULT_FILE_BUDGET)
    parser.add_argument("--scan-budget", type=int, default=DEFAULT_SCAN_BUDGET)
    parser.add_argument("--row-budget", type=int, default=DEFAULT_ROW_BUDGET)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(
        json.dumps(
            run_forecast_retention(
                apply=args.apply,
                file_budget=args.file_budget,
                scan_budget=args.scan_budget,
                row_budget=args.row_budget,
            ),
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
