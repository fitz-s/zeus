# Created: 2026-06-09
# Last reused or audited: 2026-06-17
# Authority basis: Operator mission 2026-06-09 — the ONE standing shadow-vs-live
#   comparator (K<<N: one comparator, N promotions). Historical immediate customer:
#   day0_remaining_day_q_enabled. That Day0 path is now live under
#   forecast_plus_day0; this module is offline evidence only and does not gate
#   live submit authority. Past customers each rolled their own ad-hoc harness
#   (EMOS sole-calibrator shadow-prove, bias-correction promotion, fused-q-shape
#   promotion, opening_inertia_relaxation cohort). This module replaces all of
#   them with ONE reader.
#
#   ONE-BUILDER LAW: the comparator READS persisted shadow + live q values; it
#   NEVER recomputes domain logic (no forecasting, no q construction, no
#   bin-derivation math). Shadow values MUST already be persisted by the
#   candidate's own lane. Settlement truth comes ONLY through grade_receipt
#   (src.contracts.graded_receipt) — never a value-equality / startswith join.
#   Win/loss = grade_receipt's Direction Law (buy_yes WIN iff settled_bin==
#   traded_bin; buy_no WIN iff !=). Scoring reuses src.calibration.scoring
#   (log_score / brier_score) — no new proper-scoring math.
"""shadow_comparator — the one standing shadow-vs-live promotion comparator.

A shadow CANDIDATE is defined by FOUR things and nothing more:
  1. a ``name`` (the promotion under evaluation, e.g. ``day0_remaining_day_q``);
  2. a ``cohort key`` per cell — ``(city, metric, target_date[, bin_label])``;
  3. the SHADOW q it produces (the value the promotion WOULD trade);
  4. the LIVE q it contrasts (the value being traded today).

Both q values are the *direction-adjusted posterior* — P(the traded outcome
WINS) for that (bin, direction) — exactly the ``q_live`` field a receipt
already persists. For each SETTLED cohort cell (joined to a VERIFIED
``settlement_outcomes`` row and graded through ``grade_receipt``), the
comparator scores BOTH sides against the binary settled ``won`` with a
Bernoulli proper score (log-loss + Brier), collects the paired per-cell
difference, and emits a verdict:

  PROMOTE_SUPPORTED  — shadow strictly better AND the paired-difference CI
                       excludes 0 (bootstrap) AND the sign test agrees;
  LIVE_BETTER        — live strictly better with CI excluding 0;
  INSUFFICIENT_N     — fewer than ``min_n`` settled paired cells, OR the CI
                       straddles 0 (no decision licensed).

HONEST ABSENCE: if a candidate's own lane has NOT persisted the shadow q for a
cohort cell, that cell is DROPPED (counted as ``missing_shadow``) — never
fabricated. A candidate with zero paired cells returns INSUFFICIENT_N with
``missing_shadow`` surfaced, so "the shadow lane is not persisting yet" is a
visible, honest verdict rather than a silent empty pass.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from src.calibration.scoring import LOG_LOSS_EPS, brier_score, log_score

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Verdict thresholds
# --------------------------------------------------------------------------- #

# Minimum settled paired cells before any PROMOTE/LIVE_BETTER verdict is
# licensed. Below this the comparator returns INSUFFICIENT_N and never decides.
DEFAULT_MIN_N = 30

# Bootstrap resample count for the paired-difference CI.
DEFAULT_BOOTSTRAP_N = 2000

# Two-sided CI level for the paired-difference bootstrap.
DEFAULT_CI_ALPHA = 0.05

# Probability clamp so a q of exactly 0 or 1 yields a finite Bernoulli log-loss
# rather than +inf. Reuses the same eps the multinomial scorer uses.
_Q_EPS = LOG_LOSS_EPS


# --------------------------------------------------------------------------- #
# Per-cell scored observation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairedCell:
    """One settled cohort cell with BOTH q sides scored against the outcome.

    ``won`` is graded through ``grade_receipt`` (the Direction Law lives there).
    ``shadow_q`` and ``live_q`` are each P(traded outcome WINS) — the
    direction-adjusted posterior. The Bernoulli log-loss / Brier of each side
    against ``won`` are precomputed so the verdict layer is pure arithmetic.
    """

    cohort_key: tuple
    won: bool
    shadow_q: float
    live_q: float
    shadow_logloss: float
    live_logloss: float
    shadow_brier: float
    live_brier: float

    @property
    def logloss_diff(self) -> float:
        """shadow − live log-loss. NEGATIVE = shadow better (lower loss)."""
        return self.shadow_logloss - self.live_logloss

    @property
    def brier_diff(self) -> float:
        """shadow − live Brier. NEGATIVE = shadow better (lower loss)."""
        return self.shadow_brier - self.live_brier


@dataclass(frozen=True)
class CohortObservation:
    """A raw (shadow_q, live_q) pair for one cohort cell, pre-settlement-join.

    Adapters yield these. ``settlement`` join + grading happens in the scorer,
    NOT in the adapter — the adapter only knows what its own lane persisted.
    ``shadow_q`` / ``live_q`` are direction-adjusted posteriors (P(win)).
    """

    city: str
    metric: str
    target_date: str
    bin_label: str
    direction: str
    shadow_q: Optional[float]
    live_q: Optional[float]


# --------------------------------------------------------------------------- #
# Bernoulli proper scores (reuse the multinomial scorer via a 2-class vector)
# --------------------------------------------------------------------------- #


def _bernoulli_scores(q_win: float, won: bool) -> tuple[float, float]:
    """Log-loss + Brier of P(win)=q_win against the binary settled outcome.

    Expressed as the 2-class categorical [q_win, 1-q_win] with the winner index
    decided by ``won`` so the canonical multinomial ``log_score`` / ``brier_score``
    do the arithmetic — no second proper-scoring implementation.
    """
    q = min(1.0 - _Q_EPS, max(_Q_EPS, float(q_win)))
    vector = [q, 1.0 - q]
    winner = 0 if won else 1  # index 0 == "traded outcome won"
    return log_score(vector, winner), brier_score(vector, winner)


# --------------------------------------------------------------------------- #
# Settlement join (VERIFIED only) + grading via the spine
# --------------------------------------------------------------------------- #


def _load_verified_settlements(world_conn: sqlite3.Connection) -> dict[tuple, dict]:
    """Load VERIFIED settlements keyed by (city, target_date, metric).

    Reads ``forecasts.settlement_outcomes`` (ATTACHed) restricted to
    ``authority='VERIFIED'`` — UNVERIFIED / QUARANTINED never enter the
    comparison chain (data-provenance law). Mirrors the attribution loader so
    there is one settlement-read shape, not two.
    """
    settlements: dict[tuple, dict] = {}
    for row in world_conn.execute(
        """
        SELECT city, target_date, temperature_metric,
               settlement_value, settlement_unit
        FROM forecasts.settlement_outcomes
        WHERE authority = 'VERIFIED'
        """
    ).fetchall():
        city, tdate, metric, value, unit = row
        if value is None or unit is None:
            continue
        settlements.setdefault(
            (str(city), str(tdate), str(metric)),
            {"settlement_value": float(value), "settlement_unit": str(unit)},
        )
    return settlements


def _grade_observation(obs: CohortObservation, settlement: dict) -> Optional[bool]:
    """Grade one cohort cell through the spine. Returns ``won`` or None.

    None when the bin label cannot be parsed, the direction is unknown, or the
    bin unit mismatches the settlement unit (grade_receipt's UnitMismatchError
    is the structural guard — a °F receipt against a °C settlement is refused,
    not silently mis-scored). Reuses ``_bin_from_label`` so bin parsing matches
    production exactly (one bin-derivation path).
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.cron.settlement_attribution import _bin_from_label
    from src.types.temperature import UnitMismatchError

    bin_obj = _bin_from_label(obs.bin_label, settlement["settlement_unit"])
    if bin_obj is None:
        return None

    class _S:  # minimal settlement stand-in for grade_receipt
        settlement_value = settlement["settlement_value"]
        settlement_unit = settlement["settlement_unit"]

    try:
        graded = grade_receipt(bin_obj, obs.direction, _S())
    except UnitMismatchError:
        logger.warning(
            "shadow_comparator: unit mismatch city=%s bin=%s — cell skipped",
            obs.city, obs.bin_label,
        )
        return None
    except ValueError:
        return None  # unknown direction — skip, never crash
    return graded.won


