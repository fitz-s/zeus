# Created: 2026-07-02
# Authority basis: architecture/invariants.yaml
#   §1 "batch submit + safe prefixes" (BUILD (thin) — SDK post_orders/cancel_orders
#   exist, zero call sites) + architecture/invariants.yaml
#   §3.3 "Discrete repair pass" (lines 118-121) — W2.1 packet. Lands INERT: no
#   production call site. W3's solve is the intended future consumer.
"""Pure batch-submit primitives (W2.1): chunking, safe-prefix decomposition,
and fail-closed response->request mapping.

Three independent, side-effect-free tools. None of them touch the venue SDK,
the command journal, or sqlite — they are building blocks the adapter
(src/venue/polymarket_v2_adapter.py) and the execution-side orchestrator
(src/execution/batch_order_submission.py) compose.

1. ``chunk_orders`` — split an ordered sequence into groups of at most
   ``MAX_ORDERS_PER_BATCH``. Purely a size constraint (no economic
   reasoning).

2. ``compute_safe_prefixes`` — the design doc's "safe prefixes" cut
   (architecture/invariants.yaml:118-121): given an
   ordered list of planned orders each carrying an exposure delta, and a
   caller-injected acceptability predicate over cumulative exposure, produce
   batch boundaries such that every boundary leaves an acceptable exposure
   if all later batches never execute. Ordering is the caller's job; this
   function only verifies and cuts.

3. ``map_batch_items`` — the response->request mapping ruling for
   ``post_orders``/``cancel_orders`` batch responses. The SDK's response
   array shape (item order, whether it echoes an identifying field) is
   UNVERIFIED against the live API (no test fixture or usage anywhere in
   the repo at packet time — see locate brief risk notes). Mapping
   precedence, fail-closed:
     (a) echo-id: if every request's echo key is found in the response
         (via one of a short list of candidate field names), map by that.
     (b) index: else, if the response is a list of the SAME length as the
         requests, map positionally (strict length assertion).
     (c) unmapped: else (non-list response, length mismatch, or echo-id
         only partially resolves) -- every request in this call is
         unmapped. NEVER guess success for an item we cannot attribute.
   TODO(W2.1-live-verify): confirm the real post_orders response shape
   (array ordering, per-item success signal, echoed identifier field name)
   against Polymarket CLOB sandbox or docs before the first ARM'd batch
   submit. Until then, (a) is speculative and (b)/(c) are the load-bearing
   paths. cancel_orders IS live-verified (2026-07-05): it returns a single
   envelope dict, handled by ``map_cancel_envelope`` before this mapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence, TypeVar

# Self-imposed architecture constant -- the SDK itself has NO batch-size
# limit (grepped py_clob_client_v2 for MAX_ORDERS/chunk constants: zero
# hits). ≤15 orders/batch is an architecture decision, not an SDK
# constraint: architecture/invariants.yaml:119
# "repair onto venue quantization (tick rounding, minimum order size,
# ≤15-orders-per-batch)".
MAX_ORDERS_PER_BATCH = 15

T = TypeVar("T")


def chunk_orders(items: Sequence[T], chunk_size: int = MAX_ORDERS_PER_BATCH) -> list[list[T]]:
    """Split ``items`` into consecutive groups of at most ``chunk_size``.

    Pure size-based chunking -- no exposure/economic reasoning (that is
    ``compute_safe_prefixes``'s job). Preserves input order. Empty input
    yields an empty list of chunks (not a list containing one empty chunk).
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    return [list(items[i : i + chunk_size]) for i in range(0, len(items), chunk_size)]


# ---------------------------------------------------------------------------
# Safe-prefix decomposition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedBatchOrder:
    """One caller-ordered planned order carrying an exposure delta.

    ``order_ref`` is opaque to this module -- whatever the caller wants
    back (a command draft, an intent, a plain id). ``exposure_delta`` is
    the signed change in the acceptability predicate's state this order
    contributes if it executes (e.g. additional dollar exposure; sign and
    unit are entirely the caller's convention).
    """

    order_ref: Any
    exposure_delta: Decimal


def compute_safe_prefixes(
    planned_orders: Sequence[PlannedBatchOrder],
    is_acceptable: Callable[[Decimal], bool],
    *,
    max_batch_size: int = MAX_ORDERS_PER_BATCH,
) -> list[list[Any]]:
    """Decompose an ordered plan into safe-prefix batches.

    Design law (architecture/invariants.yaml:120-121):
    "Batch plans decompose into safe prefixes -- every prefix leaves an
    acceptable exposure if later batches fail." A "prefix" here is a batch
    boundary: after batch i completes, the CUMULATIVE exposure realized so
    far (as if every batch after i simply never executes) must satisfy
    ``is_acceptable``.

    Algorithm (design decision, undocumented by the design doc beyond the
    boundary property itself): greedily extend the current batch up to
    ``max_batch_size`` orders, and cut at the LAST point within that window
    where cumulative exposure is acceptable -- this maximizes batch size
    (fewer HTTP calls) subject to staying safe and respecting the SDK-call
    size ceiling. If no acceptable cut point exists anywhere in the
    attempted window (not even after the single next order), nothing
    further is safe to submit and decomposition stops there.

    Degenerate cases:
      - empty ``planned_orders`` -> ``[]`` (no batches).
      - the very first order's own exposure_delta is already unacceptable
        -> ``[]`` (nothing is safe to submit).
      - any point where no acceptable cut exists before exhausting
        ``max_batch_size`` -> decomposition stops; that unsafe suffix is
        NOT included in the result (never submit past the last safe
        prefix).

    Returns a list of batches; each batch is a list of the original
    ``order_ref`` values (not ``PlannedBatchOrder`` wrappers) in their
    input order.
    """
    if max_batch_size <= 0:
        raise ValueError(f"max_batch_size must be > 0, got {max_batch_size}")
    if not planned_orders:
        return []

    batches: list[list[Any]] = []
    cumulative = Decimal(0)
    idx = 0
    n = len(planned_orders)

    while idx < n:
        running = cumulative
        window_refs: list[Any] = []
        best_cut: Optional[tuple[int, Decimal]] = None  # (count within window, cumulative there)
        for _ in range(max_batch_size):
            if idx + len(window_refs) >= n:
                break
            order = planned_orders[idx + len(window_refs)]
            running = running + order.exposure_delta
            window_refs.append(order.order_ref)
            if is_acceptable(running):
                best_cut = (len(window_refs), running)
        if best_cut is None:
            # No acceptable boundary anywhere in the attempted window --
            # nothing further is safe. Stop; do not include this suffix.
            return batches
        cut_len, cut_cumulative = best_cut
        batches.append(window_refs[:cut_len])
        cumulative = cut_cumulative
        idx += cut_len

    return batches


# ---------------------------------------------------------------------------
# Response -> request mapping (ruling 1, fail-closed)
# ---------------------------------------------------------------------------

# UNVERIFIED (see module docstring TODO): candidate field names a
# post_orders response item might use to echo back an identifier we sent.
# We locally compute a sha256 signed-order-hash per order the same way the
# single-order path does (polymarket_v2_adapter.py `_submit_once`), so the
# most plausible echo fields are hash-shaped; a client-supplied id is not
# part of the OrderArgs surface today so no clientOrderId-style match is
# attempted.
SUBMIT_ECHO_CANDIDATE_FIELDS: tuple[str, ...] = (
    "orderHash",
    "order_hash",
    "signedOrderHash",
    "signed_order_hash",
    "hash",
)

# UNVERIFIED (see module docstring TODO): candidate field names a
# cancel_orders response item might use to echo back the order id/hash we
# sent (cancel_orders takes a list of "order_hashes" per the SDK signature,
# which for the single-order cancel path is the venue-assigned orderID).
CANCEL_ECHO_CANDIDATE_FIELDS: tuple[str, ...] = (
    "orderID",
    "orderId",
    "order_id",
    "orderHash",
    "order_hash",
    "id",
)


def map_cancel_envelope(
    raw_response: Any, order_ids: Sequence[str]
) -> Optional[list["BatchMappedItem"]]:
    """Map the LIVE-VERIFIED cancel_orders envelope response shape.

    Live evidence (2026-07-05, commands 12e0ee45e0a44bc8 / 1a74acd884cf4ba5):
    DELETE /orders returns ONE dict for the whole batch, not a per-item
    array::

        {"canceled": ["0x9df6..."], "not_canceled": {"0x...": "reason"}}

    Membership is by exact order id. An id found in neither collection is
    unmapped (fail-closed, same ruling 1(c) as ``map_batch_items``). Returns
    ``None`` when ``raw_response`` is not this envelope shape so the caller
    can fall through to ``map_batch_items``.
    """
    if not isinstance(raw_response, dict):
        return None
    canceled_raw = raw_response.get("canceled", raw_response.get("cancelled"))
    not_canceled_raw = raw_response.get(
        "not_canceled", raw_response.get("not_cancelled")
    )
    if canceled_raw is None and not_canceled_raw is None:
        return None
    canceled: set[str] = set()
    if isinstance(canceled_raw, (list, tuple)):
        canceled = {str(item) for item in canceled_raw if item not in (None, "")}
    not_canceled: dict[str, Any] = {}
    if isinstance(not_canceled_raw, dict):
        not_canceled = {str(k): v for k, v in not_canceled_raw.items()}
    items: list[BatchMappedItem] = []
    for i, order_id in enumerate(order_ids):
        if order_id in canceled:
            items.append(
                BatchMappedItem(i, {"orderID": order_id, "canceled": [order_id]}, "envelope")
            )
        elif order_id in not_canceled:
            items.append(
                BatchMappedItem(
                    i,
                    {"orderID": order_id, "not_canceled": {order_id: not_canceled[order_id]}},
                    "envelope",
                )
            )
        else:
            items.append(BatchMappedItem(i, None, "unmapped"))
    return items


@dataclass(frozen=True)
class BatchMappedItem:
    """One request's outcome after mapping a batch response.

    ``raw_item`` is ``None`` iff ``source == "unmapped"`` -- the fail-closed
    branch. Callers must never synthesize a success outcome when
    ``raw_item`` is ``None``.
    """

    index: int
    raw_item: Optional[Any]
    source: str  # "echo_id" | "index" | "envelope" | "unmapped"


def _echo_identifier(item: Any, candidate_fields: Sequence[str]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for field in candidate_fields:
        value = item.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def map_batch_items(
    raw_response: Any,
    echo_keys: Sequence[Optional[str]],
    *,
    echo_candidate_fields: Sequence[str] = SUBMIT_ECHO_CANDIDATE_FIELDS,
) -> list[BatchMappedItem]:
    """Map a raw batch response onto the ordered list of ``echo_keys``.

    ``echo_keys[i]`` is the identifier request ``i`` would expect echoed
    back (e.g. a locally-computed signed-order-hash for submit, or the
    order id for cancel). ``None`` entries never participate in echo
    matching (they can still be resolved via the index fallback).

    See module docstring for the full (a)/(b)/(c) precedence. Never raises;
    a malformed response always resolves to ``"unmapped"`` for every
    request rather than throwing.
    """
    n = len(echo_keys)
    if not isinstance(raw_response, list):
        return [BatchMappedItem(i, None, "unmapped") for i in range(n)]

    items = list(raw_response)

    # (a) echo-id pass: build key->item from response items that carry a
    # recognizable identifier, then require ALL requests to resolve before
    # trusting it. A partial echo match is untrustworthy (we cannot tell
    # whether the unmatched requests were dropped, duplicated, or simply
    # use a field name we don't recognize) -- falls through to (b)/(c).
    echo_map: dict[str, Any] = {}
    for item in items:
        key = _echo_identifier(item, echo_candidate_fields)
        if key is not None and key not in echo_map:
            echo_map[key] = item
    if echo_map and all(key is not None and key in echo_map for key in echo_keys):
        return [
            BatchMappedItem(i, echo_map[key], "echo_id")  # type: ignore[index]
            for i, key in enumerate(echo_keys)
        ]

    # (b) index fallback -- STRICT length assertion.
    if len(items) == n:
        return [BatchMappedItem(i, items[i], "index") for i in range(n)]

    # (c) fail-closed: non-array already handled above; here it's a
    # length mismatch or an echo pass that only partially resolved.
    return [BatchMappedItem(i, None, "unmapped") for i in range(n)]
