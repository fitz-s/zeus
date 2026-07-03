"""AIFS ENS OpenData request contract for sampled-2t blocked candidate extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION, LOW_DATA_VERSION, PRODUCT_ID, SOURCE_ID


MODEL = "aifs-ens"
ECMWF_CLASS = "ai"
STREAM = "enfo"
TYPES = ("cf", "pf")
PARAMS = ("2t",)
LEVTYPE = "sfc"
SOURCE = "azure"
# ECMWF open-data is replicated across these mirrors (per ECMWF's own portal notice). AWS S3
# throttles our IP with HTTP 503 SlowDown under the 39-manifest cycle load — which starved the
# AIFS anchor download, leaving the materializer with no fresh posterior (the live stall). Default
# off AWS to Azure (verified un-throttled). Any of these mirrors is a valid switchable source.
_VALID_AIFS_SOURCES = frozenset({"azure", "ecmwf", "aws"})
DEFAULT_STEPS = tuple(range(0, 361, 6))
UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


class AifsOpenDataClient(Protocol):
    def retrieve(self, **kwargs: Any) -> Any: ...


def _coerce_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value)
    raise ValueError("forecast_date must be a date or ISO date string")


def _coerce_cycle_hour(value: int | str) -> int:
    hour = int(value)
    if hour not in {0, 6, 12, 18}:
        raise ValueError("AIFS ENS cycle hour must be one of 00/06/12/18 UTC")
    return hour


def _reject_transcript_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full product identity")


@dataclass(frozen=True)
class AifsEnsOpenDataRequest:
    forecast_date: date
    cycle_hour: int
    target_path: Path
    steps: tuple[int, ...] = DEFAULT_STEPS
    source: str = SOURCE
    model: str = MODEL
    ecmwf_class: str = ECMWF_CLASS
    stream: str = STREAM
    types: tuple[str, ...] = TYPES
    params: tuple[str, ...] = PARAMS
    levtype: str = LEVTYPE
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    high_data_version: str = HIGH_DATA_VERSION
    low_data_version: str = LOW_DATA_VERSION
    trade_authority_status: str = "BLOCKED"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "forecast_date", _coerce_date(self.forecast_date))
        object.__setattr__(self, "cycle_hour", _coerce_cycle_hour(self.cycle_hour))
        object.__setattr__(self, "target_path", Path(self.target_path))
        if not self.steps:
            raise ValueError("AIFS ENS request requires at least one step")
        if any(step < 0 or step % 6 != 0 or step > 360 for step in self.steps):
            raise ValueError("AIFS ENS sampled-2t steps must be 6-hourly in 0..360")
        if self.source not in _VALID_AIFS_SOURCES:
            raise ValueError(
                f"AIFS ENS OpenData source must be one of {sorted(_VALID_AIFS_SOURCES)} "
                f"(ECMWF open-data mirrors); got {self.source!r}"
            )
        if self.model != MODEL or self.ecmwf_class != ECMWF_CLASS:
            raise ValueError("AIFS ENS product identity must use class=ai and model=aifs-ens")
        if self.stream != STREAM or set(self.types) != set(TYPES):
            raise ValueError("AIFS ENS request must use stream=enfo and cf/pf member types")
        if self.params != PARAMS or self.levtype != LEVTYPE:
            raise ValueError("AIFS ENS sampled-2t request must use param=2t at sfc")
        for field_name, value in (
            ("source_id", self.source_id),
            ("product_id", self.product_id),
            ("high_data_version", self.high_data_version),
            ("low_data_version", self.low_data_version),
        ):
            _reject_transcript_alias(value, field_name=field_name)
        if "mx2t" in self.high_data_version or "mn2t" in self.low_data_version:
            raise ValueError("AIFS ENS sampled-2t request cannot use period-extrema data_versions")
        if self.trade_authority_status != "BLOCKED" or self.training_allowed:
            raise ValueError("AIFS ENS request is blocked until promoted by evidence")

    @property
    def source_cycle_time(self) -> datetime:
        return datetime(
            self.forecast_date.year,
            self.forecast_date.month,
            self.forecast_date.day,
            self.cycle_hour,
            tzinfo=UTC,
        )

    def client_kwargs(self) -> dict[str, Any]:
        return {"source": self.source, "model": self.model}

    def retrieve_kwargs(self) -> dict[str, Any]:
        # Do not pass class=ai to ecmwf.opendata.Client.retrieve(). In current
        # client versions that maps the request to aifs-single even when the
        # Client was built with model=aifs-ens.
        return {
            "date": self.forecast_date.strftime("%Y%m%d"),
            "time": self.cycle_hour,
            "model": self.model,
            "stream": self.stream,
            "type": list(self.types),
            "step": list(self.steps),
            "param": list(self.params),
            "levtype": self.levtype,
            "target": str(self.target_path),
        }

    def manifest_metadata(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "product_id": self.product_id,
            "model": self.model,
            "class": self.ecmwf_class,
            "stream": self.stream,
            "type": list(self.types),
            "param": list(self.params),
            "levtype": self.levtype,
            "step_count": len(self.steps),
            "source_cycle_time": self.source_cycle_time.isoformat(),
            "trade_authority_status": self.trade_authority_status,
            "training_allowed": self.training_allowed,
            "measurement_policy": "sampled_2t_6h_local_calendar_day",
        }


def build_aifs_ens_open_data_request(
    *,
    forecast_date: date | str,
    cycle_hour: int | str,
    target_path: str | Path,
    steps: tuple[int, ...] = DEFAULT_STEPS,
) -> AifsEnsOpenDataRequest:
    return AifsEnsOpenDataRequest(
        forecast_date=_coerce_date(forecast_date),
        cycle_hour=_coerce_cycle_hour(cycle_hour),
        target_path=Path(target_path),
        steps=steps,
    )


def retrieve_aifs_ens_open_data_request(
    request: AifsEnsOpenDataRequest,
    *,
    client_factory: Callable[..., AifsOpenDataClient] | None = None,
) -> Path:
    """Retrieve the AIFS ENS GRIB artifact ATOMICALLY with mirror failover.

    FLAWLESS-DOWNLOAD ANTIBODY (rule 5 — make partial/throttled corruption unconstructable;
    this download runs several times a day and must never poison the artifact store):
      * ATOMIC: retrieve into a sibling ``.partial`` temp and ``os.replace`` it onto the final
        path ONLY after the client returns a non-empty file. A throttled / killed / partial
        retrieve leaves at most an orphan ``.partial`` (removed in the finally), so the final
        artifact + its manifest can never be committed half-written — the byte_size/sha256
        mismatch in RawForecastArtifactManifest.verify_artifact that previously aborted the
        materializer becomes UNCONSTRUCTABLE at this boundary.
      * MIRROR FAILOVER: ECMWF open-data is replicated across azure/ecmwf/aws; try the requested
        source first, then the others, so a 503 SlowDown throttle on one mirror (the live-stall
        root cause) transparently fails over instead of truncating.
    """

    if not isinstance(request, AifsEnsOpenDataRequest):
        raise TypeError("request must be AifsEnsOpenDataRequest")
    if client_factory is None:
        try:
            from ecmwf.opendata import Client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("ecmwf.opendata is required to retrieve AIFS ENS OpenData artifacts") from exc
        client_factory = Client

    final = Path(request.target_path)
    tmp = final.parent / (final.name + ".partial")
    base_kwargs = dict(request.retrieve_kwargs())
    # Requested source first, then the remaining mirrors as deterministic failover.
    sources = [request.source] + [s for s in ("azure", "ecmwf", "aws") if s != request.source]
    errors: list[str] = []
    try:
        for src in sources:
            try:
                if tmp.exists():
                    tmp.unlink()
                client = client_factory(source=src, model=request.model)
                client.retrieve(**{**base_kwargs, "target": str(tmp)})
                if not tmp.exists() or tmp.stat().st_size <= 0:
                    raise RuntimeError(f"empty/missing artifact from source={src}")
                os.replace(tmp, final)  # atomic commit — ONLY on a complete retrieve
                return final
            except Exception as exc:  # noqa: BLE001 — fail over to the next mirror
                errors.append(f"{src}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
    finally:
        if tmp.exists():
            tmp.unlink()
    raise RuntimeError(f"AIFS ENS retrieve failed on all mirrors {sources}: {errors}")
