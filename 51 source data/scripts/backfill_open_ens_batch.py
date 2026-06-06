#!/usr/bin/env python3
"""Download/extract ECMWF Open Data ENS member vectors for a manifest city set."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from download_ecmwf_open_ens import download_open_ens
from extract_open_ens_city_member_vectors import extract_open_ens

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
RAW_ROOT = ROOT / "raw" / "ecmwf_open_ens"
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"


def _slug(city: str) -> str:
    return city.lower().replace(" ", "-")


def run_batch(
    *,
    date_value: date,
    run_hour: int,
    steps: list[int],
    source: str,
    manifest_path: Path,
    output_subdir: str,
    summary_path: Path | None,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cities = [row["city"] for row in manifest["cities"]]
    date_compact = date_value.strftime("%Y%m%d")
    download_dir = RAW_ROOT / source / date_compact
    output_dir = download_dir / output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    downloads = []
    extracts = []
    for step in steps:
        grib_path = download_dir / f"open_ens_{date_compact}_{run_hour:02d}z_steps_{step}_params_2t.grib2"
        if grib_path.exists():
            downloads.append({"step": step, "status": "skipped_exists", "target": str(grib_path)})
        else:
            downloads.append(
                {
                    "step": step,
                    "status": "downloaded",
                    **download_open_ens(
                        date_value=date_value,
                        run_hour=run_hour,
                        step=[step],
                        param=["2t"],
                        source=source,
                        output_path=grib_path,
                    ),
                }
            )

        for city in cities:
            output_path = output_dir / f"open_ens_{_slug(city)}_members_{date_compact}_{run_hour:02d}z_step_{step:03d}_2t.json"
            if output_path.exists():
                extracts.append({"city": city, "step": step, "status": "skipped_exists", "output_path": str(output_path)})
                continue
            summary = extract_open_ens(city, grib_path, manifest_path=manifest_path, output_path=output_path)
            extracts.append(
                {
                    "city": city,
                    "step": step,
                    "status": "written",
                    "member_count": summary["member_count"],
                    "output_path": str(output_path),
                }
            )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_value.isoformat(),
        "run_hour": run_hour,
        "steps": steps,
        "source": source,
        "manifest_path": str(manifest_path),
        "output_subdir": output_subdir,
        "city_count": len(cities),
        "downloads": downloads,
        "extracts": extracts,
    }
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--run-hour", type=int, default=0)
    parser.add_argument("--steps", nargs="+", type=int, default=[24, 48, 72, 96, 120, 144, 168])
    parser.add_argument("--source", default="ecmwf")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-subdir", default="all_cities")
    parser.add_argument("--summary-path", type=Path)
    args = parser.parse_args()

    payload = run_batch(
        date_value=date.fromisoformat(args.date),
        run_hour=args.run_hour,
        steps=args.steps,
        source=args.source,
        manifest_path=args.manifest_path,
        output_subdir=args.output_subdir,
        summary_path=args.summary_path,
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "run_hour": payload["run_hour"],
                "steps": payload["steps"],
                "city_count": payload["city_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
