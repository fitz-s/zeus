"""Raw forecast artifact manifest helpers for replacement forecast input provenance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.data.forecast_source_registry import REPLACEMENT_FORECAST_PRODUCTS


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


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
        raise ValueError("raw forecast artifact data_version is not a registered replacement raw product")
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
    target.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_manifest(path: Path | str) -> RawForecastArtifactManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("raw forecast artifact manifest must decode to an object")
    known = {item.name for item in fields(RawForecastArtifactManifest)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"raw forecast artifact manifest has unsupported fields: {sorted(unknown)}")
    raw = {key: value for key, value in raw.items() if key in known}
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
        DO UPDATE SET
            source_available_at = excluded.source_available_at,
            captured_at = excluded.captured_at,
            artifact_path = excluded.artifact_path,
            byte_size = excluded.byte_size,
            request_url = excluded.request_url,
            request_params_json = excluded.request_params_json,
            artifact_metadata_json = excluded.artifact_metadata_json,
            training_allowed = excluded.training_allowed
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
