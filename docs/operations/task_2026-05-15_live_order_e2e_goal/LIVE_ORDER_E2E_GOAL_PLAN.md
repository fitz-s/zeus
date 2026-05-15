# Live Order E2E Goal Plan

Created: 2026-05-15
Last reused or audited: 2026-05-15
Authority basis: root `AGENTS.md` money path; `docs/operations/AGENTS.md` packet routing; topology admission for this packet; live read-only probes on 2026-05-15; user `/goal` directive requiring real live order placement and designed record-chain proof.

Branch: `feat/live-order-e2e-goal-2026-05-15`
Base: `origin/main` at `8b3c3c2c59 merge: data daemon live verified`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-order-e2e-goal-2026-05-15`
Critic status: prior approval is superseded by the 2026-05-15 real-live geoblock evidence below; this revision requires a new critic pass before further live-state mutation.

## Goal

Complete the live money path with real live evidence:

`forecast-live data production -> forecast authority DB -> live reader -> live evaluator -> FinalExecutionIntent -> executor -> Polymarket limit order side effect -> command/order/fill/position/reconciliation records -> guard until the designed next lifecycle state is proven`

The task is not complete when data reaches the live reader. It is not complete when a command row is created. It is not complete when an order is rejected or moved to `REVIEW_REQUIRED`. Completion requires a normal live daemon cycle to place an expected live limit order through the executor path and prove that the resulting venue outcome is recorded through the canonical record chain.

## Current Empirical State

This plan starts from current read-only facts, not from the earlier branch narrative.

- New branch/worktree was created from `origin/main` at `8b3c3c2c59`.
- The main-based branch currently contains the first four repair commits:
  - `a85d5be234 docs(operations): freeze live order e2e goal packet`;
  - `64963a43a7 fix(operations): add live order e2e proof checker`;
  - `376823e3f2 fix(execution): reject pre-sdk collateral reservation failures`;
  - `d83ff6b57e fix(execution): add proof-backed review clearance`.
- The active live root is `/Users/leofitz/.openclaw/workspace-venus/zeus`, branch `deploy/live-order-e2e-verification-2026-05-15`, currently deployed at `babba070d20514893bd95dfb114772fa7c656639`. That branch contains live validation cherry-picks and must not become the review branch by accident.
- The previous `REVIEW_REQUIRED` blocker `cb763c300b664a4f` was cleared with the proof-backed pre-SDK no-side-effect path. That only removed an old blocker; it did not prove live order success.
- Live data-pipeline verifier currently passes on the live root:
  - exactly one `src.main` process;
  - exactly one `src.ingest.forecast_live_daemon` OpenData owner;
  - no legacy `src.ingest_main` OpenData owner;
  - latest source run `ecmwf_open_data:mn2t6_low:2026-05-15T00Z`;
  - forecast target range starts at the source-cycle date, not before it;
  - live reader returns `LIVE_ELIGIBLE / EXECUTABLE_FORECAST_READY`;
  - measured reader latency `1.444 ms`, total verifier elapsed `526.251 ms`.
- A real live daemon `opening_hunt` cycle reached the executor submit boundary:
  - cycle started `2026-05-15T17:51:52.212191+00:00`;
  - decision `44323e0a-fec`, city `Karachi`, target date `2026-05-17`;
  - command `8d82ea02c5b74905`, market `2266214`, size `10.06`, price `0.32`, snapshot `ems2-5dcb2f35a78a987713be438273aefdd100eae5ed`;
  - event chain `INTENT_CREATED -> SUBMIT_REQUESTED -> SUBMIT_TIMEOUT_UNKNOWN`;
  - `SUBMIT_REQUESTED` proves all entry components allowed at command time, including collateral, heartbeat, WS gap, decision source integrity, and executable snapshot gate;
  - `SUBMIT_TIMEOUT_UNKNOWN` payload records `PolyApiException[status_code=403]` with venue body `Trading restricted in your region`.
- Current global-VPN egress is not blocked by Polymarket:
  - read-only `https://polymarket.com/api/geoblock` now returns `blocked=false`, `country=CA`, `region=QC`;
  - `https://ipinfo.io/json` reports Montreal/Quebec egress through `AS147049 PacketHub S.A.`;
  - `scutil --nwi` shows active VPN interface `utun9`;
  - live launchd still carries legacy `HTTP_PROXY` and `HTTPS_PROXY` values pointing to `http://localhost:7890`, but current operator truth is global VPN, not per-process localhost proxy routing.
