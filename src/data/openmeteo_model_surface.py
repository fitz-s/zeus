"""Model-specific static surface evidence, independent of forecast/mean authority.

SCOPE: an explicit provider domain, immutable static entity, and selected cell.
DRAIN: ordinary producer reacquires missing evidence before building a witness.
RESET: a causal verified asset/cell restores evidence; readers never fetch data.
Official profiles/sentinels: open-meteo/open-meteo b06f4760fd1f997e5559bb380f64c5e496b4a509.
"""

# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority basis: finite_evidence_probability_symmetry Sep29 native surface; INV-14/47.

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import time
from typing import Mapping
from uuid import uuid4

import httpx

MAX_ASSET_BYTES = 8 * 1024 * 1024
REVISION = "openmeteo_model_surface_v1"
_UPSTREAM = "b06f4760fd1f997e5559bb380f64c5e496b4a509"
_PROFILES = {
    "icon_global": ("dwd_icon", 2879, 1441, -90.0, -180.0, .125, .125),
    "icon_eu": ("dwd_icon_eu", 1377, 657, 29.5, -23.5, .0625, .0625),
    "icon_d2": ("dwd_icon_d2", 1215, 746, 43.18, -3.94, .02, .02),
    "ukmo_global_deterministic_10km": ("ukmo_global_deterministic_10km", 2560, 1920,
                                         -90.0, -180.0, 360 / 2560, 180 / 1920),
    "meteofrance_arome_france_hd": ("meteofrance_arome_france_hd", 2801, 1791,
                                     37.5, -12.0, .01, .01),
}


@dataclass(frozen=True)
class SurfaceAssetCapture:
    status: str
    reason: str | None = None
    asset: Mapping[str, object] | None = None

    def as_payload(self) -> dict[str, object]:
        return {"status": self.status, "reason": self.reason, "asset": self.asset}


class _Invalid(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _cache_root() -> Path:
    from src.config import state_path
    return state_path("static")


def _asset_url(domain: str) -> str:
    return f"https://openmeteo.s3.amazonaws.com/data/{domain}/static/HSURF.om"


def _profile(model: str) -> dict[str, object]:
    if model in _PROFILES:
        domain, nx, ny, lat, lon, dx, dy = _PROFILES[model]
        return {"upstream_revision": _UPSTREAM, "domain": domain, "grid_type": "regular",
                "nx": nx, "ny": ny, "lat_min": lat, "lon_min": lon, "dx": dx, "dy": dy}
    # Explicit temperature domains only. Seamless/mixed product names cannot
    # borrow a convenient global/static grid when their actual domain is unknown.
    if model == "ukmo_uk_deterministic_2km":
        return {"upstream_revision": _UPSTREAM, "domain": model, "grid_type": "projected",
            "nx": 1042, "ny": 970, "projection": "laea", "radius": 6371229,
            "central_longitude": -2.5, "latitude_origin": 54.9,
            "origin_x": -1158000., "origin_y": -1036000., "dx": 2000., "dy": 2000.}
    if model not in {"gfs_hrrr", "ncep_nbm_conus"}:
        raise _Invalid("MODEL_SURFACE_UNSUPPORTED")
    hrrr = model == "gfs_hrrr"
    profile = {"upstream_revision": _UPSTREAM, "domain": "ncep_hrrr_conus" if hrrr else model,
        "grid_type": "projected", "projection": "lcc", "nx": 1799 if hrrr else 2345,
        "ny": 1059 if hrrr else 1597, "radius": 6371229 if hrrr else 6371200,
        "central_longitude": -97.5 if hrrr else -95., "latitude_origin": 0.,
        "standard_parallel": 38.5 if hrrr else 25.}
    # These are the official constructors, not nominal '3km' or '2.5km'.
    lat, lon = (21.138, -122.72) if hrrr else (19.229, _float32(_float32(233.723)-360.))
    profile["origin_x"], profile["origin_y"] = _project(profile, latitude=lat, longitude=lon)
    if hrrr:
        ne_x, ne_y = _project(profile, latitude=47.8424, longitude=-60.918)
        profile["dx"] = _float32(_float32(ne_x-profile["origin_x"])/_float32(profile["nx"]-1))
        profile["dy"] = _float32(_float32(ne_y-profile["origin_y"])/_float32(profile["ny"]-1))
    else:
        profile["dx"] = profile["dy"] = _float32(2539.7)
    return profile


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: object) -> datetime:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _Invalid("MODEL_SURFACE_INVALID_CLOCK") from exc


