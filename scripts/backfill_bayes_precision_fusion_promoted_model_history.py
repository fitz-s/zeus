# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 operator-directed same-day promotion of the regional-survey
#   models (/tmp/uncovered_cities_regional_report.md; model_selection.py PROVIDER_FAMILIES).
#   The EB walk-forward de-bias is the DOMINANT fusion lever (+0.59C, backtest wszeibgi0);
#   promoting a model with zero history would serve it un-de-biased and LOWN-inflated for
#   ~25 days. This backfill loads the open-meteo previous-runs fixed-lead archive (the SAME
#   product the daily download accrues) so MIN_TRAIN=25 is satisfied from day one.
"""One-shot walk-forward history backfill for the 2026-06-09 promoted models.

For each promoted model x in-domain city: ONE bulk previous-runs request (~185 days,
temperature_2m_previous_day1..day3), local-day extrema per lead (>=18 valid hours), persisted
into raw_model_forecasts with endpoint='previous_runs' via the download module's own persist
machinery (product identity, conflict audit, UNIQUE idempotency — IRON RULE #4, no parallel
writer). Rows already present for a (model, city, metric, lead, target) are skipped so daily
accrual rows are never duplicated into the covariance window.

Usage:
  PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/backfill_bayes_precision_fusion_promoted_model_history.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.bayes_precision_fusion_download import (  # noqa: E402
    BayesPrecisionFusionDownloadTarget,
    _model_in_domain,
    _persist_rows,
    _scan_and_audit_request_conflicts,
    _bayes_precision_fusion_product_identity,
)
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: E402

PROMOTED_MODELS = (
    "ncep_nbm_conus",
    "ukmo_global_deterministic_10km",
    "ukmo_uk_deterministic_2km",
)
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEADS = (1, 2, 3)
BACKFILL_DAYS = 185
MIN_VALID_HOURS = 18
RELEASE_LAG_HOURS = 14.0


def _cities() -> list[dict]:
    return json.load(open(ROOT / "config" / "cities.json"))["cities"]


def _fetch_bulk(city: dict, model: str) -> dict | None:
    hourly = ",".join(f"temperature_2m_previous_day{n}" for n in LEADS)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=BACKFILL_DAYS)
    params = {
        "latitude": city["lat"], "longitude": city["lon"],
        "hourly": hourly, "models": model,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "temperature_unit": "celsius", "timezone": city["timezone"],
    }
    url = f"{PREVIOUS_RUNS_URL}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if "hourly" not in payload:
            print(f"  SKIP {model}/{city['name']}: no hourly in response", flush=True)
            return None
        return payload
    except Exception as exc:
        print(f"  FAIL {model}/{city['name']}: {exc}", flush=True)
        return None


def _daily_extrema(payload: dict, lead: int) -> dict[str, tuple[float, float, int]]:
    """{local_date: (max_c, min_c, n_valid_hours)} for one lead var."""
    hours = payload["hourly"]["time"]
    vals = payload["hourly"].get(f"temperature_2m_previous_day{lead}") or []
    agg: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(hours, vals):
        if v is None:
            continue
        agg[t[:10]].append(float(v))
    return {d: (max(vs), min(vs), len(vs)) for d, vs in agg.items() if len(vs) >= MIN_VALID_HOURS}


def main() -> int:
    db_path = ROOT / "state" / "zeus-forecasts.db"
    conn = _connect(db_path, write_class="live")
    ensure_replacement_forecast_live_schema(conn)
    existing: set[tuple] = set(
        tuple(r) for r in conn.execute(
            "SELECT model, city, metric, lead_days, target_date FROM raw_model_forecasts"
            " WHERE endpoint='previous_runs' AND model IN (?,?,?)", PROMOTED_MODELS,
        )
    )
    captured_iso = datetime.now(tz=UTC).isoformat()
    total = 0
    for model in PROMOTED_MODELS:
        for city in _cities():
            if not _model_in_domain(model, lat=float(city["lat"]), lon=float(city["lon"]), lead_days=0):
                continue
            payload = _fetch_bulk(city, model)
            if payload is None:
                continue
            rows: list[dict] = []
            for lead in LEADS:
                for target_date, (hi, lo, _n) in _daily_extrema(payload, lead).items():
                    cycle = datetime.fromisoformat(target_date).replace(tzinfo=UTC) - timedelta(days=lead)
                    cycle_iso = cycle.isoformat()
                    available_iso = (cycle + timedelta(hours=RELEASE_LAG_HOURS)).isoformat()
                    for metric, value in (("high", hi), ("low", lo)):
                        if (model, city["name"], metric, lead, target_date) in existing:
                            continue
                        target = BayesPrecisionFusionDownloadTarget(
                            city=city["name"], metric=metric, target_date=target_date,
                            lead_days=lead, latitude=float(city["lat"]),
                            longitude=float(city["lon"]), timezone_name=str(city["timezone"]),
                        )
                        rows.append({
                            "model": model, "city": city["name"], "target_date": target_date,
                            "metric": metric, "source_cycle_time": cycle_iso,
                            "source_available_at": available_iso, "captured_at": captured_iso,
                            "lead_days": lead, "forecast_value_c": float(value),
                            "endpoint": "previous_runs",
                            **_bayes_precision_fusion_product_identity(model, "previous_runs", target),
                        })
            if not rows:
                print(f"  {model}/{city['name']}: nothing new", flush=True)
                continue
            _scan_and_audit_request_conflicts(conn, rows)
            conn.execute("BEGIN")
            try:
                written = _persist_rows(conn, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            total += written
            print(f"  {model}/{city['name']}: wrote {written} rows", flush=True)
    conn.close()
    print(f"BACKFILL DONE: {total} rows", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
