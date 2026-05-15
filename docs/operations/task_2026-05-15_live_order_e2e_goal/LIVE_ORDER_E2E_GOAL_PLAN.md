# Live Order E2E Goal Plan

Created: 2026-05-15
Last reused or audited: 2026-05-15
Authority basis: root `AGENTS.md` money path; `docs/operations/AGENTS.md` packet routing; topology admission for this packet; live read-only probes on 2026-05-15; user `/goal` directive requiring real live order placement and designed record-chain proof.

Branch: `feat/live-order-e2e-goal-2026-05-15`
Base: `origin/main` at `8b3c3c2c59 merge: data daemon live verified`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-order-e2e-goal-2026-05-15`
Critic status: Round 1 `REVISE`; this revision tightens safe review clearance, deployed-code provenance, positive verifier coverage, and active packet freeze gates.

## Goal

Complete the live money path with real live evidence:

`forecast-live data production -> forecast authority DB -> live reader -> live evaluator -> FinalExecutionIntent -> executor -> Polymarket limit order side effect -> command/order/fill/position/reconciliation records -> guard until the designed next lifecycle state is proven`

The task is not complete when data reaches the live reader. It is not complete when a command row is created. It is not complete when an order is rejected or moved to `REVIEW_REQUIRED`. Completion requires a normal live daemon cycle to place an expected live limit order through the executor path and prove that the resulting venue outcome is recorded through the canonical record chain.

## Current Empirical State

This plan starts from current read-only facts, not from the earlier branch narrative.

- New branch/worktree was created from `origin/main` at `8b3c3c2c59`.
- The active live root is still `/Users/leofitz/.openclaw/workspace-venus/zeus`, branch `deploy/live-order-e2e-verification-2026-05-15`, commit `aa4a7ccb21`. That commit is not the new main-based branch.
- The previous live-order branch is `feat/live-order-e2e-verification-2026-05-15`, ahead of `origin/main` by 29 commits with 54 changed files and more than 6000 inserted lines. It must not be blindly copied into this branch.
- Live data-pipeline verifier currently passes on the live root:
  - exactly one `src.main` process;
  - exactly one `src.ingest.forecast_live_daemon` OpenData owner;
  - no legacy `src.ingest_main` OpenData owner;
  - latest source run `ecmwf_open_data:mn2t6_low:2026-05-15T00Z`;
  - forecast target range starts at the source-cycle date, not before it;
  - live reader returns `LIVE_ELIGIBLE / EXECUTABLE_FORECAST_READY`;
  - measured reader latency `0.526 ms`, total verifier elapsed `503.402 ms`.
- Live order verifier currently fails:
  - latest command `cb763c300b664a4f`;
  - state `REVIEW_REQUIRED`;
  - event chain `INTENT_CREATED -> SUBMIT_REQUESTED -> REVIEW_REQUIRED`;
  - pre-submit envelope exists;
  - no `venue_order_id`;
  - no accepted event;
  - no open venue order fact;
  - no position rows.
- The live cycle after recovery recorded `command_recovery.scanned=1` and `advanced=1`, then portfolio governor counted `unknown_side_effect_count=1`, armed kill switch with reason `unknown_side_effect_threshold`, and blocked new entries.

Current conclusion: the data side is ready enough to feed live, but the live money path is blocked by an unresolved command/recovery semantics failure. The next root cause to eliminate is not "wait for another cycle"; it is the design gap where a pre-SDK failure can leave a command in an unresolved side-effect state that permanently blocks entry.

The existing `REVIEW_REQUIRED` command must not be cleared from local absence
alone. It can be cleared only by positive proof that SDK submit was never
reached for that command, or by mandatory external venue/order/idempotency
lookup proving absence. If neither proof exists, it remains unresolved and live
entry remains blocked until an operator-safe venue reconciliation path exists.

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
- Resolving the current `REVIEW_REQUIRED` command requires a proof-backed state transition or recovery tool, not manual DB editing.

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

## Phase 5 - Live Entry Gate Revalidation

Tasks:

- Re-run live health, data, and entry capability probes.
- Inspect latest decision artifact and block registry after the cleared command.
- If `PRODUCER_READINESS_EXPIRED` or `SOURCE_RUN_HORIZON_OUT_OF_RANGE` still appears while `check_data_pipeline_live_e2e.py` passes, root-cause the divergence between rollout gate and executable reader before submit.

Acceptance:

- `entry.allow_submit=true`, risk GREEN, heartbeat healthy, WS gap clear, and `unknown_side_effect_count=0`; or
- a typed blocker is identified with a failing relationship test and repaired before retry.

## Phase 6 - Deploy Main-Based Branch To Live

Preconditions:

- Plan critic approved.
- Source changes committed.
- Focused tests pass.
- Topology planning-lock and map-maintenance pass for changed files.
- Live rollback path is recorded.

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
```

Acceptance:

- Live processes run from the intended branch/commit.
- Launchd working directories and process cwd match the intended live checkout.
- The intended live checkout `git rev-parse HEAD` equals the branch commit
  under review, and the checkout is clean for loaded source/script/config files.
- Daemon heartbeat/status/log evidence includes or is joined to that same git SHA.
- Data verifier passes within timing budget.
- Live order checker fails only because no accepted order exists yet, not because stale unresolved state blocks entry.

## Phase 7 - Real Live Submit Attempt

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

## Phase 8 - Post-Order Record Chain And Guard

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

`REVISE` is not approval.

## Stop Conditions

Stop and re-plan if a fix requires:

- direct SDK order placement outside executor;
- manual fabrication of command/order/fill/position rows;
- deleting or rewriting production DB files;
- bypassing riskguard, heartbeat, WS gap, rollout, collateral, or Q1 egress gates;
- clearing a `REVIEW_REQUIRED` command without no-side-effect proof;
- claiming completion from data reader, command row, pre-submit envelope, rejected order, or shadow run alone.

## First Execution Slice

1. Commit this packet and registry update.
2. Run plan critic and revise until `APPROVE`.
3. Port or reimplement the read-only live-order checker on the main-based branch.
4. Add the pre-SDK terminal rejection antibody.
5. Add the proof-backed `REVIEW_REQUIRED` clearance path.
6. Use it to clear only the current safe no-side-effect live command if the proof predicates pass.
7. Revalidate entry gates.
8. Deploy the committed branch to live.
9. Run the normal live daemon until it places an expected accepted/resting live order or stronger outcome.
10. Guard and verify the canonical record chain until the designed next lifecycle state is proven.