def _safe_path(name: str, *, exists: bool = True) -> Path:
    root = _cache_root()
    # Check all existing ancestors; resolve() alone would hide a symlink.
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        raise _Invalid("MODEL_SURFACE_UNSAFE_PATH")
    if Path(name).name != name or not name or "/" in name or "\\" in name:
        raise _Invalid("MODEL_SURFACE_UNSAFE_PATH")
    path = root / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _Invalid("MODEL_SURFACE_UNSAFE_PATH")
    if exists and not path.exists():
        raise _Invalid("MODEL_SURFACE_ASSET_MISSING")
    return path


def _owned_path(value: object) -> Path:
    supplied = Path(str(value))
    path = _safe_path(supplied.name)
    if supplied != path or not supplied.is_absolute():
        raise _Invalid("MODEL_SURFACE_UNSAFE_PATH")
    return path


def _version(manifest: Mapping[str, object]) -> str:
    return _sha(_json({key: manifest[key] for key in
        ("domain", "grid_profile", "etag", "last_modified", "s3_version_id")}))


def _asset_name(manifest: Mapping[str, object]) -> str:
    return f"openmeteo_{manifest['domain']}_hsurf.{manifest['whole_sha256']}.om"


def _manifest_name(manifest: Mapping[str, object]) -> str:
    return f"{manifest['whole_sha256']}.{_version(manifest)}.{_sha(_json(manifest))}.manifest.json"


def _read_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        size = info.st_size
        if not stat.S_ISREG(info.st_mode):
            raise _Invalid("MODEL_SURFACE_UNSAFE_PATH")
        if not 0 < size <= MAX_ASSET_BYTES:
            raise _Invalid("MODEL_SURFACE_INVALID_SIZE")
        body = handle.read(MAX_ASSET_BYTES + 1)
    if len(body) != size or len(body) > MAX_ASSET_BYTES:
        raise _Invalid("MODEL_SURFACE_INVALID_SIZE")
    return body


def _decode(body: bytes, profile: Mapping[str, object], *, x: int | None = None, y: int | None = None) -> float | None:
    import fsspec
    from fsspec.implementations.memory import MemoryFileSystem
    from omfiles import OmFileReader
    # Decode exactly the immutable bytes that were hashed, not a reopened
    # filesystem path. A concurrent file A->B->A replacement cannot swap cells.
    memory = MemoryFileSystem(skip_instance_cache=True)
    name = f"/zeus-model-surface/{uuid4().hex}.om"
    memory.pipe_file(name, body)
    try:
        with OmFileReader(fsspec.core.OpenFile(memory, name, mode="rb")) as reader:
            if reader.shape != (profile["ny"], profile["nx"]):
                raise _Invalid("MODEL_SURFACE_GRID_SHAPE_MISMATCH")
            if x is not None:
                return float(reader[y:y+1, x:x+1].reshape(-1)[0])
    except _Invalid:
        raise
    except (RuntimeError, ValueError, TypeError, OSError, IndexError) as exc:
        raise _Invalid("MODEL_SURFACE_DECODE_INVALID") from exc
    finally:
        memory.rm_file(name)
    return None


def _check_manifest(manifest: Mapping[str, object], profile: Mapping[str, object]) -> None:
    if manifest["revision"] != REVISION or manifest["grid_profile"] != profile or manifest["domain"] != profile["domain"]:
        raise _Invalid("MODEL_SURFACE_MODEL_MISMATCH")
    if not re.fullmatch("[0-9a-f]{64}", str(manifest["whole_sha256"])):
        raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
    if not isinstance(manifest["etag"], str) or not manifest["etag"] or not manifest["etag"].startswith('"') or not manifest["etag"].endswith('"'):
        raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
    if isinstance(manifest["byte_size"], bool) or not isinstance(manifest["byte_size"], int) or not 0 < manifest["byte_size"] <= MAX_ASSET_BYTES:
        raise _Invalid("MODEL_SURFACE_INVALID_SIZE")
    lm, captured, recorded = (_utc(manifest[k]) for k in ("last_modified", "captured_at", "recorded_at"))
    if lm > captured or captured > recorded:
        raise _Invalid("MODEL_SURFACE_NOT_CAUSAL")


