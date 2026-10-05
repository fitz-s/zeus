# Round 5 — fast-observation closeout

Request: REQ-20261005-040720-130256. Candidate: `8fd9248238a4590a2e6087f9a1ecf80dc09b84fd`, the supplied snapshot of live `4fc49964784b4bf25fd68fac0b8a05c128793d09`. Work is published only on `feat/fast-obs-round5`.

**Verdict: the captured live writer inventory is established; complete production latency and historical economic reconciliation are not certified. Three finite residuals, R1–R3 below, replace an open-ended investigation.** Do not interpret a unit-test pass, archived writer count, or aggregate phase label as proof of a complete production money path.

## Access and execution boundary

`round5_inputs/CONTEXT.md` was read first after WebCodex_Demo failed with `tunnel_client_not_seen`. Zeus failed with the same transport error and CodexPro failed to connect. A later WebCodex check still failed. GitHub connector reads and feature-branch commits worked. No local scratch checkout, production shell, logs, database, or chain RPC was accessed. Direct cloud DNS/binary download also failed. The compressed 5,352-row CSV could not be decoded through the UTF-8-only connector; its summary, audit source and operator's flagged-row follow-up were read, but the CSV was not independently re-run this turn.

An attempted temporary isolated hosted-audit workflow write was denied. No workflow was created or run, and the denial was not bypassed. The successfully published changes use ordinary GitHub file commits. Forty-nine isolated standard-library tests were executed in the cloud container against source bytes matching the published Git blobs. No production numbers are inferred from those fixtures.

There were no production database writes, restarts, deployments, or pushes to `live`, `audit/fast-obs-round5`, or earlier feature branches. None of the four explicitly excluded source files was edited. No new weather route was promoted on unmeasured speed evidence.

## Closure ledger

| Deliverable | Terminal disposition of this round | Acceptance owner |
|---|---|---|
| A — captured live channels and writing | DONE for the supplied snapshot: all five registry `fast_admission` routes have matching print rows; KMA uses a different event surface | Evidence below and `round5_inputs/` |
| A — fresh four-city speed recheck / absolute-best claim | RESIDUAL R1: run the delivered bounded four-city HTTP race; retain incumbent unless exact value and strict speed tests pass | Operator-side agent; no credentials needed |
| B — production p50/p90/p99 and complete attribution | RESIDUAL R2: run the read-only collector, complete the precisely named source/wake hooks, and fill the disposition denominator | Operator-side agent with local logs/DBs |
| C — final economic adjudication of all flagged orders | RESIDUAL R3: decode the exact archive, export linked native facts, reconcile the six cases and the projection/snapshot flags | Operator-side agent with native wallet/receipt history |

A correct loop may consume the newest revision while earlier revisions are superseded. It may choose HOLD, KEEP or NO_TRADE and therefore produce no ACK. Those must be explicit dispositions, not invented missing orders. Conversely, fast writing alone does not prove that the corresponding q was served.

The defensible source-selection closure is **no faster free public route was established among the examined accessible candidates**. A registration requirement, failed request or finite survey does not prove that no faster route exists anywhere. The report closes the operational selection, not an unbounded nonexistence theorem.

## A. Captured live inventory

### Five admitted fast routes are writing

| City / station | Current admitted channel | Extract count | Last fetched UTC, 2026-10-05 |
|---|---|---:|---|
| Ankara / LTAC | `mgm_metar_temperature` | 64 | 08:55:01.870602 |
| Istanbul / LTFM | `mgm_metar_temperature` | 58 | 08:55:01.870602 |
| Lucknow / VILK | `imd_olbs_metar_temperature` | 59 | 08:48:18.942377 |
| Tokyo / RJTT | `jma_amedas_temperature` | 190 | 08:58:13.666546 |
| Toronto / CYYZ | `eccc_swob_temperature` | 33 | 09:01:52.291855 |

Revising Round 4 Moscow: the plaintext HTTP route was withdrawn and is absent from the candidate registry. Its absence is intentional, not a silent writer failure. Current role validation lives in `physical_current_sources.py`; the old blanket `settlement_grade` vocabulary is not the live role model. Current roles are `canonical_resolver`, `fast_admission` and `physical_only`.

