# finite_evidence_probability_symmetry -- Plan

Date: 2026-07-11
Branch: `live` (was `p2-pending-exit-restart-redecision`; renamed at main→live cutover)
Status: active

## 2026-08-17 Current-state mean actions must not be deleted by FDR confidence

Post-restart live receipts selected two positive posterior-mean actions but the
winner preflight deleted both through the legacy `FDR_REJECTED` route.  Their
global certificates already proved a fixed executable action using posterior-
predictive-mean EV and expected log wealth; the sampled false-edge rate is
confidence evidence and cannot replace that action's expected payoff.  The
solver-side deletion had already been removed, but the selected-route FDR
adapter still reapplied the same confidence threshold immediately before
submission.

For an exact sealed global current-state certificate, the selected-route FDR
shape now records the hypothesis and diagnostic false-edge rate without using
that rate as action admission.  The prefilter, certificate grammar, current q,
JIT book, wealth, positive mean EV/log-growth, price band, fees, depth, Kelly,
and all submit-boundary verification remain cumulative.  Legacy non-current-
state qkernel and family-BH routes retain their existing FDR law.  SCOPE is one
globally selected current-state BUY preflight.  DRAIN is the next complete
auction and JIT revalidation.  RESET is each new sealed global decision; stale
q/book/wealth still supersedes and reauctions rather than inheriting this pass.

## 2026-08-17 Target-complete ENS windows must not wait for an unused 144h tail

Current live auction evidence contains 150 probability families and target dates
only through 2026-08-19.  Recomputing each family against the 2026-08-17 00Z
cycle and its configured city timezone gives a largest honest required period-end
step of 72h; no current family requires step 144.  The OpenData producer still
uses the full-horizon 08:05 UTC safe-fetch gate and therefore withholds every
target even after that target's complete step window is available.  Recent
primary-object metadata places step 72 around 07:42 UTC and step 144 around
07:45 UTC, while the current gate delays collection until 08:05 UTC.

The partial window becomes collection-eligible without making an incomplete
target executable.  A source run may remain PARTIAL, but only a target whose
exact `source_run_coverage.expected_steps_json` is a subset of observed steps,
has all expected members, and passes the existing executable reader may become
COMPLETE/LIVE_ELIGIBLE.  `BLOCK_LIVE` retains its old meaning; the ECMWF tracks
move to an explicit target-window-complete policy rather than silently changing
that token's semantics.  Every partial refresh remains retryable until the full
run succeeds, and each newly committed partial frontier may wake newly complete
replacement scopes instead of being suppressed by a source-run-id-only wake
ack.

SCOPE is the two live ECMWF OpenData HIGH/LOW tracks and only target windows
whose complete required steps and 51 members have been persisted.  DRAIN is the
five-minute safe-cycle poll: it retries the same cycle, atomically replaces its
source/coverage/readiness rows, and re-enqueues the scopes made complete by the
new observed-step frontier.  RESET is a SUCCESS journal for the complete cycle;
until then PARTIAL never satisfies current-cycle dedup, and missing required
steps continue to block only their dependent target windows.  Acceptance
requires calendar antibodies for old BLOCK_LIVE and new target-complete policy,
producer/daemon selection at the partial gate, repeated partial-frontier wake
proof, full focused suites, and live rows proving source availability, target
coverage, replacement revision, auction result, and any venue actuation.

## 2026-08-13 Shared quote warming cannot consume the reserved q tranche

Live restart recovery repeatedly admitted a full-book held monitor with roughly
74 seconds remaining and explicitly reserved 35 seconds for seven current
probability reads.  The first position's five-second child deadline was created
before the shared network order-book batch, however.  When that auxiliary batch
returned after the child deadline, the position was rejected before its first q
read; the entire pass then produced zero canonical `MONITOR_REFRESHED` rows,
retaining both monitor debt and the restart entry guard.

An admitted statistical position now receives its bounded child deadline after
shared quote warming and immediately before its position-owned metadata/q path.
The outer monitor deadline remains absolute and unchanged; non-admitted
positions receive no extra capacity, and missing HWM, probability, or quote
truth still fails closed.  SCOPE is only an admitted held-position statistical
redecision whose earlier child clock was consumed by shared prerequisite work.
DRAIN is a complete fresh q/book evaluation followed by the existing canonical
monitor append.  RESET is every position attempt and every new outer monitor
claim.  Acceptance requires an antibody where shared prefetch crosses the old
child deadline but the admitted position still completes inside the unchanged
outer deadline, plus live canonical coverage, restart-guard CAS recovery, and a
new global BUY/SELL/HOLD/CASH cut.

## 2026-08-13 Partial exits retire sold capital from every auction authority

An authenticated Miami SELL reduced the current open position to 0.00857 shares
and $0.002399599 cost, while the chain API retained the original $1.4499 lot
`initialValue`.  Position-level exit and PnL authority already used the reduced
open-fill economics, but global wealth took the maximum of fill, chain, and
projection costs, while the DB-backed family selector preferred chain cost.
Both therefore re-committed sold capital and could suppress or distort the next
positive-growth order.

Fill authority now governs cost selection at both consumers: a verified trade
fill uses current open-fill cost/shares; a balance-only rescue uses chain
economics; legacy unknown authority retains the conservative fallback.  SCOPE
is global wealth and same-family selection after a partial SELL.  DRAIN is the
next complete global cut reading the reduced canonical open lot.  RESET is each
new authenticated entry/exit fill or chain observation.  Acceptance requires
stale full-lot chain cost to be ignored for a verified residual while the
existing pre-fill chain-lag antibody still charges the complete newer fill.

The same widened SELL integration suite exposed a final-boundary scope defect
in the preceding deadline hotfix: `_submit_current_global_sell` referenced the
adapter closure's held deadline even though it is also a module-level direct
boundary.  Every direct global SELL therefore failed before venue actuation
with `NameError`.  The caller now passes the exact terminal reason explicitly;
ordinary epochs retain `GLOBAL_REAUCTION_EPOCH_EXPIRED`, while a request-bound
deadline retains `HELD_SELL_DEADLINE_EXPIRED` without hidden closure state.

## 2026-08-13 Terminal incremental fills remain canonical recovery debt

The first live 1/8-Kelly cut produced four venue-confirmed entries.  The Miami
refill command bought 17 additional NO shares at 0.30, but its immediate
position projection lost a SQLite writer race after the command had already
become `FILLED`.  The canonical position therefore remained at the prior five
shares and $1.60 cost even though the confirmed fill made the true aggregate 22
shares and $6.70 cost.  Periodic authenticated-fill recovery excluded every
`FILLED` command, while the terminal-order repair compared the 17-share refill
against the whole five-share position instead of treating it as a delta.

A terminal `FILLED` ENTRY with exact confirmed trade facts, a pre-existing
same-token command-level fill aggregate, a different current order id, and no
command-bound fill projection/execution fact is now explicit recovery debt.
Recovery rebuilds the aggregate from command-level execution facts, writes one
idempotent `ENTRY_ORDER_FILLED` delta, and preserves the terminal command event.
A lagging pre-fill chain balance downgrades `chain_state` to `unknown`; it may
not remain falsely `synced` while command-derived exposure is larger.  A SQLite
writer-lock failure propagates to the bounded recovery policy instead of being
logged and counted as success.

SCOPE is one exact terminal refill command and its same-token aggregate
position.  DRAIN is the next authenticated-entry recovery pass under the
canonical TRADE writer lease.  RESET is the presence of both the exact
command-bound fill event and positive execution fact; a `FILLED` command or log
message alone is not reset.  Acceptance requires the 5 + 17 = 22 shares,
$1.60 + $5.10 = $6.70 cost antibody, one-event replay idempotence, explicit
writer-lock propagation, focused recovery/reconciliation suites, and live proof
that command `6a5be1b238f3472d` is represented by the canonical Miami position.

Allowed files for this repair are `src/execution/command_recovery.py`,
`src/execution/exchange_reconcile.py`, `tests/test_command_recovery.py`,
`architecture/test_topology.yaml`, and this plan.

## 2026-08-13 Governed 1/8 Kelly removes artificial capital starvation

Current complete global cuts found a positive posterior-mean London 32C YES
maker proposal at 0.12, but rejected its minimum executable addition solely as
`FRACTIONAL_KELLY_TARGET_REACHED`: the existing $3.03 holding already exceeded
the governed 1/32 target even though the proposal's current q mean was about
0.242 and its expected EV was positive.  With roughly $545 capital basis and a
full binary-Kelly fraction near 0.139, 1/32 constrained the family to roughly
$2.37 while leaving nearly all spendable capital idle.  That is an artificial
no-order outcome, not a global capital optimum.

The governed live fraction is retuned to 1/8.  At the same decision-time truth,
the target is roughly $9.46, permitting about $6.43 of additional exposure
before ordinary depth, correlated-payoff, portfolio, city, and single-position
limits.  This is a fourfold increase over 1/32 but still one half of the 1/4
correlated ceiling and far below full Kelly; posterior confidence bounds remain
diagnostic while posterior mean continues to own expected-log-growth ranking.

SCOPE is live Fractional Kelly sizing for every otherwise admissible proposal.
DRAIN is the next complete global cut under a freshly loaded 1/8 config, followed
by candidate-specific submit-time q/book/wealth reproduction.  RESET is every
new global cut and JIT preflight; moved price, probability, depth, risk, or
capital can reduce the target or select CASH.  Acceptance requires the exact
governed-value and correlated-ceiling antibodies, exact live reload, a current
auction receipt carrying `fractional_kelly_multiplier=0.125`, and either an
actual positive-growth venue order/fill or a precise non-artificial current
economic rejection.

## 2026-08-13 Fresh canonical coverage discharges stale auction fairness debt

A recovery full-book monitor can time out waiting for the reactor while an
already-running urgent held monitor independently refreshes every current
position.  The timeout arms periodic fairness debt, but recovery previously
cleared only canonical cadence debt after its final DB read proved the entire
held book fresh.  The leftover concurrency debt then cancelled every reserved
global auction before selection, creating a no-order ratchet after the monitor
obligation had already been satisfied.

Recovery now clears periodic fairness debt together with canonical debt only
after the exact post-attempt canonical read proves zero blocking-stale and zero
future monitor evidence.  SCOPE is the completed recovery obligation for the
current held book.  DRAIN is that exact canonical clean read.  RESET is a later
periodic handoff timeout or newly overdue held position, either of which arms a
new debt from current truth.  Acceptance requires an antibody where recovery
starts stale with fairness debt armed, ends canonically fresh, releases the
auction, and a live global cut reaches a terminal current winner/CASH result.

## 2026-08-13 Canonical monitor never waits for a lease while holding SQLite

Current runtime evidence showed a three-process lock-order cycle on the live
trade DB.  The held-position monitor retained an open SQLite write transaction
while requesting its canonical MONITOR lease; the substrate observer held the
MONITOR waiter/turnstile while waiting for the unified writer; post-trade
capital held that unified writer while waiting for SQLite.  Nine held
positions then exceeded canonical monitor cadence, BUY admission correctly
failed closed, and command recovery could not re-decide the live Miami maker
order.  This is a capital-path liveness defect, not evidence that CASH is the
global economic optimum.

Before requesting the canonical writer lease, the monitor now commits any
already-open auxiliary transaction on its long-lived connection.  The later
MONITOR_REFRESHED event and projection still append atomically in their own
bounded leased transaction.  No network call, probability value, exit verdict,
or order authority is changed.

SCOPE is one held-monitor canonical append on the live trade DB.  DRAIN is the
pre-lease commit releasing SQLite, followed by the existing bounded MONITOR
lease and event+projection transaction.  RESET is every append attempt; an
auxiliary commit failure rolls back and defers the canonical event to the next
monitor cycle.  Acceptance requires an antibody proving the connection is not
in a SQLite transaction when lease acquisition starts, focused monitor/write
coordinator tests, exact live reload, disappearance of the lock-order cycle,
fresh canonical coverage for all current positions, and command recovery of
the current resting order.

## 2026-08-13 Current witnessed maker competes on capital growth

The global auction already constructs a candidate-bound
`CurrentMakerFillWitness` from causally closed actual maker outcomes, binds it
to the current book epoch, passive limit, rest deadline, and selection-time
cut, and scores every zero/partial/full-fill outcome on the same
posterior-mean expected-log-growth axis as taker, SELL, HOLD, and CASH. The
entry policy nevertheless rejected every statistical `MAKER_REST` BUY before
that economics ran. This duplicated the typed witness contract and made a
currently executable capital-efficient order infeasible solely because its
fill is contingent.

The blanket statistical-maker veto is removed. A maker BUY is still admitted
only when the solver has validated its complete current witness and when the
exact token already owns at least one venue-minimum SELL lot. The latter
constraint remains mandatory: a first maker partial fill can otherwise create
exposure below the legal exit size and defeat probability-reversal exit. An
unwitnessed, stale, mismatched, unseeded, non-positive, or JIT-superseded maker
still cannot win or submit.

SCOPE is statistical `MAKER_REST` BUYs that already satisfy both typed current
fill authority and exitability seed law. DRAIN is the next complete global
BUY/SELL/HOLD/CASH comparison and, for a winner, exact-mode JIT revalidation.
RESET is every new q/book/wealth/fill-witness cut; no earlier maker winner or
fill distribution carries forward. Acceptance requires the adapter to contain
no statistical-maker blanket veto, the existing witness tamper/expiry,
partial-fill economics, and unseeded-maker antibodies to remain green, exact
live reload, and a current auction receipt showing either a positive witnessed
maker submission or the next exact current economic rejection.

## 2026-08-13 Positive winner retains time for submit-time revalidation

After actual admission matched the proof comparator, a Shenzhen BUY winner was
selected with positive posterior-mean growth but spent about 26 seconds in its
mandatory selected-family JIT revalidation.  The whole global cut had only 30
seconds including scope, book, and solve, so the exact duplicate-order read was
interrupted at the deadline and no venue command could be formed.

The cut is now 45 seconds.  Existing cancellation still yields to pending held
monitor or new hard Day0 authority; the book remains bounded by its separate
180-second expiry.  SCOPE is one global auction invocation.  DRAIN is a selected
winner completing JIT preflight and final actuation inside the enlarged cut.
RESET is the next invocation or any cancellation/book expiry, which still ends
the current cut without a venue side effect.  Acceptance requires the focused
work-cut antibody, the global auction integration suite, exact-head restart,
and a post-load command/venue receipt or an explicit current rejection reason.

## 2026-08-13 Current positive growth, not prior settlement, admits risk

The statistical-entry gate required profitable settlement evidence from the
exact current selection revision before that revision could place its first
order.  The proof-only comparator then repeatedly found positive executable
posterior-mean BUY winners while the actual comparator was forced to CASH.
This is circular admission: it prevents both current capital gain and the very
realized evidence demanded to clear it.

Statistical immediate-taker BUYs now enter the same live feasible set as their
proof comparison.  They still require current licensed probability semantics,
source freshness, exact executable book and fees, pre-cliff liquidation depth,
strategy policy, Fractional Kelly sizing, capital/risk limits, and submit-time
JIT reproduction.  Unresolved Day0 probability, unwitnessed maker fills, and
maker entries without a venue-legal exit seed remain blocked.  A negative or
absent expected-growth winner still selects CASH; this
change does not create a minimum-order quota.

SCOPE is the removed bankroll-wide settlement-history veto only.  DRAIN is the
next complete global cut, whose positive winner proceeds to ordinary preflight
and actuation.  RESET is every new q/book/wealth cut and submit-time preflight;
stale or negative economics cannot inherit an earlier winner.  Acceptance
requires the actual candidate policy to contain no settlement-history veto,
focused adapter and global-auction tests, exact-head restart, and a current
receipt showing either a genuinely submitted positive winner or current CASH
with no positive proof winner.

## 2026-08-13 Capital proof winner identity is isolated from frontier telemetry

The proof receipt scans rejected BUY frontiers before serializing its winner.
That scan reused the winner's local `family_key` and `context` variables, so a
frontier from another family could relabel the winner and make its exact q
diagnostic unavailable.  Selection itself was unchanged, but the receipt could
not prove which current probability witness owned the apparent positive order.

Winner identity and context are now frozen before the frontier scan; every
frontier uses its own scoped identity.  SCOPE is proof-only receipt telemetry.
DRAIN is the next complete global cut under the loaded fix.  RESET is every new
cut, which recomputes both identities from the selected candidate and current
evaluation set.  Acceptance requires a winner plus a later rejected frontier
from a different family, with the winner retaining its own family, city, q,
and confidence-cost diagnostic.

## 2026-08-13 Capital proof exposes confidence-cost amplification readiness

Posterior-mean expected growth is the common comparison axis for fixed BUY,
SELL, HOLD, and CASH proposals.  A positive mean winner does not by itself show
that increasing risk is robust to the current probability uncertainty.  The
proof-only winner therefore also freezes the selected-side lower-tail
probability confidence bound, exact fee-inclusive cost per share, and their
margin.  A non-positive margin is explicitly `BLOCKED`; a positive margin says
only that this diagnostic passed and still requires every ordinary admission,
risk, depth, Kelly, and submit-time truth check.

The confidence bound remains evidence, never a relabeled fixed-action expected
payoff and never a second selection objective.  SELL winners are capital-
release actions, so the entry-cost amplification diagnostic is explicitly not
applicable.  SCOPE is side-effect-free capital-proof telemetry only.  DRAIN is
the next complete global cut, which recomputes the diagnostic from the exact
probability witness and selected all-in cost.  RESET is any fresh q/book/wealth
cut or a different winner; no prior positive margin carries forward.  Acceptance
requires an antibody where a positive posterior-mean BUY has a negative
confidence-cost margin, remains the proof winner, and is stamped blocked without
changing selection or venue-submit counts.

When no positive order exists, a bare CASH/HOLD result does not identify what
must move before capital can be deployed.  The same proof receipt therefore
freezes the nearest rejected executable BUY probe under the solver's exact
posterior-mean comparator order: expected log-growth rate, expected delta-log
wealth, capital efficiency, cost, then stable candidate identity.  Its current
mean-cost and confidence-cost gaps expose the q/price frontier without turning a
negative probe into an order.  The frontier is absent when no BUY reached exact
fee/depth/economic scoring, and it resets on every complete global cut.

## 2026-08-13 Capital evidence is independent by target date

The capital evaluator called `(city, target_date, metric)` an independent
family-day.  That can count several globally selected orders from the same
target date as separate observations even though they share the same forecast
issuance environment, model error, and broad weather regime.  The plan's
minimum evidence unit is one target date, not one city-market cell.

Settlement grading now keeps only the first valid complete-global-cut proof per
target date.  The sample still records the selected city, metric, condition,
side, cost, payoff, and exact probability semantics, but another selection on
the same target date cannot increase the independent sample count.  This is a
strict evidence correction only; it cannot admit an order, alter selection, or
change held-position redecision.

SCOPE is the observational current-regime admission evaluator.  DRAIN is one
causally settled proof from a previously unseen target date.  RESET is the next
distinct target date; another city or metric on an already-counted date does
not reset the independence gate.  Acceptance requires an antibody with two
different families on the same target date counting once, renamed target-date
contract fields/reasons, and a current evaluator run that remains fail-closed
until 30 distinct target dates and a positive after-cost delta-log-wealth lower
bound exist.

## 2026-08-13 Capital proof retains every licensed current-evidence revision

The live replacement authority admits two exact, non-interchangeable current-
evidence shapes: same-cycle `ensemble_center_scenarios_v4`, and bounded latest-
causal `stale_ensemble_absolute_disagreement_v2`.  The latter retains raw
absolute ENS members, charges their full center disagreement against the
current provider center, forbids translation, and remains bounded by the
existing source-cycle age law.  The capital-proof writer nevertheless stamped
only the same-cycle revision.  A latest-causal statistical BUY could therefore
win the exact current q/book/wealth comparison with an empty semantics field,
making every later settlement sample ungradeable even though the probability
reader had already proven entry authority.

The proof writer now reuses the canonical entry-authority predicate and freezes
the exact persisted revision for both licensed shapes.  The evaluator accepts
those two revisions explicitly and verifies the solver's real immediate-taker
certificate: `TAKER_LIMIT`, full immediate fill probability, and the existing
`SETTLEMENT_LOCKED_BUY` capital mode.  It no longer asks for the nonexistent
`IMMEDIATE_TAKER_BUY` label.  Maker-rest or partial-fill counterfactuals remain
inadmissible, and neither revision bypasses executable book, fee, wealth,
pre-cliff liquidation, settlement, or positive lower-bound requirements.

SCOPE is proof-only probability semantics and later settlement grading; live
order selection and actuation are unchanged.  DRAIN is the next complete
global cut, which stamps the exact licensed revision on its proof winner, then
the ordinary causal settlement join.  RESET is a fresh posterior whose shape
passes the canonical entry-authority predicate; malformed, translated,
revision-mismatched, or over-age shapes emit no proof semantics.  Acceptance
requires antibodies for same-cycle and bounded latest-causal stamping, stale-
revision settlement grading, immediate full-fill taker proof, and continued
maker rejection.

## 2026-08-12 Restart recovery is current-risk bounded

The live-trading restart lane stopped the order daemon safely, then spent its
entire outer timeout inside command recovery even though the restart scope is
defined as current dangerous side effects only.  Unlike `live_tick` and the
bounded full sweep, `restart_preflight` did not thread its supplied scheduler
deadline into writer-lease acquisition, SQLite progress interruption, or the
per-pass apply factory.  A historical scan could therefore keep held-position
redecision offline while doing work that the recurring recovery lane already
owns.

Restart recovery now receives an absolute deadline shorter than the deploy
subprocess timeout and treats `restart_preflight` as a bounded scope throughout
its read/apply topology.  The trailing EDLI trade-fact bridge has its own shorter
SQLite deadline and typed contention deferral, so neither lane can consume the
other's budget. Deadline exhaustion records a typed deferral; the
subsequent read-only preflight remains responsible for refusing startup if any
current submit/cancel/exit ambiguity is still dangerous.  No historical row is
deleted, relabeled, or used to authorize a trade.

SCOPE is deploy-time command recovery before `live-trading` bootstrap.  DRAIN
is the existing recurring `live_tick`/full recovery after the daemon starts,
while current restart-dangerous commands remain covered by the read-only
preflight.  RESET is a fresh restart invocation with a new finite deadline.
Acceptance requires factory tests proving deadline interruption and finite
writer-lease acquisition for `restart_preflight`, deploy wiring of the absolute
deadline, and successful exact-head restart with entries still paused.

## 2026-08-12 Current truth is not current-regime capital advantage

### Side-effect-free global capital counterfactual

An entry pause cannot itself shrink the evidence universe to held families:
that makes the capital-proof gate self-referential because statistical BUYs
are rejected before their economics exist.  Each complete current q/book/
wealth cut therefore runs one additional proof-only selection over the same
prepared universe.  It ignores only non-actuation global admission state
(including the settlement-graded capital-proof pause that the receipt is
intended to drain); freshness, probability semantics, source quality,
family-local readiness, strategy policy, price band, fees, depth, Kelly, risk
and capital limits remain unchanged.

The proof result is embedded in the actual schema-22 global-auction receipt,
has `venue_actuation_available=false`, and asserts the venue submit counter is
unchanged.  The ordinary selected object remains the sole preflight/actuator
input.  SCOPE is statistical evidence for current selection-law evaluation.
DRAIN is settlement joining of one first complete receipt per independent
family-day.  RESET is a new selection/probability revision, which makes prior
receipts ineligible rather than mutating them.

The full global solver used current q, book, and wealth but let its
`current_state_solve` path bypass settlement-graded OOF reliability.  Freshness
prevents time travel; it does not prove that the model is more accurate than
the executable market after costs.  The current artifact predates both the
active probability semantics and the current global selection/execution rule,
while recent live fills include selected beliefs near 0.90 that later collapsed
to approximately zero.  It cannot license fresh risk.

All risk-increasing statistical BUYs are now excluded before strategy, Kelly,
or capital ranking.  Current Day0 LOCKED/REFUTED hard facts remain eligible
because their payoff is fixed by monotone source truth rather than estimated
model advantage.  SELL/HOLD/CASH remain continuously comparable, and this gate
does not force liquidation.  The no-order outcome is a valid global optimum in
a zero-sum market when no positive executable advantage is proven.

SCOPE is every statistical BUY under forecast or unresolved/unknown Day0
authority.  DRAIN is a strict causal walk-forward artifact that grades the exact
current probability semantics, current selection rule, executable cost/fill
regime, and settlement outcome.  RESET is successful validation consumed by
the same candidate-policy seam; process health, fresh q, config, or restart
cannot clear it.  Acceptance requires direct helper tests for statistical and
hard-fact cases, source-order proof before strategy/Kelly/capital, focused
global-entry tests, zero post-load ENTRY commands, and a complete held-capital
auction receipt.

## 2026-08-12 Marginal peak time is not today's conditional peak state

The Day0 HIGH generator mixed a historical `diurnal_peak_prob` atom into the
current remaining-path distribution.  That table estimates only
`P(peak already set | city, month, local hour)`.  It does not condition on
today's temperature slope, time since the running high, fast observation
innovation, provider trajectories, or remaining-path spread.  Live Dallas and
NYC fills exposed the category error: roughly 0.90 selected-side entry beliefs
collapsed to 0.0041 and 0 after the current temperature kept rising.

The empirical clock probability remains attached as telemetry, but it no
longer enters point q, bootstrap rows, held value, or order ranking.  Until a
strictly causal walk-forward conditional model is proven, the current
observation boundary plus current remaining provider path is the sole
statistical Day0 distribution.  Existing unresolved-Day0 BUY containment stays
in force, so this change cannot reopen entries; locked/refuted hard facts remain
unchanged, and SELL must still beat HOLD/CASH on the unified capital objective.

SCOPE is the statistical Day0 HIGH distribution for unresolved payoffs.  DRAIN
is immediate recomputation under probability basis v6 on the next monitor or
auction cycle.  RESET is a new q witness that excludes the marginal atom; a
future peak-state atom may return only with a named conditional basis and
strict walk-forward proof.  Acceptance requires a relationship antibody that
the same remaining path produces the same q with or without marginal telemetry,
the focused Day0 probability suite, zero new ENTRY commands while paused, and
a post-load held-capital auction receipt on the new basis.

## 2026-08-12 Paused probability carriers still redecide held capital

The post-hot-fix restart correctly kept new ENTRY commands at zero, but its
post-start proof could not form a complete held-coverage global-auction receipt.
Runtime logs showed the contradiction: the paused forecast wake materialized
its current carrier and then returned "without auction", while the next generic
reactor cycle was rejected before runtime setup because entries were globally
blocked. The carrier path therefore refreshed probability evidence but could
never compare current SELL/HOLD/CASH for capital already at risk.

A materialized paused forecast carrier now reads current held-family exposure.
An exact empty read retains the cheap no-auction completion. Non-empty or
unreadable exposure continues into the existing global completion cut, which
sets `selection_completion_reserved`, disables every BUY proposal, restricts
known scope to held families, and compares SELL/HOLD/CASH on the existing
posterior-mean expected-log-growth axis. It creates no authority to sell at a
loss: SELL must still beat HOLD and CASH under current executable truth.

SCOPE is one materialized forecast carrier while entries are paused and current
held exposure exists or cannot be read exactly. DRAIN is the existing bounded
reduce-only global auction and its canonical receipt. RESET is an exact empty
held-family read, or clearing the pause so the ordinary full feasible set runs;
a failed exposure read never resets money-at-risk redecision. Acceptance
requires helper failure/empty/non-empty antibodies, source-order wiring through
the reduce-only completion mode, the adapter's existing BUY-disabled held-scope
antibody, hot-fix landing, exact loaded SHA, zero new ENTRY commands while
paused, and one post-load complete held-coverage global-auction receipt.

## 2026-08-12 Current hold value cannot depend on sunk entry provenance

A venue-confirmed fill can outrun its original position projection.  The
recovery path preserved the real exposure but historically used zero as the
missing entry posterior.  The held exit-context builder then coupled the fresh
current confidence interval to that historical field: even with a fresh Day0
q and fresh executable book, it omitted `current_ci` and forced
`EVIDENCE_UNAVAILABLE`.  This is a category error.  The unified exit law
compares current liquidation proceeds with current hold value; entry belief and
entry price are sunk attribution facts, not inputs to that comparison.

Current belief bounds are now built whenever the current q/edge band and book
price are finite, independently of the optional entry witness.  Entry posterior
and entry CI remain available when valid and remain absent when not proven; no
historical probability is fabricated.  SCOPE is one held-position exit context.
DRAIN is the next normal monitor refresh with fresh q and book.  RESET is a
finite current held-side CI; stale/missing current evidence still fails closed.
Acceptance requires an antibody with `p_posterior=0` plus fresh current q/book
that reaches the same predicted-bin SELL law as every other position.

## 2026-08-12 Recovered fills cannot depend on a dead cross-DB FK

The confirmed-fill projection can recover a real venue acquisition before its
legacy `trade_decisions` bridge exists.  Lifecycle compatibility then invokes
the existing bridge synthesizer, but the physical trades DB still declares
`forecast_snapshot_id -> main.ensemble_snapshots`.  The K1 split moved that
parent to the forecasts DB, and SQLite foreign keys cannot cross attached
schemas.  With foreign-key enforcement enabled, every bridge INSERT or UPDATE
therefore fails at statement preparation even when the soft reference is NULL.

The already-reviewed crash-atomic W0-a rebuild removes only that unreachable
FK.  Its schema pin is advanced to the current physical shape so the later
`decision_law_id` column is copied and typed-digest checked rather than rejected
as drift or lost.  No canonical event, position, fill, probability, or execution
fact is rewritten.

SCOPE is the single `trade_decisions` table in the canonical trades DB.  DRAIN
is a short all-writer fence, one WAL transaction, and reopening every writer.
RESET is a fresh-connection schema proof with no dangling FK followed by a
genuine recovered-fill bridge write.  Acceptance requires the existing crash,
schema-drift, sequence, and row-digest matrix plus an antibody that preserves a
non-NULL `decision_law_id`, a fenced dry run, apply receipt, and forward Dallas
held-monitor evidence.

## 2026-08-12 Held freshness uses the provider vector clock

The replacement posterior has two different clocks: one causal ENS/anchor
carrier in `source_cycle_time`, and one exact per-provider value vector in
`bayes_precision_fusion.current_value_serving`.  The held reader already
verified the latter against current raw-row identities, but then independently
compared the newest scalar provider cycle with the older carrier and vetoed the
same posterior again.  A fully current mixed-clock posterior therefore became
permanently stale even after its repair seed had consumed the new provider
rows.

The scalar cycle difference is now telemetry only.  Freshness is invalidated
by the existing exact vector-HWM reason when a provider row is superseded,
late, missing, mismatched, or unverifiable.  A posterior is fresh across mixed
clocks only when its causal shape remains legal and every used provider's exact
served row is still current.

SCOPE is one held `(city, target_date, metric)` posterior read.  DRAIN is the
existing input-revision/cycle materializer.  RESET is exact equality between
persisted `current_value_serving` identities and the current coherent provider
frontier; an unconsumed or unverifiable row remains fail-closed even when only
one used provider has advanced and a replacement cohort is not materializable
yet.  Acceptance requires paired antibodies for a consumed newer provider
vector, an isolated unconsumed revision, and a superseding coherent provider
vector, plus forward live Madrid monitor proof.

## 2026-08-12 Expired held belief can rematerialize its same causal cycle

The held monitor expires a replacement posterior on its computation clock even
while the consumed source cycle remains inside the shared causal age bound.  Its
repair worker previously treated that old posterior as permanent proof that the
cycle was already covered.  With no newer provider revision or carrier cycle,
the worker returned `CYCLE_ADVANCE_NOT_NEEDED`; the stale certificate therefore
had no reset path and both statistical exit authority and complete global
auction coverage remained unavailable.

The single-family producer now receives the monitor's minimum acceptable
`computed_at`.  A posterior older than that cutoff can enqueue one same-cycle
canonical materialization while the source cycle remains legal.  A fresh
posterior, a visible seed, or the exact active queue request suppresses duplicate
work; an expired source cycle still fails closed.  Only the newly committed
posterior clears freshness—no stale row or marker is relabeled current.

SCOPE is one held `(city, target_date, metric)` family.  DRAIN is the existing
replacement materialization queue using the exact latest causal family cycle.
RESET is a canonical posterior on that cycle whose `computed_at` meets the
monitor cutoff; an active exact request only defers duplication, and an expired
source cycle requires normal cycle advance.  Acceptance requires same-cycle
expired/fresh antibodies, held reseed cutoff wiring, focused materialization and
monitor suites, live landing, and a subsequent complete current held-coverage
auction receipt.

## 2026-08-12 Monitor-shaped fill repair advances realized PnL

The partial-EXIT recovery path appended correct cumulative PnL atoms and folded
them into a non-NULL projection, but reused the `MONITOR_REFRESHED` projection
shape to preserve the still-open lifecycle.  The monitor authority merge then
unconditionally copied the older `position_current.realized_pnl_usd` value over
the new fold.  When that old value was NULL, canonical current-state PnL stayed
NULL even though the event ledger already proved the loss or gain.

The merge now distinguishes missing monitor economics from present fill-owned
economics: NULL input preserves the existing value, while a non-NULL cumulative
PnL from the append-first fold advances the projection.  No lifecycle, shares,
cost, or settlement semantics change.

SCOPE is `realized_pnl_usd` on one monitor-shaped canonical projection.  DRAIN
is the same append-and-project transaction.  RESET is equality between the
event fold and `position_current`; replaying the same fill remains idempotent.
Acceptance requires both projection directions (NULL preserves, non-NULL
advances) and the three partial-EXIT recovery antibodies covering production
shape, repeated recovery, and multiple trade identities.

## 2026-08-12 Held belief repairs keep independent RESET lanes

Madrid and Tel Aviv remained under active monitoring but could not form a fresh
exit belief after newer raw forecast inputs arrived.  The monitor correctly
requested both same-carrier input-revision repair and newer-carrier cycle
advance, but treated a durable `fusion_upgrade_enqueues` marker as proof that
all repair work was pending and returned before invoking cycle advance.  Those
markers survive seed consumption, so the independent cycle lane had no RESET
and repeated monitor cycles could remain in `BELIEF_AUTHORITY_FAULT` while a
newer materializable carrier existed.

Only a newly published input-revision seed short-circuits the current worker.
An already-enqueued revision now remains visible as pending while the same
family also evaluates cycle advance.  The later monitor still accepts only a
materialized causal posterior; no stale probability is relabeled fresh.

SCOPE is one held `(city, target_date, metric)` belief repair.  DRAIN is the
existing fusion queue and cycle-advance queue, independently.  RESET is a
posterior consuming the relevant input revision/carrier followed by a fresh
`MONITOR_REFRESHED`; a durable marker alone never resets freshness.  Acceptance
requires an antibody where `already_enqueued=1` still invokes cycle advance,
the existing new-revision/no-revision routing tests, and forward Madrid/Tel Aviv
fresh-belief evidence after live deployment.

