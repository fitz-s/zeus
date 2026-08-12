"""Control plane: runtime commands from OpenClaw/Venus without process restart.

Blueprint v2 §10: Supported commands read from state/control_plane.json.
Narrow-by-intent: each command does exactly one thing.
"""

import json
import logging
import os
import sys
import tempfile
import threading
import traceback as _traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Iterable, Mapping

import fcntl

from src.architecture.decorators import capability, protects
from src.config import state_path
from src.control.gate_decision import GateDecision, ReasonCode
from src.contracts.review_work_item import ResolveWorkItemRequest
from src.state.db import (
    DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    expire_control_override,
    get_trade_connection,
    get_trade_connection_read_only,
    get_world_connection,
    get_world_connection_read_only,
    get_world_connection_with_trades_required,
    query_control_override_state,
    upsert_control_override,
)
from src.state.review_work_items import resolve_work_item
from src.state.schema.review_work_items_schema import ensure_table as _ensure_review_work_items_table

# Live-blockers 2026-05-01: auto-pause overrides default to a 15-minute
# expiry so a single transient API/DB failure cannot permanently lock out
# entries. If the underlying issue persists, the next cycle will re-pause;
# if it was transient, entries auto-resume after the window. Operator
# overrides (issued_by="control_plane") are unaffected — they pass an
# explicit ``effective_until`` (or ``None`` for indefinite).
AUTO_PAUSE_DEFAULT_EXPIRY_SECONDS = 15 * 60

logger = logging.getLogger(__name__)

CONTROL_PATH = state_path("control_plane.json")
DEFAULT_EDGE_THRESHOLD_MULTIPLIER = 1.0
TIGHTENED_EDGE_THRESHOLD_MULTIPLIER = 2.0

# G6 antibody (2026-04-26): typed boot/catalog allowlist of strategies that
# may be enabled when the live-only daemon starts. Post-A4 (PLAN.md §A4 +
# Bug review §E) this is derived from the StrategyProfile registry —
# Only registered live strategies exist on this surface.
#
# ``LIVE_SAFE_STRATEGIES`` and ``_LIVE_ALLOWED_STRATEGIES`` remain
# importable names (backward compat for tests/test_live_safe_strategies.py
# and any other caller); they're now lazy attributes resolved through
# ``__getattr__`` so a test that swaps the registry via
# ``strategy_profile._reload_for_test`` picks up the change without
# re-importing this module.


def __getattr__(name: str):
    """PEP 562 lazy attribute resolution. Defers strategy_profile import
    so the registry is only loaded when a caller actually asks for one
    of these symbols (avoids import cycles + makes test reload trivial).
    """
    if name == "LIVE_SAFE_STRATEGIES":
        from src.strategy.strategy_profile import live_safe_keys
        return live_safe_keys()
    if name == "_LIVE_ALLOWED_STRATEGIES":
        from src.strategy.strategy_profile import live_allowed_keys
        return live_allowed_keys()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

COMMANDS = {
    "pause_entries",                # Stop entering, keep monitoring
    "resume",                       # Clear temporary global controls and resume entries
    "tighten_risk",                 # Double edge thresholds temporarily
    "request_status",               # Force status_summary write
    "set_strategy_gate",            # Enable/disable individual strategies
    "resolve_review_item",          # CAS-resolve an operator-cleared ReviewWorkItem (T6)
    "pause_source",                 # Ingest: skip ticks for a named source until cleared
    "resume_source",                # Ingest: clear pause_source for a named source
}


def assert_live_safe_strategies_under_live_mode(enabled: Iterable[str]) -> None:
    """Refuse daemon launch if any enabled strategy is outside LIVE_SAFE_STRATEGIES under live mode.

    Helper called from src/main.py boot path. Runtime is live-only, so this
    guard is unconditional and no longer reads the retired ZEUS_MODE switch.
    Exits via SystemExit so daemon launchers consume the fatal refusal cleanly.

    Post-A4: live_safe set is derived from the StrategyProfile registry
    on each call (see __getattr__ above). Tests that monkeypatch the
    registry pick up the change without re-importing this module.
    """
    from src.strategy.strategy_profile import live_safe_keys
    enabled_set = frozenset(enabled)
    live_safe = live_safe_keys()
    offenders = sorted(enabled_set - live_safe)
    if offenders:
        sys.exit(
            f"FATAL: live mode refused — non-allowlisted strategies enabled: "
            f"{offenders}. LIVE_SAFE_STRATEGIES={sorted(live_safe)}. "
            f"Disable each via control_plane set_strategy_gate before relaunching."
        )

_control_state: dict = {}
_control_thread_lock = threading.RLock()
_control_lock_depth = threading.local()


@contextmanager
def _control_payload_transaction():
    """Serialize control-queue mutations across threads and processes."""

    with _control_thread_lock:
        depth = int(getattr(_control_lock_depth, "value", 0) or 0)
        if depth:
            _control_lock_depth.value = depth + 1
            try:
                yield
            finally:
                _control_lock_depth.value = depth
            return

        parent = os.path.dirname(str(CONTROL_PATH)) or "."
        lock_path = f"{CONTROL_PATH}.lock"
        os.makedirs(parent, exist_ok=True)
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            _control_lock_depth.value = 1
            try:
                yield
            finally:
                _control_lock_depth.value = 0
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_control_payload() -> dict:
    try:
        with open(CONTROL_PATH) as f:
            data = json.load(f)
            _set_state("control_plane_fault", False)
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        logger.error("control_plane.json corrupted (JSONDecodeError)")
        _set_state("control_plane_fault", True)
        raise ValueError("Corrupted control_plane.json encountered, treating as fatal fault") from e
    except OSError:
        return {}



def _write_control_payload(
    commands: list[dict],
    acks: list[dict],
    *,
    payload_updates: dict | None = None,
) -> None:
    with _control_payload_transaction():
        payload = _load_control_payload()
        if payload_updates:
            payload.update(payload_updates)
        payload["commands"] = commands
        payload["acks"] = acks[-20:]
        parent = os.path.dirname(str(CONTROL_PATH)) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".control_plane.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONTROL_PATH)
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass



def _extract_review_work_id(payload: dict) -> str:
    work_id = payload.get("work_id") or payload.get("workId") or ""
    return str(work_id) if work_id else ""



def _set_state(key: str, value) -> None:
    _control_state[key] = value



