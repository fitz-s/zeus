"""Raw forecast artifact manifest helpers for replacement forecast input provenance."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from src.data.forecast_source_registry import REPLACEMENT_FORECAST_PRODUCTS


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


class UnregisteredRawForecastArtifactIdentityError(ValueError):
    """The manifest names a product no longer in the live replacement registry."""


class UnsupportedRawForecastArtifactManifestFieldsError(ValueError):
    """The manifest carries top-level fields outside the current schema."""

    def __init__(self, fields: set[str]) -> None:
        self.fields = frozenset(fields)
        super().__init__(
            "raw forecast artifact manifest has unsupported fields: "
            f"{sorted(self.fields)}"
        )


def _parse_utc(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _require_identity(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if _FORBIDDEN_TRANSCRIPT_ALIAS in normalized.lower():
        raise ValueError(f"{field_name} must use the full product identity, not transcript shorthand")
    return normalized


def _replacement_raw_artifact_product_by_data_version() -> dict[str, tuple[str, str]]:
    allowed_classes = {"ai_ensemble", "ifs_ens_direct_model_output", "deterministic_spatial_anchor"}
    mapping: dict[str, tuple[str, str]] = {}
    for label, product in REPLACEMENT_FORECAST_PRODUCTS.items():
        if label == "B0" or product.product_class not in allowed_classes:
            continue
        for data_version in product.data_versions:
            mapping[data_version] = (product.source_id, product.product_id)
    return mapping


def _validate_replacement_raw_artifact_identity(
    *,
    source_id: str,
    product_id: str,
    data_version: str,
) -> None:
    expected = _replacement_raw_artifact_product_by_data_version().get(data_version)
    if expected is None:
        raise UnregisteredRawForecastArtifactIdentityError(
            "raw forecast artifact data_version is not a registered replacement raw product"
        )
    expected_source_id, expected_product_id = expected
    if source_id != expected_source_id or product_id != expected_product_id:
        raise ValueError("raw forecast artifact source/product identity does not match data_version")


def sha256_file(path: Path | str) -> str:
    artifact_path = Path(path)
    digest = hashlib.sha256()
    with artifact_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RawForecastArtifactManifest:
    """Immutable per-file evidence for downloaded forecast inputs.

    This is intentionally filesystem-only. It is not a readiness record, source_run
    row, calibration artifact, or trading authority.
    """

    source_id: str
    product_id: str
    data_version: str
    artifact_path: str
    sha256: str
    byte_size: int
    source_cycle_time: datetime
    source_available_at: datetime
    captured_at: datetime
    request_url: str
    request_params: Mapping[str, Any]
    training_allowed: bool = False
    product_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _require_identity(self.source_id, field_name="source_id"))
        object.__setattr__(self, "product_id", _require_identity(self.product_id, field_name="product_id"))
        object.__setattr__(self, "data_version", _require_identity(self.data_version, field_name="data_version"))
        _validate_replacement_raw_artifact_identity(
            source_id=self.source_id,
            product_id=self.product_id,
            data_version=self.data_version,
        )
        object.__setattr__(self, "source_cycle_time", _parse_utc(self.source_cycle_time, field_name="source_cycle_time"))
        object.__setattr__(self, "source_available_at", _parse_utc(self.source_available_at, field_name="source_available_at"))
        object.__setattr__(self, "captured_at", _parse_utc(self.captured_at, field_name="captured_at"))
        if not self.artifact_path:
            raise ValueError("artifact_path must be set")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("sha256 must be a lowercase 64-character hex digest")
        if self.byte_size <= 0:
            raise ValueError("byte_size must be positive")
        if not self.request_url:
            raise ValueError("request_url must be set")
        if not isinstance(self.request_params, Mapping) or not self.request_params:
            raise ValueError("request_params must be a non-empty mapping")
        if self.source_available_at < self.source_cycle_time:
            raise ValueError("source_available_at cannot precede source_cycle_time")
        if self.captured_at < self.source_available_at:
            raise ValueError("captured_at cannot precede source_available_at")
        if self.training_allowed:
            raise ValueError("raw forecast artifacts default to training_allowed=false")

    @classmethod
    def from_file(
        cls,
        artifact_path: Path | str,
        *,
        source_id: str,
        product_id: str,
        data_version: str,
        source_cycle_time: datetime | str,
        source_available_at: datetime | str,
        captured_at: datetime | str,
        request_url: str,
        request_params: Mapping[str, Any],
        product_metadata: Mapping[str, Any] | None = None,
    ) -> "RawForecastArtifactManifest":
        path = Path(artifact_path)
        return cls(
            source_id=source_id,
            product_id=product_id,
            data_version=data_version,
            artifact_path=str(path),
            sha256=sha256_file(path),
            byte_size=path.stat().st_size,
            source_cycle_time=source_cycle_time,
            source_available_at=source_available_at,
            captured_at=captured_at,
            request_url=request_url,
            request_params=dict(request_params),
            product_metadata=dict(product_metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("source_cycle_time", "source_available_at", "captured_at"):
            payload[key] = payload[key].astimezone(UTC).isoformat()
        return payload

    def verify_artifact(self, *, root: Path | str | None = None) -> None:
        artifact_path = Path(self.artifact_path)
        if root is not None and not artifact_path.is_absolute():
            artifact_path = Path(root) / artifact_path
        if not artifact_path.exists():
            raise FileNotFoundError(str(artifact_path))
        actual_size = artifact_path.stat().st_size
        if actual_size != self.byte_size:
            raise ValueError(f"artifact byte_size mismatch: expected {self.byte_size}, got {actual_size}")
        actual_sha = sha256_file(artifact_path)
        if actual_sha != self.sha256:
            raise ValueError("artifact sha256 mismatch")

    def manifest_sha256(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawForecastArtifactInventoryReport:
    status: str
    reason_codes: tuple[str, ...]
    manifest_count: int
    manifest_bytes_total: int
    filesystem_bytes_total: int
    class_counts: Mapping[str, int]
    manifest_bytes_by_class: Mapping[str, int]
    filesystem_bytes_by_class: Mapping[str, int]
    duplicate_artifact_paths: tuple[str, ...]
    duplicate_manifest_hashes: tuple[str, ...]
    missing_artifact_paths: tuple[str, ...]
    mismatched_artifact_paths: tuple[str, ...]
    expected_manifest_bytes_by_class: Mapping[str, int]

    @property
    def valid(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "manifest_count": self.manifest_count,
            "manifest_bytes_total": self.manifest_bytes_total,
            "filesystem_bytes_total": self.filesystem_bytes_total,
            "class_counts": dict(self.class_counts),
            "manifest_bytes_by_class": dict(self.manifest_bytes_by_class),
            "filesystem_bytes_by_class": dict(self.filesystem_bytes_by_class),
            "duplicate_artifact_paths": list(self.duplicate_artifact_paths),
            "duplicate_manifest_hashes": list(self.duplicate_manifest_hashes),
            "missing_artifact_paths": list(self.missing_artifact_paths),
            "mismatched_artifact_paths": list(self.mismatched_artifact_paths),
            "expected_manifest_bytes_by_class": dict(self.expected_manifest_bytes_by_class),
            "valid": self.valid,
        }


def _artifact_class(manifest: RawForecastArtifactManifest) -> str:
    explicit = manifest.product_metadata.get("artifact_class")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    return manifest.product_id


def audit_raw_forecast_artifact_inventory(
    manifests: list[RawForecastArtifactManifest] | tuple[RawForecastArtifactManifest, ...],
    *,
    root: Path | str | None = None,
    expected_manifest_bytes_by_class: Mapping[str, int] | None = None,
) -> RawForecastArtifactInventoryReport:
    """Audit a raw replacement artifact cohort for byte/hash inventory drift."""

    if not manifests:
        return RawForecastArtifactInventoryReport(
            status="BLOCK",
            reason_codes=("RAW_FORECAST_ARTIFACT_INVENTORY_EMPTY",),
            manifest_count=0,
            manifest_bytes_total=0,
            filesystem_bytes_total=0,
            class_counts={},
            manifest_bytes_by_class={},
            filesystem_bytes_by_class={},
            duplicate_artifact_paths=(),
            duplicate_manifest_hashes=(),
            missing_artifact_paths=(),
            mismatched_artifact_paths=(),
            expected_manifest_bytes_by_class=dict(expected_manifest_bytes_by_class or {}),
        )

    artifact_paths = [manifest.artifact_path for manifest in manifests]
    manifest_hashes = [manifest.manifest_sha256() for manifest in manifests]
    duplicate_paths = tuple(sorted(path for path, count in Counter(artifact_paths).items() if count > 1))
    duplicate_hashes = tuple(sorted(hash_value for hash_value, count in Counter(manifest_hashes).items() if count > 1))
    class_counts: Counter[str] = Counter()
    manifest_bytes_by_class: defaultdict[str, int] = defaultdict(int)
    filesystem_bytes_by_class: defaultdict[str, int] = defaultdict(int)
    missing_paths: list[str] = []
    mismatched_paths: list[str] = []
    filesystem_total = 0

    for manifest in manifests:
        artifact_class = _artifact_class(manifest)
        class_counts[artifact_class] += 1
        manifest_bytes_by_class[artifact_class] += int(manifest.byte_size)
        artifact_path = Path(manifest.artifact_path)
        if root is not None and not artifact_path.is_absolute():
            artifact_path = Path(root) / artifact_path
        if not artifact_path.exists():
            missing_paths.append(manifest.artifact_path)
            continue
        actual_size = artifact_path.stat().st_size
        filesystem_total += actual_size
        filesystem_bytes_by_class[artifact_class] += actual_size
        try:
            manifest.verify_artifact(root=root)
        except (FileNotFoundError, ValueError):
            mismatched_paths.append(manifest.artifact_path)

    manifest_total = sum(int(manifest.byte_size) for manifest in manifests)
    expected = dict(expected_manifest_bytes_by_class or {})
    reasons: list[str] = []
    if duplicate_paths:
        reasons.append("RAW_FORECAST_ARTIFACT_DUPLICATE_PATH")
    if duplicate_hashes:
        reasons.append("RAW_FORECAST_ARTIFACT_DUPLICATE_MANIFEST")
    if missing_paths:
        reasons.append("RAW_FORECAST_ARTIFACT_MISSING_FILE")
    if mismatched_paths:
        reasons.append("RAW_FORECAST_ARTIFACT_HASH_OR_SIZE_MISMATCH")
    if filesystem_total != manifest_total:
        reasons.append("RAW_FORECAST_ARTIFACT_TOTAL_BYTES_MISMATCH")
    for artifact_class, expected_bytes in expected.items():
        if manifest_bytes_by_class.get(artifact_class, 0) != int(expected_bytes):
            reasons.append("RAW_FORECAST_ARTIFACT_EXPECTED_CLASS_BYTES_MISMATCH")
            break

    return RawForecastArtifactInventoryReport(
        status="BLOCK" if reasons else "PASS",
        reason_codes=tuple(dict.fromkeys(reasons or ("RAW_FORECAST_ARTIFACT_INVENTORY_PASS",))),
        manifest_count=len(manifests),
        manifest_bytes_total=manifest_total,
        filesystem_bytes_total=filesystem_total,
        class_counts=dict(sorted(class_counts.items())),
        manifest_bytes_by_class=dict(sorted(manifest_bytes_by_class.items())),
        filesystem_bytes_by_class=dict(sorted(filesystem_bytes_by_class.items())),
        duplicate_artifact_paths=duplicate_paths,
        duplicate_manifest_hashes=duplicate_hashes,
        missing_artifact_paths=tuple(sorted(missing_paths)),
        mismatched_artifact_paths=tuple(sorted(mismatched_paths)),
        expected_manifest_bytes_by_class=expected,
    )


def write_manifest(manifest: RawForecastArtifactManifest, target_path: Path | str) -> None:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_manifest(path: Path | str) -> RawForecastArtifactManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("raw forecast artifact manifest must decode to an object")
    known = {item.name for item in fields(RawForecastArtifactManifest)}
    unknown = set(raw) - known
    if unknown:
        raise UnsupportedRawForecastArtifactManifestFieldsError(unknown)
    return RawForecastArtifactManifest(**raw)


def manifest_matches_artifact(
    manifest: RawForecastArtifactManifest, *, root: Path | str | None = None
) -> bool:
    """True iff the on-disk artifact matches the manifest's byte_size AND sha256.

    A missing artifact returns False (it does not match). Callers that must
    distinguish missing-vs-drifted use verify_artifact, which raises FileNotFoundError
    for the missing case.
    """
    artifact_path = Path(manifest.artifact_path)
    if root is not None and not artifact_path.is_absolute():
        artifact_path = Path(root) / artifact_path
    if not artifact_path.exists():
        return False
    if artifact_path.stat().st_size != manifest.byte_size:
        return False
    return sha256_file(artifact_path) == manifest.sha256


def repin_manifest_from_file(
    manifest: RawForecastArtifactManifest, *, root: Path | str | None = None
) -> RawForecastArtifactManifest:
    """Rebuild byte_size + sha256 from the CURRENT artifact bytes, preserving every
    other manifest field.

    Use when a present, valid artifact was rewritten AFTER its manifest was pinned -
    e.g. the trailing ``"\\n"`` that ``_write_json`` appends (added 2026-06-24, commit
    e2cd7a9bc): the pinned manifest then records the pre-rewrite size, so
    ``verify_artifact`` hard-fails on the benign stat/sha drift and blocks
    materialization. Re-pinning from the current bytes heals that without touching the
    payload. Raises FileNotFoundError when the artifact is absent - a MISSING input is a
    distinct, non-benign condition the caller must handle (never silently re-pinned).
    """
    artifact_path = Path(manifest.artifact_path)
    if root is not None and not artifact_path.is_absolute():
        artifact_path = Path(root) / artifact_path
    if not artifact_path.exists():
        raise FileNotFoundError(str(artifact_path))
    return replace(
        manifest,
        byte_size=artifact_path.stat().st_size,
        sha256=sha256_file(artifact_path),
    )


def write_manifest_to_db(
    conn: sqlite3.Connection,
    manifest: RawForecastArtifactManifest,
    *,
    root: Path | str | None = None,
    verify_artifact: bool = True,
    repin_on_drift: bool = False,
) -> int:
    """Persist a verified raw forecast artifact manifest into forecast DB.

    The manifest is input provenance, not a trade-authority carrier. The returned
    artifact_id is the only value downstream materializers should use when linking
    derived rows to raw files.

    This registry accepts immutable raw products only. Same-natural-identity
    reuse preserves the original bytes, request and first-possession clocks;
    a later acquisition/proof is an append-only dependency, not an UPDATE here.

    ``repin_on_drift`` (default off): when the on-disk artifact is PRESENT and valid but
    its byte_size/sha256 drifted from ``manifest`` (a benign rewrite after pinning), the
    manifest is re-pinned from the current bytes before verify+write instead of aborting.
    A MISSING artifact is never re-pinned - it falls through to ``verify_artifact`` which
    raises, preserving the corruption/absence guard.
    """

    if not isinstance(manifest, RawForecastArtifactManifest):
        raise TypeError("manifest must be RawForecastArtifactManifest")
    if repin_on_drift and not manifest_matches_artifact(manifest, root=root):
        artifact_path = Path(manifest.artifact_path)
        resolved = (
            artifact_path
            if (root is None or artifact_path.is_absolute())
            else Path(root) / artifact_path
        )
        if resolved.exists():
            manifest = repin_manifest_from_file(manifest, root=root)
    if verify_artifact:
        manifest.verify_artifact(root=root)
    payload = manifest.to_dict()
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256,
            byte_size, request_url, request_params_json,
            artifact_metadata_json, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, product_id, data_version, source_cycle_time, sha256)
        DO NOTHING
        """,
        (
            manifest.source_id,
            manifest.product_id,
            manifest.data_version,
            payload["source_cycle_time"],
            payload["source_available_at"],
            payload["captured_at"],
            manifest.artifact_path,
            manifest.sha256,
            int(manifest.byte_size),
            manifest.request_url,
            json.dumps(dict(manifest.request_params), sort_keys=True, separators=(",", ":"), default=str),
            json.dumps(dict(manifest.product_metadata), sort_keys=True, separators=(",", ":"), default=str),
            1 if manifest.training_allowed else 0,
        ),
    )
    row = conn.execute(
        """
        SELECT artifact_id FROM raw_forecast_artifacts
        WHERE source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND source_cycle_time = ?
          AND sha256 = ?
        """,
        (
            manifest.source_id,
            manifest.product_id,
            manifest.data_version,
            payload["source_cycle_time"],
            manifest.sha256,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("raw forecast artifact manifest DB write failed")
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["artifact_id"])


ANCHOR_LOCAL_PROOF_REVISION = "openmeteo_anchor_local_proof_possession_v1"
_PROOF_CLOCK_SQL = "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')"
_LOCAL_PROOF_MAX_BYTES = 1024 * 1024
_LOCAL_BODY_MAX_BYTES = 32 * 1024 * 1024
_LOCAL_PROOF_BATCH_ROWS = 128


@dataclass(frozen=True)
class AnchorLocalProofEvidence:
    """Resolved local evidence, not q, HTTP freshness, or a new source issue."""

    original_body_artifact: Mapping[str, Any]
    proof_artifact_id: int
    proof_sha256: str
    owned_body: Mapping[str, Any]
    precision_metadata: Mapping[str, Any]
    scope: Mapping[str, str]
    local_possessed_at: datetime
    recorded_at: datetime


def _proof_error(reason: str) -> ValueError:
    return ValueError(f"anchor_local_proof:{reason}")


def _proof_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("anchor_local_proof:deadline_expired")


def _proof_json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise _proof_error("invalid_json") from exc


def _proof_rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _proof_original(conn: sqlite3.Connection, artifact_id: int) -> dict[str, Any]:
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id <= 0:
        raise _proof_error("invalid_original_id")
    rows = _proof_rows_as_dicts(conn.execute(
        "SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,),
    ))
    if not rows:
        raise _proof_error("original_missing")
    body = rows[0]
    from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, LOW_DATA_VERSION, PRODUCT_ID, SOURCE_ID
    if (body["source_id"], body["product_id"]) != (SOURCE_ID, PRODUCT_ID) or body["data_version"] not in {HIGH_DATA_VERSION, LOW_DATA_VERSION}:
        raise _proof_error("unsupported_original")
    try:
        if any(not isinstance(body[key], str) or len(body[key].encode()) > _LOCAL_PROOF_MAX_BYTES
               for key in ("artifact_metadata_json", "request_params_json")):
            raise _proof_error("original_descriptor_byte_budget")
        clocks = [_parse_utc(body[key], field_name=key) for key in
                  ("source_cycle_time", "source_available_at", "captured_at", "recorded_at")]
        metadata = json.loads(body["artifact_metadata_json"])
        params = json.loads(body["request_params_json"])
        if not isinstance(metadata, dict) or not isinstance(params, dict) or not isinstance(params.get("run"), str):
            raise _proof_error("invalid_original")
        expected_metric = "high" if body["data_version"] == HIGH_DATA_VERSION else "low"
        request_run = datetime.fromisoformat(params["run"].replace("Z", "+00:00"))
        if request_run.tzinfo is None:
            # The provider's run wire parameter is UTC without a suffix. This
            # interprets request grammar, never repairs an original source clock.
            request_run = request_run.replace(tzinfo=UTC)
        if (not isinstance(metadata, dict) or not isinstance(params, dict) or not params
                or not isinstance(metadata.get("city"), str) or not metadata["city"].strip()
                or date.fromisoformat(metadata["target_date"]).isoformat() != metadata["target_date"]
                or metadata.get("metric") != expected_metric or body["training_allowed"] != 0
                or clocks != sorted(clocks) or request_run.astimezone(UTC) != clocks[0]
                or params.get("models") != "ecmwf_ifs" or not isinstance(params.get("timezone"), str)
                or not params["timezone"] or "temperature_2m" not in str(params.get("hourly", "")).split(",")
                or any(isinstance(params[key], bool) or not isinstance(params[key], (int, float))
                       or not math.isfinite(params[key]) or abs(params[key]) > bound
                       for key, bound in (("latitude", 90), ("longitude", 180)))):
            raise _proof_error("invalid_original")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _proof_error("invalid_original") from exc
    return body


def _proof_scope(body: Mapping[str, Any]) -> dict[str, str]:
    metadata = json.loads(body["artifact_metadata_json"])
    return {key: metadata[key] for key in ("city", "target_date", "metric")}


def _proof_path(path: str, root: Path | str | None) -> Path:
    result = Path(path)
    if not result.is_absolute():
        if root is None:
            raise _proof_error("relative_path_without_root")
        result = Path(root) / result
    return result.resolve(strict=True)


def _proof_bytes(path: Path, expected_sha: str, expected_size: int, *, limit: int,
                 deadline: float | None) -> bytes:
    _proof_deadline(deadline)
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 < expected_size <= limit:
        raise _proof_error("byte_budget_or_size")
    if path.stat().st_size != expected_size:
        raise _proof_error("byte_size_mismatch")
    chunks = []
    remaining = expected_size + 1
    with path.open("rb") as handle:
        while remaining:
            _proof_deadline(deadline)
            chunk = handle.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    encoded = b"".join(chunks)
    if len(encoded) != expected_size or hashlib.sha256(encoded).hexdigest() != expected_sha:
        raise _proof_error("body_or_proof_sha_mismatch")
    return encoded


def _proof_precision(body: Mapping[str, Any], owned: Mapping[str, Any], precision: Mapping[str, Any],
                     *, root: Path | str | None, deadline: float | None) -> dict[str, Any]:
    if not isinstance(precision, Mapping) or not precision:
        raise _proof_error("precision_missing")
    frozen = json.loads(_proof_json(dict(precision)))
    raw = _proof_bytes(_proof_path(owned["path"], root), body["sha256"], body["byte_size"],
                       limit=_LOCAL_BODY_MAX_BYTES, deadline=deadline)
    try:
        payload = json.loads(raw)
        params = json.loads(body["request_params_json"])
        scope = _proof_scope(body)
        proof = frozen["source_geometry_proof"]
        if (not isinstance(payload, dict) or frozen["city"] != scope["city"]
                or frozen["target_local_date"] != scope["target_date"]
                or frozen["timezone_name"] != params["timezone"]
                or payload["timezone"] != params["timezone"]
                or proof["raw_payload_sha256"] != body["sha256"]
                or not isinstance(proof.get("static_asset_audit"), dict) or not proof["static_asset_audit"]
                or not isinstance(proof.get("station_ground_proof"), dict) or not proof["station_ground_proof"]):
            raise _proof_error("precision_identity_mismatch")
        for claim, fact in (("requested_lat", params["latitude"]), ("requested_lon", params["longitude"]),
                            ("nearest_grid_lat", payload["latitude"]), ("nearest_grid_lon", payload["longitude"])):
            if isinstance(frozen[claim], bool) or not isinstance(frozen[claim], (int, float)) or frozen[claim] != fact:
                raise _proof_error("precision_request_or_cell_mismatch")
        response_scope = payload.get("_zeus_current_target_scope")
        if response_scope is not None and (not isinstance(response_scope, dict)
                or response_scope.get("city") != scope["city"] or response_scope.get("target_date") != scope["target_date"]
                or response_scope.get("metric", scope["metric"]) != scope["metric"]):
            raise _proof_error("response_scope_mismatch")
    except (TypeError, ValueError, KeyError) as exc:
        raise _proof_error("precision_identity_mismatch") from exc
    # Native physics/ground/full-slot adequacy is revalidated by the owning consumer.
    return frozen


def _local_proof_rows(conn: sqlite3.Connection, body: Mapping[str, Any], deadline: float | None,
                      *, before_artifact_id: int | None = None) -> Iterator[dict[str, Any]]:
    """Stream the complete exact namespace; 128 limits memory, never permission."""
    before = before_artifact_id
    while True:
        _proof_deadline(deadline)
        # The encoded original ID also retains narrow ownership when latest metadata is damaged.
        rows = _proof_rows_as_dicts(conn.execute(
            """SELECT * FROM raw_forecast_artifacts
               WHERE source_id=? AND product_id=? AND source_cycle_time=? AND data_version=?
                 AND ((json_valid(artifact_metadata_json) AND json_extract(artifact_metadata_json,'$.original_artifact_id')=?)
                      OR artifact_path GLOB ?)
                 AND (? IS NULL OR artifact_id < ?)
               ORDER BY artifact_id DESC LIMIT ?""",
            (body["source_id"], body["product_id"], body["source_cycle_time"], ANCHOR_LOCAL_PROOF_REVISION,
             body["artifact_id"], f"*/openmeteo_anchor_local_proof_{body['artifact_id']}_*.json",
             before, before, _LOCAL_PROOF_BATCH_ROWS),
        ))
        _proof_deadline(deadline)
        for row in rows:
            _proof_deadline(deadline)
            if any(not isinstance(row[key], str) or len(row[key].encode()) > _LOCAL_PROOF_MAX_BYTES
                   for key in ("artifact_metadata_json", "request_params_json")):
                raise _proof_error("frontier_descriptor_byte_budget")
            yield row
        if len(rows) < _LOCAL_PROOF_BATCH_ROWS:
            return
        before = rows[-1]["artifact_id"]


def _proof_frontier(conn: sqlite3.Connection, body: Mapping[str, Any], deadline: float | None,
                    *, before_artifact_id: int | None = None) -> dict[str, Any]:
    """Commit every ordered ID and full immutable descriptor, with constant memory.

    A digest is returned only after the entire prefix is observed. Count/maxID
    alone never substitute for the descriptor commitment, including on deadline.
    """
    digest = hashlib.sha256()
    count = 0
    maximum = 0
    for row in _local_proof_rows(conn, body, deadline, before_artifact_id=before_artifact_id):
        descriptor_sha = hashlib.sha256(_proof_json(row)).hexdigest()
        digest.update(_proof_json({"artifact_id": row["artifact_id"], "descriptor_sha256": descriptor_sha}))
        digest.update(b"\n")
        maximum = max(maximum, row["artifact_id"])
        count += 1
    _proof_deadline(deadline)
    return {"row_count": count, "max_artifact_id": maximum, "descriptor_sha256": digest.hexdigest()}


def _proof_recorded(row: Mapping[str, Any]) -> datetime | None:
    try:
        metadata = json.loads(row["artifact_metadata_json"])
        if metadata["recorded_at"] != row["recorded_at"]:
            return None
        return _parse_utc(row["recorded_at"], field_name="recorded_at")
    except (ValueError, TypeError, KeyError):
        return None


def read_anchor_local_proof(conn: sqlite3.Connection, original_artifact_id: int, *, city: str,
                           target_date: str, metric: str, decision_at: datetime | str,
                           root: Path | str | None = None,
                           deadline_monotonic: float | None = None) -> AnchorLocalProofEvidence | None:
    """Read latest exact-scope local possession as of both possession and SQL recording.

    Missing is None; damaged latest evidence is an explicit scoped rejection, never
    fallback. A new independently observed possession may cover only its frozen
    prior frontier. Subsequent unknown/ABA evidence remains blocking (INV-47).
    """
    _proof_deadline(deadline_monotonic)
    cut = _parse_utc(decision_at, field_name="decision_at")
    body = _proof_original(conn, original_artifact_id)
    scope = _proof_scope(body)
    if scope != {"city": city, "target_date": target_date, "metric": metric}:
        raise _proof_error("scope_mismatch")
    row = next((item for item in _local_proof_rows(conn, body, deadline_monotonic)
                if _proof_recorded(item) is None or _proof_recorded(item) <= cut), None)
    if row is None:
        return None
    try:
        encoded = _proof_bytes(_proof_path(row["artifact_path"], root), row["sha256"], row["byte_size"],
                               limit=_LOCAL_PROOF_MAX_BYTES, deadline=deadline_monotonic)
        doc = json.loads(encoded)
        metadata = json.loads(row["artifact_metadata_json"])
        possessed = _parse_utc(doc["local_possessed_at"], field_name="local_possessed_at")
        prepared = _parse_utc(doc["prepared_at"], field_name="prepared_at")
        recorded = _proof_recorded(row)
        expected_metadata = {"revision": ANCHOR_LOCAL_PROOF_REVISION, "original_artifact_id": original_artifact_id,
                             **scope, "document_sha256": row["sha256"], "local_possessed_at": doc["local_possessed_at"],
                             "recorded_at": row["recorded_at"], "clock_role": "local_proof_possession_not_http"}
        if (doc["revision"] != ANCHOR_LOCAL_PROOF_REVISION or doc["original_body_artifact"] != body
                or doc["scope"] != scope or doc["request_params"] != json.loads(body["request_params_json"])
                or doc.get("clock_resolution") != "milliseconds" or "recorded_at" in doc
                or possessed.microsecond % 1000 or prepared.microsecond % 1000
                or recorded is None or not possessed <= prepared <= recorded <= cut
                or _parse_utc(body["recorded_at"], field_name="original_recorded_at") > possessed
                or row["source_available_at"] != doc["local_possessed_at"] or row["captured_at"] != doc["local_possessed_at"]
                or row["request_url"] != body["request_url"] or row["request_params_json"] != body["request_params_json"]
                or row["training_allowed"] != 0 or metadata != expected_metadata
                or doc["observed_frontier"] != _proof_frontier(conn, body, deadline_monotonic, before_artifact_id=row["artifact_id"])
                or doc["owned_body"]["sha256"] != body["sha256"] or doc["owned_body"]["byte_size"] != body["byte_size"]):
            raise _proof_error("latest_invalid_or_frontier_changed")
        precision = _proof_precision(body, doc["owned_body"], doc["precision_metadata"], root=root, deadline=deadline_monotonic)
    except TimeoutError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _proof_error("latest_invalid_or_frontier_changed") from exc
    return AnchorLocalProofEvidence(body, row["artifact_id"], row["sha256"], doc["owned_body"],
                                    precision, scope, possessed, recorded)


def _write_local_proof_file(path: Path, encoded: bytes) -> None:
    if path.exists():
        if path.read_bytes() != encoded:
            raise _proof_error("sealed_file_changed")
        return
    # A caller rollback can leave an unreferenced file; only committed canonical
    # rows make it selectable. Publish complete bytes without overwriting a peer.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise _proof_error("sealed_file_changed")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def write_anchor_local_proof(conn: sqlite3.Connection, original_artifact_id: int,
                            manifest: RawForecastArtifactManifest, *, precision_metadata: Mapping[str, Any],
                            root: Path | str | None = None,
                            deadline_monotonic: float | None = None) -> int:
    """Append exact local proof in the caller's transaction, without renewing raw clocks.

    SCOPE is one original request/family. DRAIN is normal producer-owned byte/proof
    validation and this explicit append. RESET needs the owning consumer at a new
    cut; this helper grants neither native physics nor network/venue authority.
    """
    _proof_deadline(deadline_monotonic)
    if not conn.in_transaction:
        raise _proof_error("caller_write_transaction_required")
    if not isinstance(manifest, RawForecastArtifactManifest):
        raise TypeError("manifest must be RawForecastArtifactManifest")
    body = _proof_original(conn, original_artifact_id)
    scope = _proof_scope(body)
    payload = manifest.to_dict()
    if (any(payload[key] != body[key] for key in ("source_id", "product_id", "data_version", "source_cycle_time", "sha256", "byte_size", "request_url"))
            or _proof_json(payload["request_params"]) != _proof_json(json.loads(body["request_params_json"]))
            or any(manifest.product_metadata.get(key) != value for key, value in scope.items())):
        raise _proof_error("original_request_or_scope_mismatch")
    observed = _proof_frontier(conn, body, deadline_monotonic)
    owned = {"path": str(_proof_path(manifest.artifact_path, root)), "sha256": body["sha256"], "byte_size": body["byte_size"]}
    precision = _proof_precision(body, owned, precision_metadata, root=root, deadline=deadline_monotonic)
    try:
        existing = read_anchor_local_proof(conn, original_artifact_id, **scope,
                                           decision_at=conn.execute(f"SELECT {_PROOF_CLOCK_SQL}").fetchone()[0],
                                           root=root, deadline_monotonic=deadline_monotonic)
    except ValueError:
        existing = None  # A real new possession, not reuse, can repair exactly observed bad evidence.
    if existing is not None and existing.owned_body == owned and existing.precision_metadata == precision:
        return existing.proof_artifact_id
    # Actual verification is complete. Sample the canonical SQL clock at its
    # millisecond resolution; do not round any original provider/source clock.
    verified_at = conn.execute(f"SELECT {_PROOF_CLOCK_SQL}").fetchone()[0]
    if _parse_utc(body["recorded_at"], field_name="original_recorded_at") > _parse_utc(verified_at, field_name="verified_at"):
        raise _proof_error("original_recorded_in_future")
    doc = {"revision": ANCHOR_LOCAL_PROOF_REVISION, "original_body_artifact": body,
           "scope": scope, "request_params": json.loads(body["request_params_json"]), "owned_body": owned,
           "precision_metadata": precision, "local_possessed_at": verified_at, "prepared_at": verified_at,
           "clock_resolution": "milliseconds", "observed_frontier": observed}
    encoded = _proof_json(doc)
    if len(encoded) > _LOCAL_PROOF_MAX_BYTES:
        raise _proof_error("proof_byte_budget")
    digest = hashlib.sha256(encoded).hexdigest()
    path = Path(owned["path"]).with_name(f"openmeteo_anchor_local_proof_{original_artifact_id}_{digest}.json")
    _write_local_proof_file(path, encoded)
    _proof_deadline(deadline_monotonic)
    if (_proof_original(conn, original_artifact_id) != body
            or _proof_frontier(conn, body, deadline_monotonic) != doc["observed_frontier"]):
        raise _proof_error("frontier_changed_before_insert")
    metadata = {"revision": ANCHOR_LOCAL_PROOF_REVISION, "original_artifact_id": original_artifact_id,
                **scope, "document_sha256": digest, "local_possessed_at": verified_at,
                "clock_role": "local_proof_possession_not_http"}
    conn.execute(
        f"""INSERT INTO raw_forecast_artifacts
            (source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
             artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at,training_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,json_set(?,'$.recorded_at',{_PROOF_CLOCK_SQL}),{_PROOF_CLOCK_SQL},0)""",
        (body["source_id"], body["product_id"], ANCHOR_LOCAL_PROOF_REVISION, body["source_cycle_time"],
         verified_at, verified_at, str(path), digest, len(encoded), body["request_url"], body["request_params_json"], _proof_json(metadata).decode()),
    )
    result = read_anchor_local_proof(conn, original_artifact_id, **scope,
                                     decision_at=conn.execute(f"SELECT {_PROOF_CLOCK_SQL}").fetchone()[0],
                                     root=root, deadline_monotonic=deadline_monotonic)
    if result is None:
        raise _proof_error("insert_readback_unavailable")
    return result.proof_artifact_id
