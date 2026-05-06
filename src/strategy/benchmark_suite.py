# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
"""Strategy benchmark and promotion gate for R3 A1.

The suite is deliberately evidence-only: it computes supporting benchmark
metrics and records local runs, but it never places orders or mutates a
production DB. Promotion requires an explicit promotion-grade economics metric;
diagnostic replay, simulated venue evidence, and read-only live evidence can
block promotion but cannot authorize it alone.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from sqlite3 import Connection
from statistics import fmean
from typing import Any, Iterable, Mapping, Protocol, Sequence


class BenchmarkEnvironment(str, Enum):
    """Benchmark provenance labels, not runtime modes or promotion authority."""

    REPLAY = "replay"
    SIMULATED_VENUE = "simulated_venue"
    READ_ONLY_LIVE = "read_only_live"
    PROMOTION_GRADE_ECONOMICS = "promotion_grade_economics"


class EvidenceGrade(str, Enum):
    DIAGNOSTIC_REPLAY = "diagnostic_replay"
    SIMULATED_VENUE_EVIDENCE = "simulated_venue_evidence"
    READ_ONLY_LIVE_EVIDENCE = "read_only_live_evidence"
    PROMOTION_GRADE_ECONOMICS = "promotion_grade_economics"


class PromotionVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    BLOCK = "BLOCK"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class SemanticDriftFinding:
    code: str
    message: str
    waived: bool = False


@dataclass(frozen=True)
class BenchmarkObservation:
    """One normalized observation for benchmark metrics.

    Values are unit-normalized dollar/probability/basis-point inputs supplied by
    replay, simulated-venue, read-only-live, or economics evidence providers.
    This object is not a venue command and has no side effects.
    """

    strategy_key: str
    observed_at: datetime
    alpha_pnl: float = 0.0
    spread_pnl: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    failed_settlement_cost: float = 0.0
    capital_lock_cost: float = 0.0
    fill_probability: float = 0.0
    adverse_selection_bps: float = 0.0
    time_to_resolution_hours: float = 0.0
    liquidity_decay: float = 0.0
    opportunity_cost: float = 0.0
    drawdown_equity: float | None = None
    calibrated_probability: float | None = None
    market_implied_probability: float | None = None
    capital_at_risk: float = 1.0

    @property
    def net_pnl(self) -> float:
        return (
            self.alpha_pnl
            + self.spread_pnl
            - self.fees
            - self.slippage
            - self.failed_settlement_cost
            - self.capital_lock_cost
            - self.opportunity_cost
        )


@dataclass(frozen=True)
class ReplayCorpus:
    strategy_key: str
    observations: tuple[BenchmarkObservation, ...]
    window_start: datetime
    window_end: datetime
    semantic_drift: tuple[SemanticDriftFinding, ...] = ()
    source: str = "in_memory"


@dataclass(frozen=True)
class StrategyMetrics:
    strategy_key: str
    window_start: datetime
    window_end: datetime
    ev_after_fees_slippage: float
    realized_spread_capture: float
    fill_probability: float
    adverse_selection_bps: float
    time_to_resolution_risk: float
    liquidity_decay: float
    opportunity_cost: float
    drawdown_max: float
    drawdown_max_duration_hours: float
    calibration_error_vs_market_implied: float
    environment: BenchmarkEnvironment = BenchmarkEnvironment.REPLAY
    evidence_grade: EvidenceGrade = EvidenceGrade.DIAGNOSTIC_REPLAY
    sample_count: int = 0
    alpha_pnl: float = 0.0
    spread_pnl: float = 0.0
    fees_paid: float = 0.0
    slippage_cost: float = 0.0
    failed_settlement_cost: float = 0.0
    capital_lock_cost: float = 0.0
    semantic_drift: tuple[SemanticDriftFinding, ...] = ()

    @property
    def pnl_breakdown(self) -> dict[str, float]:
        return {
            "alpha": self.alpha_pnl,
            "spread": self.spread_pnl,
            "fees": self.fees_paid,
            "slippage": self.slippage_cost,
            "failed_settlement": self.failed_settlement_cost,
            "capital_lock": self.capital_lock_cost,
        }

    @property
    def unwaived_semantic_drift(self) -> tuple[SemanticDriftFinding, ...]:
        return tuple(item for item in self.semantic_drift if not item.waived)


@dataclass(frozen=True)
class PromotionThresholds:
    min_ev_after_fees_slippage: float = 0.0
    min_fill_probability: float = 0.1
    max_adverse_selection_bps: float = 100.0
    max_drawdown: float = 1_000_000.0
    max_calibration_error_vs_market_implied: float = 0.2


@dataclass(frozen=True)
class PromotionDecision:
    verdict: PromotionVerdict
    reasons: tuple[str, ...]
    diagnostic_replay: StrategyMetrics
    simulated_venue: StrategyMetrics
    read_only_live: StrategyMetrics
    economics: StrategyMetrics | None = None

    @property
    def replay(self) -> StrategyMetrics:
        return self.diagnostic_replay


class VenueLike(Protocol):
    def get_trades(self, since: str | None = None) -> Sequence[Any]: ...

    def get_positions(self) -> Sequence[Any]: ...


STRATEGY_BENCHMARK_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_benchmark_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_key TEXT NOT NULL,
  environment TEXT NOT NULL CHECK (
    environment IN (
      'replay',
      'simulated_venue',
      'read_only_live',
      'promotion_grade_economics'
    )
  ),
  evidence_grade TEXT NOT NULL CHECK (
    evidence_grade IN (
      'diagnostic_replay',
      'simulated_venue_evidence',
      'read_only_live_evidence',
      'promotion_grade_economics'
    )
  ),
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  promotion_verdict TEXT CHECK (promotion_verdict IN ('PROMOTE','BLOCK','NEEDS_REVIEW')),
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
""".strip()


