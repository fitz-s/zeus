# Created: 2026-06-08
# Last reused or audited: 2026-06-09
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 (fail-soft multi-model live capture), §4 algorithm
#   step (1) eligible / (4) EB bias-correct; §7 antibodies (source-disagreement, lowN). The
#   capture reuses the existing Open-Meteo single-runs fetch pattern
#   (openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload) per model.
"""F1 — fail-soft multi-model live capture for the BAYES_PRECISION_FUSION-Bayes fusion.

Fetches the decorrelated globals (gfs_global, icon_global, gem_global, jma_seamless, icon_eu)
and the in-EU-polygon icon_d2 / France arome ALONGSIDE the existing AIFS+0.1 anchor, via the
existing Open-Meteo single-runs fetch pattern. EB-bias-corrects each model from a walk-forward
settlement-joined history, and emits the bayes_precision_fusion.ModelInstrument inputs for the fusion.

FAIL-SOFT IS THE STRUCTURAL GUARANTEE (Fitz #1 + spec §6): a per-model fetch failure DROPS that
model and the cycle proceeds with the survivors — the Bayesian fusion handles missing sources by
construction (a dropped source is simply absent from z; Sigma shrinks toward equal-weight). A
model with no walk-forward history is dropped from Sigma estimation but can still contribute via
equal-weight fallback. The capture NEVER raises to the cycle; absent ALL extras -> empty result
-> the materializer keeps the existing single-anchor posterior (byte-identical).

This module performs NO DB writes and NO settlement-truth lookups itself; the walk-forward
residual history is supplied by an injected provider (the live materializer wires the forecast/
settlement store; tests inject a fixture). Provider failure is caught and treated as "no
history" (fail-soft), never a crash.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Mapping, Protocol, Sequence

from src.forecast.model_selection import (
    ANCHOR_MODEL,
    DECORR_GLOBALS,
    GLOBAL_LIKELIHOOD_MODELS,
    ICON_EU_MODEL,
    REGIONAL_MODELS,
    SelectedModelSet,
    select_models,
)
from src.forecast.bayes_precision_fusion import (
    DISAGREE_W,
    MIN_TRAIN,
    ModelInstrument,
    eb_bias,
)

_LOG = logging.getLogger("zeus.bayes_precision_fusion_capture")


def _available_after_decision(
    available_at: str | datetime | None,
    decision_utc: datetime,
    *,
    model_label: str = "",
) -> bool:
    """True iff ``available_at`` is genuinely AFTER ``decision_utc`` (arrival-guard exclusion).

    FAIL-OPEN: returns False (admit) when ``available_at`` is None/empty/unparseable — the guard
    never excludes a model on the strength of missing or malformed availability evidence. Naive
    timestamps are interpreted as UTC (Zeus persists UTC wall-clocks).

    LOUD FALLBACK: every admit on missing/malformed evidence emits a WARNING so that a model
    admitted without availability proof is VISIBLE in logs (freshness-contract: no silent admit).
    The model_label parameter (e.g. the model name) is included in the warning for traceability."""
    from datetime import timezone as _tz  # noqa: PLC0415

    _label = f" model={model_label!r}" if model_label else ""
    if available_at is None:
        _LOG.warning(
            "BAYES_PRECISION_FUSION arrival guard: admitted%s on MISSING available_at "
            "(fail-open; no availability evidence to exclude on)",
            _label,
        )
        return False
    try:
        if isinstance(available_at, datetime):
            avail = available_at
        else:
            text = str(available_at).strip()
            if not text:
                _LOG.warning(
                    "BAYES_PRECISION_FUSION arrival guard: admitted%s on EMPTY available_at "
                    "(fail-open; no availability evidence to exclude on)",
                    _label,
                )
                return False
            avail = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if avail.tzinfo is None:
            avail = avail.replace(tzinfo=_tz.utc)
        decision = decision_utc if decision_utc.tzinfo is not None else decision_utc.replace(tzinfo=_tz.utc)
        return avail.astimezone(_tz.utc) > decision.astimezone(_tz.utc)
    except Exception as _parse_exc:
        _LOG.warning(
            "BAYES_PRECISION_FUSION arrival guard: admitted%s on UNPARSEABLE available_at %r "
            "(fail-open; malformed availability evidence ignored): %s",
            _label,
            available_at,
            _parse_exc,
        )
        return False

# Open-Meteo model ids for the single-runs forecast endpoint. icon_eu is OM's `icon_eu`;
# jma_seamless / gem_global / gfs_global / icon_global / icon_d2 are OM model ids; the France
# AROME-HD model is OM `meteofrance_arome_france_hd`.
OPENMETEO_MODEL_IDS: dict[str, str] = {
    "gfs_global": "gfs_global",
    "icon_global": "icon_global",
    "gem_global": "gem_global",
    "jma_seamless": "jma_seamless",
    "icon_eu": "icon_eu",
    "icon_d2": "icon_d2",
    "meteofrance_arome_france_hd": "meteofrance_arome_france_hd",
    # icon_seamless is fetched only to run the alias-dedup test against icon_d2.
    "icon_seamless": "icon_seamless",
    # 2026-06-09 promotion (universality sweep P5): explicit entries so a future open-meteo id
    # rename can never silently break the identity fallback (`.get(model, model)`).
    "ncep_nbm_conus": "ncep_nbm_conus",
    "ukmo_global_deterministic_10km": "ukmo_global_deterministic_10km",
    "ukmo_uk_deterministic_2km": "ukmo_uk_deterministic_2km",
}

# BLOCKER 3: the OM product that actually serves the ANCHOR walk-forward history via the
# previous-runs API. The stored raw_model_forecasts.model column is the fusion identity
# ('ecmwf_ifs', the 9km live anchor) but the OM previous-runs feed serves only the 0.25 product
# 'ecmwf_ifs025'. This constant is the single source of truth the capture consults to decide
# whether the anchor history needs the ifs025->ifs9 bridge. It MUST equal the download's
# OPENMETEO_PREVIOUS_RUNS_MODEL_IDS[ANCHOR_MODEL].
OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME = "ecmwf_ifs025"


@dataclass(frozen=True)
class ModelHistory:
    """Walk-forward settlement-joined history for ONE model at a (city, metric, lead).

    ``forecast_values`` and ``settlement_values`` are aligned, ordered strictly BEFORE the
    target date (no same-day leak). ``residuals`` = forecast - settlement. Empty -> the model
    gets bias=0 and is excluded from the covariance window (low-n inflation applies downstream).

    ``target_dates`` (BLOCKER 2): the ISO target_date of each (forecast, settlement) pair, in
    the SAME order as the value tuples. ``residual_by_target_date`` maps each date to its
    residual. The fusion's covariance is built ONLY over the INTERSECTION of these dates across
    the selected models, so residuals from different target_dates can never share a covariance
    row (equal length is NOT equal meaning — BAYES_PRECISION_FUSION_SPEC §2/§4). ``target_dates`` defaults
    to empty for legacy callers that supply only the value tuples (date-less -> the covariance
    aligner falls back to the proven positional stack for that caller only).
    """

    model: str
    forecast_values: tuple[float, ...]
    settlement_values: tuple[float, ...]
    target_dates: tuple[str, ...] = ()

    @property
    def residuals(self) -> tuple[float, ...]:
        return tuple(f - y for f, y in zip(self.forecast_values, self.settlement_values))

    @property
    def residual_by_target_date(self) -> dict[str, float]:
        """BLOCKER 2 — residual keyed by ISO target_date. The covariance aligner consumes THIS;
        an empty map (legacy date-less history) signals the positional-stack fallback."""
        return {
            d: f - y
            for d, f, y in zip(
                self.target_dates, self.forecast_values, self.settlement_values
            )
        }

    @property
    def n_train(self) -> int:
        return len(self.forecast_values)


class BayesPrecisionFusionHistoryProvider(Protocol):
    """Supplies walk-forward residual history per model. The live materializer wires a forecast/
    settlement-store query; tests inject a fixture. Implementations MUST be walk-forward (only
    target_date strictly before ``target_date``) and MUST NOT raise (return {} on any failure)."""

    def __call__(
        self,
        *,
        city: str,
        metric: str,
        lead_days: int,
        target_date: date | str,
        models: Sequence[str],
    ) -> Mapping[str, ModelHistory]:
        ...


def _empty_history_provider(**_kwargs: object) -> Mapping[str, ModelHistory]:
    """Default provider: no history wired -> empty. Fail-soft (anchor fallback / equal-weight)."""
    return {}


# A per-model live fetch callable: returns today's local-day extremum (degC) for the metric,
# or None on any failure (FAIL-SOFT drop). The live materializer wires the OM single-runs fetch;
# tests inject a fixture. Signature kept minimal so the capture stays provider-agnostic.
LiveFetchFn = Callable[..., float | None]


def _default_live_fetch(
    *,
    model: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    run: datetime | str,
    target_local_date: date,
    metric: str,
    forecast_hours: int,
) -> float | None:
    """Default live fetch via the existing Open-Meteo single-runs pattern. FAIL-SOFT: returns
    None on ANY error (network, parse, empty window) so the model is dropped, never crashing."""
    try:
        from urllib.parse import urlencode  # noqa: PLC0415

        from src.data.openmeteo_client import fetch  # noqa: PLC0415
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            SINGLE_RUNS_FORECAST_URL,
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )

        om_model = OPENMETEO_MODEL_IDS.get(model, model)
        run_iso = (
            run.strftime("%Y-%m-%dT%H:%M")
            if isinstance(run, datetime)
            else str(run)
        )
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m",
            "models": om_model,
            "run": run_iso,
            "forecast_hours": forecast_hours,
            "temperature_unit": "celsius",
            "timezone": timezone_name,
        }
        payload = fetch(
            SINGLE_RUNS_FORECAST_URL,
            params,
            endpoint_label=f"bayes_precision_fusion_{model}_single_runs",
        )
        anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=timezone_name,
            target_local_date=target_local_date,
        )
        return float(anchor.high_c if metric == "high" else anchor.low_c)
    except Exception as exc:  # FAIL-SOFT: drop this model, never block the cycle.
        _LOG.warning("BAYES_PRECISION_FUSION live fetch dropped model %s (fail-soft): %s", model, exc)
        return None


@dataclass(frozen=True)
class BayesPrecisionFusionCaptureResult:
    """The fail-soft capture output, ready for bayes_precision_fusion.fuse_bayes_precision_posterior.

    ``anchor_z`` is the EB-corrected anchor center — ALWAYS set (the anchor product feeds every
    cell on this path). ``anchor_tau0`` is the anchor's walk-forward residual std (the TRUSTED
    prior spread); None when the anchor lacks a >=MIN_TRAIN history, which demotes the anchor
    from T2 prior to ONE equal member in the fusion's EQUAL_WEIGHT fallback (thin-anchor
    retention, 2026-06-09 — the center is never silently dropped).
    ``likelihood`` are the surviving, gated, deduped, bias-corrected instruments.
    ``disagree_var`` is the cross-source spread term added (widen-only) into the fusion sigma.
    ``selection`` is the F4 provenance (dropped aliases / excluded regionals / used models).
    """

    anchor_z: float | None
    anchor_tau0: float | None
    likelihood: tuple[ModelInstrument, ...]
    disagree_var: float
    selection: SelectedModelSet
    dropped_models: tuple[str, ...]
    # Count of models admitted because availability evidence was missing/malformed
    # (FAIL-OPEN path). Non-zero means the arrival guard ran without evidence for
    # those models — visible in telemetry, never silently hidden.
    admitted_on_missing_availability: int = 0

    @property
    def has_extras(self) -> bool:
        """True iff at least one non-anchor instrument survived. When False AND no anchor
        history, the materializer keeps the existing single-anchor path (byte-identical)."""
        return len(self.likelihood) > 0


def _eb_corrected(model: str, raw_value: float, history: ModelHistory | None, parent_bias: float) -> tuple[float, int]:
    """Return (z = raw - b_hat, n_train) using walk-forward EB bias. No history -> bias 0."""
    resids = list(history.residuals) if history else []
    b_hat = eb_bias(resids, parent_bias) if resids else 0.0
    return raw_value - b_hat, (history.n_train if history else 0)


def capture_bayes_precision_instruments(
    *,
    city: str,
    metric: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    run: datetime | str,
    target_local_date: date,
    lead_days: int,
    forecast_hours: int = 120,
    anchor_z_corrected: float,
    history_provider: BayesPrecisionFusionHistoryProvider | None = None,
    live_fetch: LiveFetchFn | None = None,
    decision_utc: datetime | None = None,
    model_available_at: Mapping[str, str | datetime | None] | None = None,
) -> BayesPrecisionFusionCaptureResult:
    """F1 — fetch the extras fail-soft, EB-correct, gate, dedup, and build fusion inputs.

    ``anchor_z_corrected`` is the already-EB-corrected 0.1 anchor center the materializer already
    has (the soft-anchor anchor_value_c minus its EB shift); the capture pairs it with the
    anchor's walk-forward residual std to form the prior. The decorrelated globals + in-polygon
    regionals are fetched live (fail-soft), EB-corrected from their own histories, gated by the
    polygon (regionals) and deduped (icon_seamless==icon_d2), then emitted as ModelInstruments.

    ARRIVAL GUARD (C1-AVAIL-CLOCK, 2026-06-16): ``decision_utc`` + ``model_available_at`` add a
    pre-fusion honesty gate — an extra whose honest source_available_at is in the FUTURE relative to
    the decision instant could not have been possessed by then, so fusing it would let a
    faster/replacement source bias q before its real availability. Such a model is EXCLUDED here.
    SHADOW-Q-STAGED: this guard CAN change q for a cell where a provider's honest availability is
    future vs decision — that is the point; in current production it is expected to exclude ~0 (the
    extras' captured_at lands hours after the cycle, well before any decision). FAIL-OPEN: a model
    is admitted when its availability is NULL/missing, or when ``decision_utc`` is not supplied
    (legacy/test callers) — the guard never tightens on absent evidence.

    NEVER raises. Any failure of an individual model -> that model is dropped. A total failure
    (all extras dropped) -> empty likelihood -> the caller keeps the single-anchor posterior.
    """
    provider = history_provider or _empty_history_provider
    fetch_fn = live_fetch or _default_live_fetch
    availability = dict(model_available_at or {})

    candidate_models = list(GLOBAL_LIKELIHOOD_MODELS) + list(REGIONAL_MODELS) + ["icon_seamless"]

    # ---- fail-soft per-model live capture ----
    present_values: dict[str, float] = {}
    dropped: list[str] = []
    _missing_avail_count = 0  # models admitted because availability evidence was absent/malformed
    for model in candidate_models:
        # ARRIVAL GUARD: exclude an extra whose honest availability is after the decision instant
        # (it was not possessed yet — fusing it would bias q early). Fail-OPEN on NULL/missing
        # availability or when decision_utc is absent. Shadow-q-staged (expected to drop ~0 today).
        if decision_utc is not None:
            _raw_avail = availability.get(model)
            # Track admits on missing/malformed evidence for telemetry (loud, never silent).
            # A model is admitted-on-missing when its availability key is absent, None, or empty
            # string — the same cases _available_after_decision warns on internally. We count here
            # so the BayesPrecisionFusionCaptureResult carries a visible counter the caller can surface.
            _avail_is_missing = (
                _raw_avail is None
                or (isinstance(_raw_avail, str) and not _raw_avail.strip())
            )
            if _available_after_decision(_raw_avail, decision_utc, model_label=model):
                _LOG.warning(
                    "BAYES_PRECISION_FUSION arrival guard excluded %s: honest source_available_at %s "
                    "is after decision %s (not yet possessed; shadow-q-staged)",
                    model, _raw_avail, decision_utc.isoformat(),
                )
                dropped.append(model)
                continue
            if _avail_is_missing:
                _missing_avail_count += 1
        try:
            value = fetch_fn(
                model=model,
                latitude=latitude,
                longitude=longitude,
                timezone_name=timezone_name,
                run=run,
                target_local_date=target_local_date,
                metric=metric,
                forecast_hours=forecast_hours,
            )
        except Exception as exc:  # belt-and-braces: a buggy provider must not crash the cycle
            _LOG.warning("BAYES_PRECISION_FUSION capture dropped %s (provider raised, fail-soft): %s", model, exc)
            value = None
        if value is None:
            dropped.append(model)
            continue
        present_values[model] = float(value)

    # The anchor is always present (the materializer already has it); include it so selection +
    # parent-bias pooling see it.
    present_for_selection = dict(present_values)
    present_for_selection[ANCHOR_MODEL] = float(anchor_z_corrected)

    # ---- walk-forward history (fail-soft) ----
    try:
        histories = dict(
            provider(
                city=city, metric=metric, lead_days=lead_days,
                target_date=target_local_date, models=list(present_for_selection),
            )
        )
    except Exception as exc:
        _LOG.warning("BAYES_PRECISION_FUSION history provider failed (fail-soft, no history): %s", exc)
        histories = {}

    # parent bias = pooled mean residual across anchor + globals (structural prior, spec EB).
    pooled: list[float] = []
    for m in (ANCHOR_MODEL,) + GLOBAL_LIKELIHOOD_MODELS:
        h = histories.get(m)
        if h:
            pooled.extend(h.residuals)
    parent_bias = (sum(pooled) / len(pooled)) if pooled else 0.0

    # ---- alias dedup series (recent residual/value series for icon_seamless vs icon_d2) ----
    alias_series: dict[str, Sequence[float]] = {}
    for m in ("icon_d2", "icon_seamless"):
        h = histories.get(m)
        if h and h.forecast_values:
            alias_series[m] = list(h.forecast_values)

    selection = select_models(
        present_models=present_values,
        lat=latitude, lon=longitude, lead_days=lead_days,
        alias_series=alias_series or None,
    )

    # ---- EB-correct + build instruments for the SELECTED set (globals then regionals) ----
    # BLOCKER 2: each instrument carries residuals_by_target_date so the fusion aligns the
    # covariance by date (the cross-model Sigma is estimated only over the common target_dates).
    instruments: list[ModelInstrument] = []
    for m in selection.likelihood_globals:
        z, n = _eb_corrected(m, present_values[m], histories.get(m), parent_bias)
        h = histories.get(m)
        instruments.append(ModelInstrument(
            model=m, z=z, train_residuals=tuple(h.residuals) if h else (),
            residuals_by_date=h.residual_by_target_date if h else {},
            n_train=n, is_regional=False,
        ))
    for m in selection.regional_experts:
        z, n = _eb_corrected(m, present_values[m], histories.get(m), parent_bias)
        h = histories.get(m)
        instruments.append(ModelInstrument(
            model=m, z=z, train_residuals=tuple(h.residuals) if h else (),
            residuals_by_date=h.residual_by_target_date if h else {},
            n_train=n, is_regional=True,
        ))

    # ---- anchor prior (EB-corrected center + walk-forward residual std) ----
    # BLOCKER 3: the anchor history's PHYSICAL product is ecmwf_ifs025 (0.25), the only ECMWF
    # feed Open-Meteo's previous-runs API serves — NOT the live 9km ecmwf_ifs anchor. The raw
    # 025 residual std must therefore be reconciled to the 9km frame via the declared
    # ifs025->ifs9 bridge BEFORE it becomes the anchor prior tau0 (the bridge widens, never
    # narrows). Without this the anchor sigma + q_lcb would silently inherit a 0.25 product's
    # uncertainty as if it were the 9km anchor's.
    from src.forecast.bayes_precision_fusion_anchor_bridge import (  # noqa: PLC0415
        anchor_history_requires_bridge,
        bridge_anchor_tau0,
    )

    # The stored anchor history product (model_name actually served by the OM prev-runs feed).
    anchor_stored_model_name = OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME
    anchor_hist = histories.get(ANCHOR_MODEL)
    if anchor_hist and anchor_hist.n_train >= MIN_TRAIN:
        import statistics  # noqa: PLC0415

        try:
            raw_tau0 = statistics.stdev(anchor_hist.residuals)
        except statistics.StatisticsError:
            raw_tau0 = None
        anchor_z: float | None = float(anchor_z_corrected)
        if raw_tau0 is None:
            anchor_tau0: float | None = None
        elif anchor_history_requires_bridge(stored_model_name=anchor_stored_model_name):
            # 025 history -> bridge to the 9km frame (widen tau0).
            anchor_tau0 = float(bridge_anchor_tau0(float(raw_tau0)))
        else:
            anchor_tau0 = float(raw_tau0)
    else:
        # THIN/ZERO anchor history (2026-06-09 anchor-drop fix): the anchor CENTER is the
        # materializer's own EB-corrected anchor product and is ALWAYS available — it must
        # always reach the fusion, exactly like a zero-history global participates
        # LOWN-inflated in equal-weight. anchor_tau0=None is the explicit "no TRUSTED prior"
        # signal: fuse_bayes_precision_posterior then stays EQUAL_WEIGHT and blends the center as ONE
        # equal member at the conservative thin variance (TAU0_FLOOR * LOWN_INFLATE)^2.
        # (The old `if anchor_hist else None` deleted the strongest model for zero-history
        # cells, and the old comment claimed "the fusion floors tau0" — it never did: the
        # fusion required BOTH non-None, so the center was silently dropped.)
        anchor_z = float(anchor_z_corrected)
        anchor_tau0 = None

    # ---- source-disagreement term (widen-only): variance of corrected z over anchor+globals ----
    corrected_for_disagree = [ins.z for ins in instruments if not ins.is_regional]
    if anchor_z is not None:
        corrected_for_disagree.append(anchor_z)
    if len(corrected_for_disagree) >= 2:
        mean = sum(corrected_for_disagree) / len(corrected_for_disagree)
        var = sum((v - mean) ** 2 for v in corrected_for_disagree) / len(corrected_for_disagree)
        disagree_var = var * DISAGREE_W
    else:
        disagree_var = 0.0

    return BayesPrecisionFusionCaptureResult(
        anchor_z=anchor_z,
        anchor_tau0=anchor_tau0,
        likelihood=tuple(instruments),
        disagree_var=disagree_var,
        selection=selection,
        dropped_models=tuple(dropped),
        admitted_on_missing_availability=_missing_avail_count,
    )
