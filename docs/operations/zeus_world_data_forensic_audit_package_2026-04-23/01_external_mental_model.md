# 01 External Mental Model — Weather Trading Data Machine Requirements

A competent weather-based quantitative trading machine is a point-in-time market/meteorology system, not a generic weather-data warehouse. It must know not merely what temperature eventually happened, but what information was available, from which authority, at which time, in which units, under which market rules, and whether that row is eligible for live decisions, replay, training, or settlement evidence.

## 1. Required properties

### Source identity
Every row must identify the source in a way that is stable under replay: provider, product, endpoint or file, station/grid identity, model version, retrieval method, parser version, payload hash, and source role. A tag like `wu_icao_history` is not enough when settlement rules name a station URL and a finalization process.

### Provenance
Provenance must be content-addressable and auditable. Required fields include raw payload or file path, payload checksum, parser version, fetched/imported timestamps, source URL/API parameters, station metadata, run id, and any normalization/rounding applied. Empty provenance on a `VERIFIED` row is a false-confidence state.

### Causality / point-in-time validity
For forecasts and markets, the machine must know `issue_time`, `available_at`, `fetch_time`, exchange listing time, market close/open times, and finalization time. Training rows must never use data first available after the decision time. Reconstructing availability from later metadata is not canonical causality.

### Local-day time geometry
Weather markets are local-calendar-day contracts. The machine must materialize local-day start/end in UTC, local timezone, offset, DST state, and whether a local hour was missing or ambiguous. Daily maximum/minimum must be computed over the exact market local day, not over UTC date or a naïve 24-row window.

### Timezone and DST correctness
Spring-forward days have 23 local hours; fall-back days have 25 and ambiguous local-hour labels. A local hour number alone is insufficient. UTC timestamp plus local timestamp plus timezone plus offset plus missing/ambiguous flags are required.

### Unit correctness
Rows must encode raw unit, target unit, conversion version, rounding law, and settlement unit. Celsius and Fahrenheit markets have different bin edges and rounding risk. Kelvin ensemble values need explicit conversion and precision.

### Station identity
Settlement markets often resolve using a named station or station page. City names are insufficient. Station IDs must be normalized, versioned, and tied to the market rule. Nearby airports, weather offices, WMO stations, WU station pages, and gridded model points are not interchangeable.

### Settlement compatibility
Settlement rows must capture source page, station, finalization/revision policy, exact value, bin rule, rounding/truncation semantics, winning token/bin, and whether the value is final or preliminary. Settlement truth and meteorological observation truth may differ.

### Revision/finalization policy
Weather data can revise. A trading machine must distinguish preliminary, final, revised-after-final, and backfilled states. It needs a rule for whether late revisions are accepted for settlement and training.

### Source tiering / fallback policy
Fallbacks must be role-aware. A fallback can be useful for monitoring or gap analysis while being ineligible for settlement, calibration, or certain live decisions. Source role should be explicit: settlement_authority, station_evidence, fallback_station, gridded_model, reanalysis, exchange_market_data, or derived.

### Missing-data behavior
Missingness is data. A row gap must be classified as expected gap, source outage, quota failure, station unsupported, local-day incomplete, or backfill not attempted. Silent partial backfills must not be treated as low confidence complete truth.

### Confidence / quality / authority labels
Authority is not a stamp; it is a contract. `VERIFIED` must imply a row passed content checks, source-role checks, time geometry checks, unit checks, provenance checks, and eligibility checks. A `VERIFIED` row with empty provenance is internally inconsistent.

### Reproducibility for replay and training
Replay requires frozen inputs: market book, market rules, forecast snapshots, observation snapshots, settlement state, calibration model version, and strategy code version. Training requires no hindsight leakage and stable derived labels.

### Idempotent ingestion
Ingestion should be repeatable, transactional, and monotonic where appropriate. `INSERT OR REPLACE` is safe only if the replaced row is semantically identical or revision handling is explicit. Backfills need manifests and row-count expectations.

### Auditability
Auditors must be able to answer: why was this row trusted, what produced it, when was it available, what source did it come from, what alternatives were skipped, and what downstream results depended on it.

### Failure isolation
A source outage or poisoned station must not contaminate the canonical training family. The system should quarantine rows, keep evidence, and prevent consumers from accidentally using quarantined/fallback/runtime-only data.

## 2. Failure classes