class StrategyBenchmarkSuite:
    """Compute supporting evidence metrics and gate promotion-grade economics.

    Read-only live evidence consumes preloaded corpora only. It is not a live
    adapter and intentionally cannot submit/cancel/redeem venue orders.
    """

    def __init__(
        self,
        *,
        replay_corpora: Mapping[str, ReplayCorpus] | None = None,
        read_only_live_corpora: Mapping[str, ReplayCorpus] | None = None,
        thresholds: PromotionThresholds | None = None,
    ) -> None:
        self._replay_corpora = dict(replay_corpora or {})
        self._read_only_live_corpora = dict(read_only_live_corpora or {})
        self.thresholds = thresholds or PromotionThresholds()

    def evaluate_replay(self, strategy_key: str, fixture_corpus: ReplayCorpus | None = None) -> StrategyMetrics:
        corpus = fixture_corpus or self._require_corpus(self._replay_corpora, strategy_key, "replay")
        return self._metrics_from_corpus(
            strategy_key,
            corpus,
            BenchmarkEnvironment.REPLAY,
            EvidenceGrade.DIAGNOSTIC_REPLAY,
        )

    def evaluate_simulated_venue(self, strategy_key: str, fake_venue: VenueLike, duration_hours: int) -> StrategyMetrics:
        now = datetime.now(timezone.utc)
        observations = tuple(_simulated_venue_observations(strategy_key, fake_venue, now))
        if observations:
            start = min(item.observed_at for item in observations)
            end = max(item.observed_at for item in observations)
        else:
            start = now - timedelta(hours=duration_hours)
            end = now
        drift = () if observations else (
            SemanticDriftFinding(
                code="NO_SIMULATED_VENUE_TRADES",
                message="simulated venue benchmark observed no fake-venue trades in the requested window",
                waived=False,
            ),
        )
        corpus = ReplayCorpus(strategy_key=strategy_key, observations=observations, window_start=start, window_end=end, semantic_drift=drift, source="fake_venue")
        return self._metrics_from_corpus(
            strategy_key,
            corpus,
            BenchmarkEnvironment.SIMULATED_VENUE,
            EvidenceGrade.SIMULATED_VENUE_EVIDENCE,
        )

    def evaluate_read_only_live(self, strategy_key: str, capital_cap_micro: int, duration_hours: int) -> StrategyMetrics:
        corpus = self._read_only_live_corpora.get(strategy_key)
        if corpus is None:
            now = datetime.now(timezone.utc)
            corpus = ReplayCorpus(
                strategy_key=strategy_key,
                observations=(),
                window_start=now - timedelta(hours=duration_hours),
                window_end=now,
                semantic_drift=(
                    SemanticDriftFinding(
                        code="NO_READ_ONLY_LIVE_EVIDENCE",
                        message="read-only live benchmark requires preloaded read-only live evidence",
                        waived=False,
                    ),
                ),
                source=f"read_only_live_cap_micro={int(capital_cap_micro)}",
            )
        return self._metrics_from_corpus(
            strategy_key,
            corpus,
            BenchmarkEnvironment.READ_ONLY_LIVE,
            EvidenceGrade.READ_ONLY_LIVE_EVIDENCE,
        )

    def promotion_decision(
        self,
        diagnostic_replay: StrategyMetrics,
        simulated_venue: StrategyMetrics,
        read_only_live: StrategyMetrics,
        economics: StrategyMetrics | None = None,
    ) -> PromotionDecision:
        reasons: list[str] = []
        expected = (
            (EvidenceGrade.DIAGNOSTIC_REPLAY, diagnostic_replay),
            (EvidenceGrade.SIMULATED_VENUE_EVIDENCE, simulated_venue),
            (EvidenceGrade.READ_ONLY_LIVE_EVIDENCE, read_only_live),
        )
        for grade, metrics in expected:
            if metrics.evidence_grade != grade:
                reasons.append(f"{grade.value}: wrong evidence grade {metrics.evidence_grade.value}")
            reasons.extend(self._blocking_reasons(metrics))
        if economics is None:
            reasons.append("promotion_grade_economics: missing promotion-grade economics evidence")
        else:
            if economics.evidence_grade != EvidenceGrade.PROMOTION_GRADE_ECONOMICS:
                reasons.append(f"promotion_grade_economics: wrong evidence grade {economics.evidence_grade.value}")
            reasons.extend(self._blocking_reasons(economics))
        strategy_keys = [
            diagnostic_replay.strategy_key,
            simulated_venue.strategy_key,
            read_only_live.strategy_key,
        ]
        if economics is not None:
            strategy_keys.append(economics.strategy_key)
        if len(set(strategy_keys)) != 1:
            reasons.append("strategy_key mismatch across evidence metrics")
        verdict = PromotionVerdict.PROMOTE if not reasons else PromotionVerdict.BLOCK
        return PromotionDecision(
            verdict=verdict,
            reasons=tuple(reasons),
            diagnostic_replay=diagnostic_replay,
            simulated_venue=simulated_venue,
            read_only_live=read_only_live,
            economics=economics,
        )

    def ensure_schema(self, conn: Connection) -> None:
        conn.execute(STRATEGY_BENCHMARK_RUNS_DDL)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(strategy_benchmark_runs)").fetchall()}
        if "evidence_grade" not in cols:
            raise RuntimeError(
                "strategy_benchmark_runs is missing evidence_grade; run an explicit "
                "benchmark schema migration packet before recording evidence-grade runs"
            )

    def record_benchmark_run(
        self,
        conn: Connection,
        metrics: StrategyMetrics,
        promotion_verdict: PromotionVerdict | None = None,
    ) -> int:
        self.ensure_schema(conn)
        payload = metrics_to_jsonable(metrics)
        cur = conn.execute(
            """
            INSERT INTO strategy_benchmark_runs (
              strategy_key, environment, evidence_grade, window_start, window_end, metrics_json, promotion_verdict
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.strategy_key,
                metrics.environment.value,
                metrics.evidence_grade.value,
                metrics.window_start.isoformat(),
                metrics.window_end.isoformat(),
                json.dumps(payload, sort_keys=True),
                promotion_verdict.value if promotion_verdict else None,
            ),
        )
        return int(cur.lastrowid)

    def _metrics_from_corpus(
        self,
        strategy_key: str,
        corpus: ReplayCorpus,
        environment: BenchmarkEnvironment,
        evidence_grade: EvidenceGrade,
    ) -> StrategyMetrics:
        observations = tuple(item for item in corpus.observations if item.strategy_key == strategy_key)
        sample_count = len(observations)
        if sample_count == 0:
            drift = corpus.semantic_drift or (
                SemanticDriftFinding(code="NO_BENCHMARK_OBSERVATIONS", message="no benchmark observations for strategy", waived=False),
            )
            return StrategyMetrics(
                strategy_key=strategy_key,
                window_start=corpus.window_start,
                window_end=corpus.window_end,
                ev_after_fees_slippage=0.0,
                realized_spread_capture=0.0,
                fill_probability=0.0,
                adverse_selection_bps=0.0,
                time_to_resolution_risk=0.0,
                liquidity_decay=0.0,
                opportunity_cost=0.0,
                drawdown_max=0.0,
                drawdown_max_duration_hours=0.0,
                calibration_error_vs_market_implied=0.0,
                environment=environment,
                evidence_grade=evidence_grade,
                sample_count=0,
                semantic_drift=tuple(drift),
            )

        alpha_pnl = sum(item.alpha_pnl for item in observations)
        spread_pnl = sum(item.spread_pnl for item in observations)
        fees = sum(item.fees for item in observations)
        slippage = sum(item.slippage for item in observations)
        failed = sum(item.failed_settlement_cost for item in observations)
        capital_lock = sum(item.capital_lock_cost for item in observations)
        calibration_errors = [
            abs(float(item.calibrated_probability) - float(item.market_implied_probability))
            for item in observations
            if item.calibrated_probability is not None and item.market_implied_probability is not None
        ]
        return StrategyMetrics(
            strategy_key=strategy_key,
            window_start=corpus.window_start,
            window_end=corpus.window_end,
            ev_after_fees_slippage=fmean(item.net_pnl for item in observations),
            realized_spread_capture=fmean(item.spread_pnl for item in observations),
            fill_probability=fmean(_clamp_probability(item.fill_probability) for item in observations),
            adverse_selection_bps=fmean(item.adverse_selection_bps for item in observations),
            time_to_resolution_risk=_capital_weighted_resolution_risk(observations),
            liquidity_decay=fmean(item.liquidity_decay for item in observations),
            opportunity_cost=sum(item.opportunity_cost for item in observations),
            drawdown_max=_max_drawdown(observations)[0],
            drawdown_max_duration_hours=_max_drawdown(observations)[1],
            calibration_error_vs_market_implied=fmean(calibration_errors) if calibration_errors else 0.0,
            environment=environment,
            evidence_grade=evidence_grade,
            sample_count=sample_count,
            alpha_pnl=alpha_pnl,
            spread_pnl=spread_pnl,
            fees_paid=fees,
            slippage_cost=slippage,
            failed_settlement_cost=failed,
            capital_lock_cost=capital_lock,
            semantic_drift=tuple(corpus.semantic_drift),
        )

    def _blocking_reasons(self, metrics: StrategyMetrics) -> list[str]:
        threshold = self.thresholds
        reasons: list[str] = []
        prefix = metrics.evidence_grade.value
        if metrics.sample_count <= 0:
            reasons.append(f"{prefix}: no benchmark observations")
        if metrics.ev_after_fees_slippage <= threshold.min_ev_after_fees_slippage:
            reasons.append(f"{prefix}: non-positive EV after fees/slippage")
        if metrics.fill_probability < threshold.min_fill_probability:
            reasons.append(f"{prefix}: fill probability below threshold")
        if metrics.adverse_selection_bps > threshold.max_adverse_selection_bps:
            reasons.append(f"{prefix}: adverse selection above threshold")
        if metrics.drawdown_max > threshold.max_drawdown:
            reasons.append(f"{prefix}: drawdown above threshold")
        if metrics.calibration_error_vs_market_implied > threshold.max_calibration_error_vs_market_implied:
            reasons.append(f"{prefix}: calibration error above threshold")
        for finding in metrics.unwaived_semantic_drift:
            reasons.append(f"{prefix}: unwaived semantic drift {finding.code}")
        return reasons

    @staticmethod
    def _require_corpus(corpora: Mapping[str, ReplayCorpus], strategy_key: str, environment: str) -> ReplayCorpus:
        try:
            return corpora[strategy_key]
        except KeyError as exc:
            raise KeyError(f"no {environment} corpus registered for strategy_key={strategy_key!r}") from exc


def metrics_to_jsonable(metrics: StrategyMetrics) -> dict[str, Any]:
    payload = asdict(metrics)
    payload["window_start"] = metrics.window_start.isoformat()
    payload["window_end"] = metrics.window_end.isoformat()
    payload["environment"] = metrics.environment.value
    payload["evidence_grade"] = metrics.evidence_grade.value
    payload["semantic_drift"] = [asdict(item) for item in metrics.semantic_drift]
    payload["pnl_breakdown"] = metrics.pnl_breakdown
    return payload


def _simulated_venue_observations(strategy_key: str, fake_venue: VenueLike, observed_at: datetime) -> Iterable[BenchmarkObservation]:
    for trade in fake_venue.get_trades(None):
        raw = dict(getattr(trade, "raw", {}) or {})
        state = str(raw.get("state", "")).upper()
        size = _safe_float(raw.get("size"), default=0.0)
        is_confirmed = state == "CONFIRMED"
        is_failed = state == "FAILED"
        yield BenchmarkObservation(
            strategy_key=strategy_key,
            observed_at=_parse_datetime(raw.get("observed_at"), observed_at),
            alpha_pnl=size * 0.01 if is_confirmed else 0.0,
            spread_pnl=size * 0.005 if is_confirmed else 0.0,
            fees=size * 0.001 if is_confirmed else 0.0,
            slippage=size * 0.001 if is_confirmed else 0.0,
            failed_settlement_cost=size * 0.01 if is_failed else 0.0,
            capital_lock_cost=size * 0.0005,
            fill_probability=1.0 if is_confirmed else 0.0,
            adverse_selection_bps=5.0 if is_confirmed else 50.0,
            time_to_resolution_hours=1.0,
            liquidity_decay=0.0 if is_confirmed else 0.02,
            opportunity_cost=size * 0.0005,
            calibrated_probability=0.55 if is_confirmed else 0.45,
            market_implied_probability=0.50,
            capital_at_risk=max(size, 1.0),
        )


def _capital_weighted_resolution_risk(observations: Sequence[BenchmarkObservation]) -> float:
    total_capital = sum(max(item.capital_at_risk, 0.0) for item in observations)
    if total_capital <= 0:
        return 0.0
    return sum(item.time_to_resolution_hours * max(item.capital_at_risk, 0.0) for item in observations) / total_capital


def _max_drawdown(observations: Sequence[BenchmarkObservation]) -> tuple[float, float]:
    equity = 0.0
    peak = 0.0
    peak_time = observations[0].observed_at
    max_drawdown = 0.0
    max_duration = 0.0
    for item in sorted(observations, key=lambda obs: obs.observed_at):
        equity = item.drawdown_equity if item.drawdown_equity is not None else equity + item.net_pnl
        if equity >= peak:
            peak = equity
            peak_time = item.observed_at
            continue
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_duration = max((item.observed_at - peak_time).total_seconds() / 3600.0, 0.0)
    return max_drawdown, max_duration


def _clamp_probability(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return default
    return default