Revising a print-ledger-only KMA check: RKSI/RKPK write `WORLD.opportunity_events`, not `observation_prints`. CONTEXT reports 524 KMA-transport events in the last 200,000 event rows; that is not a 24-hour count. The new collector exports each station separately in an explicit UTC window.

The file named `live_route_prints_24h.json` spans October 4 around 00:00 through October 5 around 09:05, over 33 hours. These are writing-presence counts, not strict 24-hour rates. No query bug is inferred without its query; R2 recomputes the window with timestamp-aware comparisons.

### All 54 configured cities

Notation: **R** = `noaa_wrh_<lowercase ICAO>` resolver product plus the generic METAR lane **M** (`aviationweather_metar`). US R is the hourly Fahrenheit product; other R contracts use Celsius/all-reports. **W** = WU station-history resolver (`wu_icao_history`), with M when present. Ogimet-prefixed rows are historical/reference evidence, not proof of a newly admitted fast route. M is not a blanket identity certificate for a US resolver's numeric field. **E** = existing KMA event-based fast transport. Extra formal admitted routes are explicitly marked F; other registry extras are P (physical-only).

Closures below refer to the dated, checked candidates in `round4_national_survey.csv/json`, not a newly performed October 5 first-availability race. The fresh four-city probe is R1. No-faster closure means retain the known incumbent; it is not an unsupported universal-existence claim.

| City | Station | Resolver / current extra channels | Checked free-candidate closure |
|---|---|---|---|
| Amsterdam | EHAM | R + M; KNMI P configured without print evidence | KNMI public METAR previously 3/3 but slower; NetCDF clock mismatch/rate limit; R1 recheck |
| Ankara | LTAC | R + M; MGM F, writing | Retain value/speed-proven MGM |
| Atlanta | KATL | R hourly F + M | Retain exact native resolver precision; no universal METAR reconstruction |
| Auckland | NZAA | W + M | Commercial origin excluded; free MGM redistribution slower |
| Austin | KAUS | R hourly F + M | Retain exact native resolver precision |
| Beijing | ZBAA | R + M | No exact anonymous CMA airport payload obtained; MGM redistribution slower |
| Buenos Aires | SAEZ | R + M | Native public access failure/challenge; MGM slower |
| Busan | RKPK | R + E | KMA event transport retained; print-ledger absence expected |
| Cape Town | FACT | R + M | SAWS public access failure; MGM slower |
| Chengdu | ZUUU | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Chicago | KORD | R hourly F + M | Preserve resolver-native field; AWC counterexamples retained |
| Chongqing | ZUCK | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Dallas | KDAL | R hourly F + M | Preserve resolver-native field; AWC counterexamples retained |
| Denver | KBKF | R hourly F + M | Retain resolver field; extract's last R/M at about 05:11/05:26 needs source-cadence check, not automatic outage diagnosis |
| Guangzhou | ZGGG | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Helsinki | EFHK | R + M; FMI P, writing | Repeated rounded-value mismatch; no settlement-fast promotion |
| Hong Kong | HKO HQ | HKO daily authority; current 1-min mean and rhrread spot writing | Preserve distinct spot/daily products; spot JSON disagreement not final daily settlement proof |
| Houston | KHOU | R hourly F + M | Retain exact native resolver precision |
| Istanbul | LTFM | R + M; MGM F, writing | Retain value/speed-proven MGM |
| Jinan | ZSJN | W; `wu_station_history_temperature` resolver writing; WU current P writing | Existing WU origin works; MGM superiority over the current physical path not established |
| Jakarta | WIHH | W + M | BMKG public challenge; no WIII substitution; MGM slower |
| Jeddah | OEJN | R + M | No exact anonymous NCM airport payload obtained; MGM slower |
| Karachi | OPKC | R + M | PMD service page was not an observation feed; MGM slower |
| Kuala Lumpur | WMKK | R + M | Public products/aviation access did not yield exact anonymous observations; MGM slower |
| Lagos | DNMM | W + M | NiMet public access failure; no demonstrated faster redistribution |
| London | EGLC | R + M | Met Office free account/key required; not obtained without operator action |
| Los Angeles | KLAX | R hourly F + M | Retain exact native resolver precision |
| Lucknow | VILK | R + M; IMD F, writing | Retain anonymous IMD POST route |
| Madrid | LEMD | R + M | AEMET free station XML mismatches; not settlement-grade fast |
| Manila | RPLL | R + M | PAGASA previously 38/38 without demonstrated lead; R1 recheck |
| Mexico City | MMMX | R + M | Native access failure; MeteoAM redistribution had no demonstrated lead |
| Miami | KMIA | R hourly F + M | Retain exact native resolver precision |
| Milan | LIMC | R + M | MeteoAM previously 48/48 without demonstrated lead; R1 recheck |
| Moscow | UUWW | R + M | HTTP-only origin withdrawn for authenticity risk; no faster secure free alternative established |
| Munich | EDDM | R + M; DWD P, writing | Repeated same-time rounded-value mismatches |
| NYC | KLGA | R hourly F + M | Retain exact native resolver precision |
| Panama City | MPMG | R + M | AAC previously 2/2 with overlapping speed bounds; R1 recheck |
| Paris | LFPB | R + M | Meteo-France open API requires account; no anonymous exact replacement established |
| Qingdao | ZSQD | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| San Francisco | KSFO | R hourly F + M | Retain exact native resolver precision |
| Sao Paulo | SBGR | R + M | REDEMET registration required; Italian redistribution not faster |
| Seattle | KSEA | R hourly F + M | Retain exact native resolver precision |
| Seoul | RKSI | R + E | KMA event transport retained; print-ledger absence expected |
| Shanghai | ZSPD | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Shenzhen | ZGSZ | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Singapore | WSSS | R + M | Changi S24 rounded-value mismatches retained; same airport label insufficient |
| Taipei | RCSS | W + M | ANWS connector transport failure; no RCTP substitution; MGM slower |
| Tel Aviv | LLBG | R + M | Free IMS XML did not establish an exact LLBG stream; MGM slower |
| Tokyo | RJTT | R + M; JMA F, writing | Retain admitted route; current registry cumulative proof 152/152 |
| Toronto | CYYZ | R + M; ECCC F, writing | Retain admitted route; current registry cumulative proof 57/57 |
| Warsaw | EPWA | R + M; IMGW P, writing | Earlier exact-time mismatch remains disqualifying |
| Wellington | NZWN | R + M | Commercial origin excluded; free redistribution slower |
| Wuhan | ZHHH | R + M | No exact anonymous CMA airport payload obtained; MGM slower |
| Zhengzhou | ZHCC | R + M | No exact anonymous CMA airport payload obtained; MGM slower |

