"""Best-effort cross-process wake hint for the durable event reactor."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import multiprocessing
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Collection, Iterator

REACTOR_WAKE_FILENAME = "edli-reactor-wake.json"
REACTOR_WAKE_QUEUE_SUFFIX = ".d"
REACTOR_WAKE_SOCKET_SUFFIX = ".sock"
REACTOR_URGENT_WAKE_SUFFIX = ".urgent"
HELD_SELL_REAUCTION_RECEIPT_SUFFIX = ".held-sell-reauction-receipts"
HELD_SELL_REAUCTION_V2 = 2
HELD_SELL_REAUCTION_V3 = 3
HELD_SELL_REAUCTION_V4 = 4
POSITION_NO_LONGER_EXPOSED = "POSITION_NO_LONGER_EXPOSED"
SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN = (
    "SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN"
)
NO_EXECUTABLE_BOOK = "NO_EXECUTABLE_BOOK"
SELL_OBLIGATION_ENDED_BY_CANONICAL_CHAIN_ZERO = (
    "SELL_OBLIGATION_ENDED_BY_CANONICAL_CHAIN_ZERO"
)
SELL_OBLIGATION_ENDED_BY_SETTLEMENT_ONLY = (
    "SELL_OBLIGATION_ENDED_BY_SETTLEMENT_ONLY"
)
SELL_OBLIGATION_ENDED_BY_ADMIN_CLOSE_WITH_CHAIN_ZERO = (
    "SELL_OBLIGATION_ENDED_BY_ADMIN_CLOSE_WITH_CHAIN_ZERO"
)
SELL_OBLIGATION_ENDED_BY_VOID_WITH_CHAIN_ZERO = (
    "SELL_OBLIGATION_ENDED_BY_VOID_WITH_CHAIN_ZERO"
)
_HELD_SELL_SETTLED_CHAIN_STATES = frozenset(
    {
        "synced",
        "chain_present",
        "chain_confirmed_zero",
        "chain_absent_confirmed_position_unattributed",
        "external_operator_closed",
        "closed_exited",
        "closed_redeemed",
        "closed_worthless",
    }
)
_HELD_SELL_CHAIN_ZERO_CLOSED_STATES = frozenset(
    {
        "chain_confirmed_zero",
        "chain_absent_confirmed_position_unattributed",
        "external_operator_closed",
        "closed_exited",
        "closed_redeemed",
        "closed_worthless",
    }
)
_HELD_SELL_BOOK_STATES = frozenset(
    {"UNKNOWN", "NO_EXECUTABLE_BOOK", "STALE", "EXECUTABLE"}
)
GLOBAL_AUCTION_COMPLETION_WAKE_REASON = (
    "held_sell_global_auction_completion_requested"
)
COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON = "collateral_authority_refreshed"
GLOBAL_AUCTION_COMPLETION_COALESCE_LIMIT = 16
URGENT_WAKE_REASONS = frozenset(
    {
        "day0_extreme_event_committed",
        "forecast_posterior_advanced",
        "market_price_advanced",
        "position_fill_projected",
    }
)
_WAKE_QUEUE_CACHE_LOCK = threading.Lock()
_WAKE_QUEUE_CACHE: dict[Path, dict[Path, ReactorWake | None]] = {}
_WAKE_QUEUE_REVISIONS: dict[Path, tuple[int, ...]] = {}
_HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK = threading.Lock()
HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT_SECONDS = 0.25
_HELD_SELL_REAUCTION_RECOVERY_CHILD_LOCK = threading.Lock()
_HELD_SELL_REAUCTION_RECOVERY_CHILD = None


def _held_sell_reauction_recovery_read_worker(
    send_conn,
    scope_identity: str,
    path_text: str,
) -> None:
    """Read one V4 lineage/receipt snapshot in a killable child."""

    try:
        path = Path(path_text) if path_text else None
        request = latest_v4_held_sell_reauction_request(
            scope_identity,
            path=path,
        )
        current = (
            None
            if request is None
            else (
                request.request_id,
                request.material_identity,
                request.generation,
                request.attempt_identity,
            )
        )
        completed = bool(
            request is not None
            and held_sell_reauction_requests_completed((request,), path=path)
        )
        send_conn.send(
            json.dumps(
                {
                    "status": "ok",
                    "current": current,
                    "completed": completed,
                },
                separators=(",", ":"),
            )
        )
    except BaseException as exc:  # noqa: BLE001 - process boundary reports failure.
        try:
            send_conn.send(
                json.dumps(
                    {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    separators=(",", ":"),
                )
            )
        except BaseException:
            pass
    finally:
        send_conn.close()


@dataclass(frozen=True)
class HeldSellReauctionRequest:
    """One held statistical-SELL obligation that requires a global reauction."""

    request_id: str
    material_identity: str
    generation: str
    position_id: str
    family: tuple[str, str, str]
    probability_content_identity: str
    held_token_id: str
    held_best_bid: float | None
    bid_observed_at: str
    schema_version: int = 1
    scope_identity: str = ""
    book_state: str = "EXECUTABLE"
    probability_observed_at: str = ""
    attempt_identity: str = ""
    completion_deadline_at: str = ""


@dataclass(frozen=True)
class HeldSellReauctionReceipt:
    """Durable terminal result for one held reauction obligation."""

    request_id: str
    material_identity: str
    generation: str
    status: str
    reason: str
    lifecycle_phase: str = ""
    chain_state: str = ""
    chain_shares: float | None = None
    settled_at: str = ""
    selection_epoch_identity: str = ""
    sell_book_witness_identity: str = ""
    schema_version: int = 1
    scope_identity: str = ""
    book_state: str = "EXECUTABLE"
    capital_objective_proof: str = ""
    answered_probability_content_identity: str = ""
    attempt_identity: str = ""
    position_id: str = ""
    held_token_id: str = ""
    debt_event_id: str = ""
    debt_sequence_no: int = 0
    monitor_event_id: str = ""
    monitor_sequence_no: int = 0
    monitor_occurred_at: str = ""
    monitor_payload_sha256: str = ""
    monitor_probability: float | None = None
    monitor_probability_is_fresh: bool | None = None
    monitor_selected_method: str = ""
    monitor_should_exit: bool | None = None
    monitor_trigger: str = ""


@dataclass(frozen=True)
class HeldSellReauctionLineage:
    """Fixed-size durable latest-attempt fence for one V4 debt scope."""

    scope_identity: str
    request_id: str
    material_identity: str
    generation: str
    latest_attempt_identity: str
    latest_wake_id: str
    latest_request: dict[str, object]
    schema_version: int = HELD_SELL_REAUCTION_V4
    lineage_version: int = 2


@dataclass(frozen=True)
class ReactorWake:
    wake_id: str
    published_at: str
    source: str
    reason: str
    event_ids: tuple[str, ...] = ()
    forecast_families: tuple[tuple[str, str, str], ...] = ()
    held_sell_reauction_requests: tuple[HeldSellReauctionRequest, ...] = ()


def _wake_path(path: Path | None) -> Path:
    if path is not None:
        target = Path(path)
    else:
        from src.config import state_path

        target = state_path(REACTOR_WAKE_FILENAME)
    if "ZEUS_TEST_STATE_ROOT" in os.environ:
        # SCOPE: this wake target and its derived queue/socket/receipt siblings.
        # DRAIN: pytest's temporary root is discarded after the owning session.
        # RESET: marker absence takes the pre-hotfix production path unchanged.
        from src.config import validate_test_state_path

        validate_test_state_path(target)
    return target


def _wake_queue_dir(path: Path | None) -> Path:
    target = _wake_path(path)
    return target.with_name(f"{target.name}{REACTOR_WAKE_QUEUE_SUFFIX}")


def _wake_socket_path(path: Path | None) -> Path:
    target = _wake_path(path)
    socket_path = target.with_name(f"{target.name}{REACTOR_WAKE_SOCKET_SUFFIX}")
    if len(os.fsencode(socket_path)) <= 100:
        return socket_path
    digest = hashlib.sha256(os.fsencode(target)).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"zeus-reactor-wake-{digest}.sock"


def _urgent_wake_path(path: Path | None) -> Path:
    target = _wake_path(path)
    return target.with_name(f"{target.name}{REACTOR_URGENT_WAKE_SUFFIX}")


def _held_sell_reauction_receipt_dir(path: Path | None) -> Path:
    target = _wake_path(path)
    return target.with_name(f"{target.name}{HELD_SELL_REAUCTION_RECEIPT_SUFFIX}")


def _notify_reactor_wake(path: Path | None) -> None:
    """Best-effort latency signal; the durable queue remains the authority."""

    notifier: socket.socket | None = None
    try:
        notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        notifier.setblocking(False)
        notifier.sendto(b"\x01", str(_wake_socket_path(path)))
    except OSError:
        pass
    finally:
        if notifier is not None:
            notifier.close()


def _reactor_wake_socket_live(path: Path) -> bool:
    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        probe.connect(str(path))
        probe.send(b"\x00")
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            probe.close()


@contextmanager
def reactor_wake_listener_socket(
    *, path: Path | None = None
) -> Iterator[socket.socket | None]:
    """Own the local notifier socket, or yield None when another listener does."""

    target = _wake_socket_path(path)
    listener: socket.socket | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _reactor_wake_socket_live(target):
                yield None
                return
            target.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        listener.bind(str(target))
    except OSError:
        if listener is not None:
            listener.close()
        yield None
        return

    assert listener is not None
    bound_inode: int | None = None
    try:
        bound_inode = target.stat().st_ino
        yield listener
    finally:
        listener.close()
        try:
            if bound_inode is not None and target.stat().st_ino == bound_inode:
                target.unlink(missing_ok=True)
        except OSError:
            pass


def _clean_forecast_families(
    values: object,
) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in values:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            continue
        family = (
            str(raw[0] or "").strip(),
            str(raw[1] or "").strip(),
            str(raw[2] or "").strip(),
        )
        if not all(family) or family in seen:
            continue
        seen.add(family)
        families.append(family)
        if len(families) == 100:
            break
    return tuple(families)


def _held_sell_reauction_material(
    *,
    position_id: str,
    family: tuple[str, str, str],
    probability_content_identity: str,
    held_token_id: str,
    held_best_bid: float | None,
    bid_observed_at: str,
    schema_version: int = 1,
    scope_identity: str = "",
    book_state: str = "EXECUTABLE",
    probability_observed_at: str = "",
) -> dict[str, object]:
    """Validate and normalize the stable held-position witness."""

    clean_family = _clean_forecast_families((family,))
    clean_position_id = str(position_id or "").strip()
    clean_q_identity = str(probability_content_identity or "").strip()
    clean_token_id = str(held_token_id or "").strip()
    clean_observed_at = str(bid_observed_at or "").strip()
    clean_probability_observed_at = str(probability_observed_at or "").strip()
    try:
        clean_schema_version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("HELD_SELL_REAUCTION_SCHEMA_VERSION_INVALID") from exc
    clean_book_state = str(book_state or "").strip().upper()
    clean_scope_identity = str(scope_identity or "").strip()
    clean_bid: float | None
    if held_best_bid in (None, ""):
        clean_bid = None
    else:
        try:
            clean_bid = float(held_best_bid)
        except (TypeError, ValueError) as exc:
            raise ValueError("HELD_SELL_REAUCTION_BID_INVALID") from exc
        if not math.isfinite(clean_bid):
            raise ValueError("HELD_SELL_REAUCTION_BID_INVALID")
    if clean_schema_version == 1:
        if (
            len(clean_family) != 1
            or not all(
                (
                    clean_position_id,
                    clean_q_identity,
                    clean_token_id,
                    clean_observed_at,
                )
            )
            or clean_bid is None
            or not 0.05 <= clean_bid <= 1.0
        ):
            raise ValueError("HELD_SELL_REAUCTION_REQUEST_INVALID")
    elif clean_schema_version in {
        HELD_SELL_REAUCTION_V2,
        HELD_SELL_REAUCTION_V3,
        HELD_SELL_REAUCTION_V4,
    }:
        if (
            len(clean_family) != 1
            or not all((clean_position_id, clean_token_id, clean_scope_identity))
            or clean_book_state not in _HELD_SELL_BOOK_STATES
            or (clean_bid is not None and not 0.0 <= clean_bid <= 1.0)
            or (
                clean_book_state == "EXECUTABLE"
                and (
                    not all((clean_q_identity, clean_observed_at))
                    or clean_bid is None
                    or not 0.05 <= clean_bid <= 1.0
                )
            )
        ):
            raise ValueError("HELD_SELL_REAUCTION_V2_REQUEST_INVALID")
    else:
        raise ValueError("HELD_SELL_REAUCTION_SCHEMA_VERSION_INVALID")
    material = {
        "position_id": clean_position_id,
        "family": clean_family[0],
        "probability_content_identity": clean_q_identity,
        "held_token_id": clean_token_id,
        "held_best_bid": clean_bid,
        "bid_observed_at": clean_observed_at,
    }
    if clean_schema_version in {
        HELD_SELL_REAUCTION_V2,
        HELD_SELL_REAUCTION_V3,
        HELD_SELL_REAUCTION_V4,
    }:
        material.update(
            {
                "schema_version": clean_schema_version,
                "scope_identity": clean_scope_identity,
                "probability_observed_at": clean_probability_observed_at,
                "book_state": clean_book_state,
            }
        )
    return material


def held_sell_reauction_scope_identity(
    *,
    position_id: str,
    family: tuple[str, str, str],
    probability_content_identity: str,
    held_token_id: str,
    schema_version: int = HELD_SELL_REAUCTION_V4,
) -> str:
    """Return the durable held SELL debt scope for one position/token."""

    scope = {
        "position_id": str(position_id or "").strip(),
        "family": tuple(str(value or "").strip() for value in family),
        "held_token_id": str(held_token_id or "").strip(),
    }
    if int(schema_version) in {HELD_SELL_REAUCTION_V2, HELD_SELL_REAUCTION_V3}:
        # Preserve V2/V3's q-bound identity for durable wake compatibility.
        scope["probability_content_identity"] = str(
            probability_content_identity or ""
        ).strip()
    elif int(schema_version) == HELD_SELL_REAUCTION_V4:
        # SCOPE: one exact held SELL obligation. DRAIN: global-auction receipt
        # or canonical terminal proof. RESET: changed position/family/token/intent.
        scope.update({"intent": "SELL", "scope_version": 4})
    else:
        raise ValueError("HELD_SELL_REAUCTION_SCHEMA_VERSION_INVALID")
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def held_sell_reauction_material_identity(
    *,
    position_id: str,
    family: tuple[str, str, str],
    probability_content_identity: str,
    held_token_id: str,
    held_best_bid: float | None,
    bid_observed_at: str,
    schema_version: int = 1,
    scope_identity: str = "",
    book_state: str = "EXECUTABLE",
    probability_observed_at: str = "",
) -> str:
    """Return the V1 witness identity or the versioned obligation scope."""

    material = _held_sell_reauction_material(
        position_id=position_id,
        family=family,
        probability_content_identity=probability_content_identity,
        held_token_id=held_token_id,
        held_best_bid=held_best_bid,
        bid_observed_at=bid_observed_at,
        schema_version=schema_version,
        scope_identity=scope_identity,
        book_state=book_state,
        probability_observed_at=probability_observed_at,
    )
    if int(schema_version) in {
        HELD_SELL_REAUCTION_V2,
        HELD_SELL_REAUCTION_V3,
        HELD_SELL_REAUCTION_V4,
    }:
        # Versioned book/q clocks describe one attempt, not the obligation. A new
        # executable book must answer the original no-book wake generation.
        return str(material["scope_identity"])
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _held_sell_reauction_attempt_identity(material: dict[str, object]) -> str:
    """Bind one V3 attempt to its trigger q/book context."""

    return hashlib.sha256(
        json.dumps(
            {
                "scope_identity": material["scope_identity"],
                "probability_content_identity": material[
                    "probability_content_identity"
                ],
                "probability_observed_at": material["probability_observed_at"],
                "held_best_bid": material["held_best_bid"],
                "bid_observed_at": material["bid_observed_at"],
                "book_state": material["book_state"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _held_sell_reauction_request_id(
    material_identity: str,
    generation: str,
    attempt_identity: str = "",
    *,
    schema_version: int = 1,
) -> str:
    identity = {
        "generation": generation,
        "material_identity": material_identity,
    }
    if attempt_identity and int(schema_version) != HELD_SELL_REAUCTION_V4:
        identity["attempt_identity"] = attempt_identity
    return hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def make_held_sell_reauction_request(
    *,
    position_id: str,
    family: tuple[str, str, str],
    probability_content_identity: str,
    held_token_id: str,
    held_best_bid: float | None,
    bid_observed_at: str,
    generation: str | None = None,
    schema_version: int = 1,
    scope_identity: str = "",
    book_state: str = "EXECUTABLE",
    probability_observed_at: str = "",
    completion_deadline_at: str = "",
) -> HeldSellReauctionRequest:
    """Bind one monitor witness to one non-reusable request generation."""

    if int(schema_version) in {
        HELD_SELL_REAUCTION_V2,
        HELD_SELL_REAUCTION_V3,
        HELD_SELL_REAUCTION_V4,
    } and (not scope_identity or int(schema_version) == HELD_SELL_REAUCTION_V4):
        scope_identity = held_sell_reauction_scope_identity(
            position_id=position_id,
            family=family,
            probability_content_identity=probability_content_identity,
            held_token_id=held_token_id,
            schema_version=schema_version,
        )
    material = _held_sell_reauction_material(
        position_id=position_id,
        family=family,
        probability_content_identity=probability_content_identity,
        held_token_id=held_token_id,
        held_best_bid=held_best_bid,
        bid_observed_at=bid_observed_at,
        schema_version=schema_version,
        scope_identity=scope_identity,
        book_state=book_state,
        probability_observed_at=probability_observed_at,
    )
    material_identity = held_sell_reauction_material_identity(
        position_id=position_id,
        family=family,
        probability_content_identity=probability_content_identity,
        held_token_id=held_token_id,
        held_best_bid=held_best_bid,
        bid_observed_at=bid_observed_at,
        schema_version=schema_version,
        scope_identity=scope_identity,
        book_state=book_state,
        probability_observed_at=probability_observed_at,
    )
    clean_generation = str(generation or uuid.uuid4().hex).strip()
    if not clean_generation or len(clean_generation) > 128:
        raise ValueError("HELD_SELL_REAUCTION_GENERATION_INVALID")
    clean_completion_deadline = str(completion_deadline_at or "").strip()
    if clean_completion_deadline:
        try:
            parsed_deadline = datetime.fromisoformat(
                clean_completion_deadline.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "HELD_SELL_REAUCTION_COMPLETION_DEADLINE_INVALID"
            ) from exc
        if parsed_deadline.tzinfo is None:
            raise ValueError("HELD_SELL_REAUCTION_COMPLETION_DEADLINE_INVALID")
        clean_completion_deadline = parsed_deadline.astimezone(
            timezone.utc
        ).isoformat()
    attempt_identity = (
        _held_sell_reauction_attempt_identity(material)
        if int(material.get("schema_version", 1))
        in {HELD_SELL_REAUCTION_V3, HELD_SELL_REAUCTION_V4}
        else ""
    )
    request_id = _held_sell_reauction_request_id(
        material_identity,
        clean_generation,
        attempt_identity,
        schema_version=int(material.get("schema_version", 1)),
    )
    return HeldSellReauctionRequest(
        request_id=request_id,
        material_identity=material_identity,
        generation=clean_generation,
        attempt_identity=attempt_identity,
        completion_deadline_at=clean_completion_deadline,
        **material,
    )


def _clean_held_sell_reauction_requests(
    values: object,
) -> tuple[HeldSellReauctionRequest, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    requests: list[HeldSellReauctionRequest] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, (HeldSellReauctionRequest, dict)):
            continue

        def get(key: str, default: object = None) -> object:
            if isinstance(raw, dict):
                return raw.get(key, default)
            return getattr(raw, key, default)

        claimed_request_id = str(get("request_id") or "").strip()
        claimed_material_identity = str(get("material_identity") or "").strip()
        generation = str(get("generation") or "").strip()
        try:
            material_identity = held_sell_reauction_material_identity(
                position_id=str(get("position_id") or ""),
                family=tuple(get("family") or ()),
                probability_content_identity=str(
                    get("probability_content_identity") or ""
                ),
                held_token_id=str(get("held_token_id") or ""),
                held_best_bid=get("held_best_bid"),
                bid_observed_at=str(get("bid_observed_at") or ""),
                schema_version=get("schema_version", 1),
                scope_identity=str(get("scope_identity") or ""),
                book_state=str(get("book_state") or "EXECUTABLE"),
                probability_observed_at=str(
                    get("probability_observed_at") or ""
                ),
            )
            if claimed_material_identity and (
                claimed_material_identity != material_identity
            ):
                continue
            legacy_generation = not generation
            if legacy_generation:
                if claimed_request_id != material_identity:
                    continue
                generation = f"legacy-{claimed_request_id}"
            request = make_held_sell_reauction_request(
                position_id=str(get("position_id") or ""),
                family=tuple(get("family") or ()),
                probability_content_identity=str(
                    get("probability_content_identity") or ""
                ),
                held_token_id=str(get("held_token_id") or ""),
                held_best_bid=get("held_best_bid"),
                bid_observed_at=str(get("bid_observed_at") or ""),
                generation=generation,
                schema_version=get("schema_version", 1),
                scope_identity=str(get("scope_identity") or ""),
                book_state=str(get("book_state") or "EXECUTABLE"),
                probability_observed_at=str(
                    get("probability_observed_at") or ""
                ),
                completion_deadline_at=str(
                    get("completion_deadline_at") or ""
                ),
            )
        except (TypeError, ValueError):
            continue
        if not legacy_generation and claimed_request_id != request.request_id:
            continue
        if request.request_id in seen:
            continue
        seen.add(request.request_id)
        requests.append(request)
        if len(requests) == 100:
            break
    return tuple(requests)


def publish_reactor_wake(
    *,
    source: str,
    reason: str,
    path: Path | None = None,
    wake_id: str | None = None,
    published_at: datetime | None = None,
    event_ids: tuple[str, ...] = (),
    forecast_families: tuple[tuple[str, str, str], ...] = (),
    held_sell_reauction_requests: tuple[HeldSellReauctionRequest, ...] = (),
    lineage_lock_timeout_seconds: float | None = None,
) -> ReactorWake:
    """Atomically publish a non-authoritative wake hint after durable truth commits."""

    clean_source = str(source or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_source or not clean_reason:
        raise ValueError("reactor wake source and reason are required")
    clean_event_ids = tuple(
        dict.fromkeys(
            event_id
            for raw_event_id in event_ids
            if (event_id := str(raw_event_id or "").strip())
        )
    )[:100]
    clean_forecast_families = _clean_forecast_families(forecast_families)
    clean_held_sell_reauction_requests = _clean_held_sell_reauction_requests(
        held_sell_reauction_requests
    )
    wake = ReactorWake(
        wake_id=str(wake_id or uuid.uuid4().hex),
        published_at=(published_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(),
        source=clean_source,
        reason=clean_reason,
        event_ids=clean_event_ids,
        forecast_families=clean_forecast_families,
        held_sell_reauction_requests=clean_held_sell_reauction_requests,
    )
    target = _wake_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    queue_dir = _wake_queue_dir(path)
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_target = _wake_queue_target(wake, path=path)
    v4_requests = tuple(
        request
        for request in wake.held_sell_reauction_requests
        if request.schema_version == HELD_SELL_REAUCTION_V4
    )
    if v4_requests:
        lineage_lock = (
            _held_sell_reauction_lineage_locks(
                tuple(request.scope_identity for request in v4_requests),
                path=path,
            )
            if lineage_lock_timeout_seconds is None
            else _held_sell_reauction_lineage_locks(
                tuple(request.scope_identity for request in v4_requests),
                path=path,
                timeout_seconds=lineage_lock_timeout_seconds,
            )
        )
        with lineage_lock:
            for request in v4_requests:
                _read_v4_held_sell_reauction_lineage(
                    request.scope_identity,
                    path=path,
                )
            for request in v4_requests:
                _write_v4_held_sell_reauction_lineage(
                    request,
                    wake_id=wake.wake_id,
                    path=path,
                )
            # Publish the fence before replacing the deterministic queue slot.
            # A partial write can leave a missing wake for retry, but can never
            # let an old acknowledgement delete a newer attempt.
            _atomic_write_wake(queue_target, wake)
            with _WAKE_QUEUE_CACHE_LOCK:
                _WAKE_QUEUE_CACHE.get(queue_dir, {}).pop(queue_target, None)
                _WAKE_QUEUE_REVISIONS.pop(queue_dir, None)
            # The fallback participates in the same ordering fence as the V4
            # lineage and deterministic queue slot. An older publisher can
            # never overwrite it after a newer attempt has become durable.
            _atomic_write_wake(target, wake)
    else:
        _atomic_write_wake(queue_target, wake)
        _atomic_write_wake(target, wake)
    if wake.reason in URGENT_WAKE_REASONS:
        _atomic_write_wake(_urgent_wake_path(path), wake)
    _notify_reactor_wake(path)
    return wake


def _atomic_write_wake(target: Path, wake: ReactorWake) -> None:
    temp = target.with_name(f".{target.name}.{os.getpid()}.{wake.wake_id}.tmp")
    try:
        temp.write_text(
            json.dumps(
                {
                    **wake.__dict__,
                    "held_sell_reauction_requests": [
                        request.__dict__
                        for request in wake.held_sell_reauction_requests
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _wake_queue_target(wake: ReactorWake, *, path: Path | None) -> Path:
    if (
        len(wake.held_sell_reauction_requests) == 1
        and wake.held_sell_reauction_requests[0].schema_version
        == HELD_SELL_REAUCTION_V4
    ):
        scope_identity = wake.held_sell_reauction_requests[0].scope_identity
        return _v4_wake_queue_target(scope_identity, path=path)
    published_us = int(
        datetime.fromisoformat(wake.published_at.replace("Z", "+00:00")).timestamp()
        * 1_000_000
    )
    return _wake_queue_dir(path) / f"{published_us:020d}-{wake.wake_id}.json"


def _v4_wake_queue_target(scope_identity: str, *, path: Path | None) -> Path:
    return _wake_queue_dir(path) / f"held-sell-v4-{scope_identity}.json"


def _read_reactor_wake_path(
    path: Path,
    *,
    fail_on_error: bool = False,
) -> ReactorWake | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        wake = ReactorWake(
            wake_id=str(payload["wake_id"]).strip(),
            published_at=str(payload["published_at"]).strip(),
            source=str(payload["source"]).strip(),
            reason=str(payload["reason"]).strip(),
            event_ids=tuple(
                str(event_id or "").strip()
                for event_id in payload.get("event_ids", ())
                if str(event_id or "").strip()
            )[:100],
            forecast_families=_clean_forecast_families(
                payload.get("forecast_families", ())
            ),
            held_sell_reauction_requests=_clean_held_sell_reauction_requests(
                payload.get("held_sell_reauction_requests", ())
            ),
        )
    except FileNotFoundError:
        return None
    except OSError:
        if fail_on_error:
            raise
        return None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if fail_on_error:
            raise ValueError("REACTOR_WAKE_INVALID") from exc
        return None
    if not all((wake.wake_id, wake.published_at, wake.source, wake.reason)):
        if fail_on_error:
            raise ValueError("REACTOR_WAKE_INVALID")
        return None
    return wake


def _wake_queue_revision(
    queue_dir: Path,
    *,
    path: Path | None,
    fail_on_error: bool = False,
) -> tuple[int, ...] | None:
    try:
        queue_stat = queue_dir.stat()
    except FileNotFoundError:
        return None
    except OSError:
        if fail_on_error:
            raise
        return None
    if not stat.S_ISDIR(queue_stat.st_mode):
        if fail_on_error:
            raise NotADirectoryError(
                f"reactor wake queue path is not a directory: {queue_dir}"
            )
        return None
    try:
        legacy = _wake_path(path).stat()
        legacy_revision = (legacy.st_ino, legacy.st_mtime_ns, legacy.st_size)
    except FileNotFoundError:
        legacy_revision = (0, 0, 0)
    except OSError:
        if fail_on_error:
            raise
        legacy_revision = (0, 0, 0)
    return (
        queue_stat.st_ino,
        queue_stat.st_mtime_ns,
        queue_stat.st_ctime_ns,
        *legacy_revision,
    )


def _queued_wakes(
    path: Path | None,
    *,
    fail_on_error: bool = False,
) -> list[tuple[Path, ReactorWake]]:
    """Read immutable queue files once, then refresh only on durable revision change."""

    queue_dir = _wake_queue_dir(path)
    revision = _wake_queue_revision(
        queue_dir,
        path=path,
        fail_on_error=fail_on_error,
    )
    if revision is None:
        return []
    cached_snapshot: dict[Path, ReactorWake | None] | None = None
    with _WAKE_QUEUE_CACHE_LOCK:
        if _WAKE_QUEUE_REVISIONS.get(queue_dir) == revision:
            cached_snapshot = _WAKE_QUEUE_CACHE.get(queue_dir, {})
    if cached_snapshot is not None:
        if fail_on_error and any(wake is None for wake in cached_snapshot.values()):
            raise ValueError("REACTOR_WAKE_INVALID")
        return [
            (queue_file, wake)
            for queue_file, wake in cached_snapshot.items()
            if wake is not None
        ]
    try:
        queue_files = sorted(queue_dir.glob("*.json"))
    except OSError:
        if fail_on_error:
            raise
        return []
    with _WAKE_QUEUE_CACHE_LOCK:
        cached = dict(_WAKE_QUEUE_CACHE.get(queue_dir, {}))
    fresh: dict[Path, ReactorWake | None] = {}
    for queue_file in queue_files:
        fresh[queue_file] = (
            cached[queue_file]
            if queue_file in cached
            else _read_reactor_wake_path(queue_file, fail_on_error=fail_on_error)
        )
        if fail_on_error and fresh[queue_file] is None:
            raise ValueError("REACTOR_WAKE_INVALID")
    current_revision = _wake_queue_revision(
        queue_dir,
        path=path,
        fail_on_error=fail_on_error,
    )
    with _WAKE_QUEUE_CACHE_LOCK:
        _WAKE_QUEUE_CACHE[queue_dir] = fresh
        if current_revision == revision:
            _WAKE_QUEUE_REVISIONS[queue_dir] = revision
        else:
            _WAKE_QUEUE_REVISIONS.pop(queue_dir, None)
    return [(queue_file, wake) for queue_file, wake in fresh.items() if wake is not None]


def _exact_held_sell_deadline_expired(
    request: HeldSellReauctionRequest,
    *,
    now: datetime,
) -> bool:
    """Return whether one exact SELL request exhausted its actuation clock."""

    if int(request.schema_version) != HELD_SELL_REAUCTION_V4:
        return False
    deadline_text = str(request.completion_deadline_at or "").strip()
    if not deadline_text:
        return False
    try:
        deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        return False
    return deadline.astimezone(timezone.utc) <= now.astimezone(timezone.utc)


def read_reactor_wake(
    *,
    path: Path | None = None,
    exclude_wake_ids: Collection[str] = (),
    prefer_exact_held_sell: bool = False,
    prefer_forecast_carrier_progress: bool = False,
    prefer_material_progress: bool = False,
    fail_on_error: bool = False,
) -> ReactorWake | None:
    """Read the queued fact with the shortest alpha clock first.

    Day0 observations can reverse value in milliseconds and normally always preempt.
    A durable exact held-SELL completion debt is next: it survives process
    restart and ordinary fill, price, probability, or generic monitor-fairness
    streams cannot starve capital already at risk. A confirmed fill changes the
    actual portfolio endowment. Fill, price, and probability are otherwise
    joint material inputs; their oldest unconsumed input gets one turn, so no
    continuous stream can starve another. A generic auction-completion marker
    follows those material inputs: without an exact held-SELL request, it must
    not delay fresh executable evidence.
    Forecast hints carry incremental family scopes; selecting the newest hint
    does not lose older scopes because same-reason wakes are coalesced and
    acknowledgement remains exact.
    """

    excluded = {str(wake_id) for wake_id in exclude_wake_ids}
    legacy = (
        _read_reactor_wake_path(_wake_path(path), fail_on_error=True)
        if fail_on_error
        else None
    )
    queued = [
        item
        for item in _queued_wakes(path, fail_on_error=fail_on_error)
        if item[1].wake_id not in excluded
    ]
    now = datetime.now(timezone.utc)
    for _queue_file, wake in queued:
        if (
            wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
            and wake.held_sell_reauction_requests
            and any(
                _exact_held_sell_deadline_expired(request, now=now)
                for request in wake.held_sell_reauction_requests
            )
        ):
            # SCOPE: only an exact V4 SELL attempt whose immutable actuation
            # deadline has expired. DRAIN: select its durable wake until the
            # global auction writes a matching terminal receipt. RESET: a
            # receipt removes the wake, while a newer attempt carries its own
            # deadline. The auction still rebinds current q/book; this priority
            # never authorizes replay of the request's historical quote.
            return wake
    if prefer_exact_held_sell:
        for _queue_file, wake in queued:
            if (
                wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
                and wake.held_sell_reauction_requests
            ):
                return wake
    if prefer_forecast_carrier_progress:
        # SCOPE: this is only the paused-entry, proven-no-exposure carrier
        # materialization turn selected by src.main. DRAIN: a selected forecast
        # wake is acknowledged only after its existing durable carrier/no-submit
        # path completes; an empty or failed build remains queued. RESET: clearing
        # the pause or finding canonical exposure removes this preference and restores
        # ordinary Day0 priority. Exact held-SELL and fill evidence stay
        # ahead of the carrier because they can change capital already at risk.
        for _queue_file, wake in queued:
            if (
                wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
                and wake.held_sell_reauction_requests
            ):
                return wake
        for _queue_file, wake in queued:
            if wake.reason == "position_fill_projected":
                return wake
        for _queue_file, wake in reversed(queued):
            if wake.reason == "forecast_posterior_advanced":
                return wake
    if prefer_material_progress:
        # SCOPE: one post-terminal-Day0-cleanup turn, after src.main proves the
        # currently selected Day0 hint has no event or capital obligation.
        # DRAIN: exact held SELL and fill remain first; otherwise the ordinary
        # oldest material input gets one turn. RESET: the caller consumes the
        # preference once, so unfinished Day0 immediately regains priority.
        for _queue_file, wake in queued:
            if (
                wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
                and wake.held_sell_reauction_requests
            ):
                return wake
        for _queue_file, wake in queued:
            if wake.reason == "position_fill_projected":
                return wake
        for _queue_file, wake in queued:
            if wake.reason in {
                "market_price_advanced",
                "forecast_posterior_advanced",
            }:
                return wake
    for _queue_file, wake in reversed(queued):
        if wake.reason == "day0_extreme_event_committed":
            return wake
    for _queue_file, wake in queued:
        if (
            wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
            and wake.held_sell_reauction_requests
        ):
            return wake
    for _queue_file, wake in queued:
        if wake.reason == "position_fill_projected":
            return wake
        if wake.reason == "market_price_advanced":
            return wake
        if wake.reason == "forecast_posterior_advanced":
            return next(
                candidate
                for _candidate_file, candidate in reversed(queued)
                if candidate.reason == "forecast_posterior_advanced"
            )
    for _queue_file, wake in queued:
        if wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON:
            return wake
    for _queue_file, wake in queued:
        return wake
    if not fail_on_error:
        legacy = _read_reactor_wake_path(_wake_path(path))
    if legacy is not None and legacy.wake_id not in excluded:
        return legacy
    return None


def exact_held_sell_completion_wake_ids(
    *, path: Path | None = None, fail_on_error: bool = False
) -> frozenset[str]:
    """Snapshot queued exact held-SELL completion wake identities.

    The snapshot is only a one-turn selection hint. It never acknowledges,
    deletes, or changes the durable debt; a wake published after this read is
    intentionally not excluded and retains exact-debt priority.
    """

    wake_ids = {
        wake.wake_id
        for _queue_file, wake in _queued_wakes(path, fail_on_error=fail_on_error)
        if (
            wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
            and wake.held_sell_reauction_requests
        )
    }
    legacy = _read_reactor_wake_path(
        _wake_path(path),
        fail_on_error=fail_on_error,
    )
    if (
        legacy is not None
        and legacy.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        and legacy.held_sell_reauction_requests
    ):
        wake_ids.add(legacy.wake_id)
    return frozenset(wake_ids)


def reactor_wakes_since(
    published_at: str | None,
    *,
    path: Path | None = None,
    exclude_wake_ids: Collection[str] = (),
) -> tuple[ReactorWake, ...]:
    """Return queued wakes at or after one producer wake's publication time."""

    excluded = {str(wake_id) for wake_id in exclude_wake_ids}
    cutoff = None
    try:
        if published_at:
            cutoff = datetime.fromisoformat(
                str(published_at).strip().replace("Z", "+00:00")
            )
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            cutoff = cutoff.astimezone(timezone.utc)
    except (TypeError, ValueError):
        cutoff = None

    wakes: list[ReactorWake] = []
    for _queue_file, wake in _queued_wakes(path):
        if wake.wake_id in excluded:
            continue
        if cutoff is not None:
            try:
                wake_time = datetime.fromisoformat(
                    wake.published_at.replace("Z", "+00:00")
                )
                if wake_time.tzinfo is None:
                    wake_time = wake_time.replace(tzinfo=timezone.utc)
                wake_time = wake_time.astimezone(timezone.utc)
            except (TypeError, ValueError):
                wake_time = None
            if wake_time is not None and wake_time < cutoff:
                continue
        wakes.append(wake)
    return tuple(wakes)


