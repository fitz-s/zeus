# Created: 2026-06-17
# Last reused or audited: 2026-09-29
# Authority basis: replacement_final_form_2026_06_09.md §1d: target DEM,
# native model height and station ground are distinct, response-bound quantities.
"""Native-grid penalty evidence; unavailable proof is not zero physical error."""
from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from src.forecast.representativeness_variance import (
    COLD_START_REPR_VARIANCE,
    ReprVarianceFit,
    representativeness_variance,
)

_GRID_TABLE_PATH = Path(__file__).resolve().parents[2] / "config" / "grid_representativeness.json"
_BINDING_KEYS = {
    "station", "model", "product", "request_policy", "raw_response_sha256",
    "requested_lat", "requested_lon",
}


@dataclass(frozen=True)
class GridRepresentativenessRead:
    status: Literal["APPLICABLE_NATIVE", "NOT_APPLICABLE_DOWNSCALED", "UNPROVEN"]
    reason: str
    variance_c2: float | None = None
    d_eff_m: float | None = None
    delta_z_m: float | None = None


class GridRepresentativenessUnavailable(ValueError):
    def __init__(self, result: GridRepresentativenessRead):
        self.result = result
        super().__init__(f"{result.status}: {result.reason}")


@lru_cache(maxsize=1)
def load_grid_representativeness(path: str | None = None) -> dict:
    """Read an evidence artifact; unreadable geometry remains missing proof."""
    p = Path(path) if path else _GRID_TABLE_PATH
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def cell_for(city: str, model: str, *, grid_table: dict | None = None) -> dict | None:
    tbl = grid_table if grid_table is not None else load_grid_representativeness()
    rec = tbl.get(city)
    if not isinstance(rec, dict) or not isinstance(rec.get("models"), dict):
        return None
    cell = rec["models"].get(model)
    return cell if isinstance(cell, dict) else None