- Therefore the failed live attempt is historical route evidence from the moment command `8d82ea02c5b74905` was submitted, not proof that current route is blocked. The next retry must prove the live daemon process sees `blocked=false` through the global VPN route before entry submit.
- Live order verifier correctly fails on command `8d82ea02c5b74905`:
  - state `SUBMIT_UNKNOWN_SIDE_EFFECT`;
  - no `venue_order_id`;
  - no accepted event;
  - no final venue order fact;
  - no position rows.
- `unknown_side_effect_count=1` for market `2266214`, so governor correctly blocks new entries until the unknown is terminalized or reconciled.
- Command recovery currently cannot resolve this production unknown through its advertised M2 path:
  - `_lookup_unknown_side_effect_order()` requires `client.find_order_by_idempotency_key()` when no `venue_order_id` exists;
  - `PolymarketClient` does not implement that method;
  - `PolymarketV2Adapter.get_open_orders()` checks SDK method `get_orders`, but installed `py_clob_client_v2.ClobClient` exposes `get_open_orders`, not `get_orders`;
  - the local idempotency key is not included in `VenueSubmissionEnvelope` or the SDK `OrderArgs`, so any venue-side idempotency lookup must first be proven available before it is used as authority.
- The live cycle block registry simultaneously reported evaluator gate 11 blocking with `PRODUCER_READINESS_EXPIRED` and `SOURCE_RUN_HORIZON_OUT_OF_RANGE` while the same cycle still created a real command. This divergence must be reconciled before final approval, because operator block telemetry and executor behavior cannot disagree on a live-submit path.

Current conclusion: the data side is fast enough and live-eligible, and the live daemon did reach a real venue submit attempt. The remaining blockers are now process-visible global VPN/geoblock proof, post-SDK 4xx rejection grammar, production command-recovery capability drift, and rollout-gate telemetry/action divergence. The next root cause to eliminate is not "wait for another cycle"; it is the missing distinction between a deterministic venue rejection, an actually unknown side effect, and a route-dependent trading egress precondition.

## Completion Definition

All of these must be true in one timestamped evidence bundle before declaring the goal complete:

1. Deployed-code proof: live daemon, forecast-live daemon, and riskguard are running from the intended committed branch, with PID/start time after deployment.
2. Data proof: forecast-live is the single forecast owner; latest source_run, coverage, readiness, and executable reader are aligned on the same forecast DB authority chain; warm reader p95 over 30 probes is <= 5 ms and full live data verifier is <= 3 seconds.
3. Entry proof: `execution_capability.entry` allows submit or is blocked by a typed reason that has been repaired before submit; `unknown_side_effect_count=0`; risk is GREEN; heartbeat and WS gap guard allow submit.
4. Decision proof: a live daemon cycle produces the candidate and `FinalExecutionIntent` through the normal evaluator path, preserving source_run, snapshot, cost-basis, token, side, limit price, size, strategy, and idempotency identity.
5. Submit proof: `venue_commands` row and `SUBMIT_REQUESTED` event are durably persisted before SDK submit; no direct SDK/manual DB/test-double path is used.
6. Accepted-order proof: the venue returns an accepted/resting/open order identity, or stronger fill evidence. A rejection, unknown outcome, missing order id, or `REVIEW_REQUIRED` state is blocker evidence only.
7. Record-chain proof: command events, pre/final submission envelopes, venue order facts, venue trade facts when applicable, position events/projection when applicable, and status/reconciliation agree with the venue outcome.
8. Guard proof: after submit, the system is watched until the order reaches the designed next lifecycle state:
   - resting/open order with no invented position; or
   - partial/full fill with position records; or
   - cancel/expiry/reject terminal record; and
   - any later settlement obligation is captured as a continuing guard item, not silently treated as done.

The deployed-code proof is a hard gate, not a restart assumption. It must prove:

- launchd label program, arguments, and working directory after restart;
- process PID, start time, command line, and cwd;
- process cwd `git rev-parse HEAD` equals the intended commit;
- process cwd has no source-tree dirty diff for files loaded by that daemon;
- daemon heartbeat/status/log emits or is joined to the same git SHA;
- the live root checkout or launchd working directory has been intentionally moved to the main-based branch commit, not left on the older deploy branch.

## Design Failures To Remove

### D1 - Main/live branch split hides what is actually deployed.

The live root contains fixes that are not on the main-based branch. The previous branch is too broad to merge wholesale. The repair must port only proven, necessary slices from the previous branch and verify each slice independently.

### D2 - Pre-SDK failures can masquerade as unknown side effects.

The current command has no venue order id, no final envelope, and no order facts, yet recovery moved it to `REVIEW_REQUIRED`. Portfolio governor then correctly blocked new entries because unresolved review states count as side-effect risk. This is a permanent live blocker unless the grammar has a proof-backed resolution path.

### D3 - Command review handoff lacks a terminal no-side-effect clearance.

`REVIEW_REQUIRED` is counted by governor as unresolved side-effect risk. The state grammar has no narrow, proof-backed path to resolve a reviewed command as no-side-effect after evidence proves SDK submit did not happen or venue has no order.

### D4 - Live order proof tooling is not on main.

`scripts/check_live_order_e2e.py` exists in the previous live-order branch, not in `origin/main`. The main-based branch needs the checker and its tests before it can prove anything.

### D5 - Existing blockers were fixed in symptom sequence.

Prior work found DDD runtime artifact drift, pUSD allowance authority drift, uint256-to-SQLite overflow, stale collateral snapshot, uncommitted collateral refreshes, and Q1 egress evidence drift. The new branch must treat those as structural slices with tests, not as a large undocumented patch pile.

### D6 - Trading egress proof is not a first-class live-entry gate.

`proxy_health` was written to keep data-only daemons alive when a local proxy is down. That is not the live trading authority surface. Operator truth is global VPN routing, and the route must be proven by a process-visible geoblock probe immediately before live entry submit. Legacy `localhost:7890` proxy environment values must not be treated as the source of truth, and dead local proxy state must not distract from whether the global VPN route is actually nonblocked.

### D7 - Production recovery capability drift makes unknowns sticky.

The recovery design assumes either a venue order id or an idempotency-key lookup. The production client currently has neither for command `8d82ea02c5b74905`: no venue id was returned, the local idempotency key is not sent to the venue, `PolymarketClient` lacks `find_order_by_idempotency_key`, and the adapter checks an SDK method name that the installed SDK does not expose. This makes `SUBMIT_UNKNOWN_SIDE_EFFECT` a permanent live blocker.

### D8 - Venue 4xx semantics are not represented in the command grammar.

The executor correctly treats timeouts/network failures after `place_limit_order` as unknown. It currently overgeneralizes that rule to deterministic venue 4xx errors. A synchronous CLOB HTTP 403 geoblock rejection with no order id, no final envelope, no order fact, and no trade fact should be classified by an explicit proof rule; it must not share the same state as a timeout where the side effect is genuinely unknown.

### D9 - Rollout-gate telemetry/action divergence weakens live trust.

The live cycle created command `8d82ea02c5b74905` while the same cycle's block registry reported evaluator gate 11 blocking with `PRODUCER_READINESS_EXPIRED` and `SOURCE_RUN_HORIZON_OUT_OF_RANGE`. The final live proof must show that rollout gate state, executable reader readiness, and evaluator submit authority agree. If the block registry is stale/informational, it must say so; if it is authoritative, the executor must not submit.

## ADR

Decision: continue from a clean main-based branch and port or reimplement only the slices required to satisfy the live completion definition.

Drivers:

- The previous branch is empirically useful but too wide for reliable review.
- The live root proves some repairs are necessary, but deployed state is not equal to main.
- The current blocker is command side-effect resolution, not forecast ingest.
- The user requires real live execution and full record-chain proof.

Alternatives considered:

- Merge the previous 29-commit branch wholesale: rejected because it carries a large diff, governance noise, and review-trigger risk.
- Continue only on the live deploy branch: rejected because the user asked for a new branch from main and because final work must be reviewable from main.
- Wait for another live cycle: rejected because governor is currently blocking entries due unresolved side-effect state.

Consequences:

- The new branch will be smaller, but it must explicitly re-port required proven fixes.
- Live deployment cannot happen until the new branch has tests, critic approval, and a clean deploy path.
- Resolving live unknown commands requires a proof-backed state transition or recovery tool, not manual DB editing.
- The system must separate data-mode proxy bypass from trading-mode global VPN egress authority.

## Phase 0 - Packet, Critic, And Branch Hygiene

Tasks:

- Keep work in `/Users/leofitz/.openclaw/workspace-venus/zeus-live-order-e2e-goal-2026-05-15`.
- Register this packet in `docs/operations/AGENTS.md`.
- Run critic review on this plan before source edits.
- Do not edit the live root while planning.
- Before any source/script/test implementation edit, freeze this packet through
  `docs/operations/current_state.md` as the active execution packet and run
  planning-lock with this plan as evidence.

Acceptance:

- `git status --short --branch` is clean except this packet commit.
- Topology admits the packet plan and registry edit.
- Critic verdict is `APPROVE`; `REVISE` is unresolved.
- `docs/operations/current_state.md` names this packet as the active execution
  packet before Phase 1 source/script/test edits begin.
- The current_state freeze commit is separate from implementation commits.

## Phase 1 - Recreate The Main-Based Baseline

Tasks:

- Run the existing data verifier from the new branch against live paths.
- Port `scripts/check_live_order_e2e.py` and `tests/test_check_live_order_e2e.py` from the previous branch, or reimplement only the needed checker behavior if conflict review shows the previous checker is contaminated.
- Verify the checker fails on the current live command with `LIVE_ORDER_REJECTED_OR_UNKNOWN_RECORDED`, not with a false pass.

Required evidence:

```bash
python3 scripts/check_data_pipeline_live_e2e.py --json --live
python3 scripts/check_live_order_e2e.py --json
pytest -q tests/test_check_live_order_e2e.py
```

Acceptance:

- Data verifier still passes on live paths with measured seconds.
- Live order checker is read-only and fails closed on current `REVIEW_REQUIRED`.
- The checker requires venue order identity and live/open order facts; it cannot accept pre-submit envelopes alone.
- The checker has positive relationship tests for:
  - accepted/resting order with matching live order fact and no invented position;
  - filled order with venue trade fact, position event, and position projection;
  - rejected/unknown/review command classified as blocker evidence, not completion;
  - inconsistent order identity across command, events, envelopes, and order facts rejected.

## Phase 2 - Port Only Proven Live-Blocker Slices

Candidate slices from the previous branch:

- DDD runtime config moved out of deleted operations packet artifacts.
- Forecast schema verification from split DB.
- p_raw persistence to forecast snapshot authority if still required by evaluator.
- Post-only passive final intents if current live intent needs passive limit submission.
- CLOB V2 account-authority repairs:
  - explicit signature type;
  - chain allowance fallback for missing/zero CLOB allowance;
  - conservative minimum across V2 spenders.
- Collateral ledger repairs:
  - uint256 allowance clamped to SQLite domain;
  - heartbeat refresh keeps snapshot fresh;
  - owned persistent ledger commits refreshes.
- Q1 egress evidence moved to a current live-control evidence path with content validation.

Porting rule:

- Each slice gets topology admission, focused tests, and diff review before landing.
- Do not port broad governance edits, stale packet edits, or unrelated script changes just because they are in the old branch.
- If the slice is already present in `origin/main`, record it as `already_current` and do not touch it.

Acceptance:

- Every ported slice has a failing or would-have-failed relationship test.
- Focused tests pass.
- Diff size remains reviewable; broad drift is rejected.

## Phase 3 - Make Pre-SDK Reservation Failures Terminal

Purpose: prevent another `SUBMITTING -> REVIEW_REQUIRED` command when no venue side effect occurred.

Hypothesis from live evidence:

- The executor passed the initial collateral component and persisted `SUBMIT_REQUESTED`.
- A later pre-SDK reservation or collateral boundary failed before final SDK submit.
- The command remained `SUBMITTING` until command recovery converted it to `REVIEW_REQUIRED`.