## 2026-08-12 Pre-SDK terminal rejection closes its entry exposure obligation

An entry command can cross the durable reservation boundary and then fail before
the venue SDK is called.  Recovery already requires a typed adapter witness and
records `REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT`, terminalizing the command as
`REJECTED`.  The entry-obligation reconciler did not recognize that exact event,
so the command remained an open exposure obligation indefinitely despite the
same canonical journal proving that no venue side effect was possible.

The terminal no-fill proof set now includes the validated pre-SDK clearance
event.  Positive trade/execution facts or a nonterminal order fact still veto
release, and a generic rejection or unvalidated payload cannot mint this event.

SCOPE is one entry command carrying a typed pre-SDK no-side-effect terminal
event.  DRAIN is the recurring terminal entry-obligation recovery pass.  RESET
is the obligation's `RESOLVED` transition; commands without the typed event, or
with contradictory venue exposure, remain open.  Acceptance requires an
end-to-end antibody that creates the typed clearance through its validated
writer, releases the obligation exactly once, then proves idempotence, followed
by live recovery of the historical obligation without a DB edit.

## 2026-08-12 Held monitor reads the latest causal Day0 event

The held monitor freezes one cycle decision cut before its bounded probability
reads.  The Day0 event reader nevertheless selected the database's absolute
latest family event.  When the source materializer committed a new event after
that cut but before the reader ran, the probability builder correctly rejected
the event as future evidence.  The reader did not fall back to the immediately
preceding causal event, so a healthy source update produced
`INCOMPLETE_EXIT_CONTEXT`, an incomplete monitor cycle, and overdue monitor
debt that also retained the entry lane in reduce-only mode.

The reader now selects the latest family event whose available, received, and
created clocks are all at or before the frozen decision time.  This preserves
the coherent cycle cut and the no-look-ahead rule; it does not accept stale
evidence as current, and later execution still rebinds to submit-time truth.

SCOPE is one held city/date/metric probability read.  DRAIN is the same bounded
monitor pass using the newest event causally visible at its decision cut.  RESET
is a later monitor cut that makes a newly committed event eligible; an event
still newer than the cut remains excluded.  Acceptance requires an antibody
with both a causal event and a later committed event, the focused held-belief
suite, the monitor/exit suites, live hot-fix landing, and forward full-book
`MONITOR_REFRESHED` evidence without a future-event rejection loop.

## 2026-08-12 FAK no-fill retains an exact deadline-bound SELL handoff

The Seoul 32C NO timeline exposed a stronger acceptance requirement than wake
priority alone.  Its first current negative-edge SELL was submitted at 0.06 and
received a deterministic FAK no-fill, but the historical runtime produced no
second SELL command before the bid fell below the live order floor.  A selector
test proves only that an already-queued expired request wins scheduling; it does
not prove the rejected command leaves a new actionable request behind.

The composed antibody now begins with the authenticated FAK no-fill command,
requires canonical retry release, recovers a new exact V4 request bound to a
new current probability/book witness, proves the request remains incomplete,
and proves its expired actuation deadline preempts a competing Day0 wake.  This
closes the producer-to-selector proof gap without replaying the rejected
command's historical quote.  Existing global-cut tests continue to own JIT
candidate/receipt actuation and typed no-book completion.

SCOPE is one rejected exact held SELL attempt.  DRAIN is one current global
auction cut producing a new command or typed terminal current-book receipt.
RESET is that request's immutable matching receipt; absent receipt retains the
debt, while bid below the absolute floor is current no-book evidence rather
than a promised fill.  Acceptance is both holding/day0 runtime states passing
the composed antibody plus the deadline-selector and current-cut receipt tests.

## 2026-08-12 Deploy monitor gate and restart reset share one proof

Production loaded the intended SHA and began held-position monitoring, but the
deploy command accepted one coverage tranche and immediately asked the restart
guard to prove full-book coverage.  Production accepted four of twelve fresh
decisions as sufficient; four positions were still stale when the reset proof
ran, so the global entry pause remained selected even though the monitor
continued to drain normally.  This is a control-flow contradiction, not
evidence that the fresh-entry universe lacks alpha.

The post-start monitor gate now waits for the same full-book condition consumed
by the restart-guard reset: every canonical open position has a fresh
`MONITOR_REFRESHED` decision after this boot, with no future-dated event.  It
does not weaken input freshness, chain-risk, queue, or loaded-SHA checks.  The
existing eight-minute bound covers the documented three two-minute coverage
cycles plus launch jitter.

SCOPE is only this deploy invocation's global entry pause.  DRAIN is the
recurring held monitor covering every canonical open position.  RESET is zero
stale/missing and zero future monitor events against the launch floor; a newly
opened or stale position restores the wait.  Acceptance requires an antibody
that rejects partial tranche coverage, the existing complete/no-position/
future-event/chain-risk cases, and one live restart whose full-book proof and
canonical CAS reset both pass before entries resume.

## 2026-08-12 Held-capital liveness and RED action closure

Current live evidence disproves the assumption that fresh process heartbeats imply
fresh held-position decisions.  The daemon remained alive while every actionable
open position crossed the 240-second canonical `MONITOR_REFRESHED` deadline.  A
75-second full-book claim reserved only one five-second primary-belief tranche,
even though its existing degraded-coverage scheduler admits roughly one third of
the held book.  A 32-second replacement-HWM read followed by a failed 30-second
order-book batch therefore consumed the claim before any admitted belief tranche
could make canonical progress.  Recovery observed the durable debt every 30
seconds but could only contend for the same non-reentrant lane.

The monitor budget must reserve one bounded belief-read tranche for every
position already selected by the existing degraded-coverage law, capped at half
the claim so the coherent HWM prerequisite retains a finite half.  Auxiliary
debt and batch-book work may consume only the remainder.  This does not accept a
stale forecast or quote, weaken the canonical write, or convert an unavailable
book into HOLD authority; it guarantees that optional batch work cannot consume
the time explicitly reserved for causal q/book redecision.  The behavioral
antibody uses a 13-position pass and proves that five admitted primary tranches
retain 25 seconds after HWM/auxiliary work rather than sharing one five-second
reserve.

The live EDLI mesh also omitted the durable RED action law.  `RiskGuard=RED`
reached the allocator as reduce-only, but the only code that marked positions
`red_force_exit` remained in the unscheduled legacy `run_cycle`.  The scheduled
exit owner must therefore invoke the existing idempotent force-exit sweep before
normal monitoring, widen a targeted wake to the full held book under RED, and
persist its marker through the same artifact/portfolio commit.  It must not add
a second order runtime or bypass the existing submit-time RED and executable-book
checks.

Finally, a prior Seoul SELL proves that a durable global-reauction deadline can
expire for more than ten minutes without gaining reactor priority.  Expired,
unreceipted exact held-SELL debt must promote the existing durable wake ahead of
entry/forecast work while continuing to rebuild from current q/book; an old FAK
quote is never replay authority.

SCOPE is current positive held exposure, its single-writer monitor claim, current
RED state, and an exact expired held-SELL obligation.  DRAIN is bounded canonical
progress for every admitted monitor tranche, the existing RED marker-to-submit
path, and a terminal exact reauction receipt.  RESET is fresh per-position
`MONITOR_REFRESHED` evidence, risk below RED, or a matching terminal receipt;
none is reset by heartbeat, scheduler invocation, stale q, or a timestamp-only
wake.  Acceptance requires focused budget/RED/deadline-priority antibodies,
existing monitor/exit/risk suites, live hot-fix landing, exact loaded SHA, fresh
canonical monitor ages, RED action evidence, and no post-load negative-edge
executable position lacking bounded intent/command/receipt progression.

The Seoul probability reversal is a separate probability-law defect, not a
monitor-liveness patch.  The current peak-set atom is
`P(peak set | city, month, hour)` and can place about 99.3% mass on the observed
boundary while current slope/provider innovations affect only the remaining
0.7% branch.  Its replacement must be a causal two-stage posterior conditioned
on decision-time trajectory evidence and validated walk-forward over at most
seven days.  No market-price cap, post-outcome label, or uncalibrated constant is
authorized by this hot-fix slice.

## 2026-08-12 Uncalibrated unresolved Day0 q cannot authorize new capital

Forward live evidence reproduced the same probability-law defect in two new
families. NYC 84--85F YES was bought while the remaining-day q was about 0.93,
then the monotone running high reached 86F and made the bin structurally dead.
Dallas 100--101F NO was bought with held-side q about 0.89 while the current
fast observation was already near the boundary and the market subsequently
removed every executable NO bid. These outcomes do not by themselves estimate
model accuracy, but together with the already-identified marginal peak-set atom
they disprove authority for spending new capital on that unvalidated q.

The immediate containment removes only BUY proposals whose prepared current
Day0 payoff truth is `UNRESOLVED` before global capital ranking. The rule binds
the prepared truth rather than its carrier event label, so a forecast/redecision
carrier cannot reauthorize the same Day0 q. Deterministic `LOCKED`/`REFUTED`
Day0 facts, every held SELL/HOLD/CASH proposal, and BUY proposals without Day0
truth keep their existing current-truth and common-axis comparison paths. This
is not a replacement probability model and does not claim calibration success.

SCOPE is one risk-increasing BUY side whose current Day0 payoff truth is
`UNRESOLVED`. DRAIN is the separately planned causal two-stage peak posterior,
conditioned on decision-time trajectory evidence and validated strictly
walk-forward over at most seven days. RESET is either a current monotone fact
that makes the exact side `LOCKED`/`REFUTED`, or a promoted probability-semantics
revision with validation evidence that removes this containment. No market
price, stale posterior, or eventual outcome resets it.

Acceptance requires focused antibodies proving unresolved Day0 is rejected
before strategy/capital checks while hard facts and non-Day0 candidates remain
eligible; compilation and planning-lock checks; hot-fix landing and exact loaded
SHA; then current global-auction receipts showing the typed rejection and no
post-load unresolved Day0 ENTRY command. The broader goal remains active until
forward chain fills, settlements, and capital-curve evidence establish robust
capital gain rather than a single favorable trade.

## 2026-08-12 Exact held-SELL completion owns an exact quote scope

The durable completion request already names the only `(position_id, token_id)`
whose SELL may be selected, but the book epoch still projected every open held
token before applying that policy.  A one-position recovery therefore paid the
Gamma binding and CLOB I/O latency of unrelated positions even though none could
win that auction.  This is a scope mismatch, not an argument to compare capital
locally: the global posterior/wealth comparison remains intact while quote I/O
is limited to actions that the exact completion policy can authorize.

SCOPE is quote, binding, and executable-book capture for an exact reserved
held-SELL completion request.  DRAIN is one current quote epoch for the exact
open `(position_id, held_token_id)` pair followed by its existing terminal
receipt path.  RESET is that terminal receipt or disappearance of the exact
positive chain exposure.  A missing/mismatched pair produces an empty quote
scope and fails closed; it never widens to all held positions.  Non-exact
reduce-only auctions retain full held-token coverage.  Acceptance requires an
antibody with two open families proving the exact request fetches only its named
held token, plus the existing global-auction and deadline suites.

## 2026-08-12 Held Day0 bundles own the cut before the freshness cliff

The strict Day0 consumer correctly rejects bundles older than three hours, but
the producer mixed one discovery city into a three-city trading-lane cut even
when held families were already inside the one-hour refresh headroom.  Under a
six-second budget, one slow request could therefore leave only one held city on
critical quota while the fairness cursor advanced over two offered held slots.
Transport and quota failures exposed this contract gap; they did not create it.

SCOPE is only a bounded Day0 producer cut while at least one current held family
is refresh-due under the existing strict-bundle headroom proof.  DRAIN gives all
offered held cities critical quota and rotates the existing held cursor across
successive cuts; discovery resumes under the existing mixed-slot policy once no
held family is refresh-due.  RESET is a complete fresh held bundle read back by
the current probe, not a request attempt or cursor timestamp.  Acceptance uses
more held cities than the microbatch plus discovery debt, forces a one-city
budget exhaustion, and proves consecutive cuts contain only held cities with
full critical quota while cursor fairness still advances.

## 2026-08-12 Shared held-book I/O follows monitor admission

Recent forward evidence showed that only 45 of 159 monitor refreshes had both a
fresh q and fresh book while a shared order-book batch exceeded its child-read
budget.  The batch was derived from every locally missing held token before the
cycle selected its bounded coverage, urgency, and oldest-debt lanes.  One bulk
transport failure could therefore consume the auxiliary deadline and defer the
unrelated tail that the batch had no authority to prioritize.

SCOPE is optional shared network prefetch for the positions already admitted by
this cycle's coverage and urgent lanes plus its one active-network progress
reservation.  The oldest durable debt keeps its earlier bounded singular read;
unselected positions keep their normal per-position bounded refresh and are not
members of the shared failure domain.  DRAIN is a successful scoped batch or
each position's own finite quote attempt followed by canonical
`MONITOR_REFRESHED`.  RESET is a fresh canonical q/book event, never batch start
or scheduler success.  Acceptance proves a large held book produces a strict
batch subset equal to the admitted position IDs, excludes the singular oldest
debt, and retains the existing local-first, deadline, and fail-closed tests.

## 2026-08-12 Fresh target-specific ENS shape is required for entry

The current forecast database contained 190 latest family certificates: 176
used a same-cycle `ensemble_center_scenarios_v4` shape, while 14 reused an ENS
shape lagged by 6--30 hours.  The stale rows truthfully retained absolute
ENS/provider-center disagreement and remained readable, but the shared
tradeable-coverage predicate incorrectly granted them the same new-entry
authority as a target-specific same-cycle shape.  Six recently settled
qkernel entries were all admitted under that stale-shape probability identity;
their common structural defect was the missing current shape, not their city,
bin, side, or eventual outcome.

The repair separates evidence availability from action authority.  A stale
shape remains materialized and readable through the independent held-position
belief path for monitoring, uncertainty, and repair scheduling.  It no longer
satisfies live entry/FSR coverage or the new-entry bundle reader.  Entry-grade
authority now requires numeric `shape_lag_hours == 0`,
no stale reuse, no translation, the current semantic revision, and the existing
certified bootstrap bounds.  Missing lag provenance fails closed.

SCOPE is only new-entry coverage for the affected family certificate; the 176
fresh families remain eligible and held-position reads are unchanged.  DRAIN
is the existing replacement fetch/materialization cycle, which replaces a
stale certificate when the current target-specific ENS shape arrives.  RESET
is exact same-cycle lag-zero shape provenance under the current semantics;
another stale/missing/translated shape remains non-entry-grade.  Acceptance
requires executable SQL antibodies for fresh, stale, and missing-lag rows,
the full cycle-policy suite, live landing and loaded-SHA proof, current coverage
counts, and forward auction receipts showing fresh families still compete.

## 2026-08-11 Confirmed taker SELL retains exact quote proceeds

A forward Tokyo 28C NO exit sold 12.99 shares after current belief reversed.
The synchronous authenticated submit receipt proved 12.0094 USDC of proceeds
(weighted fill 0.9245111624), while the later CONFIRMED account trade exposed a
tick-rounded top-line price of 0.92 plus the complete maker-leg decomposition.
The fill synchronizer correctly preferred CONFIRMED finality but the capital
projection then used the rounded top line, understating realized PnL by 0.0586
USDC.  The position had already reduced to 0.000168 non-executable dust, so an
append-only correction must revise notional without selling shares twice.

The repair reconstructs taker-SELL proceeds only when every maker leg has a
valid selected-token BUY or complementary-token SELL shape, all legs sum to the
root filled size, and the command/envelope/token identities agree.  It appends
a corrected CONFIRMED trade fact and, when the same stable partial-fill identity
was already booked, a zero-share signed-notional correction atom.  The fold
accepts that atom only after a prior identity and requires exact cumulative
quantity/notional deltas; exposure and allocated basis remain unchanged.

The targeted CTF read can advance residual shares ahead of the Data API's
position economics.  Reconciliation therefore rejects a mixed-clock reported
cost only when it is impossible for a binary token (more than one collateral
unit per current share), deriving residual cost from the same observation's
current shares and valid average price.  This repairs the chain-cost side of
the partial-exit convergence gate without changing fill-owned acquisition cost.

An existing full reconcile path also replayed cumulative ENTRY facts after the
capital-reduction event and could resurrect the sold quantity.  ENTRY facts
remain immutable acquisition provenance, but once a later canonical
`CAPITAL_REDUCTION_FILLED` or `EXIT_ORDER_FILLED` exists they no longer have
current-exposure authority; reobservation must preserve the reduced projection.
The restart recovery projection pass applies the same rule and classifies that
reobservation as a stable no-op rather than a repair error.

SCOPE is one authenticated EXIT/SELL command and one stable economic fill
identity.  DRAIN is the normal recorded-fill economics recovery pass after a
complete CONFIRMED trade arrives; an already-booked partial fill drains through
one append-only correction event after local and chain residuals agree.  RESET
is exact equality between the canonical maker-leg notional and the persisted
cursor; replay then appends nothing.  Missing token pairs, incomplete or mixed
maker legs, quantity disagreement, absent prior identity, or divergent residual
basis remain fail-closed.  Acceptance requires positive and already-booked
partial correction antibodies, exchange/exit/fill-sync/recovery suites, live
deployment, authenticated order/trade proof, exact CTF dust balance, corrected
canonical PnL, one-time stale-full-lot chain-cost repair, and preservation of
the reduced exposure under later ENTRY reobservation, and preservation of the
global entry pause.

## 2026-08-11 Zero-price balance snapshots retain authenticated fill cost

A forward partial fill exposed a transient canonical tear: the chain mirror
observed a positive wallet balance before command recovery had projected the
complete trade fill, and that balance surface carried zero average price and
zero cost.  The mirror correctly limited attribution to the then-known owned
shares, but marked the row `synced` without any chain cost.  The strict runtime
exposure reader then rejected the whole global auction as torn economics,
excluding held SELL/HOLD/CASH comparison as well as BUY.

The writer now preserves the authenticated venue-trade unit cost for exactly
the chain-confirmed owned slice when, and only when, the balance snapshot has
no positive economics and `fill_authority` is trade-verified.  Chain truth
continues to own quantity; the chronicled venue fill owns cost.  Balance-only
and fill-unproven positions receive no synthesized economics, wallet excess is
not adopted, and a positive venue-position price retains priority.  The event
records the selected economics basis.

SCOPE is one `CHAIN_SIZE_CORRECTED` projection for a trade-verified open
position with positive attributed chain shares and a zero-price balance
snapshot.  DRAIN is the same append-first mirror write and the next normal
global-auction read.  RESET is a later positive venue-position observation or
new authenticated fill projection; missing authenticated cost remains
fail-closed.  Acceptance requires the zero-price partial-fill antibody, the
complete chain-mirror suite, related chain/exchange invariant suites, hot-fix
landing, loaded-SHA proof, and a forward auction receipt no longer rejected for
torn chain economics.  This restores decision liveness; realized capital gain
still requires later fill, exit/settlement, and capital-curve evidence.

## 2026-08-11 Entry JIT CLOB identity uses the submit lane

After typed maker direction reached executor authority, forward live attempts
were still reauctioned under `GLOBAL_JIT_CLOB_MARKET_UNAVAILABLE`.  An exact
current probe showed the endpoint and market were healthy: the default client
was denied locally as `POLYMARKET_SCAN_LEASE_BUSY:...scan_in_flight`, while the
same `/markets/{condition_id}` request with `RequestPriority.SUBMIT_JIT`
returned the current two-token market immediately.  The JIT path already used
that priority for Gamma and for the full book cut, but omitted it on the final
CLOB market-identity client and therefore self-contended with its own scan.

The repair explicitly gives that one current CLOB identity request the existing
FC-03 submit priority.  It does not bypass the governor, reuse a cached market,
change request budgets, or admit a missing/invalid response.

SCOPE is one selected entry candidate's submit-time CLOB market identity read.
DRAIN is the existing bounded request and full-market reauction.  RESET is a
fresh response whose condition, token ownership, lifecycle, tick, minimum size,
neg-risk, fee schedule, and raw book all agree; any transport or semantic
failure remains fail-closed.  Acceptance requires a call-site priority
antibody, focused JIT/global-auction tests, diff/compile checks, hot-fix landing,
loaded-SHA proof, and a forward live submit outcome.  Venue fill and later
realized PnL remain separate proof lines.

## 2026-08-11 Typed direction must retain global maker authority

Forward live submission reached a verified global `MAKER_REST` winner with
positive posterior-mean expected log growth and positive conservative submit
edge, but the executor rejected it as
`min_expected_profit_below_live_floor`.  The durable actionable certificate
and its qkernel economics both pass the global current-state verifier when the
direction is the canonical `buy_no`.  The executor instead passed
`str(Direction.NO)` (`Direction.NO`) into the maker-witness validator, which
cannot bind that text to the sealed `NO` action and therefore incorrectly
downgrades the proposal into the legacy fixed-floor lane.

The repair normalizes the typed `ExecutionIntent.direction` once through its
enum value and uses that canonical value for the global verifier, entry-price
policy, and side binding.  It does not change q, q_lcb, fill probability,
expected-log/EV ranking, sizing, price, book, risk, or submit-time freshness.

SCOPE is executor validation of one already-verified global entry intent.
DRAIN is the next submit attempt compiled from a fresh global cut.  RESET is a
canonical `buy_yes` or `buy_no` value whose side and current maker witness both
verify; malformed or mismatched directions remain fail-closed.  Acceptance
requires a typed-direction regression antibody, the focused execution suite,
diff/compile checks, standard hot-fix landing, loaded-SHA proof, and a forward
live receipt that either reaches command/venue submission or names the next
independent current blocker.  A fill alone is not capital-gain proof; later
exit/settlement and forward PnL must still be observed.

## 2026-08-11 Maker JIT price drift must reauction current market truth

Forward live receipts now reach globally selected, positive posterior-mean
`MAKER_REST` BUY candidates, but a changed passive limit between selection and
JIT is emitted as
`GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED:...current_limit_or_cashflow_changed`
and falls through the generic classifier to `BATCH_BLOCKED`.  That stops the
complete cut even though the failure names current market-authority drift and
the existing global runtime already has a bounded full-market reauction lane.

The repair classifies only this exact typed drift as
`MARKET_AUTHORITY_SUPERSEDED`.  It never reuses the selected price or maker
witness and never constructs a local replacement candidate: the batch discards
the old cut, refreshes current Gamma/CLOB/raw books, rebuilds the point-in-time
maker witness, and compares BUY/SELL/HOLD/CASH again.  All other maker-witness
failures remain fail-closed because they may be proof corruption rather than a
new executable market fact.

SCOPE is one selected maker BUY whose JIT passive limit or cashflow differs
from its sealed selection witness.  DRAIN is the existing single bounded
full-market-authority reauction.  RESET is a complete new q/book/wealth cut with
an exact current maker witness; a second drift remains fail-closed under the
existing retry limit.  Acceptance requires an exact classifier antibody, the
existing changed-limit rejection antibody, market-supersession batch tests,
focused money-path regression, loaded-SHA proof, and a forward live receipt
that either persists/submits the reselected command or names the next exact
current blocker.  It is not capital-gain proof until venue fill plus later
exit/settlement and forward PnL evidence exist.

## 2026-08-11 Point-in-time maker-fill producer

The global auction currently materializes zero `MAKER_REST` candidates because
production never populates the typed maker-witness map.  Reusing the legacy
all-band `0.19` scalar would fabricate current executable truth and remains
forbidden.  The canonical trade DB now contains enough action-specific facts to
construct a stricter producer for ENTRY BUY, but not yet for EXIT SELL.

At the current decision cut, the trailing 30-day canonical cohort contains 488
terminal, venue-acknowledged ENTRY BUY orders using the exact current grammar:
post-only GTC at `snapshot best_bid + one tick`, strictly below the captured ask.
Their append-only order facts provide the actual matched fraction observed no
later than the 20-minute rest deadline.  Early cancellation is retained as its
real zero/partial outcome rather than censored away: 114/488 had any fill, 61
were partial, 53 were full, and mean filled fraction was 0.173587.  A two-sided
99% Dvoretzky-Kiefer-Wolfowitz radius moves finite-sample uncertainty into the
no-fill atom before scoring, so the raw 23.36% any-fill frequency is not used as
authority.  This is a causal lower-bound zero/partial/full distribution, not a
visible-depth proxy or a fixed fill scalar.  The corresponding EXIT SELL cohort
has only 20 terminal same-grammar observations, so it does not clear the
30-sample action-specific minimum and cannot borrow BUY evidence.

The implementation reads only rows whose command update and order facts are at
or before the immutable selection cut, hashes the exact sample rows, and binds
the resulting distribution to each current candidate, proposal limit, book
snapshot/hash, global book epoch, ledger generation, rest deadline, issue time,
and book expiry.  Each action requires at least 30 eligible rows and a strictly
positive 99% DKW fill-probability lower bound.  Missing tables, malformed facts,
thin action-specific samples, a non-positive lower bound, a one-tick grammar
mismatch, or an expired current book produce no witness and exclude only that
maker sibling.  Taker, HOLD/CASH, and the other action remain available.

SCOPE is one maker sibling in one current q/book/wealth cut.  DRAIN is the next
normal complete auction, which re-reads only facts available by its frozen cut
and rebinds every witness to the new current book.  RESET is an action-specific
sample cohort meeting the declared minimum plus a fresh coherent book; no
operator flag or historical constant can reset it.  Acceptance requires
point-in-time/no-look-ahead and exact-grammar antibodies, a thin-SELL rejection
antibody, both TAKER_LIMIT and witnessed MAKER_REST BUY candidates in one full
comparison, selected-mode preservation through JIT, focused money-path tests,
and a live receipt proving the dual-mode candidate set.  This slice improves
entry price/capital efficiency; it is not realized capital-gain proof.

Pre-deploy verification: the current canonical read produces BUY `n=488`, raw
any-fill `0.233607`, DKW99 lower bound `0.159927`, raw expected filled fraction
`0.173587`, and witnessed expected filled fraction `0.118838`; SELL remains
unavailable.  The new point-in-time/action-specific/dual-mode antibodies pass
`3/3`, solver properties pass `209/209`, and reactor plus multiwinner tests pass
`350/350` under the live Python environment.  The complete global integration
file is `452 passed / 5 failed`; all five failing node IDs reproduce unchanged
on the current live checkout and are the existing precliff/price-band fixture
drift, so they are not represented as green.  Worktree code against live DBs
passes the read-only boot validation `ALL PASS`.

## 2026-08-11 Final maker command compiler closure

Post-deploy receipts proved that the complete auction and JIT preflight now
preserved the typed maker witness, but the final command compiler still applied
the retired unconditional maker wall.  A `STABLE` witnessed winner therefore
ended as `EDLI_LIVE_CERTIFICATE_BUILD_FAILED:CURRENT_MAKER_FILL_WITNESS_UNAVAILABLE`
before command persistence even though its rebound qkernel certificate carried
the exact zero/partial/full distribution.

The final wall now admits only the same exact `MAKER_REST` candidate carried by
the JIT handoff when its typed witness is current at final compilation and its
serialized witness, candidate/condition/token, passive limit, execution-curve
identity, fill probability/source, rest deadline, and recomputed certificate
identity all match the rebound qkernel economics.  Missing JIT handoff, any
maker/taker mode disagreement, an expired witness, or even a resealed outcome
mutation retains the existing fail-closed rejection.  Local/unwitnessed makers
remain unavailable.

SCOPE is one globally selected witnessed maker BUY after stable preflight.
DRAIN is the same one-shot final compiler and durable command outbox.  RESET is
an exact current JIT witness matching the already-validated qkernel certificate;
no flag, scalar prior, or stale selection can reset it.  Acceptance requires
the exact-witness pass plus resealed-outcome and expiry rejection antibodies,
the final mode-authority/qkernel/JIT suites, hot-fix landing, loaded-SHA proof,
and a post-deploy command/venue fact or an exact new current rejection.  It is
not capital-gain proof until fill, exit/settlement, and forward PnL evidence
exist.

## 2026-08-10 Current maker witness survives global-to-JIT handoff

Production repeatedly selected one positive posterior-mean global BUY while the
winner preflight rejected all 21 reconstructed family proofs as
`CURRENT_MAKER_FILL_WITNESS_UNAVAILABLE`.  The global auction can select
`MAKER_REST` only with a typed, current `CurrentMakerFillWitness`, but the JIT
family proof builder intentionally has no local maker authority and the existing
selected-proof rebind covers only `TAKER_LIMIT`.  The same valid global maker
authority is therefore lost between selection and preflight.

The repair binds only the exact selected BUY proof when the sealed candidate is
`MAKER_REST` and the solver's complete maker-witness validator succeeds at the
JIT decision time.  It copies the witnessed limit, fill probability, source,
and deadline into that proof; absent, mismatched, or expired witnesses remain
blocked.  Taker, SELL, siblings, and unwitnessed maker behavior are unchanged.
The immutable qkernel certificate and final JIT receipt must carry that complete
candidate-bound zero/partial/full distribution, not only its scalar fill
probability.  The final validator recomputes the witness identity, book and
candidate bindings, temporal window, cashflows, posterior-mean expected
log-growth, EV, capital efficiency, and lock-time rate.  A refreshed JIT book
may rebind the witness only when the passive limit is unchanged; any price or
cashflow drift forces a complete reauction rather than silently changing the
selected action.
Acceptance requires an antibody proving both the valid handoff and fail-closed
invalid-witness twin, resealed book/outcome-tamper rejection, same-limit JIT
rebind, changed-limit reauction, the focused integration slice, planning lock,
and a live receipt showing the old all-candidate maker-witness rejection has
disappeared without any maker submit lacking typed authority.

## 2026-08-10 Deploy restart guard reset without entry-queue circularity

The invocation-scoped deploy guard pauses new entries while the replacement
runtime proves its loaded SHA and refreshes every canonical open position.  A
claimable entry queue is therefore expected during the guard: requiring one of
those rows to make post-issue processing progress before reset makes DRAIN
depend on the action the guard itself forbids.  Production reproduced that
ratchet with loaded-SHA and 7/7 monitor evidence green, no stale processing,
and 196 claimable entry rows, while the reactor correctly parked the paused
entry wake.

SCOPE remains this deploy invocation's global entry pause. DRAIN is loaded-SHA
identity, complete fresh monitor evidence, and absence of stale in-flight
reactor ownership; claimable unowned entry work is telemetry, not debt that may
hold the guard. RESET is the existing witness-bound CAS expiry. Operator and
newer pause overrides retain precedence.  No command vocabulary, durable
storage shape, risk semantics, monitor/exit behavior, or manual resume path is
changed.

## 2026-08-10 Held-monitor deadline begins at ownership claim

Post-deploy verification reopened this slice: the first deadline propagation
still left DB acquisition, canonical portfolio materialization, and allocator
preparation outside the absolute claim clock, while non-production CLOB
adapters could bypass the hard-deadline book API.  The same absolute deadline
now bounds connection busy wait, preparation SQL progress, portfolio row
materialization, and every adapter's held-book read.  A separate canonical
audit also proved that an unarmed V4 residual placeholder copied into an exact
market-closed hold was being misclassified as immediately due reauction debt;
that state now drains only through settlement/reconciliation unless a later
executable monitor event or armed request resets it.

The claim clock is one end-to-end budget across cutover-lease acquisition,
connection PRAGMAs, ATTACH, preparation scans, and per-position retry-release
iteration. No blocking layer may reinterpret it as a fresh local timeout.

Production evidence showed a held-monitor pass retaining its single-owner claim
well past the nominal 75-second budget while command recovery performed
unbounded decision-artifact reads.  Recovery/review now admits decision-log
evidence only through the timestamp index in the command's causal window.  The
held-monitor budget begins when ownership is claimed, not after portfolio and
allocator preparation; its remaining budget reaches pending-exit preflight and
the monitor phase.  Global SELL reauction debt never starts a fresh probability
or book refresh after that deadline, and a deadline-bound book miss cannot fall
back to an unbounded quote request.  Expiry preserves the durable debt and the
original invocation's ownership until safe return; it never releases the claim
while old work can still submit.

SCOPE is one admitted held-monitor invocation and each position-scoped global
SELL debt. DRAIN is the next normal bounded monitor/recovery pass. RESET is a
fresh indexed causal decision witness or a completed current q/book reauction
within the same absolute deadline. Missing proof remains position-scoped and
fail-closed; unrelated monitoring and entries do not inherit the debt.

## 2026-08-10 Executable limit modes, fill convergence, and command ownership

The current global auction ranks each independently executable maker or taker
proposal, but the last persistence and venue boundaries still carried the
retired blanket assumption that every ENTRY must be post-only.  That split made
a globally selected, certificate-bound FOK/FAK BUY impossible to execute even
when its finite limit, current book, fees, depth, Kelly target, wealth, and fill
prefix were all proved.  The executable law remains limit-order only: every
submitted unit price is finite and inside inclusive `[0.05, 0.95]`; Zeus never
submits an unpriced market order.  The admitted shapes are now explicit and
side-aware: ENTRY/BUY may be GTC/GTD post-only maker or FOK/FAK non-post-only
certified taker; EXIT/SELL may be GTC/GTD post-only maker or FAK non-post-only
taker.  Non-post-only GTC/GTD, post-only immediate-or-cancel shapes, and SELL
FOK remain rejected.  Selection mode survives JIT rebinding and the executor
still re-proves exact actionable probability, book, fee, depth, position,
wealth, and envelope identity immediately before SDK contact.

The same exact-head audit exposed two convergence gaps.  First, an authenticated
late trade leg was appended but could be skipped by projection when a previous
partial-fill transition already existed.  Reconciliation now projects the
canonical command-level aggregate, replaces only that command's prior ENTRY
economics, and applies the symmetric EXIT aggregate only against an exact exit
intent.  Independent legs therefore converge once to one valid aggregate
without double counting or inventing economic closure.  Second, heartbeat
control imported SDK transport internals directly.  The venue adapter now owns
the dedicated HTTP client swap and request-error cause preservation; control
owns only timeout policy, delegation, and telemetry.

The pre-SDK admission audit also found that constructing a connection-backed
`CollateralLedger` after `BEGIN IMMEDIATE` ran schema `executescript()` and
implicitly committed the command/envelope/events before the reservation CAS.
A later collateral rejection could therefore leave a durable `SUBMITTING`
command despite the claimed rollback.  ENTRY now uses the DDL-free
`buy_preflight_in_transaction()` read path under the existing writer lock; a
pre-side-effect failure rolls back command, event, envelope, and reservation as
one unit.  The antibody traces SQL inside the open transaction and rejects any
DDL or COMMIT.

NC-18 additionally found three direct command-journal mutations outside the
declared `venue_command_repo` writer: recovery of an already-existing EXIT
order, absorption of an operator-confirmed external close, and mixed-token
ENTRY command rehoming.  These are observation/repair facts, not new Zeus
submits.  They move behind three narrow typed repo helpers.  Recovered order
adoption and external-close absorption use explicit creation-only command
events rather than fabricated `SUBMIT_*` history; mixed-token rehome performs
command and execution-fact compare-and-swap in one nested savepoint.  No schema
or migration is introduced, normal command transitions remain closed, and the
AST ownership gate is not weakened.