def _capture(manifest: Mapping[str, object], raw: bytes) -> SurfaceAssetCapture:
    if raw != _json(manifest):
        raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
    asset_path = _safe_path(_asset_name(manifest), exists=False)
    manifest_path = _safe_path(_manifest_name(manifest))
    return SurfaceAssetCapture("READY", asset={**manifest,
        "asset_path": str(asset_path), "manifest_path": str(manifest_path), "manifest_sha256": _sha(raw)})


def _load(asset: Mapping[str, object], profile: Mapping[str, object]) -> tuple[Mapping[str, object], bytes]:
    manifest_path = _owned_path(asset["manifest_path"])
    raw = _read_file(manifest_path)
    if _sha(raw) != asset["manifest_sha256"]:
        raise _Invalid("MODEL_SURFACE_MANIFEST_CHANGED")
    manifest = json.loads(raw)
    _check_manifest(manifest, profile)
    if raw != _json(manifest) or manifest_path.name != _manifest_name(manifest):
        raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
    for key, value in manifest.items():
        if asset.get(key) != value:
            raise _Invalid("MODEL_SURFACE_MANIFEST_CHANGED")
    path = _owned_path(asset["asset_path"])
    if path.name != _asset_name(manifest):
        raise _Invalid("MODEL_SURFACE_MODEL_MISMATCH")
    body = _read_file(path)
    if len(body) != manifest["byte_size"] or _sha(body) != manifest["whole_sha256"]:
        raise _Invalid("MODEL_SURFACE_ASSET_CHANGED")
    return manifest, body


def _scan(profile: Mapping[str, object]) -> tuple[list[SurfaceAssetCapture], list[dict[str, object]]]:
    root = _cache_root()
    if not root.exists():
        return [], []
    _safe_path("probe", exists=False)
    found = []
    invalid = []
    own_hashes = {p.name.removesuffix(".om").rsplit(".", 1)[-1]
                  for p in root.glob(f"openmeteo_{profile['domain']}_hsurf.*.om")}
    for path in root.glob("*.manifest.json"):
        identity = re.fullmatch(r"([0-9a-f]{64})\.([0-9a-f]{64})\.([0-9a-f]{64})\.manifest\.json", path.name)
        if identity is None:
            continue
        raw = None
        manifest = None
        try:
            raw = _read_file(path)
            manifest = json.loads(raw)
        except (ValueError, OSError):
            pass
        # Scope the damaged object independently of its untrusted capture
        # clock: filename asset hash / owned domain file, or the filename's
        # version digest binding the actual domain/profile fields.
        scoped = identity[1] in own_hashes
        try:
            scoped = scoped or (manifest["domain"] == profile["domain"] and
                manifest["grid_profile"] == profile and _version(manifest) == identity[2])
        except (KeyError, ValueError, TypeError):
            pass
        if not scoped:
            continue
        try:
            path = _safe_path(path.name)
            if raw is None or raw != _json(manifest) or _sha(raw) != identity[3]:
                raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
            _check_manifest(manifest, profile)
            if path.name != _manifest_name(manifest):
                raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
            capture = _capture(manifest, raw)
            found.append(capture)
        except (KeyError, ValueError, TypeError, OSError):
            invalid.append({"manifest_name": path.name, "expected_manifest_sha256": identity[3],
                "invalid_manifest_bytes_sha256": _sha(raw) if raw is not None else None,
                "quarantine_name": f"corrupt_manifest.{identity[3]}.{time.time_ns()}.json"})
    return found, invalid


def _latest(profile: Mapping[str, object], *, decision_at: datetime | None = None) -> SurfaceAssetCapture | None:
    found, invalid = _scan(profile)
    if invalid:
        raise _Invalid("MODEL_SURFACE_MANIFEST_INVALID")
    causal = [c for c in found if decision_at is None or _utc(c.asset["recorded_at"]) <= decision_at]
    if not causal:
        return None
    latest = max(causal, key=lambda c: _utc(c.asset["recorded_at"]))
    _load(latest.asset, profile)
    return latest


def read_model_surface_capture(model: str, *, decision_at: datetime | str) -> SurfaceAssetCapture:
    """Only existing local evidence at this cut; no HTTP, writes, or clock renewal."""
    try:
        profile = _profile(model)
        found = _latest(profile, decision_at=_utc(decision_at))
        if found is None:
            reason = "MODEL_SURFACE_NOT_CAUSAL" if _latest(profile) is not None else "MODEL_SURFACE_ASSET_MISSING"
            return SurfaceAssetCapture("UNAVAILABLE", reason)
        return found
    except (KeyError, ValueError, TypeError, OSError) as exc:
        return SurfaceAssetCapture("UNAVAILABLE", str(exc) if isinstance(exc, _Invalid) else "MODEL_SURFACE_CAPTURE_INVALID")