def read_grid_representativeness(
    city: str,
    model: str,
    *,
    grid_table: dict | None = None,
    expected_binding: dict | None = None,
    raw_payload_bytes: bytes | None = None,
    fit: ReprVarianceFit = COLD_START_REPR_VARIANCE,
    coastal: bool = False,
    orography: bool = False,
    urban: bool = False,
) -> GridRepresentativenessRead:
    """Apply the existing native-only formula to exact product/response/station proof.

    expected_binding comes from the consuming product, not this artifact. Distance
    and height difference are derived, never trusted legacy d_eff/delta_z labels.
    SCOPE: only this city/model/product/response's native-only penalty. DRAIN:
    supply independently bound native/station-ground proof on the next read; no
    global city gate or collection is created here. RESET: the read is stateless,
    so matching proof returns APPLICABLE_NATIVE after a mismatched response.
    """
    cell = cell_for(city, model, grid_table=grid_table)
    if cell is None:
        return GridRepresentativenessRead("UNPROVEN", "city/model geometry absent")
    binding = cell.get("binding")
    if not isinstance(binding, dict) or not _BINDING_KEYS.issubset(binding):
        return GridRepresentativenessRead("UNPROVEN", "product/request/response binding absent")
    if expected_binding is None or binding != expected_binding or binding.get("model") != model:
        return GridRepresentativenessRead("UNPROVEN", "consuming product binding missing or mismatched")
    table = grid_table if grid_table is not None else load_grid_representativeness()
    if table[city].get("station") != binding.get("station"):
        return GridRepresentativenessRead("UNPROVEN", "city/station binding mismatched")
    digest = binding.get("raw_response_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not binding.get("station") or not binding.get("product")
            or not isinstance(binding.get("request_policy"), dict)):
        return GridRepresentativenessRead("UNPROVEN", "invalid response/product binding")
    policy = binding["request_policy"]
    if (policy.get("models") != model or policy.get("latitude") != binding["requested_lat"]
            or policy.get("longitude") != binding["requested_lon"]):
        return GridRepresentativenessRead("UNPROVEN", "single-model request geometry mismatched")
    if raw_payload_bytes is None or hashlib.sha256(raw_payload_bytes).hexdigest() != digest:
        return GridRepresentativenessRead("UNPROVEN", "exact response bytes absent or mismatched")
    try:
        response = json.loads(raw_payload_bytes)
        if not isinstance(response, dict):
            raise ValueError("not a single-model object")
        mode = "daily" if "daily" in policy else "hourly"
        requested_fields = str(policy.get(mode) or "").split(",")
        if (not any(field.startswith("temperature_2m") for field in requested_fields)
                or not isinstance(response.get(mode), dict)
                or not isinstance(response.get(f"{mode}_units"), dict)
                or any(field not in response[mode] for field in requested_fields)
                or any(response[f"{mode}_units"].get(field) != "°C"
                       for field in requested_fields if field.startswith("temperature_2m"))):
            raise ValueError("single-model temperature response shape/unit mismatch")
    except (ValueError, TypeError):
        return GridRepresentativenessRead("UNPROVEN", "response object invalid")
    if cell.get("height_role") == "default_downscaled_target_dem":
        try:
            if (float(response["latitude"]) != float(cell["response_lat"])
                    or float(response["longitude"]) != float(cell["response_lon"])
                    or response.get("elevation") != cell.get("target_dem_elevation_m")
                    or "elevation" in binding["request_policy"]):
                raise ValueError("default geometry mismatch")
        except (KeyError, TypeError, ValueError):
            return GridRepresentativenessRead("UNPROVEN", "default-downscaled response geometry mismatched")
        return GridRepresentativenessRead(
            "NOT_APPLICABLE_DOWNSCALED", "provider already downscaled; target DEM is not native height"
        )
    native_basis = cell.get("native_height_basis")
    station_basis = cell.get("station_ground_height_basis")
    if (cell.get("height_role") != "native_model_cell"
            or not isinstance(native_basis, dict) or not isinstance(station_basis, dict)
            or native_basis != binding.get("native_height_basis")
            or station_basis != binding.get("station_ground_height_basis")
            or native_basis.get("kind") != "native_model_surface"
            or native_basis.get("model") != model
            or station_basis.get("kind") != "settlement_station_ground"
            or station_basis.get("station") != binding["station"]
            or not native_basis.get("source") or not station_basis.get("source")):
        return GridRepresentativenessRead("UNPROVEN", "native/station ground height provenance absent")
    try:
        slat, slon = float(binding["requested_lat"]), float(binding["requested_lon"])
        clat, clon = float(cell["native_cell_lat"]), float(cell["native_cell_lon"])
        station_ground = float(cell["station_ground_height_m"])
        native_ground = float(cell["native_cell_elevation_m"])
        if (float(station_basis["lat"]) != slat or float(station_basis["lon"]) != slon
                or float(station_basis["elevation_m"]) != station_ground):
            raise ValueError("station ground proof mismatch")
        if (binding["request_policy"].get("elevation") != "nan"
                or float(response["latitude"]) != clat or float(response["longitude"]) != clon
                or float(response["elevation"]) != native_ground):
            raise ValueError("native response geometry mismatch")
        if not all(math.isfinite(v) for v in (slat, slon, clat, clon, station_ground, native_ground)):
            raise ValueError("nonfinite geometry")
        if not (-90 <= slat <= 90 and -90 <= clat <= 90 and -180 <= slon <= 180 and -180 <= clon <= 180):
            raise ValueError("invalid coordinates")
        dp, dl = math.radians(clat - slat), math.radians(clon - slon)
        a = math.sin(dp / 2) ** 2 + math.cos(math.radians(slat)) * math.cos(math.radians(clat)) * math.sin(dl / 2) ** 2
        d_eff = 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, a)))
        dz = station_ground - native_ground
        variance = representativeness_variance(d_eff, dz, coastal=coastal, orography=orography, urban=urban, fit=fit)
        if not math.isfinite(variance) or variance < 0:
            raise ValueError("invalid native variance")
    except (KeyError, TypeError, ValueError):
        return GridRepresentativenessRead("UNPROVEN", "native geometry invalid or incomplete")
    return GridRepresentativenessRead("APPLICABLE_NATIVE", "exact native/station-ground product proof", variance, d_eff, dz)


def sigma_repr_sq_for(city: str, model: str, **kwargs) -> float:
    """Offline scalar API: unavailable evidence must not become verified zero."""
    result = read_grid_representativeness(city, model, **kwargs)
    if result.status != "APPLICABLE_NATIVE":
        raise GridRepresentativenessUnavailable(result)
    assert result.variance_c2 is not None
    return result.variance_c2