The October 5 web checks could display PAGASA RPLL reports and KNMI EHAM METAR; the web fetches for AAC Panama and the dated MeteoAM API failed. Cached web rendering is not local first availability, so no new pair count or lead was claimed. Existing physical-only rows do not become admitted merely because they are writing.

## B. Production latency: diagnostic fix delivered, measurements are R2

No production p50/p90/p99 or unmatched-observation count was computed this turn: the logs and raw databases were inaccessible. The intended audit window is **[2026-10-04T09:00:00Z, 2026-10-05T09:00:00Z)**, corresponding to 04:00 CDT boundaries. The timestamped source extract is not a substitute. Earlier 101–221 ms examples were controlled harness results, not production distributions.

Concrete source findings at the candidate:

- `src/ingest_main.py:2269–2297` selects only `max(prints, observed_at)` and emits SOURCE_COMMITTED only when `advanced`; it cannot provide one record per committed revision.
- The old `completed_trace` in `src/runtime/observation_reaction_trace.py` selected the latest same-content source commit. A→B→A corrections or separate same-content commits are ambiguous under that join.
- `readiness_state` is an UPSERT projection (`src/state/readiness_repo.py`); its current pointer cannot prove every historical readiness transition. The actual dependency key is `dependency_json.dependencies[role=soft_anchor_posterior].posterior_id`.
- The examined trace schema has no actual reactor-consumption wake ID/time. A publisher's boolean is not consumer receipt. A venue ACK log is not a durable ACK unless its event ID exists on TRADE.

