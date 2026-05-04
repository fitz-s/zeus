"""Control plane: runtime commands from OpenClaw/Venus without process restart.

Blueprint v2 §10: Supported commands read from state/control_plane.json.
Narrow-by-intent: each command does exactly one thing.
"""

import json
import logging
import sys
import traceback as _traceback
from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.config import state_path
from src.control.gate_decision import GateDecision, ReasonCode
from src.state.db import (
    DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    expire_control_override,
    get_world_connection,
    query_control_override_state,
    upsert_control_override,
)

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
# may be enabled when the live-only daemon starts. Universe of buildable
# strategies lives in src/engine/cycle_runner.py::KNOWN_STRATEGIES (4 entries).
# This set is not the runtime live-entry authority; that boundary is
# _LIVE_ALLOWED_STRATEGIES plus is_strategy_enabled(). Expansion REQUIRES an
# explicit packet — accidental drift caught by tests/test_live_safe_strategies.py.
#
# 2026-04-29: Operator authorized boot/catalog expansion to ALL 4
# KNOWN_STRATEGIES post-calibration-rebuild. Runtime live-entry stayed narrower:
# shoulder_sell remains blocked by _LIVE_ALLOWED_STRATEGIES until a promotion
# packet updates execution, sizing, evidence, and tests together.
LIVE_SAFE_STRATEGIES: frozenset[str] = frozenset({
    "opening_inertia",
    "center_buy",
    "settlement_capture",
    "shoulder_sell",
})

COMMANDS = {
    "pause_entries",                # Stop entering, keep monitoring
    "resume",                       # Clear temporary global controls and resume entries
    "tighten_risk",                 # Double edge thresholds temporarily
    "request_status",               # Force status_summary write
    "set_strategy_gate",            # Enable/disable individual strategies
    "acknowledge_quarantine_clear", # Explicit operator intent before ignore/non-resurrection
    "pause_source",                 # Ingest: skip ticks for a named source until cleared
    "resume_source",                # Ingest: clear pause_source for a named source
}


def assert_live_safe_strategies_under_live_mode(enabled: Iterable[str]) -> None:
    """Refuse daemon launch if any enabled strategy is outside LIVE_SAFE_STRATEGIES under live mode.

    Helper called from src/main.py boot path. Runtime is live-only, so this
    guard is unconditional and no longer reads the retired ZEUS_MODE switch.
    Exits via SystemExit so daemon launchers consume the fatal refusal cleanly.
    """
    enabled_set = frozenset(enabled)
    offenders = sorted(enabled_set - LIVE_SAFE_STRATEGIES)
    if offenders:
        sys.exit(
            f"FATAL: live mode refused — non-allowlisted strategies enabled: "
            f"{offenders}. LIVE_SAFE_STRATEGIES={sorted(LIVE_SAFE_STRATEGIES)}. "
            f"Disable each via control_plane set_strategy_gate before relaunching."
        )

_control_state: dict = {}


