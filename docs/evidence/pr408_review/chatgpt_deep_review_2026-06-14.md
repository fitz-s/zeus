Verdict

DO NOT MERGE until the blocking items below are fixed or explicitly scoped out of live runtime with tests proving they cannot execute.

Confidence is high on the Tier-0 findings I inspected directly: exchange_reconcile.py, src/execution/harvester.py, post_peak_harvester.py, post_peak_backtest.py, the representativeness de-bias loader/fitter/materializer, live config, and the event reactor admission path. Confidence is not high that I covered the whole PR: I could not honestly complete the requested “every changed file / 221 files” read from remote GitHub. The PR /files page only partially rendered in the browser, patch/diff/API routes were not usable in this environment, and several head-SHA paths listed in the partial /files inventory were deleted/404 at the pinned snapshot. I therefore reviewed the highest-blast-radius code paths and registries, but Claude Code must treat this as a deep Tier-0 advisory review, not a full-file certification.

The biggest merge blockers are:

INV-37 cross-DB atomicity violation in the settlement harvester: forecast-class learning/truth writes and trade-class settlement writes are committed via independent connections, not ATTACH + SAVEPOINT.

The terminal-chain-closed phantom absorber can suppress real position drift based on day-end/calendar evidence rather than verified settlement/redeem truth.

The new per-city anchor representativeness de-bias is Law-8-dangerous unless its training provenance is tightened: the fitter query does not prove settlement station, rounding rule, settlement source identity, or per-date de-duplication.

The post-peak harvester is not a demonstrated systematic all-city edge. It is explicitly built from the one London trade and encodes a London-shaped repricing-latency hypothesis; the proof harness is a stub/prospective grader, not an OOS all-city settlement replay.

Live config and event-reactor code still contain one-sided q/size suppressors and gates that conflict with the operator’s “no new gates/caps/haircuts” law.

Coverage and access limits

I opened and inspected the PR page, the pinned tree, the partial /files page, and raw head-SHA versions of the priority files. The PR page itself states the intended changes are per-city station-representativeness de-bias, exchange-reconcile phantom absorption, post-peak harvester scanner/backtest “not wired into executor,” slug discovery, and tests: https://github.com/fitz-s/zeus/pull/408

The PR UI reports 56 commits, not 55 as in the prompt: https://github.com/fitz-s/zeus/pull/408

I could not certify the complete 221-file set. Specific gaps are listed at the end.

BLOCKERS / CRITICAL
1. BLOCKER — INV-37 violation: settlement harvester writes forecasts DB and trade DB through separate connections and commits independently

Category: cross-DB INV-37 atomicity; settlement learning; live settlement correctness
Location: src/execution/harvester.py:L38-L48
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/execution/harvester.py

The settlement harvester opens two independent DB handles:

trade_conn = get_trade_connection()

shared_conn = get_forecasts_connection()

Then, inside one logical settlement event, it writes:

settlement truth / observation-derived verification through shared_conn: obs_row = _lookup_settlement_obs(...) and _write_settlement_truth(shared_conn, ...) at L42-L44;

calibration/learning pairs through shared_conn: maybe_write_learning_pair(shared_conn, ...) at L46;

position settlement / outcome / redeem intent through trade_conn: _settle_positions(trade_conn, ...) and record_settlement_result(trade_conn, ...) at L46-L47;

then commits both independently inside _db_op_trade: trade_conn.commit(); shared_conn.commit() at L48.

This violates the stated INV-37 law: writes spanning zeus-world.db / zeus-forecasts.db / zeus_trades.db must use ATTACH + SAVEPOINT, never separate connections.

The architecture registry supports the blast radius: settlement_outcomes and calibration_pairs are forecast-class tables on zeus-forecasts.db at architecture/db_table_ownership.yaml:L9 and L33, while outcome_fact is trade-class on zeus_trades.db at L211. The source rationale explicitly says src/execution/harvester.py detects settlements, records settlement events, and creates calibration facts at architecture/source_rationale.yaml:L152-L153: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/db_table_ownership.yaml
 and https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/source_rationale.yaml

Impact: A crash, SQLite busy error, process kill, or exception between commits can create a logically impossible state: settlement truth/learning updated but positions not settled, or positions settled/redeem intents emitted without the matching learning/truth write. That contaminates calibration, realized PnL, future sizing, and redeem state. This is not just accounting hygiene; it can create false settlement learning and incorrect live capital truth.

