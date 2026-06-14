Verdict: DO NOT MERGE at ef421f1fd0

Confidence: high for the blocking findings below. I read the pinned commit snapshot and every listed close-reading file that exists at that SHA. One listed file, src/engine/shadow_candidate_dispatch.py, does not exist at ef421f1fd0; I tried both blob and raw routes and checked the pinned src/engine tree. It is listed explicitly in the coverage section as the only unreadable item.

Line references below are to the raw/blob-rendered source locations I could inspect from GitHub. Claude Code should map them to physical local lines with nl -ba before patching because GitHub’s raw rendering collapsed some large Python files into large line blocks.

Blocking items

CRITICAL — K3 says INSUFFICIENT_DATA is license-by-default, but live admission turns it into a blocking unevaluated calibration authority.

CRITICAL — coverage_unlicensed_tail_rejection_reason is a new one-sided trade-killing gate explicitly designed to reverse the K3 fail-open behavior.

HIGH — settlement-coverage status is computed from the first candidate’s buy_yes leg and reused as a family-level credential for every selected leg/direction.

HIGH — UNLICENSED is treated as a live licensing status even when the shrink gate is disabled and q_lcb remains raw/unmodified.

CRITICAL — harvester.py violates INV-37 with independent cross-DB connections and sequential commits across trade/forecast state.

Those are enough to block merge under the operator laws.

Findings
CRITICAL — K3 INSUFFICIENT_DATA is not actually license-by-default in the live replacement path

Dimension: calibration honesty/leakage; gate-discipline/K-cut; runtime correctness/regression
Location: src/calibration/settlement_backward_coverage.py::settlement_backward_coverage_verdict, src/calibration/settlement_backward_coverage.py::arm_gate_coverage_blocks; src/engine/event_reactor_adapter.py::_replacement_family_coverage_verdict, _assert_event_bound_calibration_live_admitted; src/strategy/live_inference/live_admission.py::SETTLEMENT_COVERAGE_LICENSING_STATUSES

Evidence:

The K3 module’s own contract says insufficient evidence is inert/default-licensed: INSUFFICIENT_DATA must leave q_lcb unchanged and must not ARM-block. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py, raw-rendered L2-L8 and L18-L28. The implementation returns CoverageVerdict(status="INSUFFICIENT_DATA", q_lcb_out=q_lcb, coverage_ratio=None, ...) when sample size is thin, and arm_gate_coverage_blocks() only blocks UNLICENSED, not INSUFFICIENT_DATA. 
GitHub
+1

The live reactor path contradicts that. In event_reactor_adapter.py, the replacement-family certificate code states that INSUFFICIENT_DATA means “no realized settlement backing yet,” emits coverage_status, and maps statuses not in SETTLEMENT_COVERAGE_LICENSING_STATUSES to FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L650-L664. The live-admission assertion then rejects FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED as an unadmitted calibration authority; see the same file, raw-rendered L72-L73 in _assert_event_bound_calibration_live_admitted. 
GitHub
+1

The status set excludes INSUFFICIENT_DATA: SETTLEMENT_COVERAGE_LICENSING_STATUSES = {"LICENSED", "UNLICENSED"} in https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/live_inference/live_admission.py, raw-rendered L3-L5. 
GitHub

Impact: This is a direct violation of the K3 rebuild’s stated invariant. The new coverage model says thin settlement history should not suppress a trade; the runtime admission path suppresses it anyway. That is a hidden K-cut and can crush concentrated forecasts back into non-trading for exactly the city/bin/day cells where settlement history is sparse.

Concrete fix:

Make INSUFFICIENT_DATA a first-class non-blocking credential in the replacement-family admission path, while preserving the explicit status for observability. There should be exactly three live behaviors:

LICENSED          -> admitted, q_lcb may remain unchanged
INSUFFICIENT_DATA -> admitted-by-default, q_lcb unchanged, explicitly marked thin-history
UNLICENSED        -> admitted only if the selected leg’s q_lcb has actually been shrunk/recomputed, otherwise blocked

Do not map INSUFFICIENT_DATA to FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED. Reserve *_UNEVALUATED for structural failures such as missing receipt provenance, malformed metadata, or inability to compute coverage when coverage is required.