def _persist_capture(manifest: dict[str, object], body: bytes,
                     profile: Mapping[str, object], prior: SurfaceAssetCapture | None) -> SurfaceAssetCapture:
    _decode(body, profile)
    path = _safe_path(_asset_name(manifest), exists=False)
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    existing_bytes = path.exists()
    valid, invalid = _scan(profile)
    matches = [c for c in valid if c.asset["whole_sha256"] == manifest["whole_sha256"] and _version(c.asset) == _version(manifest)]
    old_capture = max(matches, key=lambda c: _utc(c.asset["recorded_at"])) if matches else None
    damaged = False
    bad_hash = None
    if existing_bytes:
        try:
            cached_body = _read_file(path)
            damaged = cached_body != body
            bad_hash = _sha(cached_body) if damaged else None
        except _Invalid as exc:
            if str(exc) != "MODEL_SURFACE_INVALID_SIZE":
                raise
            damaged = True
    if damaged:
        # The exact domain/SHA regular-file path is already checked. Only a
        # real full200 with validated bytes/shape permits quarantine. Verified
        # manifests retain their clocks; orphan evidence requires a new receipt.
        quarantine = _safe_path(f"{path.name}.corrupt.{bad_hash or 'invalid_size'}.{time.time_ns()}", exists=False)
        path.rename(quarantine)
    if not path.exists():
        with path.open("xb") as handle:
            handle.write(body)
    # A real A->B->A transition must not reuse an earlier A possession merely
    # because the server has recycled all A headers and bytes.
    transition = prior is not None and (
        prior.asset["whole_sha256"] != manifest["whole_sha256"] or _version(prior.asset) != _version(manifest))
    if old_capture is not None and not transition and not invalid:
        return old_capture
    if not invalid and old_capture is None and prior is not None and _version(prior.asset) == _version(manifest) and prior.asset["whole_sha256"] == manifest["whole_sha256"]:
        return prior  # Valid recovery receipt is reused on ordinary same-object 200.
    if invalid or (existing_bytes and prior is None) or (old_capture is not None and transition):
        manifest["reacquisition"] = {
            "kind": "OBJECT_TRANSITION" if old_capture is not None and not invalid else "EVIDENCE_REACQUIRED",
            "recovery_of_expected_manifest_identity": {"whole_sha256": manifest["whole_sha256"],
                "version_digest": _version(manifest), "invalid_references": invalid},
            "previous_capture_manifest_sha256": prior.asset["manifest_sha256"] if prior else None,
        }
    manifest["recorded_at"] = _now().isoformat()
    _check_manifest(manifest, profile)
    raw = _json(manifest)
    manifest_path = _safe_path(_manifest_name(manifest), exists=False)
    if manifest_path.exists():
        if _read_file(manifest_path) != raw:
            raise _Invalid("MODEL_SURFACE_MANIFEST_CHANGED")
    else:
        with manifest_path.open("xb") as handle:
            handle.write(raw)
    for item in invalid:
        damaged = _safe_path(item["manifest_name"])
        quarantine = _safe_path(item["quarantine_name"], exists=False)
        damaged.rename(quarantine)
    return _capture(manifest, raw)


@contextmanager
def _static_response(url: str, prior: SurfaceAssetCapture | None, deadline: float):
    headers = {"If-None-Match": str(prior.asset["etag"])} if prior else {}
    with httpx.stream("GET", url, headers=headers, timeout=min(20, deadline-time.monotonic()),
                      follow_redirects=False) as response:
        if response.status_code != 304:
            yield response
            return
        same_version = False
        if prior is not None:
            try:
                lm = parsedate_to_datetime(response.headers["last-modified"])
                same_version = (lm.tzinfo is not None and lm.astimezone(UTC) == _utc(prior.asset["last_modified"])
                    and response.headers["etag"] == prior.asset["etag"]
                    and response.headers.get("x-amz-version-id") == prior.asset["s3_version_id"])
            except (KeyError, ValueError, TypeError):
                pass
        if same_version:
            yield response
            return
        # Equal bytes/ETag do not imply the same publication epoch. A 304
        # missing identity headers is likewise insufficient for that claim.
        response.close()
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise _Invalid("MODEL_SURFACE_DEADLINE")
    with httpx.stream("GET", url, timeout=min(20, remaining), follow_redirects=False) as response:
        if response.status_code == 304:
            raise _Invalid("MODEL_SURFACE_UNEXPECTED_304")
        yield response