The committed telemetry fix adds full immutable observation-reference construction, trace IDs and actual stage clocks, exact readiness-pointer capture inside the existing posterior-ready hook, an explicit diagnostic reference status, and ambiguity/clock-order rejection. It does not write any database or gate serving. Hooks for per-row source commit and actual wake receipt are supplied, **but their producer/consumer call sites are not yet wired**. These are part of R2, not claimed completed instrumentation.

`round5_closeout.py` reads each database through mode=ro/query_only, handles .gz trace logs, computes conditional p50/p90/p99 and cardinalities per hop, reconstructs legacy references only where an exact native row is unique, and checks ACK event IDs. It refuses nearest-time matching and A→B→A ambiguity. Missing ACK is classified separately from a proved KEEP/HOLD/NO_TRADE decision. The collector reports separate database snapshot times and changing log files, not a fictitious cross-database atomic snapshot.

Pairing key: full WORLD print revision (row ID, station, channel, exact valid and receipt timestamps, native value/unit, raw hash) → exact posterior hash and family → readiness dependency → wake ID mapped to that posterior → served q version → native command ID → persisted ACK event ID. Input observation ID is not replaced by publish time. KMA needs its event/raw-report revision as an analogous typed input reference; an empty print ledger is not a failed KMA observation.

## C. Order lifecycle and disputed quantities

The supplied audit covers 5,352 commands at a consistent TRADE snapshot established **2026-10-05T09:04:49.047117Z**. FORECAST annotation was a later read, not the same cross-DB transaction.

| Command state | ENTRY | EXIT | Total |
|---|---:|---:|---:|
| CANCELLED | 1900 | 44 | 1944 |
| EXPIRED | 634 | 43 | 677 |
| FILLED | 1655 | 803 | 2458 |
| REJECTED | 115 | 100 | 215 |
| SUBMIT_REJECTED | 52 | 6 | 58 |
| Total | 4356 | 996 | 5352 |

Revising the word “positions” in the supplied summary: settled 3456, voided 1718, NO_POSITION_RECORD 151, day0_window 16, active 4, admin_closed 4 and economically_closed 3 sum to 5352 because they are **position-phase labels joined at command grain**, not a distinct-position census. Three mismatch command rows can refer to fewer positions. A closed command is not proof its position is economically closed.

The six conflict flags are produced by a zero-matched terminal venue snapshot colliding with positive execution evidence. They do not themselves compare acquired shares with projected residual shares. The current `VenueOrderTruthReducer` already preserves terminal remainder while incorporating independent fills; retained contradictory observations must not simply be deleted to make the audit green.

| Command | Supplied facts | Determination supported now | Finite repair decision under R3 |
|---|---|---|---|
| `086d130a613546f2` | 2.5 CONFIRMED; VOIDED; shares 0; chain null | Acquisition/disposition proof is incomplete; real remaining exposure is not established by the input | Verify native trade/token/funder; reconcile sales, transfers and redemption. Restore canonical exposed state only if residual is positive; otherwise preserve actual economic closure and cost |
| `37c227a8a0f24596` | 5 CONFIRMED; VOIDED; shares 0; chain null | Same risk; do not certify never-filled | Same bounded native-ledger reconciliation for 5 shares |
| `37e80adb681a416b` | 38 CONFIRMED; VOIDED; shares 0; chain null | Same risk; do not equate EXPIRED with no execution | Same bounded native-ledger reconciliation for 38 shares |
| `79e6322ae8344a92` | 39.6 CONFIRMED; SETTLED; position/chain 19.6 | A 20-share disposition requires proof, not an automatic +20 projection repair | Match precisely 20 shares of prior SELL/transfer/redeem/burn or correct duplicated/foreign fill attribution; only an unexplained residual warrants projection correction |
| `19a58c0a03d8416d` | 28.52 CONFIRMED; SETTLED; position/chain 28.52 | No quantity shortfall shown; terminal-zero order snapshot is contradicted | Validate receipt lineage and settlement payout; do not alter the matching position quantity just to clear the order-evidence flag |
| `5c25a7b16af841b6` | 25.71 CONFIRMED; SETTLED; position/chain 25.71 | Same: no quantity shortfall shown | Same bounded receipt/payout reconciliation; retain stronger confirmed execution evidence |