def pair_settled_cells(
    observations: Iterable[CohortObservation],
    world_conn: sqlite3.Connection,
    *,
    include_bin_in_key: bool = True,
) -> tuple[list[PairedCell], dict[str, int]]:
    """Join raw observations to VERIFIED settlements and score both q sides.

    Returns ``(paired_cells, counters)`` where ``counters`` carries honest
    drop reasons: ``missing_shadow`` (the candidate lane never persisted the
    shadow q), ``missing_live``, ``no_settlement`` (unsettled / unverified),
    ``ungradeable`` (bin/unit/direction refused by the spine). A cell enters
    ``paired_cells`` ONLY when BOTH q sides exist AND the cell is VERIFIED-
    settled AND gradeable.
    """
    settlements = _load_verified_settlements(world_conn)
    paired: list[PairedCell] = []
    counters = {
        "total": 0,
        "missing_shadow": 0,
        "missing_live": 0,
        "no_settlement": 0,
        "ungradeable": 0,
        "paired": 0,
    }
    for obs in observations:
        counters["total"] += 1
        if obs.shadow_q is None:
            counters["missing_shadow"] += 1
            continue
        if obs.live_q is None:
            counters["missing_live"] += 1
            continue
        settlement = settlements.get((obs.city, obs.target_date, obs.metric))
        if settlement is None:
            counters["no_settlement"] += 1
            continue
        won = _grade_observation(obs, settlement)
        if won is None:
            counters["ungradeable"] += 1
            continue
        s_ll, s_br = _bernoulli_scores(obs.shadow_q, won)
        l_ll, l_br = _bernoulli_scores(obs.live_q, won)
        key: tuple
        if include_bin_in_key:
            key = (obs.city, obs.metric, obs.target_date, obs.bin_label, obs.direction)
        else:
            key = (obs.city, obs.metric, obs.target_date, obs.direction)
        paired.append(
            PairedCell(
                cohort_key=key,
                won=won,
                shadow_q=float(obs.shadow_q),
                live_q=float(obs.live_q),
                shadow_logloss=s_ll,
                live_logloss=l_ll,
                shadow_brier=s_br,
                live_brier=l_br,
            )
        )
        counters["paired"] += 1
    return paired, counters