Fix direction: Collapse the full settlement write into one SQLite connection using ATTACH for the other DBs and a single SAVEPOINT.

Concrete shape for Claude Code to adapt:

Python
Run
conn = sqlite3.connect(str(ZEUS_TRADES_DB_PATH), timeout=...)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout = ...")
conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
conn.execute("SAVEPOINT settlement_event")
try:
    # write forecasts.settlement_outcomes / forecasts.calibration_pairs
    # write main.position_events / main.position_current / main.settlement_commands
    # write any world decision-event annotations if needed
    conn.execute("RELEASE SAVEPOINT settlement_event")
except Exception:
    conn.execute("ROLLBACK TO SAVEPOINT settlement_event")
    conn.execute("RELEASE SAVEPOINT settlement_event")
    raise

Also add a red test that injects an exception after the forecast-class write but before the trade-class write and proves neither side persists.

2. BLOCKER — _absorb_terminal_chain_closed_phantom is not fail-closed enough for live position truth

Category: runtime position correctness; exchange reconcile; blast radius
Location: src/execution/exchange_reconcile.py:L68-L88
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/execution/exchange_reconcile.py

The new absorber is wired into the full sweep at L68: if _absorb_terminal_chain_closed_phantom(...) returns true, the code resolves open position_drift findings and continues instead of recording drift. It is also used in the refresh path via the day-end terminal evidence machinery at L88-L89.

The absorber’s closed-position evidence includes local phases {settled, admin_closed, voided} and chain states {synced, exit_pending_missing} at L9-L11. It then records a token_suppression('settled_position') and resolves drift when exchange size is zero, local closed-position size is positive, no open sell is locked, and day-end terminal evidence exists at L79-L80.

The dangerous part is the terminal evidence source. _market_calendar_terminal_evidence treats the market as terminal based on canonical registry metadata plus local target-day end; the new absorber passes buffer_hours=0. The comments assert “a market this far past its question date is settled at the venue” and “day-end is sufficient” at L82-L88. That is too strong for live position truth. Day-end is not the same as official market resolution, verified settlement, redemption, or proved external sweep.

Why this can mask real drift:

The condition “exchange size 0 + local terminal holding + day-ended market” can also be true for:

a real unexpected wallet/balance disappearance after local day-end but before venue resolution;

a manual/operator sale not recorded as an external close;

an admin/void local phase that should not be treated as a proved redeemable terminal winner;

a stale/incorrect position_current terminal phase;

a wrong token/condition bridge in executable_market_snapshots.

The code resolves the drift and suppresses the token instead of keeping the latch red. That is exactly the failure mode a reconcile loop must avoid.

Fix direction:

Make the absorber require verified settlement or redeem evidence, not merely calendar/day-end.

Minimum safe rule:

require official settlement_outcomes.authority='VERIFIED' for the exact city/date/metric/condition/bin;

require token side matches the resolved winning side or a verified redeem command/result proves the CTF tokens left the wallet;

exclude voided and admin_closed from this automatic absorber unless separately proven by a venue/admin terminal fact;

keep calendar-only logic behind the old +24h suppressor, not the zero-buffer absorber;

preserve idempotency by keeping the suppression registry, but do not resolve drift unless the verified terminal proof is present.

Add test cases for: “local day ended but no verified settlement,” “admin_closed without redeem proof,” “voided position,” “NO-side token bridge,” and “real drift should remain unresolved.”

3. BLOCKER — Per-city anchor representativeness de-bias is Law-8-dangerous unless fitter provenance is tightened

Category: Law-8 metadata correctness; calibration honesty; settlement-station attribution
Locations:
src/calibration/anchor_representativeness_debias.py:L4-L10
scripts/fit_anchor_representativeness_debias.py:L9-L13
src/data/replacement_forecast_materializer.py:L122-L128
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/calibration/anchor_representativeness_debias.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/scripts/fit_anchor_representativeness_debias.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/data/replacement_forecast_materializer.py

The runtime loader is correctly fail-soft in the narrow sense: missing/malformed artifact returns {}/None, non-high metrics return None, family walk_forward.do_no_harm must be true, and city entry must be activated at anchor_representativeness_debias.py:L7-L10. The materializer only applies the shift when the artifact exists and is loaded, and comments state it goes live when the operator places the artifact in state/ and restarts at replacement_forecast_materializer.py:L124-L128.

The problem is the fitter’s truth provenance. _gather_residuals joins:

SQL
raw_model_forecasts r
JOIN settlement_outcomes s
  ON s.city = r.city
 AND s.target_date = r.target_date
 AND s.temperature_metric = r.metric
WHERE r.metric = ?
  AND r.model = ?
  AND r.endpoint = ?
  AND s.authority = 'VERIFIED'
  AND s.settlement_value IS NOT NULL

at scripts/fit_anchor_representativeness_debias.py:L11.

That query proves city/date/metric and VERIFIED, but it does not prove the Law-8 properties that matter for a station-representativeness correction:

settlement station ID / source station equals the contract settlement station;

settlement source type is the city’s declared settlement source, not a city-center or fallback source;

rounding rule / settlement preimage matches contract semantics;

old VERIFIED rows were produced after station-migration fixes;

one city/date does not contribute many correlated lead/run rows and overweight dates with more previous_runs;

per-city offset is not contaminated by lead-time forecast error, source-cycle changes, or duplicate artifacts.

The comments say lead pooling is safe because the offset is “lead-stable” at fit_anchor_representativeness_debias.py:L10, but the SQL does not encode that proof. It pools all rows in previous_runs; if a date has many lead snapshots, that date dominates the median/SE. That can make a per-city offset look well-sampled while the effective number of independent settlement days is small.

Impact: This is the highest Law-8 risk in the PR. The materializer subtracts δ_city from the raw anchor before Bayesian fusion, propagating directly into μ*, q, q_lcb, edge, side selection, and size at replacement_forecast_materializer.py:L128. A wrong offset is not a harmless calibration tweak; it shifts the bin-selection signal.

Fix direction:

Before any artifact is usable by live code:

Fit from a settlement-grade table that carries and filters on:

city

target_date

metric

station_id

settlement_source_type

rounding_rule

settlement_unit

contract_slug/condition family where available

authority='VERIFIED'

source-provenance version/digest after the station-routing fix.

De-duplicate or explicitly weight by independent settlement day. A sane default is one residual per (city, target_date, metric) for the anchor run that corresponds to the live serving horizon, or equal-date weighting if multiple leads are intentionally pooled.

Record artifact metadata proving the exact station and source identity used for every activated city.

Require city-specific rolling OOS non-harm, not just family aggregate do_no_harm. Family-level non-harm can hide a harmful city that is still activated.

Store effective independent n_dates, not only raw row count n.

Add red tests with wrong-station settlement_outcomes rows, duplicate lead rows, a changed settlement_source_type, and a city with positive family OOS but negative city OOS.

4. BLOCKER if promoted / HIGH even as research — post-peak harvester is a London-shaped one-order thesis, not a demonstrated systematic edge

Category: 1-order-hack vs systematic edge; base-rate favorite-buying risk; settlement-graded proof
Locations:
src/strategy/post_peak_harvester.py:L0-L7, L15-L19, L23-L40
src/strategy/post_peak_backtest.py:L0-L6
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/post_peak_harvester.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/post_peak_backtest.py

Direct answer: this is not a proven systematic edge. It is a non-generalizable, London-shaped repricing-latency bet unless and until an all-city settlement replay proves otherwise.

The file’s authority basis is explicitly the London fill: “post-peak microstructure edge proven live 2026-06-13,” “London ‘22°C’ bin BUY NO filled,” and “repricing latency” at post_peak_harvester.py:L0-L1. The module doc says it does not submit orders, which is good, but the strategy premise is still built from the forbidden one-order success story at L3-L7.

The “post-peak” detector is not strong enough to mean “daily max is locked.” It requires:

local time past city.historical_peak_hour with default 15.0;

running max has not advanced for roughly one hour.

That is at post_peak_harvester.py:L15-L19. A one-hour flat/decreasing METAR tail after typical peak hour is not a physical lock. Late spikes, sea-breeze fronts, convective bursts, airport microclimate, DST/daylight, and station-specific observation latency are not modeled. The code acknowledges only a one-sided Gaussian remaining-day upside tail at L23-L28.

The “edge” gate is also misleading. The doc labels G2 “honest post-cost edge,” but the formula at post_peak_harvester.py:L34-L37 is:

Python
Run
edge_cents = ((1.0 - fee_at_ask) - no_ask) * 100.0

That omits p_obs_bin_is_max. It is a raw cheapness/favorite-NO measure, not expected value. The paranoid guard later uses paranoid_fair_no = 1 - p_paranoid, which helps, but the naming and first gate encourage exactly the base-rate “NO usually wins” mistake the operator law rejects.

