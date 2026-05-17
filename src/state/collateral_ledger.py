# Created: 2026-04-26
# Last reused/audited: 2026-05-17
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
#                  + 2026-05-13 collateral_ledger singleton conn lifecycle remediation
#                  + 2026-05-17 live collateral DB lock remediation
"""R3 Z4 collateral ledger for pUSD, CTF inventory, and reservations.

pUSD is BUY collateral. CTF outcome tokens are SELL inventory. This module
keeps that asymmetry explicit and fail-closed so high pUSD balance can never
substitute for missing CTF tokens on an exit/sell path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Optional

from src.contracts import ExecutionIntent
from src.contracts.fx_classification import (
    FXClassification,
    FXClassificationPending,
    require_fx_classification,
)

AuthorityTier = Literal["CHAIN", "VENUE", "DEGRADED"]

_MICRO = 1_000_000
_CTF_SCALE = 1_000_000
SQLITE_SIGNED_INTEGER_MAX = (2**63) - 1
_TERMINAL_RESERVATION_STATES = frozenset(
    {"CANCELED", "CANCELLED", "FILLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}
)
COLLATERAL_SNAPSHOT_REFRESH_CADENCE_SECONDS = 30.0
COLLATERAL_SNAPSHOT_REFRESH_JITTER_BUDGET_SECONDS = 150.0
COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS = (
    COLLATERAL_SNAPSHOT_REFRESH_CADENCE_SECONDS
    + COLLATERAL_SNAPSHOT_REFRESH_JITTER_BUDGET_SECONDS
)
COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS = 5.0
DEFAULT_COLLATERAL_BUSY_TIMEOUT_MS = 30_000

COLLATERAL_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS collateral_ledger_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pusd_balance_micro INTEGER NOT NULL,
  pusd_allowance_micro INTEGER NOT NULL,
  usdc_e_legacy_balance_micro INTEGER NOT NULL,
  ctf_token_balances_json TEXT NOT NULL,
  ctf_token_allowances_json TEXT NOT NULL,
  reserved_pusd_for_buys_micro INTEGER NOT NULL DEFAULT 0,
  reserved_tokens_for_sells_json TEXT NOT NULL DEFAULT '{}',
  captured_at TEXT NOT NULL,
  authority_tier TEXT NOT NULL CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED')),
  raw_balance_payload_hash TEXT
);

CREATE TABLE IF NOT EXISTS collateral_reservations (
  command_id TEXT PRIMARY KEY,
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount INTEGER NOT NULL CHECK (amount >= 0),
  created_at TEXT NOT NULL,
  released_at TEXT,
  release_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL)
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL)
  )
);
"""


class CollateralInsufficient(RuntimeError):
    """Raised when live submit preflight lacks spendable collateral/inventory."""


def _collateral_busy_timeout_ms() -> int:
    raw = os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", str(DEFAULT_COLLATERAL_BUSY_TIMEOUT_MS))
    try:
        ms = int(float(raw))
    except (OverflowError, TypeError, ValueError):
        return DEFAULT_COLLATERAL_BUSY_TIMEOUT_MS
    if ms < 0:
        raise ValueError(
            f"ZEUS_DB_BUSY_TIMEOUT_MS must be >= 0; got {raw!r} ({ms} ms). "
            "Fix the environment variable before starting the daemon."
        )
    return ms