Recovered partial EXIT adoption also exposed a liveness/accounting defect.
Recovery reduced the local holding to the first observed exchange residual but
did not append per-leg partial-exit economics.  Later authenticated fills lacked
an exact full-close intent, so reconcile and pending-exit monitoring correctly
refused to invent closure but left the position, realized PnL, and capital
release stranded.  The repair keeps one immutable pre-recovery holding/cost
baseline, folds each authenticated `(command_id, trade_id)` exactly once through
the existing partial-exit economic cursor, and projects only delta
shares/cost/PnL.  It never emits `EXIT_ORDER_FILLED` without exact full intent.
A sub-minimum residual remains its true size/cost and opens an idempotent typed
`ReviewWorkItem`. Dust is decided only against the exact held token's latest
current-time fresh, non-invalidated executable snapshot; missing authority keeps
the position pending under `MISSING_FILL_AUTHORITY`. A later lower minimum
resolves the debt only after canonical chain observation and an exact
`EXIT_RETRY_RELEASED` event actually return the residual to redecision. That
chain observation must be at least as new as both the terminal order fact and
the command update; an older local residual cannot release capital.

SCOPE is respectively one selected command envelope, one command's authenticated
trade aggregate, the heartbeat transport instance, one recovered venue order,
one deterministic external-close command, or one mixed-token source/target
command pair.  Recovered partial-exit economics are scoped to one
`command_id + position_id`; they never create a family/global entry latch.
DRAIN is the next normal JIT auction, authenticated trade-leg reconciliation/recovery
sweep, heartbeat installation attempt, or exact repair retry.  RESET is a fresh
coherent submit witness, a newly observed canonical aggregate, a successfully
delegated adapter transport, an atomic adoption/absorption commit, or a complete
two-row rehome CAS.  A per-leg cursor makes replay a no-op; a tradable residual
returns to ordinary redecision, while dust resolves on later executable truth
or settlement.  A failed proof leaves only that command/order/repair
unresolved; it does not block unrelated families, held monitoring, or CASH/HOLD.

Acceptance requires mirrored maker/taker envelope and adapter antibodies,
zero-command/zero-network rejection on any missing submit proof, full
entry/exit late-leg convergence without double projection, SDK-import
confinement, creation-event grammar and idempotency tests, transaction rollback
for every partial repair failure, the NC-18 direct-mutation scan, the complete
affected test files, semantic money-path classification, and exact-head CI.
Allowed implementation/evidence surfaces are
`src/contracts/venue_submission_envelope.py`, `src/execution/executor.py`,
`src/venue/polymarket_v2_adapter.py`, `src/control/heartbeat_supervisor.py`,
`src/execution/exchange_reconcile.py`, `src/execution/command_recovery.py`,
`src/execution/command_bus.py`, `src/state/venue_command_repo.py`, their scoped
router/reference/registry entries, `tests/test_unknown_side_effect.py`,
`tests/test_v2_adapter.py`, `tests/test_heartbeat_supervisor.py`,
`tests/test_executor_command_split.py`, `tests/test_exchange_reconcile.py`,
`tests/test_command_recovery.py`, `tests/test_venue_command_repo.py`,
`tests/test_command_bus_types.py`, `tests/test_command_grammar_amendment.py`,
`src/state/collateral_ledger.py`, `tests/test_collateral_ledger.py`,
`architecture/negative_constraints.yaml`, `architecture/invariants.yaml`, and
their Semgrep/forbidden-pattern companions.  Recovered partial-exit convergence
also requires `src/contracts/review_work_item.py`,
`src/state/review_work_items.py`, the existing partial-economics/cursor seams,
and focused exchange-reconcile/command-recovery antibodies proving per-leg PnL,
replay idempotency, dust review, and no fabricated full close.

## 2026-08-10 Exact-head temporal truth and required-CI closure

The global-capital-auction exact head exposed two live-base defects while its
required relationship jobs exercised the surrounding contracts.  First, the
Day0 observation-print reducer replaced a source-issued report clock with an
older event's local availability clock.  A corrected value for the same raw
report therefore appeared to be a later physical observation instead of a
later possession of the same observation.  The reducer now keeps
`observation_time` on the source clock and `observation_available_at` on the
current ledger fetch clock; correction identity remains
`(channel, source_clock, conditioned value)`, so one correction emits once
without inventing a new
weather fact.  Second, terminal EventStore recovery counted SQLite trigger
side effects through `total_changes`; it now reports only the direct archive
UPDATE row count while retaining the append-only event and active-projection
trigger law.

The remaining required-job failures were stale or platform-bound test
fixtures, not alternate runtime behavior.  EventStore fixtures create legal
append-only parents (or explicitly enter the legacy migration shape), the
reactor preemption test owns its monitor-debt authority instead of consulting a
host DB, the market-snapshot fake returns the current capture result shape,
Day0 live-order fixtures carry the current typed remaining-window probability
authority, and EDLI subprocess/bridge/source-shape tests use the running
interpreter and current converged identities.  No runtime guard, source route,
provider, settlement rule, execution gate, or workflow is weakened.

The exact-head audit also found a read-side certificate vocabulary split left
behind by the single-live-semantics cutover: the compiler persists
`PreSubmitDecisionCertificate` under `pre_submit:` semantic keys, while the
no-submit projection and opportunity report still queried the retired
`NoSubmitDecisionCertificate` / `no_submit:` pair.  Those derived readers now
join the current certificate type and key, so a verified decision is visible
to readiness/reporting instead of being falsely reported absent.

SCOPE is one Day0 source report/correction, one terminal-recovery batch, and
one receipt-to-pre-submit-certificate derived join.  DRAIN is the next normal
observation-print scan, recovery sweep, or report/projection read.  RESET is a
fresh ledger possession clock, the next independently counted direct UPDATE,
or a verified current `pre_submit:` certificate; there is no latch and
unrelated families/events continue.  Acceptance requires
the same-clock correction to retain the original source time and current fetch
time, emit exactly once, EventStore to return one archive for one processing
row despite projection triggers, append-only orphan guards to remain active,
all initially visible and semantic-classifier-selected required-job cases plus
the full Reactor relationship suite to pass, and the exact PR head's required
jobs to become green.

Allowed files are
`src/data/replacement_forecast_current_target_plan.py`,
`src/events/event_store.py`,
`src/events/no_submit_projection.py`,
`src/analysis/event_opportunity_report.py`,
`tests/events/test_day0_extreme_updated_trigger.py`,
`tests/test_replacement_forecast_current_target_plan.py`,
`tests/events/test_event_store_idempotency.py`, `tests/events/test_reactor.py`,
`tests/events/test_live_order_aggregate.py`,
`tests/test_market_scanner_provenance.py`,
`tests/money_path/test_edli_bankroll_warm_cycle.py`,
`tests/money_path/test_edli_durable_fill_bridge_scan.py`,
`tests/money_path/test_edli_market_substrate_warm_cycle.py`,
`architecture/test_topology.yaml`, this plan, and its `scope.yaml` companion.

## 2026-08-09 Current-value serving authority matches executable law

The active replacement authority still describes `previous_runs` as a
GEM-only, exact-cycle exception.  The executable single-builder has since
generalized that rule: carrier-bound reads prefer the selected-cycle
`single_runs` row, then the selected-cycle `previous_runs` row, then the newest
eligible prior-cycle row; source-clock live reads instead use each provider's
newest same-product row possessed by decision time while ENS retains only the
shape carrier clock.  Future rows, over-age captures, and the product-mismatched
ECMWF `ifs025` history remain inadmissible, and every substitution is branded by
`served_via` and `served_cycle`.

This slice changes no selector behavior.  It aligns the active authority doc
and the serving module's stale exact-cycle commentary with the already-tested
single-builder, and registers that existing high-risk choke point in the source
rationale/module manifest.  Acceptance is the existing generalized
substitution, future-row, product-mismatch, provenance, and source-clock test
suite passing without a runtime diff.

## 2026-08-09 Current-cut global capital auction and strategy ownership

The global selector previously collapsed venue affordability and Zeus utility
ownership into one pUSD balance. That let co-tenant wallet cash change Zeus
Kelly sizing and BUY/SELL ordering even when the operator's Zeus allocation was
unchanged. It also left the new zero-wealth SELL case outside ordinary log
utility and treated a fixed maker-fill prior as if it were current executable
truth.

One frozen allocation witness now names the distinct quantities used by every
decision cut:

- `C`: current venue-spendable cash;
- `E`: Zeus-owned utility equity from `zeus_capital_allocation`;
- `L`: effective BUY commitment ceiling, defaulting and capped to `E`;
- `K`: active-position cost basis plus unresolved entry commitments;
- `U=max(E-K,0)`: Zeus-owned liquid utility cash.

Executable BUY capacity is `min(C,max(L-K,0))`. BUY and reduce-only SELL use the
same portfolio endowment `U + H[a]`, with `H[a]` the exact current/pending
same-family payoff in settlement atom `a`; venue cash is an affordability fact,
never hidden utility wealth. `wallet_total` preserves historical parity when
the complete owned basis is `C+K`. Allocation policy, all five values, remaining
capacity, and their versioned identity are bound into the immutable wealth
witness, its economic identity, the selection receipt, JIT certificate, and
executor rebuild.

CASH/HOLD remain the zero-action baseline. Every independently admitted BUY or
SELL fixed proposal competes on one posterior-predictive-mean expected-log
capital-growth basis after its own fees, depth, tick, price-band, Kelly, and
direction law. Capital lock time determines the finite growth rate. A
reduce-only SELL that improves an exact positive-probability zero-wealth atom
uses the epsilon-free extended-log limit: raw ruin-probability reduction is the
first lexicographic key, without rounding, tolerance collapse, or division by
time; finite expected log growth per hour is considered only when ruin reduction
is exactly equal. Negative or non-finite terminal wealth remains invalid, BUY
may not introduce a zero atom, and positive EV remains an explicit independent
policy gate.

The same-family Kelly solve owns only the endowment-aware cumulative target
vector. It does not preselect a "primary" token: every venue-legal positive
target is rematerialized as its own fixed BUY proposal, kept in the receipt,
and compared with every other BUY, SELL, HOLD, and CASH proposal by the same
raw global comparator. No rounded efficiency shortcut may delete a sibling
before that comparison.

A scalar maker-fill prior is not current decision-time truth and partial fills
change both terminal atoms and capital-release time. `CurrentMakerFillWitness`
therefore binds one candidate, current book epoch, token/side, limit, rest
deadline, training cutoff, issue/decision/expiry clocks, and a complete
zero/partial/full fill-fraction distribution. Only a witness whose causal clock
orders `training_cutoff <= issued <= decision <= expiry` and whose identity is
present in the current book epoch may make its `MAKER_REST` BUY/SELL proposal
rankable; a missing or mismatched witness excludes only that maker proposal with
`CURRENT_MAKER_FILL_WITNESS_UNAVAILABLE`, while its taker sibling remains
eligible. The present live observation plane does not record enough causal
queue/deadline/partial-fill facts to produce that distribution, so no production
maker witness is fabricated: live remains taker-only until a reviewed current
producer exists. A historical fill scalar is offline evidence, never a silent
live fallback. This is an executable-set decision, not a preference for taker
orders.

At submit, the selected mode is preserved and probability, executable book,
Gamma market state, CLOB market-info, fees, tradeability, `negRisk`, tick/min,
wealth/allocation, position, terminal ruin reduction, utility basis, proposal
growth, and capital horizon are canonically sealed and revalidated from one
current Gamma+CLOB+raw-book snapshot. A metadata or selected SELL drift causes a
complete global re-auction; a pure BUY depth overlay is allowed only when that
same metadata authority is unchanged. The persisted executable snapshot commits
all three raw payload hashes rather than retaining selection-time market
metadata.
The current receipt shape is schema 21 / canonical candidate encoding v13. A
winning receipt now persists the winner event/candidate/actuation and a
recomputable compact-row execution binding plus a hash of the exact persisted
summary, then freezes the exact `decision_log` row ID, mode, logical receipt
hash, execution-binding hash, persisted-summary hash, and selection epoch into
the selected actuation and `ActionableTradeCertificate`. If claim-carrier
rebinding changes the winner event or actuation identity, the runtime appends
and commits a newly sealed receipt row that references the unchanged base cut;
it never mutates or reuses the old binding. Entry command persistence re-reads
that exact row before writing the command or position attribution. A selected
SELL carries a typed receipt closure through `ExitIntent` and `ExitOrderIntent`;
inside the command SAVEPOINT, persistence re-reads the exact receipt and checks
its position, condition, token, action, execution mode, winner, and submission
envelope before any command, event, provenance, or envelope row can exist. The
canonical closure is then copied into the append-only `INTENT_CREATED` command
event and provenance payload. Settlement skill attribution follows the existing
exact `position_id -> certificate_hash` relation, revalidates the same entry
receipt row, then consumes the frozen `q_live`, `q_lcb_5pct`, and
`posterior_id`; missing, deleted, mutated, or mismatched global receipts produce
`UNATTRIBUTABLE_Q_MISSING` with no inferred fallback. An orthogonal settlement
audit follows `position_id -> EXIT/SELL command -> INTENT_CREATED closure` and
then the exact `decision_log` row, reporting each global SELL receipt as valid
or invalid without
rewriting the entry-q grade. No bridge table or settlement schema migration is
introduced.

SCOPE is one candidate or one complete q/book/wealth auction cut: malformed or
stale allocation blocks new BUY authority; unavailable maker-fill evidence
blocks only maker-dependent proposals; a drifted selected identity blocks only
that actuation. Taker siblings, CASH/HOLD, held monitoring, and unrelated
families continue. DRAIN is the next normal complete auction and submit-time
rebuild from current config, canonical commitments, probability and book.
RESET is a fresh coherent witness or, for maker, a reviewed typed current
partial-fill authority. Probability formulas, settlement, absolute order-price
bands, RiskAllocator exposure caps, and lifecycle law are unchanged.

Acceptance requires E/L/K/U math and co-tenant isolation, allocation-zero
SELL/HOLD/CASH preservation, exact sub-femtoscale ruin ordering, negative-wealth
rejection, malformed-config rejection, maker rejection with taker-sibling
survival, retention of every family-joint target through the raw global
comparator, allocation and comparison-field identity drift rejection at JIT
and submit, schema-21/v13 health validation, exact receipt-to-actuation/certificate/
command/settlement closure with mutation/deletion/mismatch antibodies, and
focused global-auction/runtime regressions.

The 2026-08-10 CI closeout harmonizes the already-executable global SELL
receipt-audit reasons, `JIT_SUBMIT` snapshot-capture provenance, and JIT/sealed
book final-submit rejection reasons with
`architecture/money_path_objects.yaml`. They remain audit, provenance, and
rejection vocabulary rather than lifecycle, command, order, or fill states.
`MP-CI-001` is the governing invariant: a synthetic classifier antibody covers
all nine registered values while unknown money-path objects remain fail-closed.
The standard-market family-joint Kelly fixture also declares its required
`neg_risk=False` authority instead of relying on an obsolete constructor shape.
Allowed CI-closeout files are `architecture/money_path_objects.yaml`,
`tests/test_money_path_semantic_ci.py`,
`tests/engine/test_multiwinner_wealth_composition.py`, this plan, and its
`scope.yaml` companion.
## 2026-08-10 Day0 HIGH peak state belongs inside the probability simplex

A causal replay of San Francisco Aug-9 HIGH found that the latest same-station
METAR temperature and current provider trajectories were refreshed before the
last executable 5-cent bid. The remaining-path q still assigned 0.23585 to a
future settlement above 75F. At the same decision instant Zeus separately
computed empirical `P(daily high already set | city, month, local hour)` near
0.91 from 70 observations, but used it only to label SELL authority mature. A
state probability that cannot change the settlement distribution leaves the
engine knowingly pricing the wrong random variable.

The correction represents two mutually exclusive causal states in one MECE
simplex: when the HIGH is already set, final settlement is the observed
settlement-channel running maximum; otherwise future extrema are drawn from the
current conditioned remaining-path model conditional on moving beyond that
running maximum. Conditioning the second branch is required: the old
`max(observed, future)` distribution already included peak-set outcomes, so
mixing it directly would double-count the same state. The empirical weight is accepted only
from the monthly empirical source with at least 30 observations and receives a
Jeffreys finite-evidence update so a historical 0/1 cell cannot create certainty
about unresolved weather. The point operator and every bootstrap row use the
same latent-state generator, preserve the simplex, and enter the probability
and witness identities. Heuristic/solar-only confidence cannot modify q. This is a
continuous-time weather-state correction, not a market-price stop or a patch
conditioned on the eventual result.

SCOPE is a current target-day HIGH family with fresh authorized observation,
current remaining-hour paths, and qualified empirical peak-set evidence. DRAIN
is every normal held-position/global-auction redecision, which rebuilds the
mixture from the newest observation, wall clock, paths, and book. RESET is the
next redecision or loss of qualified evidence; LOW, non-Day0, final settlement,
and deterministic absorbing facts retain their own laws. No price band, Kelly,
capital objective, venue mode, or settlement semantics changes.

Acceptance requires a San Francisco last-legal-bid causal snapshot to move the
held `NO >75F` probability below 0.05 before book collapse, every transformed
sample and point q to remain a valid simplex, non-MECE topology to fail closed,
LOW to remain unchanged pending its own trough-state law, the probability basis
to invalidate prior witness semantics, and focused Day0/global-solve tests to
pass. Live effectiveness still requires loaded-SHA, heartbeat, canonical
monitor receipt, fresh q/book, and SELL intent-command-fill evidence.
## 2026-08-08 Day0 probability authority survives certificate compilation

The live global auction produced a current Shanghai Day0 remaining-day witness,
ranked a positive expected-log-growth NO order, and passed exact JIT preflight.
The calibration-certificate compiler then reconstructed a reduced Day0 block
that omitted the already-validated `probability_authority`. Its own downstream
live validator consequently rejected the certificate as missing authority before
any venue command could be persisted. This was a producer/consumer transport
split, not missing probability evidence or failed economics.

The correction carries the producer's canonical Day0 authority block through
the calibration certificate and adds the same exact authority to the certificate
root. Authority, q source/mode, model count, observation clocks/value, and LCB
transform are one closed binding: multiple representations must agree exactly.
The model identity also commits to that authority. It does not infer or upgrade
authority: the source payload must first pass the existing Day0 content validator,
and the compiled certificate is validated again before command build.

SCOPE is one current Day0 remaining-day probability certificate. DRAIN is the
next normal global-auction redecision, which rebuilds the certificate from fresh
observation, probability, book, and wealth evidence. RESET is every redecision;
missing or conflicting source authority remains fail-closed. Forecast q,
probability math, economic ranking, sizing, price bands, JIT checks, venue
execution, held SELL, and settlement are unchanged.

Allowed files are `src/engine/event_reactor_adapter.py`,
`src/events/day0_authority.py`,
`tests/engine/test_cert_calibration_bridge.py`, and this plan. Acceptance
requires the compiled remaining-day certificate to preserve matching root and
nested authority, pass its downstream live validator, retain mismatch rejection,
pass focused Day0/calibration/global-auction tests, and produce a live command or
an exact later-stage rejection on the next eligible positive-EV winner.

## 2026-08-08 Current probability authority must have an executable action path

The live global auction repeatedly produced current, identity-bound replacement
and Day0 witnesses with positive posterior-mean expected log growth, then rejected
them at preflight because a static probability-promotion allowlist contained only
deterministic Day0 payoff. That gate required forward real-fill evidence while
simultaneously preventing every probabilistic fill that could produce the
evidence. It also exposed a separate forecast handoff gap: the selected global
posterior parent rebound `posterior_id` and `probability_authority` but omitted its
same-witness `q_source`.

The correction removes the duplicate static promotion allowlist and dispatches
each event directly to the owning replacement or Day0 authority validator. The
closed grammar therefore lives with the producers' executable content contract,
not in a second manually promoted registry. Grammar admission is not economic admission:
every recognized payload still passes its existing causal posterior or Day0
content validator, qkernel current-state economics, canonical q-source equality,
JIT book/price, fees, depth, Kelly, wealth, and final submit recapture. The global
forecast parent binder now carries `replacement_0_1` source and authority as one
indivisible type and rejects a conflicting pre-existing source.

SCOPE is the exact selected BUY probability witness. DRAIN is the next normal
global auction/preflight using a current typed producer payload. RESET is every
fresh re-decision, which reconstructs q, book, wealth, and the binding before any
venue side effect. Held SELL, settlement, probability formulas, price bands,
sizing, and the CASH alternative are unchanged.

Acceptance requires current forecast and all declared Day0 producer bindings to
reach their authority-specific content validators, unknown aliases and mixed
canonical sources to remain fail-closed, missing forecast q-source to be rebound
from the exact prepared replacement parent, focused and full auction tests to
pass, and live verification to show the static promotion rejection disappears
without bypassing downstream JIT/economic gates.

## 2026-08-02 FSR pause scope preserves posterior-carrier progression

The global `entries_paused` containment correctly forbids new BUY actuation,
but its reactor wake park also returned before a
`forecast_posterior_advanced` wake could emit, supersede, and drain the latest
FSR carrier. Active FSR rows therefore retained obsolete posterior identities
while fresh replacement posteriors continued to materialize.

The hot-fix permits only a targeted `forecast_posterior_advanced` carrier wake
through both reactor pause checks. Its bounded targeted carrier reaches the
existing adapter pause fence, which creates no BUY venue command and leaves the
carrier retryable; ordinary paused queue rows remain unclaimed. Exact held-SELL
requests retain their existing reduce-only path. SCOPE is new-entry BUY
actuation only. DRAIN is the targeted FSR enqueue/supersession plus bounded
no-submit redecision, followed by existing retry-floor scheduling. RESET is
clearing `entries_paused`, which re-decides the same latest carrier identity.

Acceptance requires a paused fresh-posterior carrier to progress without a
BUY command, an ordinary paused queue row to remain unclaimed, and the same
carrier identity to re-decide after the retry floor and pause reset; focused
and full reactor tests, compile, planning/map, and diff checks must pass.

## 2026-07-31 Source-clock location-batch failure isolation

Current canonical raw-capture evidence showed provider issue-to-capture delays
of 51--118 minutes while raw commit-to-posterior materialization normally took
1--20 seconds.  Data-ingest logs tied the lost capture cycles to transient TLS
handshake and read failures on 25-location Open-Meteo requests: one transport
failure flattened every independent location in the batch into the same drop.

The hot-fix bisects only a multi-location, non-NBM request that failed without
a typed HTTP outcome, quota/rate-limit signal, or expired absolute deadline.
Successful halves retain their original provider, model, source run, requested
dates, order, provenance, and normal partial commit.  Typed provider outcomes,
quota denial, NBM's atomic metadata-stamped fallback, and single-location
failures keep their existing behavior.  SCOPE is the failed request's exact
location subset.  DRAIN is bounded recursive bisection under the request's
existing monotonic source-clock deadline.  RESET is the next successful subset
capture or next normal source-clock poll.  No probability, calibration, Kelly,
price-band, risk, venue, or order-throughput rule changes.

Acceptance requires a four-location transport failure to recover as two
ordered two-location requests, quota failure to make no split request, the
focused BPF download suite and source-clock integration tests to pass, and live
deployment to prove current loaded SHA, fresh ingest/forecast heartbeats, a new
raw capture, posterior materialization, and complete global auction receipt.

## 2026-07-31 Exit cooldown preserves continuous redecision

The seven-day full-loss replay found canonical `MONITOR_REFRESHED` events
created during `pending_exit` retry cooldown by copying the position's previous
probability and quote.  The new event timestamp made old evidence appear
current while the monitor skipped `refresh_position -> evaluate_exit`
entirely.  A retry cooldown is an actuation throttle; it is not authority to
stop observing the probability curve or executable book.

The hot-fix keeps a cooldown position in monitor-only mode.  Every normal held
monitor turn still refreshes probability and quote and records the current
economic exit decision, while the existing pending-exit guard prevents a
second SELL from being submitted before the retry deadline.  SCOPE is the
exact pending-exit position.  DRAIN is every held-monitor cycle.  RESET is
cooldown expiry or terminal order reconciliation.  No probability formula,
exit threshold, price band, order retry cadence, or global capital objective
changes.

Acceptance requires a pending-exit position with an active retry cooldown to
consume a newly refreshed q/book, persist those facts as fresh, preserve the
current exit signal, and make zero duplicate venue calls.

## 2026-07-30 Canonical LOW ENS boundary evidence

Paris Jul-31 LOW and Shanghai Aug-1 LOW had current 12Z ECMWF ENS snapshots,
but source-clock posterior materialization rejected both and walked back to
stale shape evidence.  The snapshots carried only 18/51 and 2/51
boundary-ambiguous members, respectively.  The out-of-repo Open Data extractor
still stamped the retired any-member snapshot veto, while the canonical ingest
contract already used a 26/51 majority rule.  Ingest validated the canonical
decision but then persisted the producer's stale flag and derived forecast
window evidence from it, so one obsolete producer boolean overruled the
contract and broke continuous probability refresh.

The hot-fix makes the ingest contract the single interpreter of LOW boundary
evidence.  When per-member inner/boundary minima exist, it re-derives ambiguity
with the strict physical rule `boundary_min < inner_min`; otherwise it uses the
declared count, with the legacy flag only as the evidence-poor fallback.  The
ingester consumes that normalized payload for training, DB flags, contract
window attribution, and posterior selection.  Minority ambiguous members stay
null and excluded from the ENS sample; genuine missing members and majority
ambiguity remain fail-closed.  SCOPE is one city/date/metric ENS snapshot.
DRAIN is the next normal re-ingest/materialization of that source cycle. RESET
is a newer canonical snapshot identity.  No market-price belief, historical
width fallback, stale-cycle extension, action threshold, or lifecycle rule is
added.

Acceptance requires an external-legacy-shaped 2/51 payload to persist as
`boundary_ambiguous=0`, retain exactly two null members, contribute to the
target extrema, and be selected as a six-hour stale current-evidence shape by
an 18Z carrier. The raw evidence hash, artifact identity, canonical revision,
and per-member decision reasons must remain auditable in provenance. A 26/51
payload plus missing, NaN, and infinite extrema must remain blocked, followed
by the focused ingest, materializer, source-contract, and live posterior
receipts.

## 2026-07-29 WU fast evidence keeps provisional probability semantics

The Seoul Jul-29 HIGH posterior labeled a qualified same-station
`aviationweather_metar` print as the probability source. The generic Day0
finality classifier also uses that raw source id for NOAA-settled cities, so
the WU-settled Seoul certificate incorrectly treated the raw 30C print as an
absorbing settlement bound. The live monitor independently reconstructed the
margin-adjusted 29C physical boundary and correctly rejected the inconsistent
certificate, leaving every held Seoul position without fresh q.

Seed discovery now names the already-defined composite evidence
`wu_api+same_station_fast_tail` when a strictly causal seven-day residual
likelihood qualifies. The materializer accepts that composite identity only
for the same WU-city residual builder, keeps it provisional, and applies the
residual scenarios to point q and the identical bootstrap simplex. Direct
`aviationweather_metar` remains absorbing authority for NOAA-settled cities.
SCOPE is the exact WU city/date/metric/station posterior. DRAIN is the next
source-clock materialization. RESET is a newer settlement or fast print, which
creates a new residual and posterior identity. No source fallback, entry veto,
market-price belief, exit threshold, or NOAA authority is changed.

## 2026-07-27 Same-station fast residual likelihood

The Seoul Jul-27 31C NO loss exposed a category error and a missing probability
edge. RKSI published 31C at 03:04 UTC, but the source-clock posterior retained
the slower WU 29C state until 04:34. The existing WU/METAR divergence margin is
an absolute anomaly threshold; it is neither a signed measurement likelihood
nor permission to turn METAR into settlement truth. The materialization bridge
also re-read only the settlement channel, so the faster same-station print did
not reshape q at all.

The hot-fix keeps WU as the sole hard settlement bound. For a raw fast running
extreme that supersedes WU, seed discovery requires at least 20 causal
same-station WU/METAR pairs from the preceding seven days. It builds the signed
residual distribution strictly before the fast print and decision clocks. The
materializer mixes `max(O_wu, O_fast + residual)` for HIGH or the symmetric
minimum for LOW across the final probability simplex and the same bootstrap
draws used by executable bounds. One finite-evidence unknown state uses the
95% zero-hit Clopper-Pearson mass `1-0.05^(1/n)` and retains only the WU
bound, so fast evidence can move probability immediately but cannot produce
settlement certainty. Thin, mismatched, missing, or non-WU evidence is inert
and preserves the settlement-channel posterior.

The global Day0 adapter consumes that certified posterior as the single
conditioned point-q and bootstrap world for both entry and held-position
redecision; it must not rebuild a second remaining-day point-q and merely use
the fast posterior as a cap. Consumption revalidates the residual identity
hash, exact source id, station, availability clock, latest raw fast extreme,
unit, and simplex basis. WU still supplies the only deterministic payoff
boundary and payload settlement source. A physical-frontier entry veto drains
only when this exact current fast-residual posterior is present; HKO
provisional snapshots retain their existing entry block.

SCOPE is the exact city/date/metric/station posterior. DRAIN is the existing
post-commit Day0 seed bridge and materialization queue. RESET is a newer source
observation or WU settlement-channel advance, each producing a new
content-addressed posterior identity. The absorbing running extreme and the
remaining-window clock are independent state: every newer same-station print
advances the clock and rematerializes q even when a cooler HIGH or warmer LOW
print leaves the extreme on a plateau. Acceptance requires no-look-ahead and
thin-sample antibodies, HIGH/LOW absorbing symmetry, coherent point/bound
simplexes, provenance identity, a causal Seoul replay, and the existing
replacement/Day0 suites. No market-price stop, city patch, settlement writer,
lifecycle rule, or blanket family veto is added.

## 2026-07-27 Direct NOAA publication continuity

The Istanbul Jul-27 31C YES position entered at 13:07 UTC from a current LTFM
31C report. NOAA/NWS published the next LTFM METAR at 13:20 with 32C, while
the live monitor still held the 12:50 Ogimet mirror snapshot and q≈0.962 after
13:33 as the market collapsed. The existing five-second AviationWeather/NOAA
source clock already polls exact held-position stations, but its registry
excluded every `settlement_source_type=noaa` city solely because an Ogimet
lane existed. Downstream Day0 event and remaining-window readers already admit
`aviationweather_metar` for NOAA cities.

The hot-fix admits only configured NOAA cities with an exact configured ICAO
station into that existing clock and authorizes the direct publication only
when station, local date, unit, publication clock, and source type all match.
Ogimet remains the canonical hourly/history writer; direct NOAA is the faster
same-authority live publication, not a cross-source proxy or market-price
substitute. The direct print and `DAY0_EXTREME_UPDATED` now commit atomically,
with the publication ledger inserted before the event and reactor wake after
commit. Held-position monitoring compares direct and Ogimet contexts from one
causal read boundary and selects the newer exact-station fact. The typed event
separates canonical `settlement_source_type=noaa` from the
`aviationweather_metar` vendor channel. WU-only tail fusion, pre-Day0 LOW
conditioning, and WU-vs-METAR divergence comparison remain WU-only. SCOPE is
the exact configured city/date/station/metric family; DRAIN is the next
five-second station poll and atomic publication/event commit; RESET is the
newer same-station observation version followed by normal posterior
materialization and global redecision. A failed ledger write withholds the
event and enters the existing bounded commit retry instead of waking against
stale trajectory state.

Acceptance requires a two-poll Istanbul 12:50 31C -> 13:20 32C causal replay
that proves ledger-before-event order and emits an authorized 32C event, held
monitor selection of the newer direct 32C context over the 31C Ogimet mirror,
a proof that NOAA cities never enter the WU divergence comparator, unchanged
non-WU fallback behavior, focused fast-source and remaining-day tests,
planning-lock/source-contract checks, compilation, and `git diff --check`.
Live proof requires the exact loaded SHA, fresh ingest heartbeat, a new LTFM
direct publication in the canonical event/print path, and a resulting current
held-family probability/redecision receipt.

## 2026-07-26 Zero-observation Day0 reseed contract

The live Day0 source clock emitted a current Paris report and queued immediate
posterior materialization, but the canonical settlement-channel reader
truthfully returned the typed state `zero_target_date_observations`. The seed
schema and builder already admit that state; the single-family cycle-advance
wrapper did not, so the bridge retried forever with
`unexpected keyword argument 'day0_observation_state'`.

The wrapper now carries the existing typed state through the same
single-family seed transport and treats it as Day0 evidence while preserving
the builder's state-versus-extreme mutual exclusion. The poll-lane twin carries
the same field. SCOPE is the exact city/date/metric family; DRAIN is the
existing held/entry materialization worker queue; RESET is a committed
posterior followed by normal redecision. No source role, probability formula,
market-price substitution, lifecycle, action law, or exit threshold changes.

## 2026-07-26 Pre-submit DB-lock exit continuity

The restored Tel Aviv remaining-window probability selected a live 33C YES
SELL at 14c. Command persistence hit SQLite `database is locked` before the
venue boundary. `executor.py` returned the typed clean transient reason
`pre_submit_db_locked_transient` and explicitly required retry on the next
cycle, but exit lifecycle did not classify it and applied the generic
five-minute economic backoff.

The lifecycle now gives only that exact pre-venue typed rejection zero
cooldown and preserves the retry budget. The next monitor/global-auction cycle
must recapture current probability, wealth, and book before submitting; no
old command or quote is reused. SCOPE is the affected pending-exit position;
DRAIN is the next canonical retry scan/global auction; RESET is a fresh submit
or a newly justified HOLD. Runtime submit gates, venue-side unknown outcomes,
price bands, liquidity waits, and active-order locks are unchanged.

## 2026-07-26 Current-state redecision wake

Publishing the current report restored fresh local monitor q, but the global
capital auction still held the prior probability content. Statistical SELL is
intentionally non-authoritative in the local monitor, so the exact new q could
not act until a family event refreshed global holding coverage. The Day0
trigger emitted only when a WU/Ogimet running extreme moved; a newer station
report inside the old extrema changed trajectory probability without waking
the auction.

The existing Day0 source-clock gate now emits once when the source-issued
observation time advances, matching its existing HKO plateau semantics. A
mere later fetch/import of the same report remains suppressed, preserving the
anti-firehose invariant. SCOPE is the exact city/date/station/metric family;
DRAIN is the next admitted source report; RESET is committed event processing
and a new exact global holding-coverage epoch. Local SELL does not bypass the
global expected-log-capital comparison.

## 2026-07-26 Native hourly publication-ledger continuity

After canonical `temp_current` became available, held-position redecision still
reported `DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE`. The remaining-window
conditioner correctly admits only the append-only `observation_prints` ledger,
not the mutable hourly projection. The live tick wrote WU extrema to that
ledger, omitted each bucket's non-extreme latest report, and wrote no Ogimet
prints at all. Thus the current-state fact existed in the source response and
hourly projection but was absent from probability authority.