# --------------------------------------------------------------------------- #
# Paired statistics: sign test + bootstrap CI
# --------------------------------------------------------------------------- #


def _sign_test_p_value(diffs: Sequence[float]) -> tuple[int, int, int, float]:
    """Two-sided sign test on paired differences.

    Returns (n_shadow_better, n_live_better, n_tie, p_value). A NEGATIVE diff
    means shadow had lower loss (shadow better). Ties (exact 0) are excluded
    from the binomial, per the standard sign-test convention. The p-value is
    the exact two-sided binomial probability under H0: P(better)=0.5.
    """
    from scipy.stats import binomtest

    n_shadow = sum(1 for d in diffs if d < 0.0)
    n_live = sum(1 for d in diffs if d > 0.0)
    n_tie = sum(1 for d in diffs if d == 0.0)
    n_eff = n_shadow + n_live
    if n_eff == 0:
        return n_shadow, n_live, n_tie, 1.0
    p = binomtest(n_shadow, n_eff, 0.5, alternative="two-sided").pvalue
    return n_shadow, n_live, n_tie, float(p)


def _bootstrap_mean_ci(
    diffs: Sequence[float],
    *,
    n_boot: int = DEFAULT_BOOTSTRAP_N,
    alpha: float = DEFAULT_CI_ALPHA,
    seed: int = 12345,
) -> tuple[float, float, float]:
    """Percentile bootstrap CI for the MEAN paired difference.

    Returns (mean_diff, ci_lo, ci_hi). NEGATIVE mean_diff = shadow lower loss.
    Deterministic (fixed seed) so the verdict is reproducible across runs over
    the same settled record. Degenerate (n<2) returns (mean, mean, mean).
    """
    import numpy as np

    arr = np.asarray(list(diffs), dtype=float)
    n = arr.size
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = float(arr.mean())
    if n < 2:
        return mean, mean, mean
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boot_means = arr[idx].mean(axis=1)
    lo = float(np.percentile(boot_means, 100.0 * (alpha / 2.0)))
    hi = float(np.percentile(boot_means, 100.0 * (1.0 - alpha / 2.0)))
    return mean, lo, hi


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #

Verdict = str  # "PROMOTE_SUPPORTED" | "LIVE_BETTER" | "INSUFFICIENT_N"


@dataclass(frozen=True)
class CandidateScoreboard:
    """The running scoreboard for one shadow candidate."""

    name: str
    n_settled: int
    counters: dict
    shadow_logloss_mean: Optional[float]
    live_logloss_mean: Optional[float]
    shadow_brier_mean: Optional[float]
    live_brier_mean: Optional[float]
    logloss_diff_mean: Optional[float]
    logloss_diff_ci_lo: Optional[float]
    logloss_diff_ci_hi: Optional[float]
    sign_shadow_better: int
    sign_live_better: int
    sign_tie: int
    sign_p_value: Optional[float]
    verdict: Verdict
    verdict_line: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n_settled": self.n_settled,
            "counters": self.counters,
            "shadow_logloss_mean": self.shadow_logloss_mean,
            "live_logloss_mean": self.live_logloss_mean,
            "shadow_brier_mean": self.shadow_brier_mean,
            "live_brier_mean": self.live_brier_mean,
            "logloss_diff_mean": self.logloss_diff_mean,
            "logloss_diff_ci_lo": self.logloss_diff_ci_lo,
            "logloss_diff_ci_hi": self.logloss_diff_ci_hi,
            "sign_shadow_better": self.sign_shadow_better,
            "sign_live_better": self.sign_live_better,
            "sign_tie": self.sign_tie,
            "sign_p_value": self.sign_p_value,
            "verdict": self.verdict,
            "verdict_line": self.verdict_line,
        }


