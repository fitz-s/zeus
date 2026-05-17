-- Phase C-3 writer dry-run — 2026-05-04T02:15:38.337160+00:00
-- flag=ZEUS_ENTRY_FORECAST_READINESS_WRITER
-- ready_to_flip=True
-- rationale=writer fail-closed as expected: no evidence file ⇒ BLOCKED row with EVIDENCE_MISSING. Reader will surface the typed blocker rather than silently miss the row.

-- columns: strategy_key | status | market_family | condition_id | reason_codes_json
entry_forecast | BLOCKED | POLY_TEMP_LONDON | condition-evidence-probe | ["ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING","CALIBRATION_TRANSFER_SHADOW_ONLY"]