Both native WU and Ogimet hourly paths now append three independently deduped
facts per bucket: max, min, and latest causal report temperature. The ledger
remains append-only; the hourly projection and event publication commit under
the same writer lock. SCOPE is the exact city/station/source/report identity;
DRAIN is the next successful native-source tick; RESET is an admitted ledger
print followed by the normal held-position monitor cycle. This restores source
truth to current-state probability without substituting market price or adding
trade-specific thresholds.

## 2026-07-26 Same-hour latest-temperature advance

The latest-temperature repair exposed a second writer-side discontinuity.
`observation_instants` keys native reports by UTC hour bucket. A 15-minute
live tick can therefore see a newer report in an existing bucket without a
new key or a wider max/min. The typed writer allowed only extrema/count/
provenance widening, so it quarantined the changed `temp_current`; Tel Aviv's
canonical current-state anchor remained NULL even after a successful same-
source fetch.

The existing audited monotone bucket-advance contract now also updates
`temp_current`, but only when provenance proves `latest_raw_ts` does not move
backward and extrema do not narrow. The revision record preserves the prior
temperature and report time. SCOPE remains the exact city/source/hour key;
DRAIN is the next successful native-source tick; RESET is the atomic canonical
row update followed by the normal held-position monitor cycle. No source,
market-price proxy, trade threshold, lifecycle, or action law changes.

## 2026-07-27 Selected-proof mismatch must not starve the capital auction

Live global auctions repeatedly selected one positive-growth BUY and then
failed its immutable posterior-parent check with
`GLOBAL_ACTUATION_POSTERIOR_BINDING_MISMATCH`.  The proof stayed rejected, but
the unclassified exception was treated as a batch-wide block, so every lower
ranked BUY, SELL, and CASH comparison was starved on every retry.

The hot-fix preserves the exact parent check and zero-side-effect rejection.
Only that selected candidate is made ineligible for the current immutable
epoch, after which the existing global objective re-ranks all remaining
actions.  SCOPE is the mismatched candidate identity; DRAIN is the same-epoch
re-auction; RESET is a later candidate carrying the current prepared posterior
parent.  No probability, edge, sizing, price-band, venue, or batch-wide
freshness requirement is relaxed.

Acceptance requires a failing-before/passing-after proof-classification
antibody and an integration case where the rejected BUY cannot touch the venue
and a sibling SELL is selected and submitted.  Live proof requires the repeated
batch block to disappear and a subsequent receipt to show candidate-local
fallthrough or successful next-candidate preflight under the loaded hot-fix.

## 2026-07-26 Held q is independent of entry phase

The current global auction correctly closed forecast-carried families to new
BUYs once local settlement day began, but reused that entry phase gate as a
probability gate.  Held positions in those families therefore lost their
current q before the auction could compare reduce-only SELL with HOLD/CASH.
This was a composition defect: venue phase can remove an action from the
feasible set, but it cannot erase the state needed to re-optimize existing
capital.

The hot-fix preserves the phase rejection for new entry while allowing the same
current forecast carrier to prepare a held-only probability witness with
`entry_authority=False`.  Day0 fallback permissions remain disabled on this
forecast lane, the resulting positive family witness uses the existing bounded
probability cache, and the global batch's existing held-only rule keeps BUY
disabled.  Day0 provisional/physical-frontier entry protections are unchanged.

Acceptance requires the phase-rejected entry and held-only q behaviors in one
antibody, the complete global-auction integration file, syntax and diff checks,
and a base-versus-patch comparison of the wider required test slice.  Live
completion additionally requires loaded-SHA proof and a natural same-epoch
receipt showing held families reach SELL/HOLD/CASH evaluation rather than being
excluded solely because forecast entry phase is closed.

## 2026-07-26 NOAA-mirror Day0 source continuity

The Tel Aviv Jul-26 31C BUY NO loss reconstruction found a real 21.147057-share
holding with 754 monitor refreshes and no `EXIT_INTENT`.  The last fresh belief
arrived at 00:25 UTC; from 00:26 onward the held family accumulated 568
`EVIDENCE_UNAVAILABLE` decisions while the executable NO bid fell through
28c, 15c, 6c, and 4c.  Canonical `observation_instants` contained zero Tel Aviv
rows for the target day.  The ingest log showed every NOAA-settled city failing
with macOS `EADDRNOTAVAIL`: the Ogimet client bound each request to the IPv4
wildcard to avoid an older IPv6 SYN stall, but that local bind could not obtain
an address while an unbound IPv4 request to the same endpoint succeeded.

The hot-fix keeps the exact provider, station, local-day window, parser, source
tag, and extremum-preserving writer contract.  Forced IPv4 remains the preferred
route; only a proved local-address exhaustion error retries the same Ogimet URL
through the default network route.  Other transport failures remain
`NETWORK_ERROR` and fail closed.  No market-price belief substitution, forced
exit, source-role fallback, or entry/exit threshold is added.

The existing evidence gate remains family-scoped.  Its DRAIN is a successful
same-source ingest followed by the normal held-family redecision wake; RESET is
a fresh canonical target-date observation and current probability certificate.
Acceptance requires focused fallback/fail-closed antibodies, the complete
hourly-client and observation-writer suites, a live LLBG fetch, a canonical
Jul-26 row, and a natural held-position refresh that no longer reports
`zero target-date canonical observations`.

## 2026-07-25 Stale ENS disagreement remains probability uncertainty

The live Dallas Jul-26 102-103°F certificate paired a fresh 06Z carrier center
of 36.934337°C with a 00Z ENS mean of 39.067265°C. Anomaly transport shifted
the 6h-old member sample onto the fresh center and set the operational
2.132928°C disagreement to zero. Predictive sigma collapsed from the
same-evidence absolute-disagreement value 2.251251°C to 0.720244°C:
`q_yes(102-103°F)` moved from 0.120401 to 0.009900 and its upper bound from
0.717837 to 0.155766. The resulting `q_no=0.990100` admitted a live BUY NO
6.5 @ 0.82 that the complete current-evidence ambiguity would reject robustly.

The correction retires synthetic anomaly translation from finite evidence.
Raw absolute members and their ENS/provider-center displacement remain the
independent `delta_ens²` term in predictive and center uncertainty. Bounded
stale-shape certificates advance to
`stale_ensemble_absolute_disagreement_v1`, forcing the existing materialization
loop to replace v3 rows before entry or held-position belief can consume them.
The behavioral antibody reproduces the live 2.132928°C disagreement and
requires raw member semantics, full sigma, and full center uncertainty.

Post-restart evidence exposed a second reader-side bypass. `position_belief`
correctly rejected an Ankara Jul-27 posterior carrying
`ensemble_anomaly_transport_v3`, but the global-auction bundle reader called
that same row live-grade because it checked only non-null bounds and
`FUSED_NORMAL_*`. The held auction therefore kept evaluating Ankara with the
frozen `q_no=0.985060` even after `last_monitor_prob_is_fresh=0`. Live-grade
bundle selection now also requires the shared tradeable q-bound basis and the
current-evidence semantics revision. A focused antibody rejects the old
transport row and accepts the replacement stale-absolute-disagreement row.

## 2026-07-24 BUY-only preflight fallthrough

The current global auction compares new BUYs and held-position SELLs under one
posterior-mean expected-log-growth objective.  A BUY winner can fail submit-time
entry readiness while an independently feasible reduce-only SELL remains in the
same frozen auction epoch.  The live adapter already routes SELL before every
entry-only gate, but preflight classified
`LIVE_ENTRY_BLOCKED:entry_readiness:*` as an unknown batch-wide failure.  That
stopped the complete cut instead of re-auctioning, so unresolved entry outbox
or cap reservations could starve a profitable exit.

The preflight now excludes that exact BUY candidate and re-runs the immutable
current auction.  Other unknown, stale, superseded, or internally inconsistent
authority failures remain fail-closed for the complete cut.  The behavioral
antibody proves that an entry-readiness-blocked BUY falls through to a sibling
SELL without rebuilding probability, book, or wealth truth and with exactly
one venue submission.

Money path: current entry authority + held-position exit authority -> global
capital auction -> submit-time preflight -> venue action.  Probability,
lifecycle, sizing, price-band, and settlement semantics are unchanged.

## 2026-07-24 Post-trade boot identity ordering

The official live restart correctly refused to start the order daemon until
every prerequisite sidecar proved the expected loaded SHA from a fresh
process-owned heartbeat. The post-trade sidecar registered heartbeat,
harvester, and collateral jobs with the same immediate `next_run_time`, then
entered `BlockingScheduler`. Cold network/capital work occupied the scheduler
workers first, so the first new heartbeat arrived several minutes after the
90-second deploy identity window. Recovery had succeeded, but the fail-closed
deployment left live trading stopped.

The sidecar now writes one synchronous boot heartbeat only after sanctioned DB
preflight and the complete cascade-liveness poller contract pass, but before
the scheduler can run any immediate job. The existing periodic heartbeat
remains the ongoing liveness signal. This preserves the proof boundary: the
running sidecar itself publishes its immutable boot SHA; deploy does not
fabricate identity, extend a timeout around unbounded work, or start the order
daemon without prerequisite proof.

Money path: runtime code identity -> restart admission -> continuous
re-decision/exit availability. No probability, entry, sizing, lifecycle, or
capital-job semantics change. Rollback restores the prior safe deployment
block.

## 2026-07-24 Restart fill-identity convergence

The official live restart stopped the complete mesh when restart-preflight
found a confirmed six-share entry command whose original short position id had
no projection. Chain reconciliation had already restored the same six shares
onto an earlier zero-fill position row for the exact token, so naïvely opening
the command's row would create parallel exposure. The command's append-only
trade journal also contained two derived EDLI aliases of one venue trade;
projection recovery aggregated canonical trade ids instead of economic trade
ids and inflated six shares to eighteen.

Recovery now applies the existing economic-fill identity reducer before any
entry aggregation. A command may relink to a different-order chain-observed
row only when one unambiguous same-token, same-condition row has synchronized
quantity and chain cost equal to the complete command-deduped fill, has no
prior command-bound fill event or execution fact, and the command is terminal
FILLED. Any command-bound execution fact still owned by the orphan id is
rehomed in the same repair transaction and keeps one canonical intent identity.
The active-row projection then persists or reuses that command-level execution
provenance before rebuilding the aggregate; the aggregate and synchronized
chain quantity must converge to the same shares, so the fill is attributed
without adding exposure a second time. Restart-preflight runs this narrow link
repair before fill projection. The behavioral antibody reproduces the exact
zero-fill-row -> chain rescue -> later confirmed-fill shape, including an
orphan execution fact and recursive EDLI aliases, and requires one six-share
position, one execution fact, and idempotent replay.

Money path: venue fill truth -> command provenance -> canonical position
identity -> current exposure -> restart admission. No probability, sizing,
entry, or exit threshold changes. Rollback is a single hot-fix revert; because
the preflight remains fail-closed, rollback returns to a safe restart block
rather than fabricating or duplicating exposure.

## 2026-07-24 Atomic chain-reappearance economics

The complete-position pagination repair made a previously omitted Seoul
holding visible again and exposed a second canonical-truth defect. The mirror
reappearance fold restored `chain_shares` and `chain_seen_at`, but discarded
the Data API's `avg_price` / `cost` and left `chain_state` at its stale
pre-observation value. Runtime exposure authority then correctly rejected the
torn state (`chain_shares > 0` with zero chain cost basis), which blocked the
global capital auction including held-position SELLs.

The repair keeps acquisition/fill provenance fields owned by their existing
authority and atomically projects only the complete positive chain
observation: attributed chain shares, chain average price, chain cost basis,
`chain_state=synced`, and observation time in the same append-plus-projection
transaction. A behavioral antibody reproduces the exact
absence-marker -> positive reappearance transition and requires phase and
owned fill economics to remain unchanged. Because an older correction may
already have consumed the absence marker while leaving a torn row, the same
fold is also re-emitted whenever a complete positive observation finds stale
visibility or missing chain economics; recovery does not depend on historical
event ordering. No schema or lifecycle grammar changes. Rollback is a single
hot-fix revert; the next complete reconciliation re-emits the prior event
shape.

## 2026-07-24 Complete venue-position enumeration

The live loss audit found a confirmed 5-share Seoul fill whose exact Polygon
CTF `balanceOf` remained positive while both chain reconciliation and the
global held-position SELL auction treated it as absent. Polymarket's Data API
documents `/positions` with a default `limit=100`; both Zeus enumeration
callers set `sizeThreshold` but omitted `limit` and `offset`. The funded wallet
currently has 117 returned positions, so the default page silently omitted the
held token and prevented an otherwise executable statistical exit.

Both Data API enumeration paths now request the maximum documented page size
and continue by offset until a short page proves completion. Repeated assets
are deduplicated, and reaching the documented offset ceiling without a short
page fails closed instead of treating a prefix as chain absence. This repairs
`Chain/CLOB truth -> reconciliation -> current wealth -> global SELL auction`;
it changes neither probability authority nor exit economics and adds no
per-token RPC fan-out.

## 2026-07-24 Venue-outcome probability feedback

The July 22–24 loss reconstruction found a control-loop split: Gamma had
already resolved the held token and the canonical SETTLED event carried the
frozen entry q, binary payoff, and exact entry q-version, but RiskGuard required
the later physical temperature publication before scoring Brier. The latest
fifty economically resolved positions therefore remained invisible while the
probability system continued to report GREEN.

Venue resolution now makes only the held-token probability outcome
learning-ready. It does not become physical temperature or calibration truth:
`probability_outcome_ready` and `metric_ready` are separate facts. RiskGuard can
localize a degraded recorded probability mechanism from immediate zero-sum
payoff evidence even when each consumer strategy is individually below the
minimum sample floor. The live July 24 sample identifies
`decision_snapshot:metar_fast` rather than pooling it with the independent
forecast qkernel; durable gates therefore stop only the mechanism's actual
strategy consumers while leaving healthy current-evidence forecast alpha and
settlement semantics unchanged.

Strategy gates compose monotonically across independent control and RiskGuard
authorities: if any active row gates a strategy, a separate active enable row
cannot erase that protection by winning lookup order. Operators can still
re-enable a strategy by expiring the gate whose evidence has been reviewed;
an unrelated enable override is not a substitute for retiring current risk
evidence.

## 2026-07-24 Day0 continuous-state residual conditioning

The July 22–24 loss reconstruction falsified the diagnostic-only treatment
below. Across 27 Data-API loss/near-zero positions, no executable SELL command
was emitted before the bid collapsed; for the subset with a still-positive
book and `q < bid`, the decision layer selected HOLD. The common probability
failure was a discontinuity: the current station/model residual was recorded
but discarded, so the engine combined an observed running extreme with
unconditioned future hourly paths and moved toward a point mass. When the next
observation crossed the adjacent bin, q flipped after executable liquidity had
already disappeared.

The replacement law is a causal short-memory state update, shared by new-entry
and held-position probability paths. For each current model path, the latest
elapsed hourly model anchor gives residual `e0 = observed - model`. An unseen
hour at lead `h` is shifted by `e0 * exp(-h / tau)`; elapsed hours stay
unchanged, and the anchor becomes the observation for the terminal sub-hour
fallback. This preserves real future excursions and converges back to the raw
forecast rather than treating one station print as a permanent model
translation.

`tau=4.2h` is runtime config, not a per-order patch. It was fit only on the
available prior seven-day window: training dates 2026-07-21..23 produced
`tau=4.17h` from 1/2/3/4/6h residual persistence. Frozen 4.2h validation on
2026-07-24..25 improved mean absolute error at every tested horizon:
0.584/0.390/0.265/0.181/0.074C respectively. The decision certificate now
persists the conditioning revision, model residuals, and tau so cache/replay
identity cannot silently mix the old and new probability meanings.

Money path: source truth -> forecast signal -> continuous Day0 probability ->
global auction -> Kelly -> entry/held redecision. Re-decision repeats the same
operator on every fresher observation; no market price is used as probability
authority.

The same live audit found a separate constructor-contract regression affecting
12 of 14 open July 25 positions: the Day0 builder still passed retired
`bias_corrected` state into `MarketAnalysis`, so every refresh raised
`TypeError` and the monitor accumulated 12–21 stale-belief cycles under
`EVIDENCE_UNAVAILABLE`. The obsolete argument and its dead local assignments
are removed. A signature relationship antibody now requires every keyword at
that live constructor seam to exist in the current runtime contract. This is a
belief-availability repair, not a relaxation of freshness or source gates.

## 2026-07-24 HKO trajectory truth-domain correction

The July 24 Hong Kong LOW loss exposed a silent same-table-name split across
canonical DBs. The hourly daily-observation job runs with forecasts.db as MAIN
and world.db attached. Its unqualified HKO accumulator and publication-ledger
writes therefore updated forecasts.db, while the source-clock projector and
Day0 current-temperature reader consumed world.db. Forecasts held 16 current
July 24 readings through 12:00Z; world held no July 24 accumulator rows and its
spot ledger stopped at 05:02Z even as official cumulative extrema remained
fresh. No SQL error or source-health failure surfaced because both DBs contained
an HKO accumulator table.

The runtime writer now names the `world` schema explicitly for the accumulator,
spot-print ledger, and next-day realtime finalization read. The forecasts/main
path remains only for isolated legacy/test callers; production routing binds to
the registry-owned world truth domain. An antibody creates same-name tables in
both DBs and proves that a new HKO reading and its trajectory print land only in
world. This repairs source truth -> Day0 trajectory evidence; it does not use
market price as probability authority or change settlement extrema semantics.

The follow-on live audit found one remaining time-domain split after DB routing
was repaired. The Hong Kong July 25 posterior had advanced to the 23:10Z HKO
snapshot while the latest `DAY0_EXTREME_UPDATED` event remained at 22:10Z
because the catch-up scanner deduplicated HKO solely by displayed high/low.
HKO intraday extrema are provisional snapshots, not WU-style monotone bounds:
an equal value at a newer source time shortens the remaining physical window
and must refresh both HIGH and LOW probability families. HKO event watermarks
therefore bind `(value, observation_time)` per metric; an advanced source time
emits once, while rescanning the same version remains silent. The WU monotone
extreme firehose gate is unchanged.

## 2026-07-24 Interim Day0 trajectory correction (superseded)

Current live Ankara 28C evidence falsified one probability assumption before
Kelly or execution. The remaining-path builder transported each model's
instantaneous predawn error unchanged through every future hour, moving the
three current extrema from 28.2/25.6/26.0C to 25.4/24.6/24.9C. No current
temporal covariance witness authorized that permanent translation, yet it
helped produce a 98.67% NO lower bound and a live 16-share maker order.

The clock hypothesis was separately rejected: a later row carrying the same
running extreme proves the no-new-extreme coverage frontier through that later
observation time. Rebinding it to the first occurrence would discard valid
plateau information and contradict continuous-time redecision. That path is
unchanged.

At this interim stage, current temperature cut the remaining window and
recorded model innovation without propagating it into unseen hours. The
seven-day walk-forward witness in the preceding section subsequently supplied
the missing temporal law and superseded this diagnostic-only behavior.

Money path: forecast signal -> Day0 current-evidence probability -> global
auction -> Kelly -> maker lifecycle. Behavioral antibodies cover exclusion of
elapsed model points and the absence of unvalidated permanent state transport.

## 2026-07-23 Day0 raw-report freshness clock correction

Live Miami evidence separated two clocks that the hourly observation schema
intentionally stores: `utc_timestamp` is the stable UTC-hour bucket identity,
while `hour_*_raw_ts` is the source report time. Day0 readers and the event
catch-up scanner used the bucket identity as the freshness clock. A 01:53Z WU
report possessed at 02:15Z was therefore treated as a 01:00Z observation and
declared stale against a 60-minute budget, suppressing current Day0 authority
while the source stream was healthy.

The hot-fix preserves bucket identity, extrema aggregation, source routing, and
causality gates. Hourly clients now retain the latest raw report in each
bucket; the typed writer rejects possession timestamps earlier than that fact;
and both direct readers and event catch-up use that raw source time, with the
existing max/min raw timestamps as a backward-compatible fallback. HKO's
cumulative snapshot clock remains its canonical `utc_timestamp`. Eligibility
now requires bucket identity, source fact time, and possession time all to be no
later than the decision; gap analysis and latest-context selection use the same
fact clock. This restores causal held-position redecision without letting quote
time or market price enter probability authority.

## 2026-07-23 continuous-time plateau redecision correction

The loss-to-zero reconstruction found that the fast Day0 source clock emitted a
new `DAY0_EXTREME_UPDATED` event only when the rounded running extreme moved.
An unchanged HIGH after the peak, or unchanged LOW after the overnight trough,
was treated as no new information even when the settlement-station observation
version advanced. That froze the conditioned probability surface precisely when
elapsed time and a shrinking remaining window should continuously remove paths
to a different settlement bin. The replacement materializer already treats a
strictly newer observation version on a plateau as new information; the event
producer discarded that version before it could reach the materializer.

The hot-fix extends the existing split event memo with a monotone source-time
version. Rounded extremes remain absorbing and may only move in their physical
direction; observation versions may only move strictly forward. Either change
emits one event, while a repeated source version remains idempotent. This keeps
the source-report cadence (normally 30/60 minutes) and does not restore the
per-scan event firehose that the rounded-extreme gate removed.

Money path: source truth -> Day0 conditioned probability -> held-position
redecision -> exit. Re-decision behavior: plateau evidence now refreshes q(t)
using the shorter remaining window; no market-price anchor or price-only stop is
introduced. The 72-hour causal replay rejected a generic trailing stop because
its apparent gain depended on one large outlier and it sold multiple eventual
winners; this change repairs the missing fact instead of optimizing that
loss-only proxy.

The London 26°C NO incident then exposed a second, independent category error.
At 19:55–22:55 UTC every current remaining-path member was below the already
observed 26°C high, yet live q for the held NO repeatedly rose toward 0.28. The
old calculation evaluated `noise(max(observed_high, future_high))`, so fixed
instrument noise was applied to the already observed settlement boundary and
manufactured a 27°C tail even as current temperature fell to 21°C. Physical law
is `max(observed_high, noisy_future_high)` (and symmetrically `min` for LOW).
The corrected Day0-only operator preserves real future excursions, removes only
the impossible post-boundary noise, and persists its operator identity in the
probability receipt. A same-station fast print may advance the physical clock
and trajectory state, but it cannot replace the settlement-channel value used
for deterministic payoffs. Trajectory state is causal on both source publish
time and local fetch time; Fahrenheit cities require and parse the METAR
tenths-Celsius T-group instead of converting a rounded whole-C ledger value.
Those rules live at the central Day0 fact reduction as well as the trajectory
reader, so global redecision and remaining-path conditioning cannot construct
different information sets from the same observation ledger.

Live resampling exposed a third independent continuity defect: the Day0 hourly
refresh cursor advanced only when an HTTP attempt occurred and took modulo the
already-truncated three-city microbatch. With twelve held cities, throttle made
the cursor repeatedly offer the same first page while later holdings exceeded
the three-hour probability freshness window. The corrected cursor advances by
held slots offered, including throttled/failed slots, and takes modulo the full
held-capital segment. This preserves the bounded HTTP budget while guaranteeing
coverage; it increases fairness, not request volume.

## 2026-07-23 Day0 expected-value and maturity composition

The Mexico City 26C NO receipt isolated a downstream category error after its
fresh held probability fell to 3.73% and its executable bid still paid 20.7c.
Its entry and current confidence intervals were disjoint, yet the exit evaluator
priced HOLD with the current 37.33% upper confidence bound. A confidence bound
is evidence for or against reversal, not the expected terminal payoff. Reusing
it as payoff blocked thirteen of fourteen executable adverse cuts and stranded
the leg below the venue's legal exit band.

The 72-hour Ankara 31C YES winner refutes an unconditional point-q liquidation:
an early-day disjoint reversal at q=10% and bid=42c later settled in the held
side's favor. The missing discriminator is causal time. The Day0 remaining-day
builder already emits whether the observed extreme is mature enough to sponsor
a statistical exit, but the current-global materializer dropped that evidence
before family redecision.

The correction composes the existing authorities instead of adding a price
stop. CI separation remains the confidence gate; current point-q supplies the
Day0 expected HOLD payoff; current-global refresh carries the temporal maturity
reason; and family redecision blocks an otherwise valid statistical exit until
that reason clears. Non-Day0 and near-settlement UCB comparisons are unchanged.
A low-priced claim with high fresh expected value remains held, while a mature
reversal is monetized before its bid becomes unexecutable.

The downstream global auction exposed a second category error: it independently
re-scored every held SELL with lower-CVaR parameter draws and could veto the
local fixed-action expected-value decision. The 72-hour counterfactual set
contains both sides of the discriminator: Mexico City was a mature mean-positive
sale stranded by the tail objective, while Ankara and current Cape Town were
early mean-positive reversals that maturity must exclude. Outcome knowledge is
not an input to either decision.

The global correction therefore makes three different statements in three
different types. Temporal authority determines whether a Day0 statistical SELL
exists in the feasible set and is bound to the exact probability witness.
Action economics determine size and safe FAK prefixes: mature Day0 SELL uses
posterior-mean expected log wealth and EV in `expected_*`, while BUY keeps robust
admission/sizing. Finally, every admitted fixed proposal receives the same
posterior-mean expected-log-growth comparison for cross-action ranking. Mean
numbers are never written into `robust_*`; selection-time maturity is rebuilt at
submit before any venue call. Receipt schema 17 / candidate v11 / holding v2
make those distinctions auditable.

## 2026-07-23 loss-to-zero causality correction

The 72-hour loss census and decision-time reconstruction found three defects that
sit upstream of any exit-price floor: `src.main` remained launchd-running while
its recurring monitor and daemon heartbeat stopped for roughly 9h45m, and Day0
`buy_no` was classified as `settlement_capture` from direction alone even when
the observed extreme had not crossed the selected finite bin. In the same stall,
WU/OGIMET and the HKO fallback fetch path captured possession timestamps before
their network requests; when execution resumed, later source facts were persisted
as if held hours earlier. The first froze q(t) while the book moved; the second
gave forecast-dependent positions the wrong strategy identity, alpha clock,
policy cohort, and attribution; the third violated point-in-time causality.

This hot-fix extends the existing venue-heartbeat watchdog, under its existing
deploy restart lock and fresh-sidecar prerequisites, to restart a running but
heartbeat-stale live daemon. It also introduces one selected-payoff truth
contract for HIGH/LOW x YES/NO: only a side physically locked by the monotone
observed extreme is `settlement_capture`; unresolved or unavailable truth is
`day0_nowcast_entry`. Command recovery defaults missing legacy truth to nowcast.

Authority surfaces touched: `architecture/strategy_profile_registry.yaml` and
`architecture/source_rationale.yaml`. This harmonizes the registry's existing
"observation itself" versus "forecast-upside" theses; it supersedes the
direction-only aliases in evaluator/event/command-recovery code. INV-05, INV-06,
INV-41, and INV-43 remain binding: risk policy still actuates, point-in-time
truth is preserved, selected-side evidence remains mandatory, and no live price
band is weakened.

The observation repair records possession only after each fetch returns and adds
a typed-writer boundary that rejects any `imported_at` earlier than the hourly
bucket or its raw extrema print. Historical false timestamps remain evidence to
reconstruct or quarantine through sanctioned learning paths; this hot-fix does
not rewrite canonical history out of band.

## 2026-07-15 current-evidence coherence correction

A current 150-family source-clock census found 72 families where the absolute
ECMWF ENS member mean differed from the served provider center by more than the
entire persisted predictive sigma; 60 differed by at least 1°C. The live Los
Angeles July 16 HIGH carrier had `μ*=29.401°C`, ENS mean `24.965°C`, and claimed
`σ_pred=0.925°C`. The same raw ENS values were treated as absolute forecasts
for settlement-bin hit counts but only their centered population spread entered
`σ_pred`, silently deleting the 4.436°C current-model disagreement.

The first-principles correction retains the provider center, keeps the ENS
within-spread, and adds the absolute current ENS/provider-center displacement
as a third variance component in both predictive and center uncertainty. It
does not add a gate or historical calibration term; aligned evidence preserves
the original within-plus-between numeric decomposition.

Post-deploy proof then exposed an identity/redecision defect: only newly drained
seeds materialized the corrected formula, while otherwise-current active
families remained covered by pre-change posterior certificates. A complete
global auction therefore mixed probability semantics and selected a São Paulo
candidate from an old carrier. The repair must bind a shared current-evidence
semantics revision into the shape and posterior identity, make the existing
coverage/seed loop naturally re-materialize mismatched active families, and
refuse only a shaped certificate whose revision is not current. This is
probability identity, not deployment freshness and not a new runtime gate.

## Background

Current live source-clock posteriors display Normal point complements near
`0.999`, while the executable uncertainty carrier conditions on that Normal
family and can grant NO certainty unsupported by the finite current evidence.
The same path still applies a settlement-fitted historical far-tail q_lcb cap to
YES, contrary to the current-evidence-only probability law and the operator's
first-principles constraint.

## Scope

Money path: current source shape -> settlement-preimage point q -> coherent
current-evidence band -> symmetric YES/NO samples -> global lower-CVaR order
selection. Harmonizes executable source, the active replacement authority, and
test topology under INV-06 and INV-41; supersedes no independent authority.

The live proof loop also owns one execution-liveness defect discovered after
deployment: command recovery opened TRADE as MAIN with WORLD attached, while
price-channel opened WORLD as MAIN with TRADE attached. Concurrent
``BEGIN IMMEDIATE`` calls therefore reserved the two WAL writers in opposite
orders. The repair must make command-recovery hold the existing canonical
WORLD+TRADE live flocks for each short apply transaction; increasing timeouts or
editing processing rows is outside scope.

Fresh post-deploy stack evidence exposed the second half of the same inversion:
the held-position monitor could open a TRADE transaction, fetch a quote, then
persist a world-owned Day0 observation fact before releasing TRADE. The repair
must commit earlier monitor writes before probability refresh and delay the
trade-owned microstructure write until the WORLD observation write is complete.

Fresh forward runtime evidence at the London local-day boundary exposed a
probability-authority discontinuity: the monitor switched from a fresh
replacement posterior to mandatory Day0 observation at local midnight, before
EGLC had published the target day's first same-station observation. This made
one held position probability-stale and froze every otherwise-independent
global entry. Local midnight is not physical evidence: when canonical truth
positively proves that the target day still has zero observations, a held
position keeps the fresh replacement posterior until the first causal Day0
observation arrives. Generic observation faults do not prove an empty prefix;
they retain the bounded grace and then fail closed. Entry authority remains
grace-limited, and a stale replacement posterior is never promoted.

The same live proof window exposed an execution-truth discontinuity after a
reduce-only YES exit: a positive but partial MATCHED order fact was promoted to
`FILL_CONFIRMED`, although its cumulative filled size did not cover the command
size.  The repair must keep a partial exit command and position in their
nonterminal states until cumulative canonical trade facts cover the submitted
shares.  Chain-only dust remains separate reconciliation evidence; it must not
retroactively turn a partial order fact into full-fill proof.

Fresh 2026-07-14 auction evidence exposed a probability-variable mismatch in
the global preparation seam.  A Day0 family was made conditional on a current
full-day replacement posterior before the Day0 branch rebuilt the probability
of the final daily extreme from the current observed extreme and the forecast
hours that remain.  Those are different random variables.  The repair must let
the Day0 remaining-day authority bind directly to the current canonical
observation, current causal base snapshot, and current remaining-hour vectors;
it must not fabricate missing full-day ENS extrema or weaken replacement
readiness for any non-Day0 family.

Fresh order-outcome evidence on 2026-07-13 refuted three additional assumptions
at the decision/execution boundary:

- a reduce-only exit was refused solely because the running SHA differed from
  HEAD, even though the action could only decrease an already-held exposure;
- `WHALE_TOXICITY` bypassed the held-side probability and executable hold-vs-sell
  comparison, liquidating a Lucknow YES position that later paid $1;
- one confirmed-fill/chain-absence review position with no positive
  `chain_seen_at` made the global wealth witness throw, suppressing every
  unrelated family even though current chain cash remained known.

This slice treats those rows as falsification evidence, not as a historical
calibration corpus.  The first-principles contract is: a reduce-only action
must not be blocked by code-plane freshness; an order-flow observation may
modify evidence but cannot independently decide liquidation; and an uncertain
legacy claim contributes at most a conservative terminal-wealth upper bound,
never spendable cash and never a portfolio-wide entry veto.  INV-01, INV-05,
INV-06, INV-21, and INV-41 remain binding.  This harmonizes
`architecture/capabilities.yaml`, the executable gate, the canonical portfolio
read model, and the current global auction witness; it supersedes no separate
authority surface.

## Deliverables
- Enforce INV-43 at the real venue envelope boundary: every live BUY/SELL,
  entry/exit, single/batch unit price must be inside inclusive `[0.05, 0.95]`.
  No q-kernel, current-state, strategy-tail, risk, or order-role exception may
  waive it; rejection occurs before command persistence where possible and
  always before SDK contact.
- Keep Normal `q_json` as an immutable point estimate, never as executable certainty.
- Widen the shared simplex carrier by the exact 51-member zero-hit limit and the
  distribution-free Cantelli limit from current mean/variance.
- Remove historical far-tail floors from the source-clock route; preserve them
  only for explicitly non-source-clock compatibility paths.
- Preserve Day0 absorbing physical facts as dominant.
- Commit, deploy through the official restart path, then prove the result from a
  newly materialized canonical posterior and live auction/order receipts.
- Re-materialize every current active family when the current-evidence
  semantics revision changes; entry and monitor readers must not consume an
  older shaped certificate during that convergence window.
- Remove the WORLD/TRADE writer-order inversion that prevents the corrected
  probability carrier from reaching live redecision.
- Make held-position probability refresh order explicit: release TRADE, write
  the current Day0 WORLD fact, then write TRADE quote/monitor evidence.
- Preserve probability continuity before the first target-day observation:
  a typed canonical zero-observation result keeps a fresh replacement posterior
  as held-position monitor authority even after the coverage grace; this is not
  permission to add risk, use stale forecast belief, treat a generic source
  failure as zero observations, or ignore any available Day0 observation.
- Require cumulative canonical EXIT fill quantity to cover the command and the
  current position before lifecycle alignment may emit `FILL_CONFIRMED` or
  economic close; cumulative order facts never stack on existing trade facts,
  and a tx-hash aggregate alias never stacks on its exact child trade facts in
  lifecycle finality, locked-token, journal, or capital-balance views.
- Bind the global candidate's canonical YES/NO side into the current-state
  submit certificate. A missing legacy route `side` must not become `UNKNOWN`,
  while any real certificate/candidate side mismatch remains fail-closed.
- Consume the sealed global current-state certificate consistently through
  opportunity-book admission, receipt validation, and final sizing. These
  surfaces use the global target cost/shares/max-spend envelope rather than
  re-require legacy family-route optimizer fields after global selection.