def score_candidate(
    name: str,
    paired: Sequence[PairedCell],
    counters: dict,
    *,
    min_n: int = DEFAULT_MIN_N,
    n_boot: int = DEFAULT_BOOTSTRAP_N,
    ci_alpha: float = DEFAULT_CI_ALPHA,
) -> CandidateScoreboard:
    """Produce the verdict for one candidate from its paired settled cells.

    Verdict rules (log-loss is the primary axis; NEGATIVE diff = shadow better):
      - n < min_n                       -> INSUFFICIENT_N
      - CI excludes 0 AND mean < 0      -> PROMOTE_SUPPORTED (shadow better)
      - CI excludes 0 AND mean > 0      -> LIVE_BETTER
      - CI straddles 0                  -> INSUFFICIENT_N (no decision licensed)
    The sign test is reported as corroborating evidence; the bootstrap CI on the
    paired mean is the gate (the sign test alone over-rejects on heavy-tailed
    log-loss differences).
    """
    n = len(paired)
    if n == 0:
        return CandidateScoreboard(
            name=name, n_settled=0, counters=counters,
            shadow_logloss_mean=None, live_logloss_mean=None,
            shadow_brier_mean=None, live_brier_mean=None,
            logloss_diff_mean=None, logloss_diff_ci_lo=None, logloss_diff_ci_hi=None,
            sign_shadow_better=0, sign_live_better=0, sign_tie=0, sign_p_value=None,
            verdict="INSUFFICIENT_N",
            verdict_line=(
                f"INSUFFICIENT_N: 0 settled paired cells "
                f"(missing_shadow={counters.get('missing_shadow', 0)}, "
                f"missing_live={counters.get('missing_live', 0)}, "
                f"no_settlement={counters.get('no_settlement', 0)}, "
                f"ungradeable={counters.get('ungradeable', 0)}). "
                f"Shadow lane not persisting comparable q — honest absence, no fabrication."
            ),
        )

    ll_diffs = [c.logloss_diff for c in paired]
    shadow_ll = sum(c.shadow_logloss for c in paired) / n
    live_ll = sum(c.live_logloss for c in paired) / n
    shadow_br = sum(c.shadow_brier for c in paired) / n
    live_br = sum(c.live_brier for c in paired) / n
    mean_diff, ci_lo, ci_hi = _bootstrap_mean_ci(ll_diffs, n_boot=n_boot, alpha=ci_alpha)
    n_sb, n_lb, n_tie, sign_p = _sign_test_p_value(ll_diffs)

    ci_excludes_zero = (ci_lo > 0.0) or (ci_hi < 0.0)
    if n < min_n:
        verdict = "INSUFFICIENT_N"
        line = (
            f"INSUFFICIENT_N: n={n} < min_n={min_n}. "
            f"shadow log-loss={shadow_ll:.4f} vs live={live_ll:.4f} "
            f"(Δ={mean_diff:+.4f}, CI[{ci_lo:+.4f},{ci_hi:+.4f}])."
        )
    elif ci_excludes_zero and mean_diff < 0.0:
        verdict = "PROMOTE_SUPPORTED"
        line = (
            f"PROMOTE_SUPPORTED: shadow lower log-loss {shadow_ll:.4f} vs live {live_ll:.4f} "
            f"(Δ={mean_diff:+.4f}, 95% CI[{ci_lo:+.4f},{ci_hi:+.4f}] excludes 0; "
            f"sign {n_sb}/{n_sb + n_lb} shadow-better, p={sign_p:.4g}; n={n})."
        )
    elif ci_excludes_zero and mean_diff > 0.0:
        verdict = "LIVE_BETTER"
        line = (
            f"LIVE_BETTER: live lower log-loss {live_ll:.4f} vs shadow {shadow_ll:.4f} "
            f"(Δ={mean_diff:+.4f}, 95% CI[{ci_lo:+.4f},{ci_hi:+.4f}] excludes 0; n={n})."
        )
    else:
        verdict = "INSUFFICIENT_N"
        line = (
            f"INSUFFICIENT_N: n={n} >= min_n but the paired-difference CI straddles 0 "
            f"(Δ={mean_diff:+.4f}, 95% CI[{ci_lo:+.4f},{ci_hi:+.4f}]). "
            f"No promotion/demotion licensed — the settled record does not separate the sides."
        )

    return CandidateScoreboard(
        name=name, n_settled=n, counters=counters,
        shadow_logloss_mean=shadow_ll, live_logloss_mean=live_ll,
        shadow_brier_mean=shadow_br, live_brier_mean=live_br,
        logloss_diff_mean=mean_diff, logloss_diff_ci_lo=ci_lo, logloss_diff_ci_hi=ci_hi,
        sign_shadow_better=n_sb, sign_live_better=n_lb, sign_tie=n_tie, sign_p_value=sign_p,
        verdict=verdict, verdict_line=line,
    )


# --------------------------------------------------------------------------- #
# Candidate registry + adapters
# --------------------------------------------------------------------------- #

# An adapter is a callable that yields the raw (shadow_q, live_q) observations
# for its cohort, reading ONLY persisted fields from the supplied connections.
ObservationAdapter = Callable[..., Iterable[CohortObservation]]


@dataclass(frozen=True)
class ShadowCandidate:
    """A registered shadow comparison.

    ``adapter`` reads persisted shadow + live q values; ``include_bin_in_key``
    decides whether the cohort cell is keyed at the (…, bin) or (…, direction)
    grain. ``min_n`` overrides the default settled-cell floor for this candidate.
    """

    name: str
    adapter: ObservationAdapter
    include_bin_in_key: bool = True
    min_n: int = DEFAULT_MIN_N
    description: str = ""


