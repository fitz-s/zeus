#!/usr/bin/env python3
"""Plan the full replacement forecast simple-switch bundle without writing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_simple_switch_bundle import (  # noqa: E402
    build_replacement_forecast_simple_switch_bundle,
)


def _evidence(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evidence JSON must decode to an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan replacement forecast simple-switch prerequisites")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence-json", type=Path, default=None)
    parser.add_argument("--refit-handoff-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        bundle = build_replacement_forecast_simple_switch_bundle(
            args.root,
            current_fact_evidence=_evidence(args.evidence_json),
            current_fact_evidence_path=args.evidence_json,
            refit_handoff_json_path=args.refit_handoff_json,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    payload = bundle.as_dict()
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{bundle.status}: {','.join(bundle.reason_codes)}")
    return 0 if bundle.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