- At submit, let only the exact sealed global current-state winner bypass legacy
  fixed price/profit/density/win-rate/ROI floors. Bind aggregate admission to
  its `DecisionProofAccepted` audit and executor admission to the durable
  LIVE/VERIFIED actionable certificate; a recomputable identity hash alone is
  never authority.
- Serialize the selected global objective from its actual expected cost,
  robust EV, and robust delta-log-wealth; the receipt must not mark the winner
  unadmitted or synthesize legacy route-optimizer metrics.
- Remove deployment-SHA/worktree freshness from `reduce_only_exit_submit` only;
  new-entry submit remains freshness-gated and the reduce-only lane remains
  constrained by its existing kill-switch, settlement-freeze, and risk policy.
- Make `WHALE_TOXICITY` observational evidence only.  It must not bypass fresh
  probability, executable bid, CI reversal, or hold-vs-sell economics.
- Preserve global redecision when a canonical confirmed-fill dispute lacks a
  current positive chain timestamp: exclude the disputed claim from spendable
  inventory, retain only its maximum payoff in the conservative wealth ceiling,
  and bind that uncertainty into the wealth identity.
- Keep stale held-position probability fail-closed for economically actionable
  exposure, but do not convert a fully evidenced sub-minimum `pending_exit`
  dust claim into a portfolio-wide entry veto. The health surface remains
  degraded and visible; only entry authority scopes the failure to the dust
  position when every stale sample is exactly covered by the sub-min surface.
- Recover a confirmed logical venue fill exactly once when command redecision
  writes a later `ExecutionCommandCreated`: select the same latest command and
  acknowledgement rows as the ledger, clamp only the EDLI ledger timestamp to
  that causal boundary, preserve the raw venue observation time in payload,
  and collapse MATCHED/MINED/CONFIRMED revisions by `trade_id`.
- Remove full-day replacement readiness from the Day0 remaining-day probability
  branch only.  Bind its witness identity to the current observation, current
  causal base snapshot, remaining-model capture identities, finite-evidence
  carrier, and exact sample matrix; preserve all non-Day0 replacement gates.
- Rebind the sealed global winner's current-state economics atomically at JIT:
  `q_lcb`, edge, false-edge rate, prefilter verdict, and admission reason must
  come from one certificate.  A superseded scalar admission reject must not
  survive beside a positive globally certified edge.

## Work record — INV-43 recovery (2026-07-15)

- Git forensic found no reset/rebase/drop and no commit on any ref containing
  `LIVE_ORDER_UNIT_PRICE_MIN`: the 2026-07-13 implementation remained an
  uncommitted worktree slice and was overwritten before deployment.
- Paris 35C canonical evidence proved the consequence: market `2888967`
  accumulated `5106.247161` NO shares at chain average `$0.0039`; every loaded
  SHA during the submit window lacked INV-43.
- Recovery owns the envelope contract, executor/aggregate/qkernel seams,
  strategy registry, invariant/authority/reference surfaces, and direct
  single/batch/entry/exit antibodies. Canonical DB content is read-only.

## Verification
- INV-43 focused venue/entry/exit/current-state antibodies: `22 passed`;
  architecture contracts: `97 passed`; invariant citations, planning evidence,
  `py_compile`, and `git diff --check`: passed. The broader three-file
  qkernel/aggregate/economics slice produced `249 passed, 1 failed`; the lone
  failure is an existing 0.10 strategy-floor expectation for an in-band 0.0538
  order, not an absolute-band bypass.
- Focused first-principles antibody and settlement-preimage regressions pass.
- All carrier rows sum to one; NO lower-CVaR is the pointwise complement and does
  not exceed `1 - q_ucb_required`.
- Pure builder over current canonical Guangzhou 39C inputs changes executable NO
  confidence without changing its Normal point q.
- Existing global capital-optimality evaluator passes; fresh runtime evidence is
  required separately from tests.
- POSIX WAL-byte evidence shows no simultaneous opposite-order WORLD/TRADE
  writer hold after restart; reactor cycles progress beyond claim bounces.
- A deterministic local-midnight antibody proves fresh replacement belief stays
  continuous for held-position redecision while canonical truth proves zero
  observations; entry remains grace-limited, and stale belief or a generic
  post-grace observation failure is still rejected.
- A partial MATCHED EXIT antibody proves the command remains PARTIAL and the
  position remains pending exit; the full-size sibling still closes exactly
  once.
- A stale/dirty HEAD antibody proves new entry is still refused while the exact
  reduce-only capability is admitted.
- Lucknow-shaped YES and mirrored NO tests prove whale flow alone cannot force
  liquidation, while independent economic reversal and settlement evidence
  still exit.
- A Shanghai-shaped confirmed-fill dispute with missing `chain_seen_at` proves
  current cash remains selectable, the claim is not spendable, and its maximum
  payoff is represented only in the wealth ceiling.
- A Wellington-shaped stale `pending_exit` claim below the current venue minimum
  does not block unrelated entry families; the same stale claim in `active`
  phase, an incomplete sample, or any non-dust stale position still blocks.
- Mirrored YES/NO submit antibodies prove the same sealed-global rule admits
  both sides, while a legacy payload with recomputed current-state markers still
  hits the legacy floor because it does not match durable decision authority.
- A production-shaped REST fill antibody proves two command rows times two
  trade-status rows become one `UserTradeObserved`; a WS confirmed re-report
  antibody proves the same logical-fill identity, and non-fill states remain
  ineligible for recovery.
- A Day0 global-preparation antibody proves that missing full-day replacement
  readiness cannot block a complete current observation + remaining-day
  witness, while the same missing readiness still blocks a forecast event and
  missing Day0 current inputs still fail closed.
- A production-shaped Day0 global-winner antibody starts with the legacy
  `q_lcb=0` capital-efficiency reject and proves that current-state rebinding
  updates its FDR inputs as one unit before submit preflight.

## Work record

- 2026-07-17: the first post-fill Manila auction exposed a deterministic
  projection-lag window: the authenticated fill was already canonical and the
  wallet snapshot already bounded its cash effect, but `chain_shares` remained
  zero until the next reconciliation pass.  Current wealth now uses an exact
  CTF balance when the current chain snapshot carries the token; otherwise it
  admits only canonical `venue_confirmed_*` shares as a non-spendable maximum
  claim.  Unverified submitted shares remain invalid, so the repair removes a
  throughput gap without turning uncertain inventory into cash.

- 2026-07-17: the Milan authenticated fill had no `venue_order_facts` row, so
  terminal reservation settlement released the whole PUSD reservation with
  `converted_amount=0` even though canonical trade facts proved the fill.
  Reservation conversion now takes the larger of cumulative order matched size
  and the sum of exactly-once economic trade fills.  The shared alias reducer
  excludes EDLI/tx-hash duplicates before summing; tests prove both no-order-fact
  conversion and partial-fill alias deduplication.

- 2026-07-17: the first post-deploy authenticated Tokyo fill exposed a
  command-recovery gap.  The command remained `REVIEW_REQUIRED` after its
  point-order read failed, while canonical `venue_trade_facts` proved the full
  31.6-share fill and the EDLI projection wrote a terminal `execution_fact`
  with no `filled_at`.  RiskGuard correctly rejected that malformed exposure
  authority, then became stale and forced global reduce-only behavior.  The
  repair admits authenticated trade facts as fill-time authority when an order
  fact is absent, collapses the EDLI derived alias onto its cited source trade
  fact, and writes one canonical per-command execution fact at the real venue
  timestamp.  It does not edit canonical rows manually, invent wall-clock fill
  time, weaken strict exposure validation, or count the same economic fill
  twice.

- 2026-07-17: the same Tokyo fill also left its venue command in
  `REVIEW_REQUIRED`, so the canonical PUSD reservation stayed open and every
  subsequent global auction failed closed with
  `CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS`.  Command recovery now lets one
  authenticated `WS_USER` `CONFIRMED` full-fill fact plus a matching active,
  synced projection cross the existing review boundary through a formally
  validated `FILL_CONFIRMED` proof.  The proof deduplicates the EDLI alias,
  binds the exact command/order/size and venue time, then uses the canonical
  terminal-event seam to convert the reservation; it does not mutate the live
  DB out of band or infer a fill from portfolio state alone.

- 2026-07-17: command terminalization then exposed a second exactly-once gap:
  the native Tokyo fill, its EDLI `source_trade_fact_id` alias, and a later REST
  re-observation projected one 31.6-share economic fill as 63.2 local shares
  while chain truth remained 31.6.  The shared economic reducer now validates
  the EDLI pointer against the append-only source fact, independent of which
  later revision becomes canonical, and entry reconciliation consumes that
  reducer just as exit reconciliation already did.  A production-shaped
  re-observation test proves the canonical projection converges from the
  contaminated 63.2/$37.92 state back to 31.6/$18.96 without an out-of-band DB
  edit.

- 2026-07-14: post-deploy global auctions covered all 128 current families and
  repeatedly selected Sao Paulo Jul 14 HIGH NO with positive robust delta-log
  wealth, but preflight inherited `passed_prefilter=false` and the old
  `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:q_lcb=0` reason after rebinding the
  candidate to `q_lcb=0.575`.  No venue command was created.  The repair makes
  the current-state proof overlay atomic; book, probability, cash, wealth, and
  venue safety revalidation remain fail-closed.
- 2026-07-14: later 129/129 epochs selected the same Sao Paulo family at a
  full-depth NO cost of `0.999`, with Day0 win-probability LCB `1.0`, positive
  robust EV, and positive delta-log wealth.  Targeted refresh then switched the
  legacy local proof to its fixed near-settled-price rejection, so exact
  condition/token/direction binding failed before the current-state certificate
  could be evaluated.  The JIT seam now recovers only scalar gates that the
  sealed global certificate can replace; evidence/structure rejections remain
  non-rebindable.
- 2026-07-14: canonical global-auction receipts retained complete scope scanning
  but excluded Hong Kong Jul 15 LOW and Miami Jul 14 LOW because their full-day
  ENS extrema were temporally boundary-ambiguous.  Both were Day0 families with
  current observations and remaining-hour forecast inputs.  The exclusion was
  therefore traced to an incorrect full-day-posterior dependency, not to an
  absent current Day0 probability variable.  No historical fit or synthesized
  member value is admitted by the repair.
- 2026-07-14: Paris Jul 14 HIGH 35C NO produced a real FOK match: the command
  requested 90 shares at a `0.012` limit, while the venue confirmed
  `99.726666` shares at `0.011` with a Polygon transaction hash.  Wallet and
  chain projections synchronized the exposure, but command recovery kept the
  row in `REVIEW_REQUIRED` because it required fill price equality.  A limit is
  a one-sided economic bound, not an equality: BUY fills may improve below it
  and SELL fills may improve above it.  Recovery and restart priming now share
  that side-aware rule while retaining exact token, side, time, unique trade,
  bound order ID, open-order absence, confirmed status, and residual proofs.

- 2026-07-11: live rows isolated the source-clock YES historical-floor / NO
  near-one asymmetry.
- 2026-07-11: first implementation's zero-hit-only member bound was rejected:
  current member values now remain in-memory and exact settlement-preimage hit
  counts drive Clopper-Pearson UCBs; provenance persists their hash/count/hits.
- 2026-07-11: current canonical posterior 32089 / snapshot 1203438,
  Guangzhou Jul 12 39C, has 0/51 hits, Normal NO point 0.999915, but current-
  evidence NO LCB and 5% lower-CVaR 0.933965; all 400 carrier rows remain simplex.
- 2026-07-11: focused antibody 3/3, current source-clock contracts 7/7, and
  global capital-optimality evaluator 226/226 passed before final deploy audit.
- 2026-07-11: live WAL byte-range locks isolated an execution deadlock:
  price-channel held WORLD while main held TRADE; command recovery and
  price-channel used opposite MAIN/ATTACH order. No DB rows were edited.
- 2026-07-11: post-deploy SIGUSR1 stack pinned the remaining inversion to
  `exit_monitor -> refresh_position -> write_day0_metric_fact`: TRADE was open
  before the WORLD fact write while price-channel held WORLD and awaited TRADE.
- 2026-07-11: at London 00:00-00:14 local, WU returned HTTP 200 but no EGLC
  sample belonged to the new target date; aviationweather's latest EGLC METAR
  was 22:50 UTC / 23:50 local. The monitor therefore recorded three consecutive
  stale probabilities despite a fresh replacement posterior, and live health
  globally excluded multiple independent positive-EV YES candidates.
- 2026-07-11: Lucknow EXIT command `23c4fa3771644e43` submitted 60 shares,
  while its canonical trade fact proved only 46.59 shares at alignment time;
  command recovery nevertheless emitted `FILL_CONFIRMED`.  Current chain/data
  truth later exposed 0.91 share as quarantined dust, so order-fill proof and
  residual reconciliation must remain separate.
- 2026-07-11: live DB inspection found eight EXIT commands where one real fill
  appeared as both `trade_id=tx_hash` and an exact child trade ID. The old
  canonical sums were exactly 2x command size; the economic identity reducer
  maps all eight back to exactly 1x while retaining distinct child trade IDs.
- 2026-07-11: after monitor freshness recovered, Madrid YES reached actual
  submit quality but was rejected as `side=UNKNOWN:direction=buy_yes`. The
  sealed global certificate carried the global candidate and direction but did
  not copy the candidate's typed YES/NO side; the final check was therefore
  testing an unproduced legacy field rather than the selected order identity.
- 2026-07-11: after side binding deployed, Madrid passed that boundary and then
  failed certificate construction with
  `EDLI_LIVE_OPPORTUNITY_BOOK_SELECTED_MISSING`. The book admission and sizing
  consumers still validated only legacy family-route fields even though T2/T3
  had already sealed a global current-state utility certificate.
- 2026-07-13: Cape Town 20C NO and Guangzhou 39C NO expired worthless after
  near-one source-clock beliefs; Lucknow 35C YES was sold by the unconditional
  whale trigger before paying $1; Wellington 11C NO exited after current belief
  reversal and avoided the terminal loss. These are current falsification cases
  for the decision shape, not inputs to a fitted historical error floor.
- 2026-07-13: Guangzhou emitted repeated EXIT intents that were refused only by
  `reduce_only_exit_deployment_freshness_mismatch`. Current runtime inspection
  reproduced the condition from the loaded-SHA/HEAD difference.
- 2026-07-13: global entry retries isolated one contradictory projection:
  Shanghai 29C NO remained `synced` with positive `chain_shares` but blank
  `chain_seen_at` while emitting `entry_authority_chain_absence_conflict`.
  Current chain cash was fresh; the prior witness converted this one disputed
  claim into a portfolio-wide exception.
- 2026-07-13: implementation now keeps deployment freshness on new-entry
  `live_venue_submit` while removing it from reduce-only submit, demotes whale
  flow to observation, and represents unresolved local claims only in the
  terminal-wealth ceiling. A live canonical read-only replay produced
  floor/spendable `$1146.300538` and ceiling `$1361.238629` across 13 open
  positions without the prior chain-time exception.
- 2026-07-13: independent `gpt-5.6-sol` read-only review found that deleting
  the legacy reduce-only freshness error classifier would leave already-
  persisted retries cooling for up to 15 minutes. The compatibility classifier
  and antibody were restored without restoring the gate. The reviewer then
  re-read the final diff, verified mixed current balances plus uncertain claims,
  and returned PASS with no material finding.
- 2026-07-13: two post-deploy reactor cycles proved the wealth exception fixed:
  `CURRENT_WEALTH_POSITION_CHAIN_TIME_INVALID` disappeared. The sole remaining
  global entry blocker was one stale Wellington `pending_exit` dust claim with
  `0.00818` share against a venue minimum of `5`.
- 2026-07-13: the first dust-scoping implementation was rejected by independent
  `gpt-5.6-sol` review: all health queries reported a display-limited sample
  length as count, so an unseen eleventh actionable row could have been hidden;
  zero-count categories also failed to require an explicit empty sample. The
  producer now emits exact counts plus explicit truncation facts while retaining
  bounded display samples. Entry scoping requires every monitor and sub-min set
  to be complete, non-truncated, count-consistent, and ID-covered; missing or
  truncated evidence remains fail-closed. A second independent review then
  rejected stale venue-minimum evidence: sub-min coverage now requires the
  snapshot deadline to remain fresh at the entry decision. A durable canonical
  `MARKET_CLOSED_HOLD_TO_SETTLEMENT` event is the separate absorbing proof that
  a closed market needs settlement, not a fresh probability or a sell attempt.
  The Wellington token returned current CLOB `/book` 404, matching its latest
  canonical closed-hold event; neither fact is relabeled as a fresh book.
  A final non-object JSON antibody closed the last producer exception path;
  malformed JSON now yields a degraded read-unavailable surface. Independent
  `gpt-5.6-sol` follow-up returned PASS with no finding. Focused antibodies
  passed 17/17, the two affected test modules passed 223/224 with one pre-existing Day0 fragile-
  edge expectation failure, and the unchanged capital-optimality evaluator
  passed 258/258.
- 2026-07-13: the first fully healthy post-restart auction reached the NO bound
  certificate and rejected all 21 candidates with
  `parent_probability:side_q_lcb_served`. The global selection binder copied an
  already tightened tail LCB back into the field named `pre_qkernel_q_lcb_5pct`,
  overwriting the immutable replacement-served NO bound while leaving the
  signed certificate unchanged. The current-state tail may tighten executable
  economics, but it cannot rewrite its parent. The binder now preserves the
  existing pre-qkernel value and only synthesizes it for legacy proofs where the
  field is absent.
- 2026-07-13: Helsinki Jul 14 25C NO became an actual 5-share FOK fill at
  `$0.48` (`$2.40` spend), with decision-time `q_live=0.819330` and
  `q_lcb=0.696391`. Command recovery moved the durable command from
  `REVIEW_REQUIRED` to `FILLED` after canonical chain trade confirmation; the
  position then entered normal active monitoring.
- 2026-07-13: a later current global auction selected Jeddah YES (5 shares,
  `$0.44` limit) ahead of every NO alternative, proving YES participation in the
  same executable universe. Submit revalidation then rejected it only because
  the legacy absolute expected-profit floor required `$1` while the sealed
  global certificate remained positive in robust delta-log-wealth and robust
  fee-aware EV. This was a downstream objective mismatch, not a probability or
  side-selection failure.
- 2026-07-13: the first floor-alignment diff was rejected by independent
  `gpt-5.6-sol` review because a caller could add arbitrary current-state markers
  to legacy economics and recompute the public hash. Submit bypass now requires
  exact equality with the decision aggregate's qkernel audit and the durable
  LIVE/VERIFIED actionable payload, plus the full global witness, terminal-payoff,
  utility, and optimum grammar. Follow-up adversarial probes found and closed a
  one-way current-to-legacy intent downgrade and a route-less global payload q
  binding gap; global certificates no longer need legacy optimizer fields in
  either aggregate or executor. Mirrored YES/NO, recomputed-marker, downgrade,
  and route-less q-drift antibodies pass; affected modules pass 155/155; the
  declared capital-optimality evaluator passes 258/258. The actual 82-field
  Jeddah YES certificate validates against the final shared global grammar.

## 2026-07-18 alpha-clock fault containment slice

Current runtime evidence and source tracing show that family probability
preparation is serial inside one global auction.  The adapter currently erases
the distinction between a transient SQLite lock on one family and an unknown
runtime failure; the batch therefore aborts before evaluating siblings
that already have current probability and book authority.

The fault boundary for this slice is one weather family.  A recognized SQLite
`locked`/`busy` failure makes only that family's current authority unavailable
for the epoch, records the exact exclusion in the global receipt, and continues
selection over the remaining complete admissible set.  Family-local missing
current authority uses an explicit `FamilyAuthorityUnavailable` reason allowlist;
generic `ValueError`, unknown `OperationalError`, schema drift, malformed
identity/time/simplex contracts, and every unclassified exception remain
whole-epoch fail-closed.  No stale probability fallback, synthetic q, risk-gate
relaxation, DB mutation, or venue action is permitted.

Files: `src/engine/event_reactor_adapter.py`,
`src/engine/global_batch_runtime.py`,
`tests/integration/test_w3_solve_seam_g3.py`,
`tests/events/test_transient_money_path_requeue.py`, and this plan.  Acceptance
requires an adapter-to-batch two-family counterexample where the unaffected
family still wins, preservation of whole-epoch rejection for contract, schema,
and unknown preparation errors, focused tests, planning-lock, compilation, and
`git diff --check`.  Deployment remains operator-only.

Verification: independent review found and closed the original generic
`ValueError` downgrade gap.  The final full W3 global-auction seam passes
`197/197`; the money-path retry suite passes `43/43`; the focused adapter-to-batch
lock and contract/schema/unknown-error counterexamples pass `12/12`.
Planning-lock, compilation, and `git diff --check` pass.  The existing repo-wide
source registry check still reports unrelated baseline drift.  No canonical DB
was copied or mutated, and no process restart, config change, or venue action was
performed.

## 2026-07-18 Day0 action-lane fault containment

The durable Day0 wake previously made targeted held-position monitor success a
strict prerequisite for processing the same observation event.  A monitor DB,
quote, or lifecycle failure therefore blocked both the dead-position SELL lane
and every sibling BUY/HOLD/CASH redecision even when the reactor still had
current family authority.

The wake now owns two independent completion conditions.  A failed or incomplete
targeted monitor keeps the durable wake pending, but no longer blocks the event
reactor from consuming the committed observation.  Once the event is terminal,
the next poll retries only the targeted monitor and does not repeat the reactor;
the wake is acknowledged only after both lanes complete.  A monitor already in
flight remains a concurrency boundary, and every downstream submit, capital,
risk, freshness, and unknown-side-effect gate remains unchanged.

Acceptance requires the existing monitor-before-reactor success ordering, a
counterexample for both `False` and exception monitor outcomes, proof that the
reactor runs exactly once, proof that the wake remains durable until monitor
recovery, and no regression to future-retry wake retirement.  The complete wake
listener suite passes `79/79`; the focused periodic-monitor preemption antibody
passes `1/1`; compilation and `git diff --check` pass.  No runtime process,
canonical DB, config, or venue state was changed.

The same trace found a redundant lock wait at the lane boundary: an urgent Day0
monitor waited up to 30 seconds for the active reactor even though the durable
wake already retries and the active reactor observes the urgent preemption flag.
Urgent handoff acquisition is now non-blocking; periodic monitor handoff retains
the existing 30-second bound.  This prevents the wake listener itself from being
occupied for tens of seconds while preserving mutual exclusion and durable
retry.  The targeted-handoff antibody proves a zero-second timeout.

## 2026-07-18 current-center scenario preservation

Four live losing NO certificates exposed a probability-geometry defect rather
than a YES/NO complement defect. Manila, Panama, and Taipei served target-bin
YES upper bounds almost identical to their point estimates even though the
current provider center sat near the eventual winning exact bin and the ENS
center materially disagreed. The old executable band folded that disagreement
into one wide predictive Normal, then bootstrapped only the provider center;
for an exact bin near that center, extra width lowers its mass and therefore
cannot express the competing current-evidence world. Singapore already carried
a materially wide target YES upper bound and remains an acknowledged stochastic
loss, not a sign/complement error.

The correction preserves the strategy-of-record point q and changes only its
current-evidence ambiguity band. In addition to exact-member CP and Cantelli
floors, every bin now receives the maximum probability licensed by two observed
current scenarios: provider center plus ENS within-spread, and ENS center plus
the same within-spread. The existing coherent-simplex stress carrier transports
those marginal UCB requirements into symmetric YES/NO bounds and lower-CVaR.
No historical fit, price anchor, constant probability floor, or admission gate
is introduced. Both same-cycle and transported-shape revision identities bump
so stale rows must be naturally rematerialized before money-path use.

Acceptance requires a regression proving point q is unchanged while provider-
center exact-bin mass widens q_ucb enough to reject the mirrored NO at its old
cost; coherent sample rows must still sum to one; the four frozen live cases
must be replayed read-only; focused probability, cycle-policy, and global Kelly
endowment tests must pass; then standard live deploy must load the committed SHA
and produce the new semantics revision without forced orders.

Read-only replay against the exact four entry posterior identities proves the
change is selective. The target-bin YES point q remains byte-for-byte unchanged.
Manila q_ucb widens `0.239724 -> 0.558916`, so mirrored NO q_lcb becomes
`0.441084 < 0.568891` executable cost; Panama widens
`0.216604 -> 0.640282`, so NO becomes `0.359718 < 0.631780`; Taipei widens
`0.131232 -> 0.616159`, so NO becomes `0.383841 < 0.602095`. All three old
orders therefore fail the robust probability objective before Kelly sizing.
Singapore remains `q_ucb=0.308017` and `NO q_lcb=0.691983 > 0.552420`; it was
already an explicitly bounded stochastic risk and is not falsely rewritten by
the disagreement correction. Focused probability and revision tests pass
`20/20`; the three open/in-flight entry endowment antibodies pass `3/3`.

## 2026-07-18 selected Day0 deterministic-payoff preflight

The first post-deploy complete auction selected Shenzhen July 18 HIGH 30C NO,
then preflight rejected it as `DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE`. Current
authorized station evidence had already observed 31C, making the exact 30C bin
pathwise impossible: its YES payoff is zero and NO payoff is one regardless of
any remaining-hour forecast.

The global probability path already builds a `DeterministicBinPayoffWitness`
for that first-principles fact, but selected-bin preflight compared the required
bin-id string with the full `(bin_id, payoff)` tuple collection. That comparison
can never match, so the selected dead bin fell through to the unrelated
remaining-day probability path. The repair compares like types: required bin id
against the set of deterministic bin ids. Still-live bins continue to require a
current remaining-day witness; source, observation, topology, and submit checks
are unchanged. Acceptance requires the selected dead-bin witness to avoid the
remaining-day reader, a still-live sibling to keep using it, symmetric HIGH/LOW
hard-fact tests, the full W3 seam, compilation, and `git diff --check`.

## 2026-07-19 typed chain-only automatic resolution

Packet class: schema/truth-contract slice.  This remains inside the active
capital-gains packet because a stale family-scoped `CHAIN_ONLY_UNKNOWN_ASSET`
review debt still blocks an otherwise current held family after fresh chain
reconciliation proves the exact local token and size match.  The money path is
`fresh chain snapshot -> canonical reconciliation -> review/suppression state ->
portfolio and exchange drift consumers -> global redecision`.

Objective: represent the fresh exact-match transition as append-only canonical
truth without granting a permanent token ignore or hiding a future chain/local
drift.  This changes the `token_suppression` reason CHECK vocabulary and its
consumer classifications; it does not add a lifecycle phase, venue command,
probability rule, capital gate, or operator override.

Why not the smaller alternatives:

- query-time hiding cannot prove fresh chain truth and leaves the OPEN review
  item as an independent family block;
- resolving only the review item leaves the latest suppression row classified
  as external and causes exchange reconciliation to swallow future drift;
- `operator_quarantine_clear` would forge human provenance and permanently
  suppress future chain-only rediscovery.

Truth layer: `token_suppression_history` plus typed `ReviewWorkItem` state in
the canonical trade DB.  Control layer: only a complete/fresh `CHAIN_SYNCED`
exact held-token match may atomically append
`chain_only_auto_resolved_match`, resolve only OPEN
`CHAIN_ONLY_UNKNOWN_ASSET` debt for that token. The savepoint mutates canonical
DB state only. The current cycle conservatively retains its old in-memory
`ChainOnlyFact`/ignore projection; only a later canonical reload after the outer
transaction commits may remove it. Evidence layer: schema parity plus counterexamples
where the local position later disappears or drifts and therefore must be
reported again.

Zones and invariants: K0 schema/truth vocabulary and K2 reconciliation;
INV-03 append-first authority, INV-08 one transaction boundary, INV-09 missing
chain truth as a first-class fact, and INV-37 single-DB transaction discipline.
Required reads are root/scoped AGENTS, the K0 zero-context authority spine,
`docs/authority/zeus_current_architecture.md`,
`docs/authority/zeus_current_delivery.md`,
`docs/authority/zeus_change_control_constitution.md`, and the state/contracts/
execution module books.

Allowed implementation surfaces are the exact state, contract, execution,
migration, kernel-schema, registry, packet, and focused test files enumerated in
this packet's `scope.yaml`.  Direct/manual writes, copies, or backups of any
canonical DB are forbidden.  No probability, Kelly, sizing, submit, risk,
control, lifecycle phase, or venue-order surface may change.

Schema contract:

- accepted reason vocabulary adds `chain_only_auto_resolved_match`;
- review-resolved, non-resurrectable-ignore, and external-drift-suppression
  reason sets are distinct; the automatic reason belongs only to the first;
- fresh-kernel, legacy mutable-table, and B071 history/view schemas migrate
  transactionally and idempotently while preserving history ids, operations,
  timestamps, views, triggers, and indexes;
- exact proof requires non-empty and equal chain, local-position, and suppression
  condition identities in addition to exact aggregate shares;
- repeated exact matches do not append duplicate history; any failure or caller
  rollback restores both DB facts, while in-memory state remains conservatively
  unchanged until a committed canonical reload.

Acceptance requires focused migration/reconciliation/exchange tests, full
affected test files, `py_compile`, `git diff --check`, planning-lock evidence,
and independent adversarial review.  Parity is schema-row and consumer-behavior
parity rather than market replay because probability and execution economics do
not change. A deployment is the human-gated migration cutover: only the standard
`scripts/deploy_live.py restart all --allow-unpushed` path may apply it. That
path unloads live trading and every prerequisite before starting any new
live-money process. Its stopped-process restart recovery calls the typed trade
schema helper, which first runs `init_schema_trade_only` and then widens the
backward-compatible CHECK inside a dedicated transaction before any daemon is
restarted. A failed migration aborts recovery and leaves live trading stopped.
Only after that does deploy verify prerequisite code identity and start the trade
daemon, so the new reason cannot be emitted against the old three-value CHECK.
The deploy path must then prove loaded SHA, first queue/monitor progress and only
then restore its own temporary restart guard. No out-of-band migration command
or canonical DB write is allowed. Post-start evidence must include canonical
schema/row, chain, review-gate, auction, monitor, venue, and capital facts.
Rollback is the entire slice together;
if an automatic-match row already exists, rollback must first map it
fail-closed to `chain_only_quarantined` rather than loading it under an older
three-value schema.

Pre-deploy verification: reconciliation/review/exchange suites passed 169
tests; fresh/legacy/B071 schema tests passed 13; the full ops-script smoke file
passed 78; money-path semantic CI passed 10. Mutable-table and B071 alias-view
trade DB fixtures both pass the stopped deploy recovery helper, including
history identity/metadata and view/trigger/index preservation. Schema
fingerprint, source-rationale delta, planning lock, YAML load, compilation, and
diff whitespace checks pass. Independent adversarial re-review is PASS. The
broader architecture/hygiene baseline retains two unrelated failures: a
wall-clock-aged reconciliation fixture and the existing TIGGE AST metric-stamp
fixture; neither intersects this slice.

## 2026-07-19 deterministic Day0 authority continuity

Fresh complete-auction receipts selected a positive Hong Kong July 20 HIGH
29C NO action from a `DeterministicBinPayoffWitness`: the authorized current
observation had already made the exact 29C YES payoff zero, so the selected NO
payoff and both selected probability bounds were one.  No venue command was
created because the certificate bridge relabeled every global Day0 probability
as remaining-window probability and the shared Day0 verifier recognized only
replacement and remaining-window q sources.

This is a typed-authority continuity defect, not permission to weaken live
admission.  The repair must preserve the deterministic witness identity, exact
YES-payoff map, current observation binding, and selected condition/bin/side/q
through receipt projection, calibration certification, actionable validation,
and pre-submit validation.  It may admit only binary exact payoffs whose
selected YES/NO complement equals both `q_live` and `q_lcb`; missing, mixed,
nonbinary, mismatched, or relabeled evidence remains fail-closed.  Ordinary
remaining-window candidates continue to require current remaining models and
their existing transform.  No price threshold, Kelly multiplier, risk level,
operator control, venue command, or canonical DB state changes in this slice.

Allowed additional implementation surfaces are
`src/events/day0_authority.py`, `src/decision_kernel/verifier.py`,
`tests/engine/test_event_reactor_live_qkernel_gate.py`, and
`tests/engine/test_cert_calibration_bridge.py`, plus the existing shared
calibration-authority predicate antibody, as enumerated in the packet
scope. Acceptance requires producer-to-actionable and calibration-certificate
positive tests, missing/mixed/payoff-side/q-drift counterexamples, existing
remaining-window regressions, compilation, diff checks, independent
adversarial verification, one conventional commit, standard live restart, and
fresh natural auction/venue reconciliation.  A model EV is not realized PnL;
post-deploy reporting must keep selected economics, venue command/order/fill,
and settlement evidence separate.

Independent review first refuted the initial bridge: synchronized payoff/side/q
edits could retain an opaque stale witness identity, and a nested observation
could claim a different q source.  The corrected certificate now carries the
complete ordered bin-to-condition-to-YES/NO-token bindings and every input of
the canonical deterministic witness identity.  Pre-submit reconstructs that
witness, recomputes the exact-payoff sample identity, matches the selected
native token to bin and side, and compares every present q-source copy.  The
production-shaped fixture uses Hong Kong's actual `HKO`/`hko` settlement source.

Focused engine, calibration bridge, shared verifier, certificate, solver,
monitor, and symmetry suites pass `436/436`; the full W3 integration file passes
`221` tests and retains two unrelated pre-existing `epoch_superseded()` fixture
failures.  Compilation, YAML parse, changed-test topology filtering, and diff
whitespace checks pass.  The environment has no Ruff executable; Pyflakes still
reports the adapter's pre-existing dynamic/type-only names, with no new finding
in the deterministic authority implementation.  Independent re-review passed
the Day0 slice: both original attacks are rejected, `202` focused authority
tests and `6` key integration tests pass.  Its overall release verdict remains
red only because the same two unrelated W3 `epoch_superseded()` baseline
fixtures fail outside this diff; that residual is reported separately rather
than treated as evidence against this authority chain.

## 2026-07-19 Day0 held-SELL family completeness

Fresh production receipts falsified the monitor-to-auction handoff for three
Day0 positions.  Tokyo 35C NO and Kuala Lumpur 29C YES / 32C NO were refreshed
by the held monitor, yet each appeared zero times in hundreds of applicable
global-auction receipts.  The probability preparer had replaced the complete
conditional family simplex with a partial deterministic witness as soon as any
sibling bin became pathwise dead.  Book capture therefore saw only those dead
siblings and could not materialize the unresolved held legs as SELL candidates.

Whole-family preparation must retain a coherent remaining-day joint witness
whenever unresolved siblings exist.  A partial deterministic witness is valid
only for an explicitly required exact condition, and JIT revalidation must
preserve the selected witness kind rather than silently switching authority.
Every hard-fact payoff carried inside the joint witness must remain exactly
zero or one in both its sample column and point probability; conflict fails
closed.  This changes neither Kelly, price policy, operator control, risk level,
nor venue submission.  Acceptance requires a mixed exact/unresolved Day0 family
test, joint-witness JIT continuity, hard-fact conflict rejection, standard live
restart, and new receipts proving each affected position is represented as a
SELL evaluation or an explicit typed exclusion.  A positive SELL may execute
only through the ordinary robust objective and reduce-only pre-submit path.

