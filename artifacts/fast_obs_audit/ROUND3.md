# Round 3 — REQ-20260930-114240-ee2a70

## Scope and constraints
Feature branch only: feat/fast-obs-best-source. No production database writes, live-branch mutation, daemon restart, or deployment. Rebased from ee67338ca onto fetched origin/live 514cdc7d938997bd1e0d9e7ccd5f687edabb73e6; regenerated the schema fingerprint to resolve the sole conflict. Rebase result 65d079fc8b664fc164db8a233c5e3bdc2e744903.

## Execution plan
1. Reproduce current exit-safety and materializer/reader/command failures; repair the side inconsistent with current law, without skipping tests or weakening live safety. Commit/push each validated item.
2. Finish the configured-city national-origin inventory with exact candidate identifiers, access/probe outcomes, pointwise contract-value comparisons and bounded first-availability intervals. A missing credential or no transition is unknown, never evidence of inferiority. Only value-identical and demonstrably faster transports become active fast sources.
3. Extend Tokyo/Toronto to at least 48 exact-time pairs if accessible history permits; preserve all mismatches and demote when contradicted.
4. Diagnose and repair universal US resolver-equivalent precision across all 11 US stations, preserving hourly/SPECI product membership.
5. Exercise real entry and resting-order replacement auction selection downstream of committed observations with nonproduction databases and a fake external venue. Report actual hop times, not enqueue-as-completion.
6. Copy the canonical observation table read-only into a nonproduction database, measure index migration and revision preservation, and write a deployment/rollback runbook; do not deploy.

## Safety and review boundaries
Retain single probability/execution authority, K1 DB ownership and INV-37. Network acquisition stays outside canonical write leases. Data absence may degrade evidence but must not fabricate probability or block an otherwise valid incumbent from serving. No q-vs-market gate or shrink. Compare under exact contract units/rounding at equal station/UTC-valid times; do not substitute nearby observations. After schema migration, retain a revision-capable index on rollback and never remove historical evidence.

## Results
In progress. Only executed measurements and tests will be recorded below.

## Item 1a — exit safety (completed)
Reproduced 18 exit-safety and 7 materializer-suite failures on rebased 514cdc7d9: 25 failed, 864 passed across the four modules. Exit safety now passes all 359 tests (40.16 s); the targeted RED handoff/B2/negative-authority subset passes 30. No skipped or xfailed tests were introduced.

Real defects fixed: final SDK expiry recheck after client/certificate preparation; cycle-owned RED handoff accepted by protective SELL only after existing exact adjacent-event/hash verification; explicit outer transaction and rollback for atomic RED M/I/projection commit; existing canonical RED resting SELL adoption makes no new submission and no longer falls into a fresh-capital replacement path merely because POSTED retired the old submit handoff. Negative tests cover forged writer identity and append/commit failure.

Fixture corrections: command execution atoms are distinct from position-level lifecycle telemetry; terminal cancellation following partial fill remains partial execution. Collateral tests now assert network -> pre-submit lease -> venue -> final SDK receipt lease -> ACK lease. RED scenarios use the actual cycle handoff writer and canonical chain fields; missing handoffs never mint execution authority. Snapshot/lease fixtures satisfy current APIs. A raw external order id without command/size binding does not close exposure on RED status alone.

Validation: round3_exit_green.xml; round3_baseline_feature.xml/log. Baseline comparison reported by operator is confirmed at the same failure names on the newly fetched base; no additional baseline checkout was needed to establish that the old tests failed before these edits.

## Item 1b — materializer/reader/command baseline (completed)
All 531 tests pass (31.93 s), including all seven reproduced baseline failures. Production math and migration code unchanged. Three old migration tests assumed the retired trade_authority_status label should be translated to runtime authority or physically dropped. Current migration preserves obsolete schema columns but retires rows with no current runtime_layer; the tests now assert that behavior and explicit legacy constraints. Four Day0 cases now provide a real current observation plus complete causal provider/ENS vectors and a content-hashed typed residual, rather than assuming an old SimpleNamespace residual alone can sponsor a current carrier. The original owner, source/clock override, and no-likelihood-recompute-under-writer assertions remain.