def _refresh_entries_pause_from_durable_state() -> dict:
    """Refresh the entry-pause fields from the durable DB authority.

    Entry pause is a live-money submit gate.  The in-process projection is only
    an operator visibility cache; it must not be a second authority because it
    can drift from the DB view used at the executor boundary.
    """

    conn = None
    try:
        conn = get_world_connection()
        durable_state = query_control_override_state(conn)
        if durable_state.get("status") != "ok":
            raise RuntimeError(
                "entries pause durable authority unavailable: "
                f"{durable_state.get('status') or 'unknown'}"
            )
    except Exception as exc:
        logger.error("entries pause durable-state query failed: %s", exc, exc_info=True)
        durable_state = {
            "status": "query_error",
            "entries_paused": True,
            "entries_pause_source": "control_db_query_error",
            "entries_pause_reason": "entries_pause_durable_state_unavailable",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if durable_state.get("status") in {"ok", "query_error"}:
        _control_state["entries_paused"] = bool(durable_state.get("entries_paused", False))
        _control_state["entries_pause_source"] = durable_state.get("entries_pause_source")
        _control_state["entries_pause_reason"] = durable_state.get("entries_pause_reason")
        _control_state["entries_pause_issued_at"] = durable_state.get("entries_pause_issued_at")
        _control_state["entries_pause_effective_until"] = durable_state.get("entries_pause_effective_until")
        _control_state["entries_pause_issued_by"] = durable_state.get("entries_pause_issued_by")
        _control_state["durable_override_status"] = durable_state.get("status", "unknown")
    return durable_state


def is_entries_paused() -> bool:
    return bool(_refresh_entries_pause_from_durable_state().get("entries_paused", False))


def get_entries_pause_source() -> str | None:
    _refresh_entries_pause_from_durable_state()
    return _control_state.get("entries_pause_source")


def get_entries_pause_reason() -> str | None:
    _refresh_entries_pause_from_durable_state()
    return _control_state.get("entries_pause_reason")


def get_entries_pause_evidence() -> dict:
    """Return the active durable entry-pause row metadata for operator visibility."""

    _refresh_entries_pause_from_durable_state()
    return {
        "issued_at": _control_state.get("entries_pause_issued_at"),
        "effective_until": _control_state.get("entries_pause_effective_until"),
        "issued_by": _control_state.get("entries_pause_issued_by"),
        "source": _control_state.get("entries_pause_source"),
        "reason": _control_state.get("entries_pause_reason"),
    }



def alert_auto_pause(reason_code: str) -> None:
    """Emit a structured log warning when entries are auto-paused by exception."""
    logger.warning(
        "auto_pause_entries",
        extra={"reason_code": reason_code},
    )


AUTO_PAUSE_OVERRIDE_ID = "control_plane:global:entries_paused"

DEPLOY_LIVE_RESTART_GUARD_REASON = "deploy_live_restart_guard"
DEPLOY_LIVE_RESTART_GUARD_ISSUER = "control_plane"
_DEPLOY_LIVE_RESTART_GUARD_MAX_MONITOR_AGE_SECONDS = 150.0
# SCOPE: global entries gate for one fixed control identity. DRAIN: the
# post-issued reactor monitor/queue proof. RESET: exact SHA+issued_at CAS only.


@dataclass(frozen=True)
class DeployLiveRestartGuardWitness:
    """Exact invocation identity carried by the fixed control override row."""

    override_id: str
    expected_sha: str
    issued_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "override_id": self.override_id,
            "expected_sha": self.expected_sha,
            "issued_at": self.issued_at,
        }