def reactor_wakes_for_reason(
    reason: str,
    *,
    path: Path | None = None,
    exclude_wake_ids: Collection[str] = (),
    max_wakes: int = 100,
    fail_on_error: bool = False,
) -> tuple[ReactorWake, ...]:
    """Return a newest-first bounded exact-reason drain, including fallback.

    This is for control-plane hints whose service is orthogonal to alpha-wake
    priority.  It never consumes or reorders a wake of another reason.
    """

    clean_reason = str(reason or "").strip()
    limit = max(1, int(max_wakes))
    excluded = {str(wake_id) for wake_id in exclude_wake_ids}
    exact = [
        wake
        for _queue_file, wake in _queued_wakes(
            path,
            fail_on_error=fail_on_error,
        )
        if wake.wake_id not in excluded and wake.reason == clean_reason
    ]
    seen = {wake.wake_id for wake in exact}
    legacy = _read_reactor_wake_path(
        _wake_path(path),
        fail_on_error=fail_on_error,
    )
    if (
        legacy is not None
        and legacy.wake_id not in excluded
        and legacy.wake_id not in seen
        and legacy.reason == clean_reason
    ):
        exact.append(legacy)
    exact.sort(key=lambda wake: wake.published_at, reverse=True)
    return tuple(exact[:limit])