Add a regression test: create a thin-history K3 verdict (n < min_n) and verify replacement live admission succeeds, q_lcb is unchanged, arm_gate_coverage_blocks() is false, and the emitted certificate records INSUFFICIENT_DATA rather than FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED.

CRITICAL — New coverage_unlicensed_tail_rejection_reason is a forbidden one-sided gate/cap

Dimension: gate-discipline/K-cut; calibration honesty; operator law #3
Location: src/strategy/live_inference/live_admission.py::coverage_unlicensed_tail_rejection_reason; src/engine/event_reactor_adapter.py candidate scoring loop

Evidence:

coverage_unlicensed_tail_rejection_reason() is explicitly described as the “fail-CLOSED dual” of K3’s fail-open rule. It rejects cheap tail candidates when price < 0.05 and q_lcb > 2x price until they are settlement-licensed. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/live_inference/live_admission.py, raw-rendered L7-L12. 
GitHub

The reactor applies this in the live candidate scoring path. If the function returns a reason, the candidate is assigned score=0.0 and prefilter_passed=False. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L480-L484. 
GitHub

Impact: This is precisely the forbidden class of change: a new one-sided cap/gate that can only kill trades. It does not recompute the posterior, q, q_lcb, edge, or size from settlement evidence; it suppresses a trade after the model has produced a positive lower-bound edge. It also directly undermines the K3 rebuild’s stated “license-by-default” behavior for insufficient settlement history. In operator-law terms, this collapses opportunity set K without proving overconfidence by settlement-graded evidence.

Concrete fix:

Remove this function from the live admission/selection path. At most, keep it as shadow telemetry:

candidate.telemetry["would_fail_unlicensed_tail_shadow"] = ...

It must not set score to zero, set prefilter_passed=False, emit a live no-trade reason, or alter size.

If the underlying concern is real tail overconfidence, encode it in the posterior/q_lcb computation itself using settlement-graded walk-forward calibration, not a post-hoc market-price ratio kill switch.

Add a regression test where an INSUFFICIENT_DATA tail candidate with price < 0.05, q_lcb > 2*price, and positive after-cost edge remains eligible under the live path, with the warning emitted only as telemetry.

HIGH — K3 coverage credential is computed from first candidate’s buy_yes leg and reused for the whole family

Dimension: Law-8 metadata; calibration honesty; settlement-preimage identity
Location: src/engine/event_reactor_adapter.py::_replacement_family_coverage_verdict, _build_event_bound_live_certificate_payload

Evidence:

_replacement_family_coverage_verdict() is documented as returning a “family-representative” verdict from the buy_yes leg of the first candidate whose q_lcb is present. It hard-codes key=(condition_id, "buy_yes") and direction="buy_yes" for coverage lookup. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L653-L656. 
GitHub

That single _coverage_verdict is then placed into family-level evidence as settlement_coverage_status/settlement_coverage_ratio, rather than being tied to the selected candidate’s own bin and direction. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L705-L706. 
GitHub

Impact: Settlement coverage is not a family-wide scalar. It is a claim about a specific (city, target_date class, station/settlement source, metric, bin boundary, direction, q_lcb provenance) relationship. Reusing the first candidate’s buy_yes verdict can incorrectly license or block a selected buy_no, and can make candidate order affect whether live money is admitted. That is a Law-8 metadata violation: downstream q/q_lcb/edge/size become confidently wrong because the credential is bound to the wrong bin/direction preimage.

Concrete fix:

Carry settlement-coverage verdict per candidate leg:

Python
Run
coverage_by_leg[(condition_id, direction)] = CoverageVerdict(...)

Then bind the selected candidate’s certificate to the selected leg’s own verdict:

Python
Run
selected_key = (selected.condition_id, selected.direction)
selected_coverage = coverage_by_leg[selected_key]

Do not expose a single family-level settlement_coverage_status except as a summary; the admission decision must use the selected leg’s own status.

Add tests:

Two candidates in one family, one LICENSED, one UNLICENSED; selection of either candidate must use its own status.

A buy_no candidate must not inherit the buy_yes leg’s coverage.

Reordering candidates must not change admission or the emitted certificate.

HIGH — UNLICENSED is treated as a licensing status even when no shrink is applied