Implementation requirements:

- Identify the exact boundary in `_live_order` and exit-order analog where a command has been persisted but SDK submit has not yet been invoked.
- Wrap collateral reservation and any other pre-SDK post-command checks.
- On failure before SDK contact, append a terminal `SUBMIT_REJECTED`/`REJECTED` event with payload proving:
  - reason code;
  - no SDK call attempted;
  - no order id;
  - command id;
  - snapshot/idempotency correlation.
- Preserve fail-closed behavior if terminal append itself fails: do not retry submit.

Relationship tests:

- Preflight passes, command persists, reservation fails before SDK construction, command becomes terminal rejected, SDK is not constructed/called, governor does not count it as unknown side effect.
- Existing tests still prove insufficient collateral before command persistence creates no command row.
- If SDK construction or submit has already started, the unknown/review path remains intact.

Acceptance:

- No future pre-SDK failure can leave `SUBMITTING` without terminal event.
- The system distinguishes "no side effect possible" from "side effect unknown".

## Phase 4 - Add A Proof-Backed REVIEW_REQUIRED Clearance Path

Purpose: clear the existing live blocker safely, without pretending an order was placed.

Current command `cb763c300b664a4f` is unresolved and blocks entry. A safe clearance path must prove the absence of side effect before it changes state.

Implementation requirements:

- Add a narrow command-review resolution path, not a generic manual state editor.
- Allowed only for `REVIEW_REQUIRED` commands whose review reason and evidence match a safe no-side-effect class and whose no-side-effect proof is positive, not inferred from silence.
- Required proof inputs:
  - no `venue_order_id`;
  - no accepted/unknown final submission envelope;
  - no raw response;
  - no signed order blob/hash after the pre-submit envelope;
  - no venue order facts;
  - no venue trade facts;
  - one of the two positive-proof classes below.
- Positive-proof class A, pre-SDK proof:
  - the command's decision/correlation evidence ties to an execution failure
    whose exception occurred before `PolymarketClient()` construction or before
    `place_limit_order`;
  - the deployed source at that commit proves the failing boundary precedes SDK
    submit;
  - the clearance payload records the exact source commit, function, reason,
    decision_id, command_id, and why the side-effect boundary was not crossed.
- Positive-proof class B, external absence proof:
  - read-only venue/order/idempotency lookup is mandatory;
  - lookup must prove no order exists for the command's known order id or
    idempotency key;
  - if the adapter or venue cannot perform the lookup, uncertainty remains and
    the command stays `REVIEW_REQUIRED`.
- Append a legal terminal event, preferably a new explicit event or a constrained `SUBMIT_REJECTED` transition, with payload `review_cleared_no_venue_side_effect`.
- Governor must stop counting resolved terminal commands while continuing to count unresolved `REVIEW_REQUIRED`.

Relationship tests:

- Safe no-side-effect `REVIEW_REQUIRED` can be terminalized and stops blocking entry.
- `REVIEW_REQUIRED` with any order id, raw response, signed order, order fact, trade fact, or lookup uncertainty remains unresolved.
- The clearance path cannot be used from arbitrary states.

Live operation:

- Run the proof checker against `cb763c300b664a4f`.
- Only if all no-side-effect predicates and one positive-proof class pass, run the designed resolution command.
- Re-run live order checker and governor probe.

Acceptance:

- `unknown_side_effect_count=0`.
- Entry governor no longer blocks solely because of the cleared command.
- The evidence explicitly says this did not prove a live order; it only removed the blocker.

## Phase 5 - Global VPN Egress And Venue 4xx Grammar

Purpose: make process-visible blocked trading egress impossible to submit, and make deterministic venue rejections terminal without weakening real unknown-side-effect safety.

Tasks:

- Add a trading-egress authority check separate from `proxy_health` and independent of the stale localhost proxy environment:
  - data daemons may still strip a dead proxy when their task is data-only;
  - live trading must fail closed before order submit when the process-visible global VPN route is absent, geoblock probe is blocked, or egress proof is stale;
  - the check must record measured probe latency, blocked flag, country/region, network interface/VPN evidence when available, and route status without logging secrets.