- Wrong station/wrong city mapping: station page differs from market rule; city-level join hides the mismatch.
- Wrong local-day partition: UTC day or naïve date used instead of market timezone local day.
- DST off-by-one-hour/day: fall-back duplicate hour or spring-forward missing hour miscomputed.
- Unit/rounding error: C/F/K conversion, WMO half-up rounding, or HKO truncation/official integer semantics applied inconsistently.
- Source drift/stale source: public page or private endpoint changes fields, source population, or finalization behavior.
- Fallback contamination: Meteostat/Ogimet/Open-Meteo used where WU/HKO settlement authority was required.
- Final/preliminary confusion: early data trained as final settlement.
- Issue-time corruption: forecast rows lack true `issue_time`/`available_at` and are reconstructed after the fact.
- Hindsight leakage: historical forecast or observation values loaded from a future-finished archive but treated as point-in-time.
- Missing provenance fields: cannot distinguish scrape, API, static bulk file, manual fix, or parser version.
- Mixed authority rows in one training family: primary and fallback rows have identical data_version or no source-role guard.
- Duplicate/silently conflicting rows: uniqueness key omits metric, market, or station.
- Silent partial backfills: script logs failures but leaves enough rows that consumers believe coverage is complete.
- Ingest success but semantic wrongness: all rows inserted, but station/unit/period wrong.
- Settlement reconstruction mismatch: meteorological high differs from market oracle settlement because market source/rounding differs.
- Replay/training mismatch: calibration built from historical labels unavailable or invalid at live decision time.

## 3. External source classes relevant to Zeus

### ECMWF / TIGGE forecasts
TIGGE is a multi-centre global ensemble forecast archive. It is appropriate for forecast signal and calibrated probability modeling when issue cycle, model centre, member count, valid time, parameter, lead, and local-day extraction are preserved. It is not settlement authority. Zeus's TIGGE extractors encode many of these concepts, but the current DB has zero `ensemble_snapshots_v2` rows, so the external model is not realized in the uploaded DB.

### Weather Underground / WU history
WU station history can match many Polymarket market pages, especially airport station pages. It is appropriate as settlement evidence only when the exact station/source page and finalization behavior are captured. Zeus's WU daily observation rows are high-volume but mostly empty-provenance, and WU hourly uses a private endpoint/bucketed maxima/minima design; both require source-contract hardening.

### HKO
HKO is a special settlement source for Hong Kong style markets. Its decimal observations and Polymarket's integer settlement outcomes must be treated as related but separate semantic rows. Zeus correctly has HKO-specific semantics in code, but the legacy observations/settlements split shows how conflation can mislead.

### Ogimet METAR/SYNOP
Ogimet is useful as station-evidence fallback, especially when WU gaps exist. It mirrors METAR/SYNOP data and has cadence/encoding limitations. It is not safe as canonical settlement authority unless the market rule names it or the equivalence is proven.

### Meteostat bulk
Meteostat bulk is useful for historical gap evidence and rough station archives. It aggregates public sources and may lag; some Meteostat services may fill gaps. It should be fallback/evidence, never silent canonical settlement truth.

### Open-Meteo
Open-Meteo archive/previous-runs/forecast APIs are useful gridded/model/reanalysis signals and runtime fallbacks. They are not station settlement authority. Previous-runs/historical forecast rows must preserve true issue/availability semantics; otherwise they are not causal training inputs.

### Polymarket weather market rules
Polymarket rules bind each market to a source, station/location, date, unit, high/low metric, bin logic, and finalization/revision behavior. These must be stored per market. City/date-only settlement rows cannot support exact Polymarket replay.

## Zeus mapped against the standard

Zeus has many correct architectural instincts in code/docs: high/low metric separation, settlement semantics, source tiering, v2 ensemble/calibration fields, DST-aware observation instants, and data coverage. The uploaded DB, however, only partially embodies those instincts. The observation evidence lane is populated; the forecast/replay/calibration lane is not. The settlement lane is legacy high-only and missing market identity. Therefore Zeus currently fails the external standard for a quantitative trading machine even though parts of the infrastructure point in the right direction.

Source verification notes used during this audit:
- Zeus repository branch: `fitz-s/zeus`, branch `data-improve`; mandatory files were read from raw GitHub where accessible. LOCAL_VERIFICATION_REQUIRED: exact local checkout at baseline commit `0206428b26bbb0dd48223e449553d1075de37c72`, full test run, and grep over unlisted files.
- ECMWF/TIGGE: official ECMWF/TIGGE documentation describes TIGGE as a global NWP ensemble archive from multiple centres and notes 2026 migration of public access to the ECMWF Data Stores.
- Open-Meteo: official docs distinguish Historical Weather API, Historical Forecast API / Previous Runs, and forecast/model products; these are gridded/model/archive services, not Polymarket station settlement authority.
- Meteostat: official docs present bulk/historical observations from aggregated public sources and quality caveats; point APIs may fill gaps depending endpoint.
- Ogimet: Ogimet provides METAR/SYNOP query services and documentation on coded maximum/minimum temperature groups, but METAR cadence does not by itself guarantee full daily extrema unless the official settlement source shares the same observation stream.
- Weather Underground / Weather Company: WU public history pages and Weather Company API material show station/history products but do not make a stable private-v1 endpoint contract suitable as an immutable canonical feed by itself.
- Polymarket weather rules sampled from live market pages show settlement tied to specified stations/source pages and finalization/revision rules; those rules must be stored per market, not inferred from city/date only.