Dimension: calibration honesty; gate coupling; runtime correctness
Location: src/calibration/settlement_backward_coverage.py::apply_settlement_coverage; src/engine/event_reactor_adapter.py::_maybe_apply_settlement_coverage_to_lcb; src/strategy/live_inference/live_admission.py::SETTLEMENT_COVERAGE_LICENSING_STATUSES

Evidence:

apply_settlement_coverage() returns unchanged q_lcb when coverage shrink is disabled, even if the verdict is UNLICENSED. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/calibration/settlement_backward_coverage.py, raw-rendered L22-L24. 
GitHub

The reactor has an explicit no-op path when edli.q_lcb_settlement_coverage_gate_enabled is false. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L886-L891. 
GitHub

At the same time, UNLICENSED is in the set of “licensing” statuses for live admission: SETTLEMENT_COVERAGE_LICENSING_STATUSES = {"LICENSED", "UNLICENSED"}. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/strategy/live_inference/live_admission.py, raw-rendered L3-L5. The reactor maps statuses in that set to FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L662-L664. 
GitHub
+1

Impact: If the settlement-coverage gate is disabled and K3 proves overconfidence, the system can still certify the raw, unshrunk q_lcb as having settlement-coverage authority. That is worse than a simple fail-open: it gives the certificate a settlement-backed label while leaving the refuted lower bound unchanged.

Concrete fix:

UNLICENSED should not be a live licensing status unless the selected candidate’s q_lcb has actually been transformed by the coverage verdict.

The admission rule should be:

LICENSED          -> admit
INSUFFICIENT_DATA -> admit-by-default, explicitly thin-history
UNLICENSED + shrink_applied -> admit with shrunk q_lcb only if still positive after all costs
UNLICENSED + shrink_not_applied -> block/fail closed

Add assertions tying the certificate to arithmetic reality:

Python
Run
assert selected.q_lcb == selected_coverage.q_lcb_out
assert selected_coverage.status != "UNLICENSED" or selected_coverage.shrink_amount > 0

Add a regression test: with q_lcb_settlement_coverage_gate_enabled=False and verdict UNLICENSED, live admission must not emit FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE for the raw q_lcb.

CRITICAL — harvester.py violates INV-37 with independent cross-DB commits

Dimension: INV-37 atomicity; settlement/position truth; learning truth
Location: src/execution/harvester.py::run_harvester, _settle_positions, enqueue_redeem_command

Evidence:

run_harvester() opens separate connections for trade and forecasts/shared state:

Python
Run
trade_conn = get_trade_connection()
shared_conn = get_forecasts_connection()

See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py, raw-rendered L37-L38. 
GitHub

The same harvesting cycle writes settlement truth and learning/refit state on the forecasts/shared side, while also settling positions, recording settlement results, and enqueueing redeems on the trade side. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py, raw-rendered L42-L48. 
GitHub

The “atomic” finalization is not atomic across DBs:

Python
Run
trade_conn.commit()
shared_conn.commit()

inside commit_then_export(). See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py, raw-rendered L47-L48. 
GitHub

_settle_positions() also performs an internal conn.commit() after settlement-close work. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py, raw-rendered L103. 
GitHub

Impact: This is a direct INV-37 violation. A crash or exception after one DB advances but before the other commits can split:

forecasts DB: settlement truth / learning pair written
trade DB: position still open, settlement result missing, redeem not enqueued

or the inverse:

trade DB: position settled / redeem enqueued
forecasts DB: no settlement truth / no learning update

For a live Polymarket trading system, that corrupts settlement truth, position truth, and future learning. It also makes post-hoc reconciliation ambiguous because there is no single transaction boundary.

Concrete fix:

Refactor harvester.py so each settlement event or harvest cycle uses one owning SQLite connection with all required DBs attached:

SQL
ATTACH DATABASE 'zeus-forecasts.db' AS forecasts;
ATTACH DATABASE 'zeus_trades.db' AS trades;
ATTACH DATABASE 'zeus-world.db' AS world; -- if anchor/source metadata is read
SAVEPOINT harvest_event;
...
RELEASE harvest_event;