The backtest is explicitly a stub/prospective grader: it grades recorded scanner opportunities after they settle at post_peak_backtest.py:L0-L2. It does not reconstruct historical live quotes, all-city opportunity availability, depth, maker/taker costs, no-fill selection bias, city stratification, or latency windows. It reports realized NO win-rate and PnL, but the operator law says win-rate is not edge; edge is settlement-graded q_lcb > price after cost on traded markets.

Impact: If this stays as non-executing research, the immediate money-path risk is lower. If any separate executor consumes these records, this is a direct violation of “a fix just to fill one order is a failure.” It can also train the organization into optimizing for London’s retail/airport mismatch instead of all-city settlement edge.

Fix direction:

Keep this behind a hard research-only import boundary with a boot guard proving no live executor imports it.

Rename edge_cents to raw_no_payout_margin_cents or change it to true expected value:
EV_NO = (1 - p_bin) - no_ask - fee - slippage.

Replace one-hour “locked max” with a city-season-time empirical hazard model fitted on settlement-station observations: P(final max enters bin | max_so_far, local time, month, station, recent trend, sun/daylight, weather regime).

Require all-city historical replay with reconstructed quotes/depth and settlement truth before any live use.

Report by city and market liquidity class. London must not dominate the proof.

HIGH
5. HIGH — Live one-sided q_lcb caps/haircuts are enabled, directly conflicting with “no new gates/caps/haircuts”

Category: gate discipline / K-cut; q_lcb honesty; config risk
Locations:
config/settings.json:L18-L31
src/engine/event_reactor_adapter.py:L469-L471, L739-L743
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/config/settings.json

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

Head config has q_lcb_settlement_coverage_gate_enabled: true at settings.json:L18-L20, and replacement_q_market_anchor_enabled: true at L24-L28. The notes explicitly say these are high-risk live q_lcb movers and that the market anchor cap is “ONE-SIDED (only lowers q_lcb -> can never create a trade)” at L25-L27.

The event reactor applies the market-anchor cap to BUY-NO candidates only, before score/gates/proof, at event_reactor_adapter.py:L469-L471. It also applies settlement-coverage q_lcb rewriting at L739-L743, again “only ever LOWERS the q_lcb.”

This may be an honest defensive response to known phantom NO edges, but it violates the operator’s law as stated: no new gates, no q-haircuts, no one-sided caps that can only kill a trade.

Impact: The code is papering over probability-model failure by adding downstream suppressors. That may reduce losses, but it does not manufacture edge and can hide the real defect in σ-shape, calibration, or bin integration. It also makes settlement grading harder: a killed trade does not reveal whether the corrected q model is right.

Fix direction:

Disable these live flags unless the operator explicitly supersedes the law.

Move the information into the probability authority itself: calibrated q / σ shape / settlement coverage model, not a post-hoc cap.

If retained as safety telemetry, make it shadow-only and record “would cap” deltas.

Add a diff gate: any config flag whose note says “only lowers,” “haircut,” “cap,” “gate,” or “can never create a trade” should fail review unless explicitly waived.

6. HIGH — Selection authority story is inconsistent: EB expected-log-growth replacement is shadow-only; BH/FDR remains the live gate

Category: admission/selection authority; K3 expected-log-growth; test adequacy
Locations:
src/strategy/selection_shrinkage.py:L0-L5
src/engine/event_reactor_adapter.py:L154-L160, L196-L201
src/events/money_path_adapters.py:L4-L5
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/selection_shrinkage.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/events/money_path_adapters.py

selection_shrinkage.py says BH/FDR on the trading path is “condemned” and replacement math is posterior expected-log-utility thresholding plus EB shrinkage at L0-L5.

But the event reactor says the EB decision replacement flag was removed and “the live selection gate is the BH/FDR pass UNCONDITIONALLY” at event_reactor_adapter.py:L154-L155. It later computes EB shrinkage with authority_on=False at L196-L197 and then applies evaluate_fdr_full_family(...); _gate_passed = fdr.passed is the live gate at L199-L201. The adapter itself calls canonical BH FDR in money_path_adapters.py:L4.

Impact: The PR’s apparent K-cut / K3 expected-log-growth narrative is not true on the inspected live path. Robust marginal expected log utility appears to rank candidates, but BH/FDR is still the binary acceptance gate. That is another decision seam, and it is a seam that the PR’s own new module says is mathematically wrong for mutually exclusive bins.

