# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14.2 (NativeSideCandidate dataclass) +
#   §4 (native YES/NO side separation: belief / executable / portfolio spaces) +
#   §5.6 (q_lcb is a probability lower bound, not edge_ci_lower) +
#   §9 Hidden #1 (FDR denominator must include native NO) +
#   §9 Hidden #4 (native NO quote present but NO posterior missing) +
#   §13 No-Trade Gates (native side token / executable quote missing) +
#   operator directive 2026-06-08.
"""NativeSideCandidate — Phase-1 candidate contract (spec §14.2).

A ``NativeSideCandidate`` is the unified per-bin, per-side candidate object the
bin-selection upgrade ranks. It is a frozen, pure contract / dataclass with no
side effects: importing or constructing it changes NO live trading behavior.
Live eligibility and submission authority remain with the caller that consumes
the candidate.

Three spec §4 laws are encoded structurally so the wrong code is unwritable:

1. **Native YES/NO separation (executable space).** A NO candidate carries the
   NATIVE NO token + the NATIVE NO executable cost curve. It is NEVER a
   YES-complement executable price. ``p_exec(NO_i) != 1 - p_exec(YES_i)``.
   The factory refuses a cost curve whose ``side`` does not match the
   candidate's ``side`` (a YES curve cannot price a NO candidate).

2. **Missing native quote => no-trade, not complement (Hidden #4 / §13).**
   A missing native side token, or a present token with no executable ask,
   yields a NO-TRADE candidate carrying a :class:`CandidateNoTradeReason`.
   The candidate then holds NO executable curve and NO probability authority —
   there is nothing to complement-substitute from.

3. **Selected-token snapshot identity is per-side (§12.A.4).** The YES and NO
   sides of the SAME bin select DIFFERENT tokens, hence DIFFERENT snapshot
   identities. :meth:`NativeSideCandidate.selected_token_identity` keys on
   ``(token_id, side, market_snapshot_id)`` so a downstream executable snapshot
   hash differs between the two sides.

The peer objects :class:`ProbabilityUncertainty` and :class:`ExecutableCostCurve`
are built in parallel and may land slightly later. They are referenced ONLY
under ``TYPE_CHECKING`` / as ``object``-typed opaque payloads, so this module
imports cleanly regardless of their availability. The candidate reads only a
single structural fact off the cost curve — ``has_executable_ask`` — and stores
everything else opaquely.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only; peers may land later
    # Forward refs to peer contracts being built in parallel. Importing them
    # at runtime is intentionally avoided so this module is import-safe even if
    # the peer modules are not yet present.
    from src.contracts.executable_cost_curve import ExecutableCostCurve
    from src.strategy.probability_uncertainty import ProbabilityUncertainty


Side = Literal["YES", "NO"]
_VALID_SIDES: frozenset[str] = frozenset({"YES", "NO"})


class CandidateNoTradeReason(StrEnum):
    """Why a native side candidate is untradeable (a diagnostic, not a loss).

    A no-trade candidate is recorded — NOT silently omitted — so the
    family-wise FDR denominator and the learning layer can see that the side
    was tested and rejected (spec §9 Hidden #1, §11 Phase 1 acceptance, §14.6).
    These are CANDIDATE-level reasons; they are intentionally narrower than the
    evaluator-wide ``src.contracts.no_trade_reason.NoTradeReason`` so the
    candidate object stays self-contained and importable without the evaluator.
    """

    # ── Native side identity / quote availability (spec §4, §13) ──────────────
    NATIVE_TOKEN_MISSING = auto()
    """The native side token id is absent. No native identity to trade."""

    NATIVE_QUOTE_MISSING = auto()
    """The native token exists but carries no executable ask. NO complement
    substitution is permitted — buy-NO is blocked (Hidden #4 / §12.A.2)."""

    NATIVE_BOOK_NOT_ACCEPTING = auto()
    """The native book is closed / archived / not accepting orders (§13)."""

    # ── Probability authority (spec §5.6, Hidden #2/#3/#4) ────────────────────
    PROBABILITY_UNAVAILABLE = auto()
    """No native-side q / q_lcb authority exists for this side. For NO this
    means the independent NO posterior is missing — the YES complement may
    NOT be substituted (Hidden #4)."""

    Q_LCB_INVALID = auto()
    """q_lcb is unavailable, NaN, or out of [0, 1] (§13)."""


@dataclass(frozen=True)
class SideProbability:
    """Native-side belief carrier: point + lower-confidence bound for ONE side.

    This is the §4 belief-space object. Critically, a NO ``SideProbability`` is
    NOT derived by point-complementing a YES one: the lower tail of
    ``1 - q_yes`` is the UPPER tail of ``q_yes``, so ``q_lcb_no != 1 - q_lcb_yes``
    (spec §4 / §9 Hidden #3). The NO carrier must be supplied with an lcb
    computed from the NO (complement) SAMPLES, not from the YES lcb. This type
    carries the result; it does not compute the bound.

    Invariants:
      - ``side`` is exactly "YES" or "NO".
      - ``q_point`` and ``q_lcb`` are probabilities in [0, 1].
      - ``q_lcb <= q_point`` (a lower-confidence bound cannot exceed the point
        estimate; a violation signals edge_ci_lower masquerading as q_lcb —
        spec §5.6 / Hidden #2).
    """

    side: Side
    q_point: float
    q_lcb: float

    def __post_init__(self) -> None:
        if self.side not in _VALID_SIDES:
            raise ValueError(
                f"SideProbability.side must be 'YES' or 'NO', got {self.side!r}"
            )
        for name, value in (("q_point", self.q_point), ("q_lcb", self.q_lcb)):
            v = float(value)
            if not (0.0 <= v <= 1.0):
                raise ValueError(
                    f"SideProbability.{name} must be in [0, 1], got {value}"
                )
        if float(self.q_lcb) > float(self.q_point):
            raise ValueError(
                "SideProbability.q_lcb must be <= q_point (a lower-confidence "
                f"bound cannot exceed the point estimate); got q_lcb={self.q_lcb} "
                f"> q_point={self.q_point}. This is the edge_ci_lower-as-q_lcb "
                "confusion (spec §5.6 / Hidden #2)."
            )


@dataclass(frozen=True)
class NativeSideCandidate:
    """A single native YES/NO side candidate for one bin (spec §14.2).

    Constructed via the :meth:`tradeable` / :meth:`no_trade` factories rather
    than the raw constructor, so the spec §4 separation laws are enforced at
    every construction site.

    Fields (spec §14.2 dataclass). ``q_point`` / ``q_lcb`` /
    ``probability_uncertainty`` / ``executable_cost_curve`` are Optional here
    (vs the spec's non-Optional listing) because a NO-TRADE candidate carries
    none of them — making them required would force a fake complement value,
    which is exactly the failure mode §4 forbids. A TRADEABLE candidate always
    has them populated (enforced by :meth:`tradeable`).
    """

    family_key: str
    bin_id: str
    side: Side
    token_id: str
    condition_id: str
    q_point: Optional[float]
    q_lcb: Optional[float]
    probability_uncertainty: Optional["ProbabilityUncertainty"]
    executable_cost_curve: Optional["ExecutableCostCurve"]
    forecast_snapshot_id: str
    market_snapshot_id: str
    hypothesis_id: str
    no_trade_reason: Optional[CandidateNoTradeReason] = None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.side not in _VALID_SIDES:
            raise ValueError(
                f"NativeSideCandidate.side must be 'YES' or 'NO', got {self.side!r}"
            )
        if self.no_trade_reason is None:
            # Tradeable candidate: probability + executable curve are mandatory,
            # and the curve's side must be the candidate's native side.
            if self.q_point is None or self.q_lcb is None:
                raise ValueError(
                    "Tradeable NativeSideCandidate requires q_point and q_lcb; "
                    "a side with no native probability authority must be a "
                    "no-trade candidate (CandidateNoTradeReason.PROBABILITY_"
                    "UNAVAILABLE), not a point-complement fabrication."
                )
            qp, ql = float(self.q_point), float(self.q_lcb)
            for name, v in (("q_point", qp), ("q_lcb", ql)):
                if not (0.0 <= v <= 1.0):
                    raise ValueError(
                        f"NativeSideCandidate.{name} must be in [0, 1], got {v}"
                    )
            if ql > qp:
                raise ValueError(
                    "NativeSideCandidate.q_lcb must be <= q_point (lower bound "
                    f"cannot exceed point); got q_lcb={ql} > q_point={qp}. This "
                    "is the edge_ci_lower-as-q_lcb confusion (spec §5.6 / Hidden #2)."
                )
            if self.executable_cost_curve is None:
                raise ValueError(
                    "Tradeable NativeSideCandidate requires an executable_cost_"
                    "curve; a side with no executable quote must be a no-trade "
                    "candidate (CandidateNoTradeReason.NATIVE_QUOTE_MISSING), "
                    "not a YES-complement price."
                )
            if not self.token_id:
                raise ValueError(
                    "Tradeable NativeSideCandidate requires a native token_id; "
                    "a missing token must be a no-trade candidate "
                    "(CandidateNoTradeReason.NATIVE_TOKEN_MISSING)."
                )
            self._assert_curve_side_matches()

    def _assert_curve_side_matches(self) -> None:
        """Refuse a cost curve whose side differs from the candidate's side.

        This is the structural antibody for the §4 executable-space law:
        ``p_exec(NO_i) != 1 - p_exec(YES_i)``. Feeding a YES-side curve to a NO
        candidate is precisely the complement-pricing path the spec forbids;
        making it raise here makes "borrow the YES book to price NO"
        UNCONSTRUCTABLE. The check is duck-typed (``getattr``) because the real
        ``ExecutableCostCurve`` and any test stub both expose ``.side``; a curve
        without a ``side`` attribute is left unvalidated (peer not yet final).
        """
        curve_side = getattr(self.executable_cost_curve, "side", None)
        if curve_side is not None and curve_side != self.side:
            raise ValueError(
                f"executable_cost_curve.side={curve_side!r} does not match "
                f"candidate side={self.side!r}. A {self.side} candidate must "
                f"carry its OWN native {self.side} executable curve — never the "
                "opposite side's book (spec §4: p_exec(NO) != 1 - p_exec(YES))."
            )

    # ------------------------------------------------------------------
    # Derived identity
    # ------------------------------------------------------------------
    @property
    def is_tradeable(self) -> bool:
        """True iff this candidate is a tradeable native side (no no-trade reason)."""
        return self.no_trade_reason is None

    def selected_token_identity(self) -> tuple[str, str, str]:
        """Selected-token snapshot identity for this candidate (spec §12.A.4).

        Keyed on ``(token_id, side, market_snapshot_id)`` so the YES and NO
        sides of the SAME bin produce DIFFERENT identities (different token =>
        different snapshot). A downstream executable-snapshot hash keys on this
        tuple; equal tuples mean the same selected leg, different tuples mean a
        side/token/snapshot change that must invalidate any cached score.
        """
        return (self.token_id, self.side, self.market_snapshot_id)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def tradeable(
        cls,
        *,
        family_key: str,
        bin_id: str,
        side: Side,
        token_id: str,
        condition_id: str,
        q_point: float,
        q_lcb: float,
        probability_uncertainty: Optional["ProbabilityUncertainty"],
        executable_cost_curve: Optional["ExecutableCostCurve"],
        forecast_snapshot_id: str,
        market_snapshot_id: str,
        hypothesis_id: str,
    ) -> "NativeSideCandidate":
        """Build a candidate, downgrading to NO-TRADE when the native side is
        not executable.

        Downgrade rules (spec §4 / Hidden #4 / §13), checked in order:
          - ``side`` must be YES/NO (raises otherwise — a routing bug, not a
            tradeability fact).
          - empty ``token_id``  -> NATIVE_TOKEN_MISSING no-trade candidate.
          - curve missing OR ``not curve.has_executable_ask`` ->
            NATIVE_QUOTE_MISSING no-trade candidate. NO complement substitution.

        A curve whose ``side`` mismatches the candidate's side is a HARD ERROR
        (raises), not a downgrade: it indicates a caller tried to complement-
        price one side from the other's book.
        """
        if side not in _VALID_SIDES:
            raise ValueError(
                f"NativeSideCandidate.side must be 'YES' or 'NO', got {side!r}"
            )

        if not token_id:
            return cls.no_trade(
                family_key=family_key,
                bin_id=bin_id,
                side=side,
                token_id=token_id,
                condition_id=condition_id,
                forecast_snapshot_id=forecast_snapshot_id,
                market_snapshot_id=market_snapshot_id,
                reason=CandidateNoTradeReason.NATIVE_TOKEN_MISSING,
                hypothesis_id=hypothesis_id,
            )

        # A curve carrying the wrong side is a complement-pricing attempt:
        # raise BEFORE the executable-ask downgrade so the violation surfaces
        # rather than being masked as a benign no-trade.
        curve_side = getattr(executable_cost_curve, "side", None)
        if curve_side is not None and curve_side != side:
            raise ValueError(
                f"executable_cost_curve.side={curve_side!r} does not match "
                f"candidate side={side!r}. A {side} candidate must carry its "
                f"OWN native {side} executable curve — never the opposite side's "
                "book (spec §4: p_exec(NO) != 1 - p_exec(YES))."
            )

        if executable_cost_curve is None or not _curve_has_executable_ask(
            executable_cost_curve
        ):
            return cls.no_trade(
                family_key=family_key,
                bin_id=bin_id,
                side=side,
                token_id=token_id,
                condition_id=condition_id,
                forecast_snapshot_id=forecast_snapshot_id,
                market_snapshot_id=market_snapshot_id,
                reason=CandidateNoTradeReason.NATIVE_QUOTE_MISSING,
                hypothesis_id=hypothesis_id,
            )

        return cls(
            family_key=family_key,
            bin_id=bin_id,
            side=side,
            token_id=token_id,
            condition_id=condition_id,
            q_point=q_point,
            q_lcb=q_lcb,
            probability_uncertainty=probability_uncertainty,
            executable_cost_curve=executable_cost_curve,
            forecast_snapshot_id=forecast_snapshot_id,
            market_snapshot_id=market_snapshot_id,
            hypothesis_id=hypothesis_id,
            no_trade_reason=None,
        )

    @classmethod
    def no_trade(
        cls,
        *,
        family_key: str,
        bin_id: str,
        side: Side,
        token_id: str,
        condition_id: str,
        forecast_snapshot_id: str,
        market_snapshot_id: str,
        reason: CandidateNoTradeReason,
        hypothesis_id: str = "",
    ) -> "NativeSideCandidate":
        """Build a NO-TRADE candidate: a recorded diagnostic, not a price.

        It carries NO probability authority (``q_point``/``q_lcb`` = None),
        NO probability uncertainty, and NO executable cost curve — there is
        nothing to complement-substitute from. It is still recorded (not
        omitted) so the family hypothesis set / FDR denominator and the
        learning layer see the tested-and-untradeable side (spec §9 Hidden #1,
        §14.6).
        """
        if side not in _VALID_SIDES:
            raise ValueError(
                f"NativeSideCandidate.side must be 'YES' or 'NO', got {side!r}"
            )
        if not isinstance(reason, CandidateNoTradeReason):
            raise ValueError(
                "no_trade requires a CandidateNoTradeReason, got "
                f"{reason!r}"
            )
        return cls(
            family_key=family_key,
            bin_id=bin_id,
            side=side,
            token_id=token_id,
            condition_id=condition_id,
            q_point=None,
            q_lcb=None,
            probability_uncertainty=None,
            executable_cost_curve=None,
            forecast_snapshot_id=forecast_snapshot_id,
            market_snapshot_id=market_snapshot_id,
            hypothesis_id=hypothesis_id,
            no_trade_reason=reason,
        )


def _curve_has_executable_ask(curve: object) -> bool:
    """Return whether an executable cost curve exposes a real native ask.

    Duck-typed so a real ``ExecutableCostCurve`` and a test stub both work:
      - prefer an explicit ``has_executable_ask`` boolean/property if present;
      - else fall back to a non-None ``top_ask``;
      - else (no recognizable ask surface) treat as NOT executable (fail-closed),
        which downgrades the candidate to NATIVE_QUOTE_MISSING rather than
        risking a complement substitution.
    """
    flag = getattr(curve, "has_executable_ask", None)
    if isinstance(flag, bool):
        return flag
    top_ask = getattr(curve, "top_ask", None)
    if top_ask is not None:
        return True
    # Real ExecutableCostCurve (Phase 3, spec §14.3) peer surface: it has no
    # has_executable_ask/top_ask attribute — it guarantees executability via a
    # non-empty ``levels`` tuple (its constructor raises if levels is empty).
    # A non-empty levels ladder IS a real native ask ladder, so treat it as
    # executable; otherwise stay fail-closed -> NATIVE_QUOTE_MISSING.
    levels = getattr(curve, "levels", None)
    if levels:  # non-empty tuple/sequence
        return True
    return False