Remove all nested commits such as _settle_positions(...).commit(). Settlement truth, learning pairs, position settlement, settlement-result rows, redeem queue rows, and required world metadata reads should occur inside the same attached-DB transaction/SAVEPOINT.

Exports to JSON or other side effects should happen after RELEASE, not inside the money-path transaction.

Add crash-injection tests at minimum:

after settlement truth write but before position settlement,

after learning-pair write but before trade settlement,

after position settlement but before redeem enqueue,

between the old trade_conn.commit() and shared_conn.commit() boundary.

Each injected failure should leave either all relevant DBs unchanged or all advanced together.

HIGH — Redeem anchor/source provenance lookup silently degrades outside the harvest transaction

Dimension: Law-8 metadata; settlement/position truth; INV-37-adjacent transaction discipline
Location: src/execution/harvester.py::enqueue_redeem_command

Evidence:

enqueue_redeem_command() performs a decision-event anchor lookup by opening a separate world connection:

Python
Run
_world_conn = get_world_connection(write_class=None)
...
except sqlite3.OperationalError:
    _anchor_source = ""

It then passes _anchor_source into request_redeem(...). See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py, raw-rendered L17-L20 and L16-L20 in the enqueue_redeem_command block. 
GitHub
+1

The inline comment says the lookup is intended to come through a T4 world ATTACH, but the implementation uses a separate connection and silently falls back to empty source on operational failure.

Impact: The Polymarket end-anchor source is part of the settlement-preimage/provenance map. Silently degrading to unknown_legacy or empty anchor provenance for live EDLI conditions weakens the evidence chain from contract semantics to redeem command. It is not the same severity as the direct cross-DB write violation above, but it is the same design smell: live settlement action is built from multiple DB snapshots without a single transactional view.

Concrete fix:

Move this lookup into the same attached-DB harvest transaction described above. Read world.decision_events from the attached world schema using the same condition_id snapshot used to settle the trade row. Do not silently degrade anchor source for non-legacy live EDLI rows; fail the redeem enqueue or mark the settlement result as needing operator repair if the source metadata cannot be read.

Add a test where the world metadata table is unavailable/missing for a non-legacy live condition. The harvester should not enqueue a redeem command with blank/unknown anchor provenance.

HIGH — K3 receipt matching is display-label based, not canonical boundary/preimage based

Dimension: Law-8 metadata; calibration honesty; settlement-preimage identity
Location: src/engine/event_reactor_adapter.py::_coverage_band_template, _per_day_claimed_qlcb_by_date, _settlement_coverage_observations; src/state/schema/edli_no_submit_receipts_schema.py

Evidence:

The K3 path derives a band template by stripping a trailing date phrase from bin_label:

Python
Run
return re.sub(r"\s+on\s+[A-Za-z]+\s+\d{1,2}\s*$", "", label)

Then it scans edli_no_submit_receipts receipt JSON and matches by city, metric, target_date, and the stripped bin_label template. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L874-L878 and L883-L886. 
GitHub

The edli_no_submit_receipts schema exposes receipt JSON and q_lcb columns but does not provide a typed canonical settlement-boundary identity such as station, rounding rule, unit, bin low/high, inclusivity, and settlement-source preimage. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/state/schema/edli_no_submit_receipts_schema.py. 
GitHub
+1

Impact: A display string is not a contract. Any label rendering difference can change K3 history matching:

"81-82°F on June 14"
"81–82°F on Jun 14"
"81 to 82°F"
"Above 90°F"
"90° or higher"

The consequence is severe in both directions:

true historical claims can disappear, producing INSUFFICIENT_DATA and default licensing;

different bins can be pooled if their labels normalize the same way;

station/rounding/source changes can be ignored even though settlement identity changed.

This is a Law-8 precondition failure: the calibration record must be keyed by the contract-settlement preimage, not UI display text.

Concrete fix:

Extend edli_no_submit_receipts with typed, canonical metadata:

city_id / city_name
icao_station
settlement_source
rounding_rule
metric
target_date
condition_id
token_id
direction
unit
bin_kind
bin_low
bin_high
bin_low_inclusive
bin_high_inclusive
contract_boundary_hash
posterior_id / forecast_bundle_id

Then K3 matching should use canonical (city/station, metric, season, bin boundary, direction) identity, not bin_label.