def coalescible_reactor_wakes(
    selected: ReactorWake,
    *,
    path: Path | None = None,
    max_wakes: int = 100,
    max_event_ids: int = 100,
    max_forecast_families: int = 100,
) -> tuple[ReactorWake, ...]:
    """Collect same-reason wake hints that one targeted reactor drain can serve.

    A Day0 commit is one preemptible alpha unit. Combining it with older
    observation wakes can put the newest hard fact behind more event IDs than
    one reactor cycle can process. The durable event queue remains the recovery
    authority, so serve the newest Day0 wake alone and leave older hints queued.
    """

    if selected.reason == "day0_extreme_event_committed":
        return (selected,)

    queued = [wake for _queue_file, wake in _queued_wakes(path)]
    selected_index = next(
        (
            index
            for index, wake in enumerate(queued)
            if wake.wake_id == selected.wake_id
        ),
        None,
    )
    if selected_index is None or max_wakes <= 1:
        return (selected,)

    candidates: list[ReactorWake] = []
    reserved_completion_wake: ReactorWake | None = None
    if selected.reason in {
        "forecast_posterior_advanced",
        "market_price_advanced",
        "position_fill_projected",
    }:
        candidates = [
            wake
            for wake in queued
            if wake.wake_id != selected.wake_id and wake.reason == selected.reason
        ]
    elif selected.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON:
        candidates = [
            wake
            for wake in queued
            if wake.wake_id != selected.wake_id
            and wake.reason == selected.reason
        ]
        # Completion wakes are durable debt, so preserving the queue is more
        # important than attempting an unbounded monitor fan-out.  Serve one
        # request per position before a second request for any position; the
        # unselected wakes remain immutable for the next reactor turn.
        by_position: list[ReactorWake] = []
        deferred: list[ReactorWake] = []
        generic: list[ReactorWake] = []
        positions = {
            request.position_id
            for request in selected.held_sell_reauction_requests
        }
        for wake in candidates:
            wake_positions = {
                request.position_id
                for request in wake.held_sell_reauction_requests
            }
            if wake_positions and wake_positions.isdisjoint(positions):
                by_position.append(wake)
                positions.update(wake_positions)
            elif not wake_positions:
                generic.append(wake)
            else:
                deferred.append(wake)
        # Exact capital debt remains first because ``selected`` is already fixed.
        # Reserve the next bounded turn for the oldest generic completion marker
        # so a continuous stream of distinct held positions cannot starve its
        # SCOPE/DRAIN/RESET obligation; the rest keep position-fair exact order.
        reserved_completion_wake = generic[0] if generic else None
        candidates = [*by_position, *deferred, *generic[1:]]
        max_wakes = min(
            max(1, int(max_wakes)),
            GLOBAL_AUCTION_COMPLETION_COALESCE_LIMIT,
        )
    else:
        for wake in queued[selected_index + 1 :]:
            if wake.reason == "forecast_posterior_advanced":
                continue
            if wake.reason != selected.reason:
                break
            candidates.append(wake)

    wakes = [selected]
    wake_ids = {selected.wake_id}
    event_ids = set(selected.event_ids)
    families = set(selected.forecast_families)
    if (
        reserved_completion_wake is not None
        and max_wakes > 1
        and len(reserved_completion_wake.event_ids) <= max(1, int(max_event_ids))
        and len(reserved_completion_wake.forecast_families)
        <= max(1, int(max_forecast_families))
    ):
        # This is a second independently bounded completion turn, not extra
        # scope charged to the selected exact-capital turn. Keeping the two
        # budgets separate makes progress possible when each legal wake already
        # occupies its own full scope; the invocation remains bounded by two
        # per-axis budgets and GLOBAL_AUCTION_COMPLETION_COALESCE_LIMIT wakes.
        wakes.append(reserved_completion_wake)
        wake_ids.add(reserved_completion_wake.wake_id)
    for wake in candidates:
        if len(wakes) >= max(1, int(max_wakes)) or wake.wake_id in wake_ids:
            continue
        next_event_ids = event_ids | set(wake.event_ids)
        next_families = families | set(wake.forecast_families)
        if (
            len(next_event_ids) > max(1, int(max_event_ids))
            or len(next_families) > max(1, int(max_forecast_families))
        ):
            continue
        wakes.append(wake)
        wake_ids.add(wake.wake_id)
        event_ids = next_event_ids
        families = next_families
    return tuple(wakes)