The first three sum to **45.5 claimed confirmed shares**, not $45.50 of proved loss or proved currently held inventory. A current on-chain zero balance alone does not account for acquisition cash, sale proceeds, fees or redemption. The June trades' late July 13 evidence timestamp does not make them July executions. Do not use that ingestion time to answer the post-September-21 question.

Phase flags: `0e0ac1edba2e4619` and `cad0050955ae4cf7` share position `d3840f5b`; the third is `59fc7867387b4f04`. Recompute the canonical projection from valid event history and any sanctioned terminal-restore evidence; do not blindly copy the last phase string. Snapshot flags: three `adopted_exit_*` commands. Export their actual IDs and native order/envelope identity; never fabricate a backdated executable snapshot.

No chain transaction or payout receipt was obtained this turn, so no unconditional accounting repair is authorized by this report. The delivered collector emits the exact post-2026-09-21 command cohort, flagged rows and voided-with-confirmed-fill rows. Until R3 runs, “historical only; defect no longer live” is not a supported claim.

## R1–R3: finite operator execution and acceptance

All commands here are **unexecuted on the operator machine — verify locally**. No command below restarts a daemon or writes a production database.

Prepare an isolated feature worktree without switching the live checkout:

```bash
ROOT=/Users/leofitz/zeus
WT=/Users/leofitz/zeus/.claude/worktrees/fast-obs-round5
PY=/Users/leofitz/zeus/.venv/bin/python
git -C "$ROOT" fetch origin feat/fast-obs-round5
git -C "$ROOT" worktree add --detach "$WT" origin/feat/fast-obs-round5
cd "$WT"
$PY artifacts/fast_obs_audit/test_round5_trace.py
$PY artifacts/fast_obs_audit/test_round5_closeout.py
$PY artifacts/fast_obs_audit/test_round5_public_recheck.py
```

If the path already exists, inspect its branch/status rather than overwriting it. Use the operator's normal approved worktree settings setup for repository imports; do not copy credentials into published artifacts.

### R1 — finish the four anonymous speed recaptures

```bash
$PY -m pip install --target artifacts/fast_obs_audit/python_deps \
  -r artifacts/fast_obs_audit/round4_audit_requirements.txt
$PY artifacts/fast_obs_audit/round5_public_recheck.py \
  --out artifacts/fast_obs_audit/round5_free_recheck --rounds 61
```

Acceptance: four explicit station dispositions in `verdict.json`; exact-time raw/rounded pairs and mismatch values; native/peer negative-to-positive brackets at the same matched instant; transport refusals recorded without bypass. A newly eligible candidate has no prior or current mismatch and its upper availability bound precedes both current comparator lower bounds. Only then add the existing universal registry/parser route and its receipt→q tests; otherwise close that candidate as retain-incumbent, with the observed slower/overlap/access reason. No paid API or operator registration is requested. The probe refuses to overwrite an existing capture.

### R2 — measure production, then finish only identified instrumentation gaps

```bash
$PY artifacts/fast_obs_audit/round5_closeout.py production \
  --root "$ROOT" \
  --start 2026-10-04T09:00:00Z --end 2026-10-05T09:00:00Z \
  --out artifacts/fast_obs_audit/round5_live_verified
```

Exit 2 deliberately means incomplete proof; `production_latency.json` still contains every measured hop and named missing stage. First use retained logs and native evidence; do not deploy new instrumentation before attempting the historical reconstruction.

Required bounded hook work, only if missing in the capture: in `_day0_current_temperature_source_tick`, retain the actual inserted row IDs, clock the successful WORLD commit immediately and emit the supplied `emit_observation_committed` once per inserted row after commit (including backfilled/nonadvancing rows with an explicit disposition), not once for max(prints). Carry the consumed immutable reference in posterior provenance rather than attempting to guess an A→B→A revision afterward. At the materializer's successful wake publisher, record wake_id→posterior_identity_hash; at the actual `src/main.py` consumer record the same wake_id and receive clock. Do not read the latest family hash to label an older wake. Log superseded/coalesced and no-action decisions with their exact consumed q/decision identity. The four forbidden files remain untouched. These are observational hooks, not new q-vs-market gates or trading commands.