def ensure_model_surface(model: str, *, deadline: float | None = None) -> SurfaceAssetCapture:
    """Normal producer-only conditional/full GET. Cache hits never renew possession."""
    try:
        profile = _profile(model)
        try:
            prior = _latest(profile, decision_at=_now())
        except _Invalid:
            prior = None  # Recovery requires unconditional full200, never 304.
        deadline = deadline if deadline is not None else time.monotonic() + 20
        timeout = min(20.0, deadline - time.monotonic())
        if timeout <= 0:
            raise _Invalid("MODEL_SURFACE_DEADLINE")
        with _static_response(_asset_url(str(profile["domain"])), prior, deadline) as response:
            if response.status_code == 304:
                if prior is None:
                    raise _Invalid("MODEL_SURFACE_UNEXPECTED_304")
                return prior
            if response.status_code != 200:
                raise _Invalid("MODEL_SURFACE_HTTP_UNAVAILABLE")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise _Invalid("MODEL_SURFACE_HTTP_INVALID")
            if response.headers.get("content-length") is not None and not 0 < int(response.headers["content-length"]) <= MAX_ASSET_BYTES:
                raise _Invalid("MODEL_SURFACE_INVALID_SIZE")
            etag = response.headers.get("etag", "")
            modified = response.headers.get("last-modified", "")
            try:
                lm = parsedate_to_datetime(modified)
                if lm.tzinfo is None:
                    raise ValueError("missing timezone")
            except (ValueError, TypeError) as exc:
                raise _Invalid("MODEL_SURFACE_INVALID_CLOCK") from exc
            parts = []
            size = 0
            for block in response.iter_raw(chunk_size=65536):
                size += len(block)
                if size > MAX_ASSET_BYTES:
                    raise _Invalid("MODEL_SURFACE_INVALID_SIZE")
                if deadline is not None and time.monotonic() > deadline:
                    raise _Invalid("MODEL_SURFACE_DEADLINE")
                parts.append(block)
            body = b"".join(parts)
            if response.headers.get("content-length") is not None and int(response.headers["content-length"]) != size:
                raise _Invalid("MODEL_SURFACE_HTTP_INVALID")
            captured = _now()
            manifest = {"revision": REVISION, "domain": profile["domain"], "grid_profile": profile,
                "etag": etag, "last_modified": lm.astimezone(UTC).isoformat(),
                "s3_version_id": response.headers.get("x-amz-version-id"),
                "whole_sha256": _sha(body), "byte_size": size,
                "captured_at": captured.isoformat(), "recorded_at": _now().isoformat()}
        _check_manifest(manifest, profile)
        return _persist_capture(manifest, body, profile, prior)
    except (KeyError, ValueError, TypeError, OSError, httpx.HTTPError) as exc:
        return SurfaceAssetCapture("UNAVAILABLE", str(exc) if isinstance(exc, _Invalid) else "MODEL_SURFACE_CAPTURE_INVALID")


def _float32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


@lru_cache(maxsize=8)
def _float_math_function(name):
    import ctypes
    import ctypes.util
    # Foundation's Float trig/power overloads use these system float functions.
    # NumPy's SIMD trig and nearest-rounded np.pi differ by ULPs; those are not
    # the original upstream Float coordinate contract.
    try:
        library = ctypes.util.find_library("m")
        if library is None:
            raise _Invalid("MODEL_SURFACE_FLOAT_MATH_UNAVAILABLE")
        function = getattr(ctypes.CDLL(library), name)
    except (AttributeError, OSError) as exc:
        raise _Invalid("MODEL_SURFACE_FLOAT_MATH_UNAVAILABLE") from exc
    function.argtypes = [ctypes.c_float] * (2 if name in {"powf", "atan2f"} else 1)
    function.restype = ctypes.c_float
    return function