- Add a read-only geoblock probe against `https://polymarket.com/api/geoblock` or an equivalent official endpoint before live entry submit.
- Treat `blocked=true` as a typed live-entry blocker, not as a recoverable order failure. It should prevent submit before `place_limit_order` and should not create another `SUBMIT_UNKNOWN_SIDE_EFFECT`.
- Add narrow exception classification for synchronous CLOB 4xx venue rejections after SDK contact:
  - geoblock 403 with no order id and no final envelope becomes terminal `SUBMIT_REJECTED` with reason `venue_rejected_geoblock_403`;
  - timeout, connection reset, unknown SDK exceptions, 5xx, and ambiguous bodies remain `SUBMIT_UNKNOWN_SIDE_EFFECT`;
  - 4xx generalization beyond geoblock requires separate proof and critic approval.
- Add a proof-backed terminalization path for the already-created command `8d82ea02c5b74905` only if all predicates prove the geoblock class:
  - latest event is `SUBMIT_TIMEOUT_UNKNOWN`;
  - exception type is `PolyApiException`;
  - exception message contains `status_code=403` and the geoblock body;
  - command has no venue order id;
  - persisted envelopes contain no order id, no raw response, no signed order, and no signed hash;
  - no `venue_order_facts`, no `venue_trade_facts`, no `position_events`, and no `position_current` rows exist for the command;
  - the historical payload itself proves a synchronous venue geoblock rejection at submit time; current `blocked=false` route proof is required only before retry, not as proof for terminalizing the old rejection.

Relationship tests:

- Process-visible geoblock `blocked=true` refuses live submit before `place_limit_order`.
- Process-visible global VPN route with `blocked=false` allows the rest of the existing entry gate chain to decide.
- Geoblock `PolyApiException` after SDK contact terminalizes as `SUBMIT_REJECTED` and stops counting as unknown.
- Timeout/network exception after SDK contact remains unknown and still blocks duplicate economic intent.
- Current-row terminalization rejects any side-effect evidence or non-geoblock exception payload.

Acceptance:

- A missing or blocked global VPN route cannot silently fall through into live trading.
- A deterministic geoblock 403 is not misclassified as a timeout unknown.
- `unknown_side_effect_count=0` only after the current command is terminalized by the geoblock proof rule or by stronger venue reconciliation evidence.
- The plan explicitly does not claim order success from geoblock rejection; it only removes a known-false unknown blocker and prevents repeated attempts unless process-visible egress is valid.

## Phase 6 - Production Recovery Capability Audit

Tasks:

- Fix or delete the unreachable advertised recovery path:
  - if venue-side idempotency lookup is real, implement `PolymarketClient.find_order_by_idempotency_key()` and prove the idempotency key is actually submitted to the venue;
  - if venue-side idempotency lookup is not real, remove it from the recovery claim and rely only on order id, signed-order hash, venue facts, or typed no-side-effect proof.
- Correct `PolymarketV2Adapter.get_open_orders()` to use the installed SDK method `get_open_orders` when available, while preserving existing tests for SDKs that expose `get_orders`.
- Add adapter tests using the actual method roster from the installed SDK surface.
- Add recovery tests proving that missing lookup capability remains unresolved rather than fabricating absence.

Acceptance:

- Command recovery's docstring, tests, and production adapter agree on what can actually be reconciled.
- No live unknown is cleared from "open orders list did not contain it" unless the lookup key is proven to be a venue-submitted key.

## Phase 7 - Rollout Gate/Reader Authority Reconciliation

Tasks:

- Reconcile why `check_data_pipeline_live_e2e.py` reports `LIVE_ELIGIBLE / EXECUTABLE_FORECAST_READY` while the live cycle block registry reports `PRODUCER_READINESS_EXPIRED` and `SOURCE_RUN_HORIZON_OUT_OF_RANGE`.
- Identify whether block registry gate 11 is:
  - authoritative and must block evaluator submit; or
  - diagnostic-only/stale and must not be presented as a live blocker.
- Add a relationship test around the exact reader evidence and rollout evidence objects so live submit authority cannot diverge from operator telemetry.

Acceptance:

- A live cycle cannot both report evaluator rollout gate blocking and create a command from that same gate decision.
- If the gate is stale telemetry, the block registry states that explicitly and points to the executable reader authority.
- If the gate is authoritative, final execution intent construction is blocked before executor submit.

## Phase 8 - Live Entry Gate Revalidation

Tasks:

- Re-run live health, data, and entry capability probes.
- Inspect latest decision artifact and block registry after the cleared command.
- If `PRODUCER_READINESS_EXPIRED` or `SOURCE_RUN_HORIZON_OUT_OF_RANGE` still appears while `check_data_pipeline_live_e2e.py` passes, root-cause the divergence between rollout gate and executable reader before submit.
- Verify trading egress is nonblocked through the intended route before restarting live submit attempts.

Acceptance:

- `entry.allow_submit=true`, risk GREEN, heartbeat healthy, WS gap clear, and `unknown_side_effect_count=0`; or
- a typed blocker is identified with a failing relationship test and repaired before retry.

## Phase 9 - Deploy Main-Based Branch To Live

Preconditions:

- Plan critic approved.
- Source changes committed.
- Focused tests pass.
- Topology planning-lock and map-maintenance pass for changed files.
- Live rollback path is recorded.
- Current runtime route is nonblocked for Polymarket trading, or live submit remains disabled with typed egress blocker evidence.

Deployment proof:

```bash
git status --short --branch
git rev-parse HEAD
git diff --quiet
launchctl print "gui/$(id -u)/com.zeus.live-trading"
launchctl print "gui/$(id -u)/com.zeus.forecast-live"
launchctl print "gui/$(id -u)/com.zeus.riskguard-live"
launchctl kickstart -k "gui/$(id -u)/com.zeus.live-trading"
launchctl kickstart -k "gui/$(id -u)/com.zeus.forecast-live"
launchctl kickstart -k "gui/$(id -u)/com.zeus.riskguard-live"
ps -axo pid,lstart,command | rg 'src\\.main|forecast_live_daemon|riskguard'
python3 scripts/check_daemon_heartbeat.py
python3 scripts/check_data_pipeline_live_e2e.py --json --live
python3 scripts/check_live_order_e2e.py --json
python3 scripts/check_live_trading_egress.py --json
```

Acceptance:

- Live processes run from the intended branch/commit.
- Launchd working directories and process cwd match the intended live checkout.
- The intended live checkout `git rev-parse HEAD` equals the branch commit
  under review, and the checkout is clean for loaded source/script/config files.
- Daemon heartbeat/status/log evidence includes or is joined to that same git SHA.
- Data verifier passes within timing budget.
- Live order checker fails only because no accepted order exists yet, not because stale unresolved state blocks entry.
- Trading egress checker reports `blocked=false` before any real order attempt.

## Phase 10 - Real Live Submit Attempt

This phase uses the normal daemon/evaluator/executor path only.

Pre-submit capture:

- cycle id/start time;
- candidate city/date/market/token/side;
- source_run and release key;
- posterior/edge/CI;
- final limit price, size, order type, post-only setting;
- snapshot id/hash and cost-basis id/hash;
- expected notional and risk allocation;
- idempotency key and command id once created.

Submit acceptance:

- `venue_commands` exists before SDK side effect.
- `SUBMIT_REQUESTED` exists.
- final envelope or equivalent submit evidence exists after SDK contact.
- venue returns accepted/resting/open order id or stronger fill evidence.

Failure handling:

- Rejected venue result: record terminal rejection, classify root cause, repair, and retry only after tests.
- Unknown result: enter unknown/review path and reconcile; do not clear without side-effect proof.
- No candidate: do not force a trade; diagnose funnel stage and wait/repair.

## Phase 11 - Post-Order Record Chain And Guard

For an accepted/resting order:

- Verify live order fact source is `REST`, `WS_USER`, `WS_MARKET`, `DATA_API`, or `CHAIN`.
- Verify latest order fact is open/resting and matches command order id.
- Verify no position is invented without fill.
- Continue guard until cancel/expiry/fill or normal lifecycle advancement.

For a fill:

- Verify venue trade facts.
- Verify position events.
- Verify position projection.
- Verify lifecycle phase is legal.
- Verify chain/local reconciliation is synced or explicitly quarantined.