## 2026-07-19 Fractional Kelly minimum-lot boundary

The minimum marketable lot does not authorize added risk above the configured
Fractional Kelly terminal-holding target. A positive continuous solution whose
remaining fractional target is smaller than the venue minimum therefore
chooses CASH with
`FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT`; it must not round the order up to
the venue lot or substitute full Kelly. This is the direct correction for the
live minimum-lot entries that converted a small fractional target into a larger
binary loss budget. A venue minimum is an execution constraint, not an alpha
source or a risk exception.

## 2026-07-19 narrow-wake auction evidence continuity

Targeted producer wakes were replacing the process-global book cache with only
their narrow family scope.  A later disjoint wake therefore had to rebuild the
same broad venue universe and could omit otherwise fresh families from the next
bounded decision window.  The cache now atomically replaces the refreshed
family while retaining other current families, but it never renews their
freshness: the merged epoch expires at the earliest base or delta deadline, and
an already-expired delta is rejected before merge.  Replaced family topology
also removes its old Gamma metadata keys before current keys are installed.

The deterministic Day0 proof bundle now binds candidate evidence to the
selected native proof token rather than an optional family-candidate token.
Unknown proof-builder exceptions remain batch-fatal before any venue side
effect; they are not reclassified as family-local evidence and cannot authorize
a runner-up.  Focused cache, token, neg-risk, and preflight tests pass `44`; the
full W3 file passes `229` and retains only the two unrelated pre-existing
`epoch_superseded()` fixture failures.  Compilation and diff whitespace checks
pass.  Standard deployment and fresh natural auction/venue reconciliation are
still required; this continuity repair neither changes operator entry posture
nor authorizes a forced order.

## 2026-07-19 transient collateral refresh continuity

Canonical trade-ledger evidence showed intermittent `DEGRADED` refresh rows
between successful `CHAIN` snapshots.  A degraded row proves that refresh
failed; it does not prove zero collateral.  Global wealth selection therefore
uses the newest `CHAIN` or `VENUE` snapshot only while the auction's existing
freshness bound still accepts its own capture time.  Current reservations,
in-flight obligations, and portfolio claims are reread inside the same pinned
transaction, so cached cash cannot escape newer commitments.  No trusted
snapshot, a future capture, or an expired trusted snapshot remains fail-closed.

Live evidence at review time contained `94 CHAIN / 6 DEGRADED` rows in the most
recent 100 collateral refreshes.  Collateral-ledger suites pass `76`, focused
wealth tests pass `20`, executor collateral tests pass `2`, and the full W3 file
passes `229` with only its two known `epoch_superseded()` baseline failures.
Deployment and natural receipt evidence remain required.

## 2026-07-19 Day0 coverage truth and close-economics integrity

Current Day0 evidence can be numerous yet discontinuous. A gap overlapping the
metric's likely extreme window therefore cannot be called complete merely from
row count: entry must fail closed for the affected metric, while monitoring may
retain the one-sided physical bound only as non-actionable evidence until the
missing interval is bounded. An observed HIGH never moves down and an observed
LOW never moves up. HIGH and LOW attribution remains separate, HKO cumulative
observations use their own trailing-coverage semantics, and the canonical DB,
WU HTTP, and same-station fast-tail paths all derive continuity from exact
sample instants rather than first-sample time plus count. This changes neither
the settlement source nor the executable objective.

The same release set includes a truth-preservation correction at settlement.
When a real exit fill has already made a position `economically_closed`, a
later chain-mirror settlement may advance lifecycle but must preserve the
booked fill price and realized PnL. If either booked field is missing, the row
fails closed and remains economically closed for explicit recovery; the writer
must not manufacture zero PnL or a binary exit price.

Acceptance requires metric-specific coverage and monitor-bound tests, complete
and incomplete HKO cases, fresh/stale bound monotonicity, booked-close
preservation plus missing-field counterexamples, test-topology registration,
the full W3 seam, independent review, standard deployment, and fresh natural
auction/venue evidence. No forced order is authorized.

## 2026-07-19 typed HTTP retry and persistent negative cache

Current ingest evidence showed deterministic Open-Meteo HTTP 400 responses
being flattened into retryable transport failures, leaving the source cursor
deferred and repeating the same physical request on every scheduler poll. The
client now classifies the actual HTTP response before retry: an explicit
`run_not_published`/availability 400 remains conditional, ordinary 400 and
deterministic client statuses are terminal for the exact request identity,
408/425/5xx remain bounded retries, and 429 follows its `Retry-After` embargo.
Only redacted status, retry class, reason, body hash, and retry time persist in
the existing shared quota state; URL/query/body content is never stored.

BPF carries that typed result through its fail-soft report and the source-clock
cursor consumes the type instead of reparsing a generic transport string. An
exact terminal request is therefore suppressed across scheduler polls, while a
new source-run identity remains independently eligible. Acceptance requires
generic/conditional 400, 429, 5xx, cross-poll suppression, quota budget, source
health, compilation, lint, planning-lock, standard deployment, and new live
request receipts showing the repeated-400 amplification is gone. The direct
Open-Meteo metadata probe remains a separately named unification gap; this
slice does not invent a new canonical DB schema or switch provider semantics.

## 2026-07-19 external heartbeat truth continuity

The live venue keeper can be current and `HEALTHY` while an independently
constructed process reader still has a cold in-process singleton. Treating
that wiring state as `LOST` invents venue failure and sends the allocator into
false reduce-only. In external mode the first runtime read therefore binds an
`ExternalHeartbeatSupervisor` to the current keeper status atomically. Status
now distinguishes `UNCONFIGURED` from genuine `LOST` and carries its source,
reason, write time, and observed age so an operator projection cannot erase
the evidence boundary.

A fresh external snapshot permits ordinary decision processing. Missing,
unreadable, expired, internal-unconfigured, and still-starting states continue
to fail closed for new risk. Those states retain only immediate FOK/FAK order
types so a separately authorized held-position reduction is not disabled by
entry liveness. Acceptance requires cold-singleton/fresh-external,
expired-external, internal-unconfigured, entry-denial, and held-exit antibody
tests, compilation, lint, diff checks, independent live-money review, standard
deployment, and post-restart comparison of keeper truth with allocator and
execution-capability projections.

## 2026-07-19 live-order capital and projection correction

Six natural post-restart entry commands falsified the assumption that a
positive robust objective licenses a venue-minimum lot above the configured
Fractional Kelly target. Five of the six fills ended above the target; the
sharpest counterexample had seven held shares, a `7.015625` target, and still
bought five more. Fractional Kelly is therefore a hard terminal-holding budget:
when no legal venue lot fits below the remaining target, BUY is infeasible and
the auction chooses HOLD/CASH symmetrically for YES and NO. Positive local EV
does not authorize an exception to the portfolio budget.

The same window exposed a separate truth gap: an incremental entry command had
eleven authenticated shares filled while its remainder stayed open, but the
active canonical position still showed only the prior fill. Every positive
partial fill is current exposure immediately. The command remains partial and
its remainder obligation stays open, while canonical trade facts idempotently
update the command execution fact, position event, lot, and position aggregate
at their actual weighted fill economics. Restart recovery must repair the same
shape without requiring venue replay.

Finally, a fixed 250ms retry for one contended Day0 source-clock commit amplified
WORLD writer contention into a hot loop and starved reactor Window B plus
command recovery. Retries must coalesce per pending commit, back off to a bounded
five-second cadence, reset on success, and never drop the pending physical fact
or create an entry gate. Acceptance requires focused Kelly symmetry, partial
fill immediate/restart idempotency, actual-fill cost, retry coalescing/reset,
compilation, diff checks, independent review, standard deployment, and fresh
runtime proof that minimum-lot repair count is zero, the existing partial fill
reconciles, and reactor/recovery throughput advances.

## 2026-07-20 global winner claim transaction boundary

Fresh production logs showed `GLOBAL_WINNER_CLAIM_WORLD_TXN_OPEN` only when the
auction found a positive unpaged winner. The global selector had correctly
included the canonical WORLD connection in its immutable read cut, but then
called the reactor's WORLD write/claim callback before releasing that cut. A
no-trade epoch therefore looked healthy while every actionable winner failed
closed before preflight.

The selected q/book/wealth values and identities are already immutable Python
evidence at that boundary. The read snapshot now releases immediately after a
winner and actuation are selected, before any durable winner materialization;
JIT probability, book, risk, capital, and venue checks remain current and
unchanged. A production-shaped antibody uses the same SQLite connection for
selection and claim and requires `in_transaction=False` at the callback. The
focused claim/snapshot sets pass `62` and the wider global batch/winner set
passes `57`; standard deployment and a natural positive-winner receipt remain
the runtime acceptance evidence.

## 2026-07-20 held Day0 probability producer priority

Canonical loss attribution for Tokyo July 20 showed a fresh remaining-day
probability expiring during an executable exit window. The last complete hourly
bundle aged past its three-hour read limit at 04:36Z and the next bundle arrived
about 40 minutes later. Live scheduler evidence reproduced the mechanism: the
45-second producer skipped every tick while the reactor/redecision lane was
active even though successful fetches reported no quota denial or exhausted
budget. A probability consumer being busy cannot make held-capital truth
optional.

When a trading lane is active, the producer now performs only the bounded
same-local-day held-city prefix. Pending candidates, open rests, and the static
universe still defer; the existing HTTP budget, per-city throttle, critical
quota tranche, and non-blocking forecast-DB persist lock remain unchanged. No
stale vector is accepted and no exit is authorized by this producer. Acceptance
requires a pre-fix failing test that proves an active trading lane still reaches
the held-city refresh without scanning pending families, the focused scheduler
and Day0 suites, standard live deployment, and new logs with `held_only=True`
while trading remains active.

## 2026-07-20 frozen artifact HWM product-cycle scan

Production sampling attributed the auction's 140–193 second
`prepare_families` stage to the frozen raw-artifact HWM query. The query joined
requested family identity through `json_extract(artifact_metadata_json, ...)`
before narrowing the source cycle, so SQLite scanned wide historical artifact
JSON and the auction's otherwise complete q/book evidence expired before
selection. Freshness rejection was correct; the read path feeding it was not
capital-efficient.

The current structured artifact schema already owns a
`(source_id, product_id, source_cycle_time)` index. Frozen selection now walks
those product-cycle partitions newest-first, parses only one exact partition at
a time, validates payload coverage only for still-unresolved requested
families, and stops when the request set is complete. Legacy tables without the
structured product identity retain the generic fail-closed path. Acceptance
requires the pre-fix malformed-old-cycle antibody, focused HWM tests, a
read-only canonical-DB benchmark with an indexed query plan, standard live
deployment, and natural auction evidence below the 180-second evidence horizon.

## 2026-07-20 submit-feasible ranking and scalar HWM

The batch HWM repair restored complete auctions, then natural production exposed
two later-ordering defects. Winner JIT still used the legacy scalar JSON scan
inside the frozen read transaction, blocking one preflight for about 102 seconds.
The same epoch ranked a `0.99904995` all-in BUY first even though the durable live
submission contract rejects every unit price outside inclusive `[0.05, 0.95]`.
Final guards prevented a venue order, but late rejection wasted the evidence
window and hid any legal runner-up.

Structured scalar HWM reads now reuse the indexed newest-first product-cycle
resolver; legacy schemas keep the generic path. Global BUY scoring admits only
exact probe sizes whose all-in average unit cost is in the live band, while SELL
uses its exact submitted limit price. This feasibility constraint is native-side
symmetric and precedes ranking; robust log wealth, EV, correlated endowment, and
Fractional Kelly still decide among the remaining orders. Acceptance requires
pre-fix failing `0.004`/`0.999` YES/NO BUY and SELL antibodies, the malformed-old
cycle scalar antibody, focused and integration regressions, a read-only canonical
scalar benchmark, standard deployment, and natural receipts showing no late
price-band preflight loop or out-of-band venue command.

## 2026-07-20 WU post-day final observation continuity

Live held-position evidence exposed a permanent authority gap after the local
target day ended. HKO markets could promote an explicit verified daily product
to an exact settlement simplex, while WU markets always returned
`POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE` even after canonical same-station
hourly history had completed. The global auction therefore retained stale held
probability for WU positions and could not compare HOLD/SELL from current
physical evidence.

WU finality now requires two independent causal facts: exact coverage of every
UTC hour belonging to the contract-local target day (23/24/25 across DST), and
the exact first same-station `wu_icao_history` observation of the following
local day. Every contributing row must be `VERIFIED`, `OK`,
`historical_hourly`, `utc_hour_bucket_extremum`, unit/station correct, and both
observed and imported no later than the decision time. The target extreme is
then settlement-rounded and mapped to the complete exact family simplex.
Missing an hour, the following-day
publication, or current causality remains fail-closed. The reactor checks the
forecast daily-product plane first and the canonical observation connection
second; neither source role is guessed or substituted.

Acceptance requires the pre-fix failing complete-WU antibody, incomplete/future
counterexamples, a cross-connection global-simplex integration test, current
canonical read-only proof, standard deployment, and a natural held-position
receipt showing the stale WU probability is replaced without a forced order.

## 2026-07-20 deploy heartbeat priority inversion

The WU deployment exposed a restart-ordering failure rather than a strategy
gate. `deploy_live` deliberately kept the external venue-heartbeat supervisor
unloaded while it waited up to four minutes for every held position and then up
to four more minutes for reactor progress. Those proofs themselves need current
CLOB and heartbeat authority, so the verifier created `heartbeat LOST`,
`reduce_only`, and request-lease failures and could leave its temporary entry
pause in place.

The heartbeat watchdog already acquires a shared nonblocking lease on the same
restart lock held exclusively by deploy; while deploy owns that lock the
watchdog records `deploy_restart_in_progress` and cannot restart `src.main`.
Deployment must therefore restart venue-heartbeat immediately after the new
process identity is verified, before monitor and queue proofs. A failed monitor
proof is terminal for that deploy attempt, so a second four-minute queue wait
cannot repair it and is skipped. The entry pause still clears only after
runtime, monitor, queue, and sidecar proofs are all green; no money-path safety
condition is waived.

## 2026-07-20 incomplete global-book response retry amplification

Production logs showed one incomplete two-token CLOB batch response being
reclaimed roughly every four seconds. The reactor's unknown-reason fallback
correctly preserved the opportunity, but the reason was absent from the
explicit transient vocabulary, snapshot-refresh classifier, and retry-floor
set. The same event therefore repeated the full book request before the venue
or substrate could recover, multiplying API/quota pressure without adding any
decision evidence.

`GLOBAL_BOOK_RESPONSE_INCOMPLETE` is now an explicit refreshable transient. It
queues the existing same-family snapshot refresh and applies the existing
attempt-scaled, bounded snapshot retry floor; it remains horizon-bounded with
no attempt cap and every retry re-runs the complete auction from fresh evidence.
This adds no admission gate and cannot force an order. Acceptance requires the
pre-fix-shaped incomplete-response antibody, the complete transient-requeue
suite, standard live deployment, and post-restart logs with no immediate
same-event incomplete-response loop.

## 2026-07-21 heartbeat lease scope and failure backoff

The authenticated heartbeat service remained unavailable with repeated HTTP
503 responses after the deployment-order repair. Heartbeat authority owns the
lease for resting GTC/GTD orders; it does not add evidence to an immediate
FOK/FAK fill. The risk allocator already forced non-healthy heartbeat states to
TAKER and allowed only FOK/FAK, and the executor rechecked the concrete order
type before command persistence. A separate portfolio-wide reduce-only latch
made that safe path unreachable, stopping every otherwise valid entry because
an unrelated resting-order lease was unavailable.

Heartbeat health no longer activates portfolio-wide reduce-only mode. LOST,
STARTING, and UNCONFIGURED states permit only immediate FOK/FAK execution;
resting GTC/GTD still fail closed at discovery and the final executor boundary.
Failed heartbeat POSTs use bounded 10/20/30-second retry backoff after lease
loss while the keeper continues writing a fresh typed status at its normal
cadence. Economics, current book/probability, price band, global robust
delta-log-wealth/EV, Fractional Kelly, and submit-time revalidation remain
cumulative requirements. Acceptance requires LOST/stale/unconfigured
antibodies, resting-order rejection, immediate-order permission, bounded POST
rate with fresh status writes, standard deployment, and a natural receipt that
selects CASH or a positive executable order without heartbeat priority
inversion.

## 2026-07-21 live WU source truth versus durable continuity

The 48-hour loss audit separated a market/forecast miss from a source-routing
defect. Same-station METAR prints reached the append-only publication ledger
within seconds, but WU-settled positions intentionally applied a one-unit
divergence margin until the settlement source confirmed the crossing. The
direct WU client could supply that confirmation; however,
`get_current_observation` returned any existing canonical
`observation_instants` context before contacting WU. Consequently the
WU-vs-METAR anomaly guard compared no live WU side at all, and the hard-fact
lane's nominal `wu_api` refresh could actually be an older durable row.

The source contract is now explicit. `get_live_wu_observation` can return only
a current `wu_api` context and fails closed instead of substituting durable or
METAR evidence. The WU anomaly guard and WU hard-fact confirmation use that
narrow contract; HKO and general continuity consumers keep the existing
canonical-capable path. Successful live WU observations remain memoized for
ten minutes per city/date, while failures retry after two minutes, preventing
per-position HTTP amplification without freezing recovery for a full success
interval. Acceptance requires provider-isolation antibodies, failure-backoff
proof, the complete Day0 fast-observation/hard-fact suites, one read-only live
WU probe with station/source identity, standard deployment, and natural monitor
evidence that a WU-confirmed crossing dominates the prior probabilistic belief
before the settlement-channel hourly row arrives.

## 2026-07-24 chain aggregate versus owned open exposure

The 72-hour loss audit found a canonical exposure split after wallet balances
shrunk. Paris Jul 22 NO 26C retained `shares=97.8947` while fresh wallet truth
and `chain_shares` were `45.0747`; Wuhan Jul 23 YES 32C retained the original
fill aggregate in its chain cost after current wallet exposure fell from
`124.8075` to `3.1125`. The chain-mirror writer updated only `chain_shares`,
while fill-authority runtime exposure continued to read the old owned
`shares`. Its next comparison preferred the already-updated `chain_shares`,
making the torn projection appear permanently consistent.

Truth contract: the wallet position surface owns the current token balance;
command-linked fill facts own Zeus acquisition provenance; `position_current`
owns Zeus's currently open attributed slice. A lower wallet balance than the
attributed open slice reduces current sellable exposure immediately and
pro-rates remaining open cost at the existing unit basis, without inventing an
exit fill or lifecycle close. A higher wallet balance never expands one Zeus
position: the positive residual remains unattributed chain inventory for
reconciliation/risk review. The mirror must compare fresh wallet balance
against current owned `shares`, not against its prior `chain_shares` cache.

This is a K2 reconciliation/truth-ownership bugfix under INV-08, INV-18,
INV-27, and INV-37. Allowed files are
`src/state/chain_mirror_reconciler.py`,
`src/state/chain_reconciliation.py`, focused reconciliation tests, and this
plan. No schema, lifecycle grammar, probability, strategy, source, settlement,
or control change is allowed.

Acceptance requires:

- a Paris-shaped antibody proving a wallet reduction updates current
  `shares`, `chain_shares`, and proportional remaining cost in one canonical
  event/projection transaction while preserving phase;
- a second pass proving the mirror does not hide the remaining owned-vs-chain
  comparison behind cached `chain_shares`;
- an excess-wallet antibody proving wallet aggregate inventory cannot increase
  one position's shares or chain-backed attributed slice;
- existing known-empty/unknown-chain, multi-lot, pending-exit, canonical
  append/projection, and live-safety reconciliation suites remain green;
- planning-lock, source compile, and diff checks pass before hot-fix landing.

## 2026-07-24 Day0 target-window coverage at persistence and selection

The loss time-series audit found persisted hourly vectors whose
`target_date=2026-07-23` but whose provider grid began on July 24. The writer
stamped one two-day response under both requested dates without proving that
the response still covered each target's causal remaining window. The reader
then selected the newest row per model before checking coverage, so one newer
wrong-day row hid an older, still-fresh and complete target-day trajectory.
This converts a usable probability refresh into avoidable fail-closed
unavailability exactly when a held position needs continuous re-decision.

The canonical contract is target-window coverage, not response recency alone.
A vector may be persisted under the current local target only when it covers
every hourly grid point from capture through local-day end, and under the next
target only when it covers that complete local day. The live reader selects
the newest eligible row per model, skipping target-incomplete rows before
forming the required model bundle. No target-date rewriting, interpolation,
forecast fallback, or cross-day reuse is allowed.

Acceptance requires a wrong-day persistence antibody, an existing-row
fallback antibody, the full Day0 remaining-day suite, compile/diff checks, and
no weakening of model completeness, capture-skew, freshness, or causal-window
gates.

## 2026-07-27 chain-mirror continuity for held-position SELL authority

The loss reconstruction found that the scheduled chain mirror successfully
read the wallet every ten minutes but classified exact positive matches as
`CONSISTENT` without appending a new observation. `position_current.chain_seen_at`
therefore froze. Once it exceeded the 30-minute wealth-witness bound, open
positions remained capital liabilities but disappeared from the executable
SELL endowment. After restart the global auction consequently claimed complete
coverage while evaluating only two of roughly sixty-five open positions.

The structural contract is continuous positive-chain observation, not a
one-time synced label. A fresh complete chain read for an active, Day0, or
pending-exit position must refresh durable `chain_seen_at` with enough margin
before the consumer's fail-closed bound. The write is append-first,
phase-preserving, and may update only chain-observation fields; it must never
mutate owned shares or cost basis. Missing tokens remain absence evidence and
must not receive a positive timestamp.

This is a K2 reconciliation continuity hot-fix. Allowed files are
`src/state/chain_mirror_reconciler.py`, its focused test, the existing operator
CLI help, its existing script-manifest row, and this plan. No schema, lifecycle
grammar, probability, entry/exit threshold, settlement, or venue-action law
changes are allowed.

Acceptance requires:

- a Beijing-shaped matching-position antibody whose stale observation becomes
  fresh before wealth-witness expiry;
- the event and projection preserve phase, owned shares, and cost basis;
- an immediate second pass is idempotent and emits no duplicate event;
- absent and terminal rows cannot receive positive observation refreshes;
- the append writer rechecks the current phase so a concurrent terminal
  transition cannot receive a stale positive observation;
- focused reconciliation, compile, lint, planning-lock, and diff checks pass;
- after live deployment, canonical `chain_seen_at` coverage, global-auction
  held-position counts, and subsequent SELL selection/command evidence are
  re-sampled independently.

## 2026-07-24 provisional match versus economic ownership

The loss/lifecycle audit found one Tel Aviv July 25 position whose canonical
projection reported 5.3 shares although the complete authenticated Data API
snapshot had never observed the token, `chain_seen_at` was NULL, the current
order lookup returned no order, and the only durable trade fact was
`MATCHED`. Command recovery had projected `ENTRY_ORDER_FILLED` directly from
that provisional fact. Chain mirror then treated either the event name or any
`MATCHED` fact as confirmed economic ownership, so repeated complete wallet
absence could never retire the phantom exposure. A second continuity defect
made that permanent: ordinary monitor payloads containing non-finite `NaN`
were invalid to SQLite JSON functions and therefore falsely reset the
consecutive-absence proof on every monitor cycle.

The venue state grammar is causal: `MATCHED` is not `MINED` or `CONFIRMED`.
Confirmed ownership for the chain-absence guard now requires a prior positive
wallet observation, a positive chain reconciliation event, a legacy fill event
without an explicit provisional witness, or a positive `MINED`/`CONFIRMED`
trade fact. An explicitly `MATCHED`-only recovery event remains fill-unproven;
after the existing two consecutive complete absence reads and zero open orders,
the existing `CLOSED_EXITED` administrative-void path removes only the local
projection. A position with any historical positive chain observation remains
open for economic-close evidence. Plain monitor events remain continuity noise
even when their numeric payload contains `NaN`; only typed semantic monitor
events remain reset boundaries.

This is a K1/K2 canonical truth-boundary hot-fix under INV-18. Allowed files are
`src/state/chain_mirror_reconciler.py`, its focused reconciliation test, and
this plan. No schema, lifecycle grammar, venue action, probability, strategy,
source, settlement, or entry/exit threshold changes are allowed.

Acceptance requires:

- a Tel Aviv-shaped antibody proving `MATCHED`-only recovery closes only after
  two complete absent reads;
- a `MINED` fill antibody proving confirmed ownership remains open for review;
- a prior-positive-chain antibody proving current absence cannot erase real
  historical ownership;
- a non-finite plain-monitor antibody proving JSON encoding does not mint
  false Chain/CLOB evidence while typed semantic monitor events still reset;
- the complete chain-mirror and affected state/runtime safety suites remain
  green relative to their recorded baseline;
- planning lock, compile, diff, dry-run canonical reconciliation, standard
  hot-fix landing, and post-restart natural lifecycle evidence.

Rollback is the exact hot-fix revert. It restores conservative review of the
phantom row but performs no inverse DB mutation; any already-voided row remains
an append-only canonical fact for operator review.

## 2026-07-26 NOAA/Ogimet observation commit to Day0 redecision

The Tel Aviv July 26 NO 31C loss reconstruction found two serial breaks.
Ogimet hourly fetches first failed on a process-local forced-IPv4 bind; after
that transport was repaired, 24 verified LLBG observations reached canonical
`observation_instants`, but the held monitor still had no
`DAY0_EXTREME_UPDATED` carrier. NOAA-settled cities are intentionally excluded
from the fast WU/METAR emitter because their settlement source class differs.
Their Ogimet writer persisted the same-station NOAA mirror but never published
the admitted Day0 event. Reactor catch-up was only a fallback and was preempted
under the active urgent-wake backlog, leaving canonical physical truth unable
to reprice owned capital.

The correction publishes only admitted NOAA/Ogimet families from the exact
city's canonical rows in the same SQLite transaction as the observation
commit. Admission is resolved before upstream fetch/write and remains
fail-closed; raw weather facts still commit if the derived event scan fails,
using a savepoint so the durable scanner can retry. After commit, the existing
materialization bridge and reactor wake receive the inserted event identities.
The scanner's persisted monotone watermarks prevent unchanged event firehose.

SCOPE is one NOAA/Ogimet city and only market-backed/current-exposure families.
DRAIN is the next source tick plus the existing durable reactor scanner.
RESET is a successfully committed `DAY0_EXTREME_UPDATED`, followed by the
existing posterior materialization and held-position redecision lanes.

Allowed files are `scripts/obs_live_tick.py`, `src/ingest_main.py`, the focused
admission/source bridge antibody, and this plan. No settlement-source mapping,
probability formula, exit threshold, order band, lifecycle, schema, or monitor
fallback changes are allowed.

Acceptance requires:

- an idempotent replay antibody proving a previously committed canonical
  Ogimet row can publish its missing admitted event even with zero new rows;
- admission failure still commits raw observations and emits no broad event;
- event and observation commit together on success, then the existing
  post-commit materialization/wake bridge receives exact identities;
- focused source, trigger, admission, monitor, invariant, compile, lint, and
  diff checks pass;
- standard hot-fix deployment plus live proof of canonical event, fresh global
  Day0 probability, monitor redecision, loaded SHA, heartbeat, and rejection
  evidence.

## 2026-07-26 Hourly extrema plus current-state observation shape

The Tel Aviv July 26 loss reconstruction exposed a third serial break after
Ogimet transport and Day0 event publication were restored. Fresh three-model
hourly forecast vectors existed, but the current-state conditioned
remaining-window probability still failed closed. The canonical Ogimet rows
persisted hourly maxima/minima while setting `temp_current=NULL`; the source
object carried the latest report timestamp but discarded that report's
temperature. The Day0 trajectory conditioner therefore had no physical
current-state anchor and returned
`DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE`.

The observation shape now carries three distinct facts without conflation:
hour maximum, hour minimum, and the temperature from the latest causal source
report. Daily HIGH/LOW extrema continue to consume max/min. Remaining-window
probability consumes only latest-report temperature and timestamp. The latest
value is included in provenance and payload identity, so widening an existing
row from unknown current temperature to a known source value is auditable and
idempotent.

SCOPE is the shared WU/Ogimet hourly aggregate-to-canonical adapter. DRAIN is
the next admitted observation tick, which widens current-day rows and wakes the
existing Day0 redecision chain. RESET is a non-null canonical
`temp_current` paired with `latest_raw_ts`, followed by a fresh
current-state-conditioned monitor receipt. No source mapping, settlement
extremum, probability formula, exit threshold, order band, schema, or
lifecycle rule changes.

Acceptance requires:

- WU and Ogimet aggregation antibodies proving latest-report temperature is
  independent from the bucket maximum/minimum;
- writer relationship proof that `temp_current`, provenance, and payload hash
  carry that exact latest report;
- existing hourly parser/writer, remaining-window, monitor, compile, lint, and
  diff checks remain green;
- standard hot-fix deployment, targeted current-day source replay, and live
  evidence that Tel Aviv current-state remaining-window probabilities become
  fresh before evaluating exits.

## 2026-07-31 Day0 raw-provenance family isolation

The live global auction currently fails before selection when one Day0 family
has a current settlement-channel extreme but no verifiable raw-payload digest.
Rejecting that probability is correct; promoting the family-local evidence gap
to `GLOBAL_PREPARED_FAMILY_INCOMPLETE` across every otherwise independent
weather family is not.

The correction types `GLOBAL_DAY0_RAW_PROVENANCE_MISSING` as unavailable for
that weather family. The family remains excluded from BUY, SELL, HOLD, and
submit-time probability authority until a fresh canonical fact carries a real
raw digest. Other complete families remain eligible for the same global
capital auction.

SCOPE is exactly one city x target-date x metric family lacking raw Day0
provenance. DRAIN is the existing current-q family exclusion followed by the
same-epoch global auction over complete families. RESET is the next fresh
canonical Day0 fact for that family whose raw payload has a valid SHA-256,
which makes normal probability preparation succeed. No digest is synthesized,
no probability or Kelly law changes, and no venue order is forced.

Acceptance requires a two-family adapter antibody proving the incomplete
family is typed ineligible while the complete sibling family can win and
actuate; the focused W3 seam, compile, lint, diff, hot-fix landing, loaded SHA,
and a new complete live auction receipt must all be verified.

## 2026-08-01 Current-truth-only capital selection

The first complete post-recovery auction proved that every in-band candidate
was removed before economics and Fractional Kelly by settled-Brier
`risk_action:gate` rows. That historical score is valid learning telemetry but
is not part of the replacement probability authority: current provider center,
current target-specific ENS shape and disagreement, current portfolio wealth,
and the executable book are the decision-time truth.

The correction keeps settled Brier, lineage binding, and strategy breakdown in
the canonical RiskGuard receipt, but removes settled Brier and historical edge
compression from RiskLevel and durable strategy-gate emission. Current missing
probability semantics, source identity, collateral/exposure truth, operator
controls, executable-price law, fees, depth, and Kelly remain behavioral.

SCOPE is entry selection and submit for the current global auction; held
monitoring and reduce-only exits are unchanged. DRAIN is the next RiskGuard tick,
which expires obsolete automatic gates, followed by the next same-cut global
auction. RESET is unnecessary because a settled-history veto is no longer a
live state; a current truth loss still fails closed through its existing
authority-specific gate. No order is forced and CASH remains an equal candidate.

Acceptance requires a red-before-green RiskGuard antibody, full RiskGuard and
W3 regressions, standard hot-fix deployment, expired Brier risk actions, and a
new complete receipt proving all in-band YES/NO candidates reached economics
and Fractional Kelly before CASH or one unique order won.

## 2026-07-27 Boot event-claim recovery under world-writer contention

The post-auction-fix restart proved a runtime continuity defect: prerequisite
sidecars were healthy and writing the canonical world DB, but the main daemon's
boot-only prior-runtime claim recovery attempted `BEGIN IMMEDIATE`, received
`SQLITE_BUSY`, and let that transient contention terminate the process before
the scheduler and held-position monitor could start. Repeated launchd restarts
therefore preserved process churn while eliminating continuous redecision.

The correction defers only SQLite busy/locked errors from this boot-only claim
release. All other database errors remain fail-loud. SCOPE is prior-runtime
event claims during boot. DRAIN is the existing event reactor claim path, whose
300-second processing lease makes stale claims reclaimable after scheduler
start. RESET is the next successful claim transaction or later successful boot
recovery. No event is acknowledged, discarded, or relabeled by the defer path.

Allowed files are `src/main.py`, the focused runtime failure-surface antibody,
and this plan. No probability, strategy, execution threshold, order band,
lifecycle, schema, source, settlement, or risk change is allowed.

Acceptance requires:

- a real SQLite writer-contention antibody proving boot continues and closes
  its candidate connection;
- a non-lock `OperationalError` antibody proving corruption/I/O faults still
  terminate boot;
- existing boot ordering/recovery tests, source compile, and diff checks pass;
- standard hot-fix landing followed by loaded-SHA, process, heartbeat, monitor
  cadence, canonical event/receipt, and current rejection-reason evidence.

## 2026-07-27 Data-ingest boot schema-migration isolation

The restart after the global-auction correction exposed a second continuity
failure. A world schema fingerprint change made data-ingest call the complete
`init_schema()` migration engine during ordinary live boot. On the 93GB
canonical world DB that transaction held SQLite's sole writer, accumulated a
multi-gigabyte WAL, and starved EDLI claims, price publication, and continuous
position redecision while the process still appeared alive.

Live daemon boot is therefore a read-only schema-admission boundary, never a
migration lane. A current sentinel or lightweight schema proof admits ingest.
If both fail, data-ingest exits with `WORLD_SCHEMA_MIGRATION_REQUIRED`; an
operator-owned fenced migration must apply DDL before restart. This isolates
source degradation from the independent monitor/exit/global-auction money path.

SCOPE is data-ingest boot only. DRAIN is an explicit fenced world migration
while the mesh is intentionally quiesced. RESET is a successful read-only
schema proof on the next boot, followed by atomic sentinel refresh. No schema,
probability, strategy, execution threshold, order band, lifecycle, settlement,
or risk semantics change.

Acceptance requires:

- a focused antibody proving lightweight readiness never opens a writer;
- a focused antibody proving schema drift fails with the typed migration
  requirement instead of calling `init_schema()`;
- existing ingest boot/registry tests, compile, lint, and diff checks pass;
- after the already-running migration reaches a safe commit, hot-fix landing
  and restart prove loaded SHA, fresh ingest heartbeat, released world writer,
  advancing EDLI claims, and current monitor/rejection evidence.

## 2026-07-27 Global receipt probability-parent coherence

