{
  "version": "polyweather_source_truth_overlay_v1",
  "generated_utc": "2026-06-17T00:00:00Z",
  "core_law": [
    "Active Polymarket market resolution rules outrank table priority.",
    "Exact settlement station/source is a truth target, not a weighted opinion.",
    "Forecast sources enter the prior/fusion layer; observed 0D sources enter only as observation likelihood/censoring constraints.",
    "No network fetch is allowed inside the q path; all inputs must be persisted with proof-of-possession availability."
  ],
  "source_roles": {
    "wu_icao_history": {
      "live_role": "settlement_anchor/reconciliation only when market rules name that WU URL/station",
      "day0_role": "not reliable enough as only intraday source; use final history for settlement reconciliation",
      "math_weight": "target identity; no probabilistic weight"
    },
    "aviationweather_metar": {
      "live_role": "primary 0D airport observation likelihood for exact ICAO stations",
      "latency_target_min": 5,
      "math_weight": "1 / (sensor+rounding+lag+settlement-transform+mismatch variance), estimated walk-forward"
    },
    "madis_hfmetar": {
      "live_role": "US exact-station high-frequency observation likelihood, cross-checked by METAR",
      "latency_target_min": 5,
      "math_weight": "same observation likelihood; covariance with METAR must be learned, not double counted"
    },
    "hko_daily_api": {
      "live_role": "Hong Kong settlement truth/final; HKO city-center geometry",
      "math_weight": "truth target for HKO markets"
    },
    "hko_realtime_api": {
      "live_role": "Hong Kong 0D observation likelihood",
      "latency_target_min": 10,
      "math_weight": "official exact station observation likelihood"
    },
    "noaa_settlement_current": {
      "live_role": "settlement/current truth only for NOAA-settled cities explicitly named in rules",
      "math_weight": "truth target / observation likelihood when exact site"
    },
    "openmeteo_forecast": {
      "live_role": "forecast prior only",
      "latency_target_min": 360,
      "math_weight": "model instruments inside existing EB + T2 precision fusion"
    },
    "openmeteo_ensemble": {
      "live_role": "probabilistic forecast signal only",
      "latency_target_min": 360,
      "math_weight": "support/covariance through calibrated ensemble/fusion layer; not settlement truth"
    },
    "openmeteo_previous_runs": {
      "live_role": "walk-forward history, bias correction, residual/covariance estimation",
      "latency_target_min": 720,
      "math_weight": "training substrate, not current observation"
    },
    "meteostat_bulk": {
      "live_role": "backtest/history backfill, not 0D trading",
      "latency_target_min": 1440,
      "math_weight": "history only; never live settlement unless rule names Meteostat"
    },
    "restricted_or_unverified": {
      "families": [
        "amsc_awos",
        "aeroweb",
        "ncm_current",
        "ncm_forecast",
        "ims_observation_api"
      ],
      "live_role": "excluded from live q until ordinary-public access and endpoint stability are proven",
      "math_weight": "0 in live; shadow residual collection only"
    }
  }
}