def day0_remaining_day_adapter(
    world_conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
) -> list[CohortObservation]:
    """Adapter (a): historical day0 remaining-day q comparison.

    Reads ``edli_no_submit_receipts`` (WORLD DB — the table the reactor writes,
    same source the settlement-attribution loader uses) LEFT-JOINed to
    ``opportunity_events`` on ``event_id`` so the AUTHORITATIVE day0 provenance
    (``event_type='DAY0_EXTREME_UPDATED'`` and ``payload_json._edli_day0_q_mode``)
    decides which q-construction produced each receipt. The receipt ``q_live``
    is the direction-adjusted posterior (P(traded outcome WINS)) actually used;
    city/metric/date/bin_label/direction come from ``receipt_json``.

    TWO comparison sources, in priority order:
      1. An explicit shadow-cert field ``q_remaining_day`` in ``receipt_json``
         (the remaining-day q computed ALONGSIDE the live q on the SAME
         decision while the flag was OFF). This was the true paired source
         during the original promotion window.
      2. The receipt's own ``q_live`` when its event is tagged
         ``_edli_day0_q_mode='remaining_day'`` — used to pair against a legacy
         receipt for the SAME cell if both modes coexist in the window.

    This adapter is retrospective reporting only. It must not be read as a
    current live gate for ``day0_remaining_day_q_enabled``.
    """
    rows = world_conn.execute(
        """
        SELECT r.receipt_json,
               oe.event_type,
               json_extract(oe.payload_json, '$._edli_day0_q_mode') AS q_mode
        FROM edli_no_submit_receipts AS r
        LEFT JOIN opportunity_events AS oe ON oe.event_id = r.event_id
        WHERE (:since IS NULL OR r.decision_time >= :since)
        ORDER BY r.decision_time ASC
        """,
        {"since": since},
    ).fetchall()

    live_by_cell: dict[tuple, float] = {}
    shadow_by_cell: dict[tuple, float] = {}
    meta_by_cell: dict[tuple, dict] = {}
    for rj_text, event_type, q_mode in rows:
        # Day0-only: the joined opportunity_event is the AUTHORITATIVE day0
        # discriminator (event_type='DAY0_EXTREME_UPDATED'). One classification
        # path — the event link, which all 3,063 live day0 events already carry.
        if event_type != "DAY0_EXTREME_UPDATED":
            continue
        try:
            rj = json.loads(rj_text) if rj_text else {}
        except (TypeError, ValueError):
            continue
        city = rj.get("city")
        tdate = rj.get("target_date")
        metric = rj.get("metric", "high")
        bin_label = rj.get("bin_label", "")
        direction = rj.get("direction")
        q_live = rj.get("q_live")
        if not city or not tdate or not bin_label or not direction:
            continue
        cell = (str(city), str(metric), str(tdate), str(bin_label), str(direction))
        meta_by_cell.setdefault(cell, {
            "city": str(city), "metric": str(metric), "target_date": str(tdate),
            "bin_label": str(bin_label), "direction": str(direction),
        })
        # Source 1: explicit shadow-cert field (the SPEC'd dual-persist).
        q_shadow_cert = rj.get("q_remaining_day", rj.get("q_shadow_remaining_day"))
        if q_shadow_cert is not None:
            shadow_by_cell[cell] = float(q_shadow_cert)
            # The same receipt's live q is the legacy traded value it contrasts.
            if q_live is not None:
                live_by_cell.setdefault(cell, float(q_live))
            continue
        # Source 2: a receipt produced UNDER the remaining_day mode (flag ON for
        # that decision) — its q_live IS the shadow value. The event payload's
        # _edli_day0_q_mode is the authoritative mode tag.
        if q_mode == "remaining_day":
            if q_live is not None:
                shadow_by_cell.setdefault(cell, float(q_live))
        else:
            if q_live is not None:
                live_by_cell[cell] = float(q_live)

    out: list[CohortObservation] = []
    for cell, meta in meta_by_cell.items():
        out.append(
            CohortObservation(
                city=meta["city"], metric=meta["metric"], target_date=meta["target_date"],
                bin_label=meta["bin_label"], direction=meta["direction"],
                shadow_q=shadow_by_cell.get(cell),
                live_q=live_by_cell.get(cell),
            )
        )
    return out


