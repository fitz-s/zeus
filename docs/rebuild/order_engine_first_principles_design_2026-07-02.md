# Order-Decision Engine — First-Principles Optimal Design v2 (2026-07-02)

**Charge (operator, verbatim intent):** Take as GIVEN that the forecast pipeline produces the best
available posterior over settlement, continuously refreshed (new fusion cycles; on observation day,
real observations), more accurate than the market and faster to update — that speed is the alpha
source. No history, no prices-as-evidence, no returns/win-rates, no learning mechanisms inside the
decision engine. Design the optimal order-decision engine on those axioms.

**v2:** v1 was adversarially reviewed (frontier consult, same thread, within-axioms attack;
`/tmp/cgc_answer_engine_review.txt`). Verdict STRUCTURALLY INCOMPLETE: the deletion list and the
one-belief/one-knob identity stand, but four derivations overclaimed and the execution layer lacked
required state. All accepted findings are folded in below; every change keeps zero learned
parameters and zero price-as-belief.

---

## 1. Axioms

- **A1 (honest posterior).** At every time t the probability authority serves, per bin family
  (city × metric × target_date; K mutually exclusive bins, exactly one settles YES), a vector
  `q_t ∈ Δ^K` = P(settlement ∈ bin | all information at t). All uncertainty is inside q.
- **A2 (speed).** q incorporates new information (model cycle, station observation) before the
  market does; price converges toward q with a lag and to the outcome at settlement.
- **A3 (market structure).** Binary YES/NO tokens per bin, thin CLOB books, neg-risk families,
  ~zero fees, known settlement time T. Executable cost = depth-walked book only.
- **A4 (capital).** One small bankroll; capital in positions and in resting-order reservations is
  locked until fill/cancel/sale/settlement.

## 2. Consequences (corrected in v2)

**C1 — Fills carry no settlement information; execution states still carry utility.**
Under A1, `E[Y | q, fill] = q`: delete every mechanism that lets price or fills move *belief*
(selection calibrators, curse bounds, market anchors). This does NOT delete *execution*
conditioning: a maker order is a contingent asset with states
`{resting, partially_filled, cancel_pending, canceled, filled, submit_pending, delayed}`, reserved
cash, and latency. The engine models execution state; it never updates q from it.

**C2 — Begin optimal control immediately; immediacy ≠ crossing.**
The martingale property of q kills *waiting to improve belief*, not *waiting for price improvement*.
Counterexample inside the axioms: q=0.60, ask 0.58, post-only bid 0.50 — crossing earns 2¢ certain;
posting earns 10¢ × P(fill). The correct law: on every event, immediately choose the optimal ACTION
from {FOK/FAK taker, post-only GTD maker, cancel, convert/split/merge, sell, no-op}. Additionally,
cash has option value against *scheduled* future information (a known Day0 observation minutes away
dominates a thin 7-day edge now, because `E[max(edge′,0)] ≥ max(E[edge′],0)`): the solve therefore
carries a **cash shadow value by release-time bucket** derived from the known input schedule
(model-cycle calendar, observation cadence) — deterministic structure, not a forecast of q's
direction, not history.

**C3 — Staleness invariant, physically honest form.**
No NEW order may be submitted, and no resting order may be *treated as valid*, while its family has
arrived-but-unincorporated inputs. Cancellation is not teleportation: on input arrival the affected
orders enter `STALE_PENDING_CANCEL` (block submits → cancel-set out → await cancel/fill truth →
re-solve from reconciled state). Stale exposure is bounded by construction: resting notional per
family ≤ a configured dollar bound, and cancel is always prioritized over submit under rate limits.
Orders carry `q_version` + snapshot id; a fill against a superseded version is absorbed as endowment
and counted by the A2 tripwire.

**C4 — The family is the decision unit; the bankroll is the optimization unit.**
Exactly-one-wins makes the family menu a complete market over K outcomes — direction law and side
lanes dissolve. But with one bankroll across F simultaneously-unresolved families, expected log
wealth depends on the JOINT outcome distribution, not the marginals: weather errors co-move
(region, shared model). Global Kelly therefore requires either (a) **joint scenario samples served
by the probability authority** (it owns the correlation structure — same fusion residual model;
still zero engine learning), or (b) a conservative deterministic correlation envelope (treat
same-region/same-model families as comonotone for sizing). Per-family solve + budget water-filling
is only the F=1 or independence special case and may not claim optimality.