Guard cadence:

- every 5 minutes for the first 30 minutes after accepted order;
- every 15 minutes until order terminal/fill;
- normal monitoring cadence after filled position.

## Timing And Performance Gates

- Live data verifier total: <= 3.0 seconds warm.
- Executable forecast reader p95 over 30 probes: <= 5 ms.
- Live order checker read-only classification: <= 1.0 second warm.
- Live trading egress/geoblock checker: <= 2.0 seconds warm and must report route, blocked flag, and probe elapsed milliseconds.
- Command persistence to SDK submit boundary: record measured elapsed; regression budget <= 1.0 second before network submit, excluding external CLOB latency.
- Forecast-live cycle telemetry must separate `download_seconds`, `extract_seconds`, `manifest_seconds`, `db_ingest_seconds`, `authority_seconds`, `commit_seconds`, `retry_sleep_seconds`, and `total_seconds`.
- HTTP 429 behavior must obey provider `Retry-After` when parseable, fallback otherwise, and never sleep after final failed attempt.

Final statements such as "efficient" or "highest efficiency" must cite measured seconds from the deployed process, not estimates.

## Critic Attack Checklist

The critic must reject this plan or any implementation phase if any answer is "no":

1. Does it prevent main/live branch drift from becoming proof drift?
2. Does it avoid importing the previous 54-file diff wholesale?
3. Does it make pre-SDK command failures terminal instead of unknown?
4. Does it provide a safe way to clear reviewed no-side-effect commands without clearing real unknowns?
5. Does it preserve command persistence before SDK side effects?
6. Does it require real venue order identity for submit proof?
7. Does it prove record-chain continuity after venue outcome?
8. Does it measure timing on the deployed live process?
9. Does it avoid shadow evidence as completion proof?
10. Does it stop if the system correctly finds no positive-EV candidate rather than forcing a trade?
11. Does it prevent process-visible geoblocked submit attempts before command creation, regardless of stale proxy env values?
12. Does it keep timeouts/ambiguous SDK failures unknown while terminalizing only proof-backed deterministic geoblock 403 rejections?
13. Does it reconcile command recovery claims with the SDK methods and keys that production actually has?
14. Does it prove rollout gate telemetry and evaluator submit authority cannot disagree?

`REVISE` is not approval.

## Stop Conditions

Stop and re-plan if a fix requires:

- direct SDK order placement outside executor;
- manual fabrication of command/order/fill/position rows;
- deleting or rewriting production DB files;
- bypassing riskguard, heartbeat, WS gap, rollout, collateral, or Q1 egress gates;
- clearing a `REVIEW_REQUIRED` command without no-side-effect proof;
- clearing a `SUBMIT_UNKNOWN_SIDE_EFFECT` command from an unsupported idempotency lookup or open-orders absence inference;
- treating a legacy localhost proxy setting as trading egress authority;
- retrying live submit while the process-visible geoblock probe remains `blocked=true`;
- claiming completion from data reader, command row, pre-submit envelope, rejected order, or shadow run alone.

## First Execution Slice

1. Commit this packet and registry update.
2. Run plan critic again on this geoblock-aware revision and revise until `APPROVE`.
3. Port or reimplement the read-only live-order checker on the main-based branch.
4. Add the pre-SDK terminal rejection antibody.
5. Add the proof-backed `REVIEW_REQUIRED` clearance path.
6. Use it to clear only the old safe no-side-effect live command if the proof predicates pass.
7. Add the trading-egress/geoblock checker and fail-closed entry gate.
8. Add deterministic geoblock 403 terminal rejection grammar for future attempts and a proof-backed terminalization tool for current command `8d82ea02c5b74905`.
9. Audit production recovery capability and fix or remove unsupported idempotency/open-orders claims.
10. Reconcile rollout gate telemetry with executable reader submit authority.
11. Restore a nonblocked trading route (`blocked=false`) and restart live processes on the intended commit.
12. Revalidate entry gates and run the normal live daemon until it places an expected accepted/resting live order or stronger outcome.
13. Guard and verify the canonical record chain until the designed next lifecycle state is proven.
