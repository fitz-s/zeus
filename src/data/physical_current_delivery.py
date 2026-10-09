# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Replay current-temperature debt from WORLD versus consumed FORECAST identity.

No source fetch, probability formula, execution action, or new truth store lives
here. A process-local cursor controls fairness only: restart rebuilds forecast
debt from observations and posterior provenance. A separate bounded publication
receipt suppresses duplicate physical wake hints, never forecast or action debt.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import threading
import time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)
_CURSOR_LOCK = threading.Lock()
_CURSORS = [0, 0]
_PUBLICATION_LOCK = threading.Lock()
_PUBLICATION_FILE = "physical_current_publication.json"
_PUBLICATION_LIMIT = 4096


_LEDGER_SCAN_STATES: dict[tuple[object, ...], dict] = {}
_LEDGER_SCAN_LOCKS: dict[tuple[object, ...], threading.Lock] = {}


def _current_temperature_ledger_revision(conn, *, city, target: str, now: datetime) -> str | None:
    """Bounded keyset scan; deadline retries continue rather than restart work."""
    from src.state.schema.observation_prints_schema import receipt_us_sql

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    zone = ZoneInfo(city.timezone)
    day = date.fromisoformat(target)
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(timezone.utc)
    databases = {str(row[1]): str(row[2]) for row in conn.execute("PRAGMA database_list")}
    path = databases.get("main", "")
    if path:
        import os
        info = os.stat(path)
        database_identity = (path, info.st_dev, info.st_ino)
    else:
        database_identity = ("memory", id(conn))
    key = (*database_identity, city.name, target)
    lock = _LEDGER_SCAN_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise TimeoutError("CURRENT_TEMPERATURE_SCAN_BUSY")
    deadline = time.monotonic() + 0.045
    state = None

    def partial_hint():
        if state is None or not (state.get("count") or state.get("tail_revision")):
            raise TimeoutError("CURRENT_TEMPERATURE_SCAN_CONTINUES_NEXT_TURN")
        return "partial:" + hashlib.sha256(json.dumps(
            (key, state["upper"], state["as_of"].isoformat(), state["first_eligible"], state.get("tail_revision")),
            separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()

    try:
        upper = int(conn.execute("SELECT COALESCE(MAX(rowid),0) FROM observation_prints").fetchone()[0])
        state = _LEDGER_SCAN_STATES.get(key)
        if state is not None and now < max(state["as_of"], state.get("tail_as_of", state["as_of"])):
            state = None
        if state is not None and state["complete"] and state["upper"] == upper and (
            state["next_available"] is None or now < state["next_available"]
        ):
            return state["revision"]
        if state is None or state["complete"] or upper < state["upper"]:
            state = {"upper": upper, "as_of": now, "cursor": (start.date().isoformat(), 0),
                     "count": 0, "last_id": 0, "last_row": None, "next_available": None,
                     "complete": False, "revision": None, "first_eligible": None,
                     "has_read_page": False, "page_size": 128, "tail_cursor": upper, "tail_revision": None,
                     "tail_next_available": None, "tail_count": 0, "tail_as_of": now}
            _LEDGER_SCAN_STATES[key] = state
            # Derived pagination state is bounded and is rebuilt after restart.
            while len(_LEDGER_SCAN_STATES) > _PUBLICATION_LIMIT:
                oldest = next(iter(_LEDGER_SCAN_STATES))
                del _LEDGER_SCAN_STATES[oldest]
                if oldest != key:
                    _LEDGER_SCAN_LOCKS.pop(oldest, None)
        # New commits must not wait for an older, dense cut to finish. Scan
        # only its append-only rowid tail and preserve independent old progress.
        # A failed direct hint drains here; future-clock rows are replayed when
        # causal without requiring another insert or resetting the old cursor.
        if state["tail_next_available"] is not None and now >= state["tail_next_available"]:
            state["tail_cursor"] = state["upper"]
            state["tail_next_available"] = None
            state["tail_count"] = 0
        previous_tail_revision = state["tail_revision"]
        while state["tail_cursor"] < upper and time.monotonic() < deadline:
            tail_rows = json.loads(conn.execute(
                "SELECT json_group_array(json_array(ledger_id,CASE WHEN city=? THEN "
                + receipt_us_sql("publish_ts_utc") + " END,CASE WHEN city=? THEN "
                + receipt_us_sql("fetched_at_utc") + " END)) FROM ("
                "SELECT rowid AS ledger_id,city,publish_ts_utc,fetched_at_utc "
                "FROM observation_prints NOT INDEXED WHERE rowid>? AND rowid<=? "
                f"ORDER BY rowid LIMIT {state['page_size']})",
                (city.name, city.name, state["tail_cursor"], upper),
            ).fetchone()[0])
            if not tail_rows:
                state["tail_cursor"] = upper
                break
            for row in tail_rows:
                state["tail_cursor"] = int(row[0])
                try:
                    observed = epoch + timedelta(microseconds=int(row[1]))
                    received = epoch + timedelta(microseconds=int(row[2]))
                    if observed.tzinfo is None or received.tzinfo is None or not start <= observed < end:
                        continue
                    available = max(observed, received)
                    if available > now:
                        state["tail_next_available"] = min(state["tail_next_available"] or available, available)
                        continue
                    state["tail_count"] += 1
                    previous_count, previous_id = state["tail_revision"] or (0, 0)
                    state["tail_revision"] = (max(previous_count, state["tail_count"]),
                                              max(previous_id, int(row[0])))
                    state["tail_as_of"] = now
                except (TypeError, ValueError):
                    continue
            if state["tail_revision"] != previous_tail_revision:
                return partial_hint()
        while time.monotonic() < deadline:
            # The owner already stores UTC spellings. Broad date-prefix seeks
            # include both T/space separators; owner SQL preserves exact
            # microseconds (including accepted long fractional spellings). Freeze rowid/as-of for the scan,
            # so append-only corrections cannot move its cursor's past.
            # Equality on publication time exposes the index's implicit rowid
            # key. A tuple inequality made SQLite rescan a dense same-clock
            # prefix on every page, which still starved under GIL contention.
            page_limit = 1 if not state["has_read_page"] else state["page_size"]

            def read_page(where, order, parameters):
                # Return only three integer/null scalars per row, <9 KiB of
                # JSON for 128 rows. Raw bodies and arbitrarily long accepted
                # timestamp spellings never cross into the continuation cache.
                # The immutable cursor rowid resolves the original index key
                # inside SQLite, so no truncation changes clock eligibility.
                sql = (
                    "SELECT json_group_array(json_array(ledger_id,"
                    + receipt_us_sql("publish_ts_utc") + "," + receipt_us_sql("fetched_at_utc") + ")) FROM ("
                    "SELECT rowid AS ledger_id,publish_ts_utc,fetched_at_utc "
                    "FROM observation_prints WHERE " + where +
                    " ORDER BY " + order + f" LIMIT {page_limit})"
                )
                return json.loads(conn.execute(sql, parameters).fetchone()[0])
            rows = read_page(
                "city=? AND publish_ts_utc=(SELECT publish_ts_utc FROM observation_prints WHERE rowid=?) "
                "AND rowid>? AND rowid<=?", "rowid",
                (city.name, state["cursor"][1], state["cursor"][1], state["upper"]),
            ) if state["cursor"][1] else []
            next_clock_page = not rows
            if next_clock_page:
                rows = read_page(
                    "city=? AND publish_ts_utc>COALESCE((SELECT publish_ts_utc FROM observation_prints WHERE rowid=?),?) "
                    "AND publish_ts_utc<? AND rowid<=?", "publish_ts_utc,rowid",
                    (city.name, state["cursor"][1], start.date().isoformat(),
                     (end.date() + timedelta(days=1)).isoformat(), state["upper"]),
                )
            state["has_read_page"] = state["has_read_page"] or bool(rows)
            for row in rows:
                state["cursor"] = (row[1], int(row[0]))
                try:
                    observed = epoch + timedelta(microseconds=int(row[1]))
                    received = epoch + timedelta(microseconds=int(row[2]))
                    if observed.tzinfo is None or received.tzinfo is None:
                        continue
                    if not start <= observed < end:
                        continue
                    available = max(observed, received)
                    if available > state["as_of"]:
                        state["next_available"] = min(state["next_available"] or available, available)
                        continue
                    state["count"] += 1
                    if state["first_eligible"] is None:
                        state["first_eligible"] = (int(row[0]), row[1], row[2])
                    if int(row[0]) > state["last_id"]:
                        state["last_id"] = int(row[0])
                        state["last_row"] = (int(row[0]), row[1], row[2])
                except (TypeError, ValueError):
                    continue
            if next_clock_page and len(rows) < page_limit:
                state["revision"] = hashlib.sha256(json.dumps(
                    ((state["count"], state["last_id"]), state["last_row"], state["tail_revision"]),
                    separators=(",", ":"), allow_nan=False,
                ).encode()).hexdigest() if state["count"] else None
                state["complete"] = True
                return state["revision"]
        return partial_hint()
    except Exception as exc:
        # The durable wake is only a prompt to re-read canonical truth. A
        # successfully read causal page is enough; full scan drainage remains
        # independent and must not delay the executable exit window.
        import sqlite3
        if isinstance(exc, sqlite3.OperationalError) and "interrupt" in str(exc).lower():
            if state is not None:
                # A fixed large page can repeatedly lose its progress under
                # VM/GIL contention. Shrink the next page, retaining every
                # prior admitted row; eventual one-row I/O is the floor.
                state["page_size"] = max(1, state["page_size"] // 4)
            return partial_hint()
        raise
    finally:
        lock.release()


def _current_noaa_snapshot_revision(conn, *, city, target: str, now: datetime) -> str | None:
    """Wake identity only; current source consumers independently validate q."""
    row = conn.execute(
        "SELECT fetched_at, high_temp, low_temp, high_provenance_metadata, low_provenance_metadata "
        "FROM observations WHERE city=? AND target_date=? AND source=? "
        "AND rebuild_run_id LIKE 'noaa_wrh_current_%'",
        (city.name, target, f"noaa_wrh_{city.wu_station.lower()}"),
    ).fetchone()
    if row is None:
        return None
    receipt = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    if receipt.tzinfo is None or receipt > now:
        return None
    metadata = json.loads(row[3])
    # Acquisition-only confirmations do not renew a semantic wake obligation.
    recovery = metadata.get("wrh_custody_recovery")
    if recovery is not None:
        recovered_at = datetime.fromisoformat(str(recovery["recorded_at"]).replace("Z", "+00:00"))
        if recovered_at.tzinfo is None or recovered_at > now:
            recovery = None
    identity = (row[0], row[1], row[2], metadata.get("wrh_current_snapshot"), recovery)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def publish_current_temperature_wakes(
    *, cities: Sequence[Any], scopes: Sequence[tuple[str, str, str]], now: datetime,
    committed: bool = False,
) -> dict[str, object]:
    """Replay committed source revisions independently of forecast production.

    SCOPE: one city/local-day/metric ledger revision, including late corrections.
    DRAIN: the existing periodic delivery scan retries failed read/publish/record
    steps after restart; no new observation or forecast is required. RESET: a
    changed causal ledger revision. The bounded record acknowledges only durable
    wake publication, never source authority, a probability, or completed action.
    Reads are index-bounded and individually deadline-limited, outside the
    nonblocking publication lock. One failed scope cannot stop unrelated scopes.
    """
    from contextlib import closing, ExitStack
    from src.config import state_path
    from src.runtime.reactor_wake import publish_reactor_wake
    from src.state.db import get_world_connection_read_only, get_forecasts_connection_read_only
    from src.state.paths import write_json_atomic

    if now.tzinfo is None:
        raise ValueError("CURRENT_TEMPERATURE_DELIVERY_CLOCK_NAIVE")
    by_name = {city.name: city for city in cities}
    path = state_path(_PUBLICATION_FILE)
    published, deferred, scan_pending = 0, False, False
    if committed:
        # The writer already knows this committed revision's affected families.
        # Do not make it re-scan a historical ledger before issuing a hint.
        for family in dict.fromkeys(tuple(scope) for scope in scopes):
            if family[0] not in by_name or family[2] not in {"high", "low"}:
                continue
            try:
                publish_reactor_wake(source="physical_current_delivery",
                    reason="current_temperature_print_committed", forecast_families=(family,))
                published += 1
            except Exception as exc:
                deferred = True
                _LOG.warning("CURRENT_TEMPERATURE_COMMIT_WAKE_DEFERRED family=%s error=%s", family, type(exc).__name__)
        return {"status": "WAKE_DEFERRED" if deferred else "WAKE_RECONCILED", "published": published}
    revisions: dict[tuple[str, str], str | None] = {}
    try:
        with ExitStack() as stack:
            conn = stack.enter_context(closing(get_world_connection_read_only(
                deadline_monotonic=time.monotonic() + 0.1,
            )))
            forecasts, forecast_read_attempted = None, False
            conn.execute("PRAGMA busy_timeout = 50")
            for city_name, target, metric in scopes:
                if city_name not in by_name or metric not in {"high", "low"}:
                    continue
                family = (city_name, target, metric)
                key = json.dumps(family, separators=(",", ":"))
                cell = (city_name, target)
                try:
                    if cell not in revisions:
                        deadline = time.monotonic() + 0.05
                        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                        try:
                            ledger_revision = None
                            try:
                                ledger_revision = _current_temperature_ledger_revision(
                                    conn, city=by_name[city_name], target=target, now=now,
                                )
                                scan_pending = scan_pending or bool(ledger_revision and ledger_revision.startswith("partial:"))
                            except Exception as exc:
                                deferred = True
                                _LOG.warning("CURRENT_TEMPERATURE_LEDGER_WAKE_READ_DEFERRED city=%s error=%s",
                                             city_name, type(exc).__name__)
                            finally:
                                conn.set_progress_handler(None, 0)
                            # A raw ledger timeout cannot hide a separately
                            # committed EMPTY/retraction on the daily owner.
                            snapshot_revision = None
                            if getattr(by_name[city_name], "settlement_source_type", "") == "noaa":
                                try:
                                    if not forecast_read_attempted:
                                        forecast_read_attempted = True
                                        forecasts = stack.enter_context(closing(get_forecasts_connection_read_only(
                                            deadline_monotonic=time.monotonic() + 0.05,
                                        )))
                                        forecasts.execute("PRAGMA busy_timeout = 50")
                                    if forecasts is not None:
                                        snapshot_deadline = time.monotonic() + 0.05
                                        forecasts.set_progress_handler(lambda: int(time.monotonic() >= snapshot_deadline), 1000)
                                        try:
                                            snapshot_revision = _current_noaa_snapshot_revision(
                                                forecasts, city=by_name[city_name], target=target, now=now,
                                            )
                                        finally:
                                            forecasts.set_progress_handler(None, 0)
                                except Exception as exc:
                                    deferred = True
                                    _LOG.warning("CURRENT_WRH_WAKE_READ_DEFERRED city=%s error=%s", city_name, type(exc).__name__)
                            revisions[cell] = (hashlib.sha256(json.dumps(
                                (ledger_revision, snapshot_revision), separators=(",", ":"),
                            ).encode()).hexdigest() if ledger_revision or snapshot_revision else None)
                        finally:
                            conn.set_progress_handler(None, 0)
                    revision = revisions[cell]
                    if revision is None:
                        continue
                except Exception as exc:
                    revisions[cell] = None
                    deferred = True
                    _LOG.warning("CURRENT_TEMPERATURE_WAKE_SCOPE_DEFERRED family=%s error=%s",
                                 family, type(exc).__name__)
                    continue
                if not _PUBLICATION_LOCK.acquire(blocking=False):
                    deferred = True
                    continue
                try:
                    try:
                        done = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        done = {}
                    if not isinstance(done, dict):
                        done = {}
                    if done.get(key) == revision:
                        continue
                    publish_reactor_wake(
                        source="physical_current_delivery",
                        reason="current_temperature_print_committed",
                        forecast_families=(family,),
                    )
                    published += 1
                    done.pop(key, None)
                    done[key] = revision
                    while len(done) > _PUBLICATION_LIMIT:
                        del done[next(iter(done))]
                    # Failure leaves this family replayable. Duplicate hints
                    # after crash are harmless; lost delivery is not.
                    write_json_atomic(path, done)
                except Exception as exc:
                    deferred = True
                    _LOG.warning("CURRENT_TEMPERATURE_WAKE_SCOPE_DEFERRED family=%s error=%s",
                                 family, type(exc).__name__)
                finally:
                    _PUBLICATION_LOCK.release()
    except Exception as exc:
        deferred = True
        _LOG.warning("CURRENT_TEMPERATURE_WAKE_DEFERRED error=%s", type(exc).__name__)
    return {"status": "WAKE_DEFERRED" if deferred else "WAKE_RECONCILED", "published": published,
            **({"scan_pending": True} if scan_pending else {})}


def current_temperature_priority_families() -> dict[tuple[str, str, str], int]:
    """Read held and resting exposure, including commands not yet projected.

    Two read-only handles, no ATTACH or cross-database write. Reuse the substrate
    owner's exact command -> snapshot -> condition -> family resolution rather
    than treating a missing position projection as proof of no resting order.
    """
    from contextlib import closing
    from src.data.replacement_forecast_seed_discovery import held_position_family_priorities
    from src.data.substrate_observer import _open_rest_scope_rows_for_refresh
    from src.state.db import get_trade_connection_read_only, get_forecasts_connection_read_only

    priorities = dict(held_position_family_priorities())
    try:
        with closing(get_trade_connection_read_only()) as trade:
            with closing(get_forecasts_connection_read_only()) as forecasts:
                rests = _open_rest_scope_rows_for_refresh(
                    trade, forecasts_conn=forecasts, strict=True,
                )
        for family, _condition_id in rests:
            priorities.setdefault(family, 1)
    except Exception as exc:
        # The next debt scan retries this read. Do not claim zero resting orders,
        # suppress other families, or stop serving a previously valid posterior.
        _LOG.warning("CURRENT_TEMPERATURE_REST_SCOPE_UNAVAILABLE error=%s", type(exc).__name__)
    return priorities

def current_temperature_delivery_scopes(
    cities: Sequence[Any], *, now: datetime,
    held: Mapping[tuple[str, str, str], int] | None = None,
) -> tuple[tuple[str, str, str], ...]:
    if now.tzinfo is None:
        raise ValueError("CURRENT_TEMPERATURE_DELIVERY_CLOCK_NAIVE")
    if held is None:
        held = current_temperature_priority_families()
    by_name = {city.name: city for city in cities}
    scopes = {
        (city.name, now.astimezone(ZoneInfo(city.timezone)).date().isoformat(), metric)
        for city in cities for metric in ("high", "low")
    }
    # Pending/resting entries also need repricing; an ended-day held scope must
    # not disappear merely because it is outside the new-entry calendar.
    scopes.update(scope for scope in held
                  if scope[0] in by_name and scope[2] in {"high", "low"})
    return tuple(sorted(scopes, key=lambda scope: (scope not in held, scope)))

def reconcile_current_temperature_delivery(
    cfg: dict[str, object], *, cities: Sequence[Any],
    now: datetime | None = None, max_scopes: int = 12,
) -> dict[str, object]:
    from src.data.replacement_forecast_production import _enqueue_fusion_upgrade_reseeds_if_needed
    now = now or datetime.now(timezone.utc)
    held = current_temperature_priority_families()
    all_scopes = current_temperature_delivery_scopes(cities, now=now, held=held)
    groups = ([s for s in all_scopes if s in held], [s for s in all_scopes if s not in held])
    selected = []
    limit = max(2, int(max_scopes))
    # Reserve progress for both money-at-risk and first-materialization scopes.
    # A persistently unmaterializable held family cannot starve other cities.
    with _CURSOR_LOCK:
        for index, group in enumerate(groups):
            if not group:
                continue
            count = min(len(group), (limit + 1) // 2 if all(groups) else limit)
            start = _CURSORS[index] % len(group)
            selected.extend(group[(start + offset) % len(group)] for offset in range(count))
            _CURSORS[index] = (start + count) % len(group)
    # A missing/blocked ENS carrier cannot suppress held physical redecision.
    # Publish before attempting a reseed, whose failure has separate recovery.
    wake_report = publish_current_temperature_wakes(cities=cities, scopes=selected, now=now)
    report = _enqueue_fusion_upgrade_reseeds_if_needed(
        cfg, scopes=tuple(selected), changed_sources=("day0_current_temperature_state",),
        computed_at=now, limit=len(selected) or 1,
    ) if selected else None
    return {"status": "CURRENT_TEMPERATURE_RECONCILED", "scopes_offered": len(selected),
            "total_scopes": len(all_scopes), "delivery": report, "wake_delivery": wake_report}