**C5 — Exits are the same solve; the threshold is the marginal-utility one.**
A held token enters the solve as endowment. The sell condition for token i at bid b under log
utility with state-contingent wealth W_j is `b · Σ_j q_j/W_j > q_i/W_i` — NOT the risk-neutral
`b > q_i` (they coincide only when wealth is outcome-flat). The mechanical Day0 case survives:
q_i = 0 with b > 0 always sells. No separate exit lane exists.

**C6 — One discretionary knob κ; required structural state is not a knob.**
κ (fractional Kelly scalar, operator-owned) remains the only discretionary number. But κ cannot
absorb what is missing state, not risk preference: release-time cash buckets and shadow values (C2),
pending-order reservations (C1/A4), joint scenario/correlation structure (C4), and latency bounds
(C3). Those are deterministic structural objects derived from venue mechanics and the input
calendar — not learned, not historical, not tunable.

## 3. The engine — a versioned, state-contingent execution controller

Gap-harvesting (taker) and market-making around q (maker) are special cases of one controller that,
on every event, routes each family to the optimal action given belief q, execution state, and the
global reservation ledger.

### 3.1 State
- **Global reservation ledger (safety-critical, A4):** free cash, per-order reservations, holdings,
  unsettled proceeds — compare-and-swap semantics; every solve requests reservations, submissions
  consume them, cancel-acks release them, fills convert them. Two concurrent family solves can never
  spend the same dollar. Ledger-vs-venue disagreement → global RED (cancel nonessential rests, block
  entries, rebuild from venue/chain truth).
- Per family: `q` + `q_version` + input HWMs; depth-walked book snapshot; own orders with full state
  machine (`submit_pending / live / delayed / partially_filled / stale_pending_cancel / terminal`)
  each stamped (solve_id, q_version, snapshot_id, intended size, matched size, reserved cash).
- Input calendar: known model-cycle release times + Day0 observation cadence → release-time cash
  buckets and shadow values (C2), and `REST_ELIGIBLE` (3.4).

### 3.2 Events
| event | action |
|---|---|
| raw input arrives | affected families: mark q stale, orders → `STALE_PENDING_CANCEL`, cancel-set out (cancel-priority), block submits; on q_version advance + cancel/fill truth reconciled → SOLVE |
| q_version advances | SOLVE affected family |
| book moves materially | SOLVE (belief unchanged; menu changed) |
| fill / partial fill (incl. mid-plan) | ledger update; discard unsubmitted children of the old solve; SOLVE from reconciled truth |
| cancel-ack / not_canceled | ledger release / reconcile `size_matched`; SOLVE |
| schedule tick (input calendar) | refresh cash shadow values |

### 3.3 SOLVE — global, then per-family application
Objective: expected log terminal wealth over the joint outcome distribution (scenario samples or
correlation envelope, C4), over the full menu:
buy YES_i / NO_i along ask depth · sell holdings along bid depth · **neg-risk conversions**
(convert NO_j → YES-basket of the other bins; CTF split collateral → full set; merge full set →
collateral — each with its own cost/latency/failure mode) · post-only maker quotes (contingent
assets valued with reservation cost and C2 shadow values) · cash (with release-time shadow value).
Scale by κ.

**Discrete repair pass:** solve continuous, then repair onto venue quantization (tick rounding,
minimum order size, ≤15-orders-per-batch); submit only if the rounded plan still improves expected
log under worst-price checks. Batch plans decompose into **safe prefixes** — every prefix leaves an
acceptable exposure if later batches fail.

**Pre-submit guards:** internal-crossing check (no own BUY/SELL pair marketable against each other;
no new order crossing an own resting order except via cancel-ack-first replace); reservation
availability; q_version currency; snapshot freshness (FC-03).

### 3.4 Maker feasibility (deterministic, not learned)
`REST_ELIGIBLE(family) ⇔ expected_time_to_next_scheduled_input > cancel_p99 + submit_p99 +
min_rest_lifetime`. When false (e.g. Day0 observation cadence of seconds), the family is taker-only
or no-entry — quotes that cannot outlive their q are never posted. Under rate-limit saturation:
cancels first, coalesce events per family, taker-only fallback. Maker quotes are *licensed* by
feasibility rules (positive edge at quote price under C5 marginal utility, bounded reservation time,
REST_ELIGIBLE) — never optimized via learned fill intensities.

