# 13 Rejected Claims and False Confidence

1. **Rejected:** “The DB is ready because it has 1.8M hourly rows.”  
   **Reason:** those rows are observation evidence; the forecast/calibration/replay/market spine is empty, and legacy hourly rows are lossy.

2. **Rejected:** “`VERIFIED` authority means daily WU rows are auditable.”  
   **Reason:** 39,431 daily observation rows have empty provenance.

3. **Rejected:** “Settlements are complete because `settlements` is populated.”  
   **Reason:** `settlements_v2` is empty, all `market_slug` values are null, v1 is high-only and city/date-keyed.

4. **Rejected:** “Fallback sources are harmless.”  
   **Reason:** fallback station/model rows can enter current views unless source role and eligibility are enforced.

5. **Rejected:** “Open-Meteo/Meteostat/Ogimet can stand in for market settlement authority.”  
   **Reason:** they are useful evidence or model/fallback feeds, but market rules name specific source/station/finalization semantics.

6. **Rejected:** “Tests prove the real DB is safe.”  
   **Reason:** tests can prove code contracts but cannot validate uploaded DB content unless they are run against real row patterns and negative fixtures.

7. **Rejected:** “Schema fields are enough.”  
   **Reason:** v2 schema has strong fields, but critical v2 tables are empty and populated v1 tables miss key semantics.

8. **Rejected:** “Source URL plus city/date is enough for settlement.”  
   **Reason:** station, market, metric, unit, bin, finalization, and revision policy are all needed.

9. **Rejected:** “Historical forecasts can be made causal by estimating availability.”  
   **Reason:** estimated availability is evidence, not canonical point-in-time truth unless validated against source issuance.

10. **Rejected:** “Graph/context DB proves paths are covered.”  
    **Reason:** graph.db is derived context; it helps find code paths but is not authority over behavior or data correctness.