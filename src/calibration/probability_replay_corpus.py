# Created: 2026-09-25
# Last reused or audited: 2026-09-25
# Authority basis: operator directive 2026-09-24 (current-recipe replay, external
#   review); Day0 carrier law in docs/authority/replacement_final_form_2026_06_09.md.
"""Current-recipe replay corpus for probability calibration evidence.

A live decision certificate archives the causal information set its q was
computed from. Replay re-scores that set with the CURRENT probability recipe
and keeps the result beside the original live prediction; the certificate,
its revision and its q are never rewritten.

Replayable: Day0 shared remaining-day carrier states (NOAA-preliminary and HKO
provisional sources, ENTRY rebuild basis). Their decision-time inputs are
archived in the certificate, and the live rebuild helper runs on them
unchanged: it recomputes path error, finality, boundary mixture, operator and
settlement binning. Evidence-level estimates archived at decision time
(remaining-path extremes, source-clock sigma, report-survival likelihood) are
the information set and are not re-derived. Every other state is counted as
replay_unavailable with its reason, never approximated.

SETTLEMENT_STATE rows (one per decision state) and EXECUTED_ORDER rows (one per
filled ENTRY command, at its original limit, size and fill) stay distinct.
Replay generations collapse by identity, and fit weight is normalized within
each city/date/metric outcome cluster, so neither regeneration nor repeated
decisions on one settlement can raise effective sample size.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Mapping
from zoneinfo import ZoneInfo

from src.calibration.market_anchored_live_fit import (
    CanonicalMarketAnchoredFitProvider,
    MarketAnchoredArtifactCache,
    _command_outcome_index,
    _execution_contract_for,
    _parse_ts,
)
from src.calibration.market_anchored_residual import FitRow, lead_bucket_of
from src.contracts.payoff_q_correction import CalibrationFitScope, CanonicalTrainingManifest
from src.contracts.probability_validation import EXECUTED_ORDER, POPULATIONS, SETTLEMENT_STATE
from src.data.replacement_forecast_cycle_policy import CURRENT_EVIDENCE_SEMANTICS_REVISION
from src.decision_kernel.canonicalization import stable_hash
from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

REPLAY_CORPUS_REVISION = "current_recipe_replay_v1"
DAY0_ENTRY_CARRIER_BASIS = "entry_current_state_same_vector_witness_v1"
FORECAST_NOT_REPLAYED = "FORECAST_REMATERIALIZATION_NOT_IMPLEMENTED"
NO_REPLAY_EVIDENCE = "RECIPE_HAS_NO_REPLAY_EVIDENCE"
# A decision state's policy contract; ENTRY takers persist FOK (see
# event_reactor_adapter._global_entry_calibration_fit_scope).
POLICY_CONTRACT = {"TAKER_LIMIT": "FOK_FULL_OR_ZERO", "MAKER_REST": "MAKER_REST"}
_SAMPLES_KEY = "_edli_day0_remaining_probability_samples"
_INFORMATION_SET_FIELDS = (
    "_edli_day0_remaining_carrier_future_extremes_c",
    "_edli_day0_remaining_carrier_final_extremes_c",
    "_edli_day0_remaining_carrier_probability_cutoff_utc",
    "_edli_day0_remaining_vector_witness",
    "_edli_day0_provisional_revision_likelihood",
    "_edli_day0_source_clock_predictive_sigma_native",
    "_edli_day0_current_temperature_native",
    "_edli_day0_current_temperature_observed_at_utc",
    "_edli_day0_current_temperature_source",
    "_edli_day0_probability_boundary_native",
    "_edli_global_day0_binding",
    "settlement_source",
    "evidence_finality",
    "observation_time",
    "rounded_value",
    "high_so_far",
    "low_so_far",
    "settlement_unit",
)

# (population, metric, execution_mode, evaluation_recipe_id, origin_live_revision, reason)
UnavailableKey = tuple[str, str, str, str, str, str]


def settlement_contract_id(city: object, target_date: str) -> str:
    """Resolver contract that settles ``city`` on ``target_date``."""

    from src.config import settlement_source_type_for_city
    from src.contracts.settlement_semantics import SettlementSemantics

    source = settlement_source_type_for_city(city, target_date)
    view = getattr(city, "settlement_page_view", "all") if source == "noaa" else "-"
    rounding = SettlementSemantics.for_city(city).rounding_rule
    return f"{source}:{city.wu_station}:{city.settlement_unit}:{rounding}:{view}"


def current_settlement_contracts(cities: Mapping[str, object], today: date) -> frozenset[str]:
    return frozenset(settlement_contract_id(city, today.isoformat()) for city in cities.values())


@dataclass(frozen=True)
class ReplayRow:
    """One historical state re-scored by one recipe; the origin stays verbatim."""

    origin_evidence_id: str
    origin_live_revision: str | None
    origin_raw_q: float
    evaluation_recipe_id: str
    historical_decision_time: str
    historical_information_set_hash: str
    recomputed_raw_q: float
    native_side: str
    bin: str
    settlement_contract_id: str
    event_key: tuple[str, str, str]
    lead_bucket: str | None
    population: str
    execution_mode: str
    execution_contract: str | None
    historical_market_features: Mapping[str, object] | None
    execution_observation_id: str | None
    execution: Mapping[str, object] | None
    settlement_label_id: str | None
    payout: float | None
    label_available_at: str | None
    replay_generated_at: str

    def __post_init__(self) -> None:
        if self.population not in POPULATIONS:
            raise ValueError("replay row population is invalid")
        if (self.population == EXECUTED_ORDER) != (self.execution_observation_id is not None):
            raise ValueError("only an executed order carries an execution observation")

    @property
    def identity(self) -> tuple[object, ...]:
        return (self.origin_evidence_id, self.evaluation_recipe_id, self.population,
                self.execution_observation_id)

    @property
    def p0(self) -> float | None:
        features = self.historical_market_features
        return None if features is None else float(features["p0"])

    @property
    def evidence_available_at(self) -> str:
        return self.execution["fill_available_at"] if self.execution else self.historical_decision_time

    @property
    def weight_basis(self) -> float:
        return float(self.execution["confirmed_shares"]) if self.execution else 1.0


def _held(yes_q: float, side: str) -> float:
    return float(yes_q) if side == "YES" else 1.0 - float(yes_q)


def _late_evidence(
    payload: Mapping[str, object], cutoff: datetime,
    extra_clocks: tuple[tuple[str, object], ...],
) -> str | None:
    """First archived input not possessed at ``cutoff``; unparseable counts as late."""

    likelihood = payload.get("_edli_day0_provisional_revision_likelihood")
    witness = payload.get("_edli_day0_remaining_vector_witness")
    clocks = [
        ("observation_time", payload.get("observation_time")),
        ("observation_available_at", payload.get("observation_available_at")),
        ("current_temperature_observed_at", payload.get("_edli_day0_current_temperature_observed_at_utc")),
        ("physical_frontier_available_at", payload.get("_edli_day0_physical_frontier_available_at")),
        ("likelihood_cutoff", likelihood.get("cutoff") if isinstance(likelihood, Mapping) else None),
        *extra_clocks,
    ]
    if isinstance(witness, Mapping):
        for field in ("capture_times_by_model_utc", "provider_source_available_at_by_model_utc",
                      "fetch_finished_times_by_model_utc"):
            values = witness.get(field)
            if isinstance(values, Mapping):
                clocks.extend((f"vector_witness.{field}", value) for value in values.values())
    for name, raw in clocks:
        if raw in (None, ""):
            continue
        stamp = _parse_ts(raw)
        if stamp is None or stamp > cutoff:
            return f"FUTURE_EVIDENCE:{name}"
    return None


def replay_day0_state(
    payload: Mapping[str, object], *, city: str, target_date: str, metric: str,
    condition_ids: tuple[str, ...], bounds: tuple[tuple[float | None, float | None], ...],
    condition_id: str, side: str, decision_time: datetime, origin_raw_q: float,
    final_extreme_clocks: tuple[tuple[str, object], ...] = (),
) -> tuple[float | None, str | None, str | None]:
    """Re-score one archived Day0 ENTRY carrier state with the current recipe.

    Returns ``(held-side q, unavailable reason, information-set hash)``. The
    archive must first reproduce the origin's live q, proving it is the
    information set that decision actually used.
    """

    future = payload.get("_edli_day0_remaining_carrier_future_extremes_c")
    final = payload.get("_edli_day0_remaining_carrier_final_extremes_c") or []
    if not isinstance(future, list) or not future:
        return None, "CARRIER_INPUTS_NOT_ARCHIVED", None
    if payload.get("_edli_day0_decision_carrier_rebuild_basis") != DAY0_ENTRY_CARRIER_BASIS:
        return None, "NON_ENTRY_CARRIER_BASIS", None
    cutoff = _parse_ts(payload.get("_edli_day0_remaining_carrier_probability_cutoff_utc"))
    if cutoff is None:
        return None, "CARRIER_CUTOFF_UNBOUND", None
    if cutoff > decision_time:
        return None, "FUTURE_EVIDENCE:carrier_cutoff", None
    late = _late_evidence(payload, cutoff, final_extreme_clocks)
    if late:
        return None, late, None
    witness = payload.get("_edli_day0_remaining_vector_witness")
    models = witness.get("actual_models") if isinstance(witness, Mapping) else None
    # Before v18 station final-extreme centers were appended to the hourly
    # members; an untyped mixture cannot be split without guessing.
    if not isinstance(models, list) or len(future) != len(models):
        return None, "CARRIER_MEMBER_TYPING_UNBOUND", None
    if final and not final_extreme_clocks:
        return None, "FINAL_EXTREME_PROVENANCE_UNBOUND", None
    archived_q = payload.get("_edli_day0_remaining_carrier_q")
    transform = payload.get("_edli_day0_lcb_transform")
    mask = transform.get("mask") if isinstance(transform, Mapping) else None
    n = len(condition_ids)
    if (
        condition_id not in condition_ids or len(bounds) != n
        or not isinstance(archived_q, list) or len(archived_q) != n
        or (mask is not None and len(mask) != n)
    ):
        return None, "FAMILY_TOPOLOGY_UNBOUND", None
    index = condition_ids.index(condition_id)
    masked = [float(q) * float(m) for q, m in zip(archived_q, mask or [1.0] * n)]
    if sum(masked) <= 0.0 or not math.isclose(
        _held(masked[index] / sum(masked), side), origin_raw_q, rel_tol=0.0, abs_tol=1e-9,
    ):
        return None, "ARCHIVE_DOES_NOT_REPRODUCE_ORIGIN", None

    from src.config import runtime_cities_by_name
    from src.engine.event_reactor_adapter import (
        _apply_day0_mask_to_probability_vector,
        _rebuild_decision_time_day0_carrier,
    )

    city_config = runtime_cities_by_name().get(city)
    if city_config is None or payload.get("settlement_unit") != city_config.settlement_unit:
        return None, "SETTLEMENT_UNIT_CHANGED", None
    family = SimpleNamespace(
        city=city, metric=metric, target_date=target_date,
        candidates=tuple(SimpleNamespace(bin=SimpleNamespace(low=low, high=high)) for low, high in bounds),
    )
    # Shallow copy: the rebuild writes only top-level keys, so the archive is untouched.
    current = {key: value for key, value in payload.items() if key != _SAMPLES_KEY}
    current["metric"] = metric
    try:
        _rebuild_decision_time_day0_carrier(
            payload=current, family=family, unit=city_config.settlement_unit,
            decision_time=cutoff, future_extremes_c=future, final_extreme_centers_c=final,
            authority_kind="entry_current_remaining_path", entry_authority=True,
        )
        yes_q = _apply_day0_mask_to_probability_vector(
            payload=current, family=family, vector=current["_edli_day0_remaining_carrier_q"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"RECIPE_REJECTED:{str(exc).split(':')[0][:80]}", None
    information_set = stable_hash({
        "fields": {field: payload.get(field) for field in _INFORMATION_SET_FIELDS},
        "family": [city, target_date, metric, list(condition_ids), [list(item) for item in bounds]],
    })
    return _held(float(yes_q[index]), side), None, information_set


def _one_per_identity(rows: tuple[ReplayRow, ...]) -> tuple[ReplayRow, ...]:
    """Collapse regenerations; a deterministic recipe cannot disagree with itself."""

    kept: dict[tuple[object, ...], ReplayRow] = {}
    for row in sorted(rows, key=lambda item: item.replay_generated_at):
        first = kept.setdefault(row.identity, row)
        if first is not row and not math.isclose(
            first.recomputed_raw_q, row.recomputed_raw_q, rel_tol=0.0, abs_tol=1e-12,
        ):
            raise ValueError(f"REPLAY_GENERATION_CONFLICT:{row.origin_evidence_id}")
    return tuple(kept.values())


@dataclass(frozen=True)
class ReplayCorpus:
    """Replay rows plus every state that could not be replayed, by reason."""

    rows: tuple[ReplayRow, ...]
    unavailable: Mapping[UnavailableKey, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", _one_per_identity(tuple(self.rows)))

    def fit_corpus(
        self, *, population: str, current_contracts: frozenset[str], training_cutoff: datetime,
    ) -> "ReplayFitCorpus":
        if population not in POPULATIONS:
            raise ValueError("replay fit population is invalid")
        return ReplayFitCorpus(
            rows=self.rows, unavailable=self.unavailable, population=population,
            current_contracts=frozenset(current_contracts),
            training_cutoff=training_cutoff.astimezone(timezone.utc).isoformat(),
        )


@dataclass(frozen=True)
class ReplayFitCorpus:
    """Duck-typed ``CanonicalFitCorpus`` over one replay population."""

    rows: tuple[ReplayRow, ...]
    unavailable: Mapping[UnavailableKey, int]
    population: str
    current_contracts: frozenset[str]
    training_cutoff: str

    @property
    def revision(self) -> str:
        return f"{REPLAY_CORPUS_REVISION}:{self.population}"

    def _stages(self, scope: CalibrationFitScope):
        cutoff = _parse_ts(self.training_cutoff)

        def labeled(row: ReplayRow) -> bool:
            label_at = _parse_ts(row.label_available_at)
            evidence_at = _parse_ts(row.evidence_available_at)
            return (row.payout in (0.0, 1.0) and label_at is not None and evidence_at is not None
                    and label_at < cutoff and evidence_at < cutoff)

        return (
            ("evaluation_recipe", lambda row: row.evaluation_recipe_id == scope.raw_probability_revision),
            ("population", lambda row: row.population == self.population),
            ("fit_scope", lambda row: (row.event_key[2], row.execution_mode, row.execution_contract)
             == (scope.metric, scope.execution_mode, scope.execution_contract)),
            ("current_settlement_contract", lambda row: row.settlement_contract_id in self.current_contracts),
            ("label_before_cutoff", labeled),
            ("market_anchor", lambda row: row.p0 is not None),
            ("lead_bucket", lambda row: row.lead_bucket is not None),
        )

    def funnel(self, scope: CalibrationFitScope) -> tuple[tuple[str, int, int], ...]:
        """Rows and unique outcome clusters remaining after each filter."""

        rows = list(self.rows)
        out = [("all", len(rows), len({row.event_key for row in rows}))]
        for name, keep in self._stages(scope):
            rows = [row for row in rows if keep(row)]
            out.append((name, len(rows), len({row.event_key for row in rows})))
        return tuple(out)

    def _selection(self, scope: CalibrationFitScope) -> list[ReplayRow]:
        rows = list(self.rows)
        for _name, keep in self._stages(scope):
            rows = [row for row in rows if keep(row)]
        return rows

    def replay_unavailable(self, scope: CalibrationFitScope) -> dict[str, int]:
        """Why this scope's metric/mode states were not replayed to its recipe."""

        reasons: Counter[str] = Counter()
        for (population, metric, mode, recipe, _origin, reason), count in self.unavailable.items():
            if (population, metric, mode, recipe) == (
                self.population, scope.metric, scope.execution_mode, scope.raw_probability_revision,
            ):
                reasons[reason] += count
        if not reasons and not any(
            row.evaluation_recipe_id == scope.raw_probability_revision for row in self.rows
        ):
            return {NO_REPLAY_EVIDENCE: 0}
        return dict(reasons)

    def fit_rows(
        self, *, metric: str, execution_mode: str, probability_revision: str,
        execution_contract: str,
    ) -> list[FitRow]:
        scope = CalibrationFitScope(
            metric=metric, execution_mode=execution_mode,
            execution_contract=execution_contract, raw_probability_revision=probability_revision,
        )
        rows = self._selection(scope)
        totals: dict[tuple[str, str, str], float] = defaultdict(float)
        for row in rows:
            totals[row.event_key] += row.weight_basis
        # The artifact is fit in YES-event space: complement NO inputs and label together.
        return [FitRow(
            p0=row.p0 if row.native_side == "YES" else 1.0 - row.p0,
            q_raw=row.recomputed_raw_q if row.native_side == "YES" else 1.0 - row.recomputed_raw_q,
            y=int(row.payout if row.native_side == "YES" else 1.0 - row.payout),
            lead_bucket=row.lead_bucket,
            w=row.weight_basis / totals[row.event_key],
        ) for row in rows]

    def training_manifest(self, *, scope: CalibrationFitScope) -> CanonicalTrainingManifest:
        rows = self._selection(scope)
        if not rows:
            raise ValueError("replay training manifest has no rows")
        fit_rows = self.fit_rows(
            metric=scope.metric, execution_mode=scope.execution_mode,
            probability_revision=scope.raw_probability_revision,
            execution_contract=scope.execution_contract,
        )
        return CanonicalTrainingManifest.build(
            scope_hash=scope.as_payload()["scope_hash"],
            corpus_revision=self.revision,
            training_cutoff=self.training_cutoff,
            row_count=len(rows),
            event_count=len({row.event_key for row in rows}),
            weight_sum=math.fsum(row.w for row in fit_rows),
            max_fill_available_at=max(rows, key=lambda row: _parse_ts(row.evidence_available_at)).evidence_available_at,
            max_label_available_at=max(rows, key=lambda row: _parse_ts(row.label_available_at)).label_available_at,
            input_hash=stable_hash({"rows": [
                [row.origin_evidence_id, row.execution_observation_id, fit.p0, fit.q_raw, fit.y,
                 fit.lead_bucket, fit.w, row.evidence_available_at, row.label_available_at]
                for row, fit in zip(rows, fit_rows, strict=True)
            ]}),
        )