def _project(profile, *, latitude=None, longitude=None, x=None, y=None):
    """Pinned Open-Meteo spherical Float formulas, not an EPSG approximation."""
    import numpy as np
    f = np.float32
    pi, one, two = f(3.141592502593994), f(1), f(2)  # Swift Float.pi, bits1078530010.
    def fm(name, *args):
        return f(_float_math_function(name)(*(float(arg) for arg in args)))
    sin, cos, tan = (lambda value: fm("sinf", value)), (lambda value: fm("cosf", value)), (lambda value: fm("tanf", value))
    power = lambda base, exponent: fm("powf", base, exponent)
    radians = lambda value: f(value)*pi/f(180)
    degrees = lambda value: value*f(180)/pi
    lam0 = radians(profile["central_longitude"])
    phi0 = radians(profile["latitude_origin"])
    radius = f(profile["radius"])
    with np.errstate(all="ignore"):
        if profile["projection"] == "lcc":
            phi1 = radians(profile["standard_parallel"])
            n = sin(phi1)  # These two official profiles have phi1 == phi2.
            factor = cos(phi1)*power(tan(pi/f(4)+phi1/two), n)/n
            rho0 = factor/power(tan(pi/f(4)+phi0/two), n)
            if x is None:
                phi, lam = radians(latitude), radians(longitude)
                theta = n*(lam-lam0)
                rho = factor/power(tan(pi/f(4)+phi/two), n)
                a, b = radius*rho*sin(theta), radius*(rho0-rho*cos(theta))
            else:
                xx, yy = f(x)/radius, f(y)/radius
                theta = fm("atan2f", xx, rho0-yy)
                rho = fm("sqrtf", power(xx, two)+power(rho0-yy, two))
                phi = two*fm("atanf", power(factor/rho, one/n))-pi/two
                lam = lam0+theta/n
                a, b = degrees(phi), degrees(lam)
                if b > f(180):
                    b -= f(360)
        else:
            if x is None:
                phi, lam = radians(latitude), radians(longitude)
                k = fm("sqrtf", two/(one+sin(phi0)*sin(phi)+cos(phi0)*cos(phi)*cos(lam-lam0)))
                a = radius*k*cos(phi)*sin(lam-lam0)
                b = radius*k*(cos(phi0)*sin(phi)-sin(phi0)*cos(phi)*cos(lam-lam0))
            else:
                xx, yy = f(x)/radius, f(y)/radius
                p = fm("sqrtf", xx*xx+yy*yy)
                if p == 0:
                    return float(degrees(phi0)), float(f(profile["central_longitude"]))
                c = two*fm("asinf", p/two)
                phi = fm("asinf", cos(c)*sin(phi0)+yy*sin(c)*cos(phi0)/p)
                lam = lam0+fm("atanf", xx*sin(c)/(p*cos(phi0)*cos(c)-yy*sin(phi0)*sin(c)))
                a, b = degrees(phi), degrees(lam)
    return float(a), float(b)


def _cell(profile: Mapping[str, object], lat: object, lon: object) -> dict[str, object]:
    if isinstance(lat, bool) or isinstance(lon, bool):
        raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
    lat, lon = float(lat), float(lon)
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
    if profile["grid_type"] == "projected":
        px, py = _project(profile, latitude=lat, longitude=lon)
        indices = []
        for value, axis, count in ((px, "x", profile["nx"]), (py, "y", profile["ny"])):
            q = _float32(_float32(value-profile[f"origin_{axis}"])/profile["dx" if axis == "x" else "dy"])
            if not math.isfinite(q):
                raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
            index = math.floor(q+.5) if q >= 0 else math.ceil(q-.5)
            if not 0 <= index < count:
                raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
            indices.append(index)
        x, y = indices
        selected_lat, selected_lon = _project(profile,
            x=_float32(_float32(_float32(x)*profile["dx"])+profile["origin_x"]),
            y=_float32(_float32(_float32(y)*profile["dy"])+profile["origin_y"]))
        selected_lon = _float32(math.fmod(_float32(selected_lon+180), 360)-180)
        import numpy as np
        for actual, selected in ((lat, selected_lat), (lon, selected_lon)):
            # At most one original Float coordinate quantum, not a spatial
            # nearest-cell allowance. Half-cell/request coordinates fail.
            quantum = abs(float(np.spacing(np.float32(selected))))
            if not math.isfinite(selected) or abs(actual-selected) > quantum:
                raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
        return {"x": x, "y": y, "selected_latitude": selected_lat, "selected_longitude": selected_lon}
    coords = []
    indices = []
    for value, origin, step, count in ((lon, profile["lon_min"], profile["dx"], profile["nx"]),
                                      (lat, profile["lat_min"], profile["dy"], profile["ny"])):
        q = _float32(_float32(_float32(value) - _float32(origin)) / _float32(step))
        i = math.floor(q + .5) if q >= 0 else math.ceil(q - .5)
        if not 0 <= i < count:
            raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
        coord = _float32(_float32(origin) + _float32(_float32(i) * _float32(step)))
        if not math.isclose(coord, value, rel_tol=0, abs_tol=1e-5):
            raise _Invalid("MODEL_SURFACE_SELECTED_CELL_MISMATCH")
        indices.append(i)
        coords.append(coord)
    return {"x": indices[0], "y": indices[1], "selected_latitude": coords[1], "selected_longitude": coords[0]}