### 3.5 Observation day
No special lane — the same controller under a faster input calendar: absorbing boundary re-zeroes
bins inside q; the solve sells dead holdings into any positive bid and buys sharpened survivors the
lagging market still misprices; short lockup pulls capital here via the shadow values. REST_ELIGIBLE
typically false → taker-only, which is also where A2's lag premium concentrates.

## 4. Axiom tripwires (all learning-free, all fail-closed)

| axiom | cheapest runtime tripwire | fail-closed behavior |
|---|---|---|
| A1 | contract integrity only: q finite, q ∈ Δ^K, family exhaustive, q_version signed, incorporated HWMs ≥ known raw-input HWMs, Day0 absorbing boundary respected, no DATA_DEGRADED | family: cancel rests, block entries; holdings stay endowment. (Calibration telemetry may alarm the operator; it never patches q.) |
| A2 | input→q_version latency SLA; stale-fill counter; "book moved materially after raw input, before q_version" counter | family fail-closed when SLA breached or cancel backlog prevents C3 |
| A3 | metadata hard gates: CLOB active, fresh book, tick/min/negRisk known, fee within envelope, depth-walk available | missing → delete that asset from the menu; family incomplete → family closed |
| A4 | reservation-ledger reconciliation identity (free + reserved + holdings basis + unsettled = bankroll; never negative free under pending races) | global RED: cancel nonessential rests, block orders, rebuild from venue/chain truth |

## 5. Deletion list (unchanged from v1 — the axioms dissolve these)

DELETE from the decision path: q_lcb/bootstrap admission inputs · σ patch-stack as engine-adjacent
protection (ONE dispersion object inside the authority) · coverage/licensing lanes ·
selection_curse_bound & selection_calibrator · market_anchor / any price-into-belief term ·
direction law & per-side stubs · taker quality-floor cluster · Kelly haircut stack (→ κ) ·
maker-rest escalation deadlines (→ event-driven C3 + REST_ELIGIBLE) · separate monitor/exit lane
(→ C5) · strategy lanes / edge_source taxonomy · regret/shadow ledgers as decision inputs · reactor
scan cadence as primary trigger (→ events; scan = liveness backstop only).

KEEP: the probability chain as q producer (now also owning joint scenario/correlation service, C4) ·
executable_cost depth walking · FC-03 · freshness fail-closed · lifecycle enum · INV-37 · RED sweep ·
ARM gate.

## 6. Contracts

- **Probability authority:** per-family q + q_version + input HWMs, minimal input→version latency
  (THE system performance metric, C2/A2), absorbing boundary inside q, all uncertainty inside q,
  and (new, C4) joint scenario samples or a declared correlation envelope across concurrently open
  families. Freshness failure → no version advance → engine stands down on that family.
- **Venue layer:** depth-walked snapshots; full order-state truth (live/delayed/matched/size_matched/
  not_canceled); batch submit; CTF/neg-risk conversion endpoints; rate-limit budget surfaced.
- **Operator:** κ and ARM. Nothing else is tunable.

## 7. Intentionally absent

No historical fitting, walk-forward artifacts, price-conditioned corrections, reliability curves,
win-rate gates, per-city empirical kill-switches, learned fill/toxicity models. If q is honest they
are redundant; if q is dishonest the A1 tripwire fails the family closed and the defect is fixed at
the source — never papered over in the engine.

**Identity: one honest belief (with its joint structure), one executable menu (including
conversions), one global state-contingent solve, one knob, zero memory.**

## Appendix — v1→v2 adjudication record

Consult findings accepted: C1 overreach (execution states required) · C2 immediacy ≠ crossing
(post-only counterexample; action-router law) · C2 cash option value vs scheduled inputs (shadow
values) · C4 joint distribution required for multi-family Kelly (authority-served scenarios /
envelope; small-κ makes the residual error second-order but the claim of optimality required the fix) ·
C5 marginal-utility sell threshold (b·Σq_j/W_j > q_i/W_i) · C6 κ ≠ structural state · cancel-flight
STALE_PENDING_CANCEL · submit-flight delayed states · neg-risk/CTF conversion menu assets ·
tick/min-size discrete repair · partial-fill idempotent event-sourcing · self-trade guard · global
reservation ledger (CAS) · rate-limit cancel-priority + event coalescing + REST_ELIGIBLE ·
batch safe prefixes · four axiom tripwires. Rejected: none (all findings were within-axioms and
mathematically verified). Consult's superior-alternative claim ("versioned dynamic execution
controller dominates pure gap-harvesting and pure market-making") is ADOPTED as §3's framing.