def generic_two_provenance_field_adapter(
    world_conn: sqlite3.Connection,
    *,
    shadow_field: str,
    live_field: str = "q_live",
    receipt_table: str = "edli_no_submit_receipts",
    since: Optional[str] = None,
) -> list[CohortObservation]:
    """Adapter (b): the next promotion's two-provenance-field q comparator.

    Generic over ANY promotion that dual-persists its shadow q into the receipt
    JSON under ``shadow_field``, contrasted against ``live_field`` (default the
    live ``q_live``). One reader serves every future promotion that follows the
    dual-persist convention — no per-promotion harness. Both fields are read
    verbatim from ``receipt_json``; the adapter computes nothing.

    A cell with the shadow field absent yields ``shadow_q=None`` (dropped as
    ``missing_shadow`` — honest absence). City/metric/date/bin/direction come
    from the same receipt JSON keys the day0 adapter uses. Reads the WORLD DB
    (where ``edli_no_submit_receipts`` lives).
    """
    rows = world_conn.execute(
        f"""
        SELECT receipt_json
        FROM {receipt_table}
        WHERE (:since IS NULL OR decision_time >= :since)
        ORDER BY decision_time ASC
        """,
        {"since": since},
    ).fetchall()
    out: list[CohortObservation] = []
    for (rj_text,) in rows:
        try:
            rj = json.loads(rj_text) if rj_text else {}
        except (TypeError, ValueError):
            continue
        city = rj.get("city")
        tdate = rj.get("target_date")
        bin_label = rj.get("bin_label", "")
        direction = rj.get("direction")
        if not city or not tdate or not bin_label or not direction:
            continue
        shadow_raw = rj.get(shadow_field)
        live_raw = rj.get(live_field)
        out.append(
            CohortObservation(
                city=str(city), metric=str(rj.get("metric", "high")),
                target_date=str(tdate), bin_label=str(bin_label), direction=str(direction),
                shadow_q=None if shadow_raw is None else float(shadow_raw),
                live_q=None if live_raw is None else float(live_raw),
            )
        )
    return out


# The standing registry. New promotions register ONE ShadowCandidate here — they
# never write a new comparison harness.
def default_registry() -> list[ShadowCandidate]:
    """The registered shadow candidates.

    Candidate (a) is the immediate customer (day0 remaining-day q). Candidate
    (b) is the reusable generic adapter, parameterised for the next promotion
    (here pointed at a placeholder ``q_shadow`` field — re-point ``shadow_field``
    when the next promotion's dual-persist lands).
    """
    return [
        ShadowCandidate(
            name="day0_remaining_day_q",
            adapter=day0_remaining_day_adapter,
            include_bin_in_key=True,
            description=(
                "historical day0 remaining-day q comparison from "
                "_edli_day0_q_mode receipts; offline evidence only."
            ),
        ),
    ]


# --------------------------------------------------------------------------- #
# Job entry: score the registry, persist scoreboard + markdown
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_PATH = _REPO_ROOT / "state" / "shadow_comparator.json"
_EVIDENCE_DIR = _REPO_ROOT / "docs" / "evidence" / "shadow_comparisons"