Add a migration/backfill step that parses legacy receipts only once into canonical columns and rejects ambiguous labels. Add tests proving that label formatting changes do not affect coverage matching, while a true boundary/station/rounding change prevents pooling.

MEDIUM — Market-anchor cap is dormant by flag, but it is another forbidden one-sided q_lcb cap

Dimension: gate-discipline/K-cut; direction-law-adjacent runtime behavior
Location: src/engine/event_reactor_adapter.py::_replacement_q_market_anchor_enabled, _apply_market_anchor_cap_to_no_lcb, replacement candidate scoring loop

Evidence:

The function is explicitly described as a cap that “only ever LOWERS the lower bound” for a buy_no candidate. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L664-L667. 
GitHub

The scoring loop applies this cap before edge scoring when the flag is enabled. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py, raw-rendered L471-L473. 
GitHub

Impact: Default-off makes this less urgent than the blockers above, but the code path is live and explicitly one-sided. It can only reduce q_lcb and kill a trade; it cannot create a trade, recompute posterior probability, or improve settlement calibration. That conflicts with the “no new gates/caps/haircuts” operator law.

Fix direction:

Delete it or convert it to shadow-only telemetry. If market anchoring is desired, it should enter the model as an explicit probabilistic feature or Bayesian update that is settlement-graded, not as a post-hoc cap.

MEDIUM — Bayes precision fusion downloader reports the wrong candidate row count after chunked writes

Dimension: runtime correctness/regression; observability
Location: src/data/bayes_precision_fusion_download.py::download_bayes_precision_fusion_extra_raw_inputs

Evidence:

The downloader persists rows in chunks, clears the local rows buffer, and returns candidate_row_count: len(rows). In a successful chunked run, len(rows) can be zero even though rows were written. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/bayes_precision_fusion_download.py, around the chunk write/clear and return path. 
GitHub

Impact: This is not a direct trading-path correctness bug, but it can mislead operators and tests into thinking no candidate raw inputs were downloaded. That can hide materializer/backfill failures and make coverage diagnostics look empty.

Fix direction:

Track a monotonic total:

Python
Run
candidate_row_count_total = 0
...
_write_chunk(rows)
candidate_row_count_total += len(rows)
rows.clear()
...
return {"candidate_row_count": candidate_row_count_total, ...}

Add a test with enough candidates to force at least one chunk flush and assert the returned count equals the number persisted.

MEDIUM — Portfolio exit audit writes through a fresh connection and commits independently of caller transaction

Dimension: settlement/position truth; runtime correctness/regression
Location: src/state/portfolio.py::_track_exit

Evidence:

_track_exit() opens a fresh trade/world connection, writes the exit audit row, commits, and closes the connection. See https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/state/portfolio.py, raw-rendered L140-L143. 
GitHub

Impact: If close_position()/compute_settlement_close() is called from a larger settlement or risk-exit transaction, this audit write can become durable before the parent money-path update commits. If the parent then rolls back or fails, audit state can claim an exit that did not atomically pair with the canonical position update. I did not verify locally whether log_trade_exit() is purely audit or participates in operator-facing position truth, so Claude Code should inspect the call graph before assigning final severity.

Fix direction:

Pass the caller’s transaction/connection into _track_exit() or split it into:

pure exit projection
durable audit write under caller transaction

No fresh connection commit should happen inside a parent settlement/exit flow.

Per-file review notes
src/contracts/deterministic_edge.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/contracts/deterministic_edge.py
Finding: no blocking issue found. The file defines typed decision/leg containers and did not introduce direction-law or metadata mutation by itself.

src/contracts/no_trade_reason.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/contracts/no_trade_reason.py
Finding: no blocking issue found in the enum/contract layer.

src/data/bayes_precision_fusion_download.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/bayes_precision_fusion_download.py
Finding: MEDIUM observability/counting regression above.

src/data/day0_oracle_anomaly.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/day0_oracle_anomaly.py
Finding: no blocking issue found. The file is fail-closed around missing/invalid source inputs and does not appear to mutate live edge or settlement truth directly.

src/data/market_scanner.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/market_scanner.py
Finding: no blocking issue found in this pass. I did not identify a Tier-0 direction-law inversion or INV-37 write issue in the scanner itself.