Fix direction:

Pick one:

Make K3 expected-log-growth / EB shrinkage the actual live admission authority, with settlement-graded tests; or

Stop documenting the PR as having replaced BH/FDR live authority and keep EB shrinkage purely diagnostic.

Do not merge with the code and docs telling opposite stories.

7. HIGH — “PROFITABLE-ERA GATE” zeroes BUY-NO when YES side is non-executable

Category: gate discipline; direction-law safety; no-new-gates
Location: src/engine/event_reactor_adapter.py:L728-L737
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

The canonical path correctly states native NO should be 1 - q_ucb_yes and not an independent forecast at L726-L733. But when yes_executable is false, the restored “PROFITABLE-ERA GATE” sets both directions to p=1 / prefilter false and sets BUY-NO q point to 0.0 at L734-L737.

The comment justifies this by replay evidence that the prior forecast-NO path admitted loss classes. That may be true, but under the operator law this is still an added gate: YES-side non-executability kills a NO opportunity even if the NO side has its own executable native quote and a valid probability authority.

Impact: This can suppress valid NO trades for a reason that is not the candidate’s own q/price/execution edge. It also entangles quote availability on one side with admission on the other side, which is exactly the kind of seam collapse was supposed to remove.

Fix direction:

If NO executable data is missing, fail NO for NATIVE_NO_EXECUTION_MISSING.

If probability samples are missing, fail both sides for PROBABILITY_AUTHORITY_MISSING.

Do not use YES-side non-executability as a proxy veto for native NO.

Add a test where YES quote/hypothesis is absent, NO quote exists, and q_lcb_no > price; expected behavior should be decided by the operator law, not by inherited replay fear.

8. HIGH — Harvester proof harness uses win-rate gap as edge proof, which conflicts with settlement-truth law

Category: 1-order-hack vs systematic; test adequacy; base-rate favorite risk
Location: src/strategy/post_peak_backtest.py:L0-L6
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/post_peak_backtest.py

The harness says “The win-rate gap is the edge proof” at L2. That is not sufficient in this market. A BUY-NO strategy can have a high win rate and still be negative EV if it buys favorites at the wrong price. The user’s Law 5 explicitly calls this out.

The harness does calculate PnL per share and weighted PnL later at L6, which is good, but the stated proof criterion is wrong and the harness does not replay live quote availability or opportunity selection bias.

Fix direction:

Change the proof target to:

sum(realized_pnl_after_fees) > 0,

calibrated q_lcb_no > executable_no_cost at decision time,

per-city and non-London profitability,

quote/depth availability reconstructed at the decision timestamp,

no conditioning on only opportunities that were manually noticed or would have filled.

MEDIUM
9. MEDIUM — Direction-law fail-soft to WMO half-up can be wrong for truncation/non-WMO cities

Category: direction-law safety; settlement-preimage mapping
Location: src/engine/event_reactor_adapter.py:L453-L456
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

The event reactor resolves the direction-law forecast center once, which is good. But the comments state that if city-specific rounding callable construction fails, the pure module falls back to WMO half-up default at L455-L456. The same comment acknowledges HKO truncation differs from WMO half-up at L454-L455.

For non-WMO cities, a fail-soft WMO fallback is not direction-law safe. It can ban the wrong bin or permit a wrong central-NO trade.

Fix direction:

Fail closed for any city whose settlement semantics are not WMO half-up if the city-specific rounding callable cannot be constructed. Add tests for Hong Kong/CWA-style truncation at boundary values.

10. MEDIUM — Anchor de-bias tests prove loader/sign guards, not Law-8 training correctness

Category: test adequacy; calibration honesty
Location: tests/test_anchor_representativeness_debias.py:L1-L7
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/tests/test_anchor_representativeness_debias.py

The tests create a synthetic artifact with Seoul/Ankara and assert:

activated city returns finite δ;

thin city returns None;

high metric / do-no-harm gates work;

sign moves anchor toward settlement.

That is useful, but it does not test the dangerous part: fitter SQL provenance, settlement station identity, station/source migration, rounding/preimage, duplicated leads, city-specific OOS harm, or artifact metadata.

Fix direction:

Add fitter-level tests with a temp forecasts DB containing:

correct airport settlement row and wrong city-center/fallback settlement row for the same city/date;

multiple previous_runs rows for one date;