def _has_active_auto_pause_override(
    conn,
    *,
    reason_code: str,
    now_iso: str,
) -> bool:
    """Return True iff the auto-pause override is already active for this reason.

    Idempotency guard: when ``pause_entries`` fires three times in ~100ms (the
    triplet pattern observed in production logs), the second and third calls
    should not insert duplicate history rows. The check is keyed on
    (override_id, issued_by="system_auto_pause", reason, effective_until > now).
    Any failure to query is treated as "not active" so we still write — better
    to over-record than to silently lose a pause.
    """
    if conn is None:
        return False
    try:
        row = conn.execute(
            """
            SELECT issued_by, reason, value, effective_until
            FROM control_overrides
            WHERE override_id = ?
            """,
            (AUTO_PAUSE_OVERRIDE_ID,),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    try:
        issued_by = row["issued_by"]
        existing_reason = row["reason"]
        value = row["value"]
        effective_until = row["effective_until"]
    except (KeyError, IndexError, TypeError):
        return False
    if str(issued_by or "") != "system_auto_pause":
        return False
    if str(existing_reason or "") != reason_code:
        return False
    if str(value or "").strip().lower() not in {"true", "1", "yes"}:
        return False
    if effective_until is None:
        return True
    return str(effective_until) > now_iso


def _normalise_restart_guard_sha(expected_sha: object) -> str:
    value = str(expected_sha or "").strip().lower()
    if len(value) < 40 or len(value) > 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("deploy restart guard requires a full hexadecimal git SHA")
    return value


def _restart_guard_value(expected_sha: str) -> str:
    return json.dumps({"paused": True, "expected_sha": expected_sha}, sort_keys=True)


def _restart_guard_witness_from_row(row) -> DeployLiveRestartGuardWitness | None:
    if row is None:
        return None
    if (
        str(row["override_id"] or "") != AUTO_PAUSE_OVERRIDE_ID
        or str(row["target_type"] or "") != "global"
        or str(row["target_key"] or "") != "entries"
        or str(row["action_type"] or "") != "gate"
        or str(row["reason"] or "") != DEPLOY_LIVE_RESTART_GUARD_REASON
        or str(row["issued_by"] or "") != DEPLOY_LIVE_RESTART_GUARD_ISSUER
        or row["effective_until"] is not None
    ):
        return None
    try:
        payload = json.loads(str(row["value"] or ""))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("paused") is not True:
        return None
    try:
        expected_sha = _normalise_restart_guard_sha(payload.get("expected_sha"))
    except ValueError:
        return None
    issued_at = str(row["issued_at"] or "")
    if not issued_at:
        return None
    return DeployLiveRestartGuardWitness(
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        expected_sha=expected_sha,
        issued_at=issued_at,
    )


def _active_entries_pause_row(conn, *, now_iso: str):
    row = conn.execute(
        """
        SELECT override_id, target_type, target_key, action_type, value,
               issued_by, issued_at, effective_until, reason, precedence
          FROM control_overrides
         WHERE override_id = ? AND issued_at <= ?
        """,
        (AUTO_PAUSE_OVERRIDE_ID, now_iso),
    ).fetchone()
    if row is None or row["effective_until"] is not None and row["effective_until"] <= now_iso:
        return None
    raw_value = str(row["value"] or "").strip().lower()
    if raw_value in {"0", "false", "no", "off", "disabled"}:
        return None
    if raw_value not in {"1", "true", "yes", "on", "enabled"}:
        try:
            payload = json.loads(raw_value)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("paused") is not True:
            return None
    return row


def arm_deploy_live_restart_guard(
    expected_sha: str,
    *,
    issued_at: str | None = None,
) -> dict[str, object]:
    """Append one invocation witness to the existing global control row."""

    expected = _normalise_restart_guard_sha(expected_sha)
    issued = str(issued_at or datetime.now(timezone.utc).isoformat()).strip()
    if not issued:
        raise ValueError("deploy restart guard requires issued_at")
    conn = get_world_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _active_entries_pause_row(conn, now_iso=issued)
        if current is not None and _restart_guard_witness_from_row(current) is None:
            conn.rollback()
            return {
                "status": "preserved",
                "reason": str(current["reason"] or ""),
                "issued_by": str(current["issued_by"] or ""),
                "witness": None,
            }
        upsert_control_override(
            conn,
            override_id=AUTO_PAUSE_OVERRIDE_ID,
            target_type="global",
            target_key="entries",
            action_type="gate",
            value=_restart_guard_value(expected),
            issued_by=DEPLOY_LIVE_RESTART_GUARD_ISSUER,
            issued_at=issued,
            reason=DEPLOY_LIVE_RESTART_GUARD_REASON,
            effective_until=None,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    witness = DeployLiveRestartGuardWitness(AUTO_PAUSE_OVERRIDE_ID, expected, issued)
    return {"status": "armed", "witness": witness.as_dict()}


def get_active_deploy_live_restart_guard(
    *,
    now: datetime | None = None,
) -> DeployLiveRestartGuardWitness | None:
    """Return the selected active guard, never an unselected historical row."""

    now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    conn = get_world_connection_read_only()
    try:
        return _restart_guard_witness_from_row(_active_entries_pause_row(conn, now_iso=now_iso))
    finally:
        conn.close()


def _coerce_restart_guard_witness(value) -> DeployLiveRestartGuardWitness:
    if isinstance(value, DeployLiveRestartGuardWitness):
        witness = value
    elif isinstance(value, dict):
        witness = DeployLiveRestartGuardWitness(
            override_id=str(value.get("override_id") or ""),
            expected_sha=str(value.get("expected_sha") or "").strip().lower(),
            issued_at=str(value.get("issued_at") or ""),
        )
    else:
        raise TypeError("deploy restart guard witness must be a dataclass or mapping")
    expected = _normalise_restart_guard_sha(witness.expected_sha)
    if witness.expected_sha != expected:
        raise ValueError("deploy restart guard witness SHA is not canonical")
    if witness.override_id != AUTO_PAUSE_OVERRIDE_ID:
        raise ValueError("deploy restart guard witness identity mismatch")
    if not witness.issued_at:
        raise ValueError("deploy restart guard witness issued_at is missing")
    return witness


def _read_loaded_sha() -> str:
    try:
        with open(state_path("loaded_sha.json"), encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(
        payload.get("loaded_sha")
        or payload.get("boot_sha")
        or payload.get("current_sha")
        or ""
    ).strip().lower()


def _restart_guard_queue_evidence(conn, *, issued_at: str, now: datetime) -> dict[str, bool]:
    """Read reactor claimability plus bounded stale/progress probes; never aggregate history."""

    from src.events.event_store import EventStore

    stale = conn.execute(
        """
        SELECT 1
          FROM opportunity_event_processing
               INDEXED BY idx_opportunity_event_processing_stale_claim
         WHERE consumer_name = ?
           AND processing_status = 'processing'
           AND claimed_at IS NOT NULL
           AND claimed_at <= ?
           AND COALESCE(last_error, '') <> 'GLOBAL_WINNER_SUBMIT_FENCED'
         LIMIT 1
        """,
        ("edli_reactor_v1", (now - timedelta(seconds=300.0)).isoformat()),
    ).fetchone() is not None

    # SCOPE: report only event rows the reactor could claim after the deploy
    # guard resets. DRAIN: the guard itself pauses those entry claims, so
    # claimable unowned work is telemetry, not reset debt; only stale in-flight
    # ownership must drain. RESET: the post-reactor proof combines this bounded
    # stale check with loaded-SHA and complete fresh-monitor evidence, then the
    # existing witness-bound CAS expires exactly this invocation's guard.
    # Reuse the reactor's read-floor so historical rows outside its expiry,
    # selection-window, and per-city timeliness predicates remain invisible.
    claimable_pending = bool(
        EventStore(conn, consumer_name="edli_reactor_v1").fetch_pending(
            decision_time=now.isoformat(),
            limit=1,
        )
    )

    progress = False
    for status, timestamp_column in (
        ("processing", "claimed_at"),
        ("processed", "processed_at"),
        ("failed", "processed_at"),
        ("dead_letter", "processed_at"),
        ("expired", "processed_at"),
    ):
        progress = conn.execute(
            f"""
            SELECT 1
              FROM opportunity_event_processing
                   INDEXED BY idx_opportunity_event_processing_status
             WHERE consumer_name = ?
               AND processing_status = ?
               AND updated_at >= ?
               AND updated_at <= ?
               AND {timestamp_column} IS NOT NULL
               AND {timestamp_column} >= ?
               AND {timestamp_column} <= ?
             LIMIT 1
            """,
            (
                "edli_reactor_v1",
                status,
                issued_at,
                now.isoformat(),
                issued_at,
                now.isoformat(),
            ),
        ).fetchone() is not None
        if progress:
            break
    return {
        "stale_processing": stale,
        "claimable_pending": claimable_pending,
        "post_issued_progress": progress,
        "green": not stale,
    }


def prove_deploy_live_restart_guard(
    witness,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Evaluate loaded SHA plus post-issued monitor and queue evidence."""

    witness = _coerce_restart_guard_witness(witness)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        issued_dt = datetime.fromisoformat(witness.issued_at).astimezone(timezone.utc)
        loaded_sha = _read_loaded_sha()
        runtime_green = loaded_sha == witness.expected_sha

        from src.ops.monitor_cadence import collect_monitor_cadence_evidence

        trade_conn = get_trade_connection_read_only()
        try:
            monitor = collect_monitor_cadence_evidence(
                trade_conn,
                now=now_utc,
                min_occurred_at=issued_dt,
                max_age_seconds=_DEPLOY_LIVE_RESTART_GUARD_MAX_MONITOR_AGE_SECONDS,
                strict_future=True,
                monitor_refreshed_only=True,
                require_fresh_inputs=True,
                sample_limit=5,
            )
        finally:
            trade_conn.close()

        world_conn = get_world_connection_read_only()
        try:
            queue = _restart_guard_queue_evidence(
                world_conn,
                issued_at=issued_dt.isoformat(),
                now=now_utc,
            )
        finally:
            world_conn.close()
        monitor_green = (
            int(monitor.get("future_monitor_event_count") or 0) == 0
            and int(monitor.get("stale_or_missing_position_count") or 0) == 0
        )
        queue_green = bool(queue.get("green"))
        green = (
            runtime_green
            and monitor_green
            and queue_green
        )
        return {
            "green": green,
            "witness": witness.as_dict(),
            "runtime": {"loaded_sha": loaded_sha, "expected_sha": witness.expected_sha, "green": runtime_green},
            "monitor": {**monitor, "green": monitor_green},
            "queue": {**queue, "green": queue_green},
        }
    except Exception as exc:
        return {
            "green": False,
            "witness": witness.as_dict(),
            "reason": f"DEPLOY_RESTART_GUARD_PROOF_UNAVAILABLE:{type(exc).__name__}:{exc}",
        }


def reset_deploy_live_restart_guard(
    witness,
    *,
    proof: dict[str, object],
    retired_at: str | None = None,
) -> dict[str, object]:
    """CAS-retire exactly the selected guard after a green proof."""

    witness = _coerce_restart_guard_witness(witness)
    if proof.get("green") is not True or proof.get("witness") != witness.as_dict():
        return {"status": "refused", "reason": "restart_guard_proof_not_green"}
    retired = str(retired_at or datetime.now(timezone.utc).isoformat())
    conn = get_world_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _active_entries_pause_row(conn, now_iso=retired)
        current = _restart_guard_witness_from_row(row)
        if current != witness:
            conn.rollback()
            return {"status": "noop", "reason": "restart_guard_invocation_mismatch"}
        result = expire_control_override(
            conn,
            override_id=witness.override_id,
            expired_at=retired,
        )
        if int(result.get("expired_count") or 0) != 1:
            conn.rollback()
            return {"status": "noop", "reason": "restart_guard_already_retired"}
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    logger.warning(
        "DEPLOY_LIVE_RESTART_GUARD_RESET expected_sha=%s issued_at=%s",
        witness.expected_sha,
        witness.issued_at,
    )
    return {"status": "reset", "witness": witness.as_dict()}


def recover_deploy_live_restart_guard() -> dict[str, object]:
    """Prove and CAS-reset the selected guard after one reactor invocation."""

    witness = get_active_deploy_live_restart_guard()
    if witness is None:
        return {"status": "noop", "reason": "restart_guard_not_selected"}
    proof = prove_deploy_live_restart_guard(witness)
    result = reset_deploy_live_restart_guard(witness, proof=proof)
    result["proof"] = proof
    return result


def _has_active_control_plane_override(conn, *, now_iso: str) -> bool:
    """Return True iff an indefinite operator/control_plane row is active.

    Used by pause_entries(issued_by='system_auto_pause') to refuse overwriting
    an operator-issued indefinite freeze (PRECEDENCE-1). The check is keyed on
    issued_by IN ('control_plane','operator') AND effective_until IS NULL.
    Any query failure returns False so we err toward writing (fail-open for
    precedence, fail-closed for pause).
    """
    if conn is None:
        return False
    try:
        row = conn.execute(
            """
            SELECT issued_by, value, effective_until
            FROM control_overrides
            WHERE override_id = ?
            """,
            (AUTO_PAUSE_OVERRIDE_ID,),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    try:
        issued_by = row["issued_by"]
        value = row["value"]
        effective_until = row["effective_until"]
    except (KeyError, IndexError, TypeError):
        return False
    if str(issued_by or "") not in {"control_plane", "operator"}:
        return False
    raw_value = str(value or "").strip().lower()
    if raw_value not in {"true", "1", "yes", "on", "enabled"}:
        try:
            payload = json.loads(raw_value)
        except ValueError:
            return False
        if not isinstance(payload, dict) or payload.get("paused") is not True:
            return False
    return effective_until is None


def pause_entries(
    reason_code: str,
    *,
    effective_until: str | None = None,
    issued_by: str = "system_auto_pause",
) -> None:
    """Auto-pause entries after an unhandled exception in the entry path.

    Sets entries_paused and records the machine-readable reason_code in
    _control_state, then emits an alert. Also persists to DB so the pause
    survives a daemon restart.

    Live-blockers 2026-05-01:
    - When ``effective_until`` is omitted AND ``issued_by`` is the auto-pause
      writer, default to ``now + AUTO_PAUSE_DEFAULT_EXPIRY_SECONDS`` so the
      pause auto-resumes after the window if the issue was transient. The
      next cycle will re-pause (and re-arm the streak) if the failure
      persists. Manual operator pauses (``issued_by="control_plane"``) are
      indefinite by default — caller passes ``effective_until`` explicitly.
    - Idempotent on (override_id, reason_code, currently active): if the
      same auto-pause is already active in the DB we skip the duplicate
      history insert and just refresh in-memory state.
    """
    _control_state["entries_paused"] = True
    _control_state["entries_pause_source"] = "auto_exception" if issued_by == "system_auto_pause" else "manual_command"
    _control_state["entries_pause_reason"] = reason_code
    alert_auto_pause(reason_code)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    if effective_until is None and issued_by == "system_auto_pause":
        effective_until = (
            now + timedelta(seconds=AUTO_PAUSE_DEFAULT_EXPIRY_SECONDS)
        ).isoformat()
    # Persist so a daemon restart does not silently lose the pause.
    try:
        conn = get_world_connection()
        # PRECEDENCE-1 (2026-05-18): refuse to weaken an indefinite operator freeze.
        # Only system_auto_pause callers honor this; operator/control_plane callers
        # come through _apply_command, not here, so operator authority is absolute.
        if issued_by == "system_auto_pause" and _has_active_control_plane_override(conn, now_iso=now_iso):
            conn.close()
            _caller_frames = "".join(_traceback.format_stack()[-6:-1])
            logger.warning(
                "PRECEDENCE_SKIP_AUTO_PAUSE_OVER_OPERATOR_FREEZE reason=%s issued_by=%s attempted_at=%s"
                " — operator indefinite freeze active.\nCaller stack (most recent last):\n%s",
                reason_code, issued_by, now_iso, _caller_frames,
            )
            # PRECEDENCE-1 fix: in-memory state was already overwritten to
            # auto_exception values above (L284-286) before we checked the
            # DB.  Restore from DB so status consumers reflect the operator
            # freeze source/reason, not the attempted auto-pause.
            refresh_control_state()
            return
        if _has_active_auto_pause_override(conn, reason_code=reason_code, now_iso=now_iso):
            logger.debug(
                "pause_entries idempotent skip — override already active for reason=%s",
                reason_code,
            )
            conn.commit()
            conn.close()
            return
        upsert_control_override(
            conn,
            override_id=AUTO_PAUSE_OVERRIDE_ID,
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by=issued_by,
            issued_at=now_iso,
            reason=reason_code,
            effective_until=effective_until,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        conn.commit()
        conn.close()
        # SF6 antibody (2026-05-04): pause_entries was silent on success for the
        # entire 16-day live-block loop — only DB rows were left, no stderr trace.
        # Every successful auto-pause MUST leave a greppable footprint with the
        # full caller stack so future "mystery pauses" can be traced to their raise site.
        _caller_frames = "".join(_traceback.format_stack()[-6:-1])
        logger.warning(
            "ENTRIES_AUTO_PAUSED_DB_WRITTEN reason=%s issued_by=%s effective_until=%s\nCaller stack (most recent last):\n%s",
            reason_code, issued_by, effective_until, _caller_frames,
        )
    except Exception as exc:
        logger.error("Failed to persist auto-pause to DB: %s", exc, exc_info=True)
        _control_state["control_db_fault"] = True
        try:
            alert_auto_pause(f"{reason_code}_db_fault")
        except Exception:
            pass


def resume_entries(
    reason: str,
    *,
    issued_by: str = "control_plane",
    expected_override_issued_at: str | None = None,
) -> None:
    """CAS-resume the exact entry pause observed by an operator caller."""
    if issued_by not in {"control_plane", "operator"}:
        raise ValueError(
            f"resume_entries requires issued_by in {{control_plane, operator}}, got {issued_by!r}"
        )
    expected_issued_at = str(expected_override_issued_at or "").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = None
    try:
        # SCOPE: one selected entries-pause generation. DRAIN: an operator
        # re-reads its issued_at and retries explicitly. RESET: only that exact
        # generation may expire; a newer pause remains selected.
        conn = get_world_connection()
        conn.execute("BEGIN IMMEDIATE")
        current = _active_entries_pause_row(conn, now_iso=now_iso)
        current_issued_at = (
            str(current["issued_at"] or "") if current is not None else ""
        )
        current_issued_by = (
            str(current["issued_by"] or "") if current is not None else ""
        )
        if not expected_issued_at:
            if current_issued_by != "system_auto_pause":
                conn.rollback()
                raise ValueError(
                    "resume_entries requires expected_override_issued_at for "
                    "an operator/control-plane pause"
                )
            expected_issued_at = current_issued_at
        if current_issued_at != expected_issued_at:
            conn.rollback()
            raise ValueError(
                "resume_entries override changed: "
                f"expected={expected_issued_at!r} current={current_issued_at!r}"
            )
        expire_control_override(
            conn,
            override_id=AUTO_PAUSE_OVERRIDE_ID,
            expired_at=now_iso,
        )
        expire_control_override(
            conn,
            override_id="control_plane:global:edge_threshold_multiplier",
            expired_at=now_iso,
        )
        conn.commit()
        logger.warning("ENTRIES_RESUMED reason=%s issued_by=%s", reason, issued_by)
    except ValueError:
        logger.warning(
            "ENTRIES_RESUME_CAS_REFUSED reason=%s issued_by=%s",
            reason,
            issued_by,
        )
        raise
    except Exception as exc:
        logger.error("Failed to persist resume to DB: %s", exc, exc_info=True)
        _control_state["control_db_fault"] = True
        raise
    finally:
        if conn is not None:
            conn.close()
    # Refresh in-memory state from DB so downstream callers see the cleared pause
    refresh_control_state()


def retire_entries_pause_for_reasons(
    reasons: Iterable[str],
    *,
    retirement_reason: str,
) -> bool:
    """Expire only the active entry pause when its reason is explicitly retired.

    This is narrower than the operator ``resume`` command: it never clears an
    independent edge-threshold or strategy override. The read and expiry share
    one immediate transaction so a newer pause cannot be cleared by a stale
    boot-time observation.
    """

    retired = {str(reason).strip() for reason in reasons if str(reason).strip()}
    if not retired:
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_world_connection()
    expired = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = query_control_override_state(conn, now=now_iso)
        if not state.get("entries_paused") or state.get("entries_pause_reason") not in retired:
            conn.rollback()
            return False
        result = expire_control_override(
            conn,
            override_id=AUTO_PAUSE_OVERRIDE_ID,
            expired_at=now_iso,
        )
        expired = int(result.get("expired_count") or 0) == 1
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        _control_state["control_db_fault"] = True
        raise
    finally:
        conn.close()
    refresh_control_state()
    if expired:
        logger.warning("ENTRIES_PAUSE_RETIRED reason=%s", retirement_reason)
    return expired


def get_edge_threshold_multiplier() -> float:
    return _control_state.get("edge_threshold_multiplier", DEFAULT_EDGE_THRESHOLD_MULTIPLIER)


def strategy_gates() -> dict[str, GateDecision]:
    """Return the current strategy_gates table from _control_state.

    BLOCKER #2 fix (2026-04-26, con-nyx review): accepts both dict shape
    (current production — emitted by db.py::query_control_override_state
    post-fix and by control_plane._apply_command set_strategy_gate handler)
    AND legacy bare-bool shape (defense-in-depth for any cache-state path
    that bypasses the BLOCKER #2 fix). The bool branch synthesizes an
    UNSPECIFIED GateDecision so callers (is_strategy_enabled, status_summary)
    don't crash on residual legacy state. Path 1 + Path 2 together cover
    bool/dict mismatch from both ends.
    """
    raw = _control_state.get("strategy_gates", {})
    result = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            result[k] = GateDecision.from_dict(v)
        elif isinstance(v, bool):
            result[k] = GateDecision(
                enabled=v,
                reason_code=ReasonCode.UNSPECIFIED,
                reason_snapshot={},
                gated_at="",
                gated_by="legacy_bool_cache",
            )
        else:
            raise ValueError(
                f"Strategy gate for {k!r} has unsupported type {type(v).__name__}: {v!r}. "
                "Expected dict (GateDecision shape) or bool (legacy)."
            )
    return result


def is_strategy_enabled(strategy: str) -> bool:
    """Runtime live-entry gate. Post-A4: derived from the StrategyProfile
    registry on each call (live_status == "live"). Pre-A4 this read a
    hardcoded ``_LIVE_ALLOWED_STRATEGIES`` set; the symbol stays importable
    via __getattr__ above for backward compat.
    """
    if not strategy:
        return True
    if _control_state.get("live_allowed_strategies_status") != "ok":
        logger.error(
            "Strategy evidence-tier authority unavailable; failing closed for %s",
            strategy,
        )
        return False
    allowed = frozenset(_control_state.get("live_allowed_strategies", frozenset()))
    if strategy not in allowed:
        return False
    decision = strategy_gates().get(strategy)
    if decision is None:
        return True
    return decision.enabled


def _refresh_live_allowed_strategy_cache(conn=None) -> None:
    """Refresh the evidence-tier live-allowed cache without touching gates."""
    own_conn = conn is None
    try:
        if conn is None:
            from src.state.db import get_world_connection_read_only
            conn = get_world_connection_read_only()
        from src.strategy.strategy_profile import live_allowed_keys
        _control_state["live_allowed_strategies"] = live_allowed_keys(conn=conn)
        _control_state["live_allowed_strategies_status"] = "ok"
    except Exception:
        logger.exception("Strategy evidence-tier authority unavailable")
        _control_state["live_allowed_strategies"] = frozenset()
        _control_state["live_allowed_strategies_status"] = "query_error"
    finally:
        if own_conn and conn is not None:
            conn.close()



def refresh_control_state() -> None:
    data = _load_control_payload()
    entries_paused = False
    edge_threshold_multiplier = DEFAULT_EDGE_THRESHOLD_MULTIPLIER
    gates: dict[str, bool] = {}
    durable_state = {"status": "skipped_no_connection"}
    conn = None
    try:
        conn = get_world_connection_with_trades_required()
        durable_state = query_control_override_state(conn)
        _refresh_live_allowed_strategy_cache(conn)
    except Exception:
        durable_state = {
            "status": "query_error",
            "entries_paused": True,
            "edge_threshold_multiplier": float(TIGHTENED_EDGE_THRESHOLD_MULTIPLIER)
        }
        _control_state["live_allowed_strategies"] = frozenset()
        _control_state["live_allowed_strategies_status"] = "query_error"
    finally:
        if conn is not None:
            conn.close()
    if durable_state.get("status") != "ok":
        durable_state = {
            "status": "query_error",
            "entries_paused": True,
            "entries_pause_source": "control_db_query_error",
            "entries_pause_reason": "entries_pause_durable_state_unavailable",
            "edge_threshold_multiplier": float(TIGHTENED_EDGE_THRESHOLD_MULTIPLIER),
            "strategy_gates": {},
        }
    if durable_state.get("status") in {"ok", "query_error"}:
        entries_paused = bool(durable_state.get("entries_paused", False))
        edge_threshold_multiplier = float(
            durable_state.get("edge_threshold_multiplier", DEFAULT_EDGE_THRESHOLD_MULTIPLIER)
        )
        gates = dict(durable_state.get("strategy_gates", {}))
        
    # Tombstone reader retired 2026-05-04 — gate-purge Stage 2.
    # entries_paused now comes only from DB control_overrides (gate 3).

    _control_state["entries_paused"] = entries_paused
    _control_state["entries_pause_source"] = durable_state.get("entries_pause_source")
    _control_state["entries_pause_reason"] = durable_state.get("entries_pause_reason")
    _control_state["entries_pause_issued_at"] = durable_state.get("entries_pause_issued_at")
    _control_state["entries_pause_effective_until"] = durable_state.get("entries_pause_effective_until")
    _control_state["entries_pause_issued_by"] = durable_state.get("entries_pause_issued_by")
    _control_state["edge_threshold_multiplier"] = edge_threshold_multiplier
    _control_state["strategy_gates"] = gates
    _control_state["durable_override_status"] = durable_state.get("status", "unknown")



def clear_control_state() -> None:
    _control_state.clear()
    refresh_control_state()



def _acknowledge_command(name: str, cmd: dict, *, status: str, reason: str = "") -> dict:
    ack = {
        "command": name,
        "acked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
    }
    work_id = _extract_review_work_id(cmd)
    if work_id:
        ack["work_id"] = work_id
    if cmd.get("strategy"):
        ack["strategy"] = cmd["strategy"]
    if isinstance(cmd.get("enabled"), bool):
        ack["enabled"] = cmd["enabled"]
    if cmd.get("condition_id"):
        ack["condition_id"] = cmd["condition_id"]
    if cmd.get("note"):
        ack["note"] = cmd["note"]
    if reason:
        ack["reason"] = reason
    return ack


def _lower_precedence_override_reason(
    conn,
    *,
    override_id: str,
    requested_precedence: int,
    now_iso: str,
    protect_equal: bool = False,
    expected_override_issued_at: str = "",
) -> str:
    """Return an audit reason when a live override outranks this command.

    Equal-precedence commands that weaken a gate use an issued-at compare-and-set.
    This prevents a delayed command from undoing a newer live-money containment
    row while preserving an explicit, current operator release path.
    """

    if conn is None:
        return ""
    row = conn.execute(
        """
        SELECT precedence, issued_at
          FROM control_overrides
         WHERE override_id = ?
           AND issued_at <= ?
           AND (effective_until IS NULL OR effective_until > ?)
        """,
        (override_id, now_iso, now_iso),
    ).fetchone()
    if row is None:
        return ""
    current_precedence = int(row["precedence"] or 0)
    current_issued_at = str(row["issued_at"] or "")
    if requested_precedence > current_precedence:
        return ""
    if requested_precedence == current_precedence:
        if not protect_equal or expected_override_issued_at == current_issued_at:
            return ""
        reason = (
            "ignored_equal_precedence_without_cas:"
            f"override_id={override_id}:precedence={current_precedence}:"
            f"current_issued_at={current_issued_at}"
        )
    else:
        reason = (
            "ignored_lower_precedence:"
            f"override_id={override_id}:requested={requested_precedence}:"
            f"current={current_precedence}"
        )
    logger.warning("CONTROL_PRECEDENCE_PRESERVED %s", reason)
    return reason



def _apply_command(name: str, cmd: dict) -> tuple[bool, str]:
    conn = None
    issued_at = datetime.now(timezone.utc).isoformat()
    note = str(cmd.get("note") or cmd.get("reason") or "")
    issued_by = str(cmd.get("issued_by") or "control_plane")
    effective_until = cmd.get("effective_until")
    precedence = int(cmd.get("precedence") or DEFAULT_CONTROL_OVERRIDE_PRECEDENCE)
    try:
        conn = get_world_connection()
    except Exception:
        conn = None
    try:
        if name == "pause_entries":
            precedence_reason = _lower_precedence_override_reason(
                conn,
                override_id="control_plane:global:entries_paused",
                requested_precedence=precedence,
                now_iso=issued_at,
            )
            if precedence_reason:
                return True, precedence_reason
            result = upsert_control_override(
                conn,
                override_id="control_plane:global:entries_paused",
                target_type="global",
                target_key="entries",
                action_type="gate",
                value="true",
                issued_by="control_plane",
                issued_at=issued_at,
                reason=note or "control_plane:pause_entries",
                effective_until=effective_until,
                precedence=precedence,
            )
            status = str(result.get("status") or "unknown")
            return status == "written", "" if status == "written" else status
        if name == "resume":
            precedence_reason = _lower_precedence_override_reason(
                conn,
                override_id="control_plane:global:entries_paused",
                requested_precedence=precedence,
                now_iso=issued_at,
                protect_equal=True,
                expected_override_issued_at=str(cmd.get("expected_override_issued_at") or ""),
            )
            if precedence_reason:
                return True, precedence_reason
            pause_result = expire_control_override(
                conn,
                override_id="control_plane:global:entries_paused",
                expired_at=issued_at,
            )
            edge_result = expire_control_override(
                conn,
                override_id="control_plane:global:edge_threshold_multiplier",
                expired_at=issued_at,
            )
            statuses = {
                str(pause_result.get("status") or "unknown"),
                str(edge_result.get("status") or "unknown"),
            }
            ok = statuses <= {"expired", "noop"}
            return ok, "" if ok else "+".join(sorted(statuses))
        if name == "tighten_risk":
            result = upsert_control_override(
                conn,
                override_id="control_plane:global:edge_threshold_multiplier",
                target_type="global",
                target_key="entries",
                action_type="threshold_multiplier",
                value=str(TIGHTENED_EDGE_THRESHOLD_MULTIPLIER),
                issued_by=issued_by,
                issued_at=issued_at,
                reason=note or "control_plane:tighten_risk",
                effective_until=effective_until,
                precedence=precedence,
            )
            status = str(result.get("status") or "unknown")
            return status == "written", "" if status == "written" else status
        if name == "request_status":
            # Single-writer principle: status_summary.json is written ONLY by the
            # live-trading daemon (write_status / write_cycle_pulse in src/main.py).
            # The control-plane process lacks heartbeat_supervisor + collateral_ledger
            # config, so calling write_status() here produced a stale, misleading
            # snapshot (heartbeat_lost / global_allow_submit=False) that oscillated
            # with the daemon's correct writes.
            #
            # The daemon's write_cycle_pulse cadence keeps status_summary.json well
            # within live_health.py's STATUS_FRESH_BUDGET_SECONDS (300 s), so this
            # branch does not need to write anything.  Return success so callers that
            # issue request_status get an ACK without error.
            #
            # Authority: status dual-writer oscillation fix 2026-06-10.
            return True, "status_summary_written_by_daemon"
        if name == "set_strategy_gate":
            strategy = str(cmd.get("strategy") or "")
            enabled = cmd.get("enabled")
            if not strategy:
                return False, "missing_strategy"
            if not isinstance(enabled, bool):
                return False, "missing_enabled_bool"
            override_id = f"control_plane:strategy:{strategy}:gate"
            precedence_reason = _lower_precedence_override_reason(
                conn,
                override_id=override_id,
                requested_precedence=precedence,
                now_iso=issued_at,
                protect_equal=enabled,
                expected_override_issued_at=str(cmd.get("expected_override_issued_at") or ""),
            )
            if precedence_reason:
                return True, precedence_reason
            decision = GateDecision(
                enabled=enabled,
                reason_code=ReasonCode(cmd.get("reason_code", "unspecified")),
                reason_snapshot=cmd.get("reason_snapshot", {}),
                gated_at=issued_at,
                gated_by=issued_by,
            )
            gates = dict(_control_state.get("strategy_gates", {}))
            gates[strategy] = decision.to_dict()
            _control_state["strategy_gates"] = gates
            result = upsert_control_override(
                conn,
                override_id=override_id,
                target_type="strategy",
                target_key=strategy,
                action_type="gate",
                value="false" if enabled else "true",
                issued_by=issued_by,
                issued_at=issued_at,
                reason=note or f"control_plane:set_strategy_gate:{'enable' if enabled else 'disable'}",
                effective_until=effective_until,
                precedence=precedence,
            )
            status = str(result.get("status") or "unknown")
            return status == "written", "" if status == "written" else status
        if name == "resolve_review_item":
            work_id = _extract_review_work_id(cmd)
            resolver_identity = str(cmd.get("resolver_identity") or issued_by)
            resolution_evidence = str(cmd.get("resolution_evidence") or note)
            if not work_id:
                return False, "missing_work_id"
            if not resolution_evidence:
                return False, "missing_resolution_evidence"
            try:
                authority_revision = int(cmd.get("authority_revision"))
            except (TypeError, ValueError):
                return False, "missing_authority_revision"
            try:
                request = ResolveWorkItemRequest(
                    work_id=work_id,
                    authority_revision=authority_revision,
                    resolver_identity=resolver_identity,
                    resolution_evidence=resolution_evidence,
                    evidence_refs=tuple(cmd.get("evidence_refs") or ()),
                    requested_at=issued_at,
                )
            except ValueError as exc:
                return False, f"invalid_resolve_review_item:{exc}"
            trade_conn = None
            try:
                trade_conn = get_trade_connection()
                _ensure_review_work_items_table(trade_conn)
                resolved = resolve_work_item(
                    trade_conn,
                    work_id=request.work_id,
                    authority_revision=request.authority_revision,
                    resolver_identity=request.resolver_identity,
                    resolution_evidence=request.resolution_evidence,
                    resolved_at=request.requested_at or issued_at,
                )
                trade_conn.commit()
            finally:
                if trade_conn is not None:
                    trade_conn.close()
            return resolved, "" if resolved else "stale_or_missing_work_item"
        if name == "pause_source":
            source_id = str(cmd.get("source") or "")
            if not source_id:
                return False, "missing_source"
            set_pause_source(source_id, paused=True)
            return True, f"source={source_id} paused"
        if name == "resume_source":
            source_id = str(cmd.get("source") or "")
            if not source_id:
                return False, "missing_source"
            set_pause_source(source_id, paused=False)
            return True, f"source={source_id} resumed"
        return True, ""
    finally:
        if conn is not None:
            conn.commit()
            conn.close()



def process_commands(*, refresh_when_empty: bool = True) -> list[str]:
    with _control_payload_transaction():
        data = _load_control_payload()
        commands = data.get("commands", [])
        acks = data.get("acks", [])
        if not commands:
            if refresh_when_empty:
                refresh_control_state()
            return []

        if _control_state.get("control_plane_fault"):
            logger.error("Control plane fault detected. Halting command processing.")
            return []

        processed = []
        retry_commands: list[dict] = []
        rejected_commands: list[str] = []
        for cmd in commands:
            name = cmd.get("command")
            if name not in COMMANDS:
                logger.warning("Unknown control command: %s", name)
                acks.append(_acknowledge_command(str(name or ""), cmd, status="rejected", reason="unknown_command"))
                continue

            logger.info("CONTROL: executing %s", name)
            ok, reason = _apply_command(name, cmd)
            acks.append(_acknowledge_command(name, cmd, status="executed" if ok else "rejected", reason=reason))
            if ok:
                processed.append(name)
            else:
                retry_commands.append(cmd)
                rejected_commands.append(f"{name}:{reason or 'rejected'}")

        _write_control_payload(retry_commands, acks)
    refresh_control_state()
    if rejected_commands:
        raise RuntimeError(
            "control command application rejected; retry retained: "
            + ",".join(rejected_commands)
        )
    return processed


@capability("control_write", lease=False)
@protects("INV-05", "INV-21")
def enqueue_commands(new_commands: list[dict]) -> int:
    """Append commands to the durable control queue without duplicating identical payloads."""
    if not new_commands:
        return 0
    with _control_payload_transaction():
        data = _load_control_payload()
        commands = list(data.get("commands", []))
        acks = list(data.get("acks", []))
        added = 0
        for cmd in new_commands:
            if cmd not in commands:
                commands.append(cmd)
                added += 1
        _write_control_payload(commands, acks)
    return added


def recommended_autosafe_commands_from_status(status: dict) -> list[dict]:
    """Build commands safe to auto-enqueue without extra operator review."""
    control = (status or {}).get("control", {}) or {}
    control_reasons = control.get("recommended_control_reasons", {}) or {}
    commands: list[dict] = []
    for recommendation in control.get("recommended_controls_not_applied", []) or []:
        if recommendation == "tighten_risk":
            command = {"command": "tighten_risk"}
            reasons = control_reasons.get("tighten_risk", [])
            if reasons:
                command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
            commands.append(command)
        if recommendation == "pause_entries":
            command = {"command": "pause_entries"}
            reasons = control_reasons.get("pause_entries", [])
            if reasons:
                command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
            commands.append(command)
    return commands


def review_required_commands_from_status(status: dict) -> list[dict]:
    """Build commands that remain operator-review-required even if recommended.

    K3 fix (bug #5): the previous version auto-generated un-gate commands for
    every strategy in `gated_but_not_recommended`, using the note
    "recommended_by=gate_drift_resolved". This made the resolver treat absence
    from today's recommendation list as a refutation of the original gating
    reason — which is invalid, because `recommended_strategy_gates` is only
    built from edge_compression + execution_decay signals, not from strategy
    health or settlement accuracy. A strategy gated manually for losing 8/8
    settlements would never generate an edge_compression alert (no new trades
    means no new edge signal), and therefore always showed up as "drift"
    → auto un-gate recommendation.

    Until full K3 (GateDecision with reason_code + reason_snapshot + explicit
    reason_refuted() check) lands, we SUPPRESS auto un-gate entirely. Operators
    who want to re-enable a gated strategy must do so via an explicit command,
    not via drift-resolution. This prevents the LLM reporter and daily review
    from suggesting re-enablement based on a broken heuristic.

    recommended_but_not_gated still generates gate-ON commands (those are new
    recommendations from RiskGuard, which IS the correct signal direction).
    """
    control = (status or {}).get("control", {}) or {}
    gate_reasons = control.get("recommended_strategy_gate_reasons", {}) or {}
    commands: list[dict] = []
    for strategy in control.get("recommended_but_not_gated", []) or []:
        command = {
            "command": "set_strategy_gate",
            "strategy": strategy,
            "enabled": False,
        }
        reasons = gate_reasons.get(strategy, [])
        if reasons:
            command["note"] = "recommended_by=" + ",".join(str(reason) for reason in reasons)
        commands.append(command)
    # K3: gated_but_not_recommended → auto un-gate loop removed.
    # Do NOT re-add without implementing reason_refuted() on top of
    # reason_code-bearing GateDecision objects.
    return commands


def recommended_commands_from_status(
    status: dict,
    *,
    include_review_required: bool = False,
) -> list[dict]:
    """Build explicit control-plane commands from surfaced recommendation drift.

    Auto-safe commands (for example `tighten_risk`) are always included.
    Review-required commands (currently per-strategy gate flips) are only
    included when the caller explicitly opts in, keeping automation
    conservative by default and forcing all-callers surfaces to say so.
    """
    commands = recommended_autosafe_commands_from_status(status)
    if include_review_required:
        commands.extend(review_required_commands_from_status(status))
    return commands



def enqueue_command(command: dict) -> None:
    with _control_payload_transaction():
        data = _load_control_payload()
        commands = data.get("commands", [])
        commands.append(command)
        _write_control_payload(commands, data.get("acks", []))
    refresh_control_state()



def write_commands(commands: list[dict], *, acks: list[dict] | None = None) -> None:
    with _control_payload_transaction():
        data = _load_control_payload()
        _write_control_payload(commands, data.get("acks", []) if acks is None else acks)
    refresh_control_state()



def read_control_payload() -> dict:
    return _load_control_payload()



def build_resolve_review_item_command(
    *,
    work_id: str,
    authority_revision: int,
    resolver_identity: str,
    resolution_evidence: str,
    note: str = "",
) -> dict:
    command = {
        "command": "resolve_review_item",
        "work_id": work_id,
        "authority_revision": authority_revision,
        "resolver_identity": resolver_identity,
        "resolution_evidence": resolution_evidence,
    }
    if note:
        command["note"] = note
    return command


# ---------------------------------------------------------------------------
# Ingest-side pause_source helpers (design §4.5a, Phase 3)
# ---------------------------------------------------------------------------

_PAUSE_SOURCE_KEY = "paused_sources"


def set_pause_source(source_id: str, paused: bool) -> None:
    """Write a pause_source / resume_source directive to state/control_plane.json.

    The ingest daemon reads this on each tick via read_ingest_control_state()
    and skips ticks for a paused source until cleared.
    """
    with _control_payload_transaction():
        data = _load_control_payload()
        paused_sources = data.get(_PAUSE_SOURCE_KEY) or {}
        if paused:
            paused_sources[source_id] = True
        else:
            paused_sources.pop(source_id, None)
        _write_control_payload(
            data.get("commands", []),
            data.get("acks", []),
            payload_updates={_PAUSE_SOURCE_KEY: paused_sources},
        )
    logger.info("set_pause_source: source_id=%s paused=%s", source_id, paused)


def read_ingest_control_state() -> dict:
    """Read control_plane.json and return ingest-relevant directives.

    Returns a dict with key 'paused_sources': set[str] — source IDs
    currently paused by an operator directive.
    """
    data = _load_control_payload()
    raw = data.get(_PAUSE_SOURCE_KEY) or {}
    paused: set[str] = {k for k, v in raw.items() if bool(v)}
    return {"paused_sources": paused}