def _held_sell_reauction_receipt_path(
    request_id: str,
    *,
    path: Path | None = None,
) -> Path:
    return _held_sell_reauction_receipt_dir(path) / f"{request_id}.json"


def _held_sell_reauction_lineage_path(
    scope_identity: str,
    *,
    path: Path | None = None,
) -> Path:
    return _held_sell_reauction_receipt_dir(path) / f"v4-{scope_identity}.json"


def _held_sell_reauction_attempt_receipt_path(
    request_id: str,
    attempt_identity: str,
    *,
    path: Path | None = None,
) -> Path:
    return _held_sell_reauction_receipt_dir(path) / (
        f"{request_id}.{attempt_identity}.receipt.json"
    )


def _held_sell_reauction_lineage_lock_path(
    scope_identity: str,
    *,
    path: Path | None = None,
) -> Path:
    """Return the cross-process lock for one stable V4 debt scope."""

    return _held_sell_reauction_receipt_dir(path) / f".v4-{scope_identity}.lock"


@contextmanager
def _held_sell_reauction_lineage_locks(
    scope_identities: tuple[str, ...],
    *,
    path: Path | None = None,
    timeout_seconds: float | None = (
        HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT_SECONDS
    ),
) -> Iterator[None]:
    """Serialize V4 publish, receipt, completion fence, and acknowledgement."""

    scopes = tuple(sorted(set(scope_identities)))
    if not scopes or any(not scope for scope in scopes):
        raise ValueError("HELD_SELL_REAUCTION_SCOPE_IDENTITY_INVALID")
    timeout = None if timeout_seconds is None else float(timeout_seconds)
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0.0):
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT_INVALID")
    deadline = None if timeout is None else time.monotonic() + timeout

    def _remaining() -> float:
        assert deadline is not None
        return max(0.0, deadline - time.monotonic())

    directory = _held_sell_reauction_receipt_dir(path)
    directory.mkdir(parents=True, exist_ok=True)
    if deadline is None:
        acquired = _HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.acquire()
    else:
        acquired = _HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.acquire(
            timeout=_remaining()
        )
    if not acquired:
        raise TimeoutError("HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT")
    try:
        descriptors = []
        try:
            for scope_identity in scopes:
                descriptor = os.open(
                    _held_sell_reauction_lineage_lock_path(
                        scope_identity,
                        path=path,
                    ),
                    os.O_CREAT | os.O_RDWR,
                    0o600,
                )
                descriptors.append(descriptor)
                if deadline is None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                else:
                    while True:
                        try:
                            fcntl.flock(
                                descriptor,
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                            break
                        except BlockingIOError as exc:
                            remaining = _remaining()
                            if remaining <= 0.0:
                                raise TimeoutError(
                                    "HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT"
                                ) from exc
                            time.sleep(min(0.01, remaining))
            yield
        finally:
            active_error = sys.exc_info()[1]
            release_errors: list[OSError] = []
            for descriptor in reversed(descriptors):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as exc:
                    release_errors.append(exc)
                finally:
                    try:
                        os.close(descriptor)
                    except OSError as exc:
                        release_errors.append(exc)
            if release_errors and active_error is None:
                raise release_errors[0]
    finally:
        _HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.release()


@contextmanager
def _held_sell_reauction_lineage_lock(
    scope_identity: str,
    *,
    path: Path | None = None,
    timeout_seconds: float | None = (
        HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT_SECONDS
    ),
) -> Iterator[None]:
    lineage_lock = (
        _held_sell_reauction_lineage_locks((scope_identity,), path=path)
        if timeout_seconds
        in (None, HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT_SECONDS)
        else _held_sell_reauction_lineage_locks(
            (scope_identity,),
            path=path,
            timeout_seconds=timeout_seconds,
        )
    )
    with lineage_lock:
        yield


def _held_sell_reauction_receipt_from_payload(
    payload: object,
    *,
    request_id: str,
) -> HeldSellReauctionReceipt | None:
    """Decode and validate one immutable terminal receipt payload."""

    if not isinstance(payload, dict):
        return None
    try:
        receipt = HeldSellReauctionReceipt(
            request_id=str(payload["request_id"]).strip(),
            material_identity=str(payload["material_identity"]).strip(),
            generation=str(payload["generation"]).strip(),
            status=str(payload["status"]).strip(),
            reason=str(payload["reason"]).strip(),
            lifecycle_phase=str(payload.get("lifecycle_phase") or "").strip(),
            chain_state=str(payload.get("chain_state") or "").strip(),
            chain_shares=(
                None
                if payload.get("chain_shares") in (None, "")
                else float(payload["chain_shares"])
            ),
            settled_at=str(payload.get("settled_at") or "").strip(),
            selection_epoch_identity=str(
                payload.get("selection_epoch_identity") or ""
            ).strip(),
            sell_book_witness_identity=str(
                payload.get("sell_book_witness_identity") or ""
            ).strip(),
            schema_version=int(payload.get("schema_version", 1)),
            scope_identity=str(payload.get("scope_identity") or "").strip(),
            book_state=str(payload.get("book_state") or "EXECUTABLE").strip(),
            capital_objective_proof=str(
                payload.get("capital_objective_proof") or ""
            ).strip(),
            answered_probability_content_identity=str(
                payload.get("answered_probability_content_identity") or ""
            ).strip(),
            attempt_identity=str(payload.get("attempt_identity") or "").strip(),
            position_id=str(payload.get("position_id") or "").strip(),
            held_token_id=str(payload.get("held_token_id") or "").strip(),
            debt_event_id=str(payload.get("debt_event_id") or "").strip(),
            debt_sequence_no=int(payload.get("debt_sequence_no") or 0),
            monitor_event_id=str(payload.get("monitor_event_id") or "").strip(),
            monitor_sequence_no=int(payload.get("monitor_sequence_no") or 0),
            monitor_occurred_at=str(
                payload.get("monitor_occurred_at") or ""
            ).strip(),
            monitor_payload_sha256=str(
                payload.get("monitor_payload_sha256") or ""
            ).strip(),
            monitor_probability=(
                None
                if payload.get("monitor_probability") is None
                else float(payload["monitor_probability"])
            ),
            monitor_probability_is_fresh=payload.get(
                "monitor_probability_is_fresh"
            ),
            monitor_selected_method=str(
                payload.get("monitor_selected_method") or ""
            ).strip(),
            monitor_should_exit=payload.get("monitor_should_exit"),
            monitor_trigger=str(payload.get("monitor_trigger") or "").strip(),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        receipt.request_id != str(request_id or "").strip()
        or receipt.request_id
        != _held_sell_reauction_request_id(
            receipt.material_identity,
            receipt.generation,
            receipt.attempt_identity,
            schema_version=receipt.schema_version,
        )
        or not receipt.material_identity
        or not receipt.generation
        or receipt.schema_version not in {
            1,
            HELD_SELL_REAUCTION_V2,
            HELD_SELL_REAUCTION_V3,
            HELD_SELL_REAUCTION_V4,
        }
        or not receipt.reason
    ):
        return None
    if receipt.status == POSITION_NO_LONGER_EXPOSED:
        if not _terminal_no_longer_exposed_receipt_valid(receipt):
            return None
    elif receipt.status == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN:
        if not _structural_win_supersession_receipt_valid(receipt):
            return None
    elif receipt.schema_version == 1 and receipt.status not in {"ACTUATED", "REJECTED"}:
        return None
    elif receipt.schema_version in {
        HELD_SELL_REAUCTION_V2,
        HELD_SELL_REAUCTION_V3,
        HELD_SELL_REAUCTION_V4,
    } and (
        receipt.status
        not in {"ACTUATED", "CAPITAL_REJECTED", NO_EXECUTABLE_BOOK}
        or not receipt.scope_identity
        or (
            receipt.status == NO_EXECUTABLE_BOOK
            and (
                receipt.schema_version != HELD_SELL_REAUCTION_V4
                or receipt.book_state != NO_EXECUTABLE_BOOK
            )
        )
        or (
            receipt.status != NO_EXECUTABLE_BOOK
            and receipt.book_state != "EXECUTABLE"
        )
        or not receipt.answered_probability_content_identity
    ):
        return None
    if (
        receipt.schema_version in {HELD_SELL_REAUCTION_V3, HELD_SELL_REAUCTION_V4}
        and not receipt.attempt_identity
    ):
        return None
    if receipt.status == "ACTUATED" and not all(
        (
            receipt.selection_epoch_identity,
            receipt.sell_book_witness_identity,
        )
    ):
        return None
    if receipt.status == "CAPITAL_REJECTED" and not all(
        (
            receipt.selection_epoch_identity,
            receipt.sell_book_witness_identity,
            receipt.capital_objective_proof,
        )
    ):
        return None
    if receipt.status == NO_EXECUTABLE_BOOK and not all(
        (
            receipt.selection_epoch_identity,
            receipt.sell_book_witness_identity,
        )
    ):
        return None
    return receipt


def _v4_held_sell_reauction_lineage_request(
    lineage: HeldSellReauctionLineage,
) -> HeldSellReauctionRequest:
    requests = _clean_held_sell_reauction_requests((lineage.latest_request,))
    if len(requests) != 1:
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_REQUEST_INVALID")
    request = requests[0]
    if (
        request.schema_version != HELD_SELL_REAUCTION_V4
        or request.scope_identity != lineage.scope_identity
        or request.request_id != lineage.request_id
        or request.material_identity != lineage.material_identity
        or request.generation != lineage.generation
        or request.attempt_identity != lineage.latest_attempt_identity
    ):
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_IDENTITY_INVALID")
    return request


def _read_v4_held_sell_reauction_lineage(
    scope_identity: str,
    *,
    path: Path | None = None,
) -> HeldSellReauctionLineage | None:
    """Read one fixed-size V4 latest-attempt index; malformed state fails closed."""

    try:
        payload = json.loads(
            _held_sell_reauction_lineage_path(scope_identity, path=path).read_text(
                encoding="utf-8"
            )
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_INVALID") from exc
    if not isinstance(payload, dict):
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_INVALID")
    try:
        lineage = HeldSellReauctionLineage(
            scope_identity=str(payload["scope_identity"]).strip(),
            request_id=str(payload["request_id"]).strip(),
            material_identity=str(payload["material_identity"]).strip(),
            generation=str(payload["generation"]).strip(),
            latest_attempt_identity=str(
                payload["latest_attempt_identity"]
            ).strip(),
            latest_wake_id=str(payload["latest_wake_id"]).strip(),
            latest_request=dict(payload["latest_request"]),
            schema_version=int(payload["schema_version"]),
            lineage_version=int(payload["lineage_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_INVALID") from exc
    if (
        lineage.scope_identity != str(scope_identity or "").strip()
        or lineage.schema_version != HELD_SELL_REAUCTION_V4
        or lineage.lineage_version != 2
        or not all(
            (
                lineage.request_id,
                lineage.material_identity,
                lineage.generation,
                lineage.latest_attempt_identity,
                lineage.latest_wake_id,
            )
        )
    ):
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_INVALID")
    _v4_held_sell_reauction_lineage_request(lineage)
    return lineage


def latest_v4_held_sell_reauction_request(
    scope_identity: str,
    *,
    path: Path | None = None,
) -> HeldSellReauctionRequest | None:
    """Return the latest V4 witness with one bounded durable file read."""

    lineage = _read_v4_held_sell_reauction_lineage(scope_identity, path=path)
    return (
        _v4_held_sell_reauction_lineage_request(lineage)
        if lineage is not None
        else None
    )


def v4_held_sell_reauction_request_is_queued(
    request: HeldSellReauctionRequest,
    *,
    path: Path | None = None,
    lineage_lock_timeout_seconds: float | None = None,
) -> bool:
    """Check one V4 deterministic queue slot without scanning the backlog."""

    if request.schema_version != HELD_SELL_REAUCTION_V4:
        return False
    lineage_lock = (
        _held_sell_reauction_lineage_lock(
            request.scope_identity,
            path=path,
        )
        if lineage_lock_timeout_seconds is None
        else _held_sell_reauction_lineage_lock(
            request.scope_identity,
            path=path,
            timeout_seconds=lineage_lock_timeout_seconds,
        )
    )
    with lineage_lock:
        lineage = _read_v4_held_sell_reauction_lineage(
            request.scope_identity,
            path=path,
        )
        if lineage is None:
            return False
        latest = _v4_held_sell_reauction_lineage_request(lineage)
        if (
            latest.request_id != request.request_id
            or latest.generation != request.generation
            or latest.attempt_identity != request.attempt_identity
        ):
            return False
        queued = _read_reactor_wake_path(
            _v4_wake_queue_target(request.scope_identity, path=path),
            fail_on_error=True,
        )
        return bool(
            queued is not None
            and queued.wake_id == lineage.latest_wake_id
            and queued.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
            and queued.held_sell_reauction_requests == (latest,)
        )


def _write_v4_held_sell_reauction_lineage(
    request: HeldSellReauctionRequest,
    *,
    wake_id: str,
    path: Path | None = None,
) -> None:
    lineage = HeldSellReauctionLineage(
        scope_identity=request.scope_identity,
        request_id=request.request_id,
        material_identity=request.material_identity,
        generation=request.generation,
        latest_attempt_identity=request.attempt_identity,
        latest_wake_id=str(wake_id or "").strip(),
        latest_request=request.__dict__,
    )
    _v4_held_sell_reauction_lineage_request(lineage)
    if not lineage.latest_wake_id:
        raise ValueError("HELD_SELL_REAUCTION_LINEAGE_WAKE_INVALID")
    target = _held_sell_reauction_lineage_path(request.scope_identity, path=path)
    temp = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp.write_text(
            json.dumps(lineage.__dict__, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def held_sell_no_longer_exposed_reason(
    *,
    lifecycle_phase: str,
    chain_state: str,
    chain_shares: object,
    settled_at: str,
) -> str | None:
    """Return the exact completion reason only for phase-specific canonical proof."""

    phase = str(lifecycle_phase or "").strip()
    state = str(chain_state or "").strip()
    if isinstance(chain_shares, bool) or chain_shares is None:
        return None
    try:
        shares = float(chain_shares)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(shares) or shares < 0.0:
        return None

    if phase == "economically_closed":
        if state in {"chain_confirmed_zero", "synced"} and shares == 0.0:
            return SELL_OBLIGATION_ENDED_BY_CANONICAL_CHAIN_ZERO
        return None
    if phase == "settled":
        if (
            state in _HELD_SELL_SETTLED_CHAIN_STATES
            and str(settled_at or "").strip()
        ):
            return SELL_OBLIGATION_ENDED_BY_SETTLEMENT_ONLY
        return None
    if phase == "admin_closed":
        if state in _HELD_SELL_CHAIN_ZERO_CLOSED_STATES and shares == 0.0:
            return SELL_OBLIGATION_ENDED_BY_ADMIN_CLOSE_WITH_CHAIN_ZERO
        return None
    if phase == "voided":
        if state in _HELD_SELL_CHAIN_ZERO_CLOSED_STATES and shares == 0.0:
            return SELL_OBLIGATION_ENDED_BY_VOID_WITH_CHAIN_ZERO
        return None
    return None


def _terminal_no_longer_exposed_receipt_valid(
    receipt: HeldSellReauctionReceipt,
) -> bool:
    """Validate canonical closure proof without inventing auction or redeem evidence."""

    return (
        receipt.status == POSITION_NO_LONGER_EXPOSED
        and receipt.reason
        == held_sell_no_longer_exposed_reason(
            lifecycle_phase=receipt.lifecycle_phase,
            chain_state=receipt.chain_state,
            chain_shares=receipt.chain_shares,
            settled_at=receipt.settled_at,
        )
    )


def _structural_win_supersession_receipt_valid(
    receipt: HeldSellReauctionReceipt,
) -> bool:
    """Validate one exact V4 debt superseded by a later absorbing win."""

    if isinstance(receipt.monitor_probability, bool):
        return False
    try:
        probability = float(receipt.monitor_probability)
        monitor_time = datetime.fromisoformat(
            receipt.monitor_occurred_at.replace("Z", "+00:00")
        )
        int(receipt.monitor_payload_sha256, 16)
    except (TypeError, ValueError):
        return False
    return (
        receipt.schema_version == HELD_SELL_REAUCTION_V4
        and receipt.status == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
        and receipt.reason == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
        and bool(receipt.scope_identity)
        and bool(receipt.attempt_identity)
        and bool(receipt.position_id)
        and bool(receipt.held_token_id)
        and bool(receipt.debt_event_id)
        and receipt.debt_sequence_no > 0
        and receipt.monitor_sequence_no > receipt.debt_sequence_no
        and receipt.monitor_event_id
        == f"{receipt.position_id}:monitor_refreshed:{receipt.monitor_sequence_no}"
        and monitor_time.tzinfo is not None
        and len(receipt.monitor_payload_sha256) == 64
        and receipt.monitor_payload_sha256
        == receipt.monitor_payload_sha256.lower()
        and math.isfinite(probability)
        and probability == 1.0
        and receipt.monitor_probability_is_fresh is True
        and receipt.monitor_selected_method == "day0_absorbing_hard_fact"
        and receipt.monitor_should_exit is False
        and receipt.monitor_trigger == "DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD"
    )


def _read_held_sell_reauction_receipt(
    request_id: str,
    *,
    path: Path | None = None,
    attempt_identity: str = "",
) -> HeldSellReauctionReceipt | None:
    target = (
        _held_sell_reauction_attempt_receipt_path(
            request_id,
            attempt_identity,
            path=path,
        )
        if attempt_identity
        else _held_sell_reauction_receipt_path(request_id, path=path)
    )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return _held_sell_reauction_receipt_from_payload(payload, request_id=request_id)


def persist_held_sell_reauction_receipts(
    receipts: tuple[HeldSellReauctionReceipt, ...],
    *,
    path: Path | None = None,
) -> bool:
    """Durably record terminal global-auction outcomes before wake acknowledgement."""

    try:
        directory = _held_sell_reauction_receipt_dir(path)
        directory.mkdir(parents=True, exist_ok=True)
        for receipt in receipts:
            if (
                not isinstance(receipt, HeldSellReauctionReceipt)
                or receipt.schema_version not in {
                    1,
                    HELD_SELL_REAUCTION_V2,
                    HELD_SELL_REAUCTION_V3,
                    HELD_SELL_REAUCTION_V4,
                }
                or not receipt.request_id
                or not receipt.material_identity
                or not receipt.generation
                or receipt.request_id
                != _held_sell_reauction_request_id(
                    receipt.material_identity,
                    receipt.generation,
                    receipt.attempt_identity,
                    schema_version=receipt.schema_version,
                )
                or not receipt.reason
                or (
                    receipt.status == POSITION_NO_LONGER_EXPOSED
                    and not _terminal_no_longer_exposed_receipt_valid(receipt)
                )
                or (
                    receipt.status
                    == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
                    and not _structural_win_supersession_receipt_valid(receipt)
                )
                or (
                    receipt.status
                    not in {
                        POSITION_NO_LONGER_EXPOSED,
                        SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
                    }
                    and receipt.schema_version == 1
                    and receipt.status not in {"ACTUATED", "REJECTED"}
                )
                or (
                    receipt.status
                    not in {
                        POSITION_NO_LONGER_EXPOSED,
                        SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
                    }
                    and
                    receipt.schema_version in {
                        HELD_SELL_REAUCTION_V2,
                        HELD_SELL_REAUCTION_V3,
                        HELD_SELL_REAUCTION_V4,
                    }
                    and (
                        receipt.status
                        not in {
                            "ACTUATED",
                            "CAPITAL_REJECTED",
                            NO_EXECUTABLE_BOOK,
                        }
                        or not receipt.scope_identity
                        or (
                            receipt.status == NO_EXECUTABLE_BOOK
                            and (
                                receipt.schema_version != HELD_SELL_REAUCTION_V4
                                or receipt.book_state != NO_EXECUTABLE_BOOK
                            )
                        )
                        or (
                            receipt.status != NO_EXECUTABLE_BOOK
                            and receipt.book_state != "EXECUTABLE"
                        )
                        or not receipt.answered_probability_content_identity
                    )
                )
                or (
                    receipt.schema_version
                    in {HELD_SELL_REAUCTION_V3, HELD_SELL_REAUCTION_V4}
                    and not receipt.attempt_identity
                )
                or (
                    receipt.status == "ACTUATED"
                    and not (
                        receipt.selection_epoch_identity
                        and receipt.sell_book_witness_identity
                    )
                )
                or (
                    receipt.status == "CAPITAL_REJECTED"
                    and not (
                        receipt.selection_epoch_identity
                        and receipt.sell_book_witness_identity
                        and receipt.capital_objective_proof
                    )
                )
                or (
                    receipt.status == NO_EXECUTABLE_BOOK
                    and not (
                        receipt.selection_epoch_identity
                        and receipt.sell_book_witness_identity
                    )
                )
            ):
                raise ValueError("HELD_SELL_REAUCTION_RECEIPT_INVALID")
            target = _held_sell_reauction_receipt_path(receipt.request_id, path=path)
            if receipt.schema_version == HELD_SELL_REAUCTION_V4:
                # Each V4 attempt owns one immutable receipt file. Different
                # attempts never read/merge/write one shared payload.
                with _held_sell_reauction_lineage_lock(
                    receipt.scope_identity,
                    path=path,
                ):
                    lineage = _read_v4_held_sell_reauction_lineage(
                        receipt.scope_identity,
                        path=path,
                    )
                    if (
                        lineage is None
                        or lineage.request_id != receipt.request_id
                        or lineage.material_identity != receipt.material_identity
                        or lineage.generation != receipt.generation
                    ):
                        raise ValueError("HELD_SELL_REAUCTION_RECEIPT_LINEAGE_INVALID")
                    if (
                        receipt.status
                        == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
                    ):
                        latest = _v4_held_sell_reauction_lineage_request(lineage)
                        if (
                            receipt.attempt_identity != latest.attempt_identity
                            or receipt.position_id != latest.position_id
                            or receipt.held_token_id != latest.held_token_id
                        ):
                            raise ValueError(
                                "HELD_SELL_REAUCTION_SUPERSESSION_LINEAGE_INVALID"
                            )
                    target = _held_sell_reauction_attempt_receipt_path(
                        receipt.request_id,
                        receipt.attempt_identity,
                        path=path,
                    )
                    existing = _read_held_sell_reauction_receipt(
                        receipt.request_id,
                        path=path,
                        attempt_identity=receipt.attempt_identity,
                    )
                    if existing is not None:
                        continue
                    temp = target.with_name(
                        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
                    )
                    try:
                        temp.write_text(
                            json.dumps(
                                receipt.__dict__,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            encoding="utf-8",
                        )
                        os.replace(temp, target)
                    finally:
                        try:
                            temp.unlink()
                        except FileNotFoundError:
                            pass
                continue
            existing = _read_held_sell_reauction_receipt(receipt.request_id, path=path)
            if existing is not None:
                # The first valid terminal receipt is immutable authority. A
                # later coalesced cut may re-answer that completed attempt
                # while also carrying a fresh attempt in the same batch.
                # Preserve the original and continue so the fresh receipt is
                # not starved behind an idempotent old answer.
                continue
            temp = target.with_name(
                f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temp.write_text(
                    json.dumps(
                        receipt.__dict__, sort_keys=True, separators=(",", ":")
                    ),
                    encoding="utf-8",
                )
                os.replace(temp, target)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
    except (OSError, ValueError):
        return False
    return True


def held_sell_reauction_requests_completed(
    requests: tuple[HeldSellReauctionRequest, ...],
    *,
    path: Path | None = None,
    allow_structural_win_supersession: bool = False,
) -> bool:
    """A request completes only with its own durable terminal receipt.

    Structural-win supersession additionally requires an ack-time canonical DB
    revalidation under the trade writer transaction. Only that caller may set
    ``allow_structural_win_supersession``.
    """

    if not requests:
        return False
    for request in requests:
        if request.schema_version == HELD_SELL_REAUCTION_V4:
            try:
                with _held_sell_reauction_lineage_lock(
                    request.scope_identity,
                    path=path,
                ):
                    lineage = _read_v4_held_sell_reauction_lineage(
                        request.scope_identity,
                        path=path,
                    )
                    if lineage is None:
                        return False
                    latest = _v4_held_sell_reauction_lineage_request(lineage)
                    if (
                        request.request_id != latest.request_id
                        or request.material_identity != latest.material_identity
                        or request.generation != latest.generation
                        or request.attempt_identity != latest.attempt_identity
                    ):
                        return False
                    receipt = _read_held_sell_reauction_receipt(
                        request.request_id,
                        path=path,
                        attempt_identity=request.attempt_identity,
                    )
            except (OSError, ValueError):
                return False
        else:
            receipt = _read_held_sell_reauction_receipt(
                request.request_id,
                path=path,
            )
        if (
            receipt is None
            or receipt.material_identity != request.material_identity
            or receipt.generation != request.generation
            or receipt.schema_version != request.schema_version
            or (
                receipt.status
                == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
                and not allow_structural_win_supersession
            )
            or (
                request.schema_version in {
                    HELD_SELL_REAUCTION_V2,
                    HELD_SELL_REAUCTION_V3,
                    HELD_SELL_REAUCTION_V4,
                }
                and (
                    receipt.scope_identity != request.scope_identity
                    or (
                        receipt.status
                        == SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN
                        and (
                            not _structural_win_supersession_receipt_valid(receipt)
                            or receipt.position_id != request.position_id
                            or receipt.held_token_id != request.held_token_id
                        )
                    )
                    or (
                        receipt.status
                        not in {
                            POSITION_NO_LONGER_EXPOSED,
                            SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
                        }
                        and (
                            receipt.status
                            not in {
                                "ACTUATED",
                                "CAPITAL_REJECTED",
                                NO_EXECUTABLE_BOOK,
                            }
                            or not receipt.answered_probability_content_identity
                            or (
                                receipt.status == NO_EXECUTABLE_BOOK
                                and (
                                    request.schema_version
                                    != HELD_SELL_REAUCTION_V4
                                    or receipt.book_state
                                    != NO_EXECUTABLE_BOOK
                                    or not receipt.selection_epoch_identity
                                    or not receipt.sell_book_witness_identity
                                )
                            )
                        )
                    )
                )
            )
            or (
                request.schema_version
                in {HELD_SELL_REAUCTION_V3, HELD_SELL_REAUCTION_V4}
                and receipt.attempt_identity != request.attempt_identity
            )
        ):
            return False
    return True


def held_sell_reauction_recovery_snapshot_hard_deadline(
    scope_identity: str,
    *,
    timeout_seconds: float,
    path: Path | None = None,
) -> tuple[tuple[str, str, str, str] | None, bool]:
    """Read one V4 lineage/receipt snapshot without retaining the caller.

    This is a read-only recovery classifier. Killing its child can lose no
    command, receipt, or lifecycle write; timeout means the caller must defer
    classification and retry from durable truth on the next bounded pass.
    The budget bounds the parent poll after child start; OS process creation
    and final reap are cleanup tails. A retained unreaped child blocks another
    spawn, so those tails cannot accumulate into a process storm.
    """

    global _HELD_SELL_REAUCTION_RECOVERY_CHILD

    scope = str(scope_identity or "").strip()
    timeout = float(timeout_seconds)
    if not scope:
        raise ValueError("HELD_SELL_REAUCTION_SCOPE_IDENTITY_INVALID")
    if not math.isfinite(timeout) or timeout < 0.01:
        raise TimeoutError("held SELL recovery read has insufficient deadline")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    def stop_and_reap(process) -> None:
        if not process.is_alive():
            return
        process.terminate()
        process.join(timeout=min(0.25, max(0.01, remaining())))
        if process.is_alive():
            process.kill()
            process.join(timeout=0.25)

    lock_budget = min(0.05, timeout / 4.0)
    if not _HELD_SELL_REAUCTION_RECOVERY_CHILD_LOCK.acquire(
        timeout=lock_budget
    ):
        raise TimeoutError("held SELL recovery child is already active")
    receive_conn = None
    send_conn = None
    process = None
    started = False
    try:
        context = multiprocessing.get_context("spawn")
        receive_conn, send_conn = context.Pipe(duplex=False)
        process = context.Process(
            target=_held_sell_reauction_recovery_read_worker,
            args=(send_conn, scope, str(path or "")),
            name="zeus-held-sell-recovery-read",
            daemon=True,
        )
        prior = _HELD_SELL_REAUCTION_RECOVERY_CHILD
        if prior is not None:
            stop_and_reap(prior)
            if prior.is_alive():
                raise TimeoutError("prior held SELL recovery child is unreaped")
            prior.close()
            _HELD_SELL_REAUCTION_RECOVERY_CHILD = None
        process.start()
        started = True
        _HELD_SELL_REAUCTION_RECOVERY_CHILD = process
        send_conn.close()
        cleanup_reserve = min(0.25, timeout / 4.0)
        poll_budget = max(0.0, remaining() - cleanup_reserve)
        if poll_budget <= 0.0 or not receive_conn.poll(poll_budget):
            stop_and_reap(process)
            raise TimeoutError(
                f"held SELL recovery read exceeded {timeout:.2f}s child budget"
            )
        try:
            payload = json.loads(receive_conn.recv())
        except (EOFError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("HELD_SELL_REAUCTION_RECOVERY_READ_INVALID") from exc
        process.join(timeout=min(0.25, remaining()))
        stop_and_reap(process)
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise RuntimeError(
                str(
                    payload.get("error")
                    if isinstance(payload, dict)
                    else "HELD_SELL_REAUCTION_RECOVERY_READ_INVALID"
                )
            )
        raw_current = payload.get("current")
        current = (
            None
            if raw_current is None
            else tuple(str(value or "") for value in raw_current)
        )
        if current is not None and len(current) != 4:
            raise RuntimeError("HELD_SELL_REAUCTION_RECOVERY_READ_INVALID")
        return current, bool(payload.get("completed"))
    finally:
        if send_conn is not None:
            try:
                send_conn.close()
            except OSError:
                pass
        if receive_conn is not None:
            receive_conn.close()
        if started and process is not None:
            stop_and_reap(process)
            if not process.is_alive():
                process.close()
                if _HELD_SELL_REAUCTION_RECOVERY_CHILD is process:
                    _HELD_SELL_REAUCTION_RECOVERY_CHILD = None
        _HELD_SELL_REAUCTION_RECOVERY_CHILD_LOCK.release()


def acknowledge_reactor_wake(
    wake: ReactorWake,
    *,
    path: Path | None = None,
) -> bool:
    """Remove exactly one consumed wake and its matching legacy fallback."""

    return acknowledge_reactor_wakes((wake,), path=path)


def acknowledge_reactor_wakes(
    wakes: tuple[ReactorWake, ...],
    *,
    path: Path | None = None,
) -> bool:
    """Acknowledge one drain while fencing V4 against a concurrent refresh."""

    try:
        wake_ids = {wake.wake_id for wake in wakes}
        v4_pairs = tuple(
            (wake, request)
            for wake in wakes
            for request in wake.held_sell_reauction_requests
            if request.schema_version == HELD_SELL_REAUCTION_V4
        )

        def unlink_wakes() -> None:
            targets = [
                _wake_queue_target(queued, path=path)
                for queued in wakes
            ]
            legacy = _wake_path(path)
            latest = _read_reactor_wake_path(legacy)
            if latest is not None and latest.wake_id in wake_ids:
                targets.append(legacy)
            staged: list[tuple[Path, Path]] = []
            try:
                for target in dict.fromkeys(targets):
                    if not target.exists():
                        continue
                    staging = target.with_name(
                        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.ack-stage"
                    )
                    os.replace(target, staging)
                    staged.append((target, staging))
            except OSError:
                restore_error: OSError | None = None
                for original, staging in reversed(staged):
                    try:
                        if staging.exists():
                            os.replace(staging, original)
                    except OSError as exc:
                        if restore_error is None:
                            restore_error = exc
                if restore_error is not None:
                    raise restore_error
                raise
            for _original, staging in staged:
                try:
                    staging.unlink()
                except OSError:
                    # The hidden non-JSON stage is already outside the queue.
                    # Cleanup failure cannot turn a completed ack into failure.
                    pass

        if v4_pairs:
            with _held_sell_reauction_lineage_locks(
                tuple(request.scope_identity for _wake, request in v4_pairs),
                path=path,
            ):
                for wake, request in v4_pairs:
                    lineage = _read_v4_held_sell_reauction_lineage(
                        request.scope_identity,
                        path=path,
                    )
                    if lineage is None:
                        return False
                    latest_request = _v4_held_sell_reauction_lineage_request(lineage)
                    if (
                        lineage.latest_wake_id != wake.wake_id
                        or latest_request.request_id != request.request_id
                        or latest_request.generation != request.generation
                        or latest_request.attempt_identity != request.attempt_identity
                    ):
                        return False
                unlink_wakes()
        else:
            unlink_wakes()
    except (OSError, ValueError):
        return False
    return True


def reactor_wake_revision(
    *, path: Path | None = None
) -> tuple[int, int, int] | None:
    """Return a cheap revision for detecting atomic wake-file replacement."""

    try:
        stat = _wake_path(path).stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def reactor_urgent_wake_revision(
    *, path: Path | None = None
) -> tuple[int, int, int] | None:
    """Return a cheap revision for inputs whose alpha clock can preempt an epoch."""

    try:
        stat = _urgent_wake_path(path).stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def reactor_urgent_wake_reason(*, path: Path | None = None) -> str | None:
    """Return the reason carried by the current urgent-wake marker."""

    wake = _read_reactor_wake_path(_urgent_wake_path(path))
    return wake.reason if wake is not None else None


def reactor_urgent_wake_identity(
    *, path: Path | None = None
) -> tuple[str, str] | None:
    """Return the wake id and reason from one atomic urgent-marker read."""

    wake = _read_reactor_wake_path(_urgent_wake_path(path))
    return (wake.wake_id, wake.reason) if wake is not None else None
