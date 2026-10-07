# Created: 2026-04-27 (BATCH C of 2026-04-27 harness debate executor work)
# Last reused/audited: 2026-05-18
# Authority basis: docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
#   §1.1 #4 + §4.1 #4 (both proponent + opponent endorsed type-encoded HK HKO
#   antibody; opponent §3.1 has the template). Per Fitz Constraint #1 "make the
#   category impossible, not just the instance".
#
# 2026-05-18 (F3 PR 1/3): Migrated settle_market / SettlementRoundingPolicy
#   signatures to use CelsiusDecimal NewType. SettlementSemantics unit-polymorphic
#   methods (assert_settlement_value, round_single, round_values) stay as float —
#   they are dispatched at runtime by for_city() which carries measurement_unit;
#   static NewType cannot model the runtime-dispatched unit. The typed-unit gate
#   for those paths lands in PR 2/3 at the ingest boundary. See module docstring.

"""Settlement rounding contracts for Polymarket temperature markets.

Unit-type invariant (Fitz Constraint #1 — "make the category impossible"):
  - ``settle_market`` and ``SettlementRoundingPolicy.round_to_settlement``
    accept ``CelsiusDecimal`` (a NewType over ``Decimal``).  Passing a raw
    ``Decimal`` without wrapping via ``degC_d()`` fails mypy-strict at the
    call site.
  - ``Celsius + Fahrenheit`` addition is un-typecheckable: both are NewTypes
    over ``float``, so mypy sees the operands as incompatible (you need an
    explicit ``f_to_c`` / ``c_to_f`` conversion).
  - ``SettlementSemantics`` unit-polymorphic methods (``round_values``,
    ``round_single``, ``assert_settlement_value``) remain ``float``-typed
    because their unit is determined at runtime by the ``measurement_unit``
    field set by ``for_city()``.  The ingest-boundary typed gate is PR 2/3
    scope.

Scope limitation (Path A, accepted by operator 2026-05-18):
  NewType-only does NOT block ``Celsius + Fahrenheit`` arithmetic.  mypy
  treats NewTypes over ``float`` as ``float`` for operator dispatch, so
  ``c + f`` where ``c: Celsius`` and ``f: Fahrenheit`` returns ``float``
  without a type error.  Function SIGNATURE gates are the achievement here;
  full in-body arithmetic prevention requires frozen-dataclass wrappers and
  is deferred due to runtime cost in hot statistical loops.  See
  ``src/types/temperature.py`` LIMITATION comment for the full rationale.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Literal, Optional

import logging
import numpy as np

from src.architecture.decorators import capability
from src.contracts.exceptions import SettlementPrecisionError
from src.types.temperature import CelsiusDecimal

logger = logging.getLogger(__name__)

RoundingRule = Literal["wmo_half_up", "floor", "ceil", "oracle_truncate"]


def settlement_source_publication_grade(
    *, city: str, target_date: str, temperature_metric: str,
    market_slug: str | None, source_family: str | None = None,
    settlement_source: str | None = None, provenance: dict | None = None,
    qualification_at: str | None = None,
) -> dict | None:
    """Separate HKO source publication eligibility from integer rounding/payout.

    No supported original publication record or market-specific accepted
    correction format is available yet. Capture hashes, fetch clocks, first-seen
    flags and generic correction terms cannot authenticate the initial value.
    Retain such originals as evidence, but never mint a positive witness from
    them. Non-HKO sources keep their existing qualification law.
    """
    evidence = provenance or {}
    sources = (source_family, settlement_source, evidence.get("source_family"),
               evidence.get("settlement_source_type"), evidence.get("obs_source"))
    is_hko = city.replace(" ", "").lower() == "hongkong" or any(
        "hko" in str(source or "").lower() for source in sources
    )
    venue_claim = (evidence.get("claim_basis") == "venue_unique_integer_point_v1"
                   or settlement_source == "polymarket_gamma" and evidence.get("venue_point_witness") is not None)
    if not is_hko and not venue_claim:
        return None
    # SCOPE: this tuple's unproven decimal claim, not every HKO integer fact.
    # DRAIN/RESET: possessed native Gamma evidence becomes strictly resolved
    # with one YES point, independently proving its unique settlement integer.
    # Decimal publication eligibility stays a separate UNKNOWN fact.
    result = {
        "source_grade": "UNKNOWN",
        "reason": "hko_publication_witness_unavailable",
        "city": city, "target_date": target_date,
        "temperature_metric": temperature_metric, "market_slug": market_slug,
    }
    point = gamma_unique_point_truth(
        evidence.get("venue_point_witness"), city=city, target_date=target_date,
        temperature_metric=temperature_metric, market_slug=market_slug,
        qualification_at=qualification_at,
    )
    if point is not None:
        result.update(venue_integer_grade="VERIFIED", venue_point=point, reason=None)
    return result


def gamma_capture_identity(witness: dict) -> str:
    """Bind the retained entity and its capture clocks, never source-issued time."""
    fields = ("entity_sha256", "capture_started_at_utc", "capture_received_at_utc",
              "request_url", "request_params")
    return hashlib.sha256(json.dumps({key: witness.get(key) for key in fields},
                         sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def gamma_binary_outcome(market: dict) -> dict | None:
    """Strict native closed/resolved binary fact; 0/1 are payout facts only."""
    from src.contracts.settlement_outcome import classify_settlement_outcome, SettlementOutcome
    def items(value):
        return json.loads(value) if isinstance(value, str) else value
    try:
        if not isinstance(market, dict) or market.get("closed") is not True or classify_settlement_outcome(market) not in {
            SettlementOutcome.VENUE_RESOLVED_WIN, SettlementOutcome.VENUE_RESOLVED_LOSE,
        }:
            return None
        labels = items(market.get("outcomes"))
        prices = items(market.get("outcomePrices"))
        tokens = items(market.get("clobTokenIds"))
        if not all(isinstance(values, list) and len(values) == 2
                   for values in (labels, prices, tokens)):
            return None
        labels = [str(label).lower() for label in labels]
        if labels not in (["yes", "no"], ["no", "yes"]):
            return None
        if any(isinstance(value, bool) for value in prices) or [float(p) for p in prices] not in (
            [1.0, 0.0], [0.0, 1.0],
        ):
            return None
        if not all(isinstance(token, str) and token.strip() for token in tokens) or tokens[0] == tokens[1]:
            return None
        condition = market.get("conditionId")
        if not isinstance(condition, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", condition):
            return None
        yes = labels.index("yes")
        return {"condition_id": condition, "yes_token_id": tokens[yes],
                "yes_won": float(prices[yes]) == 1.0}
    except (ValueError, TypeError, KeyError):
        return None


def gamma_unique_point_truth(witness, *, city: str, target_date: str,
                             temperature_metric: str, market_slug: str | None,
                             qualification_at: str | None) -> dict | None:
    """Reproduce a venue integer from an original native entity, never weather floor.

    A hash/capture binds custody, not publication. Qualification is a separate
    actual clock no earlier than possession or the native entity's revision.
    Parsed objects and source-name/positive-grade flags authorize nothing.
    """
    from src.types.market import Bin
    from types import SimpleNamespace
    try:
        if not isinstance(witness, dict) or city != "Hong Kong" or temperature_metric not in {"high", "low"}:
            return None
        if witness.get("request_url") != "https://gamma-api.polymarket.com/events":
            return None
        if witness.get("capture_identity_sha256") != gamma_capture_identity(witness):
            return None
        raw = base64.b64decode(witness["entity_bytes_b64"], validate=True)
        if not raw or hashlib.sha256(raw).hexdigest() != witness["entity_sha256"]:
            return None
        clock = lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        started, received, qualified = map(clock, (witness["capture_started_at_utc"],
            witness["capture_received_at_utc"], qualification_at))
        if any(value.tzinfo is None for value in (started, received, qualified)) or not (
            started <= received <= qualified <= datetime.now(timezone.utc)
        ):
            return None
        target = datetime.fromisoformat(target_date).date()
        if target_date != target.isoformat():
            return None
        extreme = "highest" if temperature_metric == "high" else "lowest"
        expected_slug = f"{extreme}-temperature-in-hong-kong-on-{target.strftime('%B').lower()}-{target.day}-{target.year}"
        if market_slug != expected_slug:
            return None
        body = json.loads(raw)
        if not isinstance(body, list):
            return None
        events = [event for event in body if isinstance(event, dict) and event.get("slug") == market_slug]
        if len(events) != 1 or events[0].get("closed") is not True:
            return None
        event = events[0]
        if event.get("title") != f"{extreme.capitalize()} temperature in Hong Kong on {target.strftime('%B')} {target.day}?":
            return None
        updated = clock(event["updatedAt"])
        if updated.tzinfo is None or updated > received:
            return None
        markets = event.get("markets")
        if not isinstance(markets, list) or not markets:
            return None
        for market in markets:
            updated = clock(market["updatedAt"])
            if updated.tzinfo is None or updated > received:
                return None
        facts = [gamma_binary_outcome(market) for market in markets]
        if any(fact is None for fact in facts):
            return None
        if len({fact["condition_id"] for fact in facts}) != len(facts):
            return None
        winners = [(market, fact) for market, fact in zip(markets, facts) if fact["yes_won"]]
        if len(winners) != 1:
            return None
        winner, fact = winners[0]
        label = winner.get("groupItemTitle")
        match = re.fullmatch(r"(-?\d+)°C", str(label))
        if match is None:
            return None  # NO-only point, range and shoulder prove no scalar.
        value = float(match.group(1))
        if winner.get("question") != f"Will the {extreme} temperature in Hong Kong be {label} on {target.strftime('%B')} {target.day}?":
            return None
        bin_ = Bin(value, value, "C", str(label))
        if not bin_.is_point or bin_.settlement_values != [int(value)]:
            return None
        sem = SettlementSemantics.for_city(SimpleNamespace(settlement_source_type="hko", settlement_unit="C"))
        if sem.assert_settlement_value(value, context="gamma_unique_point_truth") != value:
            return None
        return {**fact, "settlement_value": value, "winning_bin": label,
                "unit": "C", "qualification_at": qualified.isoformat(),
                "outcomes": facts,
                "capture_identity_sha256": witness["capture_identity_sha256"]}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def gamma_point_outcomes_match(point: dict, outcomes) -> bool:
    """Only emit caller-requested payout rows independently reproduced in the entity."""
    if outcomes is None:
        return True
    try:
        native = {row["condition_id"]: row for row in point["outcomes"]}
        seen = set()
        for outcome in outcomes:
            row = outcome if isinstance(outcome, dict) else vars(outcome)
            condition = row.get("condition_id")
            if condition in seen or condition not in native or type(row.get("yes_won")) is not bool:
                return False
            seen.add(condition)
            if any(row.get(key) != native[condition][key] for key in ("yes_token_id", "yes_won")):
                return False
        return True
    except (TypeError, KeyError, AttributeError):
        return False


def expected_settlement_station_id(city: Any) -> str:
    """One station identity for both settlement writers; no airport substitution."""
    if city.settlement_source_type == "hko":
        return "HKO"
    return str(city.wu_station or "").strip().upper()


def settlement_station_matches_city(row_station: object, city: Any) -> bool:
    """Require the configured identity, preserving canonical station suffixes.

    Extracted from the execution harvester so the ingest writer cannot silently
    admit a different station through a nonexistent City.station_id attribute.
    """
    expected = expected_settlement_station_id(city)
    if not expected:
        return False
    station = str(row_station or "").strip().upper()
    return bool(station) and (station == expected or station.startswith(f"{expected}:"))


def settlement_preimage_offsets(
    rounding_rule: str, *, half_step: float = 0.5
) -> tuple[float, float]:
    """Return the (low_offset, high_offset) that expand a bin label to its rounding PREIMAGE.

    THE single declarative source of the per-city settlement PREIMAGE convention.
    Every q-integration / CDF consumer derives its integration bounds from THIS
    function so a city-specific rounding convention is declared ONCE and the
    "scattered preimage assumption" category cannot recur (Fitz Constraint #1 —
    make the wrong preimage unconstructable, not patched in N call sites).

    For a continuous predicted temperature x ~ N(μ, σ) and a bin whose integer
    label set is {a, …, b}, the bin probability is
        Φ((b + high_offset − μ)/σ) − Φ((a + low_offset − μ)/σ)
    where (low_offset, high_offset) is the preimage of the settlement rounding rule:

      - ``wmo_half_up`` (floor(x + 0.5) == t  ⟺  x ∈ [t − half_step, t + half_step)):
            (−half_step, +half_step)   ← SYMMETRIC; standard cities.
      - ``oracle_truncate`` / ``floor`` (floor(x) == t  ⟺  x ∈ [t, t + step)):
            (0.0, +2·half_step)        ← ASYMMETRIC; Hong Kong (HKO/UMA truncation).
            Settlement floors decimal °C ("28.7 hasn't reached 29 ⇒ 28"), so the
            preimage of label t is [t, t+1), NOT the symmetric [t−0.5, t+0.5).
            Using the symmetric WMO preimage for HK systematically shifts every
            HK bin's mass UPWARD by ~half a bin — the exact pollution this fixes.
      - ``ceil`` (ceil(x) == t  ⟺  x ∈ (t − step, t]):
            (−2·half_step, 0.0)        ← ASYMMETRIC, opposite direction.

    ``half_step`` is the rounding half-width = settlement_step_c / 2 (0.5 for the
    1°C / 1°F integer grids of all current Zeus markets). The full quantum is
    ``2 · half_step``; the asymmetric rules span the full quantum on one side.

    Authority: ensemble_signal.analytic_p_raw_vector_from_maxes §preimage
    derivation (the HK-aware Monte-Carlo-equivalent path); this function lifts
    that per-rule edge logic into the shared contract so the fused-Normal q path
    (replacement_forecast_materializer) consumes the SAME rule the bins declare.
    """
    if rounding_rule == "wmo_half_up":
        return (-half_step, half_step)
    if rounding_rule in ("floor", "oracle_truncate"):
        # floor(x) == t  ⟺  x ∈ [t, t + step); step = 2·half_step.
        return (0.0, 2.0 * half_step)
    if rounding_rule == "ceil":
        # ceil(x) == t  ⟺  x ∈ (t − step, t]; CDF is continuous so the open edge
        # is exact.
        return (-2.0 * half_step, 0.0)
    raise ValueError(
        f"settlement_preimage_offsets: unsupported rounding rule {rounding_rule!r}"
    )


def round_wmo_half_up_values(
    values: Any, precision: float = 1.0
) -> "np.ndarray[Any, np.dtype[Any]]":
    """Round values using WMO asymmetric half-up semantics.

    WU/NWS integer temperature displays follow WMO half-up on the number line:
    floor(x + 0.5). This differs from Python/NumPy banker's rounding and from
    half-away-from-zero for negative values.

    ``values`` is unit-polymorphic (°F for USA markets, °C for international);
    this is a pure numeric primitive — it does not interpret temperature units.
    """
    arr = np.asarray(values, dtype=float)
    inv = 1.0 / precision if precision > 0 else 1.0
    scaled = arr * inv
    return np.floor(scaled + 0.5) / inv


def round_wmo_half_up_value(value: float, precision: float = 1.0) -> float:
    """Round one value using WMO asymmetric half-up semantics."""
    return float(round_wmo_half_up_values([value], precision)[0])


def apply_settlement_rounding(
    values: Any,
    round_fn: Optional[Callable[[Any], "np.ndarray[Any, np.dtype[Any]]"]],
    precision: float = 1.0,
) -> "np.ndarray[Any, np.dtype[Any]]":
    """B081: shared settlement-rounding dispatch.

    Uses injected round_fn if provided (e.g., oracle_truncate for HKO),
    otherwise falls back to WMO asymmetric half-up: floor(x + 0.5).
    Result is float, not int - callers use >= / <= comparisons on Bin bounds.

    Consolidates duplicated logic previously in
    `src/strategy/market_analysis.py::MarketAnalysis._settle` and
    `src/signal/day0_signal.py::Day0Signal._settle`. Flagged YELLOW because
    a future unification with EnsembleSignal's SettlementSemantics-injected
    round_values() path should route through here too.
    """
    if round_fn is not None:
        return round_fn(values)
    return round_wmo_half_up_values(values, precision)


@dataclass(frozen=True)
class SettlementSemantics:
    """Every market's unique resolution rules. Drifts in rounding/precision are fatal errors.

    Replaces the global assumption of "WU integer rounding"
    with a typed, per-market object.
    """
    resolution_source: str  # e.g., "WU_LaGuardia", "CWA_Taipei"
    measurement_unit: Literal["F", "C"]
    precision: float        # 1.0 = whole degrees, 0.1 = one decimal
    rounding_rule: RoundingRule
    finalization_time: str  # "12:00:00Z"

    def round_values(
        self, values: Any
    ) -> "np.ndarray[Any, np.dtype[Any]]":
        """Apply settlement rounding according to this market contract.

        ``values`` is unit-polymorphic: the unit is determined by
        ``self.measurement_unit`` (set by ``for_city()``).  Typed-unit
        enforcement for this path is PR 2/3 scope.
        """
        arr = np.asarray(values, dtype=float)
        inv = 1.0 / self.precision if self.precision > 0 else 1.0
        scaled = arr * inv

        if self.rounding_rule == "wmo_half_up":
            rounded = np.floor(scaled + 0.5)
        elif self.rounding_rule in ("floor", "oracle_truncate"):
            # DANGER: oracle_truncate 仅限 HKO 等受到 UMA 截断偏见污染
            # 的合约使用！严禁用于正常的气象学 P_raw 模拟！
            #
            # UMA voters treat decimal °C as truncated: "28.7 hasn't
            # reached 29, so it's 28". Empirically verified: floor()
            # achieves 14/14 (100%) match on HKO same-source settlement
            # days vs 5/14 (36%) with wmo_half_up.
            rounded = np.floor(scaled)
        elif self.rounding_rule == "ceil":
            rounded = np.ceil(scaled)
        else:
            raise ValueError(f"Unsupported settlement rounding rule: {self.rounding_rule}")

        return rounded / inv

    def round_single(self, value: float) -> float:
        """Round a single settlement value to contract precision.

        This is the MANDATORY gate for all settlement DB writes.
        No code path may store a settlement_value without calling this first.
        """
        return float(self.round_values([value])[0])

    @capability("settlement_write")
    def assert_settlement_value(self, value: float, *, context: str = "") -> float:
        """Validate and round a settlement value. Returns the rounded value.

        Raises SettlementPrecisionError if the raw value is NaN or infinite.
        Always rounds to contract precision (integer for all current markets).

        Usage at every DB write boundary:
            sem = SettlementSemantics.for_city(city)
            settlement_value = sem.assert_settlement_value(raw_temp, context="wu_daily_collector")
        """
        if not np.isfinite(value):
            raise SettlementPrecisionError(
                f"Settlement value is not finite: {value}. "
                f"Contract: {self.resolution_source}, unit={self.measurement_unit}. "
                f"Context: {context}"
            )
        rounded = self.round_single(value)
        delta = abs(value - rounded)
        if delta > 1e-9:
            logger.debug(
                "Settlement value %.1f rounded to %.0f (delta=%.1f) [%s] %s",
                value, rounded, delta, self.resolution_source, context,
            )
        return rounded

    @classmethod
    def default_wu_fahrenheit(cls, city_code: str) -> "SettlementSemantics":
        """Polymarket USA city contracts: WU integer °F with WMO half-up rounding."""
        return cls(
            resolution_source=f"WU_{city_code}",
            measurement_unit="F",
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z"
        )

    @classmethod
    def default_wu_celsius(cls, city_code: str) -> "SettlementSemantics":
        """Polymarket international city contracts: WU integer °C.

        Polymarket °C markets use 1°C point bins (e.g., "4°C", "5°C").
        Settlement rounds to integer °C, same WMO half-up rounding rule as °F.
        """
        return cls(
            resolution_source=f"WU_{city_code}",
            measurement_unit="C",
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z"
        )

    @classmethod
    def for_city(cls, city: Any) -> "SettlementSemantics":
        """Construct appropriate SettlementSemantics from a City object.

        This is the single entry point. Do NOT call default_wu_fahrenheit
        for °C cities.
        """
        source_type = city.settlement_source_type
        if source_type == "wu_icao":
            # WU-based settlement (default path)
            if city.settlement_unit == "C":
                return cls.default_wu_celsius(city.wu_station)
            return cls.default_wu_fahrenheit(city.wu_station)

        # Non-WU settlement sources
        if source_type == "hko":
            # DANGER: oracle_truncate 仅限 HKO！严禁用于其他城市！
            # HKO reports 0.1°C precision. UMA voters apply truncation
            # ("28.7 → 28"), not WMO half-up rounding ("28.7 → 29").
            # Verified: floor() achieves 14/14 (100%) match on HKO
            # same-source days vs 5/14 (36%) with wmo_half_up.
            return cls(
                resolution_source="HKO_HQ",
                measurement_unit="C",
                precision=1.0,
                rounding_rule="oracle_truncate",
                finalization_time="12:00:00Z",
            )

        # CWA, NOAA, etc. — default to WMO half-up
        return cls(
            resolution_source=f"{source_type}_{city.wu_station}",
            measurement_unit=city.settlement_unit,
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z",
        )


# ---------------------------------------------------------------------------
# Type-encoded settlement-rounding policy (appended 2026-04-27 BATCH C)
#
# This block APPENDS a parallel type-encoded settlement-rounding policy. It does
# NOT replace the existing SettlementSemantics.round_values() string-dispatch
# path; that migration is Tier 3 P8 territory. New code paths SHOULD use this
# policy ABC; existing callers continue working unchanged.
# ---------------------------------------------------------------------------

class SettlementRoundingPolicy(ABC):
    """Type-encoded settlement-rounding policy. Replaces YAML antibody for
    HK HKO truncation vs WMO half-up cross-city mixing with a TypeError at
    the call site (per Fitz Constraint #1: make the category impossible).

    Subclasses MUST set the ClassVar `name` to a stable string identifier and
    implement `round_to_settlement` + `source_authority`. Mixing policies
    across incompatible markets raises TypeError in `settle_market`.

    ``round_to_settlement`` accepts ``CelsiusDecimal`` (NewType over Decimal)
    so that passing a plain ``Decimal`` fails mypy-strict without an explicit
    ``degC_d()`` wrapping call.
    """
    name: ClassVar[str]

    @abstractmethod
    def round_to_settlement(self, raw_temp_c: CelsiusDecimal) -> int:
        """Round a raw Celsius temperature to the integer settlement value."""

    @abstractmethod
    def source_authority(self) -> str:
        """Return the authority string for this policy (e.g., 'WMO', 'HKO')."""


class WMO_HalfUp(SettlementRoundingPolicy):
    """WMO asymmetric half-up: 74.45 → 74; 74.50 → 75. WU/NOAA/CWA chains.

    Negative half-values round toward +∞ (asymmetric): -3.5 → -3, NOT -4.
    Matches legacy round_wmo_half_up_value at line 16. Differs from Python's
    Decimal ROUND_HALF_UP (which is half-away-from-zero, -3.5 → -4) and from
    Python/NumPy banker's rounding (which is half-to-even).

    Per WMO No. 306 METAR convention + the file-level docstring at line 19
    ("WMO half-up on the number line: floor(x + 0.5)") + the repo doc warning
    at docs/reference/modules/contracts.md:89 ("wrong negative-half handling
    yields systematic settlement drift"). Critic batch_C_review §C4 caught a
    silent divergence in the original Decimal ROUND_HALF_UP version: SIDECAR-3
    fix replaces it with np.floor(float(x) + 0.5) to match legacy byte-for-byte.
    """
    name: ClassVar[str] = "wmo_half_up"

    def round_to_settlement(self, raw_temp_c: CelsiusDecimal) -> int:
        # Asymmetric half-up "on the number line" (toward +∞), matching legacy
        # round_wmo_half_up_values + WMO No. 306 METAR convention. -3.5 → -3
        # (NOT -4). See settlement_semantics.py:16-27 docstring + docs/reference/
        # modules/contracts.md:89 warning + critic batch_C_review §C4.
        return int(np.floor(float(raw_temp_c) + 0.5))

    def source_authority(self) -> str:
        return "WMO"


class HKO_Truncation(SettlementRoundingPolicy):
    """HKO truncation: 74.99 → 74. Hong Kong settlement chain ONLY.

    UMA voters treat decimal °C as truncated ('28.7 hasn't reached 29, so it's
    28'). Empirically verified: floor() achieves 14/14 (100%) match on HKO
    same-source settlement days vs 5/14 (36%) with WMO half-up.
    """
    name: ClassVar[str] = "hko_truncation"

    def round_to_settlement(self, raw_temp_c: CelsiusDecimal) -> int:
        return int(Decimal(raw_temp_c).quantize(Decimal('1'), rounding=ROUND_DOWN))

    def source_authority(self) -> str:
        return "HKO"


@capability("settlement_rebuild")
def settle_market(
    city_name: str,
    raw_temp_c: CelsiusDecimal,
    policy: SettlementRoundingPolicy,
) -> int:
    """Apply the rounding policy to raw °C, with type-encoded city/policy match.

    ``raw_temp_c`` must be ``CelsiusDecimal`` (wrap via ``degC_d()`` from
    ``src.types.temperature``).  Passing a plain ``Decimal`` fails mypy-strict.

    HK markets REQUIRE HKO_Truncation; non-HK markets REQUIRE non-HKO policy.
    Mismatch raises TypeError BEFORE any rounding happens — i.e., the wrong
    rounding for the wrong city is structurally unconstructable. Per Fitz
    Constraint #1 (make the category impossible).

    -------------------------------------------------------------------------
    PRODUCTION-CALLER STATUS (audited 2026-05-01, ultrareview25_remediation P0-5):

      `settle_market` has ZERO call sites in `src/`. It is the type-encoded
      FUTURE migration target ("Tier 3 P8 territory" per the line-194 author
      note above). The cross-city wrong-rounding failure mode it guards
      against is ALREADY structurally impossible in production today — but
      via the SOCIAL gate, not the TYPE gate:

        SettlementSemantics.for_city(city)
          ├── settlement_source_type == 'hko' → rounding_rule='oracle_truncate'
          └── otherwise                       → rounding_rule='wmo_half_up'

      `tests/test_settlement_semantics.py::test_settlement_semantics_
      construction_routes_through_for_city` LOCKS the SOCIAL discipline:
      no `src/` file outside this module may construct SettlementSemantics
      directly or pass `rounding_rule='...'` as a string-literal kwarg.
      Together with `for_city()`'s dispatch table, that makes WMO-for-HK
      structurally unconstructable today.

      Until the Tier 3 P8 migration wires `settle_market` into
      `assert_settlement_value`, prefer the type-encoded path for NEW code,
      and keep the SOCIAL gate routing for EXISTING call sites. Activating
      the type gate everywhere would touch every settlement DB write and is
      not justified by the marginal additional protection over the SOCIAL
      gate the routing test now enforces.

      `architecture/fatal_misreads.yaml:141`'s claim that this antibody
      makes the misread "unconstructable at compile/import time" is
      ACCURATE for callers that use settle_market() directly, and
      OVERSTATED for the (entirety of) production code that goes through
      `for_city()` — that path is type-checked at runtime via the dispatch
      table, not at compile time. See SYNTHESIS.md P0-5 reclassification
      for the proposal to amend the YAML wording.
    -------------------------------------------------------------------------
    """
    if not isinstance(policy, SettlementRoundingPolicy):
        raise TypeError(
            f"settle_market requires a SettlementRoundingPolicy instance; "
            f"got {type(policy).__name__}"
        )
    if city_name == "Hong Kong" and not isinstance(policy, HKO_Truncation):
        raise TypeError(
            f"Hong Kong markets require HKO_Truncation policy; "
            f"got {type(policy).__name__}"
        )
    if city_name != "Hong Kong" and isinstance(policy, HKO_Truncation):
        raise TypeError(
            f"HKO_Truncation policy is valid for Hong Kong only; "
            f"got city={city_name!r}"
        )
    return policy.round_to_settlement(raw_temp_c)