def _load_control_payload() -> dict:
    try:
        with open(CONTROL_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        logger.error("control_plane.json corrupted (JSONDecodeError)")
        _set_state("control_plane_fault", True)
        raise ValueError("Corrupted control_plane.json encountered, treating as fatal fault") from e
    except OSError:
        return {}



def _write_control_payload(commands: list[dict], acks: list[dict]) -> None:
    with open(CONTROL_PATH, "w") as f:
        json.dump({"commands": commands, "acks": acks[-20:]}, f, indent=2)



def _extract_quarantine_token_id(payload: dict) -> str:
    token_id = payload.get("token_id") or payload.get("tokenId") or ""
    return str(token_id) if token_id else ""



def _set_state(key: str, value) -> None:
    _control_state[key] = value



def is_entries_paused() -> bool:
    return _control_state.get("entries_paused", False)


def get_entries_pause_source() -> str | None:
    return _control_state.get("entries_pause_source")


def get_entries_pause_reason() -> str | None:
    return _control_state.get("entries_pause_reason")



def alert_auto_pause(reason_code: str) -> None:
    """Emit a structured log warning when entries are auto-paused by exception."""
    logger.warning(
        "auto_pause_entries",
        extra={"reason_code": reason_code},
    )


def _auto_pause_tombstone_path():
    return state_path("auto_pause_failclosed.tombstone")


def _clear_auto_pause_tombstone() -> None:
    """Clear the fail-closed tombstone after an explicit operator resume."""
    import os
    try:
        os.remove(_auto_pause_tombstone_path())
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.error("Failed to clear auto-pause tombstone on resume: %s", exc)



AUTO_PAUSE_OVERRIDE_ID = "control_plane:global:entries_paused"


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
            with open(state_path("auto_pause_failclosed.tombstone"), "w") as f:
                f.write(reason_code)
        except OSError:
            pass
        try:
            alert_auto_pause(f"{reason_code}_db_fault")
        except Exception:
            pass



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


_LIVE_ALLOWED_STRATEGIES = {"settlement_capture", "center_buy", "opening_inertia"}


def is_strategy_enabled(strategy: str) -> bool:
    if not strategy:
        return True
    if strategy not in _LIVE_ALLOWED_STRATEGIES:
        return False
    decision = strategy_gates().get(strategy)
    if decision is None:
        return True
    return decision.enabled



def get_acknowledged_quarantine_clear_tokens() -> set[str]:
    data = _load_control_payload()
    acknowledged: set[str] = set()
    for ack in data.get("acks", []):
        if ack.get("command") != "acknowledge_quarantine_clear":
            continue
        if ack.get("status") != "executed":
            continue
        token_id = _extract_quarantine_token_id(ack)
        if token_id:
            acknowledged.add(token_id)
    return acknowledged



def refresh_control_state() -> None:
    data = _load_control_payload()
    acknowledged_tokens: set[str] = set()
    entries_paused = False
    edge_threshold_multiplier = DEFAULT_EDGE_THRESHOLD_MULTIPLIER
    gates: dict[str, bool] = {}
    durable_state = {"status": "skipped_no_connection"}
    conn = None
    try:
        conn = get_world_connection()
        durable_state = query_control_override_state(conn)
    except Exception:
        durable_state = {
            "status": "query_error",
            "entries_paused": True,
            "edge_threshold_multiplier": float(TIGHTENED_EDGE_THRESHOLD_MULTIPLIER)
        }
    finally:
        if conn is not None:
            conn.close()
    if durable_state.get("status") in {"ok", "query_error"}:
        entries_paused = bool(durable_state.get("entries_paused", False))
        edge_threshold_multiplier = float(
            durable_state.get("edge_threshold_multiplier", DEFAULT_EDGE_THRESHOLD_MULTIPLIER)
        )
        gates = dict(durable_state.get("strategy_gates", {}))
        
    try:
        import os
        if os.path.exists(state_path("auto_pause_failclosed.tombstone")):
            entries_paused = True
    except OSError:
        pass

    for ack in data.get("acks", []):
        if ack.get("status") != "executed":
            continue
        command = ack.get("command")
        if command == "acknowledge_quarantine_clear":
            token_id = _extract_quarantine_token_id(ack)
            if token_id:
                acknowledged_tokens.add(token_id)
            continue
    _control_state["entries_paused"] = entries_paused
    _control_state["entries_pause_source"] = durable_state.get("entries_pause_source")
    _control_state["entries_pause_reason"] = durable_state.get("entries_pause_reason")
    _control_state["edge_threshold_multiplier"] = edge_threshold_multiplier
    _control_state["acknowledged_quarantine_clear_tokens"] = acknowledged_tokens
    _control_state["strategy_gates"] = gates
    _control_state["durable_override_status"] = durable_state.get("status", "unknown")



def clear_control_state() -> None:
    _control_state.clear()
    refresh_control_state()



def acknowledged_quarantine_clear_tokens() -> set[str]:
    tokens = _control_state.get("acknowledged_quarantine_clear_tokens")
    if tokens is None:
        refresh_control_state()
        tokens = _control_state.get("acknowledged_quarantine_clear_tokens", set())
    return set(tokens)



def has_acknowledged_quarantine_clear(token_id: str) -> bool:
    return token_id in acknowledged_quarantine_clear_tokens()



def _acknowledge_command(name: str, cmd: dict, *, status: str, reason: str = "") -> dict:
    ack = {
        "command": name,
        "acked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
    }
    token_id = _extract_quarantine_token_id(cmd)
    if token_id:
        ack["token_id"] = token_id
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
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "resume":
            expire_control_override(
                conn,
                override_id="control_plane:global:entries_paused",
                expired_at=issued_at,
            )
            _clear_auto_pause_tombstone()
            expire_control_override(
                conn,
                override_id="control_plane:global:edge_threshold_multiplier",
                expired_at=issued_at,
            )
            return True, ""
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
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "request_status":
            from src.observability.status_summary import write_status
            write_status()
            return True, ""
        if name == "set_strategy_gate":
            strategy = str(cmd.get("strategy") or "")
            enabled = cmd.get("enabled")
            if not strategy:
                return False, "missing_strategy"
            if not isinstance(enabled, bool):
                return False, "missing_enabled_bool"
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
                override_id=f"control_plane:strategy:{strategy}:gate",
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
            return result["status"] in {"written", "skipped_missing_table"}, "" if result["status"] != "skipped_missing_table" else "missing_control_overrides_table"
        if name == "acknowledge_quarantine_clear":
            if not _extract_quarantine_token_id(cmd):
                return False, "missing_token_id"
            return True, ""
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



def process_commands() -> list[str]:
    data = _load_control_payload()
    commands = data.get("commands", [])
    acks = data.get("acks", [])
    if not commands:
        refresh_control_state()
        return []

    if _control_state.get("control_plane_fault"):
        logger.error("Control plane fault detected. Halting command processing.")
        return []

    processed = []
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

    _write_control_payload([], acks)
    refresh_control_state()
    return processed


def enqueue_commands(new_commands: list[dict]) -> int:
    """Append commands to the durable control queue without duplicating identical payloads."""
    if not new_commands:
        return 0
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
    data = _load_control_payload()
    commands = data.get("commands", [])
    commands.append(command)
    _write_control_payload(commands, data.get("acks", []))
    refresh_control_state()



def write_commands(commands: list[dict], *, acks: list[dict] | None = None) -> None:
    data = _load_control_payload()
    _write_control_payload(commands, data.get("acks", []) if acks is None else acks)
    refresh_control_state()



def read_control_payload() -> dict:
    return _load_control_payload()



def build_quarantine_clear_command(*, token_id: str, condition_id: str = "", note: str = "") -> dict:
    command = {"command": "acknowledge_quarantine_clear", "token_id": token_id}
    if condition_id:
        command["condition_id"] = condition_id
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
    import json
    import os

    path = CONTROL_PATH
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        data = {}

    paused_sources = data.get(_PAUSE_SOURCE_KEY) or {}
    if paused:
        paused_sources[source_id] = True
    else:
        paused_sources.pop(source_id, None)

    data[_PAUSE_SOURCE_KEY] = paused_sources
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
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