Two current-state handoff defects survived the first candidate-fallthrough
repair. The selected-proof builder converted posterior-parent mismatches into a
returned no-submit receipt before the outer preflight wrapper could classify
them, so one stale BUY-NO candidate still vetoed the complete BUY/SELL/HOLD/CASH
cut. Separately, a global BUY-NO execution could rank on a current posterior
predictive mean while the receipt exported a different point functional and
retained an older served YES scalar. The venue order could fill before the
receipt-level parent check rejected that mixed-generation pair and requeued the
event.

The correction keeps stale selected-leg proof failures candidate-local whether
they are raised or returned. For accepted current-state BUY-NO actions,
`q_live` is the predictive-mean action probability that won the capital
auction, while same-bin YES remains the complement of that same current
witness's point probability. These are distinct declared functionals, not
interchangeable scalars. The immutable served replacement certificate remains
separate provenance and is not rewritten.

SCOPE is global winner preflight classification and BUY-NO receipt scalar
binding. DRAIN is immediate same-cut candidate exclusion/re-auction before any
venue side effect. RESET is a current selected proof whose receipt point pair
passes the existing exact parent check. No probability model, FDR, Kelly,
execution band, sizing objective, source, lifecycle, or settlement rule changes.

Acceptance requires:

- a returned selected-leg mismatch is converted to a candidate-local no-submit
  receipt while unclassified faults remain fail-loud;
- a predictive-mean BUY-NO receipt carries action q, current-point YES parent,
  and qLCB under their distinct declared semantics while preserving the older
  served certificate separately;
- focused integration tests, relevant global-auction/admission tests, compile,
  lint, and diff checks pass;
- hot-fix landing and restart prove current loaded SHA, no new batch-wide
  posterior mismatch, no receipt scalar mismatch, advancing exits, and fresh
  canonical runtime evidence.

## 2026-07-27 Day0 global probability-parent separation

Post-restart auction receipts isolated a second parent-shape defect. A current
Day0 candidate proof truthfully carried observation authority such as
`day0_absorbing_hard_fact`, while the independently rebuilt global witness
carried the replacement posterior ID that supplied its remaining-day forecast.
The selected-proof seam required both facts to have the same authority label,
so a positive predictive-mean candidate was repeatedly rejected even after the
complete current witness had passed freshness and identity validation.

The correction keeps the two facts separate. Conditioned, remaining-day,
absorbing, final-daily, and deterministic Day0 witnesses retain their own
current identity and observation authority; a supporting replacement row is
not relabeled as their probability parent. Forecast events and provisional
Day0 witnesses directly priced by replacement q retain exact posterior-parent
and authority-label equality.

SCOPE is the selected Day0 proof-to-global-witness parent seam. DRAIN is the
next candidate-local preflight and same-cut re-auction before any venue side
effect. RESET is a fresh Day0 proof whose observation authority and replacement
parent are both represented without cross-category equality. No probability
formula, source, FDR, Kelly, sizing, order band, lifecycle, or settlement rule
changes.

Acceptance requires:

- a non-provisional Day0 antibody proving its observation authority survives
  even when a supporting replacement row advances;
- a provisional Day0 antibody proving a mismatched direct posterior parent
  still fails closed;
- forecast-parent strictness and the full global-auction integration suite stay
  green;
- hot-fix landing and restart prove the recurring live candidate no longer
  emits `GLOBAL_ACTUATION_POSTERIOR_BINDING_MISMATCH`.

## 2026-07-27 Conditioned Day0 calibration authority

The parent-separation repair exposed the next stale taxonomy seam. The global
producer correctly emitted `day0_conditioned_replacement` with
`day0_conditioned_replacement_global_probability_v1`, but the calibration
authority verifier recognized only raw `replacement_0_1`, deterministic, and
remaining-day labels. It therefore rejected an already content-bound current
Day0 witness before preflight.

The correction admits the producer's typed conditioned-replacement pair and
reuses the existing current-observation, binding, parent-identity, city/date,
metric, source-clock, and probability-order checks. A non-provisional
conditioned witness binds the supporting replacement row inside its nested
current observation authority rather than to a stale top-level local proof ID.
A provisional Day0 witness remains directly replacement-priced and retains
exact selected/block/bound posterior equality.

SCOPE is conditioned Day0 calibration certificate construction. DRAIN is the
next event preflight using a current nested Day0 authority block. RESET is a
matching typed q-source/authority pair with coherent current binding. No
calibration math, q, FDR, Kelly, sizing, source, order band, lifecycle, or
settlement rule changes.

Acceptance requires:

- a non-provisional conditioned witness with an advanced supporting row passes
  only when its nested block and binding match;
- tampering with the nested parent still fails closed;
- a provisional conditioned witness with a mismatched selected parent remains
  rejected;
- integration, compile, lint, diff, live loaded-SHA, and post-restart receipt
  evidence prove the taxonomy seam is closed.

## 2026-07-27 Submit-time Day0 authority block continuity

Live verification after the conditioned-source taxonomy repair proved that the
global winner reached submit-time probability revalidation, where the current
Day0 payload and its typed probability block were rebuilt. The binder copied
the current scalar fields into the money-path payload but stored the typed
`day0_probability_authority` block only in receipt provenance. Calibration
therefore observed a recognized q-source with no corresponding authority block
and failed closed before preflight.

The correction writes the one freshly rebuilt typed block into both the
pre-submit payload and receipt provenance. This is evidence continuity, not a
new authority or fallback: the existing q-source/type, observation binding,
source clock, parent identity, selected side, and probability checks remain
unchanged.

SCOPE is global Day0 winner submit-time proof construction. DRAIN is the next
preflight revalidation of the selected winner. RESET is a freshly rebuilt
current payload whose typed block passes the existing authority verifier. No q,
calibration math, FDR, Kelly, sizing, execution band, lifecycle, source, or
settlement rule changes.

Acceptance requires:

- the current-global payload binder replaces stale scalar fields and installs
  the identical typed authority content in the money-path payload and receipt
  provenance;
- integration, compile, lint, diff, live loaded-SHA ancestry, and post-restart
  auction/preflight evidence are green;
- `replacement_day0_probability_authority required:missing` does not recur
  after the loaded repair SHA.

## 2026-07-27 Selected Day0 receipt type continuity

The authority-block repair advanced the live winner through calibration and
exposed a later split identity. Submit-time revalidation selected the current
`day0_conditioned_replacement` witness and its typed authority block, while the
selected `_CandidateProof` retained the earlier local proof's
`day0_remaining_day` q-source. Receipt reconstruction therefore combined a
current conditioned block with a stale top-level source label and correctly
failed the exact-source verifier.

The correction rebinds the selected proof's q-source and probability authority
from the same current Day0 payload that owns its action q. Sibling proofs remain
untouched; forecast families retain their existing global-current witness
labels; q values, bounds, selection, and sizing are unchanged.

SCOPE is the selected global Day0 candidate's submit-time receipt type. DRAIN is
the next winner revalidation and typed receipt reconstruction. RESET is one
receipt whose top-level q-source, probability authority, nested typed block, and
current action q all name the same witness. No fallback or alternate probability
regime is introduced.

Acceptance requires:

- a selected proof with no local scalar rejection still receives the current
  Day0 q-source and authority while its sibling remains byte-identical;
- existing BUY-NO cap rebinding and non-Day0 fallback behavior remain green;
- post-restart live preflight no longer reports mixed
  `day0_conditioned_replacement`/`day0_remaining_day` sources.

## 2026-07-27 Conditioned Day0 qkernel guard continuity

The selected-receipt repair advanced the live winner through typed probability
validation and exposed one final legacy dispatch in qkernel economics
validation. The guard recognized only raw `replacement_0_1` as a current
replacement route, so a sealed `day0_conditioned_replacement` certificate was
later forced through the unrelated remaining-window model-count requirement.

The correction classifies every q-source already registered in the replacement
authority map as current replacement economics. The existing current-state
identity, selected side, action-q, q-lower-bound, guard basis, and abstention
checks remain mandatory. Only the remaining-window route requires
`remaining_models`.

SCOPE is submit-time Day0 qkernel authority dispatch. DRAIN is the next global
winner preflight. RESET is a sealed conditioned-replacement economics
certificate whose selected q and side match the typed probability owner. No q,
calibration, selection, Kelly, sizing, execution band, lifecycle, source, or
settlement rule changes.

Acceptance requires:

- conditioned replacement clears qkernel validation without fabricating
  remaining-window model evidence;
- a true remaining-window payload without positive `remaining_models` still
  fails closed;
- integration, compile, lint, diff, loaded-SHA, and post-restart preflight
  evidence are green.

## 2026-07-27 Day0 catch-up work conservation

Live held-position refresh exposed a producer fairness defect after probability
and submit-time authority were repaired. The Day0 catch-up scanner sorted every
admitted family by observation freshness and applied its SQL limit before
checking persisted event watermarks. The same fresh-but-unchanged rows could
therefore occupy every bounded scan while a slightly older held family with a
new canonical observation never received a Day0 event or current monitor q.

The correction scans the bounded current-day admitted city set, applies the
existing extrema/source-clock change gate, and spends the configured limit only
on rows that reach event writing. Freshness order is preserved among actionable
rows; unchanged rows no longer consume redecision capacity.

SCOPE is canonical observation-to-Day0-event catch-up scheduling. DRAIN is the
next reactor scan, which can now advance every changed admitted family despite
unchanged fresher rows. RESET is the persisted per-family extrema/source-clock
watermark after emission. No source authority, q, calibration, selection,
Kelly, sizing, execution band, lifecycle, or settlement rule changes.

Acceptance requires:

- an unchanged freshest family cannot starve a changed older family when the
  per-cycle limit is one;
- all Day0 trigger tests, compile, lint, diff, loaded-SHA, and post-restart
  canonical event/monitor-freshness evidence are green;
- a held family with canonical target-day observation can no longer remain
  eventless solely because its global freshness rank exceeds the emit limit.

## 2026-07-29 Settlement payout and exit-price axis separation

The loss audit found settled rows whose `settlement_price` equals a
pre-settlement SELL fill such as 0.74 or 0.75. The held position had already
locked its realized PnL at that fill, but the later harvester settlement fold
reused `Position.exit_price` as the binary settlement payout. This collapses
two independent facts: execution price answers what Zeus sold for, while
settlement payout answers whether the held token resolved to 0 or 1. The
chain-mirror settlement writer already preserves this distinction; the
canonical lifecycle builder does not.

The correction makes the settlement builder author `settlement_price`
exclusively from its validated held-position `outcome` and makes the generic
position projection leave that field unset because a runtime `Position` alone
does not carry an independent settlement outcome. `exit_price` and previously
booked `realized_pnl_usd` remain unchanged for economically closed positions.
Direct-to-settlement positions still compute their PnL and exit price from the
binary payout before the canonical write.

SCOPE is the canonical settlement event/projection pair. DRAIN is the next
harvester or chain-mirror settlement fold. RESET is one SETTLED event whose
validated `outcome` projects the same binary `settlement_price`. No probability,
entry/exit decision, sizing, venue action, lifecycle grammar, source authority,
or settlement winner rule changes.

Allowed files are `src/engine/lifecycle_events.py`,
`src/state/projection.py`, the hard-terminal projection recovery twin,
focused settlement antibodies, and this plan.
Acceptance requires:

- an economically closed winner preserves its real 0.27 exit and booked PnL
  while projecting `settlement_price=1.0`;
- an economically closed loser preserves its real fill while projecting
  `settlement_price=0.0`;
- the generic projection cannot invent settlement payout from `exit_price`;
- the canonical projection boundary rejects non-binary settled payout and any
  settlement payout attached to a non-settled phase;
- direct active/day0 settlement remains binary and its PnL is unchanged;
- planning-lock, targeted settlement/state tests, compile, diff, evidence-based
  review, standard hot-fix landing, and post-restart settled-row evidence pass.

## 2026-07-29 Day0 simplex floating-boundary continuity

Live Ankara 2026-07-29 HIGH monitor refreshes repeatedly failed with
`GLOBAL_DAY0_FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID` even though the current
posterior identity, 11-bin point q, and all 400 bootstrap rows were coherent.
The exact failing cell was `1.0000000000000002`: one IEEE-754 ULP above one.
The consumer simultaneously accepted row sums within `1e-9` but rejected any
cell strictly above one, so a numerically valid current simplex made held
position redecision blind.

The repair gives component bounds and row normalization one explicit
`1e-9`, zero-relative-tolerance contract. Values inside that contract are
clipped and renormalized to a canonical exact simplex before witness
construction. Values outside it remain fail-closed under a distinct
`GLOBAL_DAY0_FAST_RESIDUAL_SIMPLEX_INVALID` reason instead of being mislabeled
as a posterior identity failure. This changes neither q, forecast/source
authority, nor economic action law beyond floating-point dust.

SCOPE is the current replacement global probability component reader for one
weather family. DRAIN is the next held-monitor or event-reactor compile using
the already-current posterior. RESET is a finite canonical component matrix
whose cells lie in `[0,1]` and rows sum to one. Entry and exit share the same
reader, so the fix is symmetric; malformed material violations still block.

Allowed files are `src/engine/event_reactor_adapter.py`, its focused trusted
replacement-probability antibody, and this plan. Acceptance requires:

- the exact `nextafter(1.0, 2.0)` live failure compiles to `[1.0, 0.0]`;
- a balanced material violation beyond `1e-9` still fails closed;
- focused replacement/Day0/global-witness tests, planning-lock, compile, diff,
  standard hot-fix landing, and post-restart fresh Ankara monitor evidence pass.

## 2026-07-31 Pending-exit per-position failure isolation

The seven-day full-loss reconstruction found repeated pending-exit scans ending
at one malformed intentional-reduction proof. The held monitor continued, but
the remaining positions in that bounded pending-exit batch did not reach fill
polling or retry release. One bad position could therefore delay unrelated
exits across repeated cycles.

The repair isolates only the four reduction precondition failures that occur
before runtime exposure or canonical projection changes. It records the
position/error in scan stats and advances to the next position. Unknown errors
and all projection/release failures after mutation still raise fail-closed.

SCOPE is one malformed pending-exit position. DRAIN is the same bounded scan,
which immediately advances to the next position and rotates on the following
cycle. RESET is corrected canonical reduction intent/fill/holding evidence for
the rejected position. No probability, sizing, entry, price-band, settlement,
or lifecycle grammar changes.

Allowed files are `src/execution/exit_lifecycle.py`,
`tests/test_exit_safety.py`, and this plan. Acceptance requires:

- a malformed full-close-shaped reduction cannot abort a later retry release;
- all three reduction completion paths share the same narrow isolation rule;
- unknown or post-mutation errors remain fail-closed;
- focused exit-safety tests, compile, diff, hot-fix landing, loaded-SHA, and
  post-restart health evidence pass.

## 2026-07-31 Global-auction common holding reference

Live health rejected a valid schema-19 global-auction delta receipt after the
receipt writer compacted all heavy payload references onto one common base.
The candidate reader already understood that compact form; the holding reader
required a per-component map that the writer intentionally omits when one base
serves every referenced component. This false corruption verdict degraded the
runtime despite intact hashes and candidate evidence.

The repair resolves holding coverage from either its explicit component record
or the writer's common base identity, then applies the same receipt hash,
component hash, encoding, payload-presence, and decode checks.

SCOPE is read-only live-health reconstruction of one holding-coverage
component. DRAIN is the next health evaluation of the latest complete auction
receipt. RESET is a valid inline, delta, component-reference, or common-reference
payload whose hashes reproduce exactly. Execution evidence, probability,
selection, sizing, lifecycle, and venue action are unchanged.

Allowed files are `src/control/live_health.py`,
`tests/test_run_mode_failure_surfaces.py`, and this plan. Acceptance requires:

- the producer's one-base common-reference compact form reconstructs holding
  coverage;
- component-specific references retain their existing validation;
- the current canonical receipt no longer reports
  `GLOBAL_AUCTION_CANDIDATE_EVIDENCE_INVALID`;
- focused tests, planning lock, compile, diff, hot-fix landing, loaded-SHA, and
  post-restart health evidence pass.

## 2026-08-11 Identity-bound submit recovery owns capital priority

A real maker submit crossed the venue boundary and persisted its exact
`venue_order_id`, but ACK persistence lost a DB race. The command remained
`SUBMITTING` while the venue order was LIVE, and a later point read proved a
separate newly ACKED order had already partially filled. The scheduled recovery
counted only unresolved cancels as capital blockers, so persistent held-monitor
I/O debt could indefinitely defer exact-order recovery and leave current fill
exposure outside canonical position monitoring.

The repair treats a `SUBMITTING` ENTRY or EXIT with a non-empty persisted venue
order ID as the same class of exact capital blocker as an unresolved cancel.
That blocker reserves the existing bounded reactor handoff; the existing
identity-bound point reader remains the sole authority for advancement. No
venue absence, replay, cancel, fill, price, probability, or sizing rule changes.

SCOPE is one known-order in-flight command. DRAIN is the next scheduled
identity-bound exact-order read and canonical apply. RESET is advancement out of
`SUBMITTING` or removal of the exact capital blocker. Acceptance requires:

- the blocker count includes known-order ENTRY/EXIT submits but excludes an
  unbound submit whose venue side effect remains unknown;
- an overdue held monitor cannot defer this exact capital recovery;
- recovery still fences the active reactor before point truth is applied;
- focused recovery/scheduler tests, compile, diff, hot-fix landing, loaded-SHA,
  and live command/fill projection evidence pass.

## 2026-08-12 Capital recovery owns its deadline at the writer boundary

Forward evidence exposed a deadline-composition defect in the same lane. The
live-tick coordinator first wrapped its writer factory with the cumulative
100ms maintenance deadline. `terminal_order_facts_fast` and its sibling capital
passes later created independent 1.5s deadlines, but wrapped that already-bound
factory again. Once the outer maintenance deadline elapsed, every capital APPLY
attempt failed before opening the DB, and the next tick deterministically
reconstructed the same expired nesting. Durable terminal orders therefore kept
collateral reservations and entry-exposure obligations open indefinitely even
though no venue ambiguity remained.

The structural law is single deadline ownership at the writer boundary. Every
capital pass constructs its priority lease directly from the canonical
trade-only connection factory using that pass's fresh absolute deadline; it
never extends or nests an earlier deadline-bound factory. Read/network work,
venue truth, probability, sizing, and lifecycle semantics are unchanged.

SCOPE is one exact capital-recovery APPLY transaction. DRAIN is the next
scheduled live-tick pass with its independently bounded capital deadline. RESET
is successful canonical terminalization/reservation release or a fresh retry on
the following tick; monitor intent still preempts the writer. Acceptance
requires a zero maintenance-budget antibody that proves terminal capital truth
still receives a positive writer lease deadline and advances while an unrelated
identity-bound submit remains unresolved, plus the full command-recovery suite.

Allowed files are `src/execution/command_recovery.py`,
`tests/test_command_recovery.py`, and this plan.

## 2026-08-11 Immutable weather snapshot accepts market-bin slug identity

After the identity-bound submit advanced to ACKED, authenticated fill sync
proved 7.462684 matched shares but rolled its entire transaction back because
the missing-position recovery parser rejected the real immutable event slug
`highest-temperature-in-singapore-on-august-12-2026-32c`. The parser accepted
only a family slug ending at the year, while executable market snapshots bind
the outcome-bin suffix as part of their event slug.

The repair extends only the weather-slug grammar after a complete
metric/city/date prefix. It accepts the executable point/range/shoulder forms
(`32c`, `50-51f`, `31c-or-below`, `90f-or-higher`) and still rejects arbitrary
suffixes. Complete snapshot and submission-envelope condition, YES/NO token,
selected-token, runtime city, and canonical market-metadata checks remain
mandatory before any position is materialized.

SCOPE is missing-position recovery for an authenticated exact-order ENTRY fill.
DRAIN is the next continuous fill-sync retry. RESET is a parseable immutable
weather market slug plus all existing identity predicates. Acceptance requires
the bin-suffixed Singapore fill test to materialize the exact canonical market
identity, malformed slugs to remain fail-closed, focused recovery/fill tests to
pass, then exact-SHA deployment and live projection of both current fills.

## 2026-08-12 Fill repair cannot monopolize canonical redecision

Live evidence showed the price-channel fill-repair job continuously holding the
trade writer while it rediscovered every confirmed aggregate, loaded every
position, and reconstructed every command link. During that interval canonical
`MONITOR_REFRESHED`, quote projection, and exit redecision writes timed out. The
configured writer `max_hold_ms` was telemetry, not a transaction deadline, so a
bounded materialization count did not bound lock ownership.

The repair moves aggregate discovery entirely onto a read-only connection and
passes one exact aggregate at a time through a fresh attached writer
transaction. The writer-side scan revalidates only that aggregate and commits
before the next aggregate may acquire the lease. Discovery uncertainty records
repair debt and retries next cycle; it never falls back to an unbounded writer
scan. Boot recovery retains its exhaustive one-shot semantics.

SCOPE is one persisted confirmed fill lacking canonical materialization. DRAIN
is the bounded read-only discovery plus one-fill writer tranches on the existing
repair cadence. RESET is an exact canonical position or terminal disposition
for that aggregate. Probability, sizing, order admission, exit economics,
settlement semantics, and lifecycle grammar are unchanged.

Allowed files are `src/ingest/price_channel_ingest.py`,
`tests/test_b5_price_channel_inv37_single_writer.py`, and this plan. Acceptance
requires that scheduled repair performs no full discovery under a writer lease,
releases the lease between candidates, preserves durable retry on uncertainty,
passes focused and fill-bridge regression tests, then proves after exact-SHA
deployment that monitor ages stay inside their watchdog and the repair job no
longer holds the canonical writer continuously.

## 2026-08-12 Terminal no-fill is defeasible by later authenticated fill truth

Seoul exposed a causal contradiction rather than an observability false
positive: command recovery terminalized an ENTRY as `EXPIRED` from a no-fill
snapshot; a later authenticated trade fact and a newer point-order fact proved
11.627905 matched shares with venue remainder, while reconcile repaired only
the position projection. Command truth stayed terminal and the late position
event incorrectly claimed `pending_entry -> active` after the position was
already `day0_window`.

The repair permits the existing fill event grammar to defeat a terminal
no-fill conclusion only with exact, newer, authenticated positive-fill proof.
A late partial correction atomically restores collateral for the still-live
venue remainder before command state can become `PARTIAL`; a full confirmed
fill becomes `FILLED`. Missing identity, stale/equal evidence, mismatched
matched/remainder arithmetic, or failed collateral CAS leaves the command
unchanged and retryable. Entry-fill projection emits from the actual current
phase, so append-only lifecycle history cannot move backward.

SCOPE is one terminal command contradicted by later facts bound to its exact
venue order. DRAIN is continuous M5 reconciliation followed by the existing
cancel/recovery cadence for any live remainder. RESET is a proof-backed
`PARTIAL`/`FILLED` command consistent with its position and collateral truth.
No probability, sizing, price band, settlement, or entry-selection law changes.

Allowed files are `src/execution/exchange_reconcile.py`,
`src/execution/command_recovery.py`,
`src/state/venue_command_repo.py`, `src/state/collateral_ledger.py`,
`src/engine/lifecycle_events.py`, `tests/test_exchange_reconcile.py`, and this
plan. Acceptance requires a terminal-no-fill plus later partial-fill replay to
produce a typed command correction, exact remainder reservation, no lifecycle
phase regression, unchanged exposure economics, rejection of stale/forged
corrections, focused regressions, exact-SHA deployment, and zero active
terminal-command/venue-fact conflicts.

Live verification exposed a missing DRAIN edge after the first deployment:
the account-wide M5 sweep runs only for a WS-gap latch or unresolved finding,
so an authenticated fill already persisted after a terminal no-fill event can
remain contradictory forever without another external trigger. The recurring
entry-exposure-obligation pass now invokes the same strict correction against
persisted canonical facts before deciding whether the obligation can close.
This makes convergence depend on durable debt plus the ordinary command-
recovery cadence, not on a future WS gap; rejected evidence remains open and
reports its exact rejection reason.

The same live proof found that the derived status cut could stay stale while
the held monitor remained continuously active: the observability job yielded
before checking its own freshness budget, so the old PID and pre-correction
command conflict survived every successful scheduler tick. Held-capital I/O
keeps priority only while both status cuts remain inside half their freshness
budget; after that backstop, the read-only pulse and composite refresh run so
operator health cannot ratchet stale. This changes no trading authority.

## 2026-08-12 Replayable substrate writes cannot outrank capital monitoring

Forward runtime evidence showed canonical `MONITOR_REFRESHED` persistence
missing both its initial and five-second retry while the substrate observer was
capturing priority snapshots. The snapshot writer was allowed to hold the
unified trade writer while SQLite waited four to eight seconds on a legacy/raw
writer. That inverts the money path: a replayable market-data projection can
consume the complete deadline of the lifecycle fact that authorizes a held
position's next exit decision.

Ordinary universe snapshots therefore remain cooperative
`BACKGROUND_RECOVERY` writers and fast-yield. Exact held/FC-03 rows are also
replayable, but they are required inputs to the next capital decision: treating
them as one-shot background probes allowed 24 fetched outcome books to produce
zero durable snapshots under transient writer occupancy. They now use the
existing monitor-aware `RECOVERY_CRITICAL` admission with a bounded one-second
queue and a 100ms per-row hold/SQLite quantum. A published MONITOR intent still
overtakes this writer, while a transient non-monitor owner no longer makes every
held row fail immediately. Capture selection priority and writer admission are
separate typed scopes: pending urgency, open-rest, and non-forced markers may
move forward in capture order but remain background writers; only canonical
held condition IDs and forced FC-03 condition IDs enter recovery admission.
Network reads and probability authority are
unchanged; only persistence admission changes. Submit-time JIT recapture is
outside this producer path and retains its own execution authority.

SCOPE is executable-substrate persistence in the recurring observer producer.
DRAIN is a successful short per-row transaction; exact held scope remains
level-triggered from canonical positions and retries on the existing 20-second
cadence until durable, while broad scope fast-yields. RESET is a fresh persisted
snapshot or expiry of its current priority request; a timeout never promotes
stale data. Acceptance requires behavioral antibodies proving exact held/forced
priority snapshots wait through a transient background probe yet yield to
MONITOR, while broad and pending-urgency snapshots remain background; focused
substrate/market-scanner tests, exact-SHA
deployment, and forward evidence that all open positions regain bounded
canonical monitor age while entry candidates continue to be reconsidered.

## 2026-08-12 Producer wakes must drain durable forecast decision debt

Live evidence showed 152 `FORECAST_SNAPSHOT_READY` and 14
`EDLI_REDECISION_PENDING` rows still pending while producer wakes repeatedly
processed only their newly committed target IDs. `targeted_only` disabled the
ordinary debt scan, and the bounded claim page reserved no place for prior
causal facts, so continuous fresh production structurally starved the very
redecision history needed to form new capital-growth proposals.

Each producer bridge claim page now reserves its final slot for the oldest
eligible non-target FSR/redecision debt. Target IDs and the durable global
winner retain precedence in the other slots; ordinary targeted-only callers
retain their old behavior. Claiming debt never accepts its old probability:
the existing current-posterior identity and submit-time authority gates still
recompute or reject it before any venue side effect.

SCOPE is one `edli_reactor_v1` producer bridge invocation and at most one
non-target FSR/redecision row. DRAIN is the existing producer wake cadence,
which claims the oldest eligible debt in the reserved slot. RESET is
invocation-local: terminalization, expiry, successful processing, or loss of
timeliness removes the row and the next wake recalculates the oldest debt.
Acceptance requires selector and real-reactor tests proving a K-sized page
contains no more than K-1 targets when debt exists, no debt is claimed without
the explicit reserve, and current posterior/pause/submit fences remain intact.

## 2026-08-12 Held-monitor probability work is admission bounded

Forward receipts proved that the held monitor could have fresh local books for
14 of 15 positions and still exhaust its full wall-clock claim after starting
slow probability refreshes serially.  The existing one-third reservation chose
the positions that should receive the bounded belief tranche, but it affected
only ordering: every ordinary position could still start a five-second belief
read until the global deadline was gone.  One slow family therefore blinded the
unvisited tail even when quote acquisition used no network.

The repair makes the existing fair bounded-coverage selection the guaranteed
admission contract.  Durable hard-fact exits and structural wins retain their
exact-probability lane.  If any admitted statistical read consumes its bounded
deadline or fails, no non-admitted tail read may start in that pass; those
positions retain the already-prefetched current quote but emit no probability
freshness or action authority, and rotate into the next pass.  When the entire
admitted slice completes quickly, the monitor may use genuine remaining time
for the tail, preserving normal full-book throughput.  Quote acquisition,
probability law, exit economics, lifecycle, and venue submission are unchanged.

SCOPE is one non-hard-fact held position in one monitor pass.  DRAIN is the
next recurring pass's fair bounded-coverage selection.  RESET is admission to
that pass followed by a complete fresh probability witness; a timeout advances
only attempt fairness and never manufactures freshness.  Acceptance requires:

- a 15-position local-book pass whose admitted belief reads consume their full
  allowance starts no more than the admitted one-third slice and starts no tail
  reads after the first expiry;
- the next pass admits a disjoint fair slice rather than retrying the same
  positions;
- a fully successful fast admitted slice may continue through the tail while
  the global deadline still has a complete per-position allowance;
- hard-fact exact-zero/exact-one actions remain outside the statistical belief
admission gate;
- equal-urgency admitted positions consume an already-current local book before
  a network-dependent peer;
- expiry in any admitted child stage (venue-close metadata, q refresh, or
  pending-exit retry quote) closes non-admitted statistical tail admission, and
  receipt ID lists enumerate every position counted as deferred;
- focused held-monitor tests, compile, planning lock, exact-SHA deployment, and
  forward receipt evidence show bounded q starts without full-book deadline
  exhaustion.

Allowed files are `src/engine/cycle_runtime.py`,
`tests/test_live_safety_invariants.py`, and this plan.

## 2026-08-12 Sub-min health binds the held outcome token and current book

Production health classified two `buy_no` holdings with the YES outcome token's
book because its read model joined every position through `token_id`.  It then
reported those positions as currently unexitable even when the selected
snapshot's freshness deadline had expired.  Shares below the venue minimum
were real, but the quoted bid, ask, and current executable classification were
not evidence for the asset Zeus actually held.

The health read model now derives the exit identity symmetrically:
`buy_yes -> token_id`, `buy_no -> no_token_id`.  Missing direction, held token,
or condition identity fails closed.  Only an exact latest snapshot for that
exit token whose aware UTC freshness deadline is still in force may compare
held shares with `min_order_size`; missing, malformed, naive, or expired
freshness is typed read-unavailable rather than promoted to `UNEXITABLE`.

SCOPE is the read-only sub-min live-health surface for one open position.
DRAIN is the existing market-snapshot producer publishing a fresh exact held-
token row. RESET is a valid direction/condition/held-token identity plus a
fresh exact snapshot. This surface remains observability evidence: it neither
authorizes a top-up/sell sequence nor creates a blanket entry veto for
unrelated families.

Acceptance requires opposite YES/NO books to select the held token, both
directions to retain symmetry, invalid identity and stale/malformed freshness
to fail closed, fresh sub-min and at-min arithmetic to remain exact, focused
health tests and compilation to pass, then exact-SHA deployment and a live
sample showing the correct held token identity.

## 2026-08-12 Held raw-HWM reads cannot spend the primary decision reserve

Production monitor pass `414811` froze an 11-family replacement-input HWM as
ready but spent 26.110 seconds doing so; pass `414813` spent 29.970 seconds.
The caller handed the batch the complete auxiliary deadline and a 20-second
CPU-time SQL allowance, despite the monitor contract already defining a
2.5-second raw-HWM stage maximum.  An occasional scheduler, busy-wait, or
validation delay could therefore consume the wall-clock tranche and leave zero
positions at `refresh_position -> evaluate_exit`.

The HWM batch now receives its own absolute wall deadline:
`min(auxiliary_deadline, started + raw_hwm_max)`.  The SQL allowance is the
same remaining wall budget, not the primary belief reserve. The read-only
connection open, initialization PRAGMAs, and snapshot `BEGIN` all consume that
same absolute deadline; no bootstrap step owns a fresh timeout. Because
`sqlite3.connect(timeout=...)` limits only SQLite busy handling, deadline-bound
opens use a daemon handoff: the caller returns unavailable at the wall deadline
and any late connection is closed by its opener. The batch HWM and the Day0
held-family HWM both use this same connection contract. Completion still
freezes one causal HWM cut. Expiry uses the existing typed unavailable snapshot
and fails probability authority closed; it never reuses an older cut, falls
back to a scalar belief, or writes a synthetic HOLD decision.

SCOPE is one held-monitor HWM batch. DRAIN is the next bounded monitor pass
against current raw artifacts. RESET is a complete causal batch within its
independent wall deadline. The remaining primary tranche continues to own
fresh q/book reads and economic decisions; this change does not lower quote,
probability, submit, price-band, or global-auction gates.

Acceptance requires an oversized HWM wait to receive the 2.5-second absolute
deadline through connection bootstrap, `BEGIN`, and the matching SQL allowance;
both HWM caller shapes must pass it and a deliberately delayed connect must be
abandoned and closed;
existing HWM-before-auxiliary ordering must remain intact, typed unavailable
behavior must retain fail-closed authority, and post-deploy decision artifacts
must show HWM wall time bounded while primary position attempts continue.

## 2026-08-12 Held belief has one raw-input HWM authority

The bounded batch alone did not guarantee progress. After freezing its
cycle-scoped HWM cut, `position_belief` independently reopened the large
forecast DB for every held position and recomputed the artifact HWM through an
older JSON-field scan. That private path neither consumed the frozen cut nor
used the shared indexed product/cycle reader. Under live writer and scheduler
load, each duplicate read reached the five-second belief deadline; the monitor
had fresh books but completed no fresh probability decisions, and later passes
could only report `previous monitor cycle is still running`.

Held redecision now delegates both model and artifact frontier reads to
`replacement_input_hwm`, exactly like entry authority. The artifact reader
therefore consumes the immutable batch snapshot (including its typed
unavailable verdict) and uses the indexed product/cycle route when no snapshot
exists. There is no second raw-input interpretation or fallback scan. The
posterior remains fail-closed when the frozen cut is unavailable; recurring
monitor passes, not stale probability reuse, provide recovery.

SCOPE is one held family's raw-input freshness proof inside one monitor cut.
DRAIN is the next recurring batch snapshot plus bounded belief read. RESET is a
complete current shared HWM cut; no old private query can create another
authority. Acceptance requires a relationship antibody proving the held reader
delegates to both shared HWM functions, deadline interruption remains bounded,
all primary-reserve/deadline monitor antibodies pass, and a current 10-position
read sample completes every fresh belief inside one tranche before deployment.

## 2026-08-12 Favorable SELL quotes are not submitted prices

Forward global-auction receipt `415033` selected an immediate Shanghai held
SELL after current probability and book evidence made it capital-positive. The
solver correctly mapped the current `0.999` counterparty bid to a legal `0.95`
submitted SELL floor, but the final executor independently required the
counterparty bid itself to lie inside the submitted-action band and rejected
the exit before command persistence. This collapsed two different facts:
the price Zeus submits and the better price the venue may execute.

