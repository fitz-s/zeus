"""Durable Open-Meteo answer store and unit ledger, shared by every daemon.

LAW: a metered request is sent only when its answer can differ from one already held.

An exact-run answer is a function of the provider's run state for each requested
model. ``fetch()`` keeps every successful exact-run answer together with the
per-model state it was fetched under, and serves it again at zero quota cost
until that state changes:

- single-runs ``run=R``: forever once R is superseded (if the held answer already
  reflected R's final modification); while R is the latest run, until R's
  ``last_run_modification_time`` advances;
- previous-runs with explicit dates: until any requested model publishes a new
  run or modifies its latest one.

A gap in an answer (nulls past a horizon, a model without a series) is part of
the answer and can only fill when the provider modifies the run, so it is never
re-requested before then. The same holds for the provider's typed refusal
"the requested model run is not available" (HTTP 400, ``run_not_published``)
once the run's availability plus the consistency wait has passed: the run is
not on the single-runs archive grid, and only a run change can alter that. This one rule replaces the caller-side "does a row
exist?" checks whose missing artifact (a 200 with nulls, a failed persist, a
restart, a replica flip) turned into an unbounded metered re-request.

Provider state is the unmetered ``/data/{slug}/static/meta.json`` answer, which
already passes through ``fetch()``. Replicas disagree, so state is pinned by run
identity and only moves forward: the latest run is the largest initialisation
seen, and each run's modification and availability are the largest seen. An
answer is provable only once its run is propagated (modification <= availability)
and the consistency wait after availability has passed. A latest-run answer is
served only while its state was confirmed within ``META_FRESH_SECONDS``; the
caller re-reads stale state (unmetered) before looking.

Medium: one SQLite file in WAL mode, for atomic cross-process upserts, one
primary-key read per request and deletes on an expiry index. Codec: compact JSON
compressed with zstd-3 into a BLOB (5.6x on the live single-runs payloads).
Retention is reachability: an answer expires two days after the last valid hour
it carries, when no target date inside it can still be live. Stored bytes are
bounded by about 0.6 KB per metered unit times the retention window, tens of MB.

Any store failure degrades to the network path: the store never blocks a fetch
and never serves an answer it cannot prove.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

import zstandard

from src.data.openmeteo_quota import PRIORITY_DAILY_LIMIT
from src.strategy.live_inference.source_clock_vnext import (
    SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES,
)

logger = logging.getLogger(__name__)

META_FRESH_SECONDS = 60.0
CONSISTENCY_WAIT_SECONDS = SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES * 60.0
# A target local day ends less than two days after any valid hour inside it.
ANSWER_REACH_SECONDS = 2 * 86_400.0
DEFAULT_RUN_HOURS = 16 * 24
LEDGER_RETENTION_DAYS = 8
PROVIDER_RUN_RETENTION_DAYS = 10
EVICT_INTERVAL_SECONDS = 600.0
ALARM_CHECK_SECONDS = 60.0
ALARM_DAILY_LIMIT = PRIORITY_DAILY_LIMIT
RATE_WINDOW_HOURS = 2.0
ERROR_LOG_INTERVAL_SECONDS = 300.0

SUPERSEDED = "superseded"
SINGLE_RUNS_HOST = "single-runs-api.open-meteo.com"
PREVIOUS_RUNS_HOST = "previous-runs-api.open-meteo.com"
META_HOST = "api.open-meteo.com"

# ``models=`` API id -> meta.json slug for every model whose run state is readable.
# Each slug answered 200 on 2026-09-26 (``metno_nordic`` and ``gfs_hrrr`` as slugs
# answer 500). A request naming any other model (best_match, a legacy family) is
# not versionable and always takes the network path. Tests pin this map against
# OPENMETEO_MODEL_IDS and OPENMETEO_MODEL_METADATA_IDS.
META_SLUGS: Mapping[str, str] = {
    "ecmwf_ifs": "ecmwf_ifs",
    "ecmwf_ifs025": "ecmwf_ifs025",
    "icon_global": "dwd_icon",
    "icon_eu": "dwd_icon_eu",
    "icon_d2": "dwd_icon_d2",
    "meteofrance_arome_france_hd": "meteofrance_arome_france_hd",
    "ncep_nbm_conus": "ncep_nbm_conus",
    "ukmo_global_deterministic_10km": "ukmo_global_deterministic_10km",
    "ukmo_uk_deterministic_2km": "ukmo_uk_deterministic_2km",
    "gfs_hrrr": "ncep_hrrr_conus",
    "gem_hrdps_continental": "cmc_gem_hrdps",
    "dmi_harmonie_arome_europe": "dmi_harmonie_arome_europe",
    "knmi_harmonie_arome_netherlands": "knmi_harmonie_arome_netherlands",
    "kma_gdps": "kma_gdps",
    "kma_ldps": "kma_ldps",
    "metno_nordic": "metno_nordic_pp",
    "italia_meteo_arpae_icon_2i": "italia_meteo_arpae_icon_2i",
    "jma_msm": "jma_msm",
    "ncep_nam_conus": "ncep_nam_conus",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    request_id TEXT PRIMARY KEY,
    proofs TEXT NOT NULL,
    status INTEGER NOT NULL,
    payload BLOB NOT NULL,
    fetched_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS responses_expiry ON responses(expires_at);
CREATE TABLE IF NOT EXISTS provider_runs (
    slug TEXT NOT NULL,
    run_init INTEGER NOT NULL,
    modification INTEGER NOT NULL,
    availability INTEGER NOT NULL,
    PRIMARY KEY (slug, run_init)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS provider_asked (
    slug TEXT PRIMARY KEY,
    asked_at REAL NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS unit_ledger (
    hour TEXT NOT NULL,
    job TEXT NOT NULL,
    metered INTEGER NOT NULL,
    served INTEGER NOT NULL,
    reissued INTEGER NOT NULL,
    PRIMARY KEY (hour, job)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS day_successes (
    day TEXT NOT NULL,
    request_id TEXT NOT NULL,
    PRIMARY KEY (day, request_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS alarms (hour TEXT PRIMARY KEY) WITHOUT ROWID;
"""