Item 1 total: 359 exit-safety tests and 531 materializer/reader/command tests pass (890 combined test cases across separate suite runs). No xfail/skip/production-law relaxation was added.

## Item 4 — US native resolver precision (completed with explicit latency boundary)
A 72-hour comparison across all eleven configured US cities produced 881 exact-time pairs. T-group-only decoding disagreed or lacked a value in 25 pairs; whole-degree body decoding disagreed in many more. Chicago's 15:30/15:40 UTC SPECI reports have body 17C and T0167: AWC gives 16.7C -> 62.06F -> bin62 while WRH's published air_temp_set_1 is 62.6F -> bin63. However Seattle/other SPECI rows use the T-group value. Choosing body for every SPECI, choosing by SLP, or a city literal is not a universal resolver reconstruction.

The physical METAR T-group parser remains unchanged. The universal station registry now acquires the actual existing WRH native numeric field for all 11 US F-settled stations as one batched station-set/unit request per minute, preserving hourly/SPECI view membership. The generic ingest writer and trace preserve native Fahrenheit without F->C->F roundtrip; exact-station/unit validation and receipt clocks remain mandatory. It is an acceleration of the existing resolver acquisition, not a claim that WRH beats AWC or an alternative-feed promotion. AWC continues independently; no model gate or blanket uncertainty shrink was introduced.

The optional route shares the existing canonical noaa_wrh_<station> ledger channel. Old daily-writer raw METAR rows remain readable; no JSON-envelope migration is required. Optional HTTP/auth/quota failure remains a deferred source fetch, never proof of source absence. One-minute per-process batch/error cache avoids eleven-fold request amplification; 429 Retry-After is honored. Aggregate quota with unrelated processes remains outside this cache's authority.

Executed: 352 passed, 1 optional netCDF4 skip across station adapters/receipt/WRH product/observation ledger/current delivery/FMI/Day0/ingest modules. The new all-city tests replay every native field and assert strict station/view/native-unit handling; a committed-world-to-reseed integration proves 62.6F stays 62.6F. Raw measurement pairs are retained in tests/fixtures/station_temperature/us_resolver_precision.json.gz; script and numerical comparison under round3_us/.

|City|Pairs|Body matches|T-group matches|METAR-vs-SPECI heuristic matches|
|---|---:|---:|---:|---:|
|Atlanta|76|48|75|76|
|Austin|83|43|82|82|
|Chicago|90|55|83|85|
|Dallas|76|44|76|74|
|Denver|76|43|71|76|
|Houston|72|44|72|72|
|Los Angeles|75|46|75|75|
|Miami|77|46|76|76|
|NYC|94|63|86|90|
|San Francisco|77|43|77|75|
|Seattle|85|58|83|78|

## Item 6 — copied-table migration and operator runbook (completed)
The actual production WORLD observation table was opened read-only/query-only in a read snapshot and all 485,931 rows copied into a new nonproduction WAL/FULL database. Source/copy row hashes agree. The exact index migration plus commit took 782.067 ms; repeat 0.058 ms; copied file 147,955,712 bytes. Before/after row SHA256 cbec366910c19f65fbbb29770b841eb17001d748daa13cad354e248392111e06; integrity_check=ok; ABAA regression inserted [True, True, True, False] and was rolled back. This is a table-complete copy, not a full copy of unrelated WORLD tables, nor production contention/power-loss proof.

ROUND3_DEPLOYMENT.md contains the exact operator-only pause/flatness/unload/exclusive-cutover/index-transaction/restart/resume-generation/rollback sequence, inspected against the actual launchd plists and deploy_live.py. It deliberately requires a flat maintenance window; no capital monitoring blackout is authorized. It preserves prior operator pauses and requires exact CAS generation on resume. The live-trading restart owns the venue-heartbeat watchdog ordering. Six-column index and revision-aware append semantics must survive rollback; no destructive deduplication or database restore over newer evidence. All runbook mutation commands are unexecuted, verify locally.