mixed settlement_source_type;

a non-WMO rounding city;

a city with family-level OOS positive but city-level OOS negative.

The test should fail until the fitter proves station/source/rounding and date-level weighting.

11. MEDIUM — settings.example.json is stale and can re-enable caps/dead knobs

Category: config governance; operator footgun
Locations:
config/settings.example.json:L3-L5
src/config.py:L8-L12
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/config/settings.example.json

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/config.py

The example config still includes entry_forecast.allow_short_horizon_06_18 and require_active_market_future_coverage at settings.example.json:L3, and it sets sizing.max_single_position_pct: 0.1 at L5, while live settings.json has a note that max_single_position_pct=0.0 is intentional because a concentration cap would be a hard clip. src/config.py claims a strict config contract at L0-L1, but strictness is top-level for the listed sections at L8-L12; nested stale keys can survive and mislead operators.

Fix direction:

Regenerate settings.example.json from the live schema or add a schema validator that rejects unknown nested keys and flags any example value that would add a cap/gate not present in live doctrine.

12. MEDIUM — Architecture registry partially updated, but test_topology.yaml appears stale for the new de-bias test

Category: governance; registry consistency
Locations:
architecture/script_manifest.yaml:L63
architecture/test_topology.yaml no match for anchor_representativeness_debias
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/script_manifest.yaml

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/test_topology.yaml

script_manifest.yaml registers fit_anchor_representativeness_debias.py and names tests/test_anchor_representativeness_debias.py as required at L63. I searched test_topology.yaml for anchor_representativeness_debias and found no match. That suggests the governance/test topology registry is stale for a Law-8-critical artifact.

Fix direction:

Add the fitter/loader/materializer tests to architecture/test_topology.yaml with explicit ownership of:

artifact missing fallback;

artifact malformed fallback;

station/source/rounding provenance;

city-specific OOS activation;

materializer integration.