Acceptance: per-hop p50/p90/p99 with n, explicit unmatched counts and mutually understood denominator grains; exact same-input family fanout, canonical ACK proof, explicit no-action and superseded outcomes; zero nearest-time joins; negative/cross-clock samples reported rather than clamped. Missing event-based KMA input must be handled as event identity, not silently omitted. Existing structured collector deliberately does not infer arbitrary Day0-derived q aliases or parse every freeform auction line; join the typed certificate/decision identity when those are the remaining gap. The source and wake helper APIs alone do not satisfy this acceptance.

Run the existing integration modules after the bounded hook edits:

```bash
$PY -m pytest -q tests/test_observation_reaction_chain.py \
  tests/test_current_temperature_delivery.py tests/test_station_temperature_adapters.py \
  tests/test_fast_obs_receipt_chain.py
```

A deployed verification window requires separate operator deployment authority. This round performs no deployment. Historical missing consumer telemetry cannot honestly be manufactured after the fact.

### R3 — exact archived census and native economic adjudication

```bash
$PY artifacts/fast_obs_audit/round5_closeout.py snapshot \
  --root "$WT" --out artifacts/fast_obs_audit/round5_snapshot_verified
```

Acceptance: exactly 5352 unique command IDs in `every_command.jsonl.gz`, the input summary counts reconcile, all six conflicts/three phase flags/three snapshot flags are enumerated, and the post-cutoff subset is explicitly printed in `snapshot_closeout.json`. Use `round5_live_verified/flagged_evidence.json` from R2 for the canonical related command/order/trade/position/settlement evidence and exact snapshot condition binding. An unavailable table or missing snapshot is a residual, not an empty economic ledger.

For each of the six commands, complete one ledger row with: exact funder and token/condition; native trade identity and chain receipt block/hash; confirmed BUY/SELL shares and cash/fees; inbound/outbound transfers; split/merge/redeem/burn legs; residual shares at an explicit chain block; payout/cash allocation. Check `confirmed buys + transfers in + minted - sells - transfers out - burned/redeemed = current balance`, treating token conversion and shared-wallet foreign trades explicitly. Reconcile acquisition cost and proceeds separately; don't confuse shares with dollars.

Repair selection is finite: (1) a misattributed/revoked trade needs evidence-backed canonical fact correction/revocation, not deletion; (2) proven positive residual after an erroneous terminal projection uses the existing chain-reconciliation terminal-exposure restoration plus `TERMINAL_RESTORE_EXPOSURE` review evidence; (3) proven zero residual with a valid exit/redemption needs the true canonical closing event and economic attribution, not a never-filled interpretation; (4) matching settled quantities need no share correction solely because an older venue snapshot said zero; (5) phase projection discrepancy is corrected through the canonical projector with source events preserved; (6) adopted-order snapshot absence is resolved with native identity/recovery evidence or explicitly retained as historical unverifiable evidence, never forged historical executable authority. These are proposed operator repairs, not executed mutations.

R3 closes only when each listed identity has a factual no-repair or canonical-repair determination, with any unallocated dollars stated numerically, and the post-September-21 cohort is either clean or has its own exact reproducing IDs. Zero current token balance is not that acceptance by itself.

## Validation and rollback

Executed in cloud: 15 telemetry tests, 30 read-only collector tests, four source-comparison tests = **49 passed, zero failed, zero skipped**. Source bytes matched the published blob SHAs; syntax compilation passed. No full Zeus suite or baseline comparison was executed here. No 24-hour production latency test, actual 5352-row reanalysis, chain query or new timed public race was executed.

The runtime change is non-authoritative telemetry only. There is no schema migration and no persisted truth update. Rollback reverts the trace file through ordinary reviewed release while retaining the existing observation index/data and audit evidence. The new posterior diagnostic performs optional read-only identity checks; its production overhead and full-suite compatibility remain R2 validation, not a claimed benchmark.

Sources: exact pinned CONTEXT, summary, live_route_prints_24h, audit_order_lifecycle, cities/physical-current registry, physical-current loader/adapters, ingest tick, observation_reaction_trace, readiness repository/builder, canonical lifecycle/reducer, and dated Round 4 candidate evidence. GitHub default-branch search was orientation only; source findings rely on pinned reads. Public web checks were the official PAGASA and KNMI pages, with failed AAC/MeteoAM fetches recorded as access limitations. No repository file changed trading authority for this report.
