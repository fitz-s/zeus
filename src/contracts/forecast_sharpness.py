# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K1 — forecast-sharpness contract (structural fix plan §3 P2.2).
#   Root cause K1: "no forecast-sharpness contract -> flat q -> 96% buy_no". A city whose
#   ensemble has no skill (settlement MAE much wider than a market bin) should emit NO
#   edge — a flat forecast spreads probability so thin that the cheap-tail buy_no clears
#   the CI bar almost everywhere. This module is the ANTIBODY: a typed, required evidence
#   object whose presence is enforced at MarketAnalysis construction (TypeError on omission)
#   and whose verdict (suppresses_edges) is consulted at the ONE gate site.

"""ForecastSharpnessEvidence: settlement-grounded forecast-resolution contract.

The evidence relates two quantities that previously never met:
  - settlement MAE: mean |forecast - realized_settlement| in the NATIVE settlement
    unit, aggregated from ``forecast_skill`` (whose ``error`` column is exactly
    ``forecast_temp - actual_temp`` and whose ``actual_temp`` is the realized
    settlement temperature — i.e. this MAE IS the settlement MAE).
  - bin width: the integer settlement span of a market bin (1 for °C points,
    2 for °F ranges).

The gate verdict is::

    suppresses_edges  <=>  mae >= N_SIGMA * bin_width   (in the native unit)

with N_SIGMA an operator-tunable multiplier (default 1.5).

Provenance / fail-closed rules (data-provenance law):
  - A MISSING ``forecast_skill`` row -> ``evidence_present=False`` -> suppresses
    (a city we cannot measure cannot be trusted to emit edges).
  - ``day0_exempt`` bypasses the gate entirely: on day0/imminent paths the
    realized observation replaces the forecast, so forecast sharpness is moot.

The BEHAVIOR (edge rejection) is flag-gated OFF at the call site
(``edli_v1.forecast_sharpness_gate_enabled``); this TYPE is always required so a
construction without it is a TypeError regardless of the flag — the error
category (forgetting to relate sharpness to the bin) is unconstructable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastSharpnessEvidence:
    """Immutable settlement-grounded sharpness evidence for one (city, unit, lead).

    Fields:
      mae:             settlement MAE in the native unit, or ``None`` when the
                       evidence is absent or exempt.
      bin_width:       integer settlement span of a market bin in the native unit
                       (1.0 for °C points, 2.0 for °F ranges).
      unit:            'C' or 'F' — re-asserted against the analysis unit at the
                       MarketAnalysis seam (B6 ETL-contamination block).
      lead_days:       capped lead bucket the MAE was aggregated over (0..7).
      n_paired:        number of (forecast, settlement) pairs behind the MAE.
      source:          provenance tag ('forecast_skill', 'test', 'exempt', 'missing').
      day0_exempt:     when True, the gate never suppresses (obs replaces forecast).
      evidence_present: False => fail-closed (suppresses regardless of the multiplier).
    """

    mae: float | None
    bin_width: float
    unit: str
    lead_days: int
    n_paired: int
    source: str
    day0_exempt: bool
    evidence_present: bool

    def __post_init__(self) -> None:
        if self.unit not in {"F", "C"}:
            raise ValueError(
                f"ForecastSharpnessEvidence.unit must be 'F' or 'C', got {self.unit!r}"
            )
        if self.bin_width <= 0:
            raise ValueError(
                f"ForecastSharpnessEvidence.bin_width must be > 0, got {self.bin_width!r}"
            )
        if self.evidence_present and not self.day0_exempt:
            if self.mae is None:
                raise ValueError(
                    "ForecastSharpnessEvidence with evidence_present=True must carry a MAE"
                )
            if self.mae < 0:
                raise ValueError(
                    f"ForecastSharpnessEvidence.mae must be >= 0, got {self.mae!r}"
                )

    # ------------------------------------------------------------------ factories

    @classmethod
    def exempt(cls, *, unit: str) -> "ForecastSharpnessEvidence":
        """Day0/imminent evidence: the gate never suppresses (obs replaces forecast)."""
        return cls(
            mae=None,
            bin_width=(2.0 if unit == "F" else 1.0),
            unit=unit,
            lead_days=0,
            n_paired=0,
            source="exempt",
            day0_exempt=True,
            evidence_present=True,
        )

    @classmethod
    def missing(
        cls, *, unit: str, bin_width: float, lead_days: int
    ) -> "ForecastSharpnessEvidence":
        """Fail-closed evidence for a city with NO measurable forecast skill row."""
        return cls(
            mae=None,
            bin_width=float(bin_width),
            unit=unit,
            lead_days=int(lead_days),
            n_paired=0,
            source="missing",
            day0_exempt=False,
            evidence_present=False,
        )

    @classmethod
    def from_mae(
        cls,
        *,
        mae: float,
        bin_width: float,
        unit: str,
        lead_days: int,
        n_paired: int,
        source: str = "forecast_skill",
    ) -> "ForecastSharpnessEvidence":
        return cls(
            mae=float(mae),
            bin_width=float(bin_width),
            unit=unit,
            lead_days=int(lead_days),
            n_paired=int(n_paired),
            source=source,
            day0_exempt=False,
            evidence_present=True,
        )

    @classmethod
    def load_for(
        cls,
        conn: sqlite3.Connection,
        *,
        city: str,
        unit: str,
        lead_days: float,
        bin_width: float,
    ) -> "ForecastSharpnessEvidence":
        """Aggregate settlement MAE from ``forecast_skill`` for one (city, unit, lead).

        Key = (city, temp_unit, int(min(lead_days, 7))). ``forecast_skill.error``
        is ``forecast_temp - actual_temp`` where ``actual_temp`` is the realized
        settlement temperature, so ``AVG(ABS(error))`` IS the settlement MAE.

        A row-count of 0 for the key -> fail-closed ``missing`` evidence.
        """
        lead_bucket = int(min(max(float(lead_days), 0.0), 7.0))
        row = conn.execute(
            """
            SELECT AVG(ABS(error)) AS mae, COUNT(*) AS n
            FROM forecast_skill
            WHERE city = ? AND temp_unit = ? AND CAST(MIN(lead_days, 7) AS INTEGER) = ?
            """,
            (city, unit, lead_bucket),
        ).fetchone()
        mae = None if row is None else row[0]
        n = 0 if row is None else int(row[1] or 0)
        if mae is None or n <= 0:
            return cls.missing(unit=unit, bin_width=bin_width, lead_days=lead_bucket)
        return cls.from_mae(
            mae=float(mae),
            bin_width=bin_width,
            unit=unit,
            lead_days=lead_bucket,
            n_paired=n,
            source="forecast_skill",
        )

    # ------------------------------------------------------------------ verdict

    def suppresses_edges(self, *, multiplier: float) -> bool:
        """Return True iff this city's forecast is too flat to emit a meaningful edge.

        - day0_exempt -> never suppresses.
        - evidence absent -> ALWAYS suppresses (fail-closed provenance).
        - otherwise -> suppress when ``mae >= multiplier * bin_width`` (native unit).
        """
        if self.day0_exempt:
            return False
        if not self.evidence_present or self.mae is None:
            return True
        return float(self.mae) >= float(multiplier) * float(self.bin_width)
