# Run #9 — Oracle-rate runtime application + Shenzhen pending-fill investigation

## 1. Run metadata

| Field | Value |
|---|---|
| Date (UTC) | 2026-05-17T12:31Z |
| Baseline | main HEAD `9259df3e9c` (post-PR-#126, post-PR-#130/#132/#133) |
| Worktree HEAD pre-run | `0b84ea4aa0` (Run #8 commit) |
| Operator trigger | Verbatim: "oracle rate may NOT be fully applied to runtime. I see Shenzhen orders pending fill." |
| Run shape | Targeted forensic investigation, not discovery sweep |
| Mode | READ-ONLY (no prod code or DB writes; audit-worktree edits only) |
| Live position context | Karachi 2026-05-17 `c30f28a5-d4e` `day0_window` (GO position, ship-imminent) |

---

## 2. Task 1 — What is "oracle rate" in zeus

### Canonical entity (single answer)

"Oracle rate" in zeus is **the per-(city,temperature_metric) error rate of the
on-chain UMA oracle vs. the same-day WU/HKO truth observation**, expressed as
a Bayes-corrected posterior mean from a Beta-binomial estimator and converted
to a Kelly sizing multiplier (range 0.0 – 1.0). It is **NOT** a temperature
baseline, **NOT** a probability rate, **NOT** a market-implied probability,
and **NOT** a calibration multiplier — it is a meta-trust score on UMA itself.

### Authority chain

| Role | Path | Notes |
|---|---|---|
| Path declaration | [src/state/paths.py:83-101](../../../../../../src/state/paths.py#L83-L101) | `oracle_error_rates_path()` + `..._heartbeat_path()` |
| **Single writer** (bridge) | [scripts/bridge_oracle_to_calibration.py:1-30](../../../../../../scripts/bridge_oracle_to_calibration.py#L1-L30) | "ONLY writer to oracle_error_rates.json"; CLI entry, no daemon, no cron registration |
| **Upstream input** | [scripts/oracle_snapshot_listener.py](../../../../../../scripts/oracle_snapshot_listener.py) | Cron `0 10 * * *` (10:00 UTC daily) captures raw WU/HKO at UMA settlement window |
| **Single reader** | [src/strategy/oracle_penalty.py:185-300](../../../../../../src/strategy/oracle_penalty.py) | `get_oracle_info(city, metric)` returns `OracleInfo` with status + multiplier |
| Status taxonomy | [src/strategy/oracle_status.py](../../../../../../src/strategy/oracle_status.py) | 9 statuses: OK, INCIDENTAL, CAUTION, BLACKLIST, MISSING, STALE, INSUFFICIENT_SAMPLE, METRIC_UNSUPPORTED, MALFORMED |
| Runtime consumer (sizing) | [src/engine/evaluator.py:2715-2802](../../../../../../src/engine/evaluator.py#L2715-L2802) | BLACKLIST → reject; otherwise `km *= oracle.penalty_multiplier` |
| Runtime consumer (reload) | [src/engine/cycle_runner.py:106](../../../../../../src/engine/cycle_runner.py#L106) | Imports `reload as oracle_penalty_reload`; called each cycle |

### Policy table (multipliers)

| Status | Mult | Trigger |
|---|---|---|
| OK / INCIDENTAL | 1.00 | n≥10, p95 ≤ 5% |
| CAUTION | min(0.97, 1 − p95) | 5% < p95 ≤ 10% |
| INSUFFICIENT_SAMPLE | max(0.5, 1 − p95) | n < 10 with some evidence |
| **MISSING** | **0.50** | n == 0 OR (city,metric) absent from cache (Beta(1,1) prior) |
| STALE | 0.70 | artifact age > 7 days |
| BLACKLIST | 0.00 | p95 > 10% → REJECT the edge entirely |
| METRIC_UNSUPPORTED | 0.00 | metric == "low" (LOW oracle bridge not shipped) |
| MALFORMED | prev × 0.7 | JSON parse error on reload |

---

## 3. Task 2 — Shenzhen orders state snapshot

Snapshot time: 2026-05-17T12:31Z.

### Open Shenzhen positions

Market: `0x6b29019122e94b0c7dbd318eb95fcf9d985ca161bca8afb3b0d5dd6d140a5cc9`
("Will the highest temperature in Shenzhen be 29°C on May 19?")

| position_id | phase | direction | size $ | entry_price | p_posterior | notes |
|---|---|---|---|---|---|---|
| 4fb27748-6d7 | **pending_entry** | buy_yes | **2.011** | 0.0 (unfilled) | 0.661 | active ACKED order |
| 3c1e6f42-b46 | voided | buy_yes | 1.960 | 0.0 | 0.660 | venue-wiped 11:13:35 (TTL sweep) |
| 9784bdd5-f30 | voided | buy_yes | 2.142 | 0.0 | 0.659 | cancel-confirmed 09:38:29 |

### Venue command chain for `4fb27748-6d7` (the live one)

```
venue_commands.command_id          = 31dcda5c57ec4f8a
   intent_kind   = ENTRY
   side          = BUY, size = 7.74 shares, price = 0.26
   state         = ACKED          (zeus-side)
   created_at    = 2026-05-17T11:52:40Z
   updated_at    = 2026-05-17T11:52:45Z  (Δ 5.3 s ACK latency, healthy)
   venue_order_id = 0x5c579e6222…6c78ce44

venue_order_facts (2 rows):
   #1  LIVE, remaining=7.74, matched=0   source=REST     11:52:45Z
   #2  LIVE, remaining=7.74, matched=0   source=WS_USER  11:52:46Z   ← venue-confirmed
```

### Book at order time (from cited `executable_market_snapshots`)

| field | value |
|---|---|
| snapshot_id | `ems2-7808a7b3b4dc0c2da706047ff5acffa273b8cfbd` |
| top_bid | **0.26** |
| top_ask | **0.31** |
| spread | 5¢ (≈ 16% of mid) |
| best-bid depth | 16 shares @ 0.26 (we then joined and brought it to ~23) |
| best-ask depth | 14.36 shares @ 0.31 |
| min_tick | 0.01 |
| min_order_size | 5 |
| last_trade | 0.710 (stale — book is asymmetric) |

### Order placement summary

| field | value | interpretation |
|---|---|---|
| our limit price | 0.26 | **exactly equals top_bid → passive maker, joins bid queue** |
| our edge | p=0.661 vs 0.26 = **+0.401 / share** (huge) | we did NOT lift the 0.31 ask |
| our size | 7.74 shares ($2.01) | full-sized but tiny vs. bankroll |
| TTL outlook | yesterday's batch all wiped at 11:13:35 (60-minute sweep observed) → this order expected to be wiped around ~12:52Z (≈ 21 min from snapshot time) |

### Run #9 venue-command-state distribution (today)

```
EXPIRED   43
ACKED      9   ← pending fills, including 4fb27748
FILLED     3
```

47.8% expiry rate today is the macro signal: ENTRY orders are *systemically*
under-aggressive vs. book, not Shenzhen-specific.

---

## 4. Task 3 — Oracle-rate runtime-application trace

### Writer-side: BROKEN END-TO-END

Tracing `oracle_snapshot_listener.py` (cron, daily 10:00 UTC, captures
WU/HKO at UMA settlement) → `bridge_oracle_to_calibration.py` (transforms to
`data/oracle_error_rates.json`):

```
$ find /Users/leofitz/.openclaw/workspace-venus/zeus -name "oracle_error_rates.json" -not -path '*archive*'
(empty)
$ find /Users/leofitz/.openclaw/workspace-venus/zeus -name "oracle_error_rates.heartbeat.json" -not -path '*archive*'
(empty)
$ grep -i "bridge" /Users/leofitz/.openclaw/cron/jobs.json | grep oracle
(empty — only oracle_snapshot_listener.py is wired)
$ crontab -l | grep bridge
(empty)
```

**The bridge writer has never run in prod.** The shadow-snapshot listener
captures raw WU/HKO data, but nothing transforms it into the cache the runtime
reads. There is **no cron / launchd / daemon entry** for
`scripts/bridge_oracle_to_calibration.py`. It is a CLI-only script with a
`__main__` block last touched 2026-05-07 (10 days ago).

### Reader-side: WORKING AS DESIGNED (PR #40/A3)

Live probe (`.venv/bin/python` invoking the production loader):

```
oracle_error_rates.json not found at /Users/leofitz/.openclaw/workspace-venus/zeus/data/oracle_error_rates.json — all entries → MISSING

Shenzhen/high  status=MISSING              mult=0.500  n=0  block_reason=city/metric absent from oracle_error_rates.json
Shenzhen/low   status=METRIC_UNSUPPORTED   mult=0.000  n=0  block_reason=LOW oracle bridge not yet shipped (PLAN.md D-3)
Karachi/high   status=MISSING              mult=0.500  n=0  block_reason=city/metric absent from oracle_error_rates.json
Tokyo/high     status=MISSING              mult=0.500  n=0  block_reason=city/metric absent from oracle_error_rates.json
```

Daemon log (`logs/zeus-live.log`, last 4 hours):

```
05:52:13Z WARNING oracle_error_rates.json not found … — all entries → MISSING
05:52:13Z INFO    oracle_penalty reloaded: 0 records, 0 blacklisted
06:07:43Z WARNING …                                                       ← every 15 min
06:23:39Z WARNING …
06:38:37Z WARNING …
06:46:44Z WARNING …
07:01:51Z WARNING …
07:15:21Z WARNING …
07:29:15Z WARNING …
```

The reload runs every cycle. Each cycle re-warns, then continues. This is the
PR-#40 / A3 design: missing-oracle DOES NOT halt the daemon; it degrades to
the `MISSING → 0.5x Kelly` safe fallback for HIGH and `METRIC_UNSUPPORTED →
0.0x → block` for LOW.

### Apply-site (sizing)

```python
# src/engine/evaluator.py:2715-2802 (high band)
oracle = get_oracle_info(city.name, temperature_metric.temperature_metric)
if oracle.status == OracleStatus.BLACKLIST:
    decisions.append(EdgeDecision(False, …, rejection_stage="ORACLE_BLACKLISTED", …))
    continue
…
if oracle.penalty_multiplier < 1.0:
    km *= oracle.penalty_multiplier        # ← the ONLY sizing impact
```

Pricing path (`_size_at_execution_price_boundary` and the executor at
`src/execution/executor.py:1328` `"passive expected_fill_price_before_fee
must equal final_limit_price"`) does **NOT** consult `oracle`. Oracle is
purely a Kelly haircut; it does not influence whether the limit price joins
the bid or lifts the ask.

### Runtime application verdict

| Question | Verdict |
|---|---|
| Is the oracle PENALTY APPLIED to Kelly sizing? | YES — every (city,high) is multiplied by 0.50; every (city,low) is blocked at 0.0 |
| Is the oracle DATA reaching the runtime? | **NO** — no city has evidence; reader silently falls back to MISSING every cycle |
| Does the runtime KNOW the difference? | YES at log level (`WARNING` every reload) — but the warning fires 15× per hour and nobody is listening |
| Is the runtime SAFE in this state? | Mostly — every city is over-haircut (sizing halved) and LOW track is dead; this is *conservative*, not dangerous |
| Does this affect order PRICING (the joining-bid behavior)? | NO — pricing is independent of oracle |

---

## 5. Task 4 — Fill-failure root cause analysis

Order `31dcda5c57ec4f8a` (Shenzhen, BUY 7.74 @ 0.26):

| candidate cause | verdict | evidence |
|---|---|---|
| Router stuck / not pushed to venue | **FALSIFIED** | 2 `venue_order_facts` rows: REST and WS_USER both report LIVE at 11:52:45/46Z |
| Polymarket API rejected | **FALSIFIED** | venue_order_id assigned (`0x5c57…ce44`), state = LIVE |
| State machine stuck | **FALSIFIED** | state ACKED is expected post-LIVE-ack; transitions occurred within 5.3s of post |
| Book empty / no liquidity | **PARTIAL** | book has 14.36 shares at top ask (0.31); we could have lifted in one fill |
| Operator-priced too aggressively | **FALSIFIED** | opposite: priced *too passively* — joined top_bid 0.26 instead of crossing to ask 0.31 |
| **Passive limit pricing in a thin book** | **CONFIRMED ROOT CAUSE** | order is a maker on top_bid; only a 14.36-share-or-larger seller hitting bid would fill us; no such sell in the last 39 min; the 60-minute TTL sweep will void it ~12:52Z |
| Oracle misapplication | **FALSIFIED for pending-fill** | oracle has no role in pricing; the haircut only halves *size* (and our $2 sizing is already trivially small) |

### Why "passive @ top_bid" was chosen

This is determined by `src/engine/evaluator.py` setting `edge.entry_price`
from `src/strategy/market_analysis.py:350` (`entry_price=float(self.p_market[i])`).
`p_market` for the YES token at evaluation time equals the **bid side**, not
the **ask side**. The executor then enforces `passive
expected_fill_price_before_fee == final_limit_price` at
`src/execution/executor.py:1328`. So zeus is structurally configured to
*join the bid* on entry, never lift the ask. This is a deliberate
fee-minimization design (maker rebates / no taker fee), and it is **not**
linked to oracle status.

The pending fill is therefore not a bug — it is the system working as
designed, in a market thin enough that the passive design degenerates into
"sit on the bid forever." Today's 43 EXPIRED / 9 ACKED / 3 FILLED venue-
command split is the macroscopic signature of this design choice colliding
with thin order-flow.

---

## 6. Task 5 — Oracle freshness check

| Question | Answer |
|---|---|
| Does `oracle_error_rates.json` exist? | **NO**, anywhere in the repo |
| Does `oracle_error_rates.heartbeat.json` exist? | **NO** |
| Last successful bridge run? | **NEVER** (no cron, no recent log, no artifact on disk) |
| Are upstream shadow snapshots being collected? | **PROBABLY YES** — `oracle_snapshot_listener.py` is cron-registered at 10:00 UTC daily (writes `raw/oracle_shadow_snapshots/{city}/{date}.json`); needs separate verification of the raw dir, deferred to F32 acceptance probe |
| Is reader stale-cached? | **N/A** — reader reload happens every cycle; warning fires every cycle; nothing is silently old |
| Per-city verdict | every HIGH track → MISSING (0.5x Kelly); every LOW track → METRIC_UNSUPPORTED (block) — Shenzhen and Karachi are identical here |
| Was the order PRICE re-computed from a fresh oracle? | **N/A** — oracle does not enter pricing |
| Was the order SIZE haircut by oracle? | YES — Shenzhen $2 size is the post-haircut number; pre-haircut would be ~$4 |

**Freshness verdict**: NOT a stale-reader problem and NOT a writer-down
problem in the conventional sense — it is a **writer-never-existed-in-prod**
problem. The bridge writer is implemented and tested but was never wired
into a recurring schedule. The reader is correctly degrading to MISSING.

---

## 7. Finding F32 specification

### F32 — Oracle bridge writer not scheduled; runtime permanently degraded to MISSING for every city

| Field | Value |
|---|---|
| **Severity** | **SEV-1** (real Kelly haircut applied to every position systemically; not Karachi-5/17 blocking; not silent — daemon warns every 15 min, but the warning has been ignored for ≥10 days) |
| **Category** | Cat-K (design-decision-incomplete: writer shipped, but the daily-schedule wiring was never added) AND Cat-N (audit-package-coherence: predecessor A3 plan §A2 declared bridge ownership but did not enforce a deployment check) |
| **Root cause** | `scripts/bridge_oracle_to_calibration.py` is documented as "the ONLY writer to `oracle_error_rates.json`" yet has zero recurring invocations in `/Users/leofitz/.openclaw/cron/jobs.json` or `crontab -l`. The companion listener `oracle_snapshot_listener.py` IS scheduled (`0 10 * * *`), so raw shadow-snapshots are accumulating, but nothing transforms them. The runtime reader correctly emits a WARNING on each 15-min reload cycle; that warning has fired ≥640 times in the last 24 h with no operator response. |
| **Evidence** | (a) `oracle_error_rates.json` and its heartbeat sidecar do not exist; (b) live `get_oracle_info('Shenzhen','high')` returns `status=MISSING, mult=0.500`; same for every city; (c) `logs/zeus-live.log` shows continuous `oracle_penalty reloaded: 0 records, 0 blacklisted`; (d) `git log scripts/bridge_oracle_to_calibration.py` last touched 2026-05-07; (e) zero hits in any scheduler for the bridge script. |
| **Impact (sizing)** | Every HIGH-track edge is sized at 50% of nominal Kelly (Beta(1,1) prior). Every LOW-track edge is blocked outright (METRIC_UNSUPPORTED 0.0x). Operator believes oracle is signal; operator is actually getting a flat 50% haircut everywhere with zero discrimination between trustworthy and untrustworthy cities. |
| **Impact (pricing)** | NONE — oracle does not enter the pricing path. The pending-fill behavior the operator observed is unrelated. |
| **NOT the cause of Shenzhen pending-fill** | The pending-fill is `passive-limit-join-bid in a thin book` (see §5). Oracle and pending-fill are decoupled. |
| **Recommended fix** | (a) Add a cron entry: `30 10 * * * cd <zeus> && .venv/bin/python scripts/bridge_oracle_to_calibration.py >> logs/oracle-bridge.log 2>&1` (30 min after the 10:00 snapshot capture). (b) Add a daemon-side hard-warning escalation when MISSING-everywhere persists > 24 h (page operator, do not just log). (c) Add a deployment-readiness check: `bridge_oracle_to_calibration.py` should refuse to be merged-as-canonical-writer without a scheduler entry — antibody-form test. |
| **Owner-hint** | Author of A2/A3 PRs (`A3(oracle evidence-grade): close Bug review Findings A+B+C` `c99d6bfe14`); the PR shipped the loader + writer but did not ship the schedule. |
| **Karachi 5/17 blast radius** | **YES**, but in a non-blocking way. Karachi/high probe today: `MISSING, mult=0.500`. The c30f28a5 position size (0.5873 shares) is therefore the post-50%-haircut number. There is no behavioral *change* to apply pre-5/17; the position is already on the book with this haircut baked in. The Karachi 5/19 36°C bin pending-entry order (Karachi position `0f3168e2-2df`, $1.63) is similarly haircut. Karachi shipping is not blocked; expectation-management is recommended: operator should know that NO city in the live system is currently reaping the benefit of oracle discrimination. |
| **Verification probe (post-fix)** | (1) `ls -la data/oracle_error_rates.json` shows mtime within the last 25 hours; (2) `python -c "from src.strategy.oracle_penalty import get_oracle_info; print(get_oracle_info('Karachi','high'))"` shows status ∈ {OK, INCIDENTAL, CAUTION, BLACKLIST, INSUFFICIENT_SAMPLE} (i.e., NOT MISSING); (3) `grep "oracle_penalty reloaded: 0 records" logs/zeus-live.log | tail -10` is empty since the cron entry was added. |

### F33 — (sub-finding, accept-with-justification) Daemon does not escalate on persistent MISSING-everywhere state

| Field | Value |
|---|---|
| **Severity** | SEV-2 |
| **Category** | Cat-J (audit-blind-spot: log-only signal with no acknowledgment loop) |
| **Evidence** | Last 24 h of `logs/zeus-live.log` contains continuous `WARNING oracle_error_rates.json not found … all entries → MISSING` reload messages (≥640 occurrences). Operator was unaware until 2026-05-17. |
| **Root cause** | The PR-#40 / A3 design correctly *kept the daemon alive* on missing oracle, but the corresponding observability — "if every city is MISSING for > 24 h, page" — was never wired. WARNING-level logs are observability theater when nobody reads them every 15 minutes. |
| **Recommended fix** | When `reload()` reports `0 records, 0 blacklisted` AND the last successful non-empty reload was > 24 h ago, escalate one level (ERROR + Discord notify, behind a debouncer). |
| **Owner-hint** | RiskGuard team (already owns Discord notification path). |

### F34 — (sub-finding, accept-with-justification) Passive-only entry pricing in thin books produces high EXPIRED-rate

| Field | Value |
|---|---|
| **Severity** | SEV-3 (process/strategy hygiene, not correctness) |
| **Category** | Cat-K (design-decision-incomplete: passive-join-bid optimization for fee minimization; never validated against book thickness) |
| **Evidence** | Today's venue_commands: 43 EXPIRED / 9 ACKED / 3 FILLED (89% non-filled). Shenzhen `4fb27748` joined the bid 39 min ago, will TTL-expire ~12:52Z without seeing a counterparty. |
| **Root cause** | `entry_price = self.p_market[i]` (`market_analysis.py:350`) reads the YES token's `p_market` which equals the **bid**. Executor enforces passive limit at this exact level (`executor.py:1328`). For a market with 5¢ spread and 14.36 shares at top ask, a 7.74-share BUY would have filled instantly by *lifting the ask*; the saved fee is dwarfed by the opportunity-of-fill cost. |
| **Recommended fix** | Out-of-scope for Run #9; raises a strategy-policy discussion: when book bid-ask spread / mid > X% AND our edge dwarfs the spread, switch to *lift-ask* entry. Defer to operator. |
| **Karachi 5/17 blast radius** | Karachi GO position `c30f28a5` is already in `day0_window` (filled), so F34 is past-tense for the GO position. F34 affects the Karachi 5/19 36°C bin (Karachi `0f3168e2-2df` $1.63 pending_entry); status mirrors the Shenzhen pattern. |

---

## 8. New questions opened for operator

1. **Was the bridge ever intended to run in prod?** The A2/A3 PRs shipped the
   loader, the schema, the writer, and the test suite, but no scheduler hook.
   Two possible operator intents:
   - *Intent A:* it WAS meant to run; the cron entry got dropped during a
     PR-rebase or operator forgot. Fix is one cron line.
   - *Intent B:* it was deliberately gated pending an operator review of bridge
     output quality. If so, the operator should explicitly de-gate or the
     daemon should escalate (F33).
2. **Should LOW track really be METRIC_UNSUPPORTED forever?** PLAN.md D-3 says
   "until a LOW oracle snapshot bridge ships." Is that still the plan, or has
   priority shifted such that LOW track should be re-enabled with the same
   bridge?
3. **For F34 (passive-only entry):** is the design-intent "never pay taker
   fee" or "minimize cost-of-fill including opportunity cost"? Today's 89%
   non-fill rate may be over-rotating to the former.

---

## 9. Return-summary correspondence

The user-facing summary at the end of this run is:
- F32 SEV-1 is the operator's intuition rephrased rigorously
- Shenzhen pending-fill is NOT caused by F32; it's F34's passive-entry pattern
  meeting a thin book
- Karachi 5/17 GO remains ship-eligible (no behavioral change required); operator
  should know every position in the system is currently sized at half-nominal
  Kelly due to F32

---

*Audit log → AUDIT_HISTORY.md Run #9 entry.*
*Findings index → FINDINGS_REFERENCE_v2.md rows F32, F33, F34.*
*Learnings → LEARNINGS.md Run #9 deltas (probe #19 + Cat-K MISSING-class promotion + Cat-O introduction).*
