# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/execution/family_book.py" block lines 619-650:
#   ExecutableLadder 623-629 [levels, side, fee_rate, min_tick_size, min_order_size],
#   MarketBook 631-642 [condition_id, bin_id, yes_token_id, no_token_id, yes_asks,
#   yes_bids, no_asks, no_bids, neg_risk], FamilyBook 643-650 [omega, markets,
#   captured_at_utc, book_hash, complete_book]) and Stage 7 block lines 1146-1164
#   (NO-as-basket / route set; the family route surface over all sibling markets,
#   native cost stays leaf). Reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD ONLY — no
#   live-file edits; the spec's "modify executable_cost to stay leaf-only" is deferred
#   to Stage 11 and the drift ledger records executable_cost is ALREADY leaf-only, so
#   it is REUSED unchanged here, never edited).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/strategy/live_inference/executable_cost.py::{QuoteLevel, NativeQuoteBook,
#                       executable_cost, quote_book_from_executable_snapshot}
#                       (the LEAF native-ladder walker — it correctly forbids
#                       midpoint/last/complement cost and walks ONE selected native
#                       ladder; family_book composes a per-sibling MarketBook OVER it
#                       and keeps it leaf-only)
#     - src/probability/outcome_space.py::OutcomeSpace / OutcomeBin
#                       (the complete Omega; complete_book is true ONLY when every
#                       omega bin has a MarketBook)
#     - src/contracts/executable_market_snapshot.py::ExecutableMarketSnapshot
#                       (neg_risk threaded per market from snapshot.neg_risk; the four
#                       native ladders sourced from snapshot.orderbook_depth_jsonb via
#                       the leaf quote_book_from_executable_snapshot)
"""FamilyBook — the executable family route surface over all sibling markets (Stage 7b).

This is Stage 7b of the q-kernel rebuild (consult_build_spec.md lines 619-650, Stage 7
block 1146-1164). A ``FamilyBook`` is the EXECUTABLE companion to the structural
``OutcomeSpace`` (Omega) and the economic ``Instrument`` payoff vectors (Stage 7a): it
carries, per sibling bin, the four NATIVE order-book ladders (yes/no asks/bids) that
the leaf ``executable_cost`` walker prices a route leg against — so a NO-as-basket
route (buy every OTHER sibling's YES) and the direct/synthetic/arb route comparisons
of Stage 7 (negrisk_routes.py) have ONE coherent, captured-at-an-instant surface to
read every sibling's depth from.

WHAT STAYS LEAF (operator law; drift ledger GREENFIELD):

  The native cost stays at the leaf. ``executable_cost`` (the live native-ladder
  walker) is REUSED UNCHANGED — it forbids midpoint/last/complement cost and walks
  ONE selected native ladder. ``family_book`` does NOT re-implement book walking and
  does NOT compute any cost itself; it COMPOSES a ``MarketBook`` per sibling, each of
  which can hand the leaf walker a ``NativeQuoteBook`` for ONE side. The midpoint /
  last-trade / NO-complement bans the leaf enforces therefore still apply to every
  family route leg — there is no family-level cost path that could bypass them.

THE ONE CONTRACT — ``complete_book`` is a STRUCTURAL consequence, not a detector
(operator law: make the bad output mathematically impossible; no gate/cap/flag that
catches an incomplete book and leaves a broken "complete" transform in place):

  ``complete_book`` is TRUE iff EVERY bin of the complete Omega has a ``MarketBook``.
  It is computed at construction as the set-equality ``frozenset(markets keyed by
  bin_id) == frozenset(omega bin_ids)`` — the ONLY path that produces the field. A
  family missing even one sibling market cannot be ``complete_book=True`` because the
  set equality cannot hold. There is no separately-passed boolean to disagree with the
  membership, so an incomplete family route surface (which a NO basket or a full-YES
  basket arb would price WRONG, because it would silently omit a sibling leg) is
  ``complete_book=False`` by construction — not flagged after the fact.

  Symmetrically, a ``MarketBook`` whose ``bin_id`` is NOT a member of the Omega is
  refused at construction (``FamilyBookError``): the markets mapping may only contain
  siblings of THIS family, so ``complete_book`` is a true "every sibling present and no
  stranger" statement.

NEG-RISK THREADED PER MARKET (spec line 641; Stage 7 route rule "If negRisk=True …"):

  Each ``MarketBook`` carries its OWN ``neg_risk``, threaded from the per-market
  ``ExecutableMarketSnapshot.neg_risk`` (or passed explicitly). negRisk is a PER-MARKET
  venue fact — the Stage 7 NO route rule ("if negRisk=False only direct NO may be used;
  if negRisk=True compare direct NO vs the synthetic sibling-YES basket") is decided
  per sibling, so the flag lives on the per-sibling ``MarketBook``, never as a single
  family-wide scalar. A family-level neg_risk would erase the per-market distinction the
  route rule needs; here it is structurally impossible to lose it.

BOOK HASH:

  ``book_hash`` is a deterministic digest over the complete captured surface (every
  sibling's condition/bin/token ids, all four ladders' price/size levels, tick / order
  size / fee / neg_risk, plus the Omega topology hash and the capture instant). It lets
  a route receipt prove which exact family book a route was priced against.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Mapping

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.probability.outcome_space import OutcomeSpace
from src.strategy.live_inference.executable_cost import (
    NativeQuoteBook,
    QuoteLevel,
    quote_book_from_executable_snapshot,
)


class FamilyBookError(ValueError):
    """Raised when a family book cannot be assembled as a coherent route surface.

    Fail-closed signal: a market does not belong to the family's Omega, a capture
    instant is not tz-aware UTC, or the per-market neg_risk / token ids are
    inconsistent — so there is no coherent executable family surface to price routes
    against, and it is refused rather than served partial.
    """


# ---------------------------------------------------------------------------
# ExecutableLadder (spec lines 623-629) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutableLadder:
    """One side of one sibling market's native book (spec lines 623-629).

    Field names are verbatim from consult_build_spec.md.

    * ``levels`` — the native price/size levels, best-first, as live ``QuoteLevel``
      (price/size in ``Decimal``) — the SAME primitive the leaf ``executable_cost``
      walker consumes, so a route leg walks these levels through the leaf unchanged.
    * ``side`` — ``"ask"`` (a buy walks asks) or ``"bid"`` (a sell walks bids). The
      yes/no asks ladders are ``"ask"``; the yes/no bids ladders are ``"bid"``.
    * ``fee_rate`` — the per-market taker fee fraction (``feeRate * p * (1-p)``), the
      SAME fraction the leaf applies via ``with_taker_fee`` / ``polymarket_fee``.
    * ``min_tick_size`` / ``min_order_size`` — the venue tick and minimum order size in
      probability units; the leaf walker asserts tick compatibility and minimum order
      size against these.

    This is a CARRIER, not a cost engine: it holds the captured ladder; the leaf
    ``executable_cost`` is the ONLY thing that walks it for a price (native cost stays
    leaf).
    """

    levels: tuple[QuoteLevel, ...]
    side: Literal["ask", "bid"]
    fee_rate: float
    min_tick_size: Decimal
    min_order_size: Decimal


# ---------------------------------------------------------------------------
# MarketBook (spec lines 631-642) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketBook:
    """One sibling market's complete native book (spec lines 631-642).

    Field names are verbatim from consult_build_spec.md. Holds the four NATIVE ladders
    (yes/no asks/bids) the leaf ``executable_cost`` walks, plus the per-market venue
    identity (``condition_id``, the Omega ``bin_id`` this market resolves, the
    ``yes_token_id`` / ``no_token_id``) and the per-market ``neg_risk`` flag the Stage 7
    NO route rule branches on.

    ``native_quote_book()`` reassembles the leaf ``NativeQuoteBook`` (all four sides +
    tick / order size / fee / neg_risk) so a route leg is priced by the leaf walker —
    family_book never prices anything itself.
    """

    condition_id: str
    bin_id: str
    yes_token_id: str
    no_token_id: str
    yes_asks: ExecutableLadder
    yes_bids: ExecutableLadder
    no_asks: ExecutableLadder
    no_bids: ExecutableLadder
    neg_risk: bool

    def __post_init__(self) -> None:
        # The four ladders must declare the side they actually are (a "bid" ladder in
        # an asks slot would mis-price a buy). This is a typing invariant on the
        # carrier, not a cost gate — the leaf walker selects a side by direction.
        for name, ladder, expected in (
            ("yes_asks", self.yes_asks, "ask"),
            ("no_asks", self.no_asks, "ask"),
            ("yes_bids", self.yes_bids, "bid"),
            ("no_bids", self.no_bids, "bid"),
        ):
            if ladder.side != expected:
                raise FamilyBookError(
                    f"MarketBook.{name} must be an {expected!r} ladder; got side={ladder.side!r}"
                )

    def native_quote_book(self) -> NativeQuoteBook:
        """Reassemble the leaf ``NativeQuoteBook`` so the leaf walker prices a leg.

        The four ladders share one venue's tick / min-order / fee (a single market), so
        the leaf book reads those from the ``yes_asks`` ladder. ``neg_risk`` is this
        market's own flag. The native cost STAYS LEAF: this hands the captured ladders
        to ``executable_cost`` (via ``NativeQuoteBook``) — it does not walk them here.
        """
        return NativeQuoteBook(
            yes_asks=self.yes_asks.levels,
            no_asks=self.no_asks.levels,
            yes_bids=self.yes_bids.levels,
            no_bids=self.no_bids.levels,
            min_tick_size=self.yes_asks.min_tick_size,
            min_order_size=self.yes_asks.min_order_size,
            fee_rate=self.yes_asks.fee_rate,
            neg_risk=self.neg_risk,
        )


# ---------------------------------------------------------------------------
# FamilyBook (spec lines 643-650) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyBook:
    """The executable family route surface over all sibling markets (spec 643-650).

    Field names are verbatim from consult_build_spec.md.

    * ``omega`` — the complete MECE Omega this family's siblings partition.
    * ``markets`` — a ``MarketBook`` per sibling, KEYED BY ``bin_id`` (the Omega bin the
      market resolves). The keys are a subset of the Omega bin ids; ``complete_book`` is
      true only when they are the WHOLE set.
    * ``captured_at_utc`` — the single capture instant for the whole surface (tz-aware
      UTC); every sibling's depth is as-of this instant so a route prices a coherent
      cross-section.
    * ``book_hash`` — a deterministic digest over the whole captured surface (proves
      which exact family book a route was priced against).
    * ``complete_book`` — TRUE iff every Omega bin has a ``MarketBook``. A STRUCTURAL
      consequence of ``markets`` vs ``omega.bins`` (computed by ``build_family_book``),
      never a separately-asserted flag.

    Prefer ``build_family_book`` to construct: it derives ``complete_book`` and
    ``book_hash`` from the markets + Omega so the two can never disagree with the
    membership. The constructor still re-validates (fail-closed) so a hand-built
    ``FamilyBook`` with a stranger market or a mismatched ``complete_book`` is refused.
    """

    omega: OutcomeSpace
    markets: Mapping[str, MarketBook]
    captured_at_utc: datetime
    book_hash: str
    complete_book: bool

    def __post_init__(self) -> None:
        captured = _require_utc(self.captured_at_utc)
        object.__setattr__(self, "captured_at_utc", captured)

        omega_bin_ids = tuple(b.bin_id for b in self.omega.bins)
        omega_bin_set = frozenset(omega_bin_ids)

        # Every market keys ITS OWN bin_id, and that bin must be a member of THIS Omega.
        # A market keyed under a bin_id it does not declare, or a market for a bin that
        # is not in the family, is a stranger — refused, so the markets mapping is a
        # pure subset of the family's siblings.
        for key, market in self.markets.items():
            if market.bin_id != key:
                raise FamilyBookError(
                    f"FamilyBook.markets key {key!r} != MarketBook.bin_id {market.bin_id!r}"
                )
            if key not in omega_bin_set:
                raise FamilyBookError(
                    f"FamilyBook market bin_id {key!r} is not a member of the family Omega "
                    f"(bins={list(omega_bin_ids)!r})"
                )

        # complete_book is the STRUCTURAL set-equality: every Omega bin present, no
        # stranger. The constructor re-derives it and refuses a passed value that
        # disagrees, so the field can NEVER claim complete while a sibling is missing.
        structural_complete = frozenset(self.markets.keys()) == omega_bin_set
        if self.complete_book != structural_complete:
            missing = sorted(omega_bin_set - frozenset(self.markets.keys()))
            raise FamilyBookError(
                f"FamilyBook.complete_book={self.complete_book!r} disagrees with the "
                f"structural sibling coverage (complete={structural_complete!r}; "
                f"missing siblings={missing!r}). complete_book must be true iff every "
                f"Omega bin has a MarketBook."
            )

    def missing_bin_ids(self) -> tuple[str, ...]:
        """Omega bin ids with no ``MarketBook`` (empty iff ``complete_book``)."""
        present = frozenset(self.markets.keys())
        return tuple(b.bin_id for b in self.omega.bins if b.bin_id not in present)


# ---------------------------------------------------------------------------
# Builders — derive complete_book + book_hash structurally (the preferred path).
# ---------------------------------------------------------------------------

def build_family_book(
    *,
    omega: OutcomeSpace,
    markets: Mapping[str, MarketBook],
    captured_at_utc: datetime,
) -> FamilyBook:
    """Assemble a ``FamilyBook`` deriving ``complete_book`` and ``book_hash`` structurally.

    ``complete_book`` is computed ONCE here as the set-equality between the supplied
    markets (keyed by bin_id) and the Omega bin ids — it is NOT an input. A family
    missing a sibling market is ``complete_book=False`` by the only path that sets it.
    ``book_hash`` is the deterministic digest over the whole captured surface.
    """
    captured = _require_utc(captured_at_utc)
    omega_bin_set = frozenset(b.bin_id for b in omega.bins)
    complete_book = frozenset(markets.keys()) == omega_bin_set
    book_hash = compute_book_hash(omega=omega, markets=markets, captured_at_utc=captured)
    return FamilyBook(
        omega=omega,
        markets=dict(markets),
        captured_at_utc=captured,
        book_hash=book_hash,
        complete_book=complete_book,
    )


def market_book_from_snapshot(
    snapshot: ExecutableMarketSnapshot,
    *,
    bin_id: str,
) -> MarketBook:
    """Build one sibling ``MarketBook`` from an ``ExecutableMarketSnapshot``.

    The four native ladders come from the leaf ``quote_book_from_executable_snapshot``
    (which parses ``snapshot.orderbook_depth_jsonb`` into native YES/NO asks/bids — the
    SAME leaf primitive the cost walker uses, so the family surface and the cost path
    read the same depth). ``neg_risk`` is threaded from ``snapshot.neg_risk`` (the
    per-market venue fact the Stage 7 NO route rule branches on); the tick / min-order /
    fee carried on each ladder are the snapshot's own.
    """
    native = quote_book_from_executable_snapshot(snapshot)
    return MarketBook(
        condition_id=snapshot.condition_id,
        bin_id=bin_id,
        yes_token_id=snapshot.yes_token_id,
        no_token_id=snapshot.no_token_id,
        yes_asks=ExecutableLadder(
            levels=native.yes_asks,
            side="ask",
            fee_rate=native.fee_rate,
            min_tick_size=native.min_tick_size,
            min_order_size=native.min_order_size,
        ),
        yes_bids=ExecutableLadder(
            levels=native.yes_bids,
            side="bid",
            fee_rate=native.fee_rate,
            min_tick_size=native.min_tick_size,
            min_order_size=native.min_order_size,
        ),
        no_asks=ExecutableLadder(
            levels=native.no_asks,
            side="ask",
            fee_rate=native.fee_rate,
            min_tick_size=native.min_tick_size,
            min_order_size=native.min_order_size,
        ),
        no_bids=ExecutableLadder(
            levels=native.no_bids,
            side="bid",
            fee_rate=native.fee_rate,
            min_tick_size=native.min_tick_size,
            min_order_size=native.min_order_size,
        ),
        neg_risk=snapshot.neg_risk,
    )


def family_book_from_snapshots(
    *,
    omega: OutcomeSpace,
    snapshots_by_bin_id: Mapping[str, ExecutableMarketSnapshot],
    captured_at_utc: datetime,
) -> FamilyBook:
    """Assemble a ``FamilyBook`` from per-sibling ``ExecutableMarketSnapshot`` rows.

    Each snapshot is threaded into a ``MarketBook`` (its own ``neg_risk`` carried per
    market). ``complete_book`` is the structural set-equality between the supplied bins
    and the Omega — a family missing a sibling snapshot is ``complete_book=False`` by
    construction.
    """
    markets = {
        bin_id: market_book_from_snapshot(snapshot, bin_id=bin_id)
        for bin_id, snapshot in snapshots_by_bin_id.items()
    }
    return build_family_book(
        omega=omega,
        markets=markets,
        captured_at_utc=captured_at_utc,
    )


# ---------------------------------------------------------------------------
# Book hash — deterministic identity over the whole captured surface.
# ---------------------------------------------------------------------------

def compute_book_hash(
    *,
    omega: OutcomeSpace,
    markets: Mapping[str, MarketBook],
    captured_at_utc: datetime,
) -> str:
    """Deterministic digest over the complete captured family route surface.

    Covers the Omega topology hash, the capture instant, and — for every sibling in a
    STABLE (bin_id) order — its condition/bin/token ids, neg_risk, and all four ladders'
    (price, size) levels plus tick / order size / fee. Stable across process runs so a
    route receipt can prove the exact family book a route was priced against.
    """
    h = hashlib.sha256()
    h.update(omega.topology_hash.encode("utf-8"))
    h.update(_require_utc(captured_at_utc).isoformat().encode("utf-8"))
    for bin_id in sorted(markets.keys()):
        market = markets[bin_id]
        h.update(b"\x00MARKET\x00")
        h.update(
            f"{bin_id}|{market.condition_id}|{market.yes_token_id}|{market.no_token_id}|"
            f"{int(market.neg_risk)}".encode("utf-8")
        )
        for ladder_name, ladder in (
            ("yes_asks", market.yes_asks),
            ("yes_bids", market.yes_bids),
            ("no_asks", market.no_asks),
            ("no_bids", market.no_bids),
        ):
            h.update(f"|{ladder_name}|{ladder.side}|{ladder.fee_rate!r}|".encode("utf-8"))
            h.update(f"{ladder.min_tick_size}|{ladder.min_order_size}|".encode("utf-8"))
            for level in ladder.levels:
                h.update(f"{level.price}:{level.size};".encode("utf-8"))
    return h.hexdigest()


def _require_utc(value: datetime) -> datetime:
    """Coerce ``value`` to tz-aware UTC; fail closed on a non-datetime."""
    if not isinstance(value, datetime):
        raise FamilyBookError(
            f"captured_at_utc must be a datetime; got {type(value).__name__}"
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