The final side-effect boundary now accepts a finite SELL counterparty bid in
the probability domain `[0.05, 1]` while continuing to require every submitted
limit, envelope, command, and SDK request to remain inside inclusive
`[0.05, 0.95]`. A bid above `0.95` therefore improves execution through the
legal floor; it never authorizes an above-band submission. Values above `1`,
below `0.05`, or non-finite remain invalid.

SCOPE is one reduce-only SELL intent at the final executor boundary. DRAIN is
the next current global auction/JIT pass rebuilding the intent from current q
and book. RESET is a current probability-domain bid plus an independently
in-band submitted floor; no stale quote or old winner is replay authority.
Acceptance requires an executor antibody proving `best_bid=0.999` persists and
submits exactly `0.95`, an invalid `1.001` bid still rejects before persistence,
focused executor tests, planning lock, compile/diff checks, hot-fix landing,
exact loaded-SHA proof, and forward winner -> venue command -> fill/progression
evidence.

Allowed files are `src/execution/executor.py`, `tests/test_executor.py`,
`architecture/test_topology.yaml`, `architecture/source_rationale.yaml`, and
this plan. A concurrent inverse change at `e7661feeb` reintroduced the same
type collapse in `src/solve/solver.py`; this hot-fix therefore also owns
`tests/solve/test_solver_properties.py` and
`tests/integration/test_w3_solve_seam_g3.py` so solver, JIT, certificate, and
final executor boundaries enforce one coherent relationship.

Independent review found two remaining consumers of the old collapsed domain:
exact bid `1.0` could not be represented by the shared ask-only `BookLevel`, and
both retry and no-order liquidity recovery treated a favorable `0.999` bid as
still blocked. The structural closeout introduces a distinct `BidBookLevel`
whose domain is `(0,1]`; BUY asks retain strict `(0,1)`. Global epoch capture,
BUY/SELL JIT bid capture, SELL proposal slicing, liquidity admission, retry
release, no-order release, typed execution authority, and the final executor
now preserve this distinction end to end. Submitted limits, envelopes,
commands, and SDK requests remain independently constrained to `[0.05,0.95]`.

The extended allowed set is `src/contracts/executable_cost_curve.py`,
`src/engine/event_reactor_adapter.py`, `src/engine/global_auction_universe.py`,
`src/execution/exit_lifecycle.py`, `tests/contracts/test_executable_cost_curve.py`,
and `tests/test_exit_safety.py`, plus the prior files. Acceptance adds exact
`1.0` JIT-to-executor proof, `>1` fail-closed proof, and old liquidity-debt
release on `0.999` without weakening the submit band.

The exact-one antibody then exposed the same collapsed check one layer earlier
in `src/data/market_scanner.py`: snapshot top-book parsing rejected every price
`>=1` regardless of side. This plan therefore also owns that file and
`tests/test_executable_market_snapshot.py`; current market authority must admit
an exact-one bid but continue rejecting an exact-one ask and any bid above one.
For finite common-axis scoring only, an exact-one raw bid receives a one-current-
tick economic haircut; the immutable raw JIT curve and execution authority keep
the actual `1.0` quote, so Zeus can submit `0.95` and retain favorable fill
improvement without creating an infinite/undefined efficiency value.

Live reconstruction then found a pre-fix Shanghai SELL stranded in
`backoff_exhausted`: its last rejection was the retired executor error
`live_order_executable_price_out_of_bounds: best_bid=0.999`. Correcting the
forward boundary alone cannot clear durable debt created by the old domain
collapse. Retry recovery now recognizes only that exact legacy shape with a
finite bid in `(0.95,1]`, first proves that no EXIT command owns the shares,
then either requests a fresh global q/book/wealth auction for a canonical
global SELL or releases a non-global exit to normal current redecision. It does
not replay the old quote or certificate, and malformed or `>1` values remain
`backoff_exhausted`. Acceptance requires an exact historical-error antibody,
global command-ownership proof, fresh-auction debt creation, and a `>1`
counterexample that remains fail-closed.

The 2026-08-13 live no-order reconstruction exposed a scheduler scope
contradiction after the allocator scope lattice had already recovered.  One
`REVIEW_REQUIRED` cancel-unknown command was exactly bound to market `3535393`;
the allocator correctly isolated that market and left every unrelated family
entry-eligible, but the scheduled command-recovery wrapper still raised a
process-global reactor handoff for every nonzero capital-blocker count.  Its
account-wide venue snapshot repeatedly exhausted the recovery deadline, so the
handoff suppressed the global auction without resolving the scoped debt.
Recovery admission must use the same scope lattice: a scoped unknown keeps its
own market isolated, never reserves the global reactor handoff, and yields its
account/DB work while canonical held-position monitor debt exists. Only a
systemic/unscopeable unknown, or capital debt not represented by the
unknown-side-effect classifier (for example an incomplete confirmed-fill
projection), may reserve the global reactor handoff and retain recovery I/O
priority. SCOPE is the affected market for classified scoped unknowns and
global only for systemic/unclassified capital ambiguity. DRAIN is first the
current held-capital monitor, then the next scheduled venue recovery plus
current wealth revalidation. RESET is the exact command/projection recovery
fact; classifier failure remains global fail-closed. Acceptance requires the
historical single-market cancel debt to leave the reactor runnable and yield to
monitor debt, systemic and unclassified debt to retain the global fence, and
the handoff to clear after success or exception.

Post-deploy evidence showed that recovery yielding was necessary but not
sufficient. The replayable EDLI reactor still ran a reduce-only auction on
every wake while all 26 held obligations lacked current monitor authority. It
repeatedly occupied orchestration, trade-DB, and forecast-DB work long enough
for the monitor's 5-second preparation and 2.5-second artifact-HWM cuts to be
interrupted. The same 25-family HWM read completed 25/25 in under one second on
the canonical read-only DB when isolated, so this is scheduler priority debt,
not evidence that current probability is intrinsically unavailable.

Canonical held-monitor debt now defers the replayable EDLI auction completely,
not merely its BUY side. A reduce-only comparison cannot be executable when
the current q/book authorities required to rank SELL/HOLD/CASH are precisely
the missing monitor facts. SCOPE is only an EDLI auction while exact canonical
monitor debt exists; settlement, command recovery classification, collateral,
and the dedicated monitor lane remain live. DRAIN is the bounded 30-second
monitor-recovery cadence and full-book current redecision. RESET requires a
canonical clean coverage read; elapsed time, a process heartbeat, or a
reduce-only receipt cannot reset it. Acceptance requires reactor deferral under
canonical debt, normal admission after exact reset, a ready 25-family HWM cut,
new MONITOR_REFRESHED coverage for the held book, and only then restart-guard
release and a fresh global auction.

With reactor/recovery contention removed, the next complete monitor cut proved
the artifact HWM ready in 0.284 seconds and completed seven current probability
reads. Four completed results were nevertheless rolled back at the final
position transaction boundary because that commit reused the already-consumed
five-second q child deadline. The child clock governs whether a remote
q/book/decision unit may start and finish; after a canonical monitor event is
constructed, its commit is existing local work and is governed by the unchanged
outer monitor claim. SCOPE is the completed position transaction only. DRAIN is
its commit before the outer monitor deadline. RESET is the next position's new
child deadline; an expired child still cannot authorize new remote work or a
late canonical decision. Acceptance adds an antibody proving that a completed
child commits against the outer claim while a refresh that crosses its child
deadline remains deferred before canonical emit.

The Seoul reconstruction also exposed a separate authority contradiction in
ENTRY recovery. An authenticated canonical order fact already recorded
`matched_size=11.627905`, while a later incomplete account read found no local
trade fact and wrote a new zero-fill fact plus `ENTRY_ORDER_VOIDED`. Order facts
and trade facts are independent evidence planes; absence from the latter cannot
negate positive fill truth in the former. Terminal no-fill construction now has
a shared command+venue-order invariant that refuses every zero-fill append when
any finite positive canonical matched size exists. The incident branch checks
the same invariant before mutation and remains `REVIEW_REQUIRED`, allowing the
existing matched/partial reconciliation lane to establish the economic fill.
SCOPE is one exact command/order identity; DRAIN is the next matched-order
reconciliation pass; RESET is authenticated fill projection or an independently
proved zero-fill identity with no contradictory positive fact. Acceptance
requires the historical positive-order-fact + absent account-read shape to
retain the positive fact, emit no clearance/void, and keep review authority.

Adversarial review found two alternate seams that must obey the same law.
Generic monitor repair may not release a legacy favorable-bid
`backoff_exhausted` position before command ownership classifies it; the shared
release helper therefore refuses that debt unless the typed retry classifier
explicitly authorizes a non-global release. Terminal zero-fill construction
also revalidates positive matched order truth inside the final repository
`INSERT ... SELECT WHERE NOT EXISTS` statement, eliminating a cross-connection
check/append race. A typed contradiction remains `REVIEW_REQUIRED` and lets the
same-cycle matched-order reconciliation lane drain the positive fill; it is not
counted as a generic recovery error. Acceptance adds generic-release bypass
rejection and repository-boundary atomic contradiction antibodies.

## Aggregate position authority over incremental entry-order lifecycle

Live reconstruction on 2026-08-12 exposed a TOCTOU seam between an ACKED
maker-add order and the already-filled position it targets. A recovery pass may
select the maker-add while the aggregate projection is absent, then observe the
earlier fill before mutation. Replaying the stale candidate as a new
`pending_entry` projection silently replaces the active aggregate; when the
maker-add later proves terminal zero-fill, `ENTRY_ORDER_VOIDED` can then void
the real exposure and erase its monitor projection until chain reconciliation
repairs it.

The structural law is order/aggregate separation: an ENTRY command owns only
its own order intent and fill delta. A LIVE zero-fill command cannot rebuild a
same-identity positive aggregate as `pending_entry`, and a terminal zero-fill
command cannot void that aggregate even when the aggregate's current
`order_id` was rebound to the incremental order. Mutation-time authority must
be rechecked by exact position id, held direction, selected token, condition,
positive local-or-chain exposure, and monitorable lifecycle; candidate-time
absence is not authority. Filled increments continue through the dedicated
positive-fill projection path and are not suppressed by this guard.

SCOPE is one exact ENTRY command and aggregate position identity. DRAIN is the
ordinary order recovery pass, which terminalizes only the zero-fill command
while leaving the aggregate under held-position monitoring. RESET is a real
positive fill for that command, which routes through cumulative fill repair,
or disappearance of the pre-existing positive aggregate. Acceptance requires
a deterministic stale-candidate race antibody that preserves phase, economics,
order identity, and monitor fields, plus a same-order-id terminal-zero-fill
antibody that emits no aggregate `ENTRY_ORDER_VOIDED`.

Allowed files for this hot-fix are `src/execution/command_recovery.py`,
`tests/test_command_recovery.py`, and this plan.

## 2026-08-17 Empty provider payload cannot cover a held probability scope

Live NYC LOW reconstruction found a current-cycle raw artifact whose complete
target horizon contained only `null` temperatures. The critical held-family
download gate treated the row's identity as proof of coverage even though the
canonical extractor could not produce a deterministic anchor. The same
syntactically valid file was then reused without another fetch, leaving the held
position read-only after its probability certificate exceeded the absolute age
bound.

Current-target raw reuse and critical coverage now require the existing
canonical local-day extractor to produce at least one finite target-day sample.
Fetched empty payloads are not published as new canonical artifacts; they remain
an explicit skipped result and the next held-priority cycle retries. No
probability freshness, quota, or price boundary is weakened.

SCOPE is the exact held `day0_window`/`pending_exit` city, target date, metric,
and provider cycle. DRAIN is the next critical provider retry, followed by the
existing seed/materialization cycle. RESET requires a materializable raw payload
for that exact scope and cycle; valid JSON, a matching DB row, or an all-null
hourly series cannot reset the gate. Acceptance requires valid-reuse and
all-null-retry antibodies, focused downloader/scheduler suites, exact-SHA live
deployment, and a fresh held-position probability certificate or the current
provider rejection as the remaining fail-closed reason.

The additional allowed files are
`scripts/download_replacement_forecast_current_targets.py`,
`src/data/replacement_forecast_production.py`,
`tests/test_replacement_download_cycle_currency_gate.py`, and this plan.

The first post-deploy exact-scope retry exposed a second identity collision:
scoped held recovery and ordinary universe rotation shared one cursor file, and
the live file still used the prior two-field schema. Exact-scope rotation is now
keyed by its concrete family set, while the single known legacy cursor shape is
read once and upgraded on its next compare-and-swap advance. Unknown fields,
invalid clocks, and corrupt values still fail closed. This keeps critical held
retries independent from ordinary discovery without deleting runtime evidence
or silently resetting a malformed cursor.

The next exact live cut exposed a separate Day0 ownership collision for
Shanghai: a newer observation seed intentionally reused the last materializable
00Z source while its enqueue marker targeted the missing 12Z cycle. Ownership
looked up `target_cycle_time` using the seed's consumed `source_cycle_time`, hit
an older marker, and discarded the newest 28C conditioning seed as stale.
Ownership now reads the latest `enqueue_id` for the exact family and compares
its seed path plus conditioning identity; the witness carries the marker's true
target cycle. SCOPE is that exact family enqueue. DRAIN is the next seed queue
poll. RESET requires a committed posterior consuming the same conditioning
identity; a newer enqueue deterministically supersedes the older owner.

The additional allowed files are
`src/data/replacement_forecast_live_materialization_queue.py` and
`tests/test_day0_extreme_updated_materialization_bridge.py`.

## 2026-08-17 Held Day0 current-q owns the final Open-Meteo reserve

Live Dallas evidence exposed a quota-lane inversion. The new 12Z ensemble was
complete, but the matching provider-center anchor stayed at 06Z. At 9,078
metered requests the ordinary source-clock lane correctly stopped at its 9,000
daily limit, while the exact held Day0 anchor refresh also stopped there even
though the quota contract reserves the final tranche through the 9,500 hard
cap for held Day0 probability. The resulting stale q blocked the current
SELL/BUY/CASH comparison and therefore blocked evidence-based capital release.

Only a canonical `day0_window` or `pending_exit` family may use that final
reserve. Source-commit scopes are partitioned from the current canonical trade
DB before download: exact held-Day0 scopes run first inside `critical_lane`;
all other scopes remain on the ordinary lane. A broad request, an unreadable
position projection, an `active` non-Day0 position, or an unlisted family can
never inherit critical authority.

The same exact held set runs first when the provider-proved `ecmwf_ifs` source
clock advances. That recovery does not depend on a BPF source-commit callback:
the extras may already have committed before quota pressure or process reload,
while the matching anchor is still missing. Its scoped manifest receipt feeds
the existing reseed triggers before the ordinary broad anchor pass; BPF
cooldown cannot postpone held-capital probability to the next quota day.
Critical recovery is idempotent at the canonical artifact identity: a scope
already holding the current-cycle anchor is not downloaded or rewritten.
Coverage instead triggers the scoped reseed with a fresh causal computation
cut. This prevents a recurring poll from advancing immutable
`captured_at/source_available_at` on the same evidence and turning the current
anchor into a perpetual future fact.

That immutability binds the ordinary fanout too. A valid on-disk payload whose
exact source/product/data-version/cycle/scope, path, size, and SHA already match
the canonical artifact is complete work: the downloader reuses it without
rewriting either the payload, companion metadata, manifest, or DB row. A
missing row, mismatched path, corrupt JSON, size drift, or SHA drift remains on
the existing fetch/repin path. Thus quota class changes who may fetch a missing
fact, never whether an already-persisted fact may acquire a later timestamp.

SCOPE is the exact `(city, target_date, temperature_metric)` intersection of a
fresh source commit and canonical held Day0/pending-exit positions. DRAIN is the
bounded scoped anchor download followed by the existing manifest-bound reseed
callback. RESET is per-call: critical authority ends when that exact scoped
download returns, and later calls must re-prove the canonical phase; manifest
currency then causes the existing current-target gate to skip further fetches.
The ordinary lane remains fail-closed at the priority cap and the independent
9,500 hard cap is unchanged.

Allowed files for this hot-fix are
`src/data/replacement_forecast_production.py`, `src/ingest_main.py`,
`scripts/download_replacement_forecast_current_targets.py`,
`tests/test_replacement_download_cycle_currency_gate.py`,
`tests/test_scheduler_adapter.py`, and this plan. Acceptance requires
antibodies proving (1) exact canonical held-Day0 scopes enter critical quota,
(2) mixed batches partition without granting critical authority to ordinary
scopes, (3) broad/nonheld downloads remain ordinary, (4) both ordinary and
critical reuse preserve the first canonical capture timestamps, focused tests, live
deployment, and a new same-cycle Dallas posterior/held-monitor receipt or the
next exact fail-closed reason.

## 2026-08-17 Entry provenance cannot replace held-exit snapshot projection

Live health intermittently reported exact held outcome tokens as
`EXIT_TOKEN_SNAPSHOT_STALE_OR_MISSING` even though the regular market scanner
had recently published a reusable three-minute snapshot.  The append-only
evidence was intact: an entry-only `JIT_PRESUBMIT` row with a newer
`captured_at` and a one-second deadline had unconditionally replaced the
token's `executable_market_snapshot_latest` projection.  Once that one-second
row expired, held-monitor fallback and health saw stale projection state until
the next ordinary scanner refresh.

The snapshot repository now separates immutable evidence append from latest
projection advancement.  Advancement remains the default and is retained by
ordinary scanner and exit `JIT_SUBMIT` writes.  Entry provenance explicitly
appends without advancing latest, so its exact row remains addressable by
snapshot id without narrowing the held-exit reuse window.

SCOPE is only entry `JIT_PRESUBMIT` persistence for one exact condition/token.
DRAIN is the existing ordinary scanner or exit JIT writer advancing the latest
projection with its normal freshness contract.  RESET is the next valid
held-token snapshot; an entry receipt, process heartbeat, or expired one-second
row cannot reset held-exit freshness.  No schema, append-log, lifecycle,
probability, or action-law semantics change.

Acceptance requires antibodies proving that the entry JIT row remains
retrievable by id while the prior reusable latest row remains selected, and
that the default insert path still advances latest.  Focused tests, compile,
diff checks, hot-fix landing, exact loaded SHA, heartbeat, canonical open
exposure, and forward health evidence complete the slice.

Additional allowed files are `src/state/snapshot_repo.py`,
`src/engine/event_reactor_adapter.py`,
`tests/test_k1_stage1_presubmit_snapshot_persist.py`,
`architecture/test_topology.yaml`, and this plan.

## 2026-08-13 Partial fill control state cannot impersonate owned wealth

Live venue truth then exposed the non-terminal twin: Wellington had a confirmed
10-share ENTRY fill and a `PARTIAL` command/order fact, while its canonical
position still contained only the prior lot and the new command had neither an
`ENTRY_ORDER_FILLED` event nor an `execution_fact`. WS ingestion had advanced
the control fold before scheduled recovery; recovery treated that control fold
as if it also proved the position projection and skipped the command forever.

An authenticated cumulative partial fill is absorbed only when the exact
command-bound position event and execution fact reproduce its cumulative shares
and weighted fill price. When `PARTIAL` state and the matched-size order fact are
already durable but those capital authorities are absent or stale, recovery
reuses the control fold and runs only the canonical projection. This prevents a
duplicate command transition while making the actual acquired exposure visible
to monitoring, exit selection, risk, and PnL attribution.

SCOPE is the exact PARTIAL ENTRY command/order and its authenticated confirmed
cumulative fill. DRAIN is the next scheduled authenticated-entry recovery pass.
RESET requires the command-bound `ENTRY_ORDER_FILLED` event and positive
`execution_fact` to reproduce cumulative fill size and price; command state and
matched-size order facts alone never reset the debt. Acceptance requires an
incremental-position race antibody, idempotent second recovery, focused command
recovery tests, live deployment, and convergence of the observed Wellington
fill into canonical position and execution truth.

Allowed files remain `src/execution/command_recovery.py`,
`tests/test_command_recovery.py`, and this plan.

## 2026-08-13 Terminal exit fill is current-capital priority until PnL books

The first executable post-guard auction selected and filled a Shenzhen
immediate-taker SELL. Venue and chain truth showed 6.52 shares sold at 0.40 and
zero remaining chain inventory, but canonical position truth remained
`pending_exit` with the original shares/cost basis and no `EXIT_ORDER_FILLED`,
exit `execution_fact`, or realized PnL. The scheduled recovery classifier gave
priority only to unresolved cancels, in-flight submits, and terminal ENTRY
projection debt; held-monitor debt therefore deferred this completed capital
release indefinitely.

A recent terminal EXIT fill is now the symmetric current-capital blocker until
all three authorities agree: `position_current` is `economically_closed` with
exit price and realized PnL, the exact command/order has `EXIT_ORDER_FILLED`,
and a positive command-bound exit `execution_fact` exists. This changes only
scheduler priority; the existing exact fill, full-exit-intent, lifecycle, and
PnL projection laws remain the sole authority for closing the position.

SCOPE is the exact recent FILLED EXIT/SELL command with bound positive REST or
WS_USER fill truth and incomplete close projection. DRAIN is the next scheduled
command-recovery turn running the existing `exit_pending_projections` pass.
RESET is the three-authority close above; command state, chain zero, or cash
proceeds alone cannot reset it. The one-hour priority window prevents old repair
debt from monopolizing live capital I/O. Acceptance requires a blocker/reset
antibody, focused recovery tests, live deployment, and canonical convergence of
the observed Shenzhen exit including realized PnL.

Allowed files remain `src/execution/command_recovery.py`,
`tests/test_command_recovery.py`, and this plan.

## 2026-08-13 Corrected Day0 facts retain authorized raw provenance

Post-deploy verification found one held Shenzhen position receiving fresh
monitor cycles but no current probability. The exact reason was
`GLOBAL_DAY0_RAW_PROVENANCE_MISSING`: the append-only WU print ledger correctly
canonicalized a same-clock 30C -> 29C correction, while the authorized
`observation_instants` query retained only the SQL maximum 30C projection. A
later authorized 29C projection owned a writer-validated provider digest, but
was discarded before the ledger could transfer that exact digest to its
canonical 29C fact.

The reader retains all authorized, decision-causal local-day instant
projections until the existing ledger correction reduction runs. Same-channel
projections are still replaced by the canonical ledger fact; they contribute
only an exact persisted digest when source and extreme match. No digest is
invented, no alternate source is substituted, and a retracted extreme cannot
win through the projection set. A direct digest lookup that did not reproduce
the existing authority predicates is removed.

SCOPE is the exact city, target date, metric, authorized source channel, and
decision time read. DRAIN is the next held-monitor or global-decision refresh,
which rereads the canonical DB and rebuilds the current fact. RESET requires a
canonical ledger extreme plus a matching authorized persisted digest; process
liveness, a different source, or an old extreme cannot reset the provenance
gate. Acceptance requires the exact Shenzhen correction antibody, the focused
Day0 suite, live deployment, Shenzhen current probability freshness, automatic
restart-guard clearance, and resumed global order decisions.

Allowed files for this hot-fix are
`src/data/replacement_forecast_current_target_plan.py` and this plan. The
already-landed Shenzhen correction antibody is reused unchanged.

## 2026-08-13 Restart proof covers current obligations without dust equality

After Shenzhen probability freshness recovered on the exact loaded SHA, the
restart guard remained closed despite complete current global-auction receipts.
The current monitor set contained 27 executable obligations, while every
receipt contained those 27 plus Miami position `ada88123-2d6`, an exact
0.00857-share residual classified by the auction as
`EXCLUDED:SELLABLE_SHARES_BELOW_PRECISION`. The execution lifecycle correctly
keeps that dust as real exposure, but it is not a current monitor execution
obligation. Requiring equality between those two differently typed sets made
the guard a permanent global entry veto.

A complete post-loaded-SHA receipt now proves restart coverage when its held
identity set is a superset of the current monitor-obligation set. All current
obligations must still appear; duplicate or blank receipt identities, a newly
opened current position absent from the receipt, incomplete held coverage,
stale decision time, or any blocking monitor input still fails closed. Extra
receipt identities cannot hide missing current capital.

SCOPE is only restart-guard recovery after an exact loaded SHA, fresh monitor
inputs, and a complete global-auction receipt. DRAIN is the next complete
auction over the current held set. RESET requires every current monitor
obligation to be present in that receipt; process liveness, a legacy receipt,
or a receipt missing a newly current position cannot reset the guard.
Acceptance requires the superset/missing-current antibody pair, the focused
restart proof suites, live deployment, proof-driven guard reset, and resumed
new-order submissions or their exact current winner rejection.

Allowed files for this hot-fix are `src/ops/monitor_cadence.py`,
`tests/test_ops_scripts_smoke.py`, and this plan.

## 2026-08-13 Day0 digest identity uses the extreme source clock

The exact-clock provenance hardening repaired Shenzhen but exposed a distinct
OGIMET hourly-bucket identity in the held Tel Aviv 33C NO position. Its
canonical ledger high is 34C at 10:20Z, its latest source frontier is 17:50Z,
and the authorized hourly projection explicitly records
`hour_max_raw_ts=10:20Z`, `latest_raw_ts=10:50Z`, plus the validated bucket
payload digest. Matching the digest to the frontier clock 17:50Z incorrectly
made valid current probability unavailable.

Projected facts now retain the writer's metric-specific extreme source clock
(`hour_max_raw_ts` or `hour_min_raw_ts`, otherwise the existing fact clock).
Ledger facts retain the canonical best print's source clock separately from
their latest frontier observation clock. Digest inheritance requires exact
source, station, unit, value, and extreme source clock equality. Frontier time
still advances freshness across a plateau; it no longer impersonates the
physical extreme's provenance identity.

SCOPE is the exact authorized city/date/metric/source extreme used by a current
Day0 probability. DRAIN is the next monitor/global-decision refresh. RESET
requires a writer-validated digest on the projection whose extreme source clock
matches the canonical ledger print; same value at a different clock, a
different source, or a future/unavailable row cannot reset provenance debt.
Acceptance requires the OGIMET peak/frontier antibody, the full focused Day0
suite, live DB replay for Tel Aviv, deployment, fresh probability for
`b065ae33-f9f`, proof-driven restart-guard reset, and resumed global auction.

Allowed files for this hot-fix are
`src/data/replacement_forecast_current_target_plan.py`,
`tests/test_replacement_forecast_current_target_plan.py`, and this plan.

## 2026-08-13 Selected global BUY uses its mandatory JIT book

After entry resumed, the complete auction selected a Paris LOW NO
`TAKER_LIMIT` at `0.56` with positive posterior-mean expected log growth. The
selected native token's current raw CLOB book remained executable and in-band,
but the event-local proof still carried an older `0.98` ask and classified the
exact winner as `LIVE_UNIT_PRICE_OUT_OF_BOUNDS`. Global preflight consequently
requeued the same positive winner without reaching its already-mandatory native
JIT book fetch.

For the exact globally selected BUY only, that stale local-quote rejection may
now reach the existing current-state rebind. No price band changes: winner
preflight must still fetch the selected native token's current raw CLOB book,
reconstruct the full curve, preserve the selected in-band limit and cost, and
pass the independent executor/SDK boundaries before any venue side effect.
Ordinary family selection and a global BUY without the current-state rebind
continue to reject the same stale out-of-band proof.

SCOPE is the exact global BUY winner carrying the typed stale local-quote
reason. DRAIN is its synchronous native-token JIT raw-book fetch and full-curve
rebind. RESET requires current in-band economics that preserve the sealed
winner; a worse, out-of-band, unavailable, or identity-mismatched JIT book
supersedes the candidate. Acceptance requires both sides of the antibody
(global current rebind admits; ordinary admission rejects), focused integration
tests, live deployment, and a new command or a different exact current
preflight reason for the observed Paris winner.

## 2026-08-12 Current-regime capital advantage is the statistical entry license

The last month of chain outcomes is net negative, and forward reconstruction
found statistical entries whose decision probability collapsed after fill.
Current probability and an executable quote are inputs to a trade, not proof
that the selected trade has positive after-cost capital value. Historical
calibrators, mixed probability revisions, and the existing per-target-date
market-relative shadow rule cannot license the current global single-order
auction.

Statistical BUY admission therefore remains fail-closed until one durable,
read-only evaluator proves the exact current probability-semantics revision and
exact current global-selection revision on strictly later VERIFIED settlements.
The independent unit is one target date; the evaluator must use frozen
decision-time executable cost/fill and portfolio wealth, require complete
scope/book/held coverage, and require both a positive one-sided 95% lower bound
of after-cost delta-log-wealth versus CASH over at least 30 independent dates
and positive real on-chain net P&L for the identical revision window. Missing,
mixed, stale, or incomplete evidence is a FAIL artifact, never an identity
fallback or permission to trade.

SCOPE is every statistical BUY proposal; monotone LOCKED/REFUTED Day0 payoff
facts remain hard-fact actions. DRAIN is new immutable global-auction/shadow
receipts plus later canonical settlement and execution facts. RESET is an exact
revision-bound PASS artifact consumed at the existing pre-ranking policy seam;
tests, model score, restart, or an older profitable cohort cannot reset it.
An admission-paused BUY wake may enter the global cut only to freeze that
venue-inert schema-22 counterfactual. The independent actual-BUY rejection
remains active, so proof collection cannot call the venue actuator.

Acceptance first requires the evaluator to reject worktree placeholder DBs,
bind WORLD/TRADES/FORECASTS explicitly read-only, report the exact missing proof
dimensions, and emit a deterministic non-authority artifact. The forward
receipt writer must then freeze selection-law revision, complete-auction
identity, decision-time total wealth/endowment, chosen action, executable cost,
fees, and family/date identity before settlement. Only after at least 30 causal
settlements and positive real net capital may the policy seam admit statistical
BUYs. Until then post-deploy evidence must show zero new ENTRY commands and no
held SELL chosen with non-positive expected objective.

Allowed files for this capital-proof lane are
`scripts/evaluate_current_regime_capital_advantage.py`,
`tests/test_evaluate_current_regime_capital_advantage.py`,
`architecture/script_manifest.yaml`, `architecture/test_topology.yaml`,
`src/engine/global_batch_runtime.py`,
`src/events/reactor.py`,
`tests/integration/test_w3_solve_seam_g3.py`,
`src/contracts/global_auction_receipt.py`, `src/contracts/AGENTS.md`,
`src/events/day0_authority.py`, `tests/test_riskguard.py`,
`src/engine/event_reactor_adapter.py`,
`src/control/live_health.py`, `tests/test_live_safety_invariants.py`,
`tests/engine/test_event_reactor_adapter_family_scoped_entry_block.py`, and
this plan.

### Current collateral refresh must not consume its own writer budget

After the proof-only global cut was allowed to run under the statistical-entry
pause, live reconstruction reached the next genuine current-truth gate:
`CURRENT_WEALTH_COLLATERAL_EXPIRED`. The dedicated 30-second capital sidecar
was alive, but every refresh constructed a path-backed `CollateralLedger` that
re-ran schema DDL before the chain read. Under canonical trade-DB contention,
that incidental `collateral_schema_init` writer lease timed out, so no fresh
snapshot could be published and the global comparison never received current
wealth.

Recurring refresh construction now validates the already-migrated collateral
schema through a read-only SQLite connection and performs no DDL. Process
bootstrap and migrations retain the existing schema-initialization path. A
missing required table or `collateral_reservations.converted_amount` remains a
hard failure before external reads; no stale snapshot or fallback wealth is
substituted.

SCOPE is the current collateral-snapshot refresh cycle. DRAIN is the next
sidecar cycle reading current chain/venue collateral and persisting its bounded
snapshot/head DML under the ordinary writer lease. RESET is a new successfully
committed snapshot with its own capture time; process liveness, an old balance,
or schema validation alone cannot reset freshness debt. The independent
statistical-BUY proof gate remains closed.

Forward live evidence then showed the DDL-free cycle could still miss every
refresh for minutes because its 250ms `STANDARD` snapshot append continually
yielded to registered monitor writers. Current collateral is itself monitor
authority for every held/BUY/SELL global comparison, so only the one-row
snapshot DML now registers as `MONITOR`; schema bootstrap stays `STANDARD`.
The original 250ms acquisition deadline and 250ms maximum hold remain intact.

The additional allowed files are `src/state/collateral_ledger.py`,
`src/execution/post_trade_capital.py`,
`src/main.py`, `src/riskguard/riskguard.py`,
`tests/test_collateral_ledger_global_path_backed.py`,
`tests/test_post_trade_capital_collateral.py`,
`tests/test_p4_post_trade_capital_lift.py`,
`tests/test_startup_wallet_dedup.py`,
`tests/test_startup_wallet_warm_overlap.py`,
`tests/test_riskguard_onchain_bankroll.py`,
`architecture/source_rationale.yaml`, `architecture/test_topology.yaml`, and
this plan. Acceptance requires construction-without-DDL and missing-schema
antibodies, the focused collateral suites, live sidecar restart on the exact
landed SHA, a newly committed current collateral snapshot, and a subsequent
global proof receipt or the next exact fail-closed reason.

## 2026-08-13 Terminal fill projection owns current-capital recovery priority

Live reconstruction after a 100-second FOK submit found an authenticated
Mexico City ENTRY fill in terminal `FILLED` command and confirmed venue-trade
truth while `position_current`, the command-bound `ENTRY_ORDER_FILLED` event,
and `execution_fact` were still absent. The existing filled-entry repair could
reconstruct all three, but `capital_blocking_command_count()` counted only
in-flight submits and unresolved cancels. With held-monitor cadence debt active,
the scheduler therefore classified the missing live exposure as zero capital
blockers and repeatedly deferred command recovery behind a monitor that could
not yet see the unprojected position.

Terminal authenticated positive fill truth is current capital even after the
venue command reaches `FILLED`. The capital-blocker classifier now includes
only an exact FILLED ENTRY command with a bound venue order, a positive
confirmed REST/WS_USER trade fact, and a missing positive same-token canonical
position, command-bound fill event, or positive command execution fact. It does
not treat a fully projected terminal command as unresolved. Current-capital
priority is bounded to the first hour after the confirmed fill (sixty ordinary
recovery cadences); older repair debt remains in the background lane and cannot
turn prior garbage into a permanent live-entry monopoly.

SCOPE is the exact terminal ENTRY command whose authenticated positive fill is
not completely projected. DRAIN is the next scheduled command-recovery turn,
which receives the existing current-capital handoff and runs the existing
filled-entry projection repair. RESET requires all three command-bound
authorities: positive open `position_current`, `ENTRY_ORDER_FILLED`, and
positive `execution_fact`; command status or elapsed time alone cannot reset
the projection, while elapsed time only demotes stale debt from capital-priority
to background recovery. Acceptance requires a missing-projection blocker antibody, its
full-projection reset twin, the focused command-recovery suite, live deployment,
and canonical convergence of the observed Mexico fill.

Allowed files for this hot-fix are `src/execution/command_recovery.py`,
`tests/test_command_recovery.py`, and this plan.