def _pragma_busy_timeout_ms(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _apply_busy_timeout(conn: sqlite3.Connection, busy_ms: int | None) -> None:
    if busy_ms is None:
        return
    # SQLite does not support bound parameters for this PRAGMA. The value is
    # normalized to int before interpolation, so no untrusted identifier/text can
    # enter the statement.
    conn.execute("PRAGMA busy_timeout = %d" % int(busy_ms))


def _connect_owned_collateral_db(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    busy_ms = _collateral_busy_timeout_ms()
    conn = sqlite3.connect(
        str(path),
        timeout=busy_ms / 1000.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_busy_timeout(conn, busy_ms)
    return conn


@dataclass(frozen=True)
class CollateralSnapshot:
    pusd_balance_micro: int
    pusd_allowance_micro: int
    usdc_e_legacy_balance_micro: int
    ctf_token_balances: dict[str, int]
    ctf_token_allowances: dict[str, int]
    reserved_pusd_for_buys_micro: int
    reserved_tokens_for_sells: dict[str, int]
    captured_at: datetime
    authority_tier: AuthorityTier
    raw_balance_payload_hash: Optional[str] = None

    @property
    def available_pusd_micro(self) -> int:
        return max(0, self.pusd_balance_micro - self.reserved_pusd_for_buys_micro)

    @property
    def available_pusd_allowance_micro(self) -> int:
        return max(0, self.pusd_allowance_micro - self.reserved_pusd_for_buys_micro)

    def available_tokens(self, token_id: str) -> int:
        return max(
            0,
            int(self.ctf_token_balances.get(token_id, 0))
            - int(self.reserved_tokens_for_sells.get(token_id, 0)),
        )

    def available_token_allowance(self, token_id: str) -> int:
        return max(
            0,
            int(self.ctf_token_allowances.get(token_id, 0))
            - int(self.reserved_tokens_for_sells.get(token_id, 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pusd_balance_micro": self.pusd_balance_micro,
            "pusd_allowance_micro": self.pusd_allowance_micro,
            "usdc_e_legacy_balance_micro": self.usdc_e_legacy_balance_micro,
            "ctf_token_balances": dict(self.ctf_token_balances),
            "ctf_token_allowances": dict(self.ctf_token_allowances),
            "reserved_pusd_for_buys_micro": self.reserved_pusd_for_buys_micro,
            "reserved_tokens_for_sells": dict(self.reserved_tokens_for_sells),
            "captured_at": self.captured_at.isoformat(),
            "authority_tier": self.authority_tier,
            "raw_balance_payload_hash": self.raw_balance_payload_hash,
        }


class CollateralLedger:
    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        """Initialize a ledger backed by a sqlite3 connection.

        Lifecycle modes:
        - ``conn=None`` (default): in-memory reservations only; tests/fakes.
        - ``conn=<existing>``: caller owns conn lifetime. The ledger is only
          safe to use while the caller's conn is open. Suitable for short
          unit-of-work helpers (e.g. legacy compat wrappers, tests).
        - ``db_path=<path>``: ledger opens AND owns a persistent connection
          for its lifetime. Use for process-wide singletons published via
          ``configure_global_ledger`` — survives transient caller-conn
          lifecycles. Mutually exclusive with ``conn``.

        Authority basis: 2026-05-13 collateral_ledger singleton conn lifecycle
        remediation. Previously the singleton wrapped a transient caller conn
        (PolymarketClient.get_balance compat wrapper), which closed the conn
        immediately after publishing the ledger globally and caused every
        downstream preflight to raise sqlite3.ProgrammingError.
        """
        if conn is not None and db_path is not None:
            raise ValueError(
                "CollateralLedger accepts at most one of conn= or db_path="
            )
        self._snapshot: CollateralSnapshot | None = None
        self._memory_reservations: dict[str, dict[str, Any]] = {}
        self._owns_conn = False
        if db_path is not None:
            # Persistent ledger-owned connection. check_same_thread=False so
            # the singleton can be read from riskguard / executor threads
            # without re-opening on every call.
            self._conn = _connect_owned_collateral_db(db_path)
            self._owns_conn = True
            init_collateral_schema(self._conn)
        else:
            self._conn = conn
            if self._conn is not None:
                init_collateral_schema(self._conn)

    def close(self) -> None:
        """Close the underlying connection iff the ledger owns it.

        Safe to call multiple times. Externally-supplied connections are
        never closed by the ledger.
        """
        if self._owns_conn and self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._owns_conn = False
            self._conn = None

    def refresh(self, adapter: Any) -> CollateralSnapshot:
        """Read pUSD/CTF collateral truth from an adapter-like object.

        Adapter failures produce a DEGRADED snapshot instead of raising so
        preflight callers can fail closed with structured context.
        """

        captured_at = datetime.now(timezone.utc)
        try:
            raw = _read_adapter_payload(adapter)
            authority: AuthorityTier = str(raw.get("authority_tier") or "CHAIN").upper()  # type: ignore[assignment]
            if authority not in {"CHAIN", "VENUE", "DEGRADED"}:
                authority = "DEGRADED"
        except Exception as exc:
            raw = {"error": str(exc), "authority_tier": "DEGRADED"}
            authority = "DEGRADED"

        reserved_pusd = self._reserved_pusd()
        reserved_tokens = self._reserved_tokens()
        payload_hash = _hash_payload(raw)
        snapshot = CollateralSnapshot(
            pusd_balance_micro=_sqlite_micro(raw.get("pusd_balance_micro", raw.get("pusd_balance", 0))),
            pusd_allowance_micro=_sqlite_micro(raw.get("pusd_allowance_micro", raw.get("pusd_allowance", 0))),
            usdc_e_legacy_balance_micro=_sqlite_micro(
                raw.get("usdc_e_legacy_balance_micro", raw.get("usdc_e_legacy_balance", 0))
            ),
            ctf_token_balances=_ctf_units_dict_from_payload(raw, "ctf_token_balances"),
            ctf_token_allowances=_ctf_units_dict_from_payload(raw, "ctf_token_allowances"),
            reserved_pusd_for_buys_micro=reserved_pusd,
            reserved_tokens_for_sells=reserved_tokens,
            captured_at=captured_at,
            authority_tier=authority,
            raw_balance_payload_hash=payload_hash,
        )
        self._snapshot = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def set_snapshot(self, snapshot: CollateralSnapshot) -> None:
        self._snapshot = snapshot
        self._persist_snapshot(snapshot)

    def snapshot(self) -> CollateralSnapshot:
        loaded = self._load_latest_snapshot()
        if loaded is not None and (
            self._snapshot is None
            or _is_newer_snapshot(loaded, self._snapshot)
        ):
            self._snapshot = loaded
        if self._snapshot is None:
            return CollateralSnapshot(
                pusd_balance_micro=0,
                pusd_allowance_micro=0,
                usdc_e_legacy_balance_micro=0,
                ctf_token_balances={},
                ctf_token_allowances={},
                reserved_pusd_for_buys_micro=self._reserved_pusd(),
                reserved_tokens_for_sells=self._reserved_tokens(),
                captured_at=datetime.now(timezone.utc),
                authority_tier="DEGRADED",
                raw_balance_payload_hash=None,
            )
        return replace(
            self._snapshot,
            reserved_pusd_for_buys_micro=self._reserved_pusd(),
            reserved_tokens_for_sells=self._reserved_tokens(),
        )

    def buy_preflight(self, intent: ExecutionIntent, *, spend_micro: int | None = None) -> bool:
        snapshot = self.snapshot()
        required = spend_micro if spend_micro is not None else _intent_worst_case_spend_micro(intent)
        if snapshot.authority_tier == "DEGRADED":
            raise CollateralInsufficient("collateral_snapshot_degraded")
        _assert_snapshot_fresh(snapshot)
        if snapshot.available_pusd_micro < required:
            raise CollateralInsufficient(
                f"pusd_insufficient: required_micro={required} "
                f"available_micro={snapshot.available_pusd_micro}"
            )
        available_allowance = snapshot.available_pusd_allowance_micro
        if available_allowance < required:
            raise CollateralInsufficient(
                f"pusd_allowance_insufficient: required_micro={required} "
                f"available_allowance_micro={available_allowance} "
                f"allowance_micro={snapshot.pusd_allowance_micro}"
            )
        return True

    def sell_preflight(
        self,
        intent: ExecutionIntent | None = None,
        *,
        token_id: str | None = None,
        size: int | float | None = None,
    ) -> bool:
        snapshot = self.snapshot()
        selected_token = token_id or (intent.token_id if intent is not None else "")
        required = _token_required_units(size if size is not None else getattr(intent, "target_size_usd", 0))
        if not selected_token:
            raise CollateralInsufficient("ctf_token_id_required")
        if snapshot.authority_tier == "DEGRADED":
            raise CollateralInsufficient("collateral_snapshot_degraded")
        _assert_snapshot_fresh(snapshot)
        available = snapshot.available_tokens(selected_token)
        if available < required:
            raise CollateralInsufficient(
                f"ctf_tokens_insufficient: token_id={selected_token} "
                f"required={required} available={available}"
            )
        allowance = int(snapshot.ctf_token_allowances.get(selected_token, 0))
        available_allowance = snapshot.available_token_allowance(selected_token)
        if available_allowance < required:
            raise CollateralInsufficient(
                f"ctf_allowance_insufficient: token_id={selected_token} "
                f"required={required} available_allowance={available_allowance} "
                f"allowance={allowance}"
            )
        return True

    def reserve_pusd_for_buy(self, command_id: str, micro: int) -> None:
        amount = _positive_int(micro, "micro")
        self.buy_preflight(_dummy_intent(), spend_micro=amount)
        self._insert_reservation(command_id, "PUSD_BUY", None, amount)

    def release_pusd_reservation(self, command_id: str) -> None:
        self._release_reservation(command_id, token_id=None, reservation_type="PUSD_BUY", reason="released")

    def reserve_tokens_for_sell(self, command_id: str, token_id: str, size: int | float) -> None:
        amount = _token_required_units(size)
        self.sell_preflight(token_id=token_id, size=size)
        self._insert_reservation(command_id, "CTF_SELL", token_id, amount)

    def release_token_reservation(self, command_id: str, token_id: str) -> None:
        self._release_reservation(command_id, token_id=token_id, reservation_type="CTF_SELL", reason="released")

    def release_reservation_on_command_terminal(self, command_id: str, state: str) -> bool:
        if str(state).upper() not in _TERMINAL_RESERVATION_STATES:
            return False
        reservation = self._reservation(command_id)
        if reservation is None:
            return False
        self._release_reservation(
            command_id,
            token_id=reservation.get("token_id"),
            reservation_type=reservation["reservation_type"],
            reason=str(state).upper(),
        )
        return True

    def _insert_reservation(
        self,
        command_id: str,
        reservation_type: str,
        token_id: str | None,
        amount: int,
    ) -> None:
        if not command_id:
            raise ValueError("command_id is required")
        now = datetime.now(timezone.utc).isoformat()
        if self._conn is None:
            existing = self._memory_reservations.get(command_id)
            if existing and existing.get("released_at") is None:
                raise ValueError(f"reservation already active for command_id={command_id}")
            self._memory_reservations[command_id] = {
                "reservation_type": reservation_type,
                "token_id": token_id,
                "amount": amount,
                "created_at": now,
                "released_at": None,
                "release_reason": None,
            }
            return
        self._conn.execute(
            """
            INSERT INTO collateral_reservations (
              command_id, reservation_type, token_id, amount, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (command_id, reservation_type, token_id, amount, now),
        )

    def _release_reservation(
        self,
        command_id: str,
        *,
        token_id: str | None,
        reservation_type: str,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self._conn is None:
            row = self._memory_reservations.get(command_id)
            if row and row["reservation_type"] == reservation_type and row.get("token_id") == token_id:
                row["released_at"] = now
                row["release_reason"] = reason
            return
        self._conn.execute(
            """
            UPDATE collateral_reservations
               SET released_at = ?, release_reason = ?
             WHERE command_id = ?
               AND reservation_type = ?
               AND (token_id IS ? OR token_id = ?)
               AND released_at IS NULL
            """,
            (now, reason, command_id, reservation_type, token_id, token_id),
        )

    def _reservation(self, command_id: str) -> dict[str, Any] | None:
        if self._conn is None:
            row = self._memory_reservations.get(command_id)
            if row and row.get("released_at") is None:
                return dict(row)
            return None
        row = self._conn.execute(
            """
            SELECT reservation_type, token_id, amount
              FROM collateral_reservations
             WHERE command_id = ? AND released_at IS NULL
            """,
            (command_id,),
        ).fetchone()
        return dict(row) if row else None

    def _reserved_pusd(self) -> int:
        if self._conn is None:
            return sum(
                int(row["amount"])
                for row in self._memory_reservations.values()
                if row["reservation_type"] == "PUSD_BUY" and row.get("released_at") is None
            )
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
              FROM collateral_reservations
             WHERE reservation_type = 'PUSD_BUY' AND released_at IS NULL
            """
        ).fetchone()
        return int(row[0] or 0)

    def _reserved_tokens(self) -> dict[str, int]:
        if self._conn is None:
            out: dict[str, int] = {}
            for row in self._memory_reservations.values():
                if row["reservation_type"] == "CTF_SELL" and row.get("released_at") is None:
                    token_id = str(row["token_id"])
                    out[token_id] = out.get(token_id, 0) + int(row["amount"])
            return out
        rows = self._conn.execute(
            """
            SELECT token_id, COALESCE(SUM(amount), 0) AS amount
              FROM collateral_reservations
             WHERE reservation_type = 'CTF_SELL' AND released_at IS NULL
             GROUP BY token_id
            """
        ).fetchall()
        return {str(row["token_id"]): int(row["amount"] or 0) for row in rows}

    def _load_latest_snapshot(self) -> CollateralSnapshot | None:
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                """
                SELECT *
                  FROM collateral_ledger_snapshots
                 ORDER BY id DESC
                 LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise
        if row is None:
            return None
        raw = dict(row)
        try:
            captured_at = datetime.fromisoformat(str(raw["captured_at"]).replace("Z", "+00:00"))
        except Exception:
            captured_at = datetime.fromtimestamp(0, timezone.utc)
        return CollateralSnapshot(
            pusd_balance_micro=int(raw["pusd_balance_micro"] or 0),
            pusd_allowance_micro=int(raw["pusd_allowance_micro"] or 0),
            usdc_e_legacy_balance_micro=int(raw["usdc_e_legacy_balance_micro"] or 0),
            ctf_token_balances=_int_dict(json.loads(raw["ctf_token_balances_json"] or "{}")),
            ctf_token_allowances=_int_dict(json.loads(raw["ctf_token_allowances_json"] or "{}")),
            reserved_pusd_for_buys_micro=self._reserved_pusd(),
            reserved_tokens_for_sells=self._reserved_tokens(),
            captured_at=captured_at,
            authority_tier=str(raw["authority_tier"] or "DEGRADED"),  # type: ignore[arg-type]
            raw_balance_payload_hash=raw.get("raw_balance_payload_hash"),
        )


    def _persist_snapshot(self, snapshot: CollateralSnapshot) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO collateral_ledger_snapshots (
              pusd_balance_micro,
              pusd_allowance_micro,
              usdc_e_legacy_balance_micro,
              ctf_token_balances_json,
              ctf_token_allowances_json,
              reserved_pusd_for_buys_micro,
              reserved_tokens_for_sells_json,
              captured_at,
              authority_tier,
              raw_balance_payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _sqlite_micro(snapshot.pusd_balance_micro),
                _sqlite_micro(snapshot.pusd_allowance_micro),
                _sqlite_micro(snapshot.usdc_e_legacy_balance_micro),
                json.dumps(snapshot.ctf_token_balances, sort_keys=True),
                json.dumps(snapshot.ctf_token_allowances, sort_keys=True),
                snapshot.reserved_pusd_for_buys_micro,
                json.dumps(snapshot.reserved_tokens_for_sells, sort_keys=True),
                snapshot.captured_at.isoformat(),
                snapshot.authority_tier,
                snapshot.raw_balance_payload_hash,
            ),
        )
        if self._owns_conn:
            self._conn.commit()


_GLOBAL_LEDGER: CollateralLedger | None = None


def init_collateral_schema(conn: sqlite3.Connection) -> None:
    busy_ms = _pragma_busy_timeout_ms(conn)
    conn.executescript(COLLATERAL_LEDGER_SCHEMA)
    # sqlite3.executescript() can clear the connection busy handler. Preserve the
    # caller's lock-wait contract so collateral initialization does not turn a
    # live writer into an immediate "database is locked" failure surface.
    _apply_busy_timeout(conn, busy_ms)


def configure_global_ledger(ledger: CollateralLedger | None) -> None:
    global _GLOBAL_LEDGER
    _GLOBAL_LEDGER = ledger


def get_global_ledger() -> CollateralLedger | None:
    return _GLOBAL_LEDGER


def _snapshot_time(snapshot: CollateralSnapshot) -> datetime:
    captured_at = snapshot.captured_at
    if captured_at.tzinfo is None:
        return captured_at.replace(tzinfo=timezone.utc)
    return captured_at.astimezone(timezone.utc)


def _is_newer_snapshot(candidate: CollateralSnapshot, current: CollateralSnapshot) -> bool:
    return _snapshot_time(candidate) > _snapshot_time(current)


def release_reservation_for_command_state(
    conn: sqlite3.Connection,
    command_id: str,
    state: str,
) -> bool:
    """Release reservations atomically with a terminal venue command state.

    Called by src.state.venue_command_repo inside its append_event savepoint so
    command terminalization and collateral release commit or roll back together.
    This intentionally avoids schema initialization/DDL because DDL can disturb
    an active SQLite savepoint.
    """

    normalized = str(state).upper()
    if normalized not in _TERMINAL_RESERVATION_STATES:
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor = conn.execute(
            """
            UPDATE collateral_reservations
               SET released_at = ?, release_reason = ?
             WHERE command_id = ? AND released_at IS NULL
            """,
            (now, normalized, command_id),
        )
    except sqlite3.OperationalError as exc:
        if "no such table: collateral_reservations" in str(exc):
            return False
        raise
    return cursor.rowcount > 0


def assert_buy_preflight(intent: ExecutionIntent, *, spend_micro: int | None = None) -> None:
    ledger = get_global_ledger()
    if ledger is None:
        raise CollateralInsufficient("collateral_ledger_unconfigured")
    ledger.buy_preflight(intent, spend_micro=spend_micro)


def assert_sell_preflight(token_id: str, size: int | float) -> None:
    ledger = get_global_ledger()
    if ledger is None:
        raise CollateralInsufficient("collateral_ledger_unconfigured")
    ledger.sell_preflight(token_id=token_id, size=size)


def require_pusd_redemption_allowed(classification: FXClassification | None = None) -> FXClassification:
    return require_fx_classification(classification)


def _read_adapter_payload(adapter: Any) -> dict[str, Any]:
    for attr in ("collateral_payload", "get_collateral_payload"):
        fn = getattr(adapter, attr, None)
        if callable(fn):
            return dict(fn() or {})
    client_fn = getattr(adapter, "_sdk_client", None)
    client = client_fn() if callable(client_fn) else adapter
    payload: dict[str, Any] = {
        "pusd_balance_micro": 0,
        "pusd_allowance_micro": 0,
        "usdc_e_legacy_balance_micro": 0,
        "ctf_token_balances": {},
        "ctf_token_allowances": {},
    }
    balance_allowance = getattr(client, "get_balance_allowance", None)
    if callable(balance_allowance):
        # Do not import venue SDK here. SDK-specific parameter shapes
        # belong inside src.venue.polymarket_v2_adapter; this generic fallback
        # is only for tests or simple adapter fakes.
        raw_balance = balance_allowance(SimpleNamespace(asset_type="COLLATERAL"))
        raw_balance = dict(raw_balance or {})
        payload["pusd_balance_micro"] = _int_micro(raw_balance.get("balance", 0))
        payload["pusd_allowance_micro"] = _int_micro(raw_balance.get("allowance", 0))
    legacy = getattr(adapter, "get_legacy_usdc_e_balance", None)
    if callable(legacy):
        payload["usdc_e_legacy_balance_micro"] = _int_micro(legacy())
    positions_fn = getattr(adapter, "get_positions", None)
    if callable(positions_fn):
        balances: dict[str, int] = {}
        allowances: dict[str, int] = {}
        for item in positions_fn() or []:
            raw = getattr(item, "raw", item)
            raw = dict(raw or {})
            token_id = raw.get("asset") or raw.get("token_id") or raw.get("tokenId")
            if not token_id:
                continue
            token_key = str(token_id)
            balance = _token_balance_units(raw.get("size", raw.get("balance", 0)))
            balances[token_key] = balances.get(token_key, 0) + balance
            allowance_raw = raw.get("allowance", raw.get("token_allowance", raw.get("approved_amount")))
            if allowance_raw is not None:
                allowance = _token_balance_units(allowance_raw)
            elif raw.get("approved") is True or raw.get("isApprovedForAll") is True:
                allowance = balance
            else:
                allowance = 0
            allowances[token_key] = allowances.get(token_key, 0) + allowance
        payload["ctf_token_balances_units"] = balances
        payload["ctf_token_allowances_units"] = allowances
    return payload


def _assert_snapshot_fresh(snapshot: CollateralSnapshot) -> None:
    captured_at = snapshot.captured_at
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - captured_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < -COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS:
        raise CollateralInsufficient(
            "collateral_snapshot_future: "
            f"age_seconds={age_seconds:.1f} "
            f"clock_skew_seconds={COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS:.1f}"
        )
    if age_seconds < 0:
        return
    if age_seconds > COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS:
        raise CollateralInsufficient(
            "collateral_snapshot_stale: "
            f"age_seconds={age_seconds:.1f} "
            f"max_age_seconds={COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS:.1f}"
        )


def _intent_worst_case_spend_micro(intent: ExecutionIntent) -> int:
    return int(math.ceil(max(0.0, float(intent.target_size_usd)) * _MICRO))


def _int_micro(value: Any) -> int:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    try:
        if isinstance(value, float) and value < 10_000:
            return int(math.ceil(value * _MICRO))
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sqlite_micro(value: Any) -> int:
    # ERC20 allowance is uint256 on-chain, while SQLite INTEGER is signed int64.
    # The ledger only needs spend-cover proof, so values above the DB domain are
    # represented as the maximum storable non-negative allowance.
    return min(SQLITE_SIGNED_INTEGER_MAX, max(0, _int_micro(value)))


def _token_required_units(value: Any) -> int:
    return _ctf_units_from_shares(value, ROUND_CEILING)


def _token_balance_units(value: Any) -> int:
    return _ctf_units_from_shares(value, ROUND_FLOOR)


def _ctf_units_from_shares(value: Any, rounding) -> int:
    try:
        decimal_value = Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return 0
    units = (decimal_value * _CTF_SCALE).to_integral_value(rounding=rounding)
    return max(0, int(units))


def _ctf_units_dict_from_payload(raw: dict[str, Any], field: str) -> dict[str, int]:
    for suffix in ("_units", "_micro", "_wei"):
        key = f"{field}{suffix}"
        if key in raw:
            return _int_dict(raw.get(key) or {})
    return {str(key): _token_balance_units(val) for key, val in dict(raw.get(field) or {}).items()}


def _positive_int(value: Any, name: str) -> int:
    amount = int(value)
    if amount <= 0:
        raise ValueError(f"{name} must be positive")
    return amount


def _int_dict(value: dict[Any, Any]) -> dict[str, int]:
    return {str(key): int(val or 0) for key, val in dict(value).items()}


def _hash_payload(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _dummy_intent() -> ExecutionIntent:
    from src.contracts import Direction
    from src.contracts.slippage_bps import SlippageBps

    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=0.0,
        limit_price=0.01,
        toxicity_budget=0.0,
        max_slippage=SlippageBps(value_bps=0.0, direction="zero"),
        is_sandbox=False,
        market_id="collateral-reservation",
        token_id="collateral-reservation",
        timeout_seconds=0,
    )