class ReplayMarketAnchoredFitProvider(CanonicalMarketAnchoredFitProvider):
    """Fits replay rows whose evaluation_recipe_id is ``scope.raw_probability_revision``.

    Offline evidence only: its artifacts carry a replay input revision and a
    private cache, so they can never be mistaken for the live ENTRY fit.
    """

    def __init__(
        self, connects, *, city_timezones: Mapping[str, str] | None, population: str,
        current_contracts: frozenset[str], **kwargs,
    ) -> None:
        if population not in POPULATIONS:
            raise ValueError("replay fit population is invalid")
        super().__init__(connects, city_timezones=city_timezones,
                         cache=MarketAnchoredArtifactCache(), **kwargs)
        self._population = population
        self._current_contracts = frozenset(current_contracts)
        self._calibration_policy = replace(
            self._calibration_policy, input_revision=f"{REPLAY_CORPUS_REVISION}:{population}",
        )

    def _corpus(self, handles, *, cutoff, corpus_key, deadline_monotonic, minimum_cutoff=None):
        if self._expired(deadline_monotonic):
            return None
        world, trade, forecast = handles
        try:
            corpus = load_replay_corpus(
                world, trade, forecast, training_cutoff=cutoff,
                generated_at=datetime.now(timezone.utc), world_schema=self._schemas[0],
                trade_schema=self._schemas[1], forecast_schema=self._schemas[2],
            )
        except Exception:  # noqa: BLE001 - a failed read is no fit, never a partial one
            return None
        return corpus.fit_corpus(
            population=self._population, current_contracts=self._current_contracts,
            training_cutoff=cutoff,
        )

    def replay_unavailable(self, *, scope: CalibrationFitScope, now: datetime) -> dict[str, int] | None:
        prepared = self._prepared_corpus(now=now, deadline_monotonic=None)
        return None if prepared is None else prepared[-1].replay_unavailable(scope)