src/data/replacement_forecast_bundle_reader.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/replacement_forecast_bundle_reader.py
Finding: no blocking issue found.

src/data/replacement_forecast_materializer.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/replacement_forecast_materializer.py
Finding: no blocking issue found in this batch. The major replacement-path problems I found are in the reactor/admission/certification side, not this materializer file.

src/data/replacement_forecast_production.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/data/replacement_forecast_production.py
Finding: no blocking issue found.

src/engine/cycle_runtime.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/cycle_runtime.py
Finding: no blocking issue found.

src/engine/evaluator.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/evaluator.py
Finding: no blocking issue found. Nothing in the reviewed evaluator path superseded the direction law or rewrote settlement/position truth.

src/engine/event_reactor_adapter.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/event_reactor_adapter.py
Findings: multiple blockers above:

CRITICAL: INSUFFICIENT_DATA mapped to unevaluated/blocking live authority.

HIGH: family-representative first-candidate buy_yes coverage credential.

HIGH: UNLICENSED can certify raw q_lcb when shrink disabled.

HIGH: K3 matching uses display bin_label template rather than canonical boundary identity.

MEDIUM: dormant market-anchor cap is a one-sided q_lcb haircut.

I did not find a direct buy-yes/buy-no inversion in the reviewed candidate scoring loop; the direction-law guard is present before later tail/cap gates. The main issue is that later gates and credentials can still suppress or mis-certify otherwise directionally valid candidates.

src/engine/monitor_refresh.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/monitor_refresh.py
Finding: no blocking issue found.

src/engine/replacement_forecast_reactor_hook.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/replacement_forecast_reactor_hook.py
Finding: no blocking issue found in the hook wrapper. The issues are downstream in event_reactor_adapter.py and live admission.

src/engine/shadow_candidate_dispatch.py

Status: could not read because the file does not exist at the pinned SHA
Provided blob URL: https://github.com/fitz-s/zeus/blob/ef421f1fd0/src/engine/shadow_candidate_dispatch.py
Raw URL attempted: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/engine/shadow_candidate_dispatch.py
Result: both blob/raw paths returned 404; the pinned src/engine tree at https://github.com/fitz-s/zeus/tree/ef421f1fd0/src/engine lists files such as dispatch.py, event_reactor_adapter.py, monitor_refresh.py, and replacement_forecast_reactor_hook.py, but not shadow_candidate_dispatch.py. 
Invalid URL
+2
+2

Claude Code should confirm with:

Bash
git ls-tree -r ef421f1fd0 -- src/engine | grep shadow_candidate_dispatch
src/execution/exchange_reconcile.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/exchange_reconcile.py
Finding: no blocking issue found in this pass. I did not find the same obvious cross-DB independent-commit pattern as in harvester.py.

src/execution/harvester.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/execution/harvester.py
Findings: CRITICAL INV-37 violation and HIGH anchor-provenance issue above.

src/ingest_main.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/ingest_main.py
Finding: no blocking issue found in this batch.

src/main.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/main.py
Finding: no blocking issue found. Claude Code should still include it in local transaction-grep verification because main-loop helpers can call shared trade/world connection functions.

src/riskguard/riskguard.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/riskguard/riskguard.py
Finding: no blocking issue found. I did not find a new riskguard-side cap/gate comparable to the replacement tail rejection.

src/state/db_writer_lock.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/state/db_writer_lock.py
Finding: no blocking issue found. The lock module supports writer serialization, but it does not by itself satisfy INV-37; the harvester still needs attached-DB transactionality.

src/state/portfolio.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/state/portfolio.py
Finding: MEDIUM independent audit commit risk above.

src/state/schema/edli_no_submit_receipts_schema.py

Status: read
Source: https://raw.githubusercontent.com/fitz-s/zeus/ef421f1fd0/src/state/schema/edli_no_submit_receipts_schema.py
Finding: supports the HIGH Law-8/K3 metadata finding above: receipts do not expose canonical typed settlement-boundary identity sufficient for K3 matching.

Local verification checklist for Claude Code

Run these locally before patching to map exact physical lines and verify the call graph:

Bash
git rev-parse HEAD
git show --stat --oneline ef421f1fd0

nl -ba src/engine/event_reactor_adapter.py | sed -n '640,720p;860,895p;460,490p'
nl -ba src/strategy/live_inference/live_admission.py | sed -n '1,80p'
nl -ba src/calibration/settlement_backward_coverage.py | sed -n '1,220p'
nl -ba src/execution/harvester.py | sed -n '1,260p'
nl -ba src/state/portfolio.py | sed -n '120,155p'
nl -ba src/state/schema/edli_no_submit_receipts_schema.py | sed -n '1,220p'

Targeted grep:

Bash
rg -n "INSUFFICIENT_DATA|SETTLEMENT_COVERAGE_LICENSING_STATUSES|FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED|coverage_unlicensed_tail_rejection_reason|_replacement_family_coverage_verdict|_maybe_apply_settlement_coverage_to_lcb" src

rg -n "get_trade_connection|get_forecasts_connection|get_world_connection|commit\\(|SAVEPOINT|ATTACH" src/execution/harvester.py src/execution/exchange_reconcile.py src/main.py src/state/portfolio.py

rg -n "_track_exit|log_trade_exit|compute_settlement_close|close_position" src

Required regression tests:

K3 thin-history admission: force settlement_backward_coverage_verdict(...).status == "INSUFFICIENT_DATA" and verify live replacement admission succeeds with q_lcb unchanged and no ARM/live authority block.

K3 proven-overconfidence: force UNLICENSED with shrink enabled and verify only the shrunk q_lcb is used; with shrink disabled, verify live admission blocks rather than certifies raw q_lcb.

Candidate-order invariance: create two replacement candidates with different bins/directions and different coverage statuses. Swap order and assert selected candidate admission/certificate does not change.

Direction-specific coverage: selected buy_no must use buy_no coverage history, never first-candidate buy_yes history.

Tail rejection removed from live path: a cheap positive-edge tail candidate under INSUFFICIENT_DATA must remain eligible; any tail warning must be telemetry-only.

K3 metadata identity: label formatting changes must not change coverage matching; boundary/station/rounding changes must prevent pooling.

Harvester crash atomicity: inject exceptions after each settlement-truth/learning/position/redeem write site and verify no partial cross-DB advancement.

Portfolio audit transactionality: verify whether log_trade_exit() is audit-only or part of position truth. If it is operator-facing/canonical, require caller-transaction wiring before merge.

Missing file confirmation: verify src/engine/shadow_candidate_dispatch.py is absent at ef421f1fd0 or correct the review file list.

Single biggest correctness risk

The largest risk is K3/admission split-brain around settlement coverage: the calibration module says INSUFFICIENT_DATA is inert/default-licensed, but live admission blocks it; simultaneously, UNLICENSED can be treated as a licensing status even when q_lcb was not actually shrunk. Combined with the first-candidate buy_yes family credential, the reactor can both suppress valid concentrated forecasts and certify refuted/raw lower bounds using the wrong bin/direction metadata. That sits exactly on the money path: metadata → q_lcb → edge → execution.

What the prior partial review likely missed

The K3 bug is not only in settlement_backward_coverage.py; the live breakage is in the coupling between event_reactor_adapter.py and live_admission.py.

The new coverage_unlicensed_tail_rejection_reason() is explicitly engineered as a fail-closed countermeasure to K3’s fail-open behavior, making it a forbidden K-cut even if the calibration module itself looks clean.

The family-level coverage credential is bound to first candidate buy_yes, not selected candidate/direction.

UNLICENSED is included in “licensing” statuses even when shrink is disabled.

The harvester still violates INV-37 despite apparent writer-lock/commit hygiene.

The listed shadow_candidate_dispatch.py file is absent at the pinned SHA, so any prior “partial” gap there was likely a source-list/path problem rather than an aggregate-diff problem.

The receipt schema lacks canonical Law-8 settlement-boundary metadata, so K3 is forced into display-string matching.

Access gaps

Only one file from the requested close-reading list could not be read:

src/engine/shadow_candidate_dispatch.py

Reason: it is not present at commit ef421f1fd0. Blob and raw URLs returned 404, and the pinned engine tree does not list it.

No other access gaps.