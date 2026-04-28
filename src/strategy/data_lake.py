# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
"""Replay-corpus accessor for A1 strategy benchmark tests.

This module is an interface seam, not a historical-data crawler. It can serve
in-memory corpora for replay tests and raises explicitly when a requested corpus
is absent instead of silently fetching live data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from src.strategy.benchmark_suite import BenchmarkObservation, ReplayCorpus


@dataclass(frozen=True)
class MarketFilter:
    strategy_key: str | None = None
    market_ids: tuple[str, ...] = ()
    city: str | None = None
    temperature_metric: str | None = None


class DataLake:
    """Read-only historical replay accessor.

    A1 intentionally ships a deterministic in-memory implementation. Future
    archive/CLOB/Gamma integrations must preserve this read-only contract and
    add their own topology profile before introducing external fetch paths.
    """

    def __init__(self, corpora: Mapping[str, ReplayCorpus] | Iterable[ReplayCorpus] | None = None) -> None:
        if corpora is None:
            self._corpora: dict[str, ReplayCorpus] = {}
        elif isinstance(corpora, Mapping):
            self._corpora = dict(corpora)
        else:
            self._corpora = {corpus.strategy_key: corpus for corpus in corpora}

    def fetch_replay_corpus(self, market_filter: MarketFilter, period: tuple[datetime, datetime]) -> ReplayCorpus:
        if market_filter.strategy_key is None:
            raise ValueError("strategy_key is required for deterministic A1 replay corpus lookup")
        try:
            corpus = self._corpora[market_filter.strategy_key]
        except KeyError as exc:
            raise KeyError(f"no replay corpus registered for strategy_key={market_filter.strategy_key!r}") from exc

        start, end = period
        observations = tuple(
            observation
            for observation in corpus.observations
            if start <= observation.observed_at <= end and _matches_filter(observation, market_filter)
        )
        return ReplayCorpus(
            strategy_key=corpus.strategy_key,
            observations=observations,
            window_start=start,
            window_end=end,
            semantic_drift=corpus.semantic_drift,
            source=corpus.source,
        )


def _matches_filter(observation: BenchmarkObservation, market_filter: MarketFilter) -> bool:
    # A1 BenchmarkObservation is strategy-key centric. Market/city/metric filters
    # are accepted for forward compatibility but remain no-op unless future
    # evidence-bearing observation fields are added under a separate packet.
    return observation.strategy_key == market_filter.strategy_key