def _geometry(profile: Mapping[str, object], body: bytes, lat: object, lon: object) -> dict[str, object]:
    cell = _cell(profile, lat, lon)
    height = _decode(body, profile, x=cell["x"], y=cell["y"])
    if math.isnan(height):
        raise _Invalid("MODEL_SURFACE_NO_DATA")
    if height <= -999:
        raise _Invalid("MODEL_SURFACE_SEA")
    if height >= 9999 or not math.isfinite(height):
        raise _Invalid("MODEL_SURFACE_NO_HEIGHT")
    return {"domain": profile["domain"], "grid_profile": profile, **cell,
            "native_surface": "LAND", "native_grid_elevation_m": height}


def model_surface_witness(model: str, *, selected_latitude: float, selected_longitude: float,
        body_captured_at: datetime | str, asset_capture: SurfaceAssetCapture | Mapping[str, object]) -> dict[str, object]:
    try:
        profile = _profile(model)
        payload = asset_capture.as_payload() if isinstance(asset_capture, SurfaceAssetCapture) else asset_capture
        if payload["status"] != "READY":
            raise _Invalid(str(payload.get("reason") or "MODEL_SURFACE_ASSET_MISSING"))
        asset = payload["asset"]
        manifest, body = _load(asset, profile)
        if _utc(manifest["last_modified"]) > _utc(body_captured_at):
            raise _Invalid("MODEL_SURFACE_EPOCH_MISMATCH")
        return {"revision": REVISION, "status": "VERIFIED", "reason": None, "model": model,
                "body_captured_at": _utc(body_captured_at).isoformat(),
                "geometry": _geometry(profile, body, selected_latitude, selected_longitude),
                "asset_audit": dict(asset)}
    except (KeyError, ValueError, TypeError, OSError) as exc:
        return {"revision": REVISION, "status": "UNPROVEN", "model": model,
                "reason": str(exc) if isinstance(exc, _Invalid) else "MODEL_SURFACE_WITNESS_INVALID"}


def validate_model_surface_witness(proof: Mapping[str, object], *, model: str,
        selected_latitude: float, selected_longitude: float,
        body_captured_at: datetime | str, decision_at: datetime | str) -> str | None:
    """Read-only local evidence replay: no network, no cache renewal."""
    try:
        if proof["model"] != model:
            raise _Invalid("MODEL_SURFACE_MODEL_MISMATCH")
        if proof["status"] != "VERIFIED":
            return str(proof.get("reason") or "MODEL_SURFACE_UNPROVEN")
        if proof["revision"] != REVISION or _utc(proof["body_captured_at"]) != _utc(body_captured_at):
            raise _Invalid("MODEL_SURFACE_WITNESS_INVALID")
        profile = _profile(model)
        manifest, entity = _load(proof["asset_audit"], profile)
        decision, body = _utc(decision_at), _utc(body_captured_at)
        if body > decision or _utc(manifest["recorded_at"]) > decision:
            raise _Invalid("MODEL_SURFACE_NOT_CAUSAL")
        if _utc(manifest["last_modified"]) > body:
            raise _Invalid("MODEL_SURFACE_EPOCH_MISMATCH")
        if proof["geometry"] != _geometry(profile, entity, selected_latitude, selected_longitude):
            raise _Invalid("MODEL_SURFACE_CELL_CHANGED")
        return None
    except (KeyError, ValueError, TypeError, OSError) as exc:
        return str(exc) if isinstance(exc, _Invalid) else "MODEL_SURFACE_WITNESS_INVALID"


def model_surface_stable_projection(proof: Mapping[str, object]) -> Mapping[str, object] | None:
    return dict(proof["geometry"]) if proof.get("status") == "VERIFIED" else None