@dataclass(frozen=True)
class ExactRequest:
    """A request whose answer is fixed by the provider run state of ``slugs``."""

    slugs: tuple[str, ...]
    run: float | None  # single-runs initialisation (epoch s); None for previous-runs
    expires_at: float


@dataclass(frozen=True)
class RunState:
    init: int
    modification: int
    availability: int
    observed_at: float


def _ids(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return ()
    return tuple(item.strip() for item in items if item.strip())


def _number(value: object) -> float:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _epoch(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if text.isdigit():
        return float(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def exact_request(url: str, params: Mapping[str, object]) -> ExactRequest | None:
    """Classify an exact-run request, or None when its answer moves with the clock."""

    parts = urlsplit(url)
    if parts.netloc not in (SINGLE_RUNS_HOST, PREVIOUS_RUNS_HOST):
        return None
    query: dict[str, object] = dict(parse_qsl(parts.query))
    query.update(params)
    models = _ids(query.get("models"))
    if not models or any(model not in META_SLUGS for model in models):
        return None
    slugs = tuple(sorted({META_SLUGS[model] for model in models}))
    if parts.netloc == SINGLE_RUNS_HOST:
        run = _epoch(query.get("run"))
        if run is None:
            return None
        hours = max(
            _number(query.get("forecast_hours")),
            24.0 * _number(query.get("forecast_days")),
        ) or DEFAULT_RUN_HOURS
        return ExactRequest(slugs, run, run + hours * 3600.0 + ANSWER_REACH_SECONDS)
    try:
        date.fromisoformat(str(query.get("start_date")))
        end = date.fromisoformat(str(query.get("end_date")))
    except ValueError:
        return None
    end_of_day = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()
    return ExactRequest(slugs, None, end_of_day + ANSWER_REACH_SECONDS)


def meta_slug(url: str) -> str | None:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if (
        parts.netloc == META_HOST
        and len(segments) == 4
        and segments[0] == "data"
        and segments[2:] == ["static", "meta.json"]
    ):
        return segments[1]
    return None


def meta_url(slug: str) -> str:
    return f"https://{META_HOST}/data/{slug}/static/meta.json"


def _hour_key(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H")


def _day_key(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


class OpenMeteoResponseStore:
    """Cross-process answer store keyed by request identity plus provider run state."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._last_evicted = 0.0
        self._last_alarm_check = 0.0
        self._last_error_log = 0.0

    # -- plumbing ---------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "pid", None) != os.getpid():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, timeout=2.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._local.conn = conn
            self._local.pid = os.getpid()
        return conn

    def _failed(self, exc: Exception) -> None:
        now = time.time()
        if now - self._last_error_log >= ERROR_LOG_INTERVAL_SECONDS:
            self._last_error_log = now
            logger.warning(
                "Open-Meteo response store unavailable (%s: %s); using the network path",
                type(exc).__name__,
                exc,
            )

    # -- provider run state ----------------------------------------------

    def record_meta(self, slug: str, payload: object, now: float | None = None) -> None:
        """Pin one meta.json reading by run identity; its stamps only move forward."""

        if not isinstance(payload, Mapping):
            return
        stamps = [
            _epoch(payload.get(key))
            for key in (
                "last_run_initialisation_time",
                "last_run_modification_time",
                "last_run_availability_time",
            )
        ]
        if any(stamp is None for stamp in stamps):
            return
        init, modification, availability = (int(stamp) for stamp in stamps)  # type: ignore[arg-type]
        try:
            db = self._db()
            db.execute(
                """
                INSERT INTO provider_runs VALUES (?, ?, ?, ?)
                ON CONFLICT(slug, run_init) DO UPDATE SET
                    modification = MAX(provider_runs.modification, excluded.modification),
                    availability = MAX(provider_runs.availability, excluded.availability)
                """,
                (slug, init, modification, availability),
            )
            # Only a reading at the pinned maximum on every stamp confirms it is
            # current; a lagging replica neither regresses the pin nor vouches for it.
            latest = self.run_state(slug)
            if latest is not None and (init, modification, availability) == (
                latest.init,
                latest.modification,
                latest.availability,
            ):
                db.execute(
                    "INSERT OR REPLACE INTO provider_asked VALUES (?, ?)",
                    (slug, time.time() if now is None else now),
                )
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)

    def run_state(self, slug: str, init: int | None = None) -> RunState | None:
        """The latest pinned run of ``slug``, or its run ``init`` when given."""

        select = (
            "SELECT r.run_init, r.modification, r.availability, COALESCE(a.asked_at, 0) "
            "FROM provider_runs r LEFT JOIN provider_asked a ON a.slug = r.slug "
            "WHERE r.slug = ? "
        )
        if init is None:
            row = self._db().execute(
                select + "ORDER BY r.run_init DESC LIMIT 1", (slug,)
            ).fetchone()
        else:
            row = self._db().execute(select + "AND r.run_init = ?", (slug, init)).fetchone()
        return RunState(*row) if row else None

    def stale_slugs(self, req: ExactRequest, now: float | None = None) -> tuple[str, ...]:
        """Slugs whose run state must be re-read before this request can be proven."""

        now = time.time() if now is None else now
        try:
            stale = []
            for slug in req.slugs:
                state = self.run_state(slug)
                superseded = state is not None and req.run is not None and req.run < state.init
                if state is None or (
                    not superseded and now - state.observed_at > META_FRESH_SECONDS
                ):
                    stale.append(slug)
            return tuple(stale)
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)
            return ()

    @staticmethod
    def _proof(req: ExactRequest, state: RunState | None, now: float) -> str | None:
        """The run state an answer fetched now provably reflects, or None."""

        if state is None:
            return None
        if req.run is not None and req.run < state.init:
            return SUPERSEDED
        if req.run is not None and req.run > state.init:
            return None
        if (
            state.modification > state.availability
            or now < state.availability + CONSISTENCY_WAIT_SECONDS
        ):
            return None
        return f"{state.init}:{state.modification}"

    def _holds(self, req: ExactRequest, slug: str, proof: object, now: float) -> bool:
        """Whether an answer proven at ``proof`` is still the provider's answer."""

        if proof == SUPERSEDED:
            return True
        init, _, modification = str(proof).partition(":")
        if not (init.isdigit() and modification.isdigit()):
            return False
        state = self.run_state(slug)
        if state is None:
            return False
        if state.init == int(init):
            return (
                now - state.observed_at <= META_FRESH_SECONDS
                and state.modification == int(modification)
            )
        if req.run is None:
            # A new run of any requested model changes a previous-runs answer.
            return False
        # The run was superseded after this fetch: the answer holds only if it
        # already reflected that run's final modification.
        final = self.run_state(slug, int(init))
        return final is not None and final.modification == int(modification)

    def proofs(self, req: ExactRequest, now: float | None = None) -> dict[str, str] | None:
        now = time.time() if now is None else now
        try:
            out: dict[str, str] = {}
            for slug in req.slugs:
                proof = self._proof(req, self.run_state(slug), now)
                if proof is None:
                    return None
                out[slug] = proof
            return out
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)
            return None

    # -- answers ----------------------------------------------------------

    def lookup(
        self, request_id: str, req: ExactRequest, now: float | None = None
    ) -> tuple[int, object] | None:
        """The held (HTTP status, body) for this request, if it is still the provider's."""

        now = time.time() if now is None else now
        try:
            row = self._db().execute(
                "SELECT proofs, status, payload FROM responses "
                "WHERE request_id = ? AND expires_at > ?",
                (request_id, now),
            ).fetchone()
            if row is None:
                return None
            proofs = json.loads(row[0])
            if not all(self._holds(req, slug, proofs.get(slug), now) for slug in req.slugs):
                return None
            return int(row[1]), json.loads(zstandard.ZstdDecompressor().decompress(row[2]))
        except (sqlite3.Error, OSError, ValueError, zstandard.ZstdError) as exc:
            self._failed(exc)
            return None

    def put(
        self,
        request_id: str,
        req: ExactRequest,
        proofs: Mapping[str, str],
        payload: object,
        status: int = 200,
        now: float | None = None,
    ) -> None:
        """Hold a 200 body, or a provider's typed "run not published" refusal."""

        now = time.time() if now is None else now
        try:
            blob = zstandard.ZstdCompressor(level=3).compress(
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
            )
            self._db().execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    json.dumps(dict(proofs), sort_keys=True),
                    int(status),
                    blob,
                    now,
                    req.expires_at,
                ),
            )
            self._evict(now)
        except (sqlite3.Error, OSError, TypeError, ValueError, zstandard.ZstdError) as exc:
            self._failed(exc)

    def _evict(self, now: float) -> None:
        """Drop what no live target date can read; bounded deletes on indexes."""

        if now - self._last_evicted < EVICT_INTERVAL_SECONDS:
            return
        self._last_evicted = now
        db = self._db()
        horizon = _hour_key(now - LEDGER_RETENTION_DAYS * 86_400.0)
        db.execute("DELETE FROM responses WHERE expires_at <= ?", (now,))
        db.execute("DELETE FROM day_successes WHERE day < ?", (_day_key(now - 86_400.0),))
        db.execute("DELETE FROM unit_ledger WHERE hour < ?", (horizon,))
        db.execute("DELETE FROM alarms WHERE hour < ?", (horizon,))
        db.execute(
            "DELETE FROM provider_runs WHERE run_init < ? AND run_init < "
            "(SELECT MAX(p.run_init) FROM provider_runs p WHERE p.slug = provider_runs.slug)",
            (int(now - PROVIDER_RUN_RETENTION_DAYS * 86_400.0),),
        )

    # -- unit ledger and burn alarm ----------------------------------------

    def _ledger_add(self, job: str, now: float, metered: int, served: int, reissued: int) -> None:
        self._db().execute(
            """
            INSERT INTO unit_ledger VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hour, job) DO UPDATE SET
                metered = metered + excluded.metered,
                served = served + excluded.served,
                reissued = reissued + excluded.reissued
            """,
            (_hour_key(now), job[:160], metered, served, reissued),
        )

    def note_metered(
        self, request_id: str, job: str, units: int, now: float | None = None
    ) -> None:
        """Account one metered send; a same-day repeat of a success is a reissue."""

        now = time.time() if now is None else now
        try:
            repeat = self._db().execute(
                "SELECT 1 FROM day_successes WHERE day = ? AND request_id = ?",
                (_day_key(now), request_id),
            ).fetchone()
            self._ledger_add(job, now, units, 0, units if repeat else 0)
            self._maybe_alarm(now)
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)

    def note_served(self, job: str, units: int, now: float | None = None) -> None:
        try:
            self._ledger_add(job, time.time() if now is None else now, 0, units, 0)
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)

    def note_success(self, request_id: str, now: float | None = None) -> None:
        try:
            self._db().execute(
                "INSERT OR IGNORE INTO day_successes VALUES (?, ?)",
                (_day_key(time.time() if now is None else now), request_id),
            )
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)

    def burn(self, now: float | None = None) -> dict[str, object]:
        """Today's units by job and the end-of-day projection at the current rate.

        The current rate is the mean over the trailing ``RATE_WINDOW_HOURS`` (hour
        buckets, the oldest one weighted by its share inside the window). Jobs are
        ranked by that rate, the burn the projection extrapolates.
        """

        now = time.time() if now is None else now
        start = now - RATE_WINDOW_HOURS * 3600.0
        first = start - start % 3600.0
        today = _day_key(now)
        jobs: dict[str, list[float]] = {}
        for hour, job, metered, served, reissued in self._db().execute(
            "SELECT hour, job, metered, served, reissued FROM unit_ledger WHERE hour >= ?",
            (min(_hour_key(first), f"{today}T00"),),
        ):
            row = jobs.setdefault(str(job), [0.0, 0, 0, 0])
            bucket = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()
            if bucket >= first:
                row[0] += metered * (1.0 if bucket > first else (first + 3600.0 - start) / 3600.0)
            if hour[:10] == today:
                row[1] += metered
                row[2] += served
                row[3] += reissued
        for row in jobs.values():
            row[0] /= RATE_WINDOW_HOURS
        ranked = sorted(jobs.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))
        metered_today = int(sum(row[1] for row in jobs.values()))
        rate = sum(row[0] for row in jobs.values())
        return {
            "day": _day_key(now),
            "metered": metered_today,
            "served": int(sum(row[2] for row in jobs.values())),
            "rate_per_hour": rate,
            "projected": metered_today + rate * (86_400.0 - now % 86_400.0) / 3600.0,
            "jobs": [
                (job, round(row[0]), int(row[1]), int(row[3])) for job, row in ranked
            ],
        }

    def _maybe_alarm(self, now: float) -> None:
        if now - self._last_alarm_check < ALARM_CHECK_SECONDS:
            return
        self._last_alarm_check = now
        burn = self.burn(now)
        if burn["projected"] <= ALARM_DAILY_LIMIT:  # type: ignore[operator]
            return
        claimed = self._db().execute(
            "INSERT OR IGNORE INTO alarms VALUES (?)", (_hour_key(now),)
        ).rowcount
        if claimed != 1:
            return
        top = "; ".join(
            f"{job} rate_1h={rate} today={metered} reissued_same_day={reissued}"
            for job, rate, metered, reissued in burn["jobs"][:3]  # type: ignore[index]
        )
        logger.warning(
            "Open-Meteo burn alarm: day=%s projected=%d > limit=%d metered=%d "
            "served_from_store=%d rate_1h=%d top3=[%s]",
            burn["day"],
            burn["projected"],
            ALARM_DAILY_LIMIT,
            burn["metered"],
            burn["served"],
            burn["rate_per_hour"],
            top,
        )


def runtime_response_store() -> OpenMeteoResponseStore | None:
    """The shared store under state/, or None inside a test process."""

    from src.config import TEST_STATE_ROOT_ENV, state_path

    if (
        os.environ.get("ZEUS_TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or TEST_STATE_ROOT_ENV in os.environ
    ):
        return None
    return OpenMeteoResponseStore(state_path("openmeteo_response_store.db"))
