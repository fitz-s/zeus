# Round 4 — free public observation sources

Request: REQ-20260930-174930-82ebb0

## Result and access

Four new free-public station routes are admitted and wired: Moscow UUWW, Lucknow VILK, Ankara LTAC and Istanbul LTFM. The inherited 15-module suite passes 1,059 tests with one optional NetCDF skip; final proof/configuration validation passes 109 with the same optional skip. No unrelated baseline debt was repaired.

Work was recovered and completed in `/Users/leofitz/zeus/.claude/worktrees/fast-obs-survey-r4` on `feat/fast-obs-survey-r4`, descended from the operator-specified `6824cbc06d37340964e286a66b56e0eb45e0786a`. The prior feature branch and `live` were not mutated by this round. WebCodex supplied repository/shell/test/public-network access. No production databases were opened for mutation, no daemon was restarted, and no deployment occurred.

## Identity and measurement law

Value equality is the identity proof: equal station and UTC valid instant, contract-native unit conversion and `SettlementSemantics.round_single`. No instrument-mapping certificate is demanded. Every distinct contradictory value at a paired clock is retained; observations repeated across polls/windows are deduplicated. A promoted alternative must have an upper first-availability bound earlier than the lower bound of every relevant current path at the SAME exact-value-paired observation instant. An unpaired ten-minute lead does not qualify. Existing faster national routes are comparators, not just AWC. Jinan cannot qualify by beating slow WU history while its existing current endpoint remains unmeasured.

Publication intervals run from the immediately preceding successful negative request to the first complete positive response. Negative lower bounds remain uncertainty bounds, not claims of negative physical latency. HTTP duration, current observation age, and historical coverage are not publication delay. Sample-specific leads do not establish universal dominance or a p99.

## New admissions

|City/station|Channel|Exact/paired|Example observation UTC|Candidate lag seconds|AWC lag seconds|WRH lag seconds|
|---|---|---:|---|---:|---:|---:|
|Moscow/UUWW|metaviatelecom_display|4/4|2026-09-30T23:00:00+00:00|[47.070, 107.153]|[226.209, 291.210]|[270.900, 330.658]|
|Lucknow/VILK|imd_olbs_metar|2/2|2026-09-30T23:30:00+00:00|[-29.442, 32.557]|[87.758, 154.390]|[272.548, 330.560]|
|Ankara/LTAC|mgm_metar|50/50|2026-09-30T23:50:00+00:00|[226.232, 288.448]|[351.870, 410.770]|[352.025, 411.478]|
|Istanbul/LTFM|mgm_metar|49/49|2026-09-30T23:50:00+00:00|[226.232, 288.448]|[351.870, 410.770]|[352.025, 411.478]|

The national-service publisher distinction remains explicit: MGM is the domestic origin for Turkey, but its Chinese, New Zealand, African and other foreign airport reports are redistribution, not proof of accessing those countries’ domestic feeds. The existing universal MGM adapter needs only registry rows for LTAC/LTFM. Fixed host, station binding, at most ten stations per batch, one-minute cache and bounded retries are shared—not city-specific trading logic.

IMD uses the anonymous public form POST (`icaos=VILK`, `type=metar`), not a login. Moscow uses the official producer-linked display path `/219`. Both enter the same station-temperature parser, append-only WORLD observation ledger, current-temperature reader and revision reseed path. The inherited posterior/q/auction/command chain remains the single implementation.

## Continued admitted-source identity

|City|Cumulative distinct pairs|Exact|Mismatch disposition|
|---|---:|---:|---|
|Tokyo|152|152|RETAIN_EXISTING_ADMISSION|
|Toronto|57|57|RETAIN_EXISTING_ADMISSION|
|Seoul|51|51|RETAIN_EXISTING_ADMISSION|
|Busan|28|28|RETAIN_EXISTING_ADMISSION|

No mismatch was observed in these cumulative admitted-origin windows. These are finite captured windows, not a promise that all future reports agree or a claim that an ongoing background validator has been deployed.

## Complete configured-city inventory and limitations

`ROUND4_NATIONAL_SURVEY.md`, `round4_national_survey.csv` and JSON contain all 54 actual configured cities, each candidate/access disposition, exact counts, mismatch values, and per-city interval comparisons. `round4_comparison.json` contains 111 city/channel rows, 2,367 distinct channel/station/time pairs and 2,341 matches. These totals include reference/redistributor comparisons; they are not independent meteorological trials or 54 completed domestic-origin experiments. 503 availability brackets are preserved.

The free-source access survey is complete as an inventory, but the stronger claim of a finite anonymous domestic-origin publication bound for every city remains unproven. Sources requiring registration, inaccessible anonymous station payloads, incompatible clock grids and periods without a new paired observation remain explicit unknowns. No unknown is filled with zero, no-better-source-exists, or an invented lag. Hong Kong compares native spot products, not its final daily settlement publication.

Concrete nonpromotions: Madrid AEMET 3129 has 22/25 matches (e.g. 28.5°C rounds to 29 while WRH is 28); Helsinki FMI 40/48 (7.6→8 versus resolver 7); Munich DWD 34/45 (6.5→7 versus resolver 6). Warsaw’s fresh 2/2 does not erase its prior mismatch and remains physical-only. Singapore’s prior 27/49 mismatch evidence is retained. HKO RHR 29°C versus same-clock native CSV 28.5°C is a spot-product mismatch under the configured truncation law.

Amsterdam’s free public KNMI METAR page gives 3/3 matches but its measured 23:55 report was slower: 526.307–592.233 s versus AWC 168.977–230.178 s. It is unused as an alternative. KNMI’s anonymous NetCDF attempt is separately recorded with its clock-grid/rate-limit limitation. Milan’s public MeteoAM API gives 48/48; Mexico City and Sao Paulo via MeteoAM give 23/23 and 24/24, without a proven lead over their current path. They remain unused. New Zealand uses free public METAR redistribution comparisons; commercial MetService routes are excluded.

