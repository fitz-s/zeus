# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14.3 + §5.3 + §5.4 + §3 + Hidden #6/#15/#16
#                  + §13 (min-order gate) + operator directive 2026-06-08
"""ExecutableCostCurve — size-dependent all-in cost of a native BUY side.

Spec Phase 3 (§11), §14.3, §5.3-5.4, Hidden issues #6/#15/#16.

WHY THIS OBJECT EXISTS (Hidden #6 — "scalar VWMP hides the convex cost curve"):
  Pre-bin-selection Zeus scored a candidate with a single scalar all-in price
  (top-of-book VWMP + fee). For an order larger than the top level's depth that
  scalar UNDERSTATES the true fill cost: the order walks into deeper, worse
  levels. A single number cannot express that the marginal dollar costs more
  than the average dollar. ExecutableCostCurve makes the convexity first-class:

      avg_cost(s)      — depth-weighted all-in cost per share for a stake of
                         ``s`` USD, returned as a TYPED ExecutionPrice in
                         probability_units (the scalar Kelly boundary at the
                         chosen stake — spec §5.3).
      avg_cost_for_shares(n)
                       — depth-weighted all-in cost per share to buy exactly
                         ``n`` SHARES (the §13 min-order quantity the candidate-
                         proof path actually asks for). Walks shares directly so
                         a SHARE -> USD-at-top-price -> SHARE round-trip never
                         underfills a thin top level and FALSE-no-trades a
                         fillable side (spec §5.3/§5.4/§13).
      marginal_cost(s) — all-in cost per share of the next marginal dollar at
                         stake ``s`` (prices against the deepest level touched).
      max_fillable(p)  — total stake (USD) fillable at all-in cost <= ``p``.

LIVE PRICING OBJECT (S1, operator directive 2026-06-08):
  As of S1 this curve IS the single live pricing object on the candidate-proof
  path (src/engine/event_reactor_adapter.py _execution_price_from_snapshot):
  avg_cost_for_shares(min_order_size) emits the typed cost-of-entry that replaces
  the scalar VWMP kernel. There is no flag and no shadow branch — one path, one
  pricing object. (The USD-stake avg_cost / marginal_cost / max_fillable remain
  for the future §5.3 USD-parameterized ELG optimizer.)

MONOTONICITY (the relationship this object guarantees, spec §5.3, Hidden #6):
  Levels are sorted ascending by price and walked cheapest-first. The all-in
  per-share transform g(p) = p + fee_rate*p*(1-p) is strictly increasing on
  (0, 1) (g'(p) = 1 + fee_rate - 2*fee_rate*p > 0 for fee_rate < 1). Therefore
  BOTH avg_cost(s) and marginal_cost(s) are monotone NON-DECREASING in stake s
  for a BUY. A relationship test (tests/contracts/test_executable_cost_curve.py)
  pins this property so a regression that re-flattens the curve fails CI.

TYPED BOUNDARY (spec §14.3, §5.3; INV-12 / D3 from execution_price.py):
  avg_cost returns ``ExecutionPrice`` — never a raw float — so the convex cost
  curve cannot launder a bare float into the Kelly boundary. marginal_cost and
  max_fillable return ``Decimal`` (internal optimizer quantities, not the Kelly
  cost-of-entry). All price/size arithmetic is Decimal, not float (spec §5.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from src.contracts.execution_price import ExecutionPrice

# Decimal numerics: a per-share all-in price lives in probability_units (0, 1);
# stake notional is a small USD figure. Default Decimal context precision (28)
# is ample. We deliberately do NOT quantize intermediate sums — quantization
# happens only when a value crosses the typed ExecutionPrice boundary, and
# even there we keep full precision because ExecutionPrice stores a float.

# Tolerance for Decimal comparisons against the min-tick grid and for the
# depth-exhaustion / min-order guards. Tight enough that a genuinely
# off-grid price is rejected, loose enough to absorb Decimal round-off.
_GRID_EPS = Decimal("1e-9")
_DEPTH_EPS = Decimal("1e-9")


@dataclass(frozen=True)
class BookLevel:
    """One price level of the executable asks ladder for a BUY.

    Attributes:
        price: ask price in probability_units, in (0, 1). For a BUY this is the
            raw venue ask BEFORE fees; the fee is applied by the owning
            ExecutableCostCurve via its FeeModel.
        size: shares available at ``price``. Must be > 0.
    """

    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.price < Decimal("1")):
            raise ValueError(
                f"BookLevel.price must be in (0, 1) probability_units, got {self.price}"
            )
        if not (self.size > Decimal("0")):
            raise ValueError(f"BookLevel.size must be > 0, got {self.size}")


@dataclass(frozen=True)
class FeeModel:
    """Polymarket taker fee model — fee_per_share = fee_rate * p * (1 - p).

    Spec §5.4 + docs.polymarket.com/trading/fees. This is the SAME formula as
    ``src.contracts.execution_price.polymarket_fee`` (single source of truth for
    the fee shape) but evaluated in Decimal space so the cost curve never drops
    to float for a price/size computation (spec §5.4). The fee is highest at
    p=0.50 and near-zero at the extremes — it is NOT a flat percentage.

    Hidden #15 (fee hash drift): fee_rate is carried on the curve and therefore
    on any cache/score key derived from it, so a post-decision fee change
    invalidates the cached cost rather than silently violating Kelly
    monotonicity.
    """

    fee_rate: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.fee_rate < Decimal("1")):
            raise ValueError(
                f"FeeModel.fee_rate must be in [0, 1), got {self.fee_rate}"
            )

    def fee_per_share(self, price: Decimal) -> Decimal:
        """Per-share taker fee at ``price`` (probability_units).

        Mirrors ``polymarket_fee`` in Decimal space. ``price`` must be a valid
        tradeable ask in (0, 1); levels are validated at BookLevel construction,
        so this is a structural guarantee, not a runtime branch.
        """
        return self.fee_rate * price * (Decimal("1") - price)

    def all_in_price(self, price: Decimal) -> Decimal:
        """All-in per-share cost g(p) = p + fee_rate*p*(1-p) (probability_units)."""
        return price + self.fee_per_share(price)


@dataclass(frozen=True)
class ExecutableCostCurve:
    """Size-dependent all-in cost curve for one native BUY side (spec §14.3).

    Attributes (spec §14.3 verbatim shape):
        token_id: CLOB token id of the native side being priced.
        side: "YES" or "NO" — the native executable side. (Buying NO walks the
            NO token's OWN ask book; it is NOT 1 - YES. Spec §4 belief/exec
            separation. This object only ever prices a BUY against ``levels``.)
        snapshot_id: identity of the executable snapshot the levels came from.
        book_hash: content hash of the orderbook (cache/score key + audit).
        levels: the asks ladder as a tuple of BookLevel. Stored sorted ascending
            by price at construction so the depth walk is cheapest-first.
        fee_model: the Polymarket p(1-p) fee model for this market.
        min_tick: minimum price increment (probability_units). Every level price
            must lie on this grid (Hidden #16 — a tick change invalidates the
            curve rather than silently rounding a limit).
        min_order_size: minimum order size in SHARES. A stake whose share count
            is below this fails closed (§13 no-trade gate).
        quote_ttl: freshness budget of the snapshot. Carried for the cache /
            redecision layer; this object does not itself enforce expiry (that
            is the runtime's job at submit recapture).

    Methods return TYPED values at the boundaries that matter: avg_cost ->
    ExecutionPrice (the Kelly cost-of-entry at the chosen stake); marginal_cost
    and max_fillable -> Decimal (internal optimizer quantities).
    """

    token_id: str
    side: Literal["YES", "NO"]
    snapshot_id: str
    book_hash: str
    levels: tuple[BookLevel, ...]
    fee_model: FeeModel
    min_tick: Decimal
    min_order_size: Decimal
    quote_ttl: timedelta

    def __post_init__(self) -> None:
        if self.side not in ("YES", "NO"):
            raise ValueError(f"side must be 'YES' or 'NO', got {self.side!r}")
        if not self.levels:
            # §13 no-trade gate: "Native side executable quote missing" /
            # "BUY candidate lacks native ask depth". An empty book is not a
            # zero-cost curve — it is an unexecutable side. Fail closed.
            raise ValueError(
                f"ExecutableCostCurve for token {self.token_id!r} has no ask "
                "levels; a BUY side with no executable depth is not tradeable"
            )
        if not (self.min_tick > Decimal("0")):
            raise ValueError(f"min_tick must be > 0, got {self.min_tick}")
        if not (self.min_order_size > Decimal("0")):
            raise ValueError(f"min_order_size must be > 0, got {self.min_order_size}")
        if self.quote_ttl <= timedelta(0):
            raise ValueError(f"quote_ttl must be positive, got {self.quote_ttl}")

        # Every level must sit on the min_tick grid (Hidden #16). An off-grid
        # price means the snapshot's tick assumption is stale relative to the
        # venue; reject rather than silently round a downstream limit price.
        for lvl in self.levels:
            ratio = lvl.price / self.min_tick
            nearest = ratio.to_integral_value()
            if abs(ratio - nearest) > _GRID_EPS:
                raise ValueError(
                    f"level price {lvl.price} is not aligned to min_tick "
                    f"{self.min_tick} (Hidden #16: tick-size change invalidates "
                    "the curve)"
                )

        # Store levels sorted ascending by price so the depth walk is always
        # cheapest-first (the structural basis of monotonicity, spec §5.3).
        # object.__setattr__ because the dataclass is frozen.
        ordered = tuple(sorted(self.levels, key=lambda lvl: lvl.price))
        object.__setattr__(self, "levels", ordered)

    # ------------------------------------------------------------------
    # Core depth walk (spec §5.3-5.4).
    # ------------------------------------------------------------------

    def _walk_for_stake(
        self, stake_usd: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Walk asks spending up to ``stake_usd`` USD all-in. Cheapest-first.

        Returns ``(shares_filled, all_in_usd_spent, last_all_in_price)`` where:
          * shares_filled        — total shares bought (Decimal).
          * all_in_usd_spent     — total all-in USD spent incl. fees (Decimal).
          * last_all_in_price    — all-in per-share price of the deepest level
                                   touched (the marginal price). Decimal.

        Raises ValueError (fail closed, spec §13) when:
          * stake_usd <= 0,
          * the book cannot fill the full stake (depth exhausted),
          * the resulting share count is below ``min_order_size``.

        The fill is computed in SHARE space against each level's capacity and in
        USD space against the remaining stake. At each level the all-in price
        per share is g(p) = p + fee(p); shares affordable from the remaining
        stake at that level are ``remaining_usd / g(p)``, capped by level depth.
        """
        if stake_usd <= Decimal("0"):
            raise ValueError(f"stake_usd must be > 0, got {stake_usd}")

        remaining_usd = stake_usd
        shares_filled = Decimal("0")
        usd_spent = Decimal("0")
        last_all_in_price = Decimal("0")

        for lvl in self.levels:
            if remaining_usd <= _DEPTH_EPS:
                break
            all_in_p = self.fee_model.all_in_price(lvl.price)
            # Shares this level can provide vs. shares the remaining stake buys.
            shares_at_level_capacity = lvl.size
            shares_affordable = remaining_usd / all_in_p
            take = min(shares_at_level_capacity, shares_affordable)
            if take <= Decimal("0"):
                continue
            cost = take * all_in_p
            shares_filled += take
            usd_spent += cost
            remaining_usd -= cost
            last_all_in_price = all_in_p

        # Depth exhaustion: the book could not absorb the full stake.
        # ``remaining_usd`` materially above zero means we walked off the end of
        # the ladder. Fail closed (§13: "Optimal stake above allowed depth").
        if remaining_usd > _DEPTH_EPS:
            max_notional = sum(
                (self.fee_model.all_in_price(lvl.price) * lvl.size for lvl in self.levels),
                Decimal("0"),
            )
            raise ValueError(
                f"stake {stake_usd} USD exceeds executable depth on token "
                f"{self.token_id!r} (max all-in notional ~{max_notional}); "
                "fail closed rather than fabricate a fill price"
            )

        if shares_filled < self.min_order_size - _DEPTH_EPS:
            raise ValueError(
                f"stake {stake_usd} USD buys {shares_filled} shares, below "
                f"min_order_size {self.min_order_size} (§13 no-trade gate)"
            )

        return shares_filled, usd_spent, last_all_in_price

    def _walk_for_shares(self, shares: Decimal) -> tuple[Decimal, Decimal]:
        """Walk asks buying exactly ``shares`` shares. Cheapest-first.

        Returns ``(shares_filled, all_in_usd_spent)``. Unlike ``_walk_for_stake``
        (which spends a USD budget), this fills an exact SHARE count — the
        question the candidate-proof path actually asks (§13 sizes the candidate
        at ``min_order_size`` shares, the smallest executable taker order).

        WHY A SEPARATE SHARE-PARAMETERIZED WALK EXISTS (share<->USD boundary,
        spec §5.3/§5.4):
          ``avg_cost(stake_usd)`` is USD-stake-parameterized (the form the §5.3
          ELG optimizer differentiates). Converting a SHARE count to a USD stake
          at the TOP level's price UNDERFILLS whenever the top level's depth is
          smaller than the requested shares: the USD budget computed at the cheap
          top price buys fewer than the requested shares once the walk crosses
          into costlier deeper levels, tripping the §13 min-order gate as a FALSE
          no-trade for a side the depth-walk can in fact fill. The fix is to walk
          SHARES directly — never round-trip shares -> USD -> shares. This also
          restores byte-identical parity with the legacy share-parameterized VWMP
          kernel (``executable_cost._book_walk_average``) for ALL books, not only
          single-sufficient-level ones.

        Raises ValueError (fail closed, spec §13) when:
          * shares <= 0,
          * shares < min_order_size (the §13 min-order gate),
          * the book cannot fill ``shares`` (depth exhausted).
        """
        if shares <= Decimal("0"):
            raise ValueError(f"shares must be > 0, got {shares}")
        if shares < self.min_order_size - _DEPTH_EPS:
            raise ValueError(
                f"requested {shares} shares is below min_order_size "
                f"{self.min_order_size} (§13 no-trade gate)"
            )

        remaining = shares
        usd_spent = Decimal("0")
        for lvl in self.levels:
            if remaining <= _DEPTH_EPS:
                break
            all_in_p = self.fee_model.all_in_price(lvl.price)
            take = min(lvl.size, remaining)
            usd_spent += take * all_in_p
            remaining -= take

        # Depth exhaustion: the ladder could not supply the full share count.
        # Fail closed (§13: "Optimal stake above allowed depth") rather than
        # average over a partial fill and fabricate a cheaper-than-real price.
        if remaining > _DEPTH_EPS:
            total_depth = sum((lvl.size for lvl in self.levels), Decimal("0"))
            raise ValueError(
                f"requested {shares} shares exceeds executable depth on token "
                f"{self.token_id!r} (total ask depth {total_depth} shares); "
                "fail closed rather than fabricate a fill price"
            )

        return shares, usd_spent

    def avg_cost_for_shares(self, shares: Decimal) -> ExecutionPrice:
        """Depth-weighted all-in cost per share to buy exactly ``shares`` shares.

        Spec §5.3 typed scalar boundary, but parameterized by an exact SHARE
        count instead of a USD stake. This is the entry point the candidate-proof
        path uses to price the venue min-order quantity (§13) without the lossy
        shares -> USD -> shares conversion that ``avg_cost`` + a top-price notional
        would incur on a thin top level. Byte-identical to the legacy
        share-parameterized VWMP kernel's all-in result for the same book.

        Returns a fee-adjusted / fee_deducted ExecutionPrice in probability_units
        (passes ``assert_kelly_safe``; the fee is ALREADY in the value, so the
        caller must NOT re-apply ``with_taker_fee``).

        Monotone NON-DECREASING in ``shares`` for a BUY (Hidden #6): more shares
        walk into higher-priced levels whose all-in g(p) is strictly larger.
        """
        shares_filled, usd_spent = self._walk_for_shares(Decimal(shares))
        all_in_per_share = usd_spent / shares_filled
        return ExecutionPrice(
            value=float(all_in_per_share),
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )

    def avg_cost(self, stake_usd: Decimal) -> ExecutionPrice:
        """Depth-weighted all-in cost per share for ``stake_usd``, as ExecutionPrice.

        Spec §5.3: this is the scalar typed cost boundary at the chosen stake.
        The returned ExecutionPrice is ``fee_adjusted`` / ``fee_deducted=True``
        in ``probability_units`` so it passes ``assert_kelly_safe()`` and feeds
        ``kelly.kelly_size`` directly — the fee is ALREADY in the value, so the
        caller must NOT call ``with_taker_fee`` again (that would double-charge,
        the inverse of Hidden #15).

        Monotone NON-DECREASING in ``stake_usd`` for a BUY (Hidden #6): larger
        stakes walk into higher-priced levels whose all-in g(p) is strictly
        larger, so the depth-weighted average can only rise.
        """
        shares_filled, usd_spent, _ = self._walk_for_stake(Decimal(stake_usd))
        all_in_per_share = usd_spent / shares_filled
        return ExecutionPrice(
            value=float(all_in_per_share),
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )

    def marginal_cost(self, stake_usd: Decimal) -> Decimal:
        """All-in per-share cost of the next marginal dollar at ``stake_usd``.

        Returns the all-in price g(p) of the DEEPEST level touched by a stake of
        ``stake_usd`` — i.e. the price the next infinitesimal dollar pays. This
        is the quantity the §5.3 ELG optimizer differentiates; it is a Decimal,
        not an ExecutionPrice, because it is an internal optimizer quantity, not
        the Kelly cost-of-entry boundary.

        Monotone NON-DECREASING in ``stake_usd`` for a BUY (Hidden #6): the
        deepest level touched only moves to higher prices as stake grows, and
        g(p) is strictly increasing in p. On a strictly convex book
        marginal_cost(s) >= avg_cost(s).
        """
        _, _, last_all_in_price = self._walk_for_stake(Decimal(stake_usd))
        return last_all_in_price

    def max_fillable(self, limit_price: Decimal) -> Decimal:
        """Total stake (USD) fillable at an all-in cost per share <= ``limit_price``.

        Spec §5.4 / Hidden #16: a buyer with a limit price can only consume
        levels whose ALL-IN per-share cost (price + fee) is at or below the
        limit. Returns the cumulative all-in USD notional of those levels.
        ``limit_price`` is compared in probability_units against g(p), so the
        fee is included in the gate (a limit set against the raw ask would
        understate cost by the fee — Hidden #15).

        Returns Decimal("0") when no level is affordable (e.g. limit below the
        best all-in ask). Does NOT raise on min_order_size — it reports raw
        fillable depth; the §13 min-order gate is enforced at avg_cost time.
        """
        limit = Decimal(limit_price)
        fillable_usd = Decimal("0")
        for lvl in self.levels:
            all_in_p = self.fee_model.all_in_price(lvl.price)
            if all_in_p > limit + _GRID_EPS:
                # Levels are sorted ascending; once one is too expensive, all
                # deeper ones are too. Stop.
                break
            fillable_usd += all_in_p * lvl.size
        return fillable_usd

    @classmethod
    def schema_packet(cls) -> dict:
        """Typed schema descriptor for K2/K3 consumption contracts."""
        return {
            "type": "ExecutableCostCurve",
            "required_fields": [
                "token_id", "side", "snapshot_id", "book_hash", "levels",
                "fee_model", "min_tick", "min_order_size", "quote_ttl",
            ],
        }
