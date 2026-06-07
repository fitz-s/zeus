"""Read-only preflight for replacement forecast raw-to-posterior materialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, audit_raw_forecast_artifact_inventory, read_manifest
from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds


@dataclass(frozen=True)
class ReplacementForecastMaterializationPreflight:
    status: str
    reason_codes: tuple[str, ...]
    forecast_db: str
    raw_manifest_dir: str
    scratch_seed_dir: str
    manifest_count: int
    manifest_identity_counts: Mapping[str, int]
    raw_candidate_counts: Mapping[str, int]
    inventory_status: str
    seed_discovery_status: str | None
    discovered_seed_count: int
    failed_seed_target_count: int
    written_seed_files: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status == "MATERIALIZATION_PREFLIGHT_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "forecast_db": self.forecast_db,
            "raw_manifest_dir": self.raw_manifest_dir,
            "scratch_seed_dir": self.scratch_seed_dir,
            "manifest_count": self.manifest_count,
            "manifest_identity_counts": dict(self.manifest_identity_counts),
            "raw_candidate_counts": dict(self.raw_candidate_counts),
            "inventory_status": self.inventory_status,
            "seed_discovery_status": self.seed_discovery_status,
            "discovered_seed_count": self.discovered_seed_count,
            "failed_seed_target_count": self.failed_seed_target_count,
            "written_seed_files": list(self.written_seed_files),
        }


def _manifest_files(raw_manifest_dir: Path) -> tuple[Path, ...]:
    if not raw_manifest_dir.exists():
        return ()
    return tuple(sorted(path for path in raw_manifest_dir.glob("*.manifest.json") if path.is_file()))


def _read_manifests(paths: tuple[Path, ...]) -> tuple[RawForecastArtifactManifest, ...]:
    manifests: list[RawForecastArtifactManifest] = []
    for path in paths:
        manifest = read_manifest(path)
        manifests.append(
            RawForecastArtifactManifest(
                **{
                    **manifest.to_dict(),
                    "product_metadata": {
                        **dict(manifest.product_metadata),
                        "manifest_json": str(path),
                    },
                }
            )
        )
    return tuple(manifests)


def _identity_counts(manifests: tuple[RawForecastArtifactManifest, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for manifest in manifests:
        key = f"{manifest.source_id}|{manifest.product_id}|{manifest.data_version}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _raw_candidate_counts(raw_manifest_dir: Path) -> dict[str, int]:
    if not raw_manifest_dir.exists():
        return {}
    return {
        "aifs_grib2": len(tuple(raw_manifest_dir.rglob("*.grib2"))),
        "openmeteo_json": len(tuple(raw_manifest_dir.rglob("openmeteo*.json"))) + len(tuple((raw_manifest_dir / "openmeteo_jun3_jun6_preday").glob("*.json"))) if (raw_manifest_dir / "openmeteo_jun3_jun6_preday").exists() else len(tuple(raw_manifest_dir.rglob("openmeteo*.json"))),
        "precision_metadata_json": len(tuple(raw_manifest_dir.rglob("*precision*.json"))),
        "manifest_json": len(tuple(raw_manifest_dir.rglob("*.manifest.json"))),
    }


def build_replacement_forecast_materialization_preflight(
    *,
    forecast_db: Path | str,
    raw_manifest_dir: Path | str,
    scratch_seed_dir: Path | str,
    computed_at: str | None = None,
    limit: int = 10,
) -> ReplacementForecastMaterializationPreflight:
    """Check whether downloaded raw artifacts can enter the materialization queue.

    This function writes only scratch seed files under ``scratch_seed_dir``. It
    never writes the live forecast DB, current facts, config, orders, settlement,
    or calibration tables.
    """

    db_path = Path(forecast_db)
    raw_dir = Path(raw_manifest_dir)
    seed_dir = Path(scratch_seed_dir)
    manifest_paths = _manifest_files(raw_dir)
    manifests = _read_manifests(manifest_paths)
    inventory = audit_raw_forecast_artifact_inventory(list(manifests), root=raw_dir) if manifests else None
    seed_report = None
    reasons: list[str] = []
    if not db_path.exists():
        reasons.append("REPLACEMENT_MATERIALIZATION_PREFLIGHT_FORECAST_DB_MISSING")
    if not raw_dir.exists():
        reasons.append("REPLACEMENT_MATERIALIZATION_PREFLIGHT_RAW_DIR_MISSING")
    if not manifests:
        reasons.append("REPLACEMENT_MATERIALIZATION_PREFLIGHT_RAW_MANIFESTS_MISSING")
    if inventory is not None and not inventory.valid:
        reasons.extend(inventory.reason_codes)
    if not reasons:
        seed_report = discover_replacement_forecast_materialization_seeds(
            forecast_db=db_path,
            raw_manifest_dir=raw_dir,
            seed_dir=seed_dir,
            computed_at=computed_at,
            limit=limit,
        )
        if seed_report.status != "DISCOVERED" or seed_report.discovered_count <= 0:
            reasons.append("REPLACEMENT_MATERIALIZATION_PREFLIGHT_NO_DISCOVERED_SEEDS")
            reasons.extend(seed_report.reason_codes)
        if seed_report.failed_count:
            reasons.append("REPLACEMENT_MATERIALIZATION_PREFLIGHT_FAILED_SEED_TARGETS")
    status = "MATERIALIZATION_PREFLIGHT_READY" if not reasons else "MATERIALIZATION_PREFLIGHT_BLOCKED"
    return ReplacementForecastMaterializationPreflight(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_MATERIALIZATION_PREFLIGHT_READY"])),
        forecast_db=str(db_path),
        raw_manifest_dir=str(raw_dir),
        scratch_seed_dir=str(seed_dir),
        manifest_count=len(manifests),
        manifest_identity_counts=_identity_counts(manifests),
        raw_candidate_counts=_raw_candidate_counts(raw_dir),
        inventory_status="NOT_RUN" if inventory is None else inventory.status,
        seed_discovery_status=None if seed_report is None else seed_report.status,
        discovered_seed_count=0 if seed_report is None else seed_report.discovered_count,
        failed_seed_target_count=0 if seed_report is None else seed_report.failed_count,
        written_seed_files=() if seed_report is None else seed_report.written_seed_files,
    )