Met Office’s free plan requires registration (360 calls/day); Météo-France’s public 6-minute/hourly observation API also requires an account; REDEMET’s station API required a key. No account was created or paid route purchased. Chinese origin catalogs, BMKG/NiMet/SAWS access failures and the other country-specific results are itemized rather than mislabeled as native station measurements. Source references and 71 preserved access outcomes are in `round4_public_access.json`.

## Correctness, security, concurrency and rollback

[HIGH] transport authenticity — `src/data/station_temperature_adapters.py:229` — Moscow’s reachable free origin uses plaintext HTTP, so observed value identity and stored SHA256 do not authenticate future data in transit. A public HTTPS/signed transport or authenticated independent corroboration is the concrete remaining mitigation. The HTTPS attempt failed; no TLS verification was disabled. This risk is not solved by fixed-host validation and is explicitly not a deployment-safety claim.

Fixed adapter URLs cannot be replaced by arbitrary config URLs; station IDs and display IDs are validated; automatic redirects are disabled for the public METAR runtime clients. Wrong station/clock/unit, NIL/future reports and same-clock conflicting values are rejected. HTTP failures remain optional missing evidence, not proof of source absence; they do not stop serving an otherwise valid incumbent. Credentials/headers never enter observation prints. The downloaded KNMI documentation blob with authorization examples is intentionally not published.

No schema migration is introduced in Round4. Existing K1 ownership, INV-37 write paths, durable observation-before-reseed ordering, and execution authority remain unchanged. No q-versus-market gate or sizing shrink is added. Rollback removes the four new registry routes and reverts the two adapter changes through a reviewed release; retain all collected observations and the inherited revision-capable index. No live restart or rollback command was executed.

## Executed validation and reproduction

|Suite|Passed|Skipped|Failures/errors|
|---|---:|---:|---:|
|round4_resume_validation|435|1|0|
|round4_final_validation|1059|1|0|
|round4_final_proof_validation|109|1|0|

The operator’s reported base comparison was 1,041 passed/one skip on the 15 inherited changed modules; this round runs the same module list plus added assertions and changes no unrelated debt. The focused ten-module suite and 15-module suite overlap and must not be added. Changed-surface map maintenance passed for ten source/config/test/fixture paths. 798 original and 324 resumed HTTP-body checksum comparisons passed. The new resume window ran 15 bounded rounds from September 30 23:52 UTC through October 1 00:06 UTC; the earlier preserved Round4 windows start at September 30 22:54 UTC.

```bash
cd /Users/leofitz/zeus/.claude/worktrees/fast-obs-survey-r4
PY=/Users/leofitz/zeus/.venv/bin/python
# Optional audit parser dependencies; not production runtime dependencies:
$PY -m pip install --target artifacts/fast_obs_audit/python_deps -r artifacts/fast_obs_audit/round4_audit_requirements.txt
# Offline reductions of preserved response evidence; no weather/network calls:
$PY -B artifacts/fast_obs_audit/round4_compare.py
$PY -B artifacts/fast_obs_audit/build_round4_survey.py
$PY -B -m pytest -q tests/test_station_temperature_adapters.py tests/test_fast_obs_receipt_chain.py tests/test_current_temperature_delivery.py tests/test_observation_reaction_chain.py
```

`round4_cumulative.py` and `round4_promote_mgm.py` additionally regenerate evidence-backed registry entries and therefore mutate the feature worktree; they are not read-only report commands. Raw weather responses and request/receipt metadata remain in the named `round4*` directories. Test module lists and numerical results are in `round4_validation.json`. `round4_resume_race.py` refuses to overwrite an existing capture window. No source collection job is left running by this round.

## Publication and operator-only boundary

All Round4 implementation/evidence is confined to `feat/fast-obs-survey-r4`. Principal implementation commits before the final evidence/report update are `634266ae9` (public adapters/Moscow), `cb7e91572` (IMD/cumulative proof), and `c45e04c77` (Ankara/Istanbul). No action is requested on `live` or the prior feature branch. Future deployment and any registration are operator-controlled and unexecuted; verify locally. There is no delegated task to redo the connector-accessible work already completed. The delivery response supplies the verified published branch HEAD.

## Post-round disposition: Moscow route withdrawn (2026-10-01)

The Moscow UUWW route (`metaviatelecom_metar`, `http://display.meteocenter.ru/219`) is removed from the registry and code before merge. Its only reachable transport is plaintext HTTP. Re-check on 2026-10-01: HTTPS still fails certificate verification (curl `ssl_verify_result=18`, self-signed). Value identity (4/4, plus 04:00Z 6°C = AWC 6°C) cannot authenticate future responses in transit. Requiring an AWC/WRH corroboration per value would remove the publication lead that justified the route. Moscow stays on its existing AWC/WRH path; UUWW AWC vs WRH agreed 46/46 over the prior 24h. Re-admit only with an authenticated transport.

## Post-round disposition: Lucknow IMD route withdrawn (2026-10-01)

The Lucknow VILK route (`imd_olbs_metar`) is removed from the registry before merge. Its parser scanned concatenated page text from the requested station header to the next `=`, so `METAR VILK ... NIL` followed by another station's report produced a VILK value from the foreign report (external review REQ-20261001-001836-85dd89). Value identity (2/2) cannot protect a parser that crosses report boundaries. Lucknow stays on its existing AWC/WRH path. Re-admit only after a bounded single-report parser rejects the NIL/foreign-report payload and the recorded value-identity/latency evidence still binds.