def run_shadow_comparator(
    candidates: Sequence[ShadowCandidate],
    *,
    world_conn: sqlite3.Connection,
    since: Optional[str] = None,
    state_path: Path = _STATE_PATH,
    evidence_dir: Path = _EVIDENCE_DIR,
    write: bool = True,
) -> dict:
    """Score every registered candidate and persist the running scoreboard.

    ``world_conn`` is a WORLD-MAIN connection with ``forecasts`` ATTACHed
    (``open_world_with_forecasts``): the adapters read ``edli_no_submit_receipts``
    + ``opportunity_events`` from it, and VERIFIED grading reads
    ``forecasts.settlement_outcomes`` over the SAME connection. Returns the
    report dict; writes ``state/shadow_comparator.json`` + a dated markdown
    under ``docs/evidence/shadow_comparisons/`` when ``write`` is True.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    boards: list[CandidateScoreboard] = []
    for cand in candidates:
        try:
            observations = list(cand.adapter(world_conn, since=since))
        except Exception as exc:  # noqa: BLE001 — one bad adapter must not kill the job
            logger.warning("shadow_comparator: adapter %s failed: %s", cand.name, exc)
            observations = []
        paired, counters = pair_settled_cells(
            observations, world_conn, include_bin_in_key=cand.include_bin_in_key
        )
        board = score_candidate(cand.name, paired, counters, min_n=cand.min_n)
        boards.append(board)

    report = {
        "generated_at": generated_at,
        "since": since,
        "candidates": [b.to_dict() for b in boards],
    }
    if write:
        _write_state(report, state_path)
        _write_markdown(report, evidence_dir, generated_at)
    return report


def _write_state(report: dict, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("shadow_comparator: scoreboard written to %s", state_path)


def _write_markdown(report: dict, evidence_dir: Path, generated_at: str) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    date_tag = generated_at[:10]
    out = evidence_dir / f"{date_tag}_shadow_comparison.md"
    lines = [
        f"# Shadow-vs-Live Comparison — {date_tag}",
        "",
        f"Generated: {generated_at}",
        "",
        "One standing comparator (K<<N). Each candidate's shadow q is contrasted",
        "against the live q over VERIFIED-settled cohort cells, graded through the",
        "settlement spine (grade_receipt). Verdict gate = bootstrap CI on the paired",
        "log-loss difference; the sign test corroborates.",
        "",
    ]
    for cand in report["candidates"]:
        lines.append(f"## {cand['name']}")
        lines.append("")
        lines.append(f"**{cand['verdict']}** — {cand['verdict_line']}")
        lines.append("")
        c = cand["counters"]
        lines.append(
            f"- settled paired cells: **{cand['n_settled']}** "
            f"(missing_shadow={c.get('missing_shadow', 0)}, "
            f"missing_live={c.get('missing_live', 0)}, "
            f"no_settlement={c.get('no_settlement', 0)}, "
            f"ungradeable={c.get('ungradeable', 0)})"
        )
        if cand["n_settled"]:
            lines.append(
                f"- log-loss: shadow={cand['shadow_logloss_mean']:.4f} "
                f"live={cand['live_logloss_mean']:.4f} "
                f"Δ={cand['logloss_diff_mean']:+.4f} "
                f"95% CI[{cand['logloss_diff_ci_lo']:+.4f}, {cand['logloss_diff_ci_hi']:+.4f}]"
            )
            lines.append(
                f"- Brier: shadow={cand['shadow_brier_mean']:.4f} "
                f"live={cand['live_brier_mean']:.4f}"
            )
            lines.append(
                f"- sign test: shadow-better={cand['sign_shadow_better']} "
                f"live-better={cand['sign_live_better']} tie={cand['sign_tie']} "
                f"p={cand['sign_p_value']:.4g}"
            )
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("shadow_comparator: evidence markdown written to %s", out)


def run_shadow_comparator_job(*, since: Optional[str] = None, write: bool = True) -> dict:
    """Daemon/scheduler entry: open the canonical connections and score.

    Mirrors the sibling settlement_guard_report job: ONE WORLD-MAIN connection
    with ``forecasts`` ATTACHed (``open_world_with_forecasts``) serves both the
    receipt/event reads (``edli_no_submit_receipts`` + ``opportunity_events``
    are WORLD-class) AND the VERIFIED grading (``forecasts.settlement_outcomes``
    over the same ATTACH). Fail-soft: any connection or scoring error returns an
    error-tagged report rather than raising into the scheduler loop.
    """
    from src.cron.settlement_attribution import open_world_with_forecasts

    try:
        with open_world_with_forecasts(write_class="bulk") as world_conn:
            return run_shadow_comparator(
                default_registry(),
                world_conn=world_conn,
                since=since,
                write=write,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("shadow_comparator job: failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Standing shadow-vs-live comparator")
    parser.add_argument("--since", default=None, help="ISO decision_time lower bound")
    parser.add_argument("--no-write", action="store_true", help="Do not persist artifacts")
    args = parser.parse_args()

    report = run_shadow_comparator_job(since=args.since, write=not args.no_write)
    for cand in report.get("candidates", []):
        print(f"[{cand['name']}] {cand['verdict_line']}")
    if report.get("status") == "error":
        print(f"[shadow_comparator] ERROR: {report['error']}")


if __name__ == "__main__":
    main()
