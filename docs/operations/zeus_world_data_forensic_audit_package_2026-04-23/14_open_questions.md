# 14 Open Questions

- What exact code path wrote the populated `settlements` rows, and can it be replayed deterministically from source payloads?
- What source payloads or logs, if any, exist for the 39,431 empty-provenance WU daily observation rows?
- Which downstream consumers currently read `hourly_observations` versus `observation_instants_v2` or v2 views?
- Are there market-rule source files for the null-`market_slug` settlement rows?
- What is the intended policy for HKO integer oracle values versus decimal HKO observations?
- What exact Polymarket high/low market universe is in scope for Zeus?
- Which v2 tables are expected to be empty in this artifact, and which are supposed to be production-populated?
- Can TIGGE/ECMWF access be run locally after 2026 migration changes, and are raw manifests available?
- Are station mappings frozen by market date, or do they use current authority files?
- What is the intended cutoff for Day0 low/high live observations in local time for each market?
- Which tests run against the real `zeus-world.db`, and which use synthetic fixtures only?
- Are archive docs contradictory to current authority docs in source tiering or settlement semantics?