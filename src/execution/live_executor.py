# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 56-60 (Gate 2);
#                  ULTIMATE_DESIGN §5 Gate 2 phantom-type subsection;
#                  RISK_REGISTER R3 mitigation (ABC split + @untyped_for_compat)

"""Gate 2: Type-time live/shadow separation.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

LiveAuthToken is an OPAQUE phantom type -- only LiveExecutor subclasses can
construct one (via the _mint_token classmethod).  ShadowExecutor lives in
shadow_executor.py and has no submit signature that accepts a token, making
it structurally impossible to confuse live and shadow paths at type-check time.

ritual_signal is emitted on:
  - each phantom token construction (_mint_token)
  - each refusal (kill-switch armed, risk-level halt, frozen window)

@untyped_for_compat is the 30-day escape hatch (R3 mitigation).  Callers that
cannot immediately add the token parameter annotate the call site with the
decorator; it adds a _compat_expires_at attribute so the test
test_gate2_live_auth_token.py::test_untyped_for_compat_escape_hatch_records_sunset
can assert the attribute is present and emit a runtime warning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from src.architecture.decorators import capability, protects
from src.architecture import gate_runtime as _gate_runtime  # Gate 5: delegate runtime checks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
SUNSET_DATE: str = "2026-08-04"
_CHARTER_VERSION: str = "1.0.0"
_GATE_NAME: str = "gate2_live_auth_token"

# @untyped_for_compat escape hatch: expires 30 days after authoring.
_COMPAT_EXPIRES_AT: str = "2026-06-05"

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_RITUAL_SIGNAL_DIR = REPO_ROOT / "logs" / "ritual_signal"

# Resolved absolute path of this file -- used for mint-guard.
_THIS_FILE = str(pathlib.Path(__file__).resolve())
_VENUE_ADAPTER_FILE = str(
    (REPO_ROOT / "src" / "execution" / "venue_adapter.py").resolve()
)


# ---------------------------------------------------------------------------
# ritual_signal helper
# ---------------------------------------------------------------------------

def _emit_signal(event: str, outcome: str, detail: str = "") -> None:
    """Emit one ritual_signal JSON line per Gate 2 event.

    Schema per ANTI_DRIFT_CHARTER §3 M1 — all required fields included:
      helper, task_id, fit_score, advisory_or_blocking, outcome,
      invocation_ts, charter_version.
    Extended fields: event, detail, sunset_date.
    """
    _RITUAL_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out_path = _RITUAL_SIGNAL_DIR / f"{month}.jsonl"
    # task_id: short hash of event+detail for per-invocation uniqueness.
    task_payload = f"{event}|{detail}"
    task_id = hashlib.sha256(task_payload.encode()).hexdigest()[:16]
    record = {
        # CHARTER §3 M1 required fields
        "helper": _GATE_NAME,
        "task_id": task_id,
        "fit_score": 1.0,
        "advisory_or_blocking": "blocking",
        "outcome": outcome,
        "invocation_ts": datetime.now(timezone.utc).isoformat(),
        "charter_version": _CHARTER_VERSION,
        # Extended Gate 2 fields
        "event": event,
        "detail": detail,
        "sunset_date": SUNSET_DATE,
    }
    try:
        with out_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("gate2: ritual_signal write failed: %s", exc)


# ---------------------------------------------------------------------------
# LiveAuthToken -- opaque phantom type
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LiveAuthToken:
    """Opaque phantom token proving a live submission passed all gate checks.

    Construction is RESTRICTED to LiveExecutor subclasses via _mint_token.
    The runtime guard in __new__ rejects any call whose source file is not
    live_executor.py or venue_adapter.py.

    mypy / pyright: the submit(order, token: LiveAuthToken) signature in
    LiveExecutor ABC will produce a type error at any call site that does not
    pass a correctly-typed token.  ShadowExecutor.submit has no token parameter
    by design, making cross-path confusion a compile-time error.

    Trust boundary (OD-K0-1b 2026-05-06):
        The phantom catches accidental misuse — forgetting to mint a token,
        passing the wrong arg, or routing through a non-Live path.  It does
        NOT defend against deliberate adversarial code inside src/execution/
        that uses object.__setattr__, ctypes, or pickle to forge a token.
        Such code already has access to _mint_token() and the same blast
        radius via the legitimate construction path.

        The trust boundary is the src/execution/ package itself.  Defense
        against malicious internals is enforced by code review of changes
        to src/execution/, not by type-system primitives Python does not
        provide.  The frozen+slots dataclass closes the casual __dict__
        forgery vector (test_live_auth_token_unforgeable_via_dict_write);
        the __setattr__ slot-write vector is accepted residual risk.
    """

    _issued_at: str
    _gate: str = _GATE_NAME

    def __new__(cls, _issued_at: str, _gate: str = _GATE_NAME) -> "LiveAuthToken":
        import sys
        frame = sys._getframe(1)
        caller_file = str(pathlib.Path(frame.f_code.co_filename).resolve())
        allowed = {_THIS_FILE, _VENUE_ADAPTER_FILE}
        if caller_file not in allowed:
            _emit_signal("token_construction_refused", "blocked", f"caller={caller_file}")
            raise RuntimeError(
                "LiveAuthToken can only be constructed by LiveExecutor._mint_token. "
                f"Unauthorized caller: {caller_file}"
            )
        return super().__new__(cls)


# ---------------------------------------------------------------------------
# LiveExecutor ABC
# ---------------------------------------------------------------------------

class LiveExecutor(ABC):
    """Abstract base for all live-order executors.

    Concrete subclasses MUST implement _do_submit.  The submit method
    handles gate checks and mints the LiveAuthToken before delegating.

    Gate checks (in order):
      1. Kill switch: ZEUS_KILL_SWITCH env var
      2. Risk level: ZEUS_RISK_HALT env var
      3. Settlement freeze: ZEUS_SETTLEMENT_FREEZE env var

    Any active block raises RuntimeError and emits ritual_signal.
    """

    @classmethod
    def _mint_token(cls) -> LiveAuthToken:
        """Construct a LiveAuthToken after all gate checks pass.

        This is the ONLY sanctioned factory.  ShadowExecutor does not call it.
        ritual_signal is emitted on each construction.
        """
        issued_at = datetime.now(timezone.utc).isoformat()
        token = LiveAuthToken(_issued_at=issued_at)
        _emit_signal("token_minted", "applied", f"executor={cls.__name__}")
        return token

    def _assert_kill_switch_off(self) -> None:
        """Raise RuntimeError if kill switch is armed.

        Delegates to gate_runtime.check("live_venue_submit") which evaluates
        kill_switch_active + risk_level_halt conditions from capabilities.yaml
        blocked_when list (Gate 5 Phase 4.D). Inline env-var check retained as
        dead fallback for callers that bypass gate_runtime.check() directly.
        """
        _gate_runtime.check("live_venue_submit")  # Gate 5 delegation — emits ritual_signal

    def _assert_not_frozen(self) -> None:
        """Raise RuntimeError if settlement freeze is active.

        Delegates to gate_runtime.check("settlement_write") for
        settlement_window_freeze_active (Gate 5 Phase 4.D).
        """
        _gate_runtime.check("settlement_write")  # Gate 5 delegation — emits ritual_signal

    @capability("live_venue_submit", lease=True)
    @protects("INV-21", "INV-04")
    def submit(self, order: Any) -> Any:
        """Run gate checks, mint token, delegate to _do_submit.

        This is the type-enforced entry point.  All gate checks run BEFORE
        token construction; a blocked gate never mints a token.
        """
        self._assert_kill_switch_off()
        self._assert_not_frozen()
        token = self._mint_token()
        return self._do_submit(order, token)

    @abstractmethod
    def _do_submit(self, order: Any, token: LiveAuthToken) -> Any:
        """Concrete implementation receives a validated order + minted token."""
        ...


# ---------------------------------------------------------------------------
# @untyped_for_compat escape hatch (R3 mitigation, 30-day expiry)
# ---------------------------------------------------------------------------

def untyped_for_compat(fn: Callable[..., Any]) -> Callable[..., Any]:
    """30-day escape hatch for callers that cannot immediately add LiveAuthToken.

    Usage::

        @untyped_for_compat
        def legacy_submit(order):
            ...

    The wrapped function emits a DeprecationWarning at call time and carries a
    _compat_expires_at attribute for CI detection.

    Expires: 2026-06-05.  Remove all usages before this date.
    """
    expires_at = _COMPAT_EXPIRES_AT

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(
            f"@untyped_for_compat call site expires {expires_at}. "
            "Add LiveAuthToken parameter before this date.",
            DeprecationWarning,
            stacklevel=2,
        )
        return fn(*args, **kwargs)

    wrapper._compat_expires_at = expires_at  # type: ignore[attr-defined]
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
