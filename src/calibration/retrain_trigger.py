# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F2.yaml
"""Operator-gated calibration retrain/promotion wiring for R3 F2.

F2 wires the control seam; it does not auto-fire model fitting. Promotion is
allowed only when the operator gate is armed, the corpus is CONFIRMED-only, and
the caller-provided frozen-replay antibody passes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from src.calibration.store import deactivate_model_v2, save_platt_model_v2
from src.state.venue_command_repo import load_calibration_trade_facts
from src.types.metric_identity import HIGH_LOCALDAY_MAX, MetricIdentity


ENV_FLAG_NAME = "ZEUS_CALIBRATION_RETRAIN_ENABLED"
OPERATOR_TOKEN_SECRET_ENV = "ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET"
ARTIFACT_PATTERN = (
    "docs/operations/task_2026-04-26_ultimate_plan/**/"
    "evidence/calibration_retrain_decision_*.md"
)


class RetrainStatus(str, Enum):
    DISABLED = "DISABLED"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    COMPLETE_REPLAYED = "COMPLETE_REPLAYED"
    COMPLETE_DRIFT_DETECTED = "COMPLETE_DRIFT_DETECTED"


class CalibrationRetrainGateError(RuntimeError):
    """Raised when operator evidence/runtime gates are not armed."""


class UnsafeCorpusFilter(ValueError):
    """Raised when a retrain corpus could consume non-CONFIRMED facts."""


class FrozenReplayFailed(RuntimeError):
    """Raised by strict callers when frozen replay blocks promotion."""


@dataclass(frozen=True)
class CorpusFilter:
    """Bounded corpus selector for retrain candidates.

    `states` is intentionally present so tests and reviewers can prove that any
    non-CONFIRMED request fails before the state repository is queried.
    """

    cluster: str
    season: str
    metric_identity: MetricIdentity = HIGH_LOCALDAY_MAX
    data_version: str = "operator_retrain_candidate_v1"
    input_space: str = "width_normalized_density"
    states: tuple[str, ...] = ("CONFIRMED",)
    observed_at_start: str | None = None
    observed_at_end: str | None = None

    def __post_init__(self) -> None:
        if not self.cluster:
            raise ValueError("CorpusFilter.cluster is required")
        if not self.season:
            raise ValueError("CorpusFilter.season is required")
        if any(state != "CONFIRMED" for state in self.states):
            raise UnsafeCorpusFilter(
                "calibration retrain corpus may consume only CONFIRMED venue_trade_facts"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cluster": self.cluster,
            "season": self.season,
            "temperature_metric": self.metric_identity.temperature_metric,
            "data_version": self.data_version,
            "input_space": self.input_space,
            "states": list(self.states),
            "observed_at_start": self.observed_at_start,
            "observed_at_end": self.observed_at_end,
        }


@dataclass(frozen=True)
class CalibrationParams:
    """Candidate Platt params produced by a packet-approved fitter."""

    A: float
    B: float
    C: float
    bootstrap_params: Sequence[Sequence[float]] = field(default_factory=tuple)
    n_samples: int = 0
    fit_loss_metric: float | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "A": float(self.A),
            "B": float(self.B),
            "C": float(self.C),
            "bootstrap_params": [list(p) for p in self.bootstrap_params],
            "n_samples": int(self.n_samples),
            "fit_loss_metric": self.fit_loss_metric,
        }


@dataclass(frozen=True)
class FrozenReplayResult:
    passed: bool
    evidence_hash: str
    message: str = ""

    def __post_init__(self) -> None:
        if self.passed and not self.evidence_hash:
            raise ValueError("frozen replay PASS requires evidence_hash")


@dataclass(frozen=True)
class ArmedRetrain:
    operator_token_hash: str
    evidence_path: str


@dataclass(frozen=True)
class RetrainResult:
    status: RetrainStatus
    promoted: bool
    confirmed_trade_count: int
    frozen_replay_status: Literal["PASS", "FAIL", "SKIPPED"]
    version_id: int | None = None
    message: str = ""


FrozenReplayRunner = Callable[[CorpusFilter, CalibrationParams, Sequence[dict[str, Any]]], FrozenReplayResult]
PromoteWriter = Callable[..., None]
DeactivateWriter = Callable[..., int]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy_env(flag_name: str, environ: Mapping[str, str]) -> bool:
    return str(environ.get(flag_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _artifact_exists(*, root: Path, pattern: str = ARTIFACT_PATTERN) -> bool:
    return any(root.glob(pattern))


def _evidence_path_matches(path: Path, *, root: Path, pattern: str = ARTIFACT_PATTERN) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return fnmatch(rel, pattern)


def status(
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RetrainStatus:
    """Return DISABLED unless both operator artifact and env flag are present."""

    env = environ or os.environ
    base = root or _project_root()
    if not _truthy_env(ENV_FLAG_NAME, env):
        return RetrainStatus.DISABLED
    if not _artifact_exists(root=base):
        return RetrainStatus.DISABLED
    return RetrainStatus.ARMED


def arm(
    operator_token: str,
    evidence_path: str | Path,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ArmedRetrain:
    """Validate the operator token + evidence file + runtime gate."""

    if not operator_token or not str(operator_token).strip():
        raise CalibrationRetrainGateError("operator_token is required to arm calibration retrain")
    env = environ or os.environ
    if not _truthy_env(ENV_FLAG_NAME, env):
        raise CalibrationRetrainGateError(f"{ENV_FLAG_NAME}=1 is required to arm calibration retrain")
    secret = str(env.get(OPERATOR_TOKEN_SECRET_ENV, "")).strip()
    if not secret:
        raise CalibrationRetrainGateError(
            f"{OPERATOR_TOKEN_SECRET_ENV} is required to validate calibration retrain operator token"
        )
    base = root or _project_root()
    path = Path(evidence_path)
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise CalibrationRetrainGateError(f"operator evidence path does not exist: {path}")
    if not _evidence_path_matches(path, root=base):
        raise CalibrationRetrainGateError(
            f"operator evidence path must match {ARTIFACT_PATTERN}: {path}"
        )
    token_hash = _signed_operator_token_hash(str(operator_token), secret)
    return ArmedRetrain(operator_token_hash=token_hash, evidence_path=str(path))


def _signed_operator_token_hash(operator_token: str, secret: str) -> str:
    parts = operator_token.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise CalibrationRetrainGateError(
            "operator_token must use signed v1.<operator_id>.<nonce>.<hmac_sha256> format"
        )
    version, operator_id, nonce, signature = parts
    if not operator_id or len(nonce) < 8:
        raise CalibrationRetrainGateError("operator_token operator_id and nonce are required")
    message = f"{version}.{operator_id}.{nonce}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise CalibrationRetrainGateError("operator_token signature mismatch")
    return hashlib.sha256(operator_token.encode("utf-8")).hexdigest()


def _ensure_versions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calibration_params_versions (
          version_id INTEGER PRIMARY KEY AUTOINCREMENT,
          fitted_at TEXT NOT NULL,
          corpus_filter_json TEXT NOT NULL,
          params_json TEXT NOT NULL,
          fit_loss_metric REAL,
          confirmed_trade_count INTEGER NOT NULL,
          frozen_replay_status TEXT CHECK (frozen_replay_status IN ('PASS','FAIL','SKIPPED')),
          frozen_replay_evidence_hash TEXT,
          promoted_at TEXT,
          retired_at TEXT,
          operator_token_hash TEXT NOT NULL,
          temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
          cluster TEXT NOT NULL,
          season TEXT NOT NULL,
          data_version TEXT NOT NULL,
          input_space TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calibration_params_versions_live
        ON calibration_params_versions(temperature_metric, cluster, season, data_version, input_space, promoted_at, retired_at)
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def load_confirmed_corpus(conn: sqlite3.Connection, corpus_filter: CorpusFilter) -> list[dict[str, Any]]:
    """Load retrain facts through the U2 CONFIRMED-only repository seam."""

    if any(state != "CONFIRMED" for state in corpus_filter.states):
        raise UnsafeCorpusFilter(
            "calibration retrain corpus may consume only CONFIRMED venue_trade_facts"
        )
    try:
        rows = load_calibration_trade_facts(conn, states=corpus_filter.states)
    except ValueError as exc:
        raise UnsafeCorpusFilter(str(exc)) from exc
    if corpus_filter.observed_at_start is not None:
        rows = [row for row in rows if str(row.get("observed_at", "")) >= corpus_filter.observed_at_start]
    if corpus_filter.observed_at_end is not None:
        rows = [row for row in rows if str(row.get("observed_at", "")) <= corpus_filter.observed_at_end]
    return _filter_corpus_identity(rows, corpus_filter)


def _filter_corpus_identity(rows: Sequence[dict[str, Any]], corpus_filter: CorpusFilter) -> list[dict[str, Any]]:
    """Fail closed on missing identity and exclude mismatched retrain facts.

    U2's CONFIRMED state proves a fill is canonical; it does not by itself prove
    that the fact belongs to this calibration family.  F2 therefore requires
    each retrain fact to carry explicit calibration identity in
    ``raw_payload_json`` (either top-level or under ``calibration_identity``).
    Missing identity is a hard error because silently accepting legacy/foreign
    rows would mix high/low, cluster, season, or input-space corpora.
    """

    matched: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in rows:
        identity = _corpus_identity(row)
        if not _identity_is_complete(identity):
            missing.append(str(row.get("trade_fact_id") or row.get("trade_id") or "?"))
            continue
        expected = {
            "cluster": corpus_filter.cluster,
            "season": corpus_filter.season,
            "temperature_metric": corpus_filter.metric_identity.temperature_metric,
            "data_version": corpus_filter.data_version,
            "input_space": corpus_filter.input_space,
        }
        if all(str(identity.get(key)) == str(value) for key, value in expected.items()):
            matched.append(row)
    if missing:
        raise UnsafeCorpusFilter(
            "confirmed retrain facts missing calibration identity: "
            + ", ".join(missing[:5])
        )
    return matched


def _corpus_identity(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("raw_payload_json")
    payload: Any = {}
    if isinstance(raw, Mapping):
        payload = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("calibration_identity")
    if isinstance(nested, Mapping):
        return nested
    return payload


def _identity_is_complete(identity: Mapping[str, Any]) -> bool:
    required = ("cluster", "season", "temperature_metric", "data_version", "input_space")
    return all(str(identity.get(key) or "").strip() for key in required)


def _insert_version(
    conn: sqlite3.Connection,
    *,
    corpus_filter: CorpusFilter,
    params: CalibrationParams,
    frozen_replay_status: Literal["PASS", "FAIL", "SKIPPED"],
    frozen_replay_evidence_hash: str | None,
    confirmed_trade_count: int,
    operator_token_hash: str,
    fitted_at: str,
    promoted_at: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO calibration_params_versions (
          fitted_at, corpus_filter_json, params_json, fit_loss_metric,
          confirmed_trade_count, frozen_replay_status, frozen_replay_evidence_hash,
          promoted_at, retired_at, operator_token_hash, temperature_metric,
          cluster, season, data_version, input_space
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            fitted_at,
            json.dumps(corpus_filter.to_json_dict(), sort_keys=True),
            json.dumps(params.to_json_dict(), sort_keys=True),
            params.fit_loss_metric,
            confirmed_trade_count,
            frozen_replay_status,
            frozen_replay_evidence_hash,
            promoted_at,
            operator_token_hash,
            corpus_filter.metric_identity.temperature_metric,
            corpus_filter.cluster,
            corpus_filter.season,
            corpus_filter.data_version,
            corpus_filter.input_space,
        ),
    )
    return int(cur.lastrowid)


def trigger_retrain(
    conn: sqlite3.Connection,
    corpus_filter: CorpusFilter,
    *,
    params: CalibrationParams,
    operator_token: str,
    evidence_path: str | Path,
    frozen_replay_runner: FrozenReplayRunner,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    promote_writer: PromoteWriter = save_platt_model_v2,
    deactivate_writer: DeactivateWriter | None = deactivate_model_v2,
) -> RetrainResult:
    """Gate, replay-check, and promote a candidate calibration version.

    The caller supplies candidate params/fitter output. F2 deliberately does not
    auto-fit from ambient data; that later operator action must pass through this
    gate and frozen-replay antibody.
    """

    armed = arm(operator_token, evidence_path, root=root, environ=environ)
    rows = load_confirmed_corpus(conn, corpus_filter)
    replay = frozen_replay_runner(corpus_filter, params, rows)
    _ensure_versions_table(conn)
    now = _utc_now()

    if not replay.passed:
        with conn:
            version_id = _insert_version(
                conn,
                corpus_filter=corpus_filter,
                params=params,
                frozen_replay_status="FAIL",
                frozen_replay_evidence_hash=replay.evidence_hash or "",
                confirmed_trade_count=len(rows),
                operator_token_hash=armed.operator_token_hash,
                fitted_at=now,
                promoted_at=None,
            )
        return RetrainResult(
            status=RetrainStatus.COMPLETE_DRIFT_DETECTED,
            promoted=False,
            confirmed_trade_count=len(rows),
            frozen_replay_status="FAIL",
            version_id=version_id,
            message=replay.message or "frozen replay drift detected; promotion blocked",
        )

    with conn:
        conn.execute(
            """
            UPDATE calibration_params_versions
               SET retired_at = ?
             WHERE temperature_metric = ?
               AND cluster = ?
               AND season = ?
               AND data_version = ?
               AND input_space = ?
               AND promoted_at IS NOT NULL
               AND retired_at IS NULL
            """,
            (
                now,
                corpus_filter.metric_identity.temperature_metric,
                corpus_filter.cluster,
                corpus_filter.season,
                corpus_filter.data_version,
                corpus_filter.input_space,
            ),
        )
        version_id = _insert_version(
            conn,
            corpus_filter=corpus_filter,
            params=params,
            frozen_replay_status="PASS",
            frozen_replay_evidence_hash=replay.evidence_hash,
            confirmed_trade_count=len(rows),
            operator_token_hash=armed.operator_token_hash,
            fitted_at=now,
            promoted_at=now,
        )
        if deactivate_writer is not None and _table_exists(conn, "platt_models_v2"):
            deactivate_writer(
                conn=conn,
                metric_identity=corpus_filter.metric_identity,
                cluster=corpus_filter.cluster,
                season=corpus_filter.season,
                data_version=corpus_filter.data_version,
                input_space=corpus_filter.input_space,
            )
        promote_writer(
            conn=conn,
            metric_identity=corpus_filter.metric_identity,
            cluster=corpus_filter.cluster,
            season=corpus_filter.season,
            data_version=corpus_filter.data_version,
            param_A=params.A,
            param_B=params.B,
            param_C=params.C,
            bootstrap_params=[tuple(p) for p in params.bootstrap_params],
            n_samples=params.n_samples or len(rows),
            brier_insample=params.fit_loss_metric,
            input_space=corpus_filter.input_space,
            authority="VERIFIED",
        )
    return RetrainResult(
        status=RetrainStatus.COMPLETE_REPLAYED,
        promoted=True,
        confirmed_trade_count=len(rows),
        frozen_replay_status="PASS",
        version_id=version_id,
        message=replay.message,
    )


# =====================================================================
# LEARNING_LOOP packet — BATCH 1 read-only listing function
# =====================================================================
# Per round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) + ULTIMATE_PLAN.md §4 #4
# (settlement-corpus → calibration update → parameter-drift → re-fit pipeline).
# PATH A measurement-only framing per LEARNING_LOOP boot §2 BATCH 1 + GO_BATCH_1
# §1 ACCEPT-DEFAULT.
#
# K1 contract: pure SELECT, no INSERT/UPDATE/DELETE, no JSON persistence.
# Sibling-coherent with CALIBRATION_HARDENING BATCH 1 canonical-read additions
# (list_active_platt_models_v2 / list_active_platt_models_legacy in store.py).
# Critic-harness 30th cycle should pay particular attention to this surface
# since src/calibration/retrain_trigger.py is HIGH per src/calibration/AGENTS.md
# L19 (operator-gated retrain promotion seam — live calibration promotion
# control file). The read function below is purely additive: no impact on
# arm() / trigger_retrain() / _insert_version() / _ensure_versions_table()
# / load_confirmed_corpus() control flow.


def list_recent_retrain_versions(
    conn: sqlite3.Connection,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List recent calibration_params_versions rows in fitted_at-DESC order.

    K1-compliant pure-SELECT lister. Read-side counterpart to the writer-side
    INSERT at trigger_retrain (L368-391) + UPDATE at L443-464 (retired_at).
    Returns one dict per row covering both PASSED (promoted) and FAILED
    (drift-detected, blocked from promotion) retrain attempts so operators
    can audit the full retrain history.

    Args:
        conn: open sqlite3 connection to a Zeus state DB.
        limit: max rows to return (most recent first by fitted_at).
            Default 100 (sibling-coherent with WP/CALIBRATION sample-window
            rationale: enough history for ~3-month trend without unbounded
            scan).

    Returns:
        list of dicts ordered by fitted_at DESC. Each dict carries the full
        calibration_params_versions row shape suitable for JSON
        serialization. Empty list when no retrain attempts exist (typical
        on a fresh DB OR when the calibration_params_versions table has not
        yet been created — _ensure_versions_table is called lazily inside
        trigger_retrain, so the table only materializes on first retrain
        attempt; pre-first-retrain DBs are graceful empty).

    Used by src.state.learning_loop_observation.compute_learning_loop_state_per_bucket
    (LEARNING_LOOP BATCH 1) to enumerate per-bucket retrain trajectory
    without re-traversing the canonical surface row-by-row at upper layers.
    """
    try:
        rows = conn.execute(
            """
            SELECT version_id, fitted_at, corpus_filter_json, params_json,
                   fit_loss_metric, confirmed_trade_count, frozen_replay_status,
                   frozen_replay_evidence_hash, promoted_at, retired_at,
                   operator_token_hash, temperature_metric, cluster, season,
                   data_version, input_space
            FROM calibration_params_versions
            ORDER BY fitted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Pre-first-retrain DB without calibration_params_versions table —
        # mirrors store.py:list_active_platt_models_v2 graceful-empty posture.
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "version_id": row["version_id"],
            "fitted_at": row["fitted_at"],
            "corpus_filter_json": row["corpus_filter_json"],
            "params_json": row["params_json"],
            "fit_loss_metric": row["fit_loss_metric"],
            "confirmed_trade_count": row["confirmed_trade_count"],
            "frozen_replay_status": row["frozen_replay_status"],
            "frozen_replay_evidence_hash": row["frozen_replay_evidence_hash"],
            "promoted_at": row["promoted_at"],
            "retired_at": row["retired_at"],
            "operator_token_hash": row["operator_token_hash"],
            "temperature_metric": row["temperature_metric"],
            "cluster": row["cluster"],
            "season": row["season"],
            "data_version": row["data_version"],
            "input_space": row["input_space"],
        })
    return out