13. MEDIUM — Candidate framework changed/deleted but I could not review every src/strategy/candidates/** file

Category: coverage gap; import/runtime regression
Evidence: Partial PR /files view showed src/strategy/candidates/*.py changes, but the pinned head tree under src/strategy did not show a candidates/ directory, and direct head paths for candidate-related modules returned 404. Pinned strategy tree: https://github.com/fitz-s/zeus/tree/675aebca271cfa11355bf024707b6624bde051c3/src/strategy

src/contracts/no_trade_reason.py also says 30 shadow-candidate reason members were removed and there are zero live emitters at L4-L6: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/contracts/no_trade_reason.py

Risk: A deleted candidate package can leave stale imports in tests, scripts, registries, docs, or entry points. This is probably intended as part of gate-collapse, but I could not verify all import surfaces remotely.

Fix direction for Claude Code:

Run:

Bash
git grep -n "src.strategy.candidates\|strategy.candidates\|CandidateFamily\|shadow_candidate" -- .
python -m compileall src scripts tests
pytest -q

Also verify architecture registries no longer reference deleted candidate modules.

LOW / NITS
14. LOW — utility_ranker.py doc says default-off/shadow while event reactor says it is live wired

Category: documentation drift
Locations:
src/strategy/utility_ranker.py:L9-L10
src/engine/event_reactor_adapter.py:L17-L18
Evidence:
https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/utility_ranker.py

https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

utility_ranker.py says importing it changes no live behavior and it is not wired into the live decision path at L9-L10. event_reactor_adapter.py says _selected_candidate_proof now makes the single live decision via _select_proof_by_robust_marginal_utility at L17-L18.

Fix direction: Update the docstring. This is not a money-path bug by itself, but stale authority comments are governance debt in this codebase.

15. LOW — Anchor loader’s “byte-identical absent artifact” claim is mostly mathematically true, not literally byte-identical operationally

Category: operational semantics
Location: src/calibration/anchor_representativeness_debias.py:L7-L8
Evidence: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/calibration/anchor_representativeness_debias.py

Missing artifact returns {}/None, so the materializer’s numerical behavior should be unchanged. But _load_table() logs a warning on any load failure at L7, and the module caches the artifact. If the operator writes the artifact into state/, it will not hot-load without restart or reset_cache().

Fix direction: Clarify “byte-identical q path, not log-identical,” and document restart/cache behavior in the operational runbook.

Positive checks from inspected code

These were not findings:

The probability uncertainty contract correctly separates q_lcb from edge_lcb, and implements native NO as per-sample 1 - YES, not 1 - q_lcb_yes, at src/strategy/probability_uncertainty.py:L1-L13: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/probability_uncertainty.py

The post-peak scanner does use settlement-station METAR reports and filters by city.wu_station in _minutes_since_max_advance at post_peak_harvester.py:L21-L22; its Law-8 station choice is better than a city-grid shortcut. The problem is edge generalization, not that specific station filter.

src/data/market_scanner.py derives slug-discovery cities from configured city slug names and searches weather tags/prefixes rather than a hard-coded London-only slug path: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/data/market_scanner.py

src/riskguard/riskguard.py auxiliary database is locked handling appears scoped to auxiliary bookkeeping rather than settlement truth, and daily loss is realized/settlement-based, not mark-to-market, in the inspected lines: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/riskguard/riskguard.py

src/ingest_main.py shows at least one K1 split path using the intended get_forecasts_connection_with_world helper for cross-DB daily observations at L19-L20; that pattern is the shape the settlement harvester should follow, not the separate-connection pattern: https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/ingest_main.py

Local verification checklist for Claude Code

Run these before any merge decision.

A. Prove/repair INV-37
Bash
git grep -n "get_forecasts_connection()\|get_world_connection()\|get_trade_connection()" src/execution/harvester.py src | cat
git grep -n "commit_then_export\|\.commit()" src/execution/harvester.py src | cat

Then add a fault-injection test:

create temp forecasts + trade DBs;

force _write_settlement_truth / maybe_write_learning_pair to succeed;

raise before _settle_positions or before trade_conn.commit;

assert no forecast/trade partial write survives.

B. Reconcile absorber safety

Create tests for _absorb_terminal_chain_closed_phantom:

day-ended but no verified settlement outcome -> drift remains open;

terminal local phase voided -> no suppression unless explicit venue/admin proof;

admin_closed -> no suppression unless explicit proof;

external operator close state -> excluded from closed-position-holding view;

NO-side condition bridge -> resolves only when token side and verified outcome match;

duplicate absorber call -> idempotent suppression and no duplicate resolution side effects.

C. Anchor de-bias Law-8 proof

Run and extend:

Bash
pytest -q tests/test_anchor_representativeness_debias.py
python scripts/fit_anchor_representativeness_debias.py --db state/zeus-forecasts.db --out /tmp/anchor_representativeness_debias.candidate.json --n-min 30

Then inspect candidate artifact for:

station/source/rounding metadata per city;

effective independent settlement dates;

per-city OOS metrics;

activated cities with n_dates >= n_min;

no city activated by duplicate lead rows alone.

Also run SQL to detect duplicate date weighting:

SQL
SELECT city, target_date, metric, COUNT(*) AS n_rows
FROM raw_model_forecasts
WHERE model='ecmwf_ifs' AND endpoint='previous_runs'
GROUP BY city, target_date, metric
HAVING COUNT(*) > 1
ORDER BY n_rows DESC;
D. Post-peak harvester quarantine
Bash
git grep -n "post_peak_harvester\|scan_event_for_opportunities\|HarvestOpportunity\|post_peak_backtest" src scripts tests

Expected before merge: no live executor/import path can place orders from this scanner. If any path consumes it, treat that as a blocker.

Build a historical replay requirement before promotion:

all configured cities;

settlement-station observations only;

historical local time windows;

reconstructed quote/depth snapshots;

fee/slippage;

per-city and excluding-London profitability;

realized PnL after cost, not win-rate only.

E. Gate/cap discipline
Bash
git diff -- config/settings.json config/settings.example.json
git grep -n "only lowers\|haircut\|cap\|gate\|can never create a trade\|q_lcb.*CAPPED" src config architecture

For every hit, classify: probability-authority construction vs post-hoc suppressor. Under the operator law, post-hoc suppressors must be removed, disabled, or explicitly waived.

F. Candidate deletion/import safety
Bash
git grep -n "src.strategy.candidates\|strategy.candidates\|shadow_candidate_dispatch\|NoTradeReason\." -- .
python -m compileall src scripts tests
pytest -q
G. Config schema and examples
Bash
python - <<'PY'
import json
from pathlib import Path
live=json.loads(Path("config/settings.json").read_text())
ex=json.loads(Path("config/settings.example.json").read_text())
print("live edli keys", sorted(live.get("edli",{})))
print("example edli keys", sorted(ex.get("edli",{})))
PY

Add nested-key validation or explicit unknown-key rejection.

Files and sources actually inspected

Priority code inspected:

src/execution/exchange_reconcile.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/execution/exchange_reconcile.py

src/execution/harvester.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/execution/harvester.py

src/strategy/post_peak_harvester.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/post_peak_harvester.py

src/strategy/post_peak_backtest.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/post_peak_backtest.py

src/calibration/anchor_representativeness_debias.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/calibration/anchor_representativeness_debias.py

src/data/replacement_forecast_materializer.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/data/replacement_forecast_materializer.py

scripts/fit_anchor_representativeness_debias.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/scripts/fit_anchor_representativeness_debias.py

tests/test_anchor_representativeness_debias.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/tests/test_anchor_representativeness_debias.py

src/engine/event_reactor_adapter.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/engine/event_reactor_adapter.py

src/strategy/selection_shrinkage.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/selection_shrinkage.py

src/strategy/probability_uncertainty.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/probability_uncertainty.py

src/strategy/utility_ranker.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/strategy/utility_ranker.py

src/contracts/no_trade_reason.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/contracts/no_trade_reason.py

src/contracts/deterministic_edge.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/contracts/deterministic_edge.py

src/events/no_submit_receipts.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/events/no_submit_receipts.py

src/state/schema/edli_no_submit_receipts_schema.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/state/schema/edli_no_submit_receipts_schema.py

src/events/money_path_adapters.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/events/money_path_adapters.py

src/data/market_scanner.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/data/market_scanner.py

src/state/portfolio.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/state/portfolio.py

src/state/db_writer_lock.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/state/db_writer_lock.py

src/riskguard/riskguard.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/riskguard/riskguard.py

src/main.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/main.py

src/ingest_main.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/ingest_main.py

src/config.py — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/src/config.py

config/settings.json — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/config/settings.json

config/settings.example.json — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/config/settings.example.json

Architecture/governance inspected:

architecture/db_table_ownership.yaml — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/db_table_ownership.yaml

architecture/script_manifest.yaml — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/script_manifest.yaml

architecture/source_rationale.yaml — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/source_rationale.yaml

architecture/test_topology.yaml — https://raw.githubusercontent.com/fitz-s/zeus/675aebca271cfa11355bf024707b6624bde051c3/architecture/test_topology.yaml

architecture/_schema_fingerprint.txt was not deeply audited beyond registry context.

Remote access gaps:

I did not complete the requested full review of all 221 changed files.

I did not read most docs/evidence/**, .claude/**, AGENTS.md, and many tests.

I could not read src/strategy/candidates/** per-file at the pinned head because the directory appears absent/deleted in the pinned tree; Claude Code must verify deletion/import safety locally.

state/anchor_representativeness_debias.json is gitignored/generated and was not available in the pinned source snapshot, so I reviewed loader/fitter contract rather than the actual artifact contents.

I did not run tests or the repo locally.

Required direct answers

(a) Merge verdict: Do not merge. Blocking items: INV-37 settlement harvester atomicity, exchange-reconcile absorber proof strength, Law-8 de-bias fitter provenance, and live one-sided q_lcb/gate discipline.

(b) Single biggest correctness risk: The per-city representativeness artifact path is the biggest correctness risk because it shifts μ* before bin integration. If its station/source/rounding/date weighting is wrong, every downstream q/q_lcb/edge/size is confidently wrong under Law 8.

(c) Is the harvester systematic edge or non-generalizable one-order bet? As written, non-generalizable one-order bet. It is explicitly justified by the London fill, uses an unproven one-hour post-peak lock heuristic, and has only a prospective/stub settlement grader. It is not an all-city, settlement-graded, after-cost systematic edge.

(d) Things the prior internal review likely missed:

The settlement harvester’s cross-DB writes commit through independent connections despite INV-37.

The exchange absorber equates day-end + local terminal state with terminal settlement/redeem proof.

The de-bias fitter query does not prove settlement station/source/rounding and can overweight duplicate previous-run rows.

The live config contains explicit one-sided q_lcb caps/haircuts while the operator law says no new caps/gates/haircuts.

EB expected-log-growth selection is shadow-only; BH/FDR remains the live binary gate.

The post-peak backtest defines win-rate gap as edge proof, which is specifically rejected by the operator’s settlement-truth law.

settings.example.json can resurrect stale knobs and caps.

utility_ranker.py authority comments are stale relative to live event-reactor wiring.