def _origin_raw_q(economics: Mapping[str, object]) -> float | None:
    """The raw q the live decision actually priced, as sealed in its certificate."""

    capture = economics.get("raw_calibration_input")
    if isinstance(capture, Mapping) and capture.get("raw_q_held") is not None:
        return float(capture["raw_q_held"])
    correction = economics.get("market_anchored_correction")
    if isinstance(correction, Mapping) and correction.get("applied") is True:
        return float(correction["q_raw"])
    value = economics.get("payoff_q_point")
    return None if value is None else float(value)


def load_replay_corpus(
    world: sqlite3.Connection, trade: sqlite3.Connection, forecast: sqlite3.Connection,
    *, training_cutoff: datetime, generated_at: datetime,
    world_schema: str = "main", trade_schema: str = "main", forecast_schema: str = "main",
) -> ReplayCorpus:
    """Read-only: re-score every archived ENTRY decision state before ``training_cutoff``."""

    from src.config import runtime_cities_by_name
    from src.ingest.payout_observer import _coherent_finalized_pair
    from src.state.fill_dedup import canonical_trade_fact_cte, economic_trade_fact_cte

    if (world_schema not in ("main", "world") or trade_schema not in ("main", "trades")
            or forecast_schema not in ("main", "forecasts")):
        raise ValueError("unsupported replay corpus schema")
    cutoff = training_cutoff.astimezone(timezone.utc)
    cutoff_text = cutoff.isoformat()
    generated_text = generated_at.astimezone(timezone.utc).isoformat()
    recipe = DAY0_PROBABILITY_SEMANTICS_REVISION
    cities = runtime_cities_by_name()
    unavailable: Counter[UnavailableKey] = Counter()

    cursor = trade.execute(f"""
        SELECT a.decision_certificate_hash, c.command_id, c.token_id, c.venue_order_id,
               c.side, c.price, c.size, e.order_type, e.post_only
        FROM {trade_schema}.position_decision_attribution a
        JOIN {trade_schema}.venue_commands c ON c.command_id = a.command_id
        LEFT JOIN {trade_schema}.venue_submission_envelopes e ON e.envelope_id = c.envelope_id
        WHERE a.intent_kind = 'ENTRY' AND c.intent_kind = 'ENTRY'
          AND julianday(c.created_at) < julianday(?)
    """, (cutoff_text,))
    names = [column[0] for column in cursor.description]
    links = [dict(zip(names, values)) for values in cursor.fetchall()]
    fact_scope = ("WHERE julianday(fact.observed_at)<julianday(?) "
                  "AND julianday(fact.ingested_at)<julianday(?)")
    source_scope = ("AND julianday(source_fact.observed_at)<julianday(?) "
                    "AND julianday(source_fact.ingested_at)<julianday(?)")
    fills: dict[str, list[tuple]] = defaultdict(list)
    for command_id, venue_order_id, size, observed, ingested, executed in trade.execute(f"""
        WITH {canonical_trade_fact_cte(source_schema=trade_schema, source_clause_sql=fact_scope)},
             {economic_trade_fact_cte(source_schema=trade_schema, source_clause_sql=source_scope)}
        SELECT command_id, venue_order_id, filled_size, observed_at, ingested_at, execution_ts
        FROM economic_trade_fact WHERE UPPER(state) = 'CONFIRMED'
    """, (cutoff_text,) * 4):
        fills[command_id].append((venue_order_id, size, observed, ingested, executed))
    # Only a filled command is an executed position; one command, one decision.
    link_counts = Counter(link["command_id"] for link in links)
    for (command_id,) in trade.execute(f"""
        SELECT command_id FROM {trade_schema}.venue_commands
        WHERE intent_kind = 'ENTRY' AND julianday(created_at) < julianday(?)
    """, (cutoff_text,)):
        if fills.get(command_id) and not link_counts[command_id]:
            unavailable[(EXECUTED_ORDER, "?", "?", "?", "?", "EXECUTION_UNBOUND:CERTIFICATE_LINK_MISSING")] += 1
    executed_by_certificate: dict[str, list[dict]] = defaultdict(list)
    for link in links:
        if not fills.get(link["command_id"]):
            continue
        if link_counts[link["command_id"]] != 1:
            unavailable[(EXECUTED_ORDER, "?", "?", "?", "?", "EXECUTION_UNBOUND:LINK_AMBIGUOUS")] += 1
            continue
        executed_by_certificate[link["decision_certificate_hash"]].append(link)

    memo: dict[tuple, list[dict]] = {}

    def select(key: tuple, conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict]:
        if key not in memo:
            cur = conn.execute(sql, params)
            columns = [column[0] for column in cur.description]
            memo[key] = [dict(zip(columns, values)) for values in cur.fetchall()]
        return memo[key]

    def family_bounds(city: str, target: str, metric: str, condition_ids: tuple[str, ...]):
        rows = select(("family", city, target, metric), forecast, f"""
            SELECT condition_id, range_label, range_low, range_high FROM {forecast_schema}.market_events
            WHERE city = ? AND target_date = ? AND temperature_metric = ? AND condition_id IS NOT NULL
        """, (city, target, metric))
        by_condition = {row["condition_id"]: row for row in rows}
        if not condition_ids or set(by_condition) != set(condition_ids):
            return None, None
        return (tuple((by_condition[c]["range_low"], by_condition[c]["range_high"]) for c in condition_ids),
                {c: by_condition[c]["range_label"] for c in condition_ids})

    def snapshot(snapshot_id: object) -> dict | None:
        if not snapshot_id:
            return None
        rows = select(("snapshot", snapshot_id), trade, f"""
            SELECT snapshot_id, condition_id, yes_token_id, no_token_id, token_map_json,
                   orderbook_top_bid, orderbook_top_ask, captured_at
            FROM {trade_schema}.executable_market_snapshots WHERE snapshot_id = ?
        """, (snapshot_id,))
        return rows[0] if rows else None

    def label(condition_id: str, token_id: str, snap: Mapping | None):
        pair = select(("payout", condition_id), trade, f"""
            WITH ranked AS (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY condition_id, outcome_index ORDER BY id DESC) AS rn
              FROM {trade_schema}.payout_observations
              WHERE condition_id = ? AND julianday(observed_at) < julianday(?) AND outcome_index IN (0, 1)
            ) SELECT * FROM ranked WHERE rn = 1
        """, (condition_id, cutoff_text))
        if snap is None or not _coherent_finalized_pair(pair):
            return None, None, None
        index = _command_outcome_index({**snap, "token_id": token_id})
        held = next((row for row in pair if row["outcome_index"] == index), None)
        if held is None or held["payout_numerator"] not in (0, held["payout_denominator"]):
            return None, None, None
        return (held["payout_numerator"] / held["payout_denominator"],
                f"payout:{condition_id}:{held['block_number']}:{held['block_hash']}",
                max(_parse_ts(row["observed_at"]) for row in pair).isoformat())

    def final_extreme_clocks(payload: Mapping, final: list) -> tuple[tuple[str, object], ...]:
        provenance = payload.get("_edli_day0_source_clock_carrier_provenance")
        posterior_id = provenance.get("posterior_id") if isinstance(provenance, Mapping) else None
        if not final or type(posterior_id) is not int:
            return ()
        rows = select(("posterior", posterior_id), forecast, f"""
            SELECT computed_at, json_extract(provenance_json,
                   '$.day0_remaining_carrier_station_extreme_providers') AS providers
            FROM {forecast_schema}.forecast_posteriors WHERE posterior_id = ?
        """, (posterior_id,))
        providers = json.loads(rows[0]["providers"]) if rows and rows[0]["providers"] else []
        if [float(item.get("forecast_value_c")) for item in providers] != [float(v) for v in final]:
            return ()
        return (("final_extreme_posterior_computed_at", rows[0]["computed_at"]),
                *((f"final_extreme.{field}", item.get(field))
                  for item in providers for field in ("source_available_at", "captured_at")))

    seen: set[str] = set()
    rows: list[ReplayRow] = []
    for certificate_hash, decision_raw, persisted_raw, payload_hash, q_source, revision, metric, mode, payload_json in world.execute(f"""
        SELECT certificate_hash, decision_time, persisted_at, payload_hash,
               json_extract(payload_json, '$.q_source'),
               json_extract(payload_json, '$.probability_semantics_revision'),
               COALESCE(json_extract(payload_json, '$.temperature_metric'), json_extract(payload_json, '$.metric')),
               json_extract(payload_json, '$.qkernel_execution_economics.global_execution_mode'),
               CASE WHEN json_extract(payload_json, '$.q_source') = 'day0_remaining_day'
                    THEN payload_json END
        FROM {world_schema}.decision_certificates
        WHERE certificate_type = 'ActionableTradeCertificate' AND mode = 'LIVE'
          AND verifier_status = 'VERIFIED' AND julianday(persisted_at) < julianday(?)
    """, (cutoff_text,)):
        seen.add(certificate_hash)
        executed = executed_by_certificate.get(certificate_hash, ())
        state_recipe = recipe if q_source == "day0_remaining_day" else (
            CURRENT_EVIDENCE_SEMANTICS_REVISION if q_source == "replacement_0_1" else "UNKNOWN")
        key = (str(metric), str(mode), state_recipe, str(revision))

        def miss(reason: str) -> None:
            unavailable[(SETTLEMENT_STATE, *key, reason)] += 1
            unavailable[(EXECUTED_ORDER, *key, reason)] += len(executed)

        if q_source != "day0_remaining_day":
            miss(FORECAST_NOT_REPLAYED)
            continue
        payload = json.loads(payload_json)
        decision_at = _parse_ts(decision_raw)
        persisted_at = _parse_ts(persisted_raw)
        economics = payload.get("qkernel_execution_economics") or {}
        observation = (payload.get("day0_probability_authority") or {}).get(
            "global_current_observation_payload") or {}
        city, target, condition_id = payload.get("city"), payload.get("target_date"), payload.get("condition_id")
        side = {"buy_yes": "YES", "buy_no": "NO"}.get(payload.get("direction"))
        origin_q = _origin_raw_q(economics)
        city_config = cities.get(city)
        transform = observation.get("_edli_day0_lcb_transform") or {}
        condition_ids = tuple(sorted(set(transform.get("no_lcb_by_condition") or {})
                                     | set(transform.get("yes_lcb_by_condition") or {})))
        if (None in (decision_at, persisted_at, side, origin_q, city_config) or not target
                or metric not in ("high", "low") or mode not in POLICY_CONTRACT):
            miss("CERTIFICATE_IDENTITY_UNBOUND")
            continue
        if not observation.get("_edli_day0_remaining_carrier_future_extremes_c"):
            miss("CARRIER_INPUTS_NOT_ARCHIVED")
            continue
        bounds, labels = family_bounds(city, target, metric, condition_ids)
        if bounds is None or labels.get(condition_id) != payload.get("bin_label"):
            miss("FAMILY_TOPOLOGY_UNBOUND")
            continue
        if stable_hash(payload) != payload_hash:
            miss("CERTIFICATE_PAYLOAD_HASH_UNBOUND")
            continue
        recomputed, reason, information_set = replay_day0_state(
            observation, city=city, target_date=target, metric=metric,
            condition_ids=condition_ids, bounds=bounds, condition_id=condition_id, side=side,
            decision_time=decision_at, origin_raw_q=origin_q,
            final_extreme_clocks=final_extreme_clocks(
                observation, observation.get("_edli_day0_remaining_carrier_final_extremes_c") or []),
        )
        if reason:
            miss(reason)
            continue
        capture = economics.get("raw_calibration_input") or {}
        book = snapshot(capture.get("book_snapshot_id"))
        features = None
        if (book is not None and capture.get("p0_held") is not None
                and capture.get("token_id") == payload.get("token_id") and capture.get("side") == side
                and capture.get("condition_id") == condition_id and capture.get("execution_mode") == mode
                and _parse_ts(book["captured_at"]) is not None
                and _parse_ts(book["captured_at"]) <= decision_at):
            features = {"p0": float(capture["p0_held"]), "p0_basis": capture.get("p0_basis"),
                        "book_snapshot_id": book["snapshot_id"], "best_bid": book["orderbook_top_bid"],
                        "best_ask": book["orderbook_top_ask"], "captured_at": book["captured_at"]}
        payout, label_id, label_at = label(
            condition_id, payload.get("token_id"),
            snapshot(payload.get("executable_snapshot_id")) or book,
        )
        local_date = decision_at.astimezone(ZoneInfo(city_config.timezone)).date()
        common = dict(
            origin_evidence_id=certificate_hash, origin_live_revision=revision, origin_raw_q=origin_q,
            evaluation_recipe_id=recipe, historical_decision_time=decision_at.isoformat(),
            historical_information_set_hash=information_set, recomputed_raw_q=recomputed,
            native_side=side, bin=str(payload.get("bin_label")),
            settlement_contract_id=settlement_contract_id(city_config, target),
            event_key=(city, target, metric),
            lead_bucket=lead_bucket_of(local_date, date.fromisoformat(target)),
            execution_mode=mode, historical_market_features=features,
            settlement_label_id=label_id, payout=payout, label_available_at=label_at,
            replay_generated_at=generated_text,
        )
        rows.append(ReplayRow(
            **common, population=SETTLEMENT_STATE, execution_contract=POLICY_CONTRACT[mode],
            execution_observation_id=None, execution=None,
        ))
        for command in executed:
            contract, contract_reason = _execution_contract_for(
                mode, command["order_type"], command["post_only"])
            shares, available = 0.0, []
            for venue_order_id, size, observed, ingested, executed_raw in fills[command["command_id"]]:
                ingested_at = _parse_ts(ingested if "+" in str(ingested) or str(ingested).endswith("Z")
                                        else str(ingested).replace(" ", "T") + "Z")
                observed_at, executed_at = _parse_ts(observed), _parse_ts(executed_raw)
                if (None in (observed_at, ingested_at, executed_at) or venue_order_id != command["venue_order_id"]
                        or not persisted_at <= executed_at <= observed_at < cutoff
                        or not executed_at <= ingested_at < cutoff):
                    shares = -1.0
                    break
                shares += float(size)
                available.extend((observed_at, ingested_at))
            problem = (
                "EXECUTION_UNBOUND:COMMAND_IDENTITY" if (
                    command["side"] != "BUY" or command["token_id"] != payload.get("token_id"))
                else f"EXECUTION_UNBOUND:{contract_reason}" if contract_reason
                else "EXECUTION_UNBOUND:FILL_CLOCK_OR_IDENTITY" if shares <= 0.0 else None
            )
            if problem:
                unavailable[(EXECUTED_ORDER, *key, problem)] += 1
                continue
            rows.append(ReplayRow(
                **common, population=EXECUTED_ORDER, execution_contract=contract,
                execution_observation_id=command["command_id"],
                execution={"limit_price": command["price"], "size": command["size"],
                           "confirmed_shares": shares, "fill_available_at": max(available).isoformat()},
            ))
    for certificate_hash, commands in executed_by_certificate.items():
        if certificate_hash not in seen:
            unavailable[(EXECUTED_ORDER, "?", "?", "?", "?", "CERTIFICATE_MISSING_OR_NOT_LIVE_VERIFIED")] += len(commands)
    return ReplayCorpus(rows=tuple(rows), unavailable=dict(unavailable))
