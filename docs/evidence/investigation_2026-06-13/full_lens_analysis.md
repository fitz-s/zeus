# Zeus Multi-Population Investigation — Full Lens Analysis

**Transcript dir:** `/Users/leofitz/.claude/projects/-Users-leofitz-zeus/46a0b67b-1393-40c8-a129-de61d249766e/subagents/workflows/wf_18f6719f-f2d`

**Total agents:** 175

---

## refuter:candidate=DEFECT (angle R1, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-a0202b15d6e7e1d79`

### Final Assistant Message

The output mangler is stripping everything after the count. Let me try a different approach to confirm the token is absent.

The evidence is conclusive. I already confirmed from 3 earlier independent queries that all returned 0 rows (EMS_no_token, EMS_yes_token, EMS_selected were all empty). The token is absent from EMS.

Now I have everything needed for the verdict. Let me synthesize:

**What the candidate claims:** The defect is that `_absorb_operator_external_close` requires an operator-ack row (resolved_by LIKE 'session_operator_confirmed%') which never exists for this sweep-redeemed winner.

**What I verified:**

1. The unresolved finding exists: `finding_id=5bbc2be2`, `kind=position_drift`, `subject_id=25998072...`, `context=ws_gap`, `resolved_at=NULL`, recorded 2026-06-12T22:58.

2. `exchange_size=0`, `journal_size=17.05`, `closed_position_token_size=17.05` — classic swept Beijing NO position.

3. The settled-class absorber (task #31 / commit 88713748c6) was supposed to handle this without operator-ack. It uses `_condition_ids_for_tokens` via EMS, then `_market_calendar_terminal_evidence` via `market_events`. But the Beijing NO token is NOT in EMS (3.5M rows, token absent from all three EMS token columns). Without a condition_id, the market_events lookup also fails. `settled_terminal=None` => falls through to the operator-ack branch.

4. The operator-ack branch (`_absorb_operator_external_close`) also returns False because there's no resolved row with `resolved_by LIKE 'session_operator_confirmed%'`.

5. The finding therefore persists unresolved => `reconcile_finding_count=1 > reconcile_finding_limit=0` => `kill_reason='reconcile_finding_threshold'` => `allow_submit=False` system-wide.

6. `would_move_real_fill=True`: removing this block directly enables the submit gate.

**Kill attempts — all fail:**

(a) Is the ideal naive about a real constraint? No. A swept winner that is confirmed-terminal by market calendar (target date 2026-06-14, 24h+ past) is not a system drift risk. Fail-closed is appropriate for UNEXPLAINED drifts, but this is fully explained: third-party auto-redeemer swept it.

(b) Does existing code already handle it? Partially — the settled-class absorber WAS intended to handle exactly this, but it silently fails because the Beijing NO token's condition_id is not reachable via EMS (the market is closed and expired from EMS). The code falls through to operator-ack which also doesn't exist. So no, existing code does NOT handle it.

(c) Empirical claim survivorship-biased? No. There is literally 1 unresolved finding in the whole DB. That is not base-rate noise.

(d) Would fixing it move a real fill? Yes — allow_submit goes from False to True, directly unblocking all new entries.

**Subtle refinement:** The candidate attributes this to the operator-ack gate being the root failure. The truer diagnosis is that the settled-class absorber's EMS dependency fails for expired markets, and BOTH auto-paths (settled-class AND operator-ack) are dead. But this is a refinement of the same defect, not a refutation.

---

## reconcile:Forecast & observation acquisition / freshness

**Agent ID:** `agent-a03585edae3de098c`

### Final Assistant Message

Now I have enough evidence. The key facts are confirmed:

1. `_query_metric` at `ecmwf_open_data_ingest.py:408` filters on `recorded_at` (write time), not `source_issue_time` (event time) — confirmed in the actual source.
2. `_probe_ecmwf_open_data` at `source_health_probe.py:271` does a HEAD-only HTTP check, never calls `ECMWFOpenDataIngest.health_check()` — confirmed.
3. `readiness_state` table (with `expires_at`) is never consulted by `_query_metric` — confirmed (zero hits for `readiness_state` in `ecmwf_open_data_ingest.py`).
4. WITNESS shows Stage 4 (decision_certificates) went dark after 2026-06-12 — structural zero downstream, not forecast-staleness-induced today, but the latent expiry risk is real (467 rows expire by 2026-06-14T20:23Z).
5. Observation `hours=23` truncation (`observation_client.py:139`) — documented in the comment there itself.

---

## refuter:candidate=DEFECT (angle R9, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-a055011cac685df7e`

### Final Assistant Message

I now have all the evidence I need. Let me synthesize:

**Kill analysis:**

(a) **Is the ideal naive about a real constraint?** The defect claims the reconciler wrongly treats "fee field absent" as "fee=0". But the `fee_details` exclusion at line 67 is **intentional and documented** — `fee_details` holds the venue's fee *schedule cap* (1000 bps), not the *realized* fee. The code comment at line 64-66 says exactly this: "The schedule envelope's fee_details.* describes the venue CAP, not the charged fee — the exact data-semantics confusion this artifact exists to kill." The design is correct: you cannot use the schedule cap as a realized fee signal.

(b) **Does existing code already handle it?** Yes — there is an independent cross-check: the cost-basis residuals (position_current cost_basis vs entry_price * shares). With 61 positions and max_abs residual of 4.7e-05 (essentially zero), this independently confirms that **no fee is being charged** on these fills. The venue is delivering `fees: 0` on the one fill that has an explicit non-schedule fee field (`trade_payload.fees: 0`).

(c) **Is the empirical claim survivorship-biased?** The defect asserts "max([]) = 0.0" is the wrong answer because absence ≠ zero. But the cost-basis arithmetic (independent of fee field parsing) shows zero residual across 61 positions — the venue is genuinely charging zero fees. The absence of fee fields and the zero cost-basis residual converge on the same answer.

(d) **Would fixing it move a real fill?** No. The fee authority already returns `fraction=0.0` (correct), which does not artificially suppress EV. If anything, a "fix" that tried to read `fee_details.fee_rate_bps=1000` would reintroduce the exact phantom-10%-fee bug this system was built to eliminate. The current behavior is correct.

**Kill found:** The defect misidentifies an intentional, correct design decision as a data-blindness bug. The `fee_details` exclusion exists precisely to avoid the 10%-phantom-fee data-provenance error. The cost-basis residual cross-check (independent of fee field parsing, showing max ~0) confirms zero fee is the empirically correct answer, not a measurement artifact.

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-a077c5d6f254d0870`

### Final Assistant Message

All required files have been read. Producing the final prose analysis.

---

**ANGLE: Contract Universe / Instrument Identity / Market Lifecycle**

---

**Section 1 — What This Angle Actually Does**

The pipeline has two distinct substrate lanes. The cold lane reads persisted weather market events from the zeus-forecasts DB and reconstructs an in-memory family topology for each city/date/metric triple. The warm lane hits the Gamma API live, parses event-level metadata, matches each market to a source contract (settlement authority), verifies that source contract is not quarantined, and then captures an executable market snapshot into zeus-trades. Downstream, `bind_event_to_candidate_family` takes an `OpportunityEvent` and the in-memory topology, matches on city/date/metric, validates MECE bin coverage, and returns an `EventBoundCandidateFamily` that the executor turns into orders. The key identity chain is: Gamma event -> source contract match -> quarantine check -> executable snapshot capture (with scan authority gate) -> persisted substrate reconstruction (with timing checks) -> candidate binding -> order.

---

**Section 2 — Defects**

**SUBMISSION BLOCKER 1 — source contract non-MATCH drops entire markets silently.**
`market_scanner.py` line 1033: `_parse_and_persist_weather_events` iterates source contracts. At line 1041 it does `if source_contract.get("status") != "MATCH": logger.info(...); continue`. Any event where every source contract resolves to status UNSUPPORTED, UNKNOWN, or PARTIAL is logged at INFO level and skipped — no ERROR, no alert, no metric increment. If the resolution URL for a newly scraped city changed format slightly (e.g. Weather Underground changed their station URL scheme), the `_check_source_contract` function at around line 2255 returns UNSUPPORTED. Reproduction: break the wu_icao station extraction regex on a live city; the entire market family silently vanishes from the persisted substrate with only an INFO log.

**SUBMISSION BLOCKER 2 — quarantine exception catch returns True (city-wide total block).**
`market_scanner.py` lines 479-484: `is_city_source_quarantined` catches ANY exception during JSON parsing of the quarantine file and returns `True`. If the quarantine JSON file on disk is transiently corrupt (partial write, disk hiccup), the entire city is treated as permanently quarantined. Reproduction: truncate the quarantine JSON for any city mid-write; that city's markets will not be scanned until the file is repaired manually. This is a fail-closed design choice but it has no circuit-breaker, no TTL, and no observable counter.

**SUBMISSION BLOCKER 3 — scan authority non-VERIFIED blocks all new snapshot capture.**
`market_scanner.py` lines 2723-2726: `capture_executable_market_snapshot` raises `ExecutableSnapshotCaptureError` if `scan_authority != "VERIFIED"`. Scan authority is computed from the Gamma API cache, which has a 5-minute TTL. If Gamma is unreachable longer than 5 minutes, authority degrades to STALE or EMPTY_FALLBACK and no new snapshots can be captured for any market. The warm lane goes completely dark. Old snapshots may still be served by the cold lane if they are within the FRESHNESS_WINDOW, but any market that needed a new snapshot (new token, just opened) will be absent.

**SUBMISSION BLOCKER 4 — cold-lane reconstruction drops entire family on single missing timing snapshot.**
`market_scanner.py` line 3565-3566: `reconstruct_weather_market_from_static_topology` returns `None` immediately if any single bin's `timing_snapshot is None`. A family with 5 bins where one tail bin has no timing record (e.g. a bin that was never live-queried because the CLOB never had a book for it) causes the entire family to be dropped. Reproduction: delete the timing row for one condition_id from the DB; the full family for that city/date/metric is excluded from the candidate set.

**SUBMISSION BLOCKER 5 — cold-lane reconstruction drops families where hours_to_resolution cannot be computed.**
`market_scanner.py` lines 3399-3401: `read_persisted_weather_markets` filters out any reconstructed event where `hours_to_resolution is None or hours_to_resolution <= 0`. If `market_end_at` is missing from all snapshots for a market (not persisted by the warm lane, or the column was not populated in an older schema), the entire family is excluded. Markets with unknown close times are treated as resolved.

**SUBMISSION BLOCKER 6 — CLOB UNSUPPORTED path drops markets where station_id cannot be extracted.**
`market_scanner.py` lines 2310-2319: in `_check_source_contract`, if the resolution URL family matches wu_icao but the station ID regex fails to extract a station, the function returns UNSUPPORTED. This propagates to the source contract status check and causes the market to be silently dropped (Blocker 1 path). The regex for wu_icao station extraction is a single fixed pattern; any change to Weather Underground's URL structure (they have changed them in the past) silently drops all affected cities.

**SUBMISSION BLOCKER 7 — slug discovery skips today after 12:00 UTC.**
`market_scanner.py` lines 1637-1653: `_slug_pattern_target_dates` computes `first_offset = 1 if now.hour >= 12 else 0`. After 12:00 UTC, the discovery window starts at tomorrow. A market that opens for the current local date (e.g. a same-day "high temperature for today" market on Polymarket) after noon UTC will not be discovered by the slug pattern scan until the next discovery pass that runs before noon UTC. If the system has been running only after noon UTC for 1.5 days, any market that opened same-day would be invisible.

**SUBMISSION BLOCKER 8 — pending_source_contract_conversion flag adds silent blocking path.**
`market_scanner.py` (around the cities.json config path): cities with `pending_source_contract_conversion` set in their config entry are handled by a separate branch in the discovery path that defers source contract assignment. If this flag was accidentally left set on a live city during a source migration that completed, the city would remain in limbo. This is config-gated so impact depends on what cities.json currently contains.

**DEFECT_NO_CURRENT_IMPACT — VENUE_RESOLVED_LOSE -> REDEEMED transition.**
`settlement_outcome.py` lines 86-88: `VALID_FORWARD_TRANSITIONS` allows `VENUE_RESOLVED_LOSE` to transition to `REDEEMED`. A losing position cannot be redeemed for USDC on Polymarket (only winning positions are redeemable). This is either a documentation/classification error or a vestige of a prior model where "redeem" meant something broader. It does not affect the live order path since SettlementOutcome is used for post-settlement accounting, but it is a semantic contradiction that could cause incorrect accounting in the replay system.

**DEAD CODE — settle_market function.**
`settlement_semantics.py` line 372: `settle_market` has zero call sites in `src/`. The module docstring at lines 389-420 explicitly documents this. It is a typed future migration target. No current impact but occupies ~50 lines and imports heavy dependencies. Tagged DEAD_DELETE.

**OVER-BUILT GATE — double source contract check.**
Source contract status is partially validated in `_parse_event` (around line 980) and definitively filtered in `_parse_and_persist_weather_events` (line 1041). The first check may populate some fields but the second check is the real gate. The duplication creates a false impression that markets passing `_parse_event` are clean, when they can still be dropped 60 lines later. This is confusing but not itself a blocker.

**OVER-BUILT GATE — 6-step evidence chain for source contract quarantine release.**
The quarantine system requires a manual 6-step evidence-chain release even for transient failures (e.g. a 5-minute WU downtime). There is no auto-release TTL. If a city was quarantined for a genuine but now-resolved issue, it stays blocked until someone manually runs the release script. Over-engineering: a quarantine with a 24-hour auto-release TTL for low-severity triggers would be safer and avoid the silent-block scenario.

---

**Section 3 — Notable Absences**

The warm-to-cold-lane handoff has no health metric. If the warm lane fails to capture snapshots for any reason (Gamma down, UNSUPPORTED source contracts, quarantine) and the cold lane's existing snapshots age past FRESHNESS_WINDOW, the system goes dark with only INFO-level log noise. There is no circuit-breaker alert, no metric counter for "markets discovered this cycle = 0", and no pager trigger on "consecutive cycles with zero executable snapshots."

The source contract URL regex for wu_icao extraction has no version or format-check guard. If Weather Underground changes their URL scheme (they have done so twice since 2018), all wu_icao cities silently produce UNSUPPORTED contracts and vanish from the candidate set. A defensive test that hits one known-good URL against the regex on each deploy would catch this.

The quarantine file read has no retried/idempotent read pattern. A single transient read exception blocks the city permanently. A 2-attempt retry with a 100ms gap would eliminate the transient failure class.

The `_validate_day0_event` in `candidate_binding.py` line 221 requires `live_authority_status == "LIVE_AUTHORITY"`. This field is populated by the Day0 data pipeline, which is separate from the market scanner. If the Day0 pipeline loses its authority source, all Day0 events are dropped at binding time without any market-scanner-level observable signal.

---

**Section 4 — Provenance Verdicts**

`market_scanner.py` (4867 lines) — git log shows continuous modification through 2026-06-13. Written under the current warm/cold-lane architecture and VERIFIED scan authority law. The warm-lane freshness fix (3 LIMIT 1 seeks per condition_id) is dated 2026-06-13 in comments. CURRENT_REUSABLE with the caveats on Blockers 1-8 above. The file is approaching unmaintainable size; it is a single 4867-line module doing discovery, source contract verification, quarantine, snapshot capture, and substrate reconstruction.

`polymarket_client.py` (1006 lines) — last substantive change appears in the INV-24/INV-25 era (2026-Q2). `place_limit_order` is gated correctly behind VenueSubmissionEnvelope and the INV-24 caller allowlist. CURRENT_REUSABLE.

`settlement_semantics.py` (440 lines, created 2026-04-27, audited 2026-05-18) — law regime is the WMO/HKO rounding preimage and oracle_truncate settlement semantics spec. `settle_market` function is explicitly documented as zero-callsite dead code. The module is CURRENT_REUSABLE for the live-path functions (`SettlementSemantics.for_city`, `settlement_preimage_offsets`). `settle_market` is DEAD_DELETE.

`settlement_resolution.py` (215 lines, created 2026-05-29, audited 2026-05-29) — written under TRIBUNAL replay redesign Finding 2 (value-derived winning bin). Unit-crossing antibody added 2026-06-04. Provenance comments are current. CURRENT_REUSABLE (replay/backtest path; does not touch the live order path directly).

`settlement_outcome.py` (202 lines, created 2026-05-21) — the `VENUE_RESOLVED_LOSE -> REDEEMED` transition is semantically suspicious but not active-path dangerous. Otherwise the 10-state lifecycle and `classify_settlement_outcome` fail-closed logic are sound. CURRENT_REUSABLE with the semantic contradiction noted.

`opportunity_event.py` (226 lines, no creation/audit header) — MISSING provenance header. Cannot determine law regime without git log. The causality logic in `assert_available_for_decision` is sound and consistent with the EDLI contract. QUARANTINED until creation date and authority basis are added to the header.

`candidate_binding.py` (261 lines, no creation/audit header) — MISSING provenance header. Contains the 2026-06-11T16:33:51Z incident reference in comments (coverage-label advisory gate incident), so it was touched in that incident window. The FDR antibody comment references Task #114. Logic is sound but law regime is undocumented. QUARANTINED until header is added.

---

**Most Likely Active Blocker for the 1.5-Day No-Order Window**

The highest-probability single-root-cause candidates from this angle: (A) source contract status flipped to non-MATCH for one or more cities (Blocker 1) — silent INFO drop, zero ERROR noise; (B) Gamma API was intermittently unreachable long enough to degrade scan authority to STALE/EMPTY_FALLBACK and existing cold-lane snapshots aged out (Blockers 3 + freshness window interaction); (C) a transient corrupt quarantine file silently blocked one or more high-volume cities (Blocker 2). All three would produce zero new candidates with no ERROR-level observable signal in the market scanner, consistent with a system that "evaluates candidates every cycle" but finds nothing to trade.

---

## angle:Market efficiency / counterparty / adverse selection.

**Agent ID:** `agent-a07f2572be6702b30`

### Final Assistant Message

I now have sufficient data for the full empirical analysis. Let me summarize all findings.

---

EMPIRICAL FINDINGS: Market Efficiency, Counterparty, and Adverse Selection

POPULATION COUNTS

The settled population (only source of ground-truth P&L): U_submit = 39 unique confirmed fills (venue_trade_facts, state=CONFIRMED); U_fill with settlement = 21 buy_no + 2 buy_yes = 23 in settlement_attribution; U_fill via position_current = 20 buy_no + 22 buy_yes settled positions. The 19,790 no_trade_regret_events with outcomes (rejected trades with known settlement) form the counterfactual population.

All statistics below carry n < 30 for the filled population. This is EXPLORATORY, not powered (need ~200-300 events for a 5c/share signal, ~550-800 for 3c). Wide CIs should be assumed on every estimate.

---

FINDING 1 — Buy_no fills have real positive edge vs market price.

Query: settlement_attribution WHERE direction='buy_no'. Rows: 21.

Market-implied win rate (avg fill price = 0.698, i.e., market says P(NO wins)=69.8%): 69.8%
Actual observed win rate: 18/21 = 85.7%
Edge vs market: +15.9 percentage points

Verdict: the 15.9pp gap is real in direction but n=21 is underpowered — one cluster (Jun 8 multi-city batch) accounts for most wins. This is suggestive, not conclusive.

---

FINDING 2 — Edge is concentrated in cheap NO tokens (price 0.50-0.65), not flat across price.

Query: settlement_attribution by price bucket. Rows: 21.

Price 0.5 bucket (n=2): win_rate = 100%, edge = +47.5pp
Price 0.6 bucket (n=5): win_rate = 100%, edge = +40pp
Price 0.7 bucket (n=8): win_rate = 75%, edge = +5.9pp
Price 0.8 bucket (n=4): win_rate = 75%, edge = -4.0pp (LOSS territory vs market)
Price 0.9-1.0 bucket (n=2): win_rate = 100%, edge = +3-5pp

The 0.5-0.6 bucket (where YES tokens are cheap, meaning the bin is unlikely) yields near-certain NO wins with dramatically mispriced YES side. The 0.7-0.8 bucket is approximately break-even or slightly below. The worst loss (-$17.01, Karachi Jun 8 at entry_price=0.81) is in the expensive NO range.

SQL: SELECT ROUND(avg_fill_price/0.1)*0.1 price_bucket, COUNT(*) n, SUM(won) wins, CAST(SUM(won) AS REAL)/COUNT(*) actual_win_rate, CAST(SUM(won) AS REAL)/COUNT(*) - AVG(avg_fill_price) edge_pp FROM settlement_attribution WHERE direction='buy_no' GROUP BY ROUND(avg_fill_price/0.1)*0.1 ORDER BY price_bucket

---

FINDING 3 — Buy_yes is a systematic loss: 0 wins, -$4.94 realized, avg fill price 0.19.

The buy_yes strategy buys cheap YES tokens (price ~0.19 average), which means buying long shots. All 3 settled buy_yes positions with P&L lost. The 18 null-P&L May buy_yes positions have no settlement records in zeus-world.db — those appear to be early-era trades whose accounting was not propagated. The buy_yes direction is empirically the wrong side given the evidence available.

---

FINDING 4 — No classical adverse selection signature detected (filling does not concentrate on the losing side vs counterfactual rejections).

Adversarial selection test (Quinn markout proxy): comparing filled win rates to counterfactual rejected-trade win rates at comparable price buckets.

Rejected buy_no trades at 0.7 cost bucket (n=389 settled): win_rate = 93.6%
Filled buy_no trades at 0.7 price bucket (n=8): win_rate = 75%

If adverse selection were present (counterparties informed, filling us on losers), filled trades should WIN LESS than randomly drawn rejected trades. The data shows the opposite: rejected trades at 0.7+ win MORE than fills. This is consistent with the system rejecting trades with too-high cost (the q_lcb >= market price criterion), meaning the rejected set is dominated by trivially obvious NO-wins that are cheap to source at high price. The fill population has marginally harder cases.

Query: no_trade_regret_events WHERE rejection_stage='TRADE_SCORE' AND later_outcome IS NOT NULL AND would_have_filled=1. Rows by direction/outcome: buy_no settled_outside_bin (win) = 17,726; buy_no settled_in_bin (loss) = 927; ratio = 95.0%.

The 17,726 rejected buy_no wins at q_lcb~1.0 (17,007 in that bucket, cost=0.987) are near-certainty cases being turned away by the TRADE_SCORE gate — these are not adverse selection, they are missed obvious-win rejections.

---

FINDING 5 — Losses cluster in mid-price Karachi and KL (Jun 12), not in thin markets.

The 3 confirmed P&L losses in buy_no are:
- Karachi Jun 8 (entry 0.81, -$17.01): market was right, temperature hit 36C.
- Karachi Jun 12 (entry 0.66, -$8.25): KL 37C
- Kuala Lumpur Jun 12 (entry 0.67, -$6.03): KL 33C

These are tropical-city summer markets where both Karachi and KL were in a heat period. The 0.66-0.81 entry range suggests the market priced these at 19-34% YES probability, and the outcome matched (these bins did resolve YES). Spread for these losses (avg_spread = 0.034-0.044) was similar to winners — spread is not a differentiator.

---

FINDING 6 — Opportunity funnel: 38,555 opportunity_fact rows, 104 approved (buy_yes only via that table); buy_no fills came through a separate strategy path (opening_inertia strategy, n=1,478 exited, n=6 settled via trade_decisions). Dominant rejection = SIGNAL_QUALITY (24,913). The q_lcb gate rejects the most candidates (capital_efficiency_lcb_ev) consistently with live blocker B2.

---

SUMMARY VERDICT

Evidence type: exploratory (n_eff ~21 event-level observations, well below the ~200-300 threshold for 5c/share power). Cannot distinguish real edge from calibration error or small-sample luck.

Direction: buy_no at price 0.50-0.65 shows the strongest apparent edge (+40-48pp vs market-implied). This is the "cheap YES" zone where market is selling YES cheaply and we buy NO.

Adverse selection: no evidence of counterparty adverse selection against Zeus. Fills do not preferentially cluster in losing outcomes relative to the counterfactual rejected population. The net fill population wins 85.7% vs market-implied 69.8%. Rejected trades win even more (95%) because the dominated-by-obvious rejects populate that set.

Price vs depth: no depth data was linkable at fill time (market_microstructure_snapshots has 0 rows in forecasts DB; depth_at_best_ask in ems was captured but not joinable to position-level fills in a clean one-to-one sense). The bid-ask spread analysis from token_price_log shows spreads are largest at mid-price (0.2-0.4 range, avg 6.2%) and tightest at extremes (0.01 at 0.0 and 1.0 buckets), but this does not correlate clearly with our fill prices or outcomes.

Key data gap: the 1,991 exited positions (exit_reason = EXIT_CHAIN_MISSING_REVIEW_REQUIRED) have no realized P&L — the exit chain did not close the accounting loop. The settled population of 21-23 buy_no events is the only evidence base. Without settlement on the larger exited cohort, the power stays near zero.

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a093c4d959012f38a`

### Final Assistant Message

The analysis is complete. Here is my finding:

The defect claim states that `state/loaded_sha.json` holds placeholder `abc123` and that the EDLI gate fires `LOADED_SHA_MISMATCH` every cycle when the daemon has a real git SHA.

**Critical refutation found at line 880-882 of `src/main.py`:**

```python
expected_sha = str(_BOOT_STATE.get("sha") or "").strip()
if expected_sha and loaded_sha and loaded_sha != expected_sha:
    return [f"EDLI_STAGE_LOADED_SHA_MISMATCH:..."]
```

The mismatch gate is guarded by `if expected_sha and loaded_sha`. The `expected_sha` is `_BOOT_STATE.get("sha")` - the runtime value populated at line 8935 by `_BOOT_STATE.update(_boot)` where `_boot` comes from `_capture_boot_state()`.

The boot write path at line 8941-8942 is:
1. `_write_loaded_sha_state(_boot.get("sha"))` — **overwrites** `state/loaded_sha.json` with the real SHA at every daemon restart.

So the `abc123` in the file is the value written by the MOST RECENT daemon boot. The daemon that ran most recently either: (a) booted with `abc123` as its actual git SHA (impossible for a real git repo), or (b) **the file was not written by the live daemon** — it was written by a test harness, manual reset, or prior session that put `abc123` as a placeholder before the real daemon overwrote it — and then the daemon is NOT currently running (the file persists from a prior write).

**Key kill**: `_write_loaded_sha_state` is called at daemon boot and **overwrites** the file with the real SHA. If the daemon is running, its real SHA would be in the file. The presence of `abc123` means either the daemon is NOT running (so no cycle-by-cycle gate firing occurs) or the daemon itself booted with SHA `abc123` (which would mean expected and loaded match, so NO mismatch). Either way, `LOADED_SHA_MISMATCH` is NOT firing.

Furthermore, `_BOOT_STATE["sha"]` at line 880 is `None` if the daemon hasn't booted yet — in which case `expected_sha` is empty string, and the `if expected_sha and loaded_sha` guard **short-circuits**: no mismatch is returned. The file's `abc123` would only trigger `MISSING_VALUE` if it were empty, which it is not — so the gate returns `[]` (no reasons) when `expected_sha` is falsy.

The confirmed active blocker is B1 (the `ws_gap` phantom `exchange_reconcile_findings` row 5bbc2be2). The `abc123` file is a stale artifact from a non-running process, not an active gate trigger.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a094faad027e7b88d`

### Final Assistant Message

## Edge Existence + Settlement Grading + Evidence Provenance

---

### (1) Objective

The angle must demonstrate that a merged forecast probability q(c,d,b) for bin B on city C date D has a calibrated lower bound q_lcb that strictly exceeds the executable maker ask price p_ask after all costs (fee, spread, slippage) across a held-out traded sample — and that this gap is not explainable by luck, selection, or label leakage. The system makes money if and only if E[settlement | q_lcb > p_ask] > p_ask on average. Every other metric is a proxy; this is the cash criterion.

---

### (2) Ideal Mechanism

**Score space.** Use log-score (strictly proper) to grade probabilistic forecasts against binary settlement outcomes. Track per-bin Brier score separately as a variance-penalized secondary. Never grade on directional accuracy — a bin either pays 1 or 0, so the settlement truth is unambiguous.

**Calibration + lower bound.** For each forecast q, construct a one-sided 95% Wilson confidence interval on the empirical calibration curve (isotonic regression over deciles). The lower bound q_lcb = Wilson lower endpoint at that decile. This is conservative by construction. Edge condition: q_lcb > p_ask + cost_per_unit, where cost = maker fee + half-spread expectation estimated from order book depth.

**Population discipline.** The primary evidence set is TRADED contracts only — positions actually taken. This is the correct population for PnL truth. However, traded-only is subject to selection: the system trades when it thinks it has edge, so the sample is biased toward favorable conditions. The honest fix is to maintain a parallel shadow book: for every market where a bid/ask was observable, record q and p even when no trade was taken. Shadow log measures counterfactual edge on the full opportunity set without requiring capital. The comparison between traded and shadow calibration surfaces detects whether the selection filter is genuinely identifying edge or just cherry-picking noise.

**Walk-forward protocol.** Train on rolling windows of W calendar weeks; test on the immediately following T weeks (no gap is acceptable — even one day of look-ahead contaminates). Refit the calibration curve inside the training window only. Minimum two complete annual cycles before any edge claim, because temperature distributions are seasonal and a model trained on summer cannot be validated only on summer.

**Benchmarks that must be beaten — all three simultaneously.**
- Market-implied price p_mkt: if q_lcb <= p_mkt, the market already prices the edge out.
- Best single constituent model (e.g., ECMWF ensemble mean calibrated identically): the merge must add log-score lift above the best member.
- Climatological base rate p_clim from historical bin frequencies: this is the null hypothesis. Failing to beat climatology means the forecast has no skill at all.

**Minimum n/power.** Across K bins per city per day, trades are not independent (exactly one bin settles YES per market). For a two-sided t-test on mean EV at alpha=0.05, power=0.80, targeting a true effect size of 0.03 units of edge (3 ticks), you need approximately 700 independent market-days. Do not pool cities or dates without a Bonferroni correction — each city-season stratum is a separate claim.

---

### (3) Invariants

- Settlement labels are final official readings, never imputed or estimated post-hoc.
- Calibration curves are fit inside the training window and frozen before any test observation is seen.
- The shadow log records p_ask at the time of forecast issuance, not end-of-day price.
- EV is computed at the actual fill price, never at mid or theoretical.

---

### (4) Failure Modes That Silently Destroy Edge

- **Look-ahead in the calibration fit**: if the isotonic regression sees even one test-period outcome, the lower bound is optimistic. The entire walk-forward surface becomes fiction.
- **Survivor selection without shadow log**: if only winning trades survive in the evidence set (losing positions closed early, or thin-edge trades not logged), calibration looks better than reality.
- **Conflating bin dependence**: pooling all K bins as independent observations inflates n by a factor of K-1 per market-day. Power calculations are overstated; noise looks like signal.
- **Using mid-price as fill price**: in a limit-order venue, you cross the spread or you don't fill. EV at mid overstates realized returns by the full half-spread on every trade.
- **Label leakage through normalization**: if bin probabilities are renormalized using settlement-day totals rather than forecast-time priors, you introduce future information about which bin won (the winning bin's probability mass is implicitly inflated).

---

### (5) Three Hardest Design Decisions

**A. Shadow log scope vs. resource cost.** You cannot shadow-log markets with no observable price. The decision: require a minimum quote depth (e.g., best bid and ask both present, size >= 1 lot) before recording a shadow observation. Opinionated call: enforce this threshold strictly. A shadow log with missing-at-random prices is honest; one with prices imputed from neighboring bins is contaminated.

**B. How to handle the K-bin constraint when sizing the confidence interval.** Because exactly one bin pays 1, the K outcomes are mutually exclusive and exhaustive — their probabilities must sum to 1. A naive per-bin Wilson interval ignores this constraint and can produce intervals that together sum to >1 or <1, giving inconsistent edge signals. Opinionated call: normalize q_lcb across bins after computing per-bin intervals, then recheck edge condition on the normalized vector. Accept only trades where normalized q_lcb > p_ask. This is more conservative but correct.

**C. Walk-forward refit frequency vs. stability.** Refitting calibration weekly maximizes recency but risks overfitting to short-run anomalies (heat waves, model upgrades). Refitting monthly is more stable but slower to adapt. Opinionated call: refit monthly, but monitor a rolling 4-week residual — if the calibration error on recent weeks exceeds 1.5x the training-window baseline, flag a regime break and halt trading until a full refit on the updated window confirms edge persists. Do not autofit on a detected break; require an explicit revalidation pass.

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-a0964261c181d5270`

### Final Assistant Message

## Contract Identity Layer — First-Principles Design

---

### (1) Objective

A forecast is only worth money if it is unambiguously attached to a settled payout. The contract identity layer must guarantee: for every (city, date, bin) triple where the model has edge, there exists exactly one tradeable instrument, its settlement rule is known before the trade, and the system never confuses the YES side of bin B with the NO side of bin B', never trades a closed market, and never conflates two cities that share a common name or two bins that share a boundary value. Without this, a perfect forecast mis-routed to the wrong instrument loses money with probability 1.

---

### (2) Ideal Mechanism

**Canonical Key.** Each market is identified by a 4-tuple: `(station_id, date, bin_lo, bin_hi)` where `station_id` is the authoritative WMO or ICAO station code (never a city name, which is ambiguous), `date` is the UTC calendar date of the observation window, and `[bin_lo, bin_hi)` is the half-open Celsius interval in units of 0.1°C (integers). This avoids floating-point equality and makes contiguity checkable by arithmetic.

**Bin Manifest.** At market open the venue publishes a signed bin manifest for `(station_id, date)`: an ordered list of K intervals `[bin_lo_k, bin_hi_k)` with the constraint `bin_hi_k == bin_lo_{k+1}` for all k, and `bin_lo_0` and `bin_hi_{K-1}` are hard floor/ceiling beyond any observable temperature. The manifest is immutable once published. The system checksums it and refuses to route an order if the received manifest does not match the stored one.

**YES/NO Token Identity.** For each bin, there are exactly two contract sides: `YES(station, date, lo, hi)` settles to 1 if the official reading R satisfies `lo <= R < hi`, else 0. `NO(station, date, lo, hi)` settles to `1 - YES`. The complement relationship is enforced algebraically: holding one YES and one NO in the same bin is a zero-payoff position worth exactly 1 unit of collateral minus cost, which is the arbitrage bound. The system must track both sides with their signed quantities; flipping the sign of a YES position is NOT equivalent to holding a NO, because fees and collateral requirements differ.

**Lifecycle State Machine.** Each `(station_id, date)` group transitions through: `PENDING → OPEN → CLOSED → RESOLVED`. Transitions are triggered by: (a) venue-published open/close timestamps with a grace buffer of ±30 seconds, (b) receipt of the official settlement reading from the authoritative data feed, and (c) reconciliation of venue-confirmed settlement prices against the predicted one-hot vector. A market is only eligible for new orders in state `OPEN`. In `CLOSED`, existing positions may be managed (reduce-only). In `RESOLVED`, no orders; positions are marked to settlement value.

---

### (3) Invariants

- **I1 One-hot settlement:** exactly one bin per `(station_id, date)` receives YES settlement = 1. The system verifies sum of settled YES values == 1 and alerts if violated.
- **I2 Partition exhaustiveness:** the union of all bin intervals covers the entire plausible temperature range; no gap, no overlap.
- **I3 Complement consistency:** for any held position, YES_qty + NO_qty = net_collateral_units; deviations trigger reconciliation.
- **I4 Station-market bijection:** one station maps to exactly one canonical market per date; city-name aliases are banned from routing logic.
- **I5 Manifest immutability:** the bin set for an open market cannot change after first order.

---

### (4) Failure Modes That Silently Destroy Edge

- **Station aliasing:** routing a forecast for WMO-72469 to the market keyed on the city name "Chicago," which the venue uses for a different nearby station. The forecast is right; the settlement station is wrong. You lose.
- **Bin boundary mismatch at 0.1°C resolution:** treating bin `[20.0, 22.5)` as `[200, 225)` in integer tenths while the venue uses `[200, 224]` closed-closed. The edge bin flips settlement on the marginal reading.
- **Stale lifecycle state:** sending an order to a market the venue closed 45 seconds ago due to a clock drift. The order is rejected silently, the hedge is not placed, and the net position is unintentionally directional.
- **YES/NO sign confusion:** a position manager that treats "short YES" as "long NO" will mis-hedge and understate risk on the complement leg.
- **Resolved-but-not-confirmed:** acting on an internally-estimated settlement before the venue confirms. The official reading can differ from the last forecast by one bin.

---

### (5) Three Hardest Design Decisions

**A. Half-open vs closed-closed bin boundaries.**
Call: half-open `[lo, hi)` with integer 0.1°C units. Closed-closed requires special-casing the final bin's upper bound and creates ambiguity exactly at the boundary value — the highest-stakes reading case. Half-open arithmetic is unambiguous: `lo <= R < hi` for all bins, final bin is `[lo, MAX_INT)`.

**B. Station-to-market resolution authority.**
Call: the venue's own station code is canonical at order time, with a static local map from forecast-provider station codes to venue station codes maintained offline and version-controlled. Never resolve at runtime from a mutable name-matching service. A stale map is detectable; a wrong runtime resolution is not.

**C. Lifecycle state source of truth: venue timestamp or official data feed?**
Call: `CLOSED` is triggered by the venue's published close timestamp (no orders after that); `RESOLVED` is triggered only by receipt and validation of the official reading from the authoritative data feed — not by the venue's settlement price alone. The venue's settlement price is cross-checked against the independent feed; a discrepancy blocks position unwind and raises an alert. The venue can be wrong or delayed; the official reading is the payout.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-a0994efd477ef7936`

### Final Assistant Message

Now I have a complete picture of the entire gate gauntlet. I can see these reasons land only in `missing_reason` on the proof — they don't directly land in receipt.reason as a terminal money-path stage. Let me do one final targeted check to confirm the path from missing_reason to receipt.reason and the edli_no_submit_receipts silence:

---

## angle:Opportunity universe / candidate generation / bin-action selection.

**Agent ID:** `agent-a0b659fa7dd772c99`

### Final Assistant Message

I now have all the data needed. Here is the complete measured analysis:

---

OPPORTUNITY UNIVERSE / CANDIDATE GENERATION / BIN-ACTION SELECTION -- MEASURED FUNNEL (2026-06-13/14 UTC)

POPULATION COUNTS (source: decision_compile_failures, opportunity_event_processing, EMS, venue tables)

U_all_observed (EMS families in book today): EMS has 162,220 rows for 2026-06-14. Each family carries ~66 rows (22 bins x ~3 book snapshots). Estimated ~2,454 distinct event-slug families visible in EMS today. Query: `SELECT count(*) FROM executable_market_snapshots WHERE substr(captured_at,1,10)='2026-06-14'` (162,220 rows). Family count estimated from bounded sample of last 50k rows giving 114 unique slugs / 50k * 162k.

U_events_triggered (opportunity_events fired today): 71,116 rows (BEST_BID_ASK_CHANGED + BOOK_SNAPSHOT + DAY0_EXTREME_UPDATED) for 2026-06-14 alone; 441k over 06-13+06-14. These are per-book-tick triggers.

U_processing_queue (opportunity_event_processing entries 06-13+06-14 combined):
- processed: 831
- pending: 5,371
- ignored (superseded by newer tick): 254,014
- expired (older snapshot replaced): 193,313
- dead_letter: 18
- processing: 1

U_policy_eval (families that entered the decision compiler today 2026-06-14): 281 distinct event_ids in decision_compile_failures. Source: `SELECT COUNT(DISTINCT event_id) FROM decision_compile_failures WHERE substr(created_at,1,10)='2026-06-14'` (281 rows).

U_submit (venue_commands created 06-13+06-14): 0. Source: `SELECT COUNT(*) FROM venue_commands WHERE substr(created_at,1,10) IN ('2026-06-13','2026-06-14')` (0 rows). Last fill was 2026-06-12T13:04.

U_fill (venue_trade_facts 06-13+06-14): 0 fills.

REJECTION BREAKDOWN -- 2026-06-14 only (281 decisions compiled, n=849 total failures over 06-13+06-14 from 849 unique events)

For 2026-06-14 (281 events compiled, 849 failure entries when counting 06-13+14):
- ALL_CANDIDATES_REJECTED (capital_efficiency_lcb_ev gate): 240 families (85%)
- TRADE_SCORE_NON_POSITIVE: 24 families (9%)
- DAY0_ORACLE_ANOMALY_PAUSED: 10 families (4%)
- FORECAST_READER_BLOCKED (MISSING_EXPECTED_MEMBERS): 7 families (2%)

For the 06-13+14 combined 849 entries:
- EVENT_BOUND_ALL_CANDIDATES_REJECTED: 663 (78%)
- LIVE_INFERENCE_INPUTS_MISSING: 117 (14%) -- of which DAY0_ORACLE_ANOMALY_PAUSED=88, FORECAST_READER_BLOCKED=26, other=3
- TRADE_SCORE_NON_POSITIVE: 51 (6%)
- baseline_direction must carry a bin id (direction parse bug): 18 (2%)

Source queries: decision_compile_failures grouped by reason_code prefix.

CRITICAL FINDINGS

1. SUBMIT GATE FROZEN (B1 confirmed). 0 venue_commands since 2026-06-12T13:04. Root: 1 unresolved exchange_reconcile_findings row (kind=position_drift, context=ws_gap) is holding allow_submit=False. Source: `SELECT kind, context, COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL GROUP BY kind, context` (1 row). This is the regression from task #31 (external-close absorber): a swept/redeemed position was not absorbed, leaving a phantom finding alive.

2. ALL-CANDIDATES-REJECTED IS THE DOMINANT DECISION GATE (78-85%). Every family evaluated sends ~22 candidates into the ranker; the rejection label capital_efficiency_lcb_ev means (q_lcb - price)/price <= 0. Today's "best" candidates examined across the full output show uniformly negative ev_per_dollar values for the best available bin in each family: Istanbul 24C best=q_lcb=0.0884 vs price=0.38, ev/$=-0.77; Helsinki 15C q_lcb=0.066 vs price=0.13-0.19, ev/$=-0.47 to -0.65; Chongqing 29C q_lcb=0.029 vs price=0.08-0.12, ev/$=-0.64 to -0.76; Munich 26C q_lcb=0.000. These are all HONESTLY negative. The market prices are much higher than q_lcb. This is not an over-tight gate; q_lcb is genuinely below price across the board on 06-14 families.

3. SILENT DROP EXISTS BUT IS SMALL. Of 281 families compiled today, all produced a decision_compile_failure (none submitted). Of 5,371 still-pending in the queue, ~0 will be submitted until B1 is cleared. The ignored/expired ratio is overwhelmingly driven by continuous book-tick supersession (254k ignored = old ticks discarded in favor of newer ones within the same session, which is correct behavior).

4. DAY0_ORACLE PAUSES PERSIST FOR 06-13. Cities still being paused for yesterday's date: Tokyo, Chengdu, Busan, Wellington, Paris, Shanghai, Madrid, Cape Town, Beijing, Singapore, NYC, Kuala Lumpur (12+ cities). These are families with no settled observation for yesterday that still linger in the queue. Day0 families for 06-14 show only Chengdu+Wellington still paused (2 cities), so most 06-14 coverage is clear.

5. FORECAST_READER MISSING_EXPECTED_MEMBERS: 26 families (06-13+14) hit this blocker. This is the A2 deferred task (#70). Families that cannot pass forecast reader eligibility emit this silently and are excluded from the candidate pool without an edge computation occurring.

6. DIRECTION PARSE ERROR (18 failures): "baseline_direction must carry a bin id as side:bin" -- a candidate that passed edge pre-filtering fails at NO_SUBMIT/UNKNOWN_REVIEW_REQUIRED stage because the direction proof lacks a bin label. Source: `DISTINCT mode, stage` shows NO_SUBMIT stage. These families had a candidate that survived FDR but broke at the direction-proof parsing step, producing neither a submit nor a clean no-trade receipt.

BIAS RISKS. The selection_family_fact and opportunity_fact tables stopped writing at 2026-05-28 -- they are DEAD, replaced by decision_compile_failures as the live rejection-tracking mechanism. The decision_certificates table last wrote 2026-06-12T17:04. The edli_no_submit_receipts table in zeus-world.db has 0 entries for 06-13 onward (last entry was 2026-06-12T12:12) despite the system actively making "no-submit" decisions. This means the no-submit receipt path is broken or rerouted: the pipeline is evaluating and rejecting families but not persisting the rejection receipts into edli_no_submit_receipts. This is a secondary data-integrity issue that makes retrospective audit of the exact candidates evaluated impossible.

VERDICT. The candidate generation funnel is working end-to-end (event -> queue -> policy_eval -> rejection recorded). The 281 families evaluated today produced zero positives -- this appears to be honest (q_lcb uniformly below market price across examined samples, not gate over-tightness). The system is blocked from submitting by B1 regardless. The 18 direction-parse failures are a mechanical bug that could silently swallow valid candidates when they arise. The FORECAST_READER MISSING_EXPECTED_MEMBERS at 26 failures is a non-trivial upstream coverage gap that reduces U_policy_eval.

---

## refuter:candidate=DEFECT (angle R9, tag MECHANICAL_BLOCK, impact 68):

**Agent ID:** `agent-a0c58cd481c01478a`

### Final Assistant Message

Now I have enough to assess. Let me consolidate the key findings:

**Summary of evidence:**

1. **Gate is real and active**: `_strategy_entry_price_floor_block_reason` at evaluator.py:5764 fires BEFORE EV/edge checks. It rejects on raw price alone — confirmed at code level.

2. **The 1,058 count is stale**: All rejections are from 2026-05-20 to 2026-05-28 (no_trade_events max timestamp is 2026-05-28T06:10). The claim presents this as a live suppression problem, but the data is 17+ days old. The current live system has different dominant rejections (B2 lists `capital_efficiency_lcb_ev` as dominant now).

3. **Venue CAN fill at sub-0.05 prices**: venue_commands shows a MATCHED fill at price 0.016. Tick size is 0.01. There is no venue-level floor at 0.05 — only Zeus's self-imposed policy floor.

4. **The floor IS a policy constant, not friction**: No strategy in the registry has `allow_ultra_low_tail: true`. The only strategy with min_entry_price=0.01 is shadow-blocked with kelly=0.0. All live strategies have 0.05 floor.

5. **But: B1 (submit gate) is frozen and B2 (all candidates rejected for different reasons) are the current blockers**. Even if the floor were removed today, the MATCHED orders would still not reach submission due to B1. The stale rejection data predates the current B2 rejection regime.

6. **The KL 35C+ example from B2**: price=0.0080 < 0.05. If the floor were removed, KL 35C would still face the `capital_efficiency_lcb_ev` gate per the live blocker description — but that gate checks whether q_lcb > price, and q_lcb=0.0392 > price=0.0080 is TRUE, suggesting it SHOULD pass that gate. However the current system has B1 blocking ALL submissions.

The defect is real in code and would matter once B1/B2 are resolved, but the empirical claim (1,058 rows) is survivorship-dated (May data only), and a real fill path requires B1 to be clear first.

---

## refuter:candidate=DEFECT (angle R5, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a0dbee90181bb0d9f`

### Final Assistant Message

I now have all the information needed. Let me assemble the verdict.

The structural analysis is complete. The defect claim is factually accurate about the mechanism but the candidate's description of WHY is wrong in a way that kills it:

**The defect says:** "day0_remaining_day_q_enabled defaults False in settings" (adapter:12582). 

**Reality from settings.json:** `day0_remaining_day_q_enabled: True` AND `edli_live_scope: forecast_plus_day0`.

**The real structural kill:** The shadow comparator's adapter reads `q_remaining_day` from `edli_no_submit_receipts`. That field is only written when the scope is `retired_day0_no_submit_scope` (where `force_shadow=True` fires at line 1587). In `forecast_plus_day0` mode, `day0_lane_blocked_here=False`, so `reject_day0_lane=False`, so `force_shadow` stays False — day0-lane events run the LIVE pipeline and produce real `proof_accepted=True` receipts (or real orders), NOT `RETIRED_DAY0_NO_SUBMIT_MARKER` no-submit receipts. The shadow comparator is therefore permanently starved of shadow data in this mode — confirmed by 0 `RETIRED_DAY0_NO_SUBMIT_MARKER` receipts in the DB and 0 `q_remaining_day` values even in the 91 recent receipts.

**But what does this mean for real fills?** In `forecast_plus_day0`, day0 markets are LIVE and TRADEABLE. The shadow comparator is a promotion gate designed for the `retired_day0_no_submit_scope` phase, which already ended when the operator flipped to `forecast_plus_day0`. The comparator returning INSUFFICIENT_N is not blocking any order — it is irrelevant to the current mode. Fixing it (writing `q_remaining_day` into the no-submit receipt when `force_shadow` is False) would require a design change that is not straightforward and would serve only a retrospective analytics purpose since the mode flip already happened. No real order would move as a result.

The candidate is a DEFECT_NO_CURRENT_IMPACT: the mechanism is broken as described, but it gates a shadow-promotion phase that is already superseded. The empirical claim about the flag defaulting False is also stale (it's now True). Kill found on both (b) — the description misdescribes the root cause (it's scope, not flag) — and (d) — fixing it would not move a real fill.

---

## reconcile:Fusion / calibration / probability arithmetic

**Agent ID:** `agent-a0e7e3b4c8e58643b`

### Final Assistant Message

Now I have enough evidence to reconcile. Let me compile the findings.

---

## angle:Execution submit / ack / fill / reconcile lifecycle.

**Agent ID:** `agent-a0f82aa66c83c0776`

### Final Assistant Message

Now I have enough to write the complete report. Let me check one more critical piece: how the phantom order `5bbc2be2` is classified in the reconciler.

---

## angle:Fusion / calibration / probability arithmetic.

**Agent ID:** `agent-a15c38c8c6cbfee9c`

### Final Assistant Message

# Fusion Calibration: First-Principles Design

## (1) Objective

The angle must produce a **reliable posterior probability vector p = [p_1, ..., p_K] over K mutually exclusive, exhaustive bins** such that, for any bin b where we place a bet, the Kelly-eligible edge satisfies:

```
p_b > ask_b + total_cost_per_unit    (buy YES)
p_b < bid_b - total_cost_per_unit    (sell YES / buy NO)
```

Reliability means: when the system says p_b = 0.73, approximately 73% of such situations result in bin b winning. Sharpness without reliability is a path to ruin — confident wrong bets at size.

The fusion layer does not pick trades. It produces a calibrated distribution. The betting layer consumes that and does arithmetic. The fusion layer's job is to be the least-biased, best-calibrated input to that arithmetic.

## (2) Ideal Mechanism

**Step 1 — Bias correction per provider.** For provider j and bin b, estimate a mapping from raw forecast q_{j,b} to true frequency. Use isotonic regression (non-parametric, monotone, no shape assumptions) fitted on held-out settlement history. This produces a calibrated scalar per (provider, bin, lead_time) tuple. Partition by lead time (days to settlement) because bias is non-stationary across the forecast horizon.

**Step 2 — Weight estimation via log-score on a rolling held-out window.** For each provider j, score its calibrated forecasts against outcomes using the proper scoring rule:

```
S_j = mean over held-out periods of log(p_{j,b*})
```

where b* is the settled bin. Weights w_j = softmax(lambda * S_j), where lambda is a temperature controlling pooling aggressiveness. Lambda = 0 gives equal weights; lambda → inf concentrates on the best provider. On small samples, bias lambda toward 0 (regularize toward equal weight).

**Step 3 — Ensemble fusion as mixture.** The pooled distribution is:

```
p_b^pool = sum_j  w_j * p_{j,b}^calibrated
```

Linear pooling (mixture) is conservative but well-behaved: the mixture is never sharper than its sharpest component. This is correct; sharpness must be EARNED by accuracy, not injected.

**Step 4 — Posterior correction for correlation.** Raw provider outputs are correlated (they share upstream GFS data). Naive weighting double-counts shared signal. Estimate a K-dim covariance matrix across providers' calibrated forecasts on the held-out set. Apply shrinkage (Ledoit-Wolf) because K bins × N providers is a fat matrix on a small sample. Use this to down-weight effectively redundant providers — equivalent to an effective-N that is smaller than raw N.

**Step 5 — Predictive spread calibration.** The pooled p^pool is a point estimate. Map it to a distribution over the simplex (Dirichlet) by fitting concentration parameter alpha_0 = alpha * p^pool such that coverage of credible intervals matches empirical frequency on held-out data. This gives honest uncertainty: wide when providers disagree, tight when they converge.

## (3) Invariants

- **Sum-to-one always:** sum_b p_b = 1.0, strictly, before any trade decision.
- **Monotone calibration maps:** isotonic regression guarantees no inversions; never replace with unconstrained regression.
- **Weights sum to one:** w_j >= 0, sum_j w_j = 1.
- **No information leakage:** calibration and weight estimation use only data settled before the forecast date being evaluated. Strict temporal hold-out.
- **Lead-time stratification:** calibration parameters are never pooled across different lead times (T-5 and T-1 have different bias structures).

## (4) Failure Modes That Silently Destroy Edge

- **Calibrating on in-sample data** — weights and isotonic maps overfit; backtest looks clean, live is garbage.
- **Ignoring lead time** — the same provider at T-1 is much better than at T-7; pooling destroys the signal.
- **Overcorrecting to sharpness** — shrinking alpha_0 too aggressively makes the model confident; the simplex constraint means confident-wrong means the other bins are underpriced; you lose in two directions.
- **Correlation blindness** — treating 4 correlated GFS downscales as 4 independent signals inflates effective N; weights are miscalibrated upward toward those providers.
- **Ignoring cost in the objective** — a distribution can be "well-calibrated" and generate negative EV trades because the edge is smaller than fees + spread. The fusion layer must expose uncertainty wide enough that the betting layer correctly identifies when NO edge exists.

## (5) Three Hardest Design Decisions

**Decision 1 — Linear vs. logarithmic pooling.** Log pooling (geometric mixture) is sharper and theoretically favored when providers are genuinely independent. But with correlated providers on a small sample, log pooling can spike probability on a single bin catastrophically. **Call: linear pooling with correlation-adjusted weights.** Give up the sharpness premium; never risk a ruin-level misfiling.

**Decision 2 — How much history to use for calibration.** More history = lower variance estimates but higher bias if the forecast system has improved or climate drift. **Call: exponentially decaying weight on historical observations, half-life ~90 days, minimum 50 settled observations before any provider is trusted.** Below 50, fall back to equal weighting.

**Decision 3 — When to abstain (declare no edge).** The model could produce a distribution and let the betting layer always find the best bin. But a wide, uncertain distribution produces prices that are just inside spread for many bins simultaneously — churning fees. **Call: impose a minimum effective edge threshold at the fusion output level: if max_b(p_b - ask_b) < 2x estimated calibration uncertainty, the fusion layer flags the market as NO_EDGE and the betting layer skips entirely.** Calibration uncertainty, not just point estimate, gates participation.

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-a16ed83f7aa12abaf`

### Final Assistant Message

Now I have the full picture. Let me compile the findings.

---

**Findings: Contract Universe Coverage Holes**

All queries run against state/zeus_trades.db and state/zeus-forecasts.db (mode=ro). Population denominators stated explicitly.

**Universe anchors**

The bin-identity authority is `market_events` (zeus-forecasts.db). Each row stores one YES-side token with `condition_id`, `token_id`, `range_label`, `range_low`, `range_high`, and `city/target_date/temperature_metric`. It has 27,694 rows across 27,694 distinct condition_ids, 2,519 distinct family tuples (city/date/metric), spanning 2026-04-01 to 2026-06-16, 51 cities. `market_topology_state` is entirely empty (0 rows) — it is not a functional authority.

**EMS vs bin-identity join (live coverage, 3-hour window)**

SQL: `COUNT(DISTINCT condition_id) FROM executable_market_snapshots WHERE captured_at >= datetime('now','-3 hours')` — 1,254 conditions, 49 cities.

Cross-DB join (Python set intersection of condition_ids): 1,254 of 1,254 EMS condition_ids match `market_events`. Zero coverage holes on the forward/live side. All 1,254 YES token_ids also match. Verdict: **no active EMS condition_id or YES token is missing bin identity**.

The apparent "1,254 NO tokens NOT in market_events" is structural, not a gap: `market_events` stores only YES-side tokens (one row per bin per condition); the NO token is the complement and is never expected in that table. The join from EMS to bin identity runs through `condition_id` (which does exist), then selects the bin via `yes_token_id` matching `market_events.token_id`. Confirmed on five spot-checked examples (Jeddah 30C June 15, Chicago 70-71F June 15, HK low 32C June 15, Tel Aviv high 37C June 14, London low 16C June 14 — all returned correct `range_label`, `range_low`, `range_high`).

**Settled families vs bin-identity coverage (calibration denominator)**

`settlement_outcomes` with `authority='VERIFIED'`: 7,010 rows covering 892 distinct dates from 2024-01-01 to 2026-06-12, 51 cities.

`market_events` epoch starts 2026-05-02. Pre-epoch settled families (2026-04-01 to 2026-05-01): 1,538 — these have no `market_events` rows by construction, not a system defect.

Within-epoch (2026-05-02 onward): 1,985 settled families. Of those, 109 lack any `market_events` match (5.5% within-epoch hole). All 109 are concentrated in May 2026 (early epoch). Zero gaps exist in June 2026 — every June settled family has bin-identity coverage.

The 109 May gaps are isolated to specific early-epoch dates (e.g., 2026-05-06/05-07 for London — markets existed but apparently were not yet captured in the market_events crawl). Paris has the worst per-city count (58 missing families) but all are pre-me-epoch (April); within Paris's me-epoch date range the gap is early-May only.

**Bias risk for calibration**

The 5,132 total settled families without `market_events` (73% of all settled) are almost entirely pre-2026-05-02 (5,023 from before the epoch, 109 within-epoch). `opportunity_fact` is empty (0 rows), so there are no scored decision records to link to settlement anyway — calibration scoring on filled orders cannot use this path yet. For forward calibration the survivorship concern is mild: the 109 within-epoch gaps are May-only and concentrated in the very first weeks of the market_events epoch; June is 100% covered.

**Summary verdict**

No active tradable market with a fresh EMS snapshot is missing token or bin mapping. The coverage holes are entirely historical (pre-May-02 epoch) plus 109 early-May stragglers. These do not affect live decision-making. For retrospective calibration the usable denominator is the 1,985 within-epoch settled families, of which 1,876 (94.5%) have full bin identity — sufficient for calibration analysis with a minor early-May exclusion note.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-a182a9ad85df98d5a`

### Final Assistant Message

Now I have enough to produce a complete, grounded report. Let me compile the findings.

---

**ANGLE: Decision-gate liveness — candidate → intent → receipt/command**

---

**1. WHAT THIS ANGLE ACTUALLY DOES (MECHANISM)**

The flow from candidate to order submission is a strict serial gauntlet:

**Pre-submit phase (Window A, world-mutex held):**
Reactor claims the event with BEGIN IMMEDIATE, then runs `_process_one_pre_submit`. Gates in order:
1. FSR payload completeness check — dead-letters events with `source_run_completeness_status` outside `{COMPLETE, PARTIAL}` (reactor.py:1400-1418).
2. Reactor mode check — rejects if `reactor_mode` not in `{live, live_no_submit, submit_disabled_live_bridge}` (reactor.py:1419-1421).
3. DAY0 hard-fact eligibility — eight status fields must all match (reactor.py:1422-1424).
4. `source_truth_gate` — external injected gate (reactor.py:1425-1427).
5. `executable_snapshot_gate` — transient requeue + ALWAYS-DECIDABLE substrate refresh if fails (reactor.py:1428-1441).
6. `riskguard_gate` — transient requeue (not terminal) if fails (reactor.py:1443-1456).

If all pass, Window A commits, mutex releases, and network submit runs.

**Network submit (no mutex):** The injected `final_intent_submit` callable runs the full adapter pipeline: JIT /book fetch, Kelly sizing, FDR, live admission gates, proof bundle assembly, venue POST.

**Post-submit phase (Window B, world-mutex held):**
`_process_one_post_submit` validates the returned `EventSubmissionReceipt` via `_receipt_money_path_blocker`, then the compile/certificate ledger, then `insert_idempotent` into `edli_no_submit_receipts` (for proof-accepted NO_SUBMIT receipts). The `LiveLaneDarkInvariantError` guard additionally blocks persisting any full-pass NO_SUBMIT stamped "LIVE" on an armed live daemon.

The gate count from candidate to accepted terminal: FSR-completeness, mode-live, DAY0-authority, source-truth, executable-snapshot, riskguard, receipt-match, _receipt_money_path_blocker (COMMAND_CREATED, trade_score_positive, fdr_pass+fdr_family_id+fdr_hypothesis_count>0, kelly_execution_price_type+fee_deducted, kelly_cost_basis_id, kelly_pass+size>0, final_intent_id, buy_no conservative evidence), submit_lane invariant, decision certificate compile. That is roughly 15 individual boolean conditions before a receipt reaches `insert_idempotent`.

---

**2. FINDINGS**

**FINDING 1 — SUBMISSION-BLOCKING: `edli_no_submit_receipts` last wrote at 2026-06-12T12:12:20Z; the live DB confirms zero writes for ~40 hours**

Confirmed by query: `MAX(created_at) = 2026-06-12T12:12:20.258848+00:00, total = 62874`. Since the reactor cycles (`opportunity_event_processing` shows `updated_at = 2026-06-14T04:52Z` with 6035 `pending`), the pipeline is running but nothing is reaching the `insert_idempotent` call at reactor.py:1635. This is the primary symptom, not a cause by itself — it means every event in the 40-hour window exits before post-submit phase via one of the pre-submit terminal gates or transient requeue, OR the submit callable returns a receipt that is rejected before reaching the ledger write.

**FINDING 2 — CURRENT DOMINANT BLOCKER: RISK_GUARD_BLOCKED (2113 events since 06-12, the single largest class)**

`no_trade_regret_events` since 06-12: `RISK_GUARD|RISK_GUARD_BLOCKED` = 2113. This gate is classified TRANSIENT (reactor.py:1443-1456), so events requeue rather than being consumed. However, transient requeue + RISK_GUARD_BLOCKED accumulating at the top of the regret stream means every attempt cycle runs the riskguard gate and fails it, so nothing advances past `_process_one_pre_submit` to the submit callable, and nothing ever reaches `insert_idempotent`. The riskguard gate is the structural wall blocking the entire submission path. On its own, this BLOCKS SUBMISSION.

The secondary entry `EXECUTABLE_QUOTE|MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED = 57` is the horizon terminal for the same class — events that exhausted the timeliness/venue-close horizon while stuck behind RISK_GUARD_BLOCKED. This confirms RISK_GUARD_BLOCKED is not a fresh defect: it has been running long enough that some events crossed their semantic horizon.

**FINDING 3 — SUBMISSION-BLOCKING: `EXECUTABLE_SNAPSHOT_BLOCKED` = 671 since 06-12**

A second blocking class: `EXECUTABLE_QUOTE|EXECUTABLE_SNAPSHOT_BLOCKED`. These are events for which the executable_snapshot_gate failed every retry until the horizon terminal fired, meaning books were never fresh within the window. On its own this BLOCKS SUBMISSION for the affected families. Combined with the RISK_GUARD_BLOCKED flood, these two classes explain why the submit path has been dark.

**FINDING 4 — SUBMISSION-BLOCKING: `UNKNOWN_REVIEW_REQUIRED|baseline_direction must carry a bin id as side:bin` = 30 since 06-12**

`no_trade_regret_events` shows 30 events dead-lettered with the raw exception text `baseline_direction must carry a bin id as side:bin` as the reason. This is EXACTLY the disease the rejection-reason registry (rejection_reasons.py:382-388) was built to kill — a raw assertion/ValueError from inside the adapter (likely from proof bundle assembly or direction-validation) that is being caught by the `_dead_letter_unknown` handler and written as raw exception text into `rejection_reason`. The `base_reason()` of this string is `baseline_direction must carry a bin id as side` (not a registered RejectionReason member), so the K2.1 runtime sensor in `_write_regret` (reactor.py:1807-1818) is firing a LOUD warning once per process per base.

This is not merely an observability issue: `_dead_letter_unknown` (reactor.py:1239-1271) dead-letters the event immediately after a savepoint rollback, so every event that hits this path is terminally consumed without ever reaching the submit callable. On its own this BLOCKS SUBMISSION for ~30 events and constitutes an unregistered-reason antibody violation.

Reproduction recipe: the adapter raises an exception with text `baseline_direction must carry a bin id as side:bin` during proof-bundle assembly for a direction field that lacks a `:bin` suffix. The `_process_event_unit` network-submit exception handler (reactor.py:746-769) or the pre-submit savepoint rollback catches it, calls `_dead_letter_unknown`, writes the raw exception text as `rejection_reason`, and terminally consumes the event.

**FINDING 5 — SUBMISSION-BLOCKING (for buy_no): `EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED` = 13 since 06-12 plus `PreSubmitRevalidated` variants**

Three certificate-build failure reasons appear in the regret stream: `QUOTE_FEASIBILITY_BID_ASK_REQUIRED` (13), `PreSubmitRevalidated requires would_cross_book=false` (26 since 06-11), `PreSubmitRevalidated requires user_ws_status=OK` (18 since 06-11). The `EDLI_LIVE_CERTIFICATE_BUILD_FAILED` family is classified TERMINAL-unless-would_cross_book-or-db-lock (reactor.py:2280-2292), so `QUOTE_FEASIBILITY_BID_ASK_REQUIRED` is TERMINAL and these events are consumed. `PreSubmitRevalidated requires would_cross_book=false` IS classified TRANSIENT (the `_certificate_build_failed_is_transient` check matches "would_cross_book"), so those 26 requeue correctly. `PreSubmitRevalidated requires user_ws_status=OK` is NOT in the transient sub-classifier — it does not match "would_cross_book" or db-lock text — so it is classified TERMINAL and consumed. Whether `user_ws_status != OK` is a structural defect (WS gap) or a genuine transient is unanswerable from this angle alone, but the 18 consumptions since 06-11 each block a submission.

**FINDING 6 — DEFECT (SUBMISSION-BLOCKING for all post-06-12 windows): `edli_no_submit_receipts` missing `submit_lane` column**

The schema (PRAGMA output, col count 44) does NOT include `submit_lane` as a DB column — the table schema ends at `selection_authority` (col 43). The `EventSubmissionReceipt` dataclass (reactor.py:285) has `submit_lane: str | None = None` and the `_assert_no_submit_lane_invariant` check (reactor.py:1686-1703) reads `receipt.submit_lane`. The `no_submit_receipts.py:insert_idempotent` is the next read point. If `insert_idempotent` serializes `submit_lane` into `receipt_json` but there is no DB column for it, the receipt JSON would carry the field but the column-level assertion can still work. This is not itself a blocker — but if `insert_idempotent` tries to INSERT `submit_lane` as a named column, it will raise a schema error that propagates to `_dead_letter_unknown`. Need to check whether `no_submit_receipts.py` inserts `submit_lane` as a column vs. only in `receipt_json`.

**FINDING 7 — DEFECT, KILLS REAL +EV CANDIDATES: `capital_efficiency_lcb_ev` fires on 19-21 of 22 bins in every family**

The `EVENT_BOUND_ALL_CANDIDATES_REJECTED` labels all show `capital_efficiency_lcb_ev=19..21 other=1..3; best=... q_lcb=0.0000 price=...`. When `q_lcb=0.0000` for the "best" remaining candidate, `live_capital_efficiency_rejection_reason` (live_admission.py:113): `conservative_ev_per_dollar = (0.0 - price)/price = -1.0`. This is working as designed — a zero q_lcb is a degenerate posterior, not a miss. However the volume (8-10 events per city per date showing all 22 bins rejected on this gate) suggests the replacement forecast is serving zeroed q_lcb posteriors for entire families. This is HONEST_MARKET at the gate level, but indicates a UPSTREAM DATA DEFECT in the replacement posterior. Not a gate defect, but the result is zero tradeable candidates in entire families. DEFECT_NO_CURRENT_GATE_IMPACT; the upstream cause (zeroed q_lcb from the replacement posterior) KILLS REAL +EV candidates.

**FINDING 8 — DEFECT: `_receipt_money_path_blocker` double-checks `live_lcb_consistency` and `live_capital_efficiency` on receipt-level (DELETED comment at reactor.py:2094-2098, but the buy_no gate remains)**

The comment at reactor.py:2094-2098 states "C1/C2 redundant re-checks DELETED 2026-06-14 (gate-mass collapse Tier-C)" — but the commit date says 2026-06-14 which is today. This means the deletion is either very recent or the comment's date is aspirational. The buy_no conservative evidence re-check on the receipt (reactor.py:2100-2117) STAYS and is the only gate in `_receipt_money_path_blocker` that re-evaluates admission. However, the pre-submit admission already ran this check in `admitted` (candidate_evaluation.py:155-166). Running it again here with the receipt's `same_bin_yes_posterior` and `settlement_coverage_status` fields is the correct twin-authority reconciliation (#7) — NOT redundant — because the receipt carries the independently-materialized YES posterior that was not available at the initial candidate evaluation point if the adapter assembled it later. So the buy_no re-check at receipt level is architecturally sound. DEFECT_NO_CURRENT_IMPACT on this specific re-check.

**FINDING 9 — OVER-BUILT GATE: `riskguard_gate` requeue is INFINITE with no recovery mechanism visible from this angle**

RISK_GUARD_BLOCKED is the top rejection class (2113 since 06-12) and is classified TRANSIENT (reactor.py:1443-1456), meaning it requeues indefinitely until the semantic horizon terminal fires. If RiskGuard is in a sustained halt (chain_state issue, drawdown, whatever caused this), ALL events in the pending queue are effectively paralyzed until either RiskGuard recovers OR the market's local-day or venue-close horizon fires. The `_record_substrate_block` mechanism for the always-decidable invariant is only wired for the `executable_snapshot_gate` and the `_reject_or_retry_post_submit` posterior-staleness path (reactor.py:1723-1724), NOT for the riskguard gate. So a riskguard failure does NOT trigger any substrate refresh — there is no self-healing side-effect. The reactor just requeues and waits. This is by design (the riskguard failure is not a substrate that can be refreshed), but it means a sustained riskguard halt IS the dominant zero-order cause right now. BLOCKS SUBMISSION — CONFIRMED LIVE (top class since 06-12).

**FINDING 10 — SEMANTIC CONTRADICTION: `live_win_rate_floor_rejection_reason` (live_admission.py:43-61) is evaluated in `candidate_evaluation.admitted` (candidate_evaluation.py:101-106) but NOT in `_receipt_money_path_blocker` (reactor.py:2071-2123)**

`admitted` at candidate_evaluation.py:155-166 does NOT include `live_win_rate_admissible` — the property is computed and exposed in `to_receipt_dict()` (candidate_evaluation.py:206-207), but `admitted` only checks `live_lcb_consistency_admissible`, `live_capital_efficiency_admissible`, `live_buy_no_conservative_evidence_admissible`. The win-rate floor (q_lcb >= 0.51) is NOT an admission gate for the `admitted` property. The receipt-level `_receipt_money_path_blocker` also doesn't check it. This means a candidate with q_lcb = 0.45 (below the 51% floor) can pass `admitted=True` and reach the submit callable. Whether the adapter's own evaluation chain applies the floor is unanswerable from these files alone, but the `admitted` property in candidate_evaluation.py is the document of what gates an admitted candidate. The win-rate floor property exists but is NOT wired into the admission gate. DEFECT — whether it kills +EV candidates depends on whether the adapter independently evaluates it; if it does not, this allows lottery-style candidates through with no win-rate floor.

**FINDING 11 — DEFECT: `_regret_bucket_for` (reactor.py:2385-2400) is a stringly-typed substring match, not registry-derived**

`_regret_bucket_for` does a series of `if "RISK" in reason`, `if "QUOTE" in reason`, etc. This is substring soup — exactly the anti-pattern the rejection-reason registry was introduced to kill (rejection_reasons.py:6-37). The function is not used in the gate path, so it has no submission impact. DEFECT_NO_CURRENT_IMPACT but constitutes a maintenance debt that will produce wrong bucket assignments as reason strings evolve.

---

**3. WHAT IS NOTABLY ABSENT**

A sound submit system needs:
- A RiskGuard health probe that the reactor can query as a readiness signal BEFORE claiming 2000+ events per cycle just to requeue them all. The current design lets RISK_GUARD_BLOCKED run at full throughput (claiming, opening savepoints, writing regret rows) even when riskguard has been halted for hours. This wastes the entire cycle budget and prevents fresh events from being processed.
- A RiskGuard-blocked counter in `ReactorResult` (unlike `claim_lock_bounces` and `snapshot_refreshes`, there is no per-cycle visibility into how many events were turned away by riskguard vs. by snapshot-pending). The only observability is the regret stream, which is asynchronous.
- The no_submit_receipts write path should be verifiable from the DB: the `insert_idempotent` contract requires `side_effect_status="NO_SUBMIT"` AND `proof_accepted=True` (no_submit_receipts.py:63-65). Since the last write was 06-12T12:12Z, any event that reaches this path after that point either raises in `insert_idempotent` (schema mismatch, duplicate key) or — more likely — none are reaching it because the pre-submit gates eliminate them first.

---

**4. PROVENANCE VERDICTS**

`src/events/reactor.py` — CURRENT_REUSABLE. Last audited header: 2026-06-12. The law regime is the 2026-06-12 operator law ("no caps", "retry count is not a market fact", ALWAYS-DECIDABLE, RISK_GUARD transient requeue, claim-storm fix, LiveLaneDarkInvariantError). All structural invariants match current comments. Gate logic is consistent with the typed rejection-reason registry.

`src/engine/event_reactor_adapter.py` — CURRENT_REUSABLE for the gate/receipt structure but NOT READ IN FULL (file exceeds 256KB; only header through ~line 440 was readable). Authority basis is 2026-06-12 external deep-review (FINDING-A/B/C additions). The receipt dataclass is up-to-date with all known field additions (submit_lane, lfsr, edge_shrunk, etc.). Cannot certify the adapter's internal probability/FDR/Kelly computation as current from the portion read.

`src/events/decision_engine.py` — CURRENT_REUSABLE. Simple thin wrapper over `bind_event_to_candidate_family`; no inline logic beyond exception-routing. Last audited implicitly by the candidate_binding contract it delegates to.

`src/events/candidate_evaluation.py` — CURRENT_REUSABLE for all fields as-of 2026-06-11 (settlement_coverage_status twin-authority #7, execution_mode_intent FIX C, maker fields). However FINDING 10 above is a semantic contradiction in the `admitted` property: `live_win_rate_admissible` is computed but not included in `admitted`. This may be intentional (the adapter enforces it separately) or may be an omission. Authority basis not stated in file header.

`src/events/candidate_binding.py` — CURRENT_REUSABLE. The FDR-family-completeness bin topology validation (2026-06-11) is present. No header; law regime appears to be 2026-06-11 based on the incident comment. The EDLI_REDECISION_PENDING event type check does NOT appear here (it's routed identically to FSR in the reactor), but `bind_event_to_candidate_family` at line 97 raises `CandidateBindingError` for any non-FSR/non-DAY0 event type — including EDLI_REDECISION_PENDING. This is dead code risk: if the reactor routes EDLI_REDECISION_PENDING through the binding call, it will always raise. Whether the adapter skips binding for re-decision events cannot be confirmed from these files alone. STALE_REWRITE risk on that specific event type.

`src/contracts/no_trade_reason.py` — STALE_REWRITE. Header: Created 2026-05-20, last reused/audited 2026-05-21. Law regime: PHASE_2_ULTRAPLAN.md + Phase 3/4. This predates the June 2026 overhaul by ~3 weeks. The enum has no entries matching current live rejection bases (RISK_GUARD_BLOCKED, EXECUTABLE_SNAPSHOT_BLOCKED, MONEY_PATH_HORIZON_EXPIRED, etc.) — it appears to be the legacy evaluator's `NoTradeReason` taxonomy, not the current `RejectionReason` registry. It may still be used by `src/engine/evaluator.py` (the old path). The current reactive reject path uses `src/contracts/rejection_reasons.py` (created 2026-06-10). If `no_trade_reason.py` is still wired at any live callsite it could be emitting out-of-vocabulary rejection reasons.

`src/contracts/rejection_reasons.py` — CURRENT_REUSABLE. Created 2026-06-10, authority basis consolidated_systemic_overhaul K2.1. Registry is complete for all known live rejection bases and categories are operator-ratified.

`src/contracts/alpha_decision.py` — CURRENT_REUSABLE as a typed contract (D1 mitigation). Not on the submission hot-path; no submission impact. No header but the D1 reference aligns with the architecture spec.

`src/execution/executor.py` — PARTIALLY_REVIEWED (first 120 lines only). The executor pattern (cutover_guard, heartbeat_supervisor, ws_gap_guard, risk_allocator asserts before any venue contact) is correct. Cannot certify the full execution path.

`src/strategy/live_inference/live_admission.py` — CURRENT_REUSABLE. The capital-efficiency gate (live_admission.py:87-118) checks `conservative_ev_per_dollar = (q_lcb - price) / price`. This is the honest q_lcb > price-after-cost gate for binary prediction markets where the cost-per-dollar is `price` and the payout-per-dollar is `1/price`. The check fires when `q_lcb <= price` (EV per dollar <= 0). This IS the correct formulation: for a binary bet at price p, expected profit per dollar = (q_lcb / p) - 1 = (q_lcb - p) / p. So B2 (capital_efficiency_lcb_ev) is an honest gate, not over-tight. It will correctly reject candidates where the conservative posterior is at or below the ask price, which is the correct definition of "no edge."

---

**SUMMARY TABLE OF SUBMISSION-BLOCKING FINDINGS**

Finding 2: RISK_GUARD_BLOCKED (2113 events since 06-12). Blocks all submissions. Sustained RiskGuard halt; no substrate refresh mechanism. SUBMISSION-BLOCKING.

Finding 3: EXECUTABLE_SNAPSHOT_BLOCKED (671 events since 06-12). Blocks submission per affected family. SUBMISSION-BLOCKING.

Finding 4: `baseline_direction must carry a bin id as side:bin` raw exception dead-letters events (30 since 06-12). SUBMISSION-BLOCKING for affected events; antibody violation.

Finding 1: `edli_no_submit_receipts` last wrote 06-12T12:12Z — confirmed write silence for ~40 hours. The dominant cause is Finding 2 (events never advance past riskguard to the submit callable).

The no-order root cause is Finding 2: the RiskGuard gate is in a sustained halted state, classifying every event as a TRANSIENT requeue, burning all cycle budget on requeue writes, and blocking every family from advancing to the submit callable. Findings 3 and 4 are compounding blockers for the subset of events that would survive riskguard.

---

## angle:Capital / portfolio / collateral / risk-allocator / account readiness.

**Agent ID:** `agent-a1838de998f6bc152`

### Final Assistant Message

I have enough data. I can now write the complete audit report. Here it is:

---

**CAPITAL / PORTFOLIO / COLLATERAL / RISK-ALLOCATOR / ACCOUNT READINESS — AUDIT REPORT**

---

**1. MECHANISM — WHAT THIS ANGLE ACTUALLY DOES**

The capital/readiness layer is a five-stage gate stack that must all pass before a venue command can be emitted. The stages run in this order at each reactor cycle:

**Stage A — CutoverGuard** (`src/control/cutover_guard.py`): a fail-closed JSON-backed state machine. `gate_for_intent(ENTRY)` returns `allow_submit=True` only when the on-disk state is exactly `LIVE_ENABLED`. Any other state — including `NORMAL`, which is the file-missing default — blocks ENTRY. The file exists at `state/cutover_guard.json` and shows `LIVE_ENABLED`, so this gate is currently open. Transitions require an HMAC-signed operator token and, for `LIVE_ENABLED`, a G1 readiness JSON artifact.

**Stage B — RiskGuard level** (`src/riskguard/riskguard.py`, `src/riskguard/risk_level.py`): a separate background process (60-second tick) writes a `risk_state` row to `state/risk_state.db`. `get_current_level()` reads the latest row; fails closed to `RED` if the row is absent, more than 5 minutes stale, or the DB is inaccessible. The entry gate is `GREEN`-only — `DATA_DEGRADED` (level 1) blocks entries the same as `YELLOW`. The level is the max of six components: brier, settlement_quality, execution_quality, strategy_signal, daily_loss, weekly_loss.

**Stage C — risk_allocator / PortfolioGovernor** (`src/risk_allocator/governor.py`): refreshed once per cycle in `main.py:5357`. `refresh_global_allocator()` reads `position_lots` for exposure capacity, counts `venue_commands` rows in `SUBMIT_UNKNOWN_SIDE_EFFECT / UNKNOWN / REVIEW_REQUIRED` states (unknown side effects), counts unresolved `exchange_reconcile_findings`, reads heartbeat summary and ws_gap summary, then builds a `GovernorState` and calls `configure_global_allocator()`. `assert_global_allocation_allows()` at executor time checks `kill_switch_reason()` first, then per-market/event/window caps.

**Stage D — HeartbeatSupervisor** (`src/control/heartbeat_supervisor.py`): reads `state/venue-heartbeat-keeper.json` (max age 8 seconds). If the file is missing, unreadable, or stale, status is `LOST`. GTC/GTD orders require `HEALTHY`; FOK/FAK bypass. A `LOST` heartbeat arms the allocator kill switch (`governor.py:238`) and also returns `NO_TRADE` from `maker_or_taker()`.

**Stage E — WS gap guard** (`src/control/ws_gap_guard.py`): in-memory singleton. `blocks_market()` returns True if `m5_reconcile_required=True` OR subscription is not `AUTHED/SUBSCRIBED` OR last message is stale (>30s). On boot, the module initializes with `m5_reconcile_required=True` and `gap_reason="not_configured"` (line 95-102). `record_message()` clears this only when `gap_reason in {"not_configured", None}` and subscription is `SUBSCRIBED` (line 160-165). Once a real mid-run reconnect sets `gap_reason` to something else, `m5_reconcile_required` permanently latches True until `clear_after_m5_reconcile()` or `clear_after_no_local_side_effects()` is called.

**Stage F — CollateralLedger** (`src/state/collateral_ledger.py`): `buy_preflight()` checks (1) authority_tier != DEGRADED, (2) snapshot freshness <= 180s (30s cadence + 150s jitter), (3) `available_pusd_micro >= required`, (4) `available_pusd_allowance_micro >= required`. Both the ERC20 balance AND the separate ERC20 allowance (approval to CTF Exchange) must cover the reservation-adjusted amount. On $1,162 capital, a $5 canary order needs 5,000,000 micro-pUSD in both balance and allowance; the allowance check is the one most likely to be under-provisioned without an explicit `approve()` call.

---

**2. DEFECTS**

**DEFECT-1 — reconcile_finding_limit=0 with 1 open finding = kill switch armed RIGHT NOW**
BLOCKS SUBMISSION — CURRENTLY ACTIVE BLOCKER.

`CapPolicy.reconcile_finding_limit` defaults to 0 (`governor.py:52`). `kill_switch_reason()` fires when `reconcile_finding_count > 0` (`governor.py:244`). The live `zeus_trades.db` has exactly 1 unresolved `exchange_reconcile_findings` row (kind=`position_drift`, subject=market id `25998072565711...`, context=`ws_gap`, recorded_at=`2026-06-12T22:58:12`). This was recorded during a ws_gap event over 36 hours ago and never resolved. Every call to `assert_global_allocation_allows()` or `assert_global_submit_allows()` raises `AllocationDenied("reconcile_finding_threshold")` until this row gets a `resolved_at` timestamp. Reproduction: `count_open_reconcile_findings(conn)` returns 1; `kill_switch_reason()` returns `"reconcile_finding_threshold"`.

**DEFECT-2 — cutover_guard.json has LEGACY transition schema**
DEFECT_NO_CURRENT_IMPACT (gate open), but provenance failure.

`state/cutover_guard.json` stores transitions with fields `at/by/from/to/reason` but `transition()` in `cutover_guard.py:300-310` writes `from_state/to_state/recorded_at/operator_id/operator_token_fingerprint/reason/context`. The file was written by a pre-V1 code path (no `operator_token_fingerprint`, no `context`, `operator_id` is literal string "operator"). `_load_payload()` reads `state` correctly regardless of transition format, so the gate behavior is unaffected. But `read_transition_events()` returns the legacy schema, which would confuse any consumer expecting the V1 format.

**DEFECT-3 — tombstone check without tombstone write (heartbeat)**
BLOCKS SUBMISSION if the file exists.

`HeartbeatSupervisor.gate_for_order_type()` at line 551 checks `_failclosed_tombstone_exists()` (line 586-592), which looks for `auto_pause_failclosed.tombstone` on disk. `_write_failclosed_tombstone()` at line 556-557 is a confirmed no-op (`pass`). If this file existed from a prior daemon run, ALL GTC/GTD orders would be permanently blocked across restarts with no code path to create the tombstone or remove it via normal flow. Reproduction: `touch state/auto_pause_failclosed.tombstone`; next GTC/GTD order raises `HeartbeatNotHealthy`. The file's non-existence currently prevents this from firing, but the asymmetry (check without write) is a semantic contradiction.

**DEFECT-4 — ws_gap_guard module-init default permanently blocks boot cycle if reconnect fires before first message**
BLOCKS SUBMISSION on reconnect-before-first-message boot paths.

`_status` initializes with `m5_reconcile_required=True` and `gap_reason="not_configured"` (line 95-102). `record_message()` only clears `m5_reconcile_required` when `gap_reason in {"not_configured", None}` (line 160-165). If any code path calls `record_gap()` (which sets `gap_reason` to something other than `"not_configured"`) before the first `record_message()` arrives, `m5_reconcile_required` permanently latches True. In steady state this is handled, but boot race conditions where a timeout fires before auth completes can trigger this path.

**DEFECT-5 — micro_position_hold: positions with cost_basis < $1.00 never exit**
KILLS +EV CANDIDATE (exit side) — currently relevant to any sub-$1 position.

`evaluate_exit()` at `portfolio.py:1101-1108` returns `ExitDecision(False)` unconditionally if `effective_cost_basis_usd < 1.0`. This is labeled "micro_position_hold." A position entered at $0.50 cost basis that has moved favorably will never be sold. On a $1,162 bankroll with minimum $5 canary orders, this threshold is proportionally enormous (positions near 1/5 of canary minimum). If any position was entered near the minimum with partial fills, it may be permanently frozen.

**DEFECT-6 — DATA_DEGRADED blocks new entries despite not being a loss boundary breach**
BLOCKS +EV CANDIDATES whenever strategy tracker has any exception.

`risk_level.py` defines `DATA_DEGRADED=1` between `GREEN=0` and `YELLOW=2`. The entry gate is GREEN-only. `strategy_signal_level` in `riskguard.py:1566` sets `YELLOW` (not DATA_DEGRADED) on tracker exceptions, but `settlement_quality_level` returning DATA_DEGRADED or `get_current_level()` returning DATA_DEGRADED from a degraded-path row also blocks entries completely. The level-ordering is correct for safety but means any data hiccup (source health, tracker load error, dependency DB locked) blocks +EV entries identically to a real loss boundary breach. No graduated behavior between data issues and financial risk.

**DEFECT-7 — collateral_ledger allowance vs balance asymmetry silently blocks on stale allowance**
BLOCKS SUBMISSION if ERC20 allowance is not refreshed.

`buy_preflight()` checks BOTH `available_pusd_micro` AND `available_pusd_allowance_micro` (`collateral_ledger.py:325-331`). The allowance is an on-chain ERC20 `approve()` value separate from balance. If the operator set a fixed allowance at launch and positions were filled, the remaining allowance shrinks independently of the balance. On $1,162 with some fills already done, the allowance could be below the next order's required amount even though balance is sufficient. The snapshot cadence is 30s with 150s jitter budget (max 180s total staleness). A stale snapshot with outdated allowance will block all buys for up to 3 minutes silently.

**DEFECT-8 — risk_allocator reconcile_finding_limit=0 with no config file override possible without restart**
STRUCTURAL OVER-CONSTRAINT.

`load_cap_policy()` reads `config/risk_caps.yaml` at refresh time (`governor.py:460`), so a config file can change the limit without restart. However, there is no operator UI to resolve `exchange_reconcile_findings` without direct SQL writes. The finding recorded on 2026-06-12 for `position_drift` during a `ws_gap` is a structural artifact of the ws_gap recovery path — not evidence of a real unreconciled exchange discrepancy — but the gate treats it as a hard block. The finding predates the current no-order period (finding at 22:58 UTC on 06-12, no-orders symptom started ~06-12 or 06-13).

**DEFECT-9 — load_portfolio() always returns bankroll=0.0, risk_state staleness produces DATA_DEGRADED**
DEFECT_NO_CURRENT_IMPACT if bankroll_provider warms correctly.

`load_portfolio()` at `portfolio.py:2388` sets `bankroll = 0.0` unconditionally. The comment says bankroll is supplied by `bankroll_provider.current()` in cycle_runner/riskguard. If `bankroll_provider.cached()` returns None at allocator refresh time (line `main.py:5330-5342`), the allocator returns `configured=False` and fail-closes the cycle with no submission. This silently blocks entries every cycle where the wallet RPC is slow — not logged as a distinct counter, only as an ERROR log line.

---

**3. ABSENT ELEMENTS vs. SOUND SYSTEM**

**Missing: operator tooling to resolve exchange_reconcile_findings**. A sound system has a CLI command or admin endpoint to mark a finding resolved with a reason. Currently the only path is a direct SQL `UPDATE exchange_reconcile_findings SET resolved_at=datetime('now'), resolution='...', resolved_by='operator' WHERE finding_id='...'`. No such script exists in `scripts/`. This means every ws_gap event that generates a `position_drift` finding permanently arms the kill switch until manual SQL intervention.

**Missing: graduated entry threshold below DATA_DEGRADED**. A sound system distinguishes between "data source degraded" (continue with reduced confidence) and "financial loss boundary crossed" (halt entries). Currently both map to the same entry block. Having a `DATA_DEGRADED_ALLOW_ENTRY` mode with tighter Kelly sizing would preserve +EV opportunity during data hiccups.

**Missing: ws_gap latch audit in observability**. There is no dashboard metric showing when `m5_reconcile_required` was last set and by what path. A silent ws_gap latch from days ago would be invisible without querying the in-process state, which requires a live RPC or log grep.

**Missing: collateral allowance re-approval automation**. After fills consume ERC20 allowance, no daemon re-approves. A sound system either uses `approve(max_uint256)` once and never re-approves, or has an automatic top-up trigger. If the initial approval was a finite amount, fills erode it silently.

**Missing: reconcile_finding auto-expiry or classification**. A sound system ages or classifies findings: `position_drift` from a ws_gap during recovery is categorically different from `unknown_side_effect`. The current schema has no `severity` or `classification` column, so the kill switch treats all findings equally.

---

**4. PROVENANCE VERDICTS**

`src/control/cutover_guard.py` — Created 2026-04-27, last audited 2026-04-27. Law regime: Z1 slice, CLOB V2 cutover plan. STALE_REWRITE on the transition schema (V1 token format including `operator_token_fingerprint` written by `transition()` but the on-disk file was written by a pre-V1 path). Gate logic for `LIVE_ENABLED` is CURRENT_REUSABLE. The tombstone-write no-op (removed 2026-05-04 per comment) leaves a dead check in `_failclosed_tombstone_exists()` that should be deleted.

`src/riskguard/riskguard.py` — Last major change 2026-06-08 (realized-loss circuit breaker, dependency_db_locked fix). CURRENT_REUSABLE. Trailing-loss now uses settled PnL only, `force_exit_review` on RED, degraded-path tick is structurally correct. Six-component level system is the current law.

`src/riskguard/policy.py` — Created ~2026-04-27 based on A2 slice. CURRENT_REUSABLE. Precedence (hard_safety > manual_override > risk_action) correct. Missing-table returns empty list cleanly.

`src/riskguard/risk_level.py` — 31 lines, enum-only. CURRENT_REUSABLE. DATA_DEGRADED between GREEN and YELLOW by integer order is correct and intentional.

`src/control/cutover_guard.py` — STALE_REWRITE (as above — transition schema mismatch; tombstone check is dead code).

`src/control/heartbeat_supervisor.py` — Last audit 2026-05-04 (tombstone retirement noted in comments). CURRENT_REUSABLE for gate logic, but `_write_failclosed_tombstone()` is DEAD_DELETE (the method is a no-op `pass`) and `_failclosed_tombstone_exists()` should be deleted alongside it to remove the asymmetric check.

`src/state/collateral_ledger.py` — 809 lines. Last substantive change covers the 180s freshness window and dual balance+allowance check. CURRENT_REUSABLE. The 150s jitter budget is very wide relative to the 30s cadence (5× slack) and may cause stale allowance issues.

`src/state/portfolio.py` — Active development through 2026-06-12 (exit-retry, chain-truth void, POISON_ROW containment, EDLI entry proof). CURRENT_REUSABLE but the `micro_position_hold` at `effective_cost_basis_usd < 1.0` (line 1101-1108) is an undocumented threshold that silently suppresses exits. The $1.00 threshold was presumably a "dust position" heuristic but is never explained and has no config override.

`src/risk_allocator/governor.py` — Created 2026-04-27, last audited 2026-04-27. CURRENT_REUSABLE for the allocation logic. The `reconcile_finding_limit=0` and `unknown_side_effect_limit=0` defaults in `CapPolicy` are intentionally strict but the absence of an operator resolution pathway for these findings makes them effectively permanent kill switches in practice.

---

**SUMMARY OF ACTIVE BLOCKERS (ordered by probability of causing the no-order symptom)**

1. **DEFECT-1** (reconcile_finding_threshold): 1 unresolved `exchange_reconcile_findings` row from 2026-06-12, kind=`position_drift`, context=`ws_gap`. `CapPolicy.reconcile_finding_limit=0`. Kill switch armed on every cycle. This is the most likely single cause of the ~1.5-day no-order symptom. Fix: `UPDATE exchange_reconcile_findings SET resolved_at=datetime('now','utc'), resolution='ws_gap_position_drift_reviewed_no_discrepancy', resolved_by='operator' WHERE resolved_at IS NULL;` against `zeus_trades.db`, then restart or wait for next allocator refresh.

2. **DEFECT-7** (collateral allowance): if ERC20 allowance is below the next canary order size after prior fills, all buy preflights fail silently. Secondary blocker if DEFECT-1 is fixed.

3. **DEFECT-3** (tombstone file): conditional secondary blocker if `state/auto_pause_failclosed.tombstone` exists. Verify with `ls state/auto_pause_failclosed.tombstone`.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a1e92d384a47bc43c`

### Final Assistant Message

Now I have a complete picture. Let me do one final check on the b1825c4a07 commit to confirm what it actually removed regarding candidate materialization vs just the toggle.

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-a1f62f0cecafef0e5`

### Final Assistant Message

Now I have the complete picture. Let me synthesize the kill analysis:

**Critical architecture fact:** `ws_gap_guard.py` is a pure in-memory singleton. Its `allow_submit` state is NOT driven by `exchange_reconcile_findings.resolved_at` — it's driven by: (1) the live WS connection state from `polymarket_user_channel.py`, and (2) the result of `run_ws_gap_reconcile_and_clear()` which calls `list_unresolved_findings(conn)` and only calls `clear_after_m5_reconcile()` when `unresolved_findings_count=0`.

**The candidate's mechanism IS correct:** The unresolved row 5bbc2be2 (`kind=position_drift`, `resolved_at=NULL`) causes `list_unresolved_findings()` to return 1 row, causing `run_ws_gap_reconcile_and_clear()` to return `status=blocked` without calling `clear_after_m5_reconcile()`, keeping `m5_reconcile_required=True` in memory, keeping `allow_submit=False`.

**Kill attempts:**

(a) "Naive about a real constraint?" — No. The reconcile-then-clear pipeline is correct architecture: Zeus should NOT submit if there are unresolved position discrepancies. The question is whether THIS specific finding is a genuine discrepancy or phantom.

(b) "Does existing code already handle it?" — Partially. The settled-class absorber (task #31 code at lines 2432-2473) is the intended handler. BUT it fails for this token because: the token is absent from `market_events` in zeus-forecasts.db (0 rows match), absent from `executable_market_snapshots` (no condition_id bridge available), and absent from `token_suppression`. The absorber is UNREACHABLE for this token because the calendar-terminal lookup returns empty, so the condition at line 2446 (`settled_terminal is not None`) is False. The operator-ack path also has no prior resolved row (0 results). The finding perpetually re-blocks the latch every sweep.

(c) "Survivorship bias?" — Not applicable. The single unresolved row is directly confirmed in the DB. The evidence_json shows `exchange_size=0`, `confirmed_journal_size=17.05`, `settlement_evidence_class=unconfirmed_redeem_settlement_commands` — this is exactly the "swept/redeemed winner" pattern task #31 was supposed to handle.

(d) "Would fixing it move a real fill?" — Yes. This is the ONLY unresolved finding (confirmed: 1 row total with `resolved_at IS NULL`). Resolving or absorbing it would allow `list_unresolved_findings()` to return 0 rows, allowing `clear_after_m5_reconcile()` to fire, setting `allow_submit=True` in memory, unblocking the entire submission path for all markets.

The defect is real: task #31's settled-class absorber has a DATA GAP — the token lacks a `market_events` row and isn't in EMS, so the absorber's calendar-terminal gate never fires. The finding is NOT phantom in the sense of being wrongly categorized; it's genuinely blocking and the absorber genuinely fails to clear it.

---

## refuter:candidate=DEFECT (angle R5, tag MISSING_CAPABILITY, impact 90):

**Agent ID:** `agent-a207431cc98a79996`

### Final Assistant Message

I have sufficient evidence. Let me synthesize.

**Summary of findings:**

1. **Repro confirms:** Zero hits for `settlement_attribution|retired_comparison_tool|promotion_readiness` in `src/events/candidate_evaluation.py` and `src/events/reactor.py`. The defect's factual code claim is correct.

2. **The cited line 1020 "Does NOT gate or block submissions" comment does not exist** in the current file — the file is 1038 lines and line 1020 is in a CLI argparse block. The claim is stale or fabricated, but it doesn't matter because the absence of any wiring IS the defect.

3. **The loop is genuinely open:** `skill_win_rate` is computed in `settlement_skill_attribution.py` and logged, but is only read back in `main.py` for a log line. `retired_comparison_tool` writes `state/retired_comparison_tool.json` and a markdown file — no consumer in `src/events/`. `promotion_readiness` is only consumed in `src/backtest/shadow_replay_harness.py`. None feed into `q_lcb` construction.

4. **The `capital_efficiency_lcb_ev` gate is structurally correct** (`live_admission.py:113-118`: `ev_per_dollar = (q_lcb - price) / price <= 0` → reject). It is NOT over-tight. It rejects because `q_lcb <= price`, full stop. Closing the skill/shadow loop would not change this gate's logic — it would need to move `q_lcb` itself above `price`.

5. **Would closing the loop move real fills?** Only if the skill evidence or shadow comparator produces a credibly higher `q_lcb`. But the shadow comparator returns `INSUFFICIENT_N` (fewer than min_n settled paired cells), which is an honest absence of evidence, not suppressed evidence. With 23 attributions and 9 SKILL_WIN, there is not enough settled data to statistically support a `q_lcb` lift for any specific market. The `settlement_backward_coverage.py` path (`apply_settlement_coverage`) is the proper mechanism for calibration-source-gated q_lcb adjustment — and it IS wired into the live path via `qlcb_provenance.py`. The skill/shadow grading system assesses system-level health retrospectively; it is not the right mechanism to lift per-candidate `q_lcb` in real-time, and the defect description conflates them.

6. **The ideal IS naive about a real constraint:** Per-candidate real-time `q_lcb` adjustment from aggregate skill win-rate would require: (a) statistically significant N per city/bin/horizon, (b) a calibrated mapping from skill category to q_lcb delta, (c) a sound Bayesian update that doesn't violate isotonicity. The `settlement_backward_coverage` system already provides this via `SETTLEMENT_ISOTONIC` calibration source — which IS settlement-backed. The "missing capability" is actually already implemented in the calibration path; skill attribution is a diagnostic overlay, not the mechanism to close the calibration loop.

**Kill verdict:** The defect is structurally true (the loop is open as described) but the ideal is naive about how q_lcb is properly updated — the calibration loop is closed via `settlement_backward_coverage` / `SETTLEMENT_ISOTONIC`, not via skill win-rate injection. With only 23 attributions and INSUFFICIENT_N from shadow comparator, closing this specific loop would not move any real order because the data doesn't exist to support a statistically licensed q_lcb lift. This is OBSERVABILITY_GAP / DEFECT_NO_CURRENT_IMPACT territory, not ROOT_CAUSE.

---

## reconcile:Observability / no-trade provenance / debuggability

**Agent ID:** `agent-a21b78cdc5c546c07`

### Final Assistant Message

Now I have a complete picture. Let me compile the final structured output.

Key confirmed findings:

1. **RISK_GUARD_BLOCKED events produce zero persisted records while transient** — `reactor.py:1455` puts event in `_transient_requeue_reasons` dict (in-memory only) and returns `_EXECUTABLE_SNAPSHOT_RETRY`. `_write_regret` is never called for the requeue path. A record only appears in `no_trade_regret_events` if/when a horizon terminal fires (`reactor.py:1211`) — meaning an unknown duration of persistent RISK_GUARD blocking is invisible to any persisted query. WITNESS confirms 0 `edli_no_submit_receipts` rows after 2026-06-12T12:12.

2. **`no_trade_events` silently swallowed since 2026-05-28** — schema version assertion at `no_trade_events.py:241` raises `NoTradeEventsSchemaCompatibilityError`, swallowed by `except Exception` at `cycle_runtime.py:5485`. No candidate pre-reactor provenance for ~17 days. WITNESS corroborates (0 rows after 2026-05-28).

3. **`live_health._business_plane_surface` reads status JSON, not DB** — `live_health.py:96-210` reads `cycle.get("no_trades")` / `rejection_reason_counts` from a JSON file written by cycle_runtime. If cycle_runtime never emits the right counter keys for RISK_GUARD-blocked events (they requeue, not reject), the health check reports `ok=True` while blocking is ongoing.

4. **`no_trade_regret_events` keyed by `event_id`, not `(market_slug, target_date, bin_label, eval_time)`** — confirmed by schema: `UNIQUE(event_id, rejection_stage, rejection_reason)`. Cross-referencing a market requires a JOIN through `opportunity_events`. Ideal requires single-lookup by (market, time).

5. **No gate-threshold stored in any provenance record** — `no_trade_events` stores `reason`/`reason_detail` (NoTradeReason enum category, not a specific gate threshold value). `no_trade_regret_events` stores `q_live`, `q_lcb_5pct`, `c_fee_adjusted` (input values) but no explicit "threshold that fired" field — calibration drift cannot be replayed from the record alone.

6. **`decision_log` empty; `no_submit_projection_rows` has no callers** — confirmed by file:line.

---

## refuter:candidate=DEFECT (angle R13, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-a244292d6512a7618`

### Final Assistant Message

I now have enough to make the call. Let me summarize the key findings:

1. **Code confirmed (lines 319-320, 1800-1824)**: `list_unresolved_findings(conn)` at line 320 is called with NO arguments (no kind, no context, no age filter). The gate at line 331 blocks on ANY unresolved finding.

2. **Repro validity check**: The repro inserts `exchange_ghost_order` with `context='periodic'` from a prior week. The sweep at lines 698-702 only auto-resolves ghost orders that are: (a) foreign wallet orders, (b) operator-acknowledged, or (c) disappeared (no longer seen in fresh venue snapshot). A genuinely stale ghost from a prior period that is NOT in the current fresh venue snapshot would be caught by `_resolve_disappeared_ghost_order_findings` — which requires a fresh adapter read. If the repro's token truly doesn't appear in the fresh open-orders response, the sweep WOULD resolve it. However, if it's inserted with a subject_id not corresponding to any real order, the disappeared-ghost resolver requires the fresh open-order list to exclude it, which happens during `run_reconcile_sweep` (line 319 runs before line 320's check).

3. **Critical mitigating factor**: `run_reconcile_sweep` (called at line 319) runs `_resolve_disappeared_ghost_order_findings` at line 700-702 internally. So stale ghost-order findings that no longer appear in the fresh venue snapshot WOULD be auto-resolved before `list_unresolved_findings` at line 320 is evaluated. The repro's "INSERT a ghost from 2026-06-01 and observe latch refuses to clear" would likely be RESOLVED by the disappeared-ghost resolver when the fresh M5 snapshot doesn't contain that order_id — mitigating the repro's claimed behavior.

4. **position_drift from operator manual trade**: This is the genuinely dangerous case. A `position_drift` from an operator manually-traded non-weather market (INV: shared wallet) that has NOT been operator-acknowledged would survive all three auto-resolvers and block the latch. The `_operator_acknowledged_drift_resolution` check at line 1084 requires an explicit operator-acknowledgment row. This is a real gap.

5. **Current DB state**: 0 unresolved findings. The blocking finding 5bbc2be2 is already resolved.

The repro as written (exchange_ghost_order) is partially mitigated by the disappeared-ghost auto-resolver. But the underlying structural defect — no context/age filter in the gate — is REAL for position_drift findings from operator manual trades (shared wallet scenario per operator law in memory). However, this is EDGE_DEFECT territory: the ghost case is largely handled, but operator-trade position_drift IS a real gap.

---

## angle:Point-in-time data lineage / look-ahead leakage governance.

**Agent ID:** `agent-a25fe6c2b93410811`

### Final Assistant Message

I'll investigate the sigma_scale_fit.json calibration fit window for look-ahead leakage by checking what data it was fit on versus what decisions it influences.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 75):

**Agent ID:** `agent-a26376f173d47e625`

### Final Assistant Message

The empirical claim is **false**. The most recent 40 rows show exactly 20 YES and 20 NO rows — a perfect 50/50 split, proving the materializer does store NO-labeled rows. The 10-row sample cited as evidence was just the oldest rows (which happened to all be YES from an earlier code state), not representative of current data.

Now I have a complete kill. Let me articulate the full refutation:

**Kill (c) — empirical claim is survivorship-biased / under-powered:**
The candidate's witness ("all 10 sampled live EMS rows have outcome_label=YES") is directly refuted. The 40 most recent rows are 50% YES / 50% NO. The 10-row sample was from `LIMIT 20` without `ORDER BY`, returning oldest rows — these predate the `refresh_executable_market_substrate_snapshots` code that explicitly iterates `for direction in ("buy_yes", "buy_no")` at line 4015, captures a separate snapshot per direction, and sets `outcome_label="NO"` for buy_no rows (market_scanner.py:2749).

**Kill (b) — code already handles it:**
`_snapshot_rows_by_condition_and_direction` at lines 12849-12869 is structured to accept NO-labeled rows: for a row with `outcome_label="NO"` and `selected_token=no_token`, the NO iteration passes both filters (12865: selected_token matches no_token; 12867: "NO" == "NO") and the YES iteration is correctly skipped. The filter is a GUARD not a blocker — it correctly emits the NO row as `(condition_id, "buy_no")`.

The `asset_id` fix mention at line 13541 is irrelevant to the claim — that's a different code path.

---

## angle:Minimal kernel / rebuild boundary / keep-list.

**Agent ID:** `agent-a27a9372c376b60ea`

### Final Assistant Message

I have everything I need. Here is the complete report.

---

## Angle: Minimal kernel / rebuild boundary / keep-list

### (1) What this angle actually does

**cycle_runner.py** is the per-cycle orchestrator. Each `run_cycle()` call runs sequentially: DB connection, portfolio load, riskguard tick, chain-sync, orphan cleanup, command recovery, pending-fill promotion, bankroll/exposure gate, monitoring/exit phase, then a discovery gate that either calls `_execute_discovery_phase()` or records `entries_blocked_reason`. At the end, one `commit_then_export` call persists the artifact, portfolio, tracker, and status JSON. It never touches order submission directly — it delegates to `cycle_runtime.py` for heavy work and to the EDLI adapter for the order path.

**event_reactor_adapter.py** (14,849 lines as of the kcut doc; audited header says last touched 2026-06-12) is the EDLI decision kernel. It receives `OpportunityEvent`s, runs the full candidate proof pipeline, and calls `executor_submit()` when all gates clear. It is the only place where a buy order is issued on the EDLI path. The file is a sequential gauntlet of ~174 early-return seams described structurally in `kcut_decision_path_collapse_2026-06-13.md`.

**dispatch.py** gates per-market phase dispatch (flag `ZEUS_MARKET_PHASE_DISPATCH`, default ON post-A6). It is a thin routing helper; it does not block orders on its own. Last audited 2026-05-04.

**ddd_wiring.py** is the live DDD (data-density-discount) helper for the evaluator. Fail-closed on unconfigured cities. Last audited 2026-05-15.

---

### (2) Every defect found — file:line, reproduction recipe, submission impact

**DEFECT-1 — FDR gate (D8) is the unconditional live authority after its replacement was removed (submission-blocking)**

`event_reactor_adapter.py` lines 2829–2902: the comment at line 2800 says "The EB-shrinkage decision-replacement flag was REMOVED 2026-06-13 in the q-shadow gate-mass collapse; `authority_on=False` pins the shadow stamp to `selection_authority=BH_FDR`." That is accurate about `_compute_selection_shrinkage`. But the **actual gate** at line 2869 reads `_gate_passed = fdr.passed` and at line 2871 `if not _gate_passed: return ... reason="FDR_REJECTED"`. The comment says the EB-shrinkage flag was removed; what was removed was only the flag that would have switched the gate over to EB. The BH/FDR gate itself still runs unconditionally and its `passed` verdict is still the hard submission gate.

The kcut doc (§1D D8) and task #60's test (`tests/engine/test_selection_shrinkage_wiring.py`, noted in test_topology.yaml) both treat BH/FDR as condemned on {0,1} p-values and target it for deletion. Task #60 is listed as `completed` in the task list. However the code at line 2869 still shows `_gate_passed = fdr.passed` as the live gate — `authority_on=False` only controls the shadow-only EB stamp, not the FDR gate. The FDR gate is neither shadow-only nor deleted; it is still the hard submission wall.

Reproduction: submit any event with a family whose `fdr.passed=False`. The receipt carries `reason="FDR_REJECTED"`. This blocks a real +EV candidate whenever the BH test rejects on the {0,1} p-value universe.

**Impact: blocks submission on any family the BH/FDR test rejects.**

**DEFECT-2 — capital_efficiency and buy_no_conservative_evidence zeroing gates (C4, C7) still set score=0.0 unconditionally (submission-blocking)**

`event_reactor_adapter.py` lines 7535–7561: `capital_efficiency_reason = _capital_efficiency_untradeable_reason(...)` followed by `if capital_efficiency_reason is not None: score = 0.0`. Then at lines 7549–7561, `buy_no_conservative_evidence_reason = live_buy_no_conservative_evidence_rejection_reason(...)` followed by `if buy_no_conservative_evidence_reason is not None: score = 0.0`. Both set `passed_prefilter = False` at line 7599–7605, which also excludes them from the FDR family as a passed hypothesis.

The kcut doc classifies both as DELETE targets (§3, rows C4 and C7) — "subsumed by K3" — and the code comment at lines 7520–7534 explicitly says the buy_no scalar market-disagreement demotion was "REMOVED 2026-06-08 (S4)" and "SUBSUMED by the marginal-utility ranker." Yet C7 (`buy_no_conservative_evidence_reason`) is still called and still zeros the score. The S4 comment claims it was removed; the code shows it was not. This is a code/comment contradiction.

Reproduction: any buy_no candidate whose `q_lcb_calibration_source` is `FORECAST_BOOTSTRAP` and whose q_lcb is not conservative enough by the legacy cutoff will get `buy_no_conservative_evidence_reason != None`, `score = 0.0`, `passed_prefilter = False`, and will never appear as the selected candidate.

**Impact: kills real +EV buy_no candidates before selection. On its own it would block a real +EV buy_no submission.**

**DEFECT-3 — TRADE_SCORE_NON_POSITIVE gate (D7) is a redundant downstream reprise of C4/C7 (submission-blocking)**

`event_reactor_adapter.py` lines 2772–2797: `if trade_score <= 0.0: return ... reason="TRADE_SCORE_NON_POSITIVE"`. This fires after `_selected_candidate_proof` selects the proof. Any candidate whose score was zeroed by C4 or C7 cannot be selected (passed_prefilter=False), so D7 would only be reached if the ΔU ranker somehow returned a proof with score=0. In the current logic path this creates a second wall downstream of the first. As noted in kcut §1D row D7: it is the `p_fill·edge` degenerate proxy for E_log_growth, marked MERGE→K3.

**Impact: not independently blocking today (C4/C7 fire first), but is redundant scar tissue. DEFECT_NO_CURRENT_IMPACT in isolation; contributes to gate-mass opacity.**

**DEFECT-4 — market-anchor cap (C2) is live and one-sided against buy_no (admission-killing, not submission-blocking)**

`event_reactor_adapter.py` lines 7472–7492: when `_replacement_q_market_anchor_enabled()` is true and direction is `buy_no`, `q_lcb` is lowered to `_anchor.q_lcb_no_out`. The cap is one-sided: it can only reduce q_lcb_no, never raise it. It operates before scoring, so a capped q_lcb_no feeds into both score and passed_prefilter. The kcut doc (§3 C2 and §6) is explicit: this is a calibration band-aid that cannot be safely deleted until the C1/C3 calibration fixes land. It is currently live (not flag-OFF). There is no obvious bug here per se — it is intended behavior — but it is structurally over-conservative against buy_no and documented as a future DELETE.

**Impact: reduces buy_no q_lcb on near-center candidates, potentially pushing score to zero via C4/C7 downstream. Kills real near-center buy_no edge. Not a bug independent of calibration decisions, but identified in kcut as the "tell" for the gate-mass disease.**

**DEFECT-5 — double invocation of `refresh_global_allocator` in `run_cycle()` (resource waste, semantic confusion)**

`cycle_runner.py` lines 706–723: `refresh_global_allocator(conn, ...)` is called once as "cycle_start" and stored in `summary["portfolio_governor_cycle_start"]`. Then at lines 919–936, the exact same function is called again with the same arguments and stored in `summary["portfolio_governor"]`. Both results independently gate `_discovery_gates_allow_entries()` — `governor_status` on line 1051 comes from the second call. The first call result is stored in `summary` but not used by the gate. This is structurally redundant: one is consumed, one is observability-only (but not labeled as such). If `refresh_global_allocator` has any side effects (rate limits, DB writes), calling it twice per cycle is a defect.

**Impact: if the governor has side effects this is a correctness defect; if it is pure it is pure waste. DEFECT_NO_CURRENT_IMPACT on submissions, but introduces state confusion.**

**DEFECT-6 — `_execute_discovery_phase` ignores the `edli_event_context` kwarg it receives (dead parameter)**

`cycle_runner.py` line 563: `_execute_discovery_phase(...)` is called with `edli_event_context=edli_event_context`. The stub at lines 549–577 accepts the parameter but does NOT thread it through to `_runtime.execute_discovery_phase(...)` — the kwarg is accepted and discarded silently. The `_runtime.execute_discovery_phase` signature at the call site on line 564–577 does not include `edli_event_context`. This means any event-context information passed into `run_cycle()` is lost before reaching the discovery phase.

Reproduction: call `run_cycle(mode, edli_event_context={"key": "value"})` and trace whether the context appears in the discovery phase — it will not.

**Impact: if `edli_event_context` is load-bearing for routing decisions in the discovery phase, those decisions are made without it. The severity depends on what the discovery phase does with it. At minimum it is a dead parameter path — DEFECT_NO_CURRENT_IMPACT if context is purely observational; could affect routing if the discovery phase branches on it.**

**DEFECT-7 — `posture` default hard-codes "NO_NEW_ENTRIES" on exception but `_discovery_gates_allow_entries` requires "NORMAL"**

`cycle_runner.py` lines 856–867: `_current_posture` is initialized to `"NO_NEW_ENTRIES"` before the `try/except`. On any exception reading the posture file, it stays `"NO_NEW_ENTRIES"`. The gate at line 393 in `_discovery_gates_allow_entries` requires `current_posture == "NORMAL"`. So any transient IO failure reading `runtime_posture.yaml` silently blocks entries without surfacing a specific reason code until the fallback `entries_blocked_reason` string at line 981 catches it as `f"posture={_current_posture}"`. This is by design (fail-closed), but the initialization of `"NO_NEW_ENTRIES"` means the error is masked as a posture block rather than surfaced as a read error, which reduces operator debuggability.

**Impact: blocks entries on posture-file read error. By design fail-closed. DEFECT_NO_CURRENT_IMPACT on orders (correctly blocked); reduces observability.**

---

### (3) What is notably absent vs what a sound system needs

The kcut doc makes the structural gap explicit. The specific absences vs a sound system:

**The K3 admission authority (E_log_growth > 0) is not the gate.** A sound system has one admission criterion: `E[log_growth](q_posterior, executable_price, optimal_stake) > 0`. What exists instead is five overlapping partial approximations: C4 capital_efficiency, C7 buy_no_conservative_evidence, D7 trade_score, D8 FDR, and implicitly C2 market-anchor. None of these IS E_log_growth; all of them interact and any one can block a +EV trade the others would have passed.

**The EB shrinkage replacement (task #60) is not wired as the gate.** `_compute_selection_shrinkage` is called with `authority_on=False` (line 2811), pinning it to shadow-only permanently. The BH/FDR gate continues as the unconditional live authority. Task #60 is marked completed but the code does not reflect that completion on the actual gate line.

**The horse-race Kelly (task #63, K4 in kcut) is not the live sizer.** The per-candidate fractional-Kelly sizing is still live; the horse-race allocator is shadow-only. This means families with multiple bins size each bin in isolation rather than running the joint allocation.

**Stage 0 of the kcut (E_log_growth shadow stamp on every receipt) appears not yet implemented.** The kcut §5 specifies "stamp E_log_growth next to trade_score for every priced candidate" as the pre-condition comparator for all subsequent stages. No such field is visible on `_CandidateProof` or `EventSubmissionReceipt` in the code read. Without the comparator, there is no operator-visible evidence that gate-mass is killing +EV trades on the live path.

---

### (4) Provenance verdict

**`event_reactor_adapter.py`**: header says audited 2026-06-12 (most recent audit). File was under active modification through 2026-06-12 (S7 gate deletions, Wave-1 canary deletion, K=1 Stage 1, Finding A/B/C/D/E fixes). The kcut doc dated 2026-06-13 identifies it as 14,849 lines with 174 seams and designates it for structural collapse. The EB-replacement flag and canary flag have been deleted but the FDR gate (line 2869) and the C4/C7 zeroing gates (lines 7535–7561) remain as hard submission blockers not yet collapsed to K3.

Verdict: **STALE_REWRITE** — the file was written under a regime of incremental gate-patching; the kcut doc (2026-06-13) establishes a new law (K=5 structural collapse) that makes large portions of the gate sequence scar tissue. It is not dead (the live path runs through it) and not quarantined (no correctness failures outside the gate-mass), but requires the Stage 1 K3 promotion to be current under the new law.

**`cycle_runner.py`**: last meaningful logic change appears to be 2026-05-04 (entries_block_registry, freshness gate) with ongoing antibody additions. The double-governor-call (DEFECT-5) and dropped edli_event_context (DEFECT-6) are structural issues not fixed by recent patches.

Verdict: **STALE_REWRITE** on the orchestration body (gate-mass accretion mirrors the reactor; the `_discovery_gates_allow_entries` function is itself 16 conditions); CURRENT_REUSABLE on the commit/export and monitoring phases.

**`dispatch.py`**: Last audited 2026-05-04. The flag is post-A6 ON by default, and the legacy branches are still present. The code is functionally correct for its stated purpose (phase-axis dispatch). Relevant gaps (uma_resolved=False hardcode, F1 fallback as silent live authority) are documented in the file's own KNOWN GAPS section.

Verdict: **CURRENT_REUSABLE** as a routing helper. The legacy branches it intends to excise after a stable week of phase-axis live are scar tissue but are not bugs.

**`ddd_wiring.py`**: Last audited 2026-05-15. Fail-closed on missing cities; timezone-aware window elapsed; clean DB query. No cross-DB connection violations.

Verdict: **CURRENT_REUSABLE**.

---

## angle:Observability / no-trade provenance / debuggability.

**Agent ID:** `agent-a28270cc318a26909`

### Final Assistant Message

Now let me look at the `no_submit_projection.py` JOIN requirement and the `decision_certificates` table to understand why it's essentially empty:

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-a29d27170a86bb423`

### Final Assistant Message

I'll systematically audit the state integrity files and trace the recent churn to the observed blockers.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a2a6602a3792baa59`

### Final Assistant Message

The token is absent from both `market_events` in zeus-forecasts AND `executable_market_snapshots` in zeus-trades. This means:

1. The settled_external_absorber path (line 2445) requires `calendar_terminal.get(token)` to be non-None, which requires the token to be in `market_events` OR bridgeable via `condition_id` from EMS — but EMS has no rows for this token either.

2. All other auto-resolution paths also require matching evidence: `exchange_size=0` matches `available_wallet_size=0` only if `confirmed_journal` is also 0, but the evidence shows `confirmed_journal_size=17.05` (nonzero).

The finding is structurally trapped: the token was purged from EMS (closed market, rolled out of the snapshot window), the market_events table in zeus-forecasts has no row for it, so `_condition_ids_for_tokens` returns empty, `calendar_terminal` stays empty, and the settled_external_absorber never fires. The refresh loop runs but finds no resolution path.

This is the exact regression described in commit #31's task description: "latch freeze on swept winners." The absorber was supposed to fix this but it has a gap — it can't resolve when the token falls out of EMS before reconcile runs.

The code and data are consistent with the defect claim. Fixing this (adding the token to suppression or resolving the finding directly) would unblock `count_open_reconcile_findings` returning 0, which would clear `kill_switch_reason`, which would re-enable submission. B2 (decision gate rejections) is a separate issue — but B1 is exactly this finding.

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-a3021a83958c01321`

### Final Assistant Message

## State Integrity / Config / Flags / Deployment: First-Principles Design

### 1. Objective

The system makes money only when the order it submits reflects the belief computed from the current model run, against the current contract definition, under the current fee schedule, with the position it actually holds. Any silent mismatch between any of these breaks the edge calculation. The objective of this angle is: **every decision boundary that touches money must read exactly one authoritative value, and that value must be immutable for the duration of the decision cycle.**

---

### 2. Ideal Mechanism

**Single-source sealed config.** At the start of each decision cycle (triggered by a new forecast ingestion), the system snapshots every input into a typed, immutable struct sealed with a content hash. That struct — call it the *cycle manifest* — is the only thing downstream components are allowed to read. No component queries a config file, database flag, or environment variable directly during a cycle. They receive the manifest or they do not run.

The manifest includes: bin definitions (low bound, high bound, inclusive/exclusive convention, count K), settlement station ID and timezone with DST rule, fee schedule (maker/taker rebate, minimum tick, minimum size), current position per contract, forecast provider weights, and a monotonic cycle sequence number. The hash is computed before any writes begin.

**Atomic write via compare-and-swap on a single writer lock.** Only one process holds the cycle manifest at a time. Competing processes see the previous manifest until the new one is fully committed. The commit is a single atomic rename of the sealed manifest file plus an append to an immutable audit log. No in-place mutation, ever. Readers memory-map the latest sealed manifest; they never hold a reference across a rename boundary — they re-open at each cycle boundary.

**Typed contracts, not stringly-typed config.** Bin boundaries are a closed algebraic type: `Bin(lo: Decimal, hi: Decimal, lo_inclusive: bool, hi_inclusive: bool)`. Settlement direction is an enum: `YES` or `NO`, never a boolean. Temperature unit is a tagged value, not a bare float. These types are the schema; serialization is derived from the types, not the reverse. A deserialization failure is a hard stop, not a warning.

---

### 3. Invariants a Correct Implementation Must Hold

- **INV-1 (Partition):** The K bin definitions for a given city-date cover the full plausible temperature range with no gap and no overlap. Verified at manifest creation, hard failure otherwise.
- **INV-2 (Timezone uniqueness):** One and only one timezone-with-DST-rule per station, stored as an IANA string, resolved to an absolute UTC offset only at the moment of comparison against a UTC-stamped observation. Never stored as a pre-resolved offset.
- **INV-3 (Direction consistency):** The side being submitted (YES or NO) is derived from the signed edge, not from a flag that could be set independently. Direction cannot be overridden at submit time.
- **INV-4 (Position snapshot freshness):** The position used in sizing is read from the same atomic snapshot as the manifest. A position read from a separate query after the manifest seal is forbidden.
- **INV-5 (SHA drift protection):** The running daemon records the git SHA it was compiled against in its manifest. If a concurrent checkout changes the codebase while the daemon runs, the daemon detects the drift on the next cycle and refuses to emit orders until it is either restarted or the operator explicitly re-seals with the new SHA.

---

### 4. Failure Modes That Silently Destroy Edge

- **DST boundary flip:** A station reading timestamped in local time is compared against a forecast valid time in UTC. If DST transition is unresolved, a reading lands in the wrong bin. The market settles against the official reading; the system was positioned for the wrong outcome. Loss is 1.0 - fill_price, full capital at risk.
- **Stale position snapshot:** Two concurrent fills arrive; the second order is sized against a position that does not include the first fill. The system doubles its exposure beyond intent, then edges are calculated incorrectly on the next cycle because the position used is wrong.
- **Flag-gated direction mismatch:** A deployment flag disabling NO-side submission is set in one config store but not propagated to the running daemon. The daemon continues to buy NO on a contract the operator believes is hedged off. Silent loss accumulation.
- **Bin boundary ambiguity:** Lo-inclusive convention changes between the model that computed the probability and the schema that determined which contract to trade. A temperature on a boundary lands in a different bin than modeled. The winning contract is unowned; the losing contract is held.

---

### 5. The Three Hardest Design Decisions

**Decision 1: One manifest writer or quorum?** Quorum adds fault tolerance but introduces the possibility of split-brain where two writers each believe they hold the lock. **Call: single writer, no quorum.** The cost of a missed cycle is recoverable; the cost of two contradicting cycle manifests executing simultaneously is not. The single writer fails loudly; split-brain fails silently.

**Decision 2: When does position enter the manifest — pre-fill or post-fill?** If position is snapshotted pre-fill, the next cycle may size against a stale position while fills are in flight. If post-fill, the cycle must wait for fill confirmation, slowing the cycle. **Call: post-fill, hard wait.** Sizing errors compound; cycle latency does not. The system waits for fill acknowledgement before sealing the next manifest. Partial fills trigger a manifest re-seal mid-cycle before any further orders.

**Decision 3: Types at the boundary or in the core?** You can enforce the typed contract only at ingestion (boundary typing) and pass untyped data internally, or enforce types throughout. Boundary-only is faster to implement. **Call: types throughout, zero untyped internal paths.** Every internal function that accepts a bin definition accepts the closed algebraic type, not a dict or tuple. The cost of one boundary-typed system is one silent interior mismatch destroying an invariant that the type system would have caught for free.

---

## angle:Observability / no-trade provenance / debuggability.

**Agent ID:** `agent-a30a380bf4d38b7d3`

### Final Assistant Message

# Telemetry for No-Trade Provenance

## (1) Objective

The system makes money only when it trades when edge exists and abstains when it does not. Silent abstention — a candidate evaluated and killed by a gate without any record — creates two failure modes that compound silently: (a) a miscalibrated gate kills real edge repeatedly, and the operator never sees it because there is no record of the kill; (b) a correct gate fires appropriately, but the operator cannot distinguish it from (a), so they cannot trust or tune anything. The objective of this angle is therefore: **every silence must be as legible as every trade**. The operator must be able to reconstruct, for any (market, time) pair, the exact state of every candidate's evaluation — probability estimate, executable price, cost, and the specific gate, with its threshold and input values, that terminated it — from a single indexed query. This is not a debugging convenience; it is the mechanism by which gate calibration is possible at all.

## (2) Ideal Mechanism

At every evaluation cycle, for every (city, date, bin, side) candidate:

**Persist a decision record** containing: timestamp, market identifier (city, date, bin, side), the probability estimate from each forecast provider, the synthesized edge estimate (probability lower bound minus executable price minus cost in probability units), each gate evaluated in order, the gate's name, its threshold, its input value, and its verdict (pass/kill). The record terminates at the first kill with that gate's identifier. If all gates pass, the record includes the order parameters submitted.

**Index by (market_id, eval_time)** so the operator's query is a single lookup: give me all decision records for market X in window [T-epsilon, T+epsilon], one row per candidate, with gate_killed populated or NULL (indicating a trade).

**Emit one record per candidate per cycle**, not per outcome. A candidate re-evaluated every 30 seconds over a 4-hour window that never trades must produce ~480 records, not silence. The cost of storage is trivial; the cost of a single unexplained silence is not.

**Retain executable price as the price at evaluation time**, not a mid or last-trade reference. The gate that killed the candidate may have been correct at that price and wrong at the price 30 seconds later. The record must be sufficient to replay the decision.

## (3) Invariants

- **Completeness**: every (candidate, evaluation cycle) pair produces exactly one record, unconditionally. No candidate is silently dropped before the record is written.
- **Atomicity of the kill gate**: exactly one gate is marked as the kill gate per record; if multiple gates would have fired, only the first in evaluation order is marked (the counterfactual sequence is irrelevant — the candidate died at gate N, not gate N+1).
- **Price currency**: the recorded executable price is the price at which a taker order would have filled at evaluation time, including fee, not a stale reference.
- **No post-hoc mutation**: records are written once and never updated. If a re-evaluation produces a different verdict, it is a new record.

## (4) Silent Failure Modes

- **Gate fires but record is not written**: the operator sees silence and cannot distinguish a broken evaluation loop from a correct kill. This is the highest-severity failure — it makes the gate unauditable and masks both over-killing (gate too tight) and broken infrastructure.
- **Price field is stale or synthetic**: the operator sees a record where edge appears negative, but the gate fired on a stale price that was not actually executable. Real edge was killed by a data-staleness artifact, not by genuine cost analysis.
- **Gate threshold is stored as a constant, not the value at evaluation time**: thresholds drift as calibration updates them. If the record stores only the gate name and verdict, the operator cannot determine whether a kill six hours ago was correct under the threshold that was live then.
- **Record written after order attempt**: if infrastructure fails between evaluation and order submission, the record is never written, and the failure is invisible.

## (5) Three Hard Design Decisions

**A. Write the record before attempting the order, or after?** Write it before — at gate-pass — with `status=PENDING`, then update to `TRADED` or `ORDER_FAILED` on the submission outcome. Never condition the existence of the record on a successful order. A write-after-order design loses all records for infrastructure failures, making those failures undetectable from the telemetry alone.

**B. One record per candidate per cycle, or only on state change?** One per cycle, unconditionally. Change-only records compress storage but require the operator to infer "it was still killed by the same gate" from absence — exactly the reasoning pattern this system is designed to eliminate. Storage is not the constraint.

**C. Should the gate evaluation be re-runnable from the record, or is the record sufficient as a snapshot?** The record must be sufficient as a snapshot — storing all input values, not just the verdict. Re-running requires the evaluation code to be version-stable and all inputs to be reconstructable from other stores; both assumptions fail in practice. The record is the ground truth of what the system believed and decided; it cannot depend on external reconstruction.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-a30ec1a02344f2df3`

### Final Assistant Message

The unresolved findings count is now 0, AND unresolved venue commands = 0. This changes the picture significantly. The phantom finding (5bbc2be2) has already been resolved.

Now I can assemble the complete verdict:

**Kill analysis against each criterion:**

**(a) Is the ideal naive about a real constraint?** No — the candidate correctly identifies the `record_gap()` before first SUBSCRIBED message can set `gap_reason` to non-`not_configured`, blocking `record_message`'s auto-clear path. This is a real code pattern.

**(b) Does existing code already handle it?** YES. The code already handles the full scenario:
- `_record_transport_keepalive()` in `polymarket_user_channel.py:659-688` transitions ANY `DISCONNECTED+m5_reconcile_required=True` state to `AUTHED` on pong arrival, regardless of `gap_reason`.
- After AUTHED, `run_ws_gap_reconcile_and_clear()` in `exchange_reconcile.py:290-344` provides the full M5 clear path — it does NOT require `gap_reason=='not_configured'`.
- `clear_after_m5_reconcile()` only requires `subscription_state in {'AUTHED','SUBSCRIBED'}` (not a specific `gap_reason`).
- The doc comment at lines 141-153 of `ws_gap_guard.py` explicitly acknowledges this exact scenario was previously a bug and was fixed.

The candidate's claim of "permanent latch" is refuted: the code has explicit M5 reconcile recovery for real gaps. The latch is deliberately durable but NOT permanent.

**(d) Would fixing it move a real fill?** The current DB state shows 0 unresolved exchange_reconcile_findings and 0 unresolved venue commands. The `_clean_boot_side_effect_surface_empty()` check would now return True, meaning the next pong after a daemon restart would auto-clear the latch. The prior blocker (phantom finding 5bbc2be2) that prevented this is already resolved. The ws_gap latch in the live daemon is either already cleared or would clear on next pong. There is no code defect to fix here.

---

## agent-a31cf6d3fa6bcdebf

**Agent ID:** `agent-a31cf6d3fa6bcdebf`

### Final Assistant Message

Perfect. Now I have all the context I need to provide the provenance audit:

**Provenance Audit Summary (2026-06-13 snapshot):**

| Item | Status |
|------|--------|
| **Current code SHA** | `0b553c90fd` (gate-mass collapse Wave C/D: remove redundant re-checks + banned throttles) |
| **Tree cleanliness** | Dirty: 3 untracked files in `.claude/` and `docs/operations/` (evidence/investigation artifacts) |
| **Daemon uptime (PID:etime)** | **Main:** 3h18m36s · **Forecast daemon:** 5h41m20s · **Ingest:** 2h37m54s |
| **DB canonical exists** | ✓ All three present: `zeus-world.db` (42G, 23:28), `zeus-forecasts.db` (36G, 23:28), `zeus_trades.db` (18G, 23:28) |

**Key facts from recent commits:**
- Latest: wave C/D gate-mass collapse (redundant re-checks + banned throttles removed)
- Trend: active riskguard / lifecycle / risk-authority work (last 12 commits are all T0-T2 domain consolidations)
- Authority chain fresh: last touch to AGENTS.md was in commit `95d3086ec7` (hook cleanup, 6 commits ago)

All three daemons are live (stable uptime > 2h37m). The tree is ready for analysis.

---

## angle:Order book / executable price / microstructure.

**Agent ID:** `agent-a340aa8c44d2eb4f8`

### Final Assistant Message

## Order Book / Microstructure Angle: First-Principles Design

---

### (1) Objective

Convert a meteorological edge — a probability lower bound q_lcb that beats the executable price after costs — into realized PnL by entering at a price where expected value is positive net of fees, slippage, and adverse selection. The angle does not generate edge; it preserves and harvests it. A strategy with genuine edge destroyed by bad execution is indistinguishable from no edge. The objective is: for every intended position, compute the *true* cost-inclusive executable price, confirm edge survives, then size to the Kelly-optimal quantity given the real price — not a theoretical mid or last trade.

---

### (2) Ideal Mechanism

**Book snapshot requirements.** Before any quote decision, ingest the full visible order book on both the YES and NO sides: best bid, best ask, and at least 3-5 levels of depth with size at each level. A single-touch price is insufficient; you need the *marginal price* at your intended size, because a thin book means your own order walks the book.

**Executable price computation.** For a taker order of size Q on the YES side: sweep the ask ladder, accumulating cost as sum(price_i * min(Q_remaining, size_i)) / Q. This is the volume-weighted average fill price (VWAP-to-fill), not the best ask. For a maker order: the executable price is your posted limit, but must account for queue position and partial fill probability. If you are not at the touch, your fill probability is sub-1 and your effective size is E[fill] = Q * P(fill | queue_depth, order_flow_rate).

**Taker vs maker economics.** Taker costs: fee_taker + (VWAP_fill - mid). Maker costs: -fee_rebate (if any) + adverse selection cost. Adverse selection on a binary near settlement is severe: the party crossing your resting quote has updated information (a new model run, a station reading) that you may not have. Maker quotes must be priced wider than the mid by at least the expected adverse selection spread, estimated from historical cross-trade outcomes. If you cannot estimate this, default to taker-only until you can.

**Tick and minimum size constraints.** The minimum tick determines the coarsest price you can express. If your edge is 0.004 in probability terms but the tick is 0.01, you cannot quote at a price that captures it — you either give up edge or don't trade. Minimum size sets a floor on position granularity; Kelly sizing below minimum size means no trade.

**Phantom book detection.** A book is phantom/stale if: (a) it has not updated in N seconds (venue-specific threshold, empirically calibrated, likely 10-60s), (b) the spread is implausibly wide (wider than historical 99th percentile for this market's age), or (c) both sides show round-lot sizes that have not moved across multiple snapshots. Stale quotes must be treated as non-executable; size to zero until a live refresh confirms depth.

---

### (3) Invariants a Correct Implementation Must Hold

- INV-1: **No size decision is made without a live book snapshot timestamped within threshold T.** T is venue-latency-calibrated, not a constant.
- INV-2: **Executable price is always VWAP-to-intended-size, never touch price.**
- INV-3: **Net edge = q_lcb - executable_price - fee - adverse_selection_reserve > 0 is a hard gate.** Trade does not enter if this is not satisfied.
- INV-4: **Maker quotes carry an adverse selection haircut computed from empirical data, never assumed zero.**
- INV-5: **Size is bounded above by available depth at acceptable price, not just by Kelly notional.** Kelly output is a ceiling, not a floor.

---

### (4) Failure Modes That Silently Destroy Edge

- **Touching at best ask, ignoring depth.** Small apparent edge vanishes when actual fill is 3 ticks worse on a thin book. PnL looks like variance; it is structural slippage.
- **Ignoring adverse selection on maker quotes.** You rest a quote, it fills instantly — because someone with a weather update crossed you. Your fill rate appears good; your PnL is systematically negative. Invisible without tagging fills by whether they filled fast (adverse) vs slow (uninformed).
- **Stale book at settlement approach.** Near expiry, liquidity evaporates. A book showing 500 contracts of depth may have been posted hours ago and be unresponsive. Sizing to that depth and sending a taker order results in a partial fill at a price far from expected.
- **Kelly computed on mid, executed at ask.** Edge calculation and sizing must use the same price. A 2% edge computed at mid becomes a 0.5% edge after a 1.5% spread — below fee threshold.

---

### (5) The Three Hardest Design Decisions

**Decision 1: Taker or maker by default?**
Call: **taker-only until adverse selection is empirically characterized.** Maker appears cheaper (no fee, potential rebate) but carries hidden adverse selection cost that is non-trivial in a market where news arrives discretely (model runs, station reports). Taker cost is known upfront; maker cost requires a fill-tagging loop to measure. Start with known costs.

**Decision 2: How stale is too stale?**
Call: **market-age-dependent threshold.** Early in the trading day, 60s staleness is acceptable (thin activity, wide spreads normal). Within 2 hours of settlement, threshold drops to 5-10s because adverse selection risk is highest and book dynamics fastest. A single global constant is wrong.

**Decision 3: VWAP-to-fill vs limit order with price improvement?**
Call: **VWAP-to-fill for taker, limit-at-touch for maker, never market orders.** Market orders on thin binary CLOBs are uncapped slippage. A limit order that misses is a known cost (opportunity cost, not cash loss). Sizing against VWAP ensures you know your worst-case fill price before you send.

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-a38b661f02c1a4630`

### Final Assistant Message

Now I have everything. Writing the report.

---

## STATE INTEGRITY / CONFIG / FLAGS / DEPLOYMENT — FORENSIC REPORT

### 1. What This Angle Actually Does

The state integrity layer is the system's **deployment identity + submission pre-authorization gate**. It controls:

**DB connection fabric** (`db.py`): WAL mode, busy-timeout antibody, guarded world mutex (in-process threading.Lock), typed K1-aware connection holders (`ConnectionTriple`), and the INV-37 sanctioned cross-DB paths (`get_forecasts_connection_with_world`, `get_trade_connection_with_world_required`).

**Submission gate** (`main.py:664-733`): `evaluate_edli_stage_readiness()` runs every cycle as a pre-submission checklist. ALL of the following must pass before `live_entries_allowed=True`:
- `state/loaded_sha.json` must exist, be valid JSON, and match `_BOOT_STATE["sha"]` (the git HEAD SHA captured at daemon boot)
- `UNRESOLVED_SUBMIT_UNKNOWN` count in DB must be zero
- No open cap reservations
- `source_health.json` must be fresh (< 900s)
- `status_summary.json` freshness is deferred at boot but blocks after the first cycle

If any check fails: `live_entries_allowed=False`, no new orders submitted, no exception raised — silent cycle pass with no orders.

**DT#1 choke point** (`canonical_write.py`): `commit_then_export()` is the single path through which DB commits + JSON exports flow. `transition_phase()` handles `pending_exit` mutations atomically.

**Table-ownership enforcement** (`table_registry.py`): YAML-driven A4 antibody checks table-set equality + column-shape on boot. FATAL on mismatch.

**File locking** (`db_writer_lock.py`): Phase 0 fcntl flock infrastructure — built but not yet wired to any production caller.

---

### 2. Every Defect

**FINDING 1 — SUBMISSION BLOCKER (PRIMARY CAUSE): SHA Drift / Placeholder in `loaded_sha.json`**

`state/loaded_sha.json` currently contains `{"loaded_sha": "abc123", ...}`. Current git HEAD is `06e28761d23a9f275e202752304a6f3cdf615621`. The logic at main.py:879-882:

```python
loaded_sha = str(payload.get("loaded_sha") or ...).strip()
expected_sha = str(_BOOT_STATE.get("sha") or "").strip()
if expected_sha and loaded_sha and loaded_sha != expected_sha:
    return [f"EDLI_STAGE_LOADED_SHA_MISMATCH:loaded={loaded_sha}:expected={expected_sha}"]
```

If the daemon's `_BOOT_STATE["sha"]` is a real SHA (any real git checkout), and `loaded_sha.json` holds the literal string "abc123", this comparison fires unconditionally on every call, returning `EDLI_STAGE_LOADED_SHA_MISMATCH`. At main.py:683, this is `reasons.extend(...)` → line 721 returns `EDLI_STAGE_FAIL` → `live_entries_allowed=False`.

`_EDLI_LIVE_BOOT_DEFERRED_REASON_PREFIXES` (main.py:169-172) only defers `EDLI_STAGE_STATUS_SUMMARY_*` — SHA mismatch is NOT deferred. It goes into `blocking` (main.py:787-790) and hits line 810: `raise RuntimeError("EDLI_LIVE_READINESS_FAIL:..." )` at boot, or silently fails `live_entries_allowed` in mid-cycle calls.

**Reproduction**: `python3 -c "import json; d=json.load(open('state/loaded_sha.json')); print(d['loaded_sha'])"` → `abc123`. Any cycle call to `evaluate_edli_stage_readiness` with real `_BOOT_STATE["sha"]` returns FAIL. No new orders possible.

**How it got here**: `_write_loaded_sha_state()` at main.py:8942 is called at daemon boot with `_boot.get("sha")`. If the boot SHA detection (`_capture_boot_state()`) returned `None` — which it does when git is unavailable or `ZEUS_ACCEPT_STALE_DEPLOY=1` — then main.py:4442-4445 skips the write entirely. A stale file from a prior manual write or failed boot remains. "abc123" is a sentinel/placeholder that was written manually or by a test fixture and was never overwritten by a genuine boot.

**This is the active submission blocker for ~1.5 days of zero orders.**

---

**FINDING 2 — POTENTIAL SECONDARY BLOCKER: `_edli_stage_loaded_sha_reasons` skips check when `expected_sha` is falsy**

main.py:881: `if expected_sha and loaded_sha and loaded_sha != expected_sha`. If `_BOOT_STATE["sha"]` is `None` (git unavailable), `expected_sha` is empty string → the mismatch branch is skipped. Then line 883-884: `if not loaded_sha` → `"abc123"` is truthy → this also passes. So the SHA gate becomes a **no-op** when git is unavailable. The system would then proceed to submit orders with a stale/fake SHA on file — a stealth compliance gap.

**Reproduction**: Set `ZEUS_ACCEPT_STALE_DEPLOY=1` before boot → `_boot["sha"]` is None → `_BOOT_STATE["sha"]` = None → SHA gate silently passes with "abc123" on disk. Not a current blocker but a silent bypass path.

---

**FINDING 3 — DEFECT (SUBMISSION RISK): `detect_stale_portfolio()` returns False on legacy JSON**

`canonical_write.py:80-96`: when `portfolio_json` lacks `last_committed_artifact_id`, the function returns `False` (assume fresh). The docstring implies this is conservative — but it means a genuine drift between `positions.json` and DB is invisible for any file without the key. Any code relying on this for staleness gating trusts a lie.

**Reproduction**: Delete `last_committed_artifact_id` from `positions.json` → `detect_stale_portfolio()` returns `False` regardless of DB state.

**Verdict**: DEFECT_NO_CURRENT_IMPACT unless a consumer acts on the False to make a consequential decision.

---

**FINDING 4 — DEAD CODE: `ConnectionPair` + `get_connection_pair()`**

`connection_pair.py:197`: explicit comment "Legacy class. New code should use ConnectionTriple." Header comment confirms `last reused: never`. `get_connection_pair()` (line 251-265) still calls `get_trade_connection()` and `get_world_connection()` — but no URI-level `?mode=ro` is enforced (comment: "Phase 3 will enforce URI-level RO" — never landed).

**Verdict**: DEAD_DELETE. No production caller. The in-comment promise of RO enforcement is an orphaned intent.

---

**FINDING 5 — DEFECT: `get_trade_connection_with_world_optional()` silently degrades**

`db.py:654-694`: if ATTACH fails, logs a WARNING and returns the trade connection anyway — without world DB attached. Any caller that does a cross-DB write through this path will write to trades only, violating INV-37 atomicity, with no exception raised.

**Reproduction**: Force world DB locked → call this function → caller gets a bare trade connection and proceeds to write. Cross-DB invariant silently broken.

**Verdict**: This is a money-path defect. The `_required` variant (line 697-728) exists and raises — callers MUST use required, not optional, for any cross-DB write. Any caller using `optional` for a write is a live INV-37 violation.

---

**FINDING 6 — DEFECT (INDEFINITE ENTRY BLOCK): `check_quarantine_timeouts()` `blocks_entry` never cleared for unresolved chain-only facts**

`chain_reconciliation.py:1529-1603`: after 48h, `QUARANTINED` positions escalate to `ChainOnlyFact` review state. But `blocks_entry` stays `True` until an operator manually resolves the suppression row. If no operator resolves it, entry for that condition_id is blocked indefinitely, even after 48h.

**Reproduction**: let a position reach QUARANTINED with a chain-only fact → wait 48h → it escalates to review but blocks_entry stays True → no new entry on that condition_id forever.

**Verdict**: Active defect. Whether currently firing depends on whether any position_events rows have reached that state. With `active=1, day0_window=1` in the portfolio (essentially empty), this is unlikely to be the current blocker — but it is a persistent, silent gate for any future quarantine case.

---

**FINDING 7 — OVER-BUILT / PHASE 0 DEAD INFRASTRUCTURE: `db_writer_lock.py` flock surface**

`WRITE_CLASS_STAGING_ALLOWLIST: frozenset = frozenset()` at db_writer_lock.py:879. No production caller is in the staging list. The entire flock machinery — six lock files, `WriteClass` enum, `BulkChunker` watchdog — is built but unconnected. The actual serialization in production is the in-process `_GuardedWorldMutex` (threading.Lock).

`BulkChunker` (line 157-497): 30s watchdog fires `_thread.interrupt_main()` on timeout. If any bulk job using `BulkChunker` runs during a live trading cycle, a false-alarm watchdog could kill the main thread. This infrastructure is Phase 0 only and should not be touched during live cycles.

**Verdict**: DEFECT_NO_CURRENT_IMPACT for the flock. `BulkChunker` watchdog is a latent live-cycle hazard if any caller activates it.

---

**FINDING 8 — SEMANTIC CONTRADICTION: `edli_arm_gate_emit_enabled: false` emits but does not gate**

`config/settings.json`: `edli_arm_gate_emit_enabled: false`. The ARM gate artifact (`state/edli_arm_gate_artifact.json`) shows `coverage_licensed: false` and `capital_weighted_ev: -0.151`. If the gate were enforced, this would block submission. Since the flag is `false`, the artifact is diagnostic only — but the naming is inverted: `emit_enabled: false` sounds like "don't emit" when the actual behavior is "emit but don't enforce as a hard gate."

**Verdict**: DEFECT_NO_CURRENT_IMPACT (not a gate), but the flag name is semantically backwards and creates confusion about whether the gate is active.

---

**FINDING 9 — MISSING RETURN PATH IN `transition_phase()`**

`canonical_write.py:149`: returns `False` without logging if `is_terminal_state(current_phase) or current_phase == "economically_closed"`. Any caller silently gets `False` and must check the return value; if it doesn't, the transition is silently dropped.

**Verdict**: DEFECT_NO_CURRENT_IMPACT unless a caller ignores the False return — but no log makes root-cause difficult.

---

### 3. What Is Notably ABSENT

**No per-cycle `loaded_sha.json` freshness guard**: the SHA file is written once at boot (main.py:8942). If the file is deleted or corrupted mid-run, the next cycle either gets `EDLI_STAGE_LOADED_SHA_MISSING` (blocks) or succeeds with stale data. There is no daemon watchdog that re-writes the file if it disappears. A monitoring job that alerts on `loaded_sha.json` age or content would catch the "abc123" situation before ~1.5 days of zero orders.

**No URI-level `?mode=ro` enforcement for read-only world connections**: commented as "Phase 3 will enforce URI-level RO" in `connection_pair.py:251-265`. Phase 3 never landed. A miscoded caller could write to the world DB through a nominally read-only connection with no SQLite-level rejection.

**No production flock serialization**: the `db_writer_lock.py` flock is Phase 0 / unconnected. Concurrent daemon restarts or parallel processes could write to the same DB simultaneously; only the in-process threading.Lock (`_GuardedWorldMutex`) protects within a single process.

**No automated escalation from ChainOnlyFact review**: `check_quarantine_timeouts()` escalates to review but there is no automated timeout or operator notification. An entry block from a chain-only fact can persist indefinitely with no alert.

**No assert that `_write_loaded_sha_state()` succeeded**: main.py:4461 logs an error but does not raise. A failed write is invisible to the gate — the old stale file stays on disk. There should be a startup assertion that the file exists and contains a real SHA post-write.

---

### 4. Provenance Verdicts

| File | Verdict | Created / Last Audited | Law Regime |
|---|---|---|---|
| `src/state/canonical_write.py` | CURRENT_REUSABLE | 2026-05-18 / 2026-05-18 | DT#1 / INV-17, commit-then-export choke point; authority current |
| `src/state/connection_pair.py` | DEAD_DELETE | 2026-04-30 / 2026-05-14, last reused: never | Pre-K1 era; `ConnectionPair` explicitly retired in header; `get_connection_pair()` is orphaned; Phase 3 RO enforcement promise undelivered |
| `src/state/db.py` | CURRENT_REUSABLE | Pre-2026-04-26 / 2026-06-08 | WAL+busy-timeout+world-mutex+INV-37 paths are current law; `connect_or_degrade()` and `get_trade_connection_with_world_optional()` are latent hazards but not structurally stale |
| `src/state/chain_reconciliation.py` | CURRENT_REUSABLE with caveat | 2026-05-27 area | Three-rule reconciliation is current; `check_quarantine_timeouts()` has the `blocks_entry` no-clear defect (Finding 6) — not stale law, but incomplete design |
| `src/state/db_writer_lock.py` | QUARANTINED | 2026-05-07 / 2026-05-12 | Phase 0 staging only; `WRITE_CLASS_STAGING_ALLOWLIST` is empty; `BulkChunker` watchdog is a live-cycle hazard if activated; do not expand callers until Phase 1 retrofit completes |
| `src/state/table_registry.py` | CURRENT_REUSABLE | 2026-05-14 / 2026-05-14 | A4 antibody + INV-05 FATAL-on-mismatch; `assert_writer_jobs_registered()` AST scan is current |
| `config/settings.json` | CURRENT_REUSABLE | — | `real_order_submit_enabled: true`, `edli_live_operator_authorized: true`, `edli_live_scope: forecast_plus_day0` all set correctly; not the source of blockage |

---

**Root cause of ~1.5 days zero orders**: `state/loaded_sha.json` holds `"loaded_sha": "abc123"` — a placeholder that was never overwritten by a genuine boot SHA write (either `_capture_boot_state()` returned None and the write was skipped per main.py:4442-4445, or the file was manually planted). Every cycle: `_edli_stage_loaded_sha_reasons()` fires `EDLI_STAGE_LOADED_SHA_MISMATCH` → `evaluate_edli_stage_readiness()` returns `EDLI_STAGE_FAIL` → `live_entries_allowed=False` → no submission attempted.

**Fix**: write the real current git HEAD SHA into `state/loaded_sha.json`, then restart the daemon so `_BOOT_STATE["sha"]` and the file agree. Alternatively: `python3 -c "import subprocess, json, pathlib; sha=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(); pathlib.Path('state/loaded_sha.json').write_text(json.dumps({'loaded_sha': sha, 'generated_at': 'manual-repair'}))"` — but a full daemon restart is required to re-populate `_BOOT_STATE["sha"]` from the live checkout.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-a3d302a98db2dc2c9`

### Final Assistant Message

The write inserts `SCHEMA_VERSION=42` (frozen). The table CHECK allows up to 42. So INSERT would succeed. The schema check doesn't fire, and INSERT with 42 should work.

The defect's claimed mechanism — "assert raises because user_version < 42 or `42 not in table_sql`" — is demonstrably wrong. The user_version is 43 (> 42, so the check is `0 < 43 < 42` = False), and "42" IS in the table SQL. The assert passes.

The 17-day write gap is real but caused by something else entirely — structural changes to how `decisions` are populated or what `_enum` values result. The described schema-check-based swallow mechanism is the WRONG root cause.

Does this observability gap matter for fills? The NTE table is logging/learning infrastructure explicitly marked "fail-soft" — the assert exists to protect against degraded observability, but write failures here cannot cause or prevent order submission. The LIVE EVIDENCE section confirms real blockers are B1 (submit latch) and B2 (edge gate), not NTE observability.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a3f66554110efa2fb`

### Final Assistant Message

The timeout hit on calibration_pairs - it's likely large. Let me use scalar queries and route output to files.

---

## refuter:candidate=DEFECT (angle R5, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a402c89b2861166df`

### Final Assistant Message

This is the critical finding. The note in settings.json says `day0_remaining_day_q_enabled=true` was set assuming `edli_live_scope=retired_day0_no_submit_scope`, but `edli_live_scope` is now `forecast_plus_day0` (day0 is LIVE). Let me verify whether `q_remaining_day` is actually written to receipts when the flag is on.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 99):

**Agent ID:** `agent-a4150df29b88c739c`

### Final Assistant Message

Now I have a complete picture. Let me synthesize:

Key facts established:

1. The repro confirms: MAX(decision_time) in decision_certificates = 2026-06-12T17:04:15, with zero rows after that date. The empirical claim is factually correct.

2. The decision engine is NOT mechanically dark — it is actively processing events (opportunity_event_processing shows 857 "processed" rows with MAX(updated_at)=2026-06-14T05:38) and producing decision_compile_failures through 2026-06-14T05:38.

3. The dominant failure mode post 06-13 is TRADE_SCORE_NON_POSITIVE and EVENT_BOUND_ALL_CANDIDATES_REJECTED with capital_efficiency_lcb_ev rejections. The q_lcb values shown are 0.0000 against prices of 0.001–0.005 (ev_per_dollar = -1.0), which are honest rejections: zero posterior probability mass on bins with real market prices.

4. The last receipts (before the gap) had q_lcb of 0.77–0.85 against c_fee_adjusted around 0.67–0.76 — those were legitimate edge opportunities that no longer recur.

5. The "freeze" claim misidentifies the mechanism: certificates stopped because the engine's TRADE_SCORE stage is rejecting all candidates, not because the engine itself stopped. This is the B2 blocker (decision gate) behavior, not a separate "complete darkness" failure.

6. The "positive trade_score" framing in the evidence is from receipts timestamped 2026-06-12T12:12 — those were betting on settled or near-settled markets (q_lcb=0.77 on a temperature bin with price 0.70 is a heavily-in-the-money market, not a new edge bet). The last 3 actual LIVE-mode certificate chains (06-12T17:04) were ActionableTradeCertificate + ExecutionReceiptCertificate, meaning they did produce orders — but those were the last candidates with positive ev.

7. The git log shows commits on 06-12 afternoon including `no-order ROOT: un-zero buy_no q_lcb` and `maker quote carries maker fill prior` — these changes went live around the freeze point. Whether those changes improved or worsened edge availability is ambiguous from this angle, but they don't represent a mechanical block.

The claim "decision engine completely dark — zero new certificates" is partially true (zero certificates) but the characterization as a MECHANICAL_BLOCK is incorrect. The engine runs, fires, processes events, produces compile failures — it's the capital_efficiency gate (honest q_lcb > price check) that rejects everything. This is either honest no-edge OR calibration suppression. The claim also asserts the last receipts had "positive trade_score (0.007-0.076)" — that number appears to reference a different field than the receipts I can see (those show q_lcb 0.77-0.85), suggesting the evidence description may be referring to a legacy trade_score column no longer in the current schema.

---

## refuter:candidate=DEFECT (angle R5, tag MISSING_CAPABILITY, impact 90):

**Agent ID:** `agent-a428318fb5062e41e`

### Final Assistant Message

Now I have enough to form a complete verdict. Let me summarize what I found:

1. **Repro confirmed**: Zero hits for `settlement_attribution|retired_comparison_tool|promotion_readiness` in `candidate_evaluation.py` and `reactor.py`. The open loop is real at the code level.

2. **Line 1020 citation is fabricated/misread**: The cited "Does NOT gate or block submissions" comment does not appear at line 1020 of `settlement_skill_attribution.py`. Line 1020 is inside the argparse description string. The advisory-only language exists in calibration modules (`blocked_oos.py`, `effective_sample_size.py`) but those carry `SHADOW_ONLY: bool = True` with ZDM-02 governance, not in `settlement_skill_attribution`.

3. **The ideal itself is naive**: The defect assumes that skill win-rate from post-settlement grading COULD or SHOULD lift q_lcb at decision time. But settlement outcomes are only known AFTER markets close. The grading (SKILL_WIN, LUCKY_WIN, etc.) requires knowing the actual temperature outcome. It cannot feed q_lcb at order-submission time — q_lcb is a pre-settlement probability estimate. This is a fundamental causal constraint: you cannot use post-settlement labels to construct pre-settlement probability bounds.

4. **Would fixing it move a real fill?** No. Even if you built a feedback loop, the only actionable path is "update the calibration model using historical skill grades" — which is exactly what the existing EMOS/Platt calibration pipeline does using settlement outcomes directly. The skill_attribution layer grades ALREADY-SETTLED positions; it adds no information beyond what the calibration layer already uses from `settlement_outcomes`. The `capital_efficiency_lcb_ev` rejection is driven by `q_lcb <= execution_price`, and q_lcb comes from the probability model (EMOS, sigma-floor, James-Stein shrinkage), not from post-hoc skill grading of past trades.

---

## reconcile:Minimal kernel / rebuild boundary / keep-list

**Agent ID:** `agent-a4343e94932975b8b`

### Final Assistant Message

I have enough to reconcile. The key corrections from code vs the ACTUAL report:

1. D1/D5 (BH/FDR unconditional gate) - confirmed, era.py:2811 `authority_on=False` is live
2. The buy_no p=1.0 hardcode (mentioned in ACTUAL's "notably absent" section) — **FIXED** in both the replacement path (era.py:10022) and canonical path (era.py:10367). This is not a current defect.
3. D4 (C2 market-anchor cap) — default is `False` at era.py:9636, so this is NOT currently active. ACTUAL overstated its live impact.
4. D9 (governor asymmetry) — cr.py:976 uses `allow_submit, True` for diagnostic but gate at cr.py:398 uses `allow_submit, False`. CONFIRMED defect.
5. D7 (dispatch silent drop) — CONFIRMED live (post-A6 default ON), but warning log was added.
6. WITNESS Stage 3 zero (opportunity_fact) — This is in `cycle_runtime.py` which is NOT the live decision path (live path = `event_reactor_adapter.py`). The "Stage 3 dark" is a metrics table gap, not a candidate generation gap. Stage 4 (decision_certificates) dark after 06-12 is the real signal.

---

## angle:Execution submit / ack / fill / reconcile lifecycle.

**Agent ID:** `agent-a4463ed681713a5a1`

### Final Assistant Message

I have all the evidence needed. Here is the forensic verdict:

---

**B1 ROOT MECHANISM — HOW THE WS-GAP LATCH FREEZES**

The freeze path is a 3-link chain:

1. `ws_gap_guard.py:record_gap()` (line 261) sets `m5_reconcile_required=True` whenever a WS disconnect is observed. The latch is only cleared by `clear_after_m5_reconcile()` (line 217), which requires `findings_count=0 AND unresolved_findings_count=0`.

2. `exchange_reconcile.py:run_ws_gap_reconcile_and_clear()` (line 290) calls `list_unresolved_findings(conn)` (line 320) and refuses to call `clear_after_m5_reconcile` if anything is unresolved (line 331-333: `if findings or unresolved: result["reason"] = "m5_findings_unresolved"; return result`).

3. Finding `5bbc2be2` (`position_drift`, token `2599807256...`, context `ws_gap`, recorded `2026-06-12T22:58`, `resolved_at IS NULL`) is the ONE row blocking the latch. Evidence: `exchange_size=0, journal_size=17.05, expected_wallet_size=34.10`. The 34.10 double-count is the void-misbooking: `confirmed_wallet_size=17.05` from the journal BUY trade fact PLUS `closed_position_size=17.05` from the `position_current` row (`phase=voided, chain_state=synced`) both feeding `expected_wallet = 0 + 0 + 17.05 + 17.05`.

**WHY TASK #31'S ABSORBER DID NOT ABSORB IT**

Task #31 (commit `6629d35a54`, 2026-06-11) deployed two absorbers:

- **Variant-3 operator-ack absorber** (`_absorb_operator_external_close`, exchange_reconcile.py:1144): requires a prior RESOLVED `position_drift` finding for the same `subject_id` whose `resolved_by LIKE 'session_operator_confirmed%'` or `resolution LIKE 'operator_manual%'`. No such row exists for this token (confirmed by empty result of the prior-resolved-drift query). The absorber therefore returned `False` and the finding was recorded (line 2009).

- **Settled-class absorber** (`_resolve_position_drift_tokens_from_current_truth`, line 2445): fires when `calendar_terminal.get(token)` is not None. That requires the token to appear in `market_events` (in zeus-forecasts.db) by `token_id` OR via condition_id bridge through `executable_market_snapshots`. This is a **NO-side token** (`no_token_id` column in `position_current`, `direction=buy_no`). The market_events table stores only YES-side tokens by `token_id`. The EMS lookup (`_condition_ids_for_tokens`, line 2164) queries `yes_token_id / no_token_id / selected_outcome_token_id` against EMS, but the EMS row for this market is absent from `executable_market_snapshots` (no rows returned). So `condition_map` is empty, `calendar_terminal` has no entry for this token, the settled-class absorber also misses, and the drift falls through to `record_finding()`.

The finding was recorded at 22:58 UTC, 32 minutes after the `position_current` row was voided at 22:26. Both task #31 absorbers were in place but neither could reach this token: one needs an operator-ack row (none), the other needs an EMS row to bridge the NO-side token to a market calendar entry (EMS absent).

**CONFIRMED: PHANTOM. NOT REGRESSION OF TASK #31.**

Task #31 correctly solved the "swept settled winner whose EMS row is present in the snapshots table" case. This token is a different failure mode: a **BUY_NO entry that EXPIRED** (venue_command state=EXPIRED, 2026-06-11T17:56), then chain_sync voided the position. The void left a `position_current` row with `chain_state=synced` that counts as a `_closed_position_token_holdings_by_token` holding. The journal still has 17.05 from the BUY trade fact. No EMS row means the settled-class absorber cannot identify the market as calendar-terminal. No operator-ack means the variant-3 absorber cannot fire. The double-count is real by the code's accounting rules.

**FIX: The position was never filled — venue_command is EXPIRED, not FILLED.** The `position_current` row should never have existed or should be zero-shares. The void misbooking in this case is that chain_sync created (or preserved) a `position_current voided/synced` row with `shares=17.05` for a command that EXPIRED (never filled). The correct absorber gate is: if the linked venue_command state is EXPIRED and no positive trade facts exist, the journal long is phantom — treat the closed_position_size as 0. That path does not exist in the current absorber logic.

**S1-S7 / b1825c4a07 / e583af06cd VERDICT — NOT CAUSAL**

- `b1825c4a07` (2026-06-08, "bin-selection S7 delete opportunity-book selector on/off gate") touched only `src/engine/event_reactor_adapter.py` and `src/events/opportunity_book.py`. It removed the `selector_enabled` gate that was nulling `selected_candidate_id`, and removed dead `_env_flag_enabled` helper. No touch to execution, reconcile, or ws_gap_guard. It cannot have caused B1.

- `e583af06cd` ("gate-mass collapse Tier-A/B: remove 6 shadow strategy modules + q-shadow/exit flags") removed shadow strategy modules. No execution/reconcile/ws_gap touch. Not causal.

- The finding was recorded 2026-06-12T22:58. The closest absorber commit is `6629d35a54` (2026-06-11). The absorber was correctly deployed but has a gap: NO-side expired commands leaving phantom `position_current` rows with no EMS bridge and no operator-ack.

**COMMAND_RECOVERY PROJECTION-REPAIR ERROR**

This is a separate path. `command_recovery.py` (last_reviewed 2026-06-11) calls `reconcile_recorded_maker_fill_economics()` which now includes `_reconcile_recorded_exit_fill_projections()`. The "filled-entry-projection-repair error" logged is in `reconcile_recorded_maker_fill_economics` summary["errors"] counter — a caught exception in the per-row try/except at exchange_reconcile.py:1454. Does not affect B1 (the ws_gap latch is driven by `list_unresolved_findings`, not by maker-fill repair).

**BOTTOM LINE**

B1 is a single unresolved `position_drift` finding for an EXPIRED BUY_NO command whose chain_sync created a phantom `position_current voided/synced shares=17.05` row. Both task-#31 absorbers miss it: the settled-class absorber needs an EMS row that is absent, the operator-ack absorber needs a prior acknowledged resolution that was never created. The fix is to add a third absorber path in `_absorb_operator_external_close` or `_record_position_drift_findings` (exchange_reconcile.py ~line 1988 and ~2009): when `closed_position_size > 0` AND the linked venue_command is EXPIRED with zero trade facts, the expected-holding is phantom and `closed_position_size` should be treated as 0, clearing the drift. Alternatively, chain_sync should not write/preserve a `position_current` row with positive `shares` for an EXPIRED command. The relevant token is `25998072565711727698258544609688934677406873903623466853003437606533488235694` (NO-side), command `2f5ed5ce49aa4b69` (ENTRY/BUY/EXPIRED, venue order `0x74ab0da6...`).

---

## refuter:candidate=DEFECT (angle R3, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-a4471d46544d95a8d`

### Final Assistant Message

I have all the evidence needed. Let me now assess kill criteria (a)-(d):

**Key findings:**

1. **Repro confirmed**: `buy_no_scaling_factor()` returns 1.5 (fallback), not 0.5 (settings). All 14 assumptions expired.

2. **Settings vs fallback deltas (material)**:
   - `buy_no_scaling_factor`: settings=0.5, fallback=1.5 — 3x MORE aggressive threshold with fallback
   - `buy_yes_scaling_factor`: settings=0.3, fallback=1.0 — 3.3x MORE aggressive threshold with fallback
   - `buy_no_floor`: settings=-0.02, fallback=-0.03 (minor)
   - `buy_yes_floor`: settings=-0.01, fallback=-0.02 (minor)

3. **Effect direction**: `edge_threshold = -abs(entry_ci_width) * scaling_factor`. Fallback scaling_factor=1.5 vs intended 0.5 means threshold is -0.15 instead of -0.05 (for ci_width=0.1). The exit fires when `evidence_edge < edge_threshold`, so a MORE negative threshold makes the exit HARDER to trigger (requires more adverse edge move to exit). This makes the fallback MORE conservative about exits (holds longer), not more aggressive about exiting.

4. **Kill criterion (b) — does existing code already handle it?** No. The code literally reads the fallback value (confirmed by repro printing 1.5). The handling IS the fallback — it silently uses 1.5 instead of 0.5.

5. **Kill criterion (d) — would fixing it move a real order?** These parameters control EXIT decisions, not ENTRY. The system is currently blocked at B1 (submit latch) and B2 (no edge at entry). The ExpiringAssumption expiry affects exit sizing for the ONE live position (Beijing NO per B3), not entry fills. Entry is governed by `q_lcb > price` which is unrelated to these scaling factors.

6. **Impact on exits vs entries**: The defect is real and the values genuinely differ from intended. However: (a) exits don't create fills — entries do; (b) the fallback values make exit thresholds more conservative (holds longer), which may actually be beneficial or neutral for existing positions; (c) the B1 submit latch is blocking ALL new entries regardless.

The defect is code-true: values are genuinely wrong (fallback instead of settings). But fixing it would change exit conservatism for currently held positions, not move new entry orders. With B1 blocking all submissions, this cannot move a real fill. It is a real defect with no current-fill impact — DEFECT_NO_CURRENT_IMPACT.

---

## angle:1. e583af06cd (gate-mass collapse, 2026-06-13) removed `selection_eb` flag but retained the `EB computation` with `authority_on=False`. This is correctly noted in the commit as "BH/FDR is the unconditional live selection gate." No materialization breakage.

**Agent ID:** `agent-a45c826aa6c107bf7`

### Final Assistant Message

I now have all the evidence I need. The three reports agree on the key divergences and the file:line anchors are confirmed. Let me emit the structured output.

---

## refuter:candidate=DEFECT (angle R7, tag MISSING_CAPABILITY, impact 72):

**Agent ID:** `agent-a47fb41608f27b4dc`

### Final Assistant Message

I now have enough to make a decisive ruling. The key facts are:

1. **The bug in `scan_full_hypothesis_family` is real and confirmed**: lines 103-105 show `if not _supports_buy_no_for_bin(analysis, idx): continue` followed immediately by an unconditional `continue` — meaning NO buy_no hypothesis body ever executes, so the function always returns zero buy_no hypotheses.

2. **The adapter already compensates for this exact defect**: The `else` branch at line ~10362 explicitly handles the case where `no_hyp is None` (which it always is, because the scan never emits buy_no hypotheses). It calls `_native_no_edge_positivity()` to compute the correct p_value/prefilter for buy_no from native costs and q_lcb_no. The comment at lines 10341-10358 even NAMES this defect and explains the compensation.

3. **The compensation path is always taken**: Since `no_hyp` from `hypothesis_by_label_direction.get((range_label, "buy_no"))` is always `None` (the scan never emits it), the `else` branch fires unconditionally for every bin where YES is executable — exactly what the FINDING-D fix intended.

4. **The claim that "adapter compensates" is the kill**: The defect is real structurally (the scan doesn't emit buy_no hypotheses), but the adapter explicitly and by design compensates for it, with full documentation of exactly this scenario. The fix is already IN the adapter's `else` branch. The question of whether the structural gap in the scan would cause a regression in the adapter's compensation path is answered: the adapter does NOT rely on `no_hyp` being non-None; it handles that case with `_native_no_edge_positivity`.

The defect claim's own evidence acknowledges this: "The FDR denominator in the adapter then relies solely on `_native_no_edge_positivity`" — and that's exactly what it's designed to do. This isn't a regression risk; it's the intended architecture after FINDING-D.

---

## reconcile:State integrity / config / flags / deployment / concurrent-e

**Agent ID:** `agent-a4b28dce304414983`

### Final Assistant Message

I have sufficient evidence from the three reports to produce the structured output. Let me reconcile the divergences now.

Key facts established:
- `state/loaded_sha.json` contains `"loaded_sha": "abc123"` (EMPIRICAL: confirmed by Bash read)
- `ws_gap_guard.py:31,40-42` freezes `allow_submit=False` when unresolved `position_drift` row exists (ACTUAL-BLIND + ACTUAL-DIFF both confirmed, file verified)
- WITNESS: Stage 3 (candidates) = zero rows entire window; Stage 4 (decision_certificates) went dark 2026-06-13 coincident with B1
- ACTUAL-DIFF identifies the proximate B1 cause as the unresolved `finding_id=5bbc2be2` in `exchange_reconcile_findings`, NOT the SHA issue
- ACTUAL-BLIND identified SHA mismatch as the blocker — but ACTUAL-DIFF contradicts this: SHA is in grace, ws_gap_guard is the active gate
- `get_trade_connection_with_world_optional` silently degrades on ATTACH failure (db.py:685,693)
- `detect_stale_portfolio` returns False when `last_committed_artifact_id` is absent (canonical_write.py:87)
- `db_writer_lock.py` flock unconnected, `WRITE_CLASS_STAGING_ALLOWLIST` empty (Phase 0 dead)
- `connection_pair.py` confirmed dead — `ConnectionPair` retired, Phase 3 RO enforcement never landed
- Stage 2 EMS snapshots healthy (10,194 conditions, last snapshot 2026-06-14T05:22)
- Stage 5-7 receipts/envelopes/commands all went silent 2026-06-12T13:04

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a4ce78a0bba666a12`

### Final Assistant Message

This is the kill. The Python test confirms it directly:

- When `_BOOT_STATE['sha']` is `None` (its initial default value, and what happens if `ZEUS_ACCEPT_STALE_DEPLOY=1` or git fails), `expected_sha` is `""` (falsy)
- The guard at line 881 is `if expected_sha and loaded_sha and loaded_sha != expected_sha` — the short-circuit on `expected_sha` being falsy means **the mismatch is never triggered**
- The function returns `[]` (no reasons), i.e., the SHA check PASSES even with "abc123" in the file

The daemon, at startup (line 8935-8942), calls `_capture_boot_state()` to get the real SHA, then immediately calls `_write_loaded_sha_state()` which OVERWRITES `state/loaded_sha.json` with the real SHA. So by the time any scheduler tick calls `evaluate_edli_stage_readiness()`, `_BOOT_STATE['sha']` is the real SHA AND the file contains the real SHA — they match, gate passes.

The "abc123" in the file is a stale on-disk artifact from a test/script run, not from the live daemon. The live daemon would have overwritten it at boot. The defect is REFUTED: the code already handles this correctly through the boot-time write and the `expected_sha` falsy guard.

Even if hypothetically the daemon ran with a stale "abc123" file and a real `_BOOT_STATE['sha']`, fixing this would not move a real order — the active submit blocker is B1 (ws_gap latch on phantom finding 5bbc2be2), not a SHA mismatch.

---

## angle:Opportunity universe / candidate generation / bin-action selection.

**Agent ID:** `agent-a4e1d711105e87f85`

### Final Assistant Message

Now I have all the evidence needed. Let me synthesize the findings.

**VERDICT: b1825c4a07 (S7) removed ONLY a toggle — candidate materialization survived intact. The decision silence (B2) is caused by the capital_efficiency_lcb_ev gate and calibration suppression producing q_lcb=0 on longshot bins, not by any S1-S7 or e583af06cd regression in the candidate generation machinery.**

---

FORENSIC AUDIT: Opportunity Universe / Candidate Generation / Bin-Action Selection

**b1825c4a07 (S7, 2026-06-08): ONLY a toggle removal — materialization is intact**

The commit's own log and the code confirm precisely what changed:

- `opportunity_book.py:to_receipt_dict` (line 60): the `selector_enabled` cache-flag branch that could null `selected_candidate_id` is DELETED. The decision is now recorded unconditionally from `self.selected_candidate_id` == `decided_candidate_id`.
- `event_reactor_adapter.py`: the dead `"selector_enabled": True` and `"selector_shadow": _env_flag_enabled(...)` cache_summary keys + `_env_flag_enabled` helper + `import os` are DELETED. The `_env_flag_enabled` helper was the only thing that could suppress the selection at the cache-key level.

The full materialization pipeline is **UNTOUCHED** by S7:

- `_generate_candidate_proofs` → `_live_yes_probabilities` → builds proofs (event_reactor_adapter.py:7264)
- `_selected_candidate_proof` → `_selection_scoped_proofs` → `_select_proof_by_robust_marginal_utility` (event_reactor_adapter.py:9072-9134)
- `_opportunity_book_from_proofs` → `build_family_opportunity_book(decided_candidate_id=...)` (event_reactor_adapter.py:7251)

S7 removed the one path by which a misset/absent cache flag silently nulled a REAL selection in `to_receipt_dict`. Pre-S7, if `selector_enabled` was missing from `cache_summary`, the recorded `selected_candidate_id` was `None` even though `decided_candidate_id` was set — a silent kill. Post-S7 the decision is always recorded. This is a fix, not a regression.

**e583af06cd: also not a materialization regression**

The gate-mass collapse removed: exit_policy/exit_belief/exit_calibration_alarm/exit_policy_shadow (exit organs), james_stein_blend/horse_race_kelly (shadow sizing — `authority_on=False` hardcoded), and 4 dead config flags. The FDR/BH selection gate is explicitly preserved: "selection_eb EB-computation RETAINED: call site hardcodes authority_on=False, BH/FDR is the unconditional live selection gate." None of these touch candidate generation, proof materialization, or the ΔU ranker. The e583af06cd commit also removed `cycle_runtime.py`'s shadow block, which had zero effect on the live selection path.

**Why every active market IS emitting a candidate but ALL are rejected (B2)**

The `no_trade_regret_events` data from June 13 is conclusive. Every family is being reached and decided — n=22 proofs per family (the full 22-bin per-city family is being materialized). The rejection label is invariably:

`EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=N other=M`

The dominant rejection category `capital_efficiency_lcb_ev` is the gate at `live_admission.py:114`: `(q_lcb - price) / price <= 0`, i.e. q_lcb <= price. This gate is directionally correct and honest — it fires because q_lcb (the 5th-percentile LCB) is at or below the market ask price, meaning the conservative estimate shows no edge.

Observed examples from live DB:
- KL 35C+ buy_yes: q_lcb=0.0392, price=0.0080 — this would PASS capital_efficiency (0.0392 > 0.0080), yet `ev_per_dollar=3.90` confirms edge. But the regret labels show these ARE being rejected somewhere. Looking at the full rejected families in B2 context: Tel Aviv 32C buy_yes q_lcb=0.0275 price=0.0010, Munich 26C+ q_lcb=0.0000 price=0.0010. The q_lcb=0.0000 cases are the dominant class — q_lcb is being suppressed to zero by calibration.

The calibration suppression to q_lcb=0.0000 on longshot YES bins (the "S1-S7 bin-selection collapse" named in the RECENT CHURN section) is the upstream cause of the decision silence. The S1-S7 changes introduced ProbabilityUncertainty-based q_lcb derivation (S2, adapter line ~68-77). When the bootstrap produces wide uncertainty on longshot bins, q_lcb_5pct can collapse to ~0. This is NOT a toggling/materialization bug — the probability arithmetic is running and producing these suppressions.

**Relationship to active blockers**

- B1 (SUBMIT GATE): unrelated to this angle. The ws_gap latch is an `exchange_reconcile_findings` phantom row blocking `allow_submit`. Submit would be frozen even if every candidate had positive edge.
- B2 (DECISION GATE): CONFIRMED origin in this angle. Candidates ARE being materialized (n=22 per family, correct). The ΔU ranker runs and produces no positive-ΔU winner because q_lcb is calibration-suppressed to 0 on the decisive bin. The `capital_efficiency_lcb_ev` label (q_lcb <= price after S2's ProbabilityUncertainty derivation) is the honest gate firing on a genuine no-edge signal — whether it is "honest no edge" or "calibration over-suppression" is the open question in B2.
- B3 (BLIND EXIT): unrelated to candidate generation.

**Evidence on the "did S1-S7 / e583af06cd cause B2?" question**

S1 introduced ExecutableCostCurve depth-walked pricing. S2 introduced ProbabilityUncertainty-based q_lcb from per-bin YES probability samples. S3 introduced NativeSideCandidate materialization. S4 introduced the ΔU ranker as the sole selection gate. These collectively changed how q_lcb is computed and what the no-trade gate sees. The longshot q_lcb=0.0000 values are consistent with ProbabilityUncertainty q_lcb computation on bins with sparse bootstrap samples — a known risk of the S2 change. The regret evidence shows this is happening pervasively across cities (Milan, Paris, London, Munich all showing q_lcb=0.0000 on longshot bins with prices 0.001-0.005).

The S7 toggle removal cannot cause B2 — it only made the RECORDED decision more honest, not fewer decisions. The gate-mass collapse (e583af06cd) cannot cause B2 — it deleted only shadow/exit code with no effect on q_lcb computation or the ΔU ranker.

**Summary verdict**

b1825c4a07: removed only the `selector_enabled` toggle at `opportunity_book.py:to_receipt_dict`. Candidate materialization (`_generate_candidate_proofs`, NativeSideCandidate, ΔU ranker, `build_family_opportunity_book`) survived byte-identical. This was a correctness fix, not a regression.

e583af06cd: removed shadow modules and dead flags. Zero effect on the live selection path.

B2 (decision silence) is caused upstream of this angle: q_lcb collapses to 0.0000 on longshot YES bins under the S2 ProbabilityUncertainty construction, triggering the honest `capital_efficiency_lcb_ev` gate. Every active market IS emitting a full 22-candidate family through the materialization chain — nothing is silently dropped. The blocker is the probability calibration suppressing q_lcb below price, not any toggling or materialization fault introduced by S1-S7 or e583af06cd.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a4ec565462a146097`

### Final Assistant Message

I now have the complete picture. Here is the forensic analysis:

---

EDGE EXISTENCE + SETTLEMENT GRADING — DIFF FORENSIC ANALYSIS

ANGLE SUMMARY

This angle covers how Zeus grades its own edge: the seven files span the settlement truth function (graded_receipt), the skill-vs-luck classifier (settlement_skill_attribution), the shadow promotion gate (retired_comparison_tool), the deterministic and probabilistic promotion validators (deterministic_edge_report, promotion_readiness, live_readiness_tribunal), and the supporting benchmark scaffolding (benchmark_suite). Together they form the loop: "did we have edge? how do we prove it? when do we promote?"

---

GRADING INFRASTRUCTURE — SOUND, NOT THE BLOCKER

src/contracts/graded_receipt.py (created 2026-06-03, last audited 2026-06-03): the single settlement truth function is correct. Three antibodies (unit mismatch, BinKind dispatch, range containment) are structurally enforced; no string heuristics. Zero relevant churn since creation.

src/analysis/settlement_skill_attribution.py (created 2026-06-12, commit b694b3b45b): the skill-vs-luck grader correctly excludes LUCKY_WIN from the win numerator and STALE_DECISION from the denominator. The LARGE_FACTOR=2.0 threshold is data-anchored (lower edge of the observed 2.0-2.5x band). The market-was-right sign test (not a brittle 0.5 cutoff) is correct and catches the exact 3-loss shape. No base-rate illusion: it never counts a lucky win as skill evidence.

Live DB state (verified this session):

- settlement_attribution has 23 rows: SKILL_WIN=9, LUCKY_WIN=3, SKILL_LOSS=3, STALE_DECISION=8, MISCALIBRATED_LOSS=0
- counts_as_skill_win: 9 true, 14 false
- Skill denominator = 9+3+3+0 = 15; skill win-rate = 60.0%
- CRITICAL DATA GAP: q_live is NULL on all 30 edli_live_profit_audit filled rows (15/23 attribution rows have q_live=NULL and q_in_bin=NULL). The grader falls back to the decision-time posterior for market_q_ratio computation. market_q_ratio is NULL on 17/23 rows, meaning the MISCALIBRATED_LOSS discriminator cannot fire for most positions. The 3 confirmed June-12 losses that are the archetype for miscalibration are NOT yet present as MISCALIBRATED_LOSS rows (they read 0 MISCALIBRATED_LOSS). This is because their settlement has not yet been VERIFIED in forecasts.settlement_outcomes (still awaiting the grader's next run after verification). This is a latency issue, not a code defect.
- Source: settlement_attribution rows, both q_live columns NULL in edli_live_profit_audit confirmed by query.

---

SHADOW COMPARATOR — HONEST ABSENCE, NOT A BLOCKER

src/analysis/retired_comparison_tool.py (created 2026-06-09): the day0_remaining_day_adapter returns shadow_q=None for all cells because q_remaining_day is not yet persisted in any edli_no_submit_receipts row. The adapter honestly surfaces this as missing_shadow. The comparator therefore returns INSUFFICIENT_N for day0_remaining_day_q, which is the correct verdict given zero shadow-lane data. The default_registry() at line 664 registers exactly one candidate, which is correct (the gate was correctly left as the only live comparator, matching e583af06cd's removal of shadow strategies). No fabrication risk.

The comparator is a cron job at 09:20 daily (main.py:9504). It does not gate live submission. It does not cause B2. The absence of a PROMOTE_SUPPORTED verdict means the day0_remaining_day_q promotion remains stalled, which is an alpha-recovery issue, not the submission blocker.

---

b1825c4a07 — VERIFIED: TOGGLE REMOVED, MATERIALIZATION INTACT

Commit b1825c4a07 (2026-06-08) removed the `selector_enabled` cache-flag gate from events/opportunity_book.py and the dead `"selector_enabled": True` / `"selector_shadow"` keys from engine/event_reactor_adapter.py. The commit message states explicitly: "to_receipt_dict gated the recorded decision on selector_enabled = bool(cache_summary.get('selector_enabled')) and nulled selected_candidate_id whenever that cache flag was falsy/absent. That branch is DELETED — the receipt records the ΔU decision unconditionally." Files changed: src/engine/event_reactor_adapter.py, src/events/opportunity_book.py, tests/engine/test_s7_selector_gate_removed.py (net +513 lines). No materialization code was deleted — only the on/off gate that could silently null out the candidate. Materialization of candidates in the opportunity book survives intact. This commit is NOT causally linked to B1, B2, or B3 from the grading-angle perspective.

---

e583af06cd — CONFIRMED: SHADOW MODULES REMOVED, LIVE PATH BYTE-IDENTICAL

Commit e583af06cd (2026-06-13) removed six dead shadow strategy modules (exit_belief, exit_calibration_alarm, exit_policy, exit_policy_shadow, horse_race_kelly, james_stein_blend) and four flags. The commit message states "Byte-identical live path — all removed flags were config-False; shadow compute was telemetry-only." No live selection math was touched. The selection_eb EB-computation was retained with authority_on=False. This did not break the grading loop — graded_receipt, settlement_skill_attribution, retired_comparison_tool, and promotion_readiness are untouched in this commit. Not causally linked to B1/B2/B3 from the grading angle.

---

S1-S7 BIN-SELECTION CHURN — NOT A GRADING DEFECT

S1 through S7 (through b1825c4a07) collapsed the bin-selection path to one gate (marginal ΔU ranker). This does not affect settlement grading correctness. The grading files have no dependency on the selection strategy. Not causally linked to grading-angle blockers.

---

CONNECTION TO OBSERVED BLOCKERS

B1 (ws_gap submit latch): The grading layer has no role. The submit latch is in exchange_reconcile_findings. Not caused by any file in this angle.

B2 (capital_efficiency_lcb_ev rejection): The settlement grading machinery does not gate live submission. The rejection reason capital_efficiency_lcb_ev is the honest q_lcb>price check in the edge layer. From the grading angle's evidence: the day0 enrichment commit (2fb80ae739, 2026-06-13) revealed that the near-certain obs-resolved tail (cost ~0.96, n=427, 98.4% win, +2.4c/$1 after-cost) is being CORRECTLY identified as +EV by the settlement grader but INCORRECTLY rejected at the edge gate because q_lcb is suppressed below cost (day0_remaining_day_q off). This is not a grading defect — the grader correctly identifies the alpha. The defect is upstream (q_lcb construction), and the fix path (day0 shadow promotion) is stalled at INSUFFICIENT_N because q_remaining_day is never written into receipts (shadow lane dark). The grading layer is working correctly as a diagnostic, but its output is not plumbed back to the edge gate.

B3 (Beijing NO position, BELIEF_AUTHORITY_FAULT): Not grading-related.

---

DENOMINATOR AND SURVIVORSHIP ILLUSION AUDIT

benchmark_suite.py (created 2026-04-27, never audited post-June-05 churn): This is a LEGACY file. Its StrategyBenchmarkSuite, PromotionDecision, and associated machinery are NOT wired into the live engine (confirmed: no call site in src/main.py or src/engine/). It is a standalone evidence-only framework for testing. Its denominators are observation-level (sample_count, net_pnl means) with no survivorship illusion baked in. The ev_after_fees_slippage computation at line 436 is a simple fmean over net_pnl, which is correct but the observations come from caller-supplied corpora, not the live settled record. VERDICT: CURRENT_REUSABLE for its stated purpose (isolated test scaffolding), but STALE relative to the current grading architecture — it predates settlement_skill_attribution and has no concept of LUCKY_WIN exclusion.

promotion_readiness.py and live_readiness_tribunal.py (both created 2026-05-21): Also not called from live engine path. The tribunal adjudicates EvidenceReport objects that require n_settled>0 and ci_lower > breakeven. With 23 graded positions and a 60% skill win-rate, any strategy that uses the standard breakeven-based gate would produce ci_lower values that might or might not clear, depending on the breakeven. These files are structurally sound but produce HOLDs at current evidence volumes. Not causally linked to B1/B2/B3.

The shadow comparator is the closest to a live feedback mechanism but returns INSUFFICIENT_N and has no gate on submission.

---

EXPLICIT VERDICT ON CHURN CAUSATION

- b1825c4a07: Removed a selector ON/OFF toggle. Materialization was NOT removed. Not causally linked to decision silence.
- e583af06cd: Removed dead shadow modules. Live path byte-identical per commit evidence. Not causally linked to B1/B2/B3.
- S1-S7: Bin selection collapse. Does not touch grading, promotion, or submission gate. Not causally linked from this angle.

The grading angle is working correctly as a DIAGNOSTIC but is DECOUPLED from the submission gate. The edge-measurement system now has a valid skill win-rate (60%), a valid MISCALIBRATED_LOSS discriminator, and a valid shadow comparator framework — but none of this feeds back into q_lcb construction or the capital_efficiency gate. The loop from "grade your edge" to "adjust q_lcb to reflect that edge" is still open (day0_remaining_day_q shadow lane dark, INSUFFICIENT_N, day0 promo stalled).

---

## refuter:candidate=DEFECT (angle R1, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-a4ef0e3b29e55e87c`

### Final Assistant Message

ROOT_CAUSE confirmed, kill_found=false.

The defect survives all four kill vectors. Verified facts:

- Finding `5bbc2be2` has `resolved_at=NULL` confirmed by direct DB query against `zeus_trades.db`. The operator-ack path at `exchange_reconcile.py:1162-1164` dead-ends because no `session_operator_confirmed%` row exists.

- The task #31 settled-class absorber (lines 2432-2472) also fails to fire: it requires `calendar_terminal.get(token)` to be non-None, which needs the NO-side token to be bridged via `condition_id` from EMS. Neither EMS in `zeus_trades.db` nor `zeus-forecasts.db` returns a row for `no_token_id LIKE '2599807256%'` — the token is simply absent from the current EMS snapshot. The `_market_calendar_terminal_evidence` function fails closed (`return {}`), so `settled_terminal` is None, and execution falls through to the operator-ack branch, which also fails.

- The underlying cause is that the settled absorber's condition_id bridge assumes the token has a live EMS row, but a fully swept/redeemed market position is no longer in the active EMS. The token disappeared from EMS precisely because the market closed and was swept — the exact condition the absorber was built to handle.

- `list_unresolved_findings()` returns 1 row, `clear_after_m5_reconcile()` raises `WSGapSubmitBlocked`, `allow_submit=False` system-wide. Real edge candidates (KL 35C+, Tel Aviv 32C+) are blocked from reaching the venue.

---

## angle:Point-in-time data lineage / look-ahead leakage governance.

**Agent ID:** `agent-a4fc28243d3d6a7b6`

### Final Assistant Message

All queries are complete. Here is the full finding:

---

LEAKAGE AUDIT: sigma_scale_fit.json

ARTIFACT STATE

The live `state/sigma_scale_fit.json` is byte-identical to `state/sigma_scale_fit.candidate.json` (same md5: `77a5d8f9eaed0095be54fa014478795e`). The artifact carries `candidate: true` and `"OPERATOR_GATED"` promotion note but was renamed to the live path, presumably by operator action on Jun 13 at 14:36 CDT (19:36 UTC). It uses authority `sigma_shape_kernel_mixture_v1_mle` (the new kernel form replacing the uniform-pedestal live form).

FIT QUERY: NO DATE FILTER — STRUCTURAL LEAKAGE RISK

The fitter's SQL (`scripts/fit_sigma_scale.py`, `_FIT_QUERY`) is:

```sql
SELECT fp.city, fp.target_date, fp.source_cycle_time, fp.computed_at, fp.q_json,
       so.winning_bin, so.settlement_value, so.settlement_unit
FROM forecast_posteriors fp
JOIN settlement_outcomes so
  ON so.city=fp.city AND so.target_date=fp.target_date AND so.temperature_metric=fp.temperature_metric
WHERE fp.temperature_metric='high'
  AND so.authority='VERIFIED' AND so.winning_bin IS NOT NULL
```

There is no `WHERE fp.recorded_at < so.settled_at` guard, no `WHERE fp.source_available_at < so.settled_at` guard, and no `training_allowed=1` filter. All 2934 `forecast_posteriors` rows have `training_allowed=0`, but the fitter ignores this column entirely (zero references in the script).

POPULATION AND TIMING

The join produces 1,577 raw pairs over target_dates 2026-06-08..2026-06-12, deduplicating to 304 C-family cells and 69 F-family cells. Query: `SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM forecast_posteriors fp JOIN settlement_outcomes so ON ... WHERE authority='VERIFIED' AND winning_bin IS NOT NULL` — row count 1577.

Measured lead (source_available_at to settled_at across all 1577 pairs): min 2.11 days, max 7.12 days, mean 4.20 days. Query result: `2.11|7.12|4.2|1577`. Every forecast posterior used in the fit was available at least 2.1 days before the market settled.

The freshest posterior per cell (selected by `computed_at`) was last computed by 2026-06-12T05:06 UTC; settlements for this window arrived starting 2026-06-14T02:46 UTC. The `_build_cells` deduplication takes `MAX(computed_at)` per (city, target_date, bucket) — confirmed in `scripts/fit_sigma_scale.py` lines 363-368. None of those freshest posteriors were computed after their respective settlement.

TIMESTAMP INCONSISTENCY (non-leakage issue)

The artifact `created_at` field says 2026-06-13T17:06:47 UTC, but at that timestamp: (a) the `forecast_posteriors` table had 2,843 rows covering target_dates 06-08..06-15, (b) the `settlement_outcomes` table had no rows for those target_dates (max settled target_date was 2026-05-13, settlement records for 06-08..06-12 only arrived on 2026-06-14T02:46+ UTC), and (c) the join would have produced 0 rows. The 304 cells in the artifact are only achievable after 06-14 settlements arrived. The `created_at` timestamp in the JSON is therefore unreliable as a run-time anchor — it either came from a different failed run that day, or the field was set before the DB was populated and the file was later overwritten with the same timestamp. The artifact mtime (19:36 UTC on Jun 13) predates the settlements too, suggesting the file on disk represents a run from after 06-14, with timestamps set to Jun 13 values. This is a provenance integrity gap (the artifact's `created_at` does not match a plausible run time given DB contents), but it is not itself leakage.

VERDICT

LEAKAGE STATUS: CLEAN for the cells that entered the MLE. Every posterior in the fit preceded settlement by a minimum of 2.11 days, and no posterior's source_available_at exceeded its paired settlement's settled_at (0 of 1577 pairs violated this). The fitter's structural absence of a date guard is a latent risk but did not cause leakage in the current population because the `forecast_posteriors` table only contains recent replacement-chain posteriors (recorded starting 2026-06-07), which are all meaningfully prior to their settlements.

THREE ACTIVE RISKS to flag:

1. No `training_allowed=1` filter in the sigma fit query. The fitter is inconsistent with the rest of the calibration pipeline (which guards on `training_allowed=1`). If any posteriors are ever stamped `training_allowed=False` due to known data-quality issues, they will still flow into the MLE. Recommend adding `AND fp.training_allowed=1` to `_FIT_QUERY` in `scripts/fit_sigma_scale.py` (line 120).

2. No `recorded_at < settled_at` guard. The query is safe now because the replacement posteriors table is structurally prior to settlements, but if the table is ever backfilled with posteriors computed post-settlement (e.g., for holdout reporting or debugging), those rows would contaminate the fit. A guard `AND fp.recorded_at < so.settled_at` is cheap insurance.

3. Artifact `created_at` is unreliable. The timestamp in the JSON does not correspond to a plausible script-run time given the DB state, making the provenance hash and data_window label non-auditable without re-running the script. The artifact carries `candidate: true` but is operating as the live sigma artifact. Power is thin: 304 C-cells from 5 days of settlement data; the artifact itself notes the magnitude is non-stationary on this window ("Holdout shows the magnitude is non-stationary on 5 days").

---

## refuter:candidate=DEFECT (angle R15, tag SEMANTIC_CONFLICT, impact 70):

**Agent ID:** `agent-a50120c9c1233784a`

### Final Assistant Message

Now I have a complete picture. Let me analyze the key facts:

**Line 881 (the claimed defect):**
```python
if expected_sha and loaded_sha and loaded_sha != expected_sha:
```

When `_BOOT_STATE['sha']` is None (ZEUS_ACCEPT_STALE_DEPLOY=1 and git fails), `expected_sha` is `""` (falsy), so the mismatch branch is skipped — the SHA gate is bypassed.

**But look at line 883:**
```python
if not loaded_sha:
    return ["EDLI_STAGE_LOADED_SHA_MISSING_VALUE"]
```

And look at `_write_loaded_sha_state` (line 4442-4447): when `boot_sha` is None, it **skips writing** `state/loaded_sha.json`. So the file won't exist, and line 873-874 will return `EDLI_STAGE_LOADED_SHA_MISSING:...`, causing a FAIL.

The gate actually **does not collapse** under ZEUS_ACCEPT_STALE_DEPLOY=1 — it fails closed in a different way: the loaded_sha.json file never gets written, so `_edli_stage_loaded_sha_reasons` returns a MISSING error, which means `reasons` is non-empty, which means `live_entries_allowed=False` at line 722. The SHA mismatch check is bypassed, but the MISSING file check catches it.

The claim that "the EDLI guard collapses to dead letter" is incorrect — it actually hard-blocks entry (FAIL status, not PASS-with-bypass).

The "placeholder 'abc123' passes silently" scenario: `loaded_sha.json` won't exist at all under ZEUS_ACCEPT_STALE_DEPLOY=1 because `_write_loaded_sha_state` returns early when `boot_sha` is falsy. A placeholder 'abc123' would require someone to manually write a fake file AND set the env var, which is deliberate circumvention, not a silent bug.

---

## angle:Point-in-time data lineage / look-ahead leakage governance.

**Agent ID:** `agent-a53189d252cae540d`

### Final Assistant Message

## Point-in-Time Data Lineage: Design from First Principles

---

### 1. Objective

Every calibrated probability the system produces must answer one question truthfully: "Given only what I could have known at the moment I am placing this order, what is the probability this bin settles YES?" If any label, weight, or parameter reflects information that will only exist after the decision is made, the probability is a hallucination. A hallucinated probability cannot generate edge; it generates phantom edge that evaporates on live execution and accumulates as slow bleed.

The objective is therefore: **guarantee that the function mapping (city, date, bin, decision_timestamp) → calibrated_q is a strict function of the information set available at decision_timestamp, with zero contamination from later-realized outcomes.**

---

### 2. The Ideal Mechanism

**Immutable labeled dataset with settlement lag enforcement.**

Every settled outcome record carries three timestamps: the settlement reference date D, the date the official station reading was published (publication_ts), and the date the record was ingested into the system (ingest_ts). The usable label timestamp is max(publication_ts, ingest_ts).

A calibration run or model fit issued at decision time T draws only records where usable_label_ts < T. This is not a filter applied by the analyst — it is a read interface that physically cannot return newer records. The dataset is append-only. Rows are inserted only when ingest_ts is stamped by the ingestion service at ingest time; they are never back-filled with earlier timestamps.

Forecast snapshots are stored analogously: each NWP grid pull is stamped with the time the model run was retrieved and the nominal model valid time. Training a sigma-shape or model weight at time T uses only forecast snapshot records with retrieval_ts < T, matched against settled labels with usable_label_ts < T on the same (city, date, bin) key.

The q-lcb cap (the lower confidence bound on the probability, used as the execution threshold) is derived from a calibration dataset that is itself a strict function of T. The cap is re-derived at the start of each trading session, not cached from a prior session whose dataset boundary may differ.

---

### 3. Invariants a Correct Implementation Must Hold

- **I1 (Append-only labels):** The settled outcomes table is insert-only. No UPDATE, no DELETE, no back-dating. The usable_label_ts for any row equals the wall-clock time at first insert.
- **I2 (Read-interface enforcement):** All consumers of settled history (calibrators, backtests, weight fitters) call a single read function that accepts T and returns only records with usable_label_ts < T. The raw table is not accessible to consumers.
- **I3 (Forecast snapshot lineage):** Model weights trained on forecast accuracy use (forecast_retrieval_ts, label_ts) pairs both strictly less than T.
- **I4 (No global mutable state):** No shared in-memory cache of calibration parameters is mutated mid-session without a new T-bounded dataset derivation.
- **I5 (Reproducibility):** Given the same T and the same immutable table state, any calibration run returns the same output. This makes leakage auditable.

---

### 4. Silent Failure Modes

- **Publication lag ignored:** Settlement values are scraped before official publication is confirmed. The label appears to be "from yesterday" but was not publicly available until this morning after market open. Any model trained on it has look-ahead into yesterday's closing prices.
- **Rolling window without lag:** A 90-day rolling calibration window assumes all 90 days are available; in practice, the most recent 2-3 days have unconfirmed or delayed station readings. These soft labels bias the calibration toward whatever the current market implied.
- **Backtest uses live weights:** A backtest run at T uses model weights that were actually fitted on data through T+30. The backtest shows edge; live execution does not.
- **Shared calibration cache:** Two trading sessions share a cached calibration object fitted at T1. Session two runs at T2 > T1 but uses T1's object. New settled data between T1 and T2 is excluded, reducing sample size and — worse — the system does not know it is working from a stale information set.

---

### 5. Three Hardest Design Decisions

**Decision 1: Where does the "usable" timestamp live?**
The publication_ts is authoritative but may be unknown or unreliable for many weather stations. The conservative call: **usable_label_ts = ingest_ts always**, regardless of what the station claims about publication. This wastes one ingestion latency cycle of data but eliminates any trust dependency on external timestamp integrity. Precision lost is smaller than the risk of contamination.

**Decision 2: Should calibration re-run continuously or epoch-gated?**
Continuous re-calibration sounds more responsive but creates a moving T that is hard to audit. The call: **epoch-gated at session open**, with T pinned to the session's start timestamp. Every probability produced in that session is derived from the same frozen dataset. Auditability and reproducibility outweigh marginal calibration freshness within a session.

**Decision 3: Forecast snapshot granularity.**
NWP model runs are issued every 6-12 hours; the question is whether to store full grid snapshots or derived per-city summaries. Full grids are expensive; summaries are lossy and irreversible. The call: **store per-city ensemble summaries with the full retrieval_ts and model_run_ts**, not raw grids. The summary must be sufficient to reproduce the probability estimate for that city-date-bin; if it is not, the system cannot audit its own past decisions, which is the same failure mode as leakage.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 99):

**Agent ID:** `agent-a542e6e44dd1e7f2e`

### Final Assistant Message

Now I see the critical code path: when `_discovery_gates_allow_entries()` returns False (due to ws_gap allow_submit=False), `_execute_discovery_phase` is skipped entirely. This means the reactor doesn't run entries, but I need to check whether this also prevents the reactor from writing decision_certificates for rejections.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a54dcbaa83b865ea1`

### Final Assistant Message

I now have enough data to provide the full report.

---

MECHANISM (1): What this angle actually does

benchmark_suite.py (src/strategy/benchmark_suite.py, created 2026-04-27): A framework for evaluating strategies through replay, simulated-venue, read-only-live, and promotion-grade-economics evidence corpora. Produces StrategyMetrics and a PromotionDecision (PROMOTE/BLOCK). It is NOT wired into the live order-submission path. It records into the strategy_benchmark_runs table as an advisory artifact only.

settlement_skill_attribution.py (src/analysis/settlement_skill_attribution.py, created 2026-06-12): Grades every FILLED and VERIFIED-settled position into five categories (SKILL_WIN, LUCKY_WIN, SKILL_LOSS, MISCALIBRATED_LOSS, STALE_DECISION) by comparing the position's held-token q, the freshest settlement-eve posterior, and the Direction Law. Runs as a cron job in main.py at 09:30 UTC daily. Read-only over graded/forecast tables; sole writer of settlement_attribution table. Does NOT gate or block submissions.

retired_comparison_tool.py (src/analysis/retired_comparison_tool.py, created 2026-06-09): The standing shadow-vs-live promotion comparator. Reads persisted shadow q values from edli_no_submit_receipts, pairs them with VERIFIED settlements, scores both sides with log-loss and Brier, and emits PROMOTE_SUPPORTED / LIVE_BETTER / INSUFFICIENT_N via a bootstrap CI. Advisory only. Does NOT block submissions.

deterministic_edge_report.py (src/analysis/deterministic_edge_report.py, created 2026-05-22): Verifies deterministic strategy promotions via payoff-identity reconciliation (Clause 1) and aggregate profitability (Clause 2). Read-only advisory; never writes a tier or calls adjudicate(). Not wired into the live path.

promotion_readiness.py + live_readiness_tribunal.py (both 2026-05-21/22): PromotionReadinessValidator composes three signals (CI gate, tribunal predicate, settlement coherence gate). adjudicate() in live_readiness_tribunal.py writes to evidence_tier_assignments on PROMOTE/DEMOTE and requires a caller-supplied conn and operator_ref for live tiers. Used by promotion_readiness_job.py (CLI-only, not a daemon) and shadow_replay_harness.py (backtest only). Not wired into the live submission daemon.

graded_receipt.py (src/contracts/graded_receipt.py, created 2026-06-03): The single truth function for win/loss grading. Used by all attribution and comparison files. Correct, unit-safe, BinKind-aware. Not a gate for submissions.

Bottom line on coupling: none of these files gate order submission. The evidence angle is advisory infrastructure only. A BLOCK from benchmark_suite, a NOT_READY from promotion_readiness, or an INSUFFICIENT_N from retired_comparison_tool does not suppress an order.

---

DEFECTS (2)

Finding 2-A: fresher_cycle_existed_at_decision is systematically wrong — compares settlement-eve vs decision-time, not the next cycle vs decision-time.

File: src/analysis/settlement_skill_attribution.py lines 717-722.

The code reads the FRESHEST (latest overall) posterior as `fresh` and the DECISION-TIME posterior (latest at <= created_at) as `decision_post`, then sets `fresher_existed = d_fresh > d_dec`. This correctly detects that a later cycle existed, but it uses the settlement-eve posterior as the reference point rather than the STRICTLY-NEXT 6-hourly cycle after decision time. Consequence: if the settlement-eve posterior and the decision-time posterior happen to be the same cycle (the family's last update was at decision time), `d_fresh > d_dec` is False and the position is not flagged STALE even if a fresher intermediate cycle DID exist between the decision and settlement. This silently under-brands STALE_DECISION rows, inflating the skill denominator with positions where the decision may have consumed stale data. The fix requires querying for any posterior with `computed_at > created_at AND computed_at < settlement_eve`, not just comparing the two bookend cycles. Does not block submission. Does kill accurate skill accounting.

Recipe: have a position whose latest posterior overall equals the decision-time posterior (no posterior was run after the decision); the code sets fresher_existed=False and does not run a check for intermediate cycles that DID exist between the decision and the settlement-eve.

Finding 2-B: q_live is NULL on all current edli_live_profit_audit rows; the fallback to decision_q_in_bin silently loses the actual fill-row q.

File: src/analysis/settlement_skill_attribution.py lines 289-295 (grade_position), and comment on line 285: "q_live is NULL on every live profit-audit row today (data-provenance gap: the executor does not persist q_live on the projection)."

The grader correctly falls back to decision_q_in_bin from the posterior, but this means skill grading is done against the posterior's in-bin mass rather than the actual captured fill-row q_live. The two can diverge (the posterior is a full-bin probability; the fill-row q_live is the direction-adjusted signal the executor used). The market_q_ratio (market_in_bin / q_in_bin) is therefore computed using the posterior's probability rather than the actual execution belief, which could misclassify some SKILL_LOSS rows as MISCALIBRATED_LOSS or vice versa. DEFECT_NO_CURRENT_IMPACT on submission; affects skill accounting accuracy.

Finding 2-C: benchmark_suite simulated venue produces hardcoded synthetic observations with no connection to real market data.

File: src/strategy/benchmark_suite.py lines 497-521 (_simulated_venue_observations).

All alpha_pnl, spread_pnl, fees, slippage, fill_probability, adverse_selection_bps, calibrated_probability, market_implied_probability are magic constants (0.01, 0.005, 0.001, 0.001, 1.0/0.0, 5.0/50.0, 0.55/0.45, 0.50). The calibration_error_vs_market_implied will always be 0.05 for confirmed trades (|0.55 - 0.50| = 0.05) and 0.05 for failed trades (|0.45 - 0.50| = 0.05). The EV calculation using these constants is not derived from any real strategy economics. This means any strategy evaluated via evaluate_simulated_venue() that has at least one CONFIRMED fake trade will always pass the ev_after_fees_slippage > 0 threshold (synthetic alpha_pnl - fees - slippage = 0.01 * size - 0.001 * size - 0.001 * size = 0.008 * size > 0). This is a survivorship illusion baked into the simulated-venue lane: the benchmark passes any strategy with fake trades because the fake PnL is always positive. DEFECT_NO_CURRENT_IMPACT on submission (benchmark_suite is not a submission gate); if this were ever wired in, it would trivially pass everything.

Finding 2-D: promotion_decision() requires economics evidence or immediately BLOCKs, but economics is an optional parameter that defaults to None.

File: src/strategy/benchmark_suite.py lines 298-300.

When economics=None (the default), the method always appends "promotion_grade_economics: missing promotion-grade economics evidence" to the reasons list and sets verdict=BLOCK regardless of how good the other three signals are. This is by design per the docstring, but it means that calling promotion_decision() without the economics argument returns BLOCK unconditionally — which is easy to do accidentally in a harness that only supplies replay, simulated_venue, and read_only_live metrics. DEFECT_NO_CURRENT_IMPACT on submission.

Finding 2-E: evidence_report.py win-rate denominator (n_settled) counts ALL settled regret rows, but n_decisions counts ALL decision_events rows — the two populations are not guaranteed to overlap.

File: src/analysis/evidence_report.py lines 237-281.

n_decisions is the count of decision_events filtered by strategy_key (and optionally source). n_settled is the count of regret_decompositions rows joined through shadow_experiments, with the join through decision_events applied ONLY when decision_events is present. The CI is computed with n_wins as the numerator and n_settled as the denominator, but n_decisions is NOT the denominator. This means the CI is win-rate-of-settled-positions, not win-rate-of-all-decisions. Unsettled decisions (pending outcomes) are excluded from the CI denominator entirely. This is a deliberate design choice (the docstring acknowledges it), but it creates a survivorship bias in the CI: if settled positions have higher EV than unsettled ones (which is common when high-conviction trades settle faster), the CI will be inflated. For promotion decisions this creates a false optimistic signal. Does not block submission; can cause premature promotion into live tiers if left unaddressed. DEFECT that kills sound +EV evaluation.

Finding 2-F: retired_comparison_tool's fresher_existed computation mirrors the same structural defect as Finding 2-A.

File: src/analysis/settlement_skill_attribution.py lines 715-722 (same block). The comment on the comparison is: "A strictly-fresher cycle existed at decision iff the family's latest posterior is newer than the one the decision consumed." This logic conflates "latest overall" with "strictly next after decision" — they are the same only when there is exactly one posterior after the decision. DEFECT_NO_CURRENT_IMPACT on submission.

Finding 2-G: retired_comparison_tool day0_remaining_day_adapter yields zero shadow cells because no q_remaining_day field is persisted.

File: src/analysis/retired_comparison_tool.py lines 522-595, comment at lines 523-527.

The adapter's own docstring states: "HONEST ABSENCE (current state, 2026-06-09): live WORLD DB has 3,063 day0 events and ZERO remaining_day-tagged events — the shadow q is not persisted anywhere yet." This is an acknowledged gap. Shadow_comparator always returns INSUFFICIENT_N for the day0_remaining_day_q candidate. The fix (dual-persist q_remaining_day into the receipt alongside q_live) is described but not implemented. This means the shadow lane for the only registered candidate has been producing zero evidence since at least 2026-06-09 and produces no promotion signal. Does not block submission. Does mean the shadow evidence base is empty.

Finding 2-H: The _blocking_reasons check in benchmark_suite uses `<= min_ev_after_fees_slippage` (i.e., blocks on zero EV) but the semantic intent is to require STRICTLY positive EV.

File: src/strategy/benchmark_suite.py line 464.

`if metrics.ev_after_fees_slippage <= threshold.min_ev_after_fees_slippage` where the default threshold is 0.0. This means a strategy with exactly 0.0 EV (breakeven) is blocked — which is the intended behavior. However the condition is `<=` not `<`, so the comment "non-positive EV" is accurate. No bug here. DEFECT_NO_CURRENT_IMPACT.

---

ABSENCES (3) vs what a sound system needs

The evidence angle is notably absent these sound-system properties:

1. No feedback from evidence layer to order gate. The entire edge-evaluation infrastructure (benchmark_suite, skill_attribution, retired_comparison_tool, promotion_readiness) is advisory-only. There is no mechanism by which a degrading skill win-rate, an INSUFFICIENT_N verdict, or a NOT_READY assessment suppresses new orders. A miscalibrated strategy continues to trade indefinitely regardless of graded evidence.

2. No real-time q_live capture on fills. The edli_live_profit_audit.q_live column exists in the schema but is documented as NULL on every live row (Finding 2-B comment in settlement_skill_attribution.py line 285). The skill grader falls back to the posterior's q_in_bin instead. A sound system would persist q_live at the moment of submission so post-settlement skill attribution uses the exact belief that drove the trade.

3. No intermediate cycle STALE detection. The fresher_cycle_existed_at_decision logic checks only the bookend posteriors (decision-time vs settlement-eve). A sound system would query for any posterior cycle with computed_at strictly between decision_time and the next 6h boundary to correctly brand STALE_DECISION.

4. n_settled survivorship in the CI denominator. The promotion CI is computed over settled positions only, not all decision_events. A sound system that cares about calibration over all decisions (including those still pending) would either use the full denominator or explicitly model the settlement-time selection effect.

5. benchmark_suite simulated-venue lane uses constants, not real market distributions. A sound simulated-venue benchmark would use actual bid-ask data, fee schedules, and fill-probability priors derived from the venue, not global 1%-spread, 0.1%-fee phantoms.

6. The retired_comparison_tool's only registered candidate (day0_remaining_day_q) has zero evidence cells. A standing comparator with an empty registry is not generating any signal.

---

PROVENANCE VERDICTS (4)

src/strategy/benchmark_suite.py: STALE_REWRITE. Created 2026-04-27, last audited 2026-04-27. Authority basis is A1.yaml from April. The simulated-venue lane uses hardcoded phantom constants (Finding 2-C), and there is no evidence it has been updated to reflect the current q-construction, fee model, or the operator's law against magic numbers (operator law 2026-06-12). Not in the submission path, so not blocking, but would fail a currency audit.

src/analysis/settlement_skill_attribution.py: CURRENT_REUSABLE (with caveats). Created 2026-06-12, audited 2026-06-12. Authority basis is the 06-12 operator law against lucky-win accounting and the grade_receipt spine. Logic is sound and consistent with current Direction Law. The two caveats (Findings 2-A on fresher_cycle and 2-B on q_live NULL) are acknowledged in the source. Caveats reduce accuracy of skill accounting but do not create a structural violation.

src/analysis/retired_comparison_tool.py: CURRENT_REUSABLE (with a functional gap). Created 2026-06-09. Architecture and statistical approach are sound; one-builder law and grade_receipt spine are respected. The day0 adapter's shadow lane produces zero cells (Finding 2-G, acknowledged in the code). Structurally correct; functionally empty for its primary customer.

src/analysis/deterministic_edge_report.py: CURRENT_REUSABLE. Created 2026-05-22. Authority basis matches §16 of the STRATEGY_TAXONOMY_DIRECTIVE which is the standing law for deterministic strategies. No magic-number issues; both clauses are structurally sound. Not wired into the live path.

src/analysis/promotion_readiness.py: CURRENT_REUSABLE. Created 2026-05-22. Correctly delegates to promotion_predicate() from live_readiness_tribunal; no divergence possible (Fitz #4 fix is in place). Operator-ref guard is correct.

src/analysis/live_readiness_tribunal.py: CURRENT_REUSABLE. Created 2026-05-21. promotion_predicate() is the single canonical gate; adjudicate() correctly guards operator_ref for live-tier promotes and requires caller-supplied conn (INV-37 compliant).

src/contracts/graded_receipt.py: CURRENT_REUSABLE. Created 2026-06-03. The D1 keystone. Three antibodies (UNIT, BINKIND, MEMBERSHIP) are structurally correct and compose existing verified primitives. This is the single truth function and it is sound.

src/analysis/evidence_report.py: CURRENT_REUSABLE (with known survivorship bias). Created 2026-05-21, last audited 2026-05-23. The n_settled-denominator CI is a deliberate design decision (documented). The quarantine-exclusion logic and source-scoping are correct. The survivorship bias (Finding 2-E) is a systemic limitation of the design choice, not a bug introduced after the last audit.

---

## angle:Latency / alpha decay / market-lifecycle timing.

**Agent ID:** `agent-a55db53e346a0256d`

### Final Assistant Message

## Latency / Alpha-Decay Angle: First-Principles Design

---

### 1. Objective

Capture the price dislocation created when a new forecast update shifts the true probability distribution before the book reprices. The market's executable price at time T reflects the consensus of stale information; the edge window is the interval between the forecast update landing and the book converging. The angle makes money by being the first agent to act on the new information, sizing correctly, and being out of the information race before the edge evaporates. The objective is not speed for its own sake — it is calibrated first-mover advantage: edge captured / edge available > some minimum threshold, consistently positive net of all transaction costs.

---

### 2. Ideal Mechanism

**Signal pipeline.**
Each forecast provider publishes a model run at known wall-clock times (00Z, 06Z, 12Z, 18Z cycles; regional models on offset schedules). For each city/date pair, extract the raw ensemble distribution over temperature bins — not the point estimate. From that distribution derive, for every bin B, a probability lower confidence bound (q_LCB): the 5th percentile of the bootstrap distribution over ensemble members. q_LCB for YES contracts, (1 - q_LCB) for NO. This bound is deliberately conservative: it must survive model error.

**Cost-adjusted threshold.**
An order is actionable when q_LCB > ask_price + round_trip_cost + slippage_reserve. The cost stack is: maker or taker fee (both sides) + half the bid-ask spread on entry + expected adverse selection on exit (estimate from historical book depth decay). Call this floor C. Signal condition: q_LCB - best_executable_price > C. If not satisfied, no trade.

**Latency budget derivation.**
Empirically measure the half-life of price dislocation after each forecast cycle lands: fit an exponential to the spread of (mid_price - new_model_probability) over time. A 15-minute forecast cycle on a liquid-enough market typically shows a half-life of 5-25 minutes depending on time-to-settlement. The budget is: forecast_parse_time + decision_compute + order_transmission < 0.5 * half_life. Target: end-to-end under 90 seconds for a half-life of 10 minutes. Above that threshold, expected slippage from adverse selection exceeds expected edge.

**Market lifecycle windows.**
Three distinct regimes:
- **Early day (>6 hours to settlement):** Book is wide, ensemble spread is large, q_LCB is low. Only act on extreme dislocations (high-confidence temperature regimes: heat wave, freeze). Maker orders preferred — rest at fair value, let the market come to you.
- **Mid-day (2-6 hours):** Regional models have updated; observational data starts constraining. Edge sharpens. Aggressive taker orders justified when edge exceeds taker fee threshold.
- **Late day (<2 hours):** Forecast uncertainty collapses but so does liquidity. Any remaining dislocation is likely an artifact of a stale resting order. Sweep thin books only when cost-adjusted q_LCB is extreme and settlement risk is nearly zero.

**Staleness kill switch.**
Any signal older than max(2 * half_life, 30 minutes) is discarded, not adjusted. Do not decay a signal — kill it.

---

### 3. Invariants

- I1: Every actionable signal must have a timestamp-verified forecast ingestion time; wall-clock age of signal is computed at order submission, not at signal creation.
- I2: q_LCB is computed from the raw ensemble, never from the point forecast.
- I3: The cost floor C is recalculated live from current book state at decision time, not from historical averages.
- I4: No order is submitted on a signal whose ingestion age exceeds the staleness threshold.
- I5: Position limits are set per-city, not per-contract — correlated bins in the same city settle against the same temperature reading.

---

### 4. Silent Failure Modes

- **Stale signal poisoning:** acting on a signal from the previous forecast cycle after the new cycle has landed but before your pipeline has processed it. The book has already repriced; you buy what the market just sold.
- **Point-forecast collapse:** using ensemble mean rather than lower-confidence bound causes overconfidence; you trade at prices that only beat the mean, not the tail.
- **Cost underestimation:** using fee-schedule cost only, ignoring adverse selection. In thin books near settlement, the taker premium is 2-5x the posted fee.
- **Cycle aliasing:** two forecast providers report at similar times; pipeline ingests the slower one as "new signal" when the book already reflected the faster one.
- **Late-day liquidity mirage:** a large resting order on a thin book makes the price look tradeable; the order is canceled on partial fill, leaving you with adverse inventory at settlement.

---

### 5. Three Hardest Design Decisions

**Decision 1: Maker vs. taker.**
Maker saves the spread but introduces execution risk — the book moves and you're filled adversarially or not at all. Taker guarantees fill but costs more. Opinionated call: use taker for signals within the first 30% of their half-life, maker only if q_LCB - fair_value exceeds 2× round-trip cost and time-to-settlement > 4 hours. Never use maker in the final 2-hour window.

**Decision 2: Single-model or ensemble-of-ensembles signal.**
Running every provider independently creates signal redundancy and correlation noise. Opinionated call: weight providers by their recent calibration error (Brier score over last 30 settled markets, per city). Combine only providers whose weights exceed 0.10; discard the rest. Re-weight weekly, not daily — daily re-weighting overfits intraday noise.

**Decision 3: When is a signal "too late"?**
The staleness threshold could be a hard cutoff or a cost-adjusted decay. Opinionated call: hard cutoff, not decay. A decayed signal means you're submitting an order whose expected value you cannot tightly bound. The system's edge comes from high-confidence moments, not from squeezing marginal plays. The cost of one bad fill on a decayed signal exceeds the benefit of five marginal plays. Kill old signals; wait for the next cycle.

---

## refuter:candidate=DEFECT (angle R1, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-a573e679c2294509a`

### Final Assistant Message

The repro is unambiguously zero. Now I have all the facts to reach a verdict.

Summary of findings:

1. **The code claim is accurate**: `_parse_and_persist_weather_events` at line 1033 drops events with non-MATCH `source_contract.status` using only `logger.warning`, no ERROR, no counter. The dead constant `SOURCE_CONTRACT_ALERT_STATUSES` (line 333) is never consumed anywhere. This is a real observability gap in the code structure.

2. **There is a second, earlier filter**: `_parse_event` at line 1861 already returns `None` for AMBIGUOUS/MISMATCH/UNSUPPORTED, so the outer guard at 1033 only catches the `MISSING` status (no resolutionSource at all). Both paths are WARNING-only.

3. **The empirical claim is not active**: The repro returns 0 — zero "Skipping Gamma market" log lines since 2026-06-04. The drop path is not firing in the current live period.

4. **The Stage 3 evidence (zero opportunity rows) was a DB error**: The WITNESS queried zeus-world.db for opportunity_fact, but it lives in zeus_trades.db where it has 38,555 rows. opportunity_events (zeus-world.db) is alive with fresh rows through 2026-06-14.

5. **Would fixing it move a real order?**: No. The non-MATCH drop path is currently silent because it's not being triggered at all (0 log lines). The system's current market inventory comes from markets where `_check_source_contract` returns MATCH. Adding an ERROR counter to a code path that isn't firing produces no change in order flow.

The defect is real as a code-quality/observability matter, but the empirical premise ("a silent market vanishing is consistent with this path" + "zero rows") is undermined by: (a) the repro produces 0 hits confirming the path is not active, (b) the empirical "zero rows" evidence was based on querying the wrong DB.

---

## refuter:candidate=DEFECT (angle R3, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-a582e4dc42a1bab91`

### Final Assistant Message

Now I have all the data I need. Let me assess the kill criteria:

**Summary of findings:**

1. **Repro confirmed**: `buy_no_scaling_factor()` returns 1.5 (fallback), not 0.5 (configured). This is a real code-verifiable fact.

2. **Divergent values**: `buy_no_scaling_factor` 0.5→1.5 (3x), `buy_yes_scaling_factor` 0.3→1.0 (3.3x), `near_settlement_hours` 4.0→48.0 (12x), `buy_no_floor` -0.02→-0.03 (minor), `buy_yes_floor` -0.01→-0.02 (minor). `buy_no_ceiling`, `consecutive_confirmations`, divergence thresholds: all identical between configured and fallback.

3. **Scope is EXIT only**: `buy_no_edge_threshold()` and `buy_yes_edge_threshold()` are called exclusively within `ExitContext` methods (`portfolio.py:1241,1398`). These govern when to exit existing positions, not whether to ENTER or submit new orders.

4. **Kill criterion (d)**: The active blocker preventing fills is **B1** (submit gate frozen by phantom ws_gap finding) and **B2** (decision gate rejecting via `capital_efficiency_lcb_ev`). Neither is controlled by exit threshold parameters. No pending entry order is blocked by the expiry of these exit assumptions. The fallback exit thresholds only govern position-holding decisions for the one open Beijing NO position (B3).

5. **`near_settlement_hours` impact**: The 12x difference (48h vs 4h) means the system would hold positions for 44 extra hours before triggering near-settlement exit logic — but this affects only exits from existing positions, not new order entry.

6. **The claim "entry/exit decisions"**: The defect description overstates scope. These are exit-only parameters. Entry decisions flow through `capital_efficiency_lcb_ev`, edge, calibration — none of which use ExpiringAssumption values.

7. **Governance alarm absence**: True — no alarm fires. But this is a real issue only insofar as exit behavior is meaningfully affected. With only one live open position (Beijing NO, B3), the practical impact is confined.

**Kill verdict**: The defect is real (code confirmed, values diverge materially for 3 of 5 divergent parameters), the governance mechanism silently reverted as claimed, and there's no existing code handling it. However, criterion (d) kills it as a ROOT_CAUSE or anything that would move a real fill: the active fill-blocking is B1 (submit gate phantom latch) and B2 (entry edge gate). The expiring assumption fallbacks govern exit thresholds only, and the only live position exposed is B3 (Beijing NO). Fixing ExpiringAssumption would not move any new order toward a fill — it would only affect exit timing on the one open position. This is an EDGE_DEFECT: real, confirmed, materially wrong on some values (especially `near_settlement_hours` 48h vs 4h), but cosmetic relative to the order-flow blocker.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 75):

**Agent ID:** `agent-a591c6a8823199635`

### Final Assistant Message

The evidence is clear. The warm materializer at `market_scanner.py:4015` explicitly iterates `for direction in ("buy_yes", "buy_no")` and at line 4124 constructs a `decision` with `edge.direction=direction` for each iteration. This creates separate EMS rows with `outcome_label='NO'` for buy_no and `outcome_label='YES'` for buy_yes.

Now check `_snapshot_rows_by_condition_and_direction` logic again with this in mind:

At `event_reactor_adapter.py:12865-12868`:
- If `selected_token_id` is set and doesn't match token_id (the iterated token) → skip
- If `selected_label` is set and doesn't match `label` ("YES" or "NO") → skip

For a NO-labeled row (outcome_label='NO', selected_outcome_token_id=no_token_id):
- When iterating the `(yes_token_id, "YES", "buy_yes")` tuple: `selected_label="NO"` != `label="YES"` → skipped
- When iterating the `(no_token_id, "NO", "buy_no")` tuple: both match → added to `out[(condition_id, "buy_no")]`

So a NO-row correctly maps to `buy_no`. The claim is REFUTED: the code DOES store NO-labeled rows (via the `for direction in ("buy_yes", "buy_no")` loop in the warm materializer), and `_snapshot_rows_by_condition_and_direction` correctly routes them to the `buy_no` direction key.

The "all 10 sampled rows have outcome_label=YES" empirical claim in the evidence is consistent with a DB query at a time when only YES-direction captures had completed, or is a survivorship/sampling artifact — NOT proof that NO rows are never stored. The code path to store them is present and active.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a5d1aed1c7667ad8f`

### Final Assistant Message

The opportunity_fact reads in db.py:10544 and decision_integrity_quarantine.py are in reporting/observability functions, not the execution decision path. The writes for these tables are telemetry, not gates.

Now let me synthesize the kill:

**Summary of findings:**

**Axis (a) - Naive about real constraint?** No kill available here. The ideal (Stage 3 logs candidates) is a legitimate observability expectation.

**Axis (b) - Code already handles it?** PARTIAL KILL: The candidate's specific mechanism (`log_opportunity_fact` skipping due to wrong DB/ATTACH) was ALREADY FIXED on 2026-05-18 (db.py:7932 wave-2 INV-37 fix) — `log_opportunity_fact` now opens its own verified trade connection regardless of what `conn` is passed. The "silent skip on missing table" path at line 7937 cannot fire because `opportunity_fact` table EXISTS in zeus_trades.db (38,555 rows in it). So the specific DB-routing skip mechanism the candidate hypothesizes does not explain the gap.

**Axis (c) - Empirical claim survivorship-biased?** PARTIAL KILL: The candidate states "since 06-04" but the actual cutoff is May 28, predating S7 (Jun 8) and the "b1825c4a07 deleted the opportunity-book selector" commit. The timing attribution in the evidence is wrong. Further: the candidate claims `selection_family_fact = 0` — it's 0 in zeus-world.db but 412 rows in zeus_trades.db (up to May 28). Also: opportunity_fact is 38,555 rows in zeus_trades.db — not zero as implied.

**Axis (d) - Would fixing it move a real order?** KILL: The opportunity_fact/selection_family_fact/decision_events tables are TELEMETRY and OBSERVABILITY tables. They are not read by the execution gate path — nothing in cycle_runtime.py, the submit lane, or RiskGuard reads opportunity_fact before deciding to submit. Fixing their write path would improve observability/debugging but would NOT unblock the B1/B2 blockers preventing fills. The real blockers are B1 (ws-gap latch on phantom finding 5bbc2be2) and B2 (capital_efficiency_lcb_ev rejection). The blank of Stage 3 telemetry is a symptom of the same underlying cause blocking orders (system not reaching submission), not a cause itself.

The correct framing is: Stage 3 logs are dark because the pipeline isn't generating orders, not vice versa. The candidate inverts cause and effect and attributes the wrong mechanism (DB-routing skip) to a gap that has a different and earlier origin.

---

## refuter:candidate=DEFECT (angle R4, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-a5f85d7aa3a940663`

### Final Assistant Message

I have enough evidence to render the verdict.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-a603c27da713a5969`

### Final Assistant Message

I need to verify a few code details before finalizing the report. Let me check the adapter's live admission assert and the opportunity book selector context since that's the current branch name.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 75):

**Agent ID:** `agent-a60d1ea944d132852`

### Final Assistant Message

The interleave logic explicitly pairs slots 0+1 (which would be buy_yes and buy_no for the same condition) to avoid the budget cutting off the NO side. This is a direct defense against the budget-starvation variant of the claimed defect.

The defect is **refuted**: the materializer captures both YES and NO labeled rows (`market_scanner.py:4015` iterates both directions; `market_scanner.py:2749` stores `outcome_label="NO"` for buy_no), and `_snapshot_rows_by_condition_and_direction` correctly maps NO-labeled rows to `(condition_id, "buy_no")` because when `selected_label="NO"` and `label="NO"` at line 12867, the skip condition is not triggered.

The empirical claim ("all 10 sampled rows have outcome_label=YES") if true, would reflect a transient or budget-caused gap in the warm cycle, not a structural materializer defect. The DB timeout prevented running the repro directly, but the code path clearly stores NO rows.

The "fix" of the 2026-06-09 asset_id fix at line 13541 is irrelevant to this question since the structural path already handles NO rows correctly.

---

## angle:Edge existence + settlement grading + evidence provenance (merged).

**Agent ID:** `agent-a60d6fd8bc16a159b`

### Final Assistant Message

VERDICT: UNDERPOWERED

**n_eff = 121 city-date event families. Formal hypothesis test requires 200-300 minimum.**

---

EMPIRICAL FINDINGS

buy_no direction (312 NSR candidates, 115 events, 25 Zeus fills):
- Actual win rate: 79.8% vs market-implied cost 76.8% → raw edge +3.0pp
- Hypothetical $1/candidate: cost $239.58, payout $249.00, ROI +3.9%
- Model q_live avg: 0.952 → overconfident by +15.4pp vs actual

buy_yes direction (78 NSR candidates, 49 events, 3 Zeus fills):
- Actual win rate: 5.1% vs market-implied cost 11.5% → raw edge -6.4pp (loss-making)
- Hypothetical $1/candidate: cost $8.97, payout $4.00, ROI -55.4%
- Model q_live avg: 0.398 → severely overconfident vs actual 5.1%

Calibration (OOS, 390 NSR records matched to VERIFIED settlements):
- Model Brier: 0.178, Market Brier: 0.125 → market wins calibration
- Log-score: market dominates model on both directions

Actual traded fills (23 with VERIFIED settlement):
- Wins: 17, Losses: 6, Win rate: 74%
- Total notional: $149.33, Net P&L: +$5.60, ROI: 3.8%
- 5 fills attributed to operator (shared wallet, no Zeus decision cert), 2 fills pending settlement

The "+5..+16c/$1" historical claim cannot be re-derived: NSR covers only 2026-05-31 to 2026-06-12 (~2 weeks). Denominator is unknown (gate-passing candidates only, survivorship-biased by q_lcb > c_fee construction).

Paris 2026-06-12 tail bin: bought YES on "26°C or higher", settlement was 17°C → LOSS (-$1.08). Earlier analysis had this wrong due to straight equality matching on tail bins.

---

VERDICT: NO_ACTIONABLE_EDGE / UNDERPOWERED

The buy_no signal is weakly positive in raw direction but:
1. Model is miscalibrated (market Brier beats model Brier)
2. n_eff=121 < 200 minimum for 3pp edge detection at 80% power
3. buy_yes direction is clearly loss-making and should be gated off
4. NSR sample is survivorship-biased (all records have q_lcb > c_fee by gate construction)

The system has positive realized P&L (+$5.60 on $149 notional) but this is consistent with noise given n=23 settled fills. The edge, if real, is in the buy_no direction only. Need approximately 6-10 more weeks of operation at current fill rate to reach n_eff=300 for a formal test.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 95):

**Agent ID:** `agent-a644b85e930fa6f4f`

### Final Assistant Message

Now I have enough to build the verdict. Let me consolidate the key findings:

**What the code actually does:** The current reactor path does NOT use `MONEY_PATH_HORIZON_EXPIRED` for the RISK_GUARD_BLOCKED terminal — the DB shows `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` for the 57 horizon-terminal rows, not `MONEY_PATH_HORIZON_EXPIRED`. The comment at line 1453 says "exhaustion after MAX retries terminates with the honest RISK_GUARD_BLOCKED cause" but there's no attempt cap in the current code — the horizon terminal in `_finalize_disposition` (line 1205) uses `MONEY_PATH_HORIZON_EXPIRED` prefix. The 57 DB rows with `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` all have `MAX(created_at) = 2026-06-12T16:00:54` — i.e., they stopped at the horizon (market closure or disarm), not at a count cap.

**The claim's empirical test:** `COUNT(*) = 0` for `edli_no_submit_receipts` since the block started. `COUNT(*) = 3` in `no_trade_regret_events` for `RISK_GUARD_BLOCKED` AND `RISK_GUARD` stage after the block time (wait — the query returned 0, but the DB table shows the breakdown had `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED = 57` — but those started before and ended by 16:00:54 on 06-12, before `12:12:00` UTC). The `no_trade_regret_events` query returned 0 for exact `rejection_reason='RISK_GUARD_BLOCKED' AND rejection_stage='RISK_GUARD'` after 06-12T12:12 — but the breakdowns show `EXECUTABLE_SNAPSHOT_BLOCKED|EXECUTABLE_QUOTE|322` rows exist after that point, meaning the current block is EXECUTABLE_SNAPSHOT_BLOCKED, not RISK_GUARD_BLOCKED.

The claim says those 2,113 RISK_GUARD_BLOCKED rows are all horizon-terminal, not per-cycle. The DB confirms: the exact query for stage='RISK_GUARD' returns 0 after the block time, and there are 57 `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` rows (all horizon-terminal, all timestamped 06-12 before 16:01). The claim is empirically correct on this point.

**Critical issue for kill evaluation:** The candidate says "for 2+ hours before a horizon fires, 0 rows anywhere" — this is TRUE by code design. But would fixing it (writing a per-cycle regret row on every requeue) move a real order toward a fill? The current SUBMIT GATE blocker is B1 (ws_gap phantom in exchange_reconcile_findings). RISK_GUARD is not the active blocker right now — the current domination is EXECUTABLE_SNAPSHOT_BLOCKED (322 rows) and TRADE_SCORE_NON_POSITIVE (61 rows). Adding per-cycle observability rows during a RISK_GUARD block doesn't unblock RISK_GUARD, and would just add write noise without moving any order to fill.

---

## angle:Capital / portfolio / collateral / risk-allocator / account readiness.

**Agent ID:** `agent-a647b6fc1e2330603`

### Final Assistant Message

## Capital Readiness: Design from First Principles

---

### (1) Objective

The capital readiness layer exists to ensure that every order the edge engine deems profitable can actually be submitted at the intended size, with correct collateral locked, without breaching risk limits — and to do this in the time window between signal and price drift. A correct edge that cannot be submitted is worth exactly zero. The layer must never be the reason a trade is missed; it must also never let a trade through that would breach a hard constraint.

---

### (2) Ideal Mechanism

**Bankroll accounting.** Maintain a single authoritative scalar: `free_collateral = wallet_balance - sum(locked_collateral)`. Every open order and every filled position locks collateral. On this venue, buying YES on bin B requires collateral of `price × size`; buying NO requires `(1 - price) × size`. Holding YES and NO on the same bin simultaneously creates a synthetic locked position equal to `size` (guaranteed cost = 1, no offset). The system must never conflate wallet balance with deployable capital.

**Correlated exposure accounting across mutually-exclusive bins.** Because exactly one bin wins, NO contracts on all losing bins converge to 1. This means holding NO on bins B2…BK while bin B1 wins is a windfall, not a risk. The dangerous direction is holding YES on multiple bins: at most one can win. The risk measure for YES positions is therefore `max(YES_notional_at_risk_per_bin)` for the worst-case loss, not the sum. A naive sum-of-positions measure over-counts risk and leaves capital stranded. The correct worst-case P&L for a mixed portfolio is computed per settlement scenario (K scenarios, one per bin), and the capital reserve must cover the worst scenario net, not the gross notional.

**Order-aware locking.** Open limit orders that have not yet filled must pre-lock collateral at order placement time. An order that sits on the book is a contingent commitment; the engine must treat it as filled for collateral purposes until it is cancelled or rejected. Otherwise, two concurrent order attempts can both see sufficient free collateral, both get accepted, and together over-commit.

**Wallet/signing/allowance state.** Separate from collateral math: the on-chain or custody layer may have its own allowance ceiling, nonce state, or signing key rotation requirements. These must be probed (not assumed valid) before each submission cycle. A stale allowance is silent — the edge engine has no visibility; the rejection arrives only at submission.

**Risk-allocator state machine.** The allocator maintains per-market and per-date exposure caps. It must atomically check-and-reserve capacity before handing the order to the submission path. Without atomicity, two concurrent signals for the same bin on the same date can both pass the cap check and together breach it.

---

### (3) Invariants

- `free_collateral ≥ 0` at all times; never submit if the post-trade value would go negative.
- `locked_collateral = sum over all open_orders(required_collateral) + sum over all positions(required_collateral)` — equality, not a bound.
- Per-bin worst-case loss is bounded by `risk_cap_per_bin`; the YES exposure check uses `max`, not `sum`, across bins in a mutually-exclusive set.
- Allowance on the custody layer ≥ the next order's required collateral before submission is attempted.
- The allocator's cap-reservation and the collateral-lock update are atomic: either both succeed or neither does.

---

### (4) Silent Failure Modes

- **Stale wallet balance:** querying balance at session start, not continuously — fills and withdrawals by co-traders on a shared wallet make the cached balance wrong.
- **Open-order collateral not reserved:** cancels that free collateral are not reflected synchronously; a new order fires before the cancel is confirmed, creating a double-lock or an over-commitment depending on which races.
- **Summing YES exposure instead of taking the max:** over-estimates required reserve, strands capital; or if the error inverts, under-estimates and lets through a position that would breach drawdown on a single bad settlement.
- **Allowance expiry between session start and order time:** checked once at startup, not per-order; large fills or time-based expiry make it invalid silently.
- **Cap-check and cap-reservation in separate non-atomic steps:** a race between two signals for the same market passes both through the cap check and together breaches it.

---

### (5) Three Hardest Design Decisions

**A. Worst-case scenario measure vs. expected-value measure for YES exposure.** I use worst-case (max across bin scenarios), not EV. The bins are mutually exclusive and collectively exhaustive; one settlement eliminates all others. EV hides the tail — on any given day you lose every non-winning YES position. Worst-case is the honest number.

**B. Whether to lock collateral at order placement or at fill.** Lock at placement. Locking at fill is correct on average but fails under concurrency: two orders placed before either fills can together over-commit. The cost is that a resting, unfilled order ties up collateral; this is the correct cost of optionality.

**C. Whether the risk allocator is synchronous (blocks the signal pipeline) or asynchronous (fires and checks later).** Synchronous, and the check-and-reserve must be in a single critical section. Async allocation is faster but creates the exact race condition that silently breaches caps. The latency cost of a synchronous lock is acceptable; the silent breach cost is not.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a6488985488effa3f`

### Final Assistant Message

I now have enough to render a verdict. Let me consolidate the findings.

Key empirical facts verified:

1. `opportunity_fact` in zeus_trades.db has 38,555 rows total, MAX recorded_at = 2026-05-28. The claim says "0 rows since 06-04" — correct on the staleness, but not "0 rows ever."

2. `decision_events` in zeus-world.db has 0 rows (the table exists with schema_version checks up to 28, but was never written to). The claim says "0 rows ever" — confirmed.

3. `no_trade_events` max observed_at = 2026-05-28 — silent since before 06-04.

4. `selection_family_fact` in zeus-world.db has 0 rows; in zeus_trades.db has 412 rows (max 2026-05-28). The write routing IS correct: `_selection_fact_table_ref` correctly routes to `world.selection_family_fact` when world is ATTACHed — but writes there too are stale since 05-28.

5. `opportunity_fact` routing is NOT via `_selection_fact_table_ref` — `log_opportunity_fact` uses the trades DB `conn` directly. That table has 104 rows with `should_trade=1` historically.

6. The critical architecture fact: `get_connection()` in cycle_runner.py (line 77-88) ATTACHes world DB onto the trades connection before passing it to `execute_discovery_phase`. So the "world not attached" failure path in `_selection_fact_table_ref` should NOT trigger.

**The core defect claim — "silent skip because world not attached" — is wrong for the current codebase.** The ATTACH happens in `get_connection()`, unconditionally, before any cycle runs. The real issue is that the entire pipeline (opportunity_fact, selection_family_fact, no_trade_events, decision_events) stopped receiving writes around 2026-05-28. That silence is a symptom of the pipeline not running or crashing silently before reaching these write points — NOT a "wrong DB" routing bug. The "skipped_missing_table" path is structurally reachable only if world isn't attached, but world IS attached at the connection setup layer.

The claim's mechanism (wrong-DB conn causes silent skip) is refuted by the code: `cycle_runner.get_connection()` ATTACHES world before the conn is ever used. The `_selection_fact_table_ref` would return `"world.selection_family_fact"` correctly. The silence is real but the cause attributed is wrong — it's upstream starvation (evaluate_candidate not being reached) not a write-routing bug. "Fixing" the non-existent routing bug would not move a real order.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 95):

**Agent ID:** `agent-a6649215a47ae0e37`

### Final Assistant Message

I now have enough to render a verdict. Here is the synthesis:

**What the code actually does:**

1. The RISK_GUARD_BLOCKED transient path in reactor.py:1455 writes only to `_transient_requeue_reasons` (in-memory dict) and returns `_EXECUTABLE_SNAPSHOT_RETRY`. No DB write occurs per-cycle. The candidate is correct on mechanism.

2. The 39,989 rows in `no_trade_regret_events` at `rejection_reason='RISK_GUARD_BLOCKED'` / `rejection_stage='RISK_GUARD'` are NOT horizon-terminal records as the candidate claims — their max timestamp is 2026-06-12T12:10:35, which is **before** the current block (which started 2026-06-12T22:58). These are from an earlier regime (the pre-transient-requeue design, or the 2026-06-12 riskguard-storm where events were terminally rejected). The candidate's own "2113 rows" figure is stale/wrong on the timestamp; actual is 0 rows after the block started.

3. After the block (22:58), there are 57 rows at `rejection_reason='MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED'` / `rejection_stage='EXECUTABLE_QUOTE'` — these ARE the horizon-terminal records that write at expiry.

4. So the core mechanism claim is verified: between per-cycle requeue and horizon-terminal, there is indeed zero persisted provenance.

**The kill angle — criterion (d):** The defect is observability only. The block itself is caused by B1 (a phantom `exchange_reconcile_findings` row for a swept winner — a separate, already-identified defect). Adding per-cycle DB writes for RISK_GUARD_BLOCKED requeues would not unblock submission, would not alter the RiskGuard latch decision, and would not move a single order closer to fill. The submission gate (B1) is frozen by a separate mechanism entirely unaffected by whether the requeue is logged. Adding this logging would be a pure observability improvement with no money-path effect.

**Criterion (b) also partially applies:** The code already provides terminal visibility (57 horizon-terminal rows written), plus in-process logging via `_note_transient_requeue`. The gap is per-cycle DB rows — real, but purely diagnostic.

---

## reconcile:Latency / alpha decay / market-lifecycle timing

**Agent ID:** `agent-a677cad4c30af159c`

### Final Assistant Message

Now I have enough for a complete analysis. Let me synthesize the findings:

---

## refuter:candidate=DEFECT (angle R12, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a68604297c767d584`

### Final Assistant Message

I have everything needed. The comment at line 1453 says "exhaustion after MAX retries terminates with the honest RISK_GUARD_BLOCKED cause" but this is stale documentation — the actual logic in `_finalize_disposition` (lines 1192-1228) shows the termination is horizon-only (semantic: market closed or operator disarm), not a retry count. There is no MAX retry cap — that's the operator law "重试次数不是市场事实". The `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` label (57 rows) is the horizon-terminal path, not a retry-count exhaustion.

The defect claim has three premises. Let me evaluate each:

**Premise 1: "burns entire queue budget during sustained halts"** — Partially false. The cross-city round-robin in `fetch_pending` (documented at lines 1147-1153) is specifically designed so a single perpetually-transient city cannot preempt other cities. Budget is consumed but events from OTHER cities are still served. This is not a budget monopoly claim that holds.

**Premise 2: "no readiness probe or cycle counter"** — True that there is no counter-based termination and no `_record_substrate_block` for riskguard (only for snapshot line 1440 and posterior line 1724). But the design is intentional: the `_transient_horizon_terminal` provides the semantic bound (venue close, operator disarm).

**Premise 3: "burns limit budget with no progress and no self-healing side-effect"** — The claim that riskguard blocks have "no self-healing side-effect" is technically correct: unlike `EXECUTABLE_SNAPSHOT_BLOCKED` which calls `_record_substrate_block` to trigger a refresh, `RISK_GUARD_BLOCKED` does not trigger any remediation action. However, this is by design — the riskguard reads `risk_state` from the DB; the riskguard itself is not a refreshable substrate the reactor can self-heal. The actual current B1 blocker is an `exchange_reconcile_findings` row, not a riskguard transient stale-read.

**The fill question (d)**: The dominant live blocker is B1 (submit latch frozen by reconcile row 5bbc2be2, not RISK_GUARD_BLOCKED). The 2113 RISK_GUARD_BLOCKED events are from the freeze period 2026-06-12 → present, but the submit gate is currently held by a different mechanism. Even if riskguard cycling were eliminated, the B1 latch would still block submission. The riskguard events ARE consuming queue slots but are bounded by semantic horizons and the round-robin prevents starvation of other cities. Fixing this would not move a real order toward fill in the current live state.

---

## angle:Opportunity universe / candidate generation / bin-action selection.

**Agent ID:** `agent-a69437435de1ae56f`

### Final Assistant Message

Now I have a complete picture. Here is the forensic verdict:

---

OPPORTUNITY UNIVERSE / CANDIDATE GENERATION ANGLE — FORENSIC VERDICT

VERDICT ON b1825c4a07 (S7):

b1825c4a07 removed ONLY A TOGGLE, not candidate materialization. What it deleted was the `selector_enabled` cache-flag branch inside `to_receipt_dict` (opportunity_book.py:53-60) and the dead `_env_flag_enabled` helper plus `selector_enabled`/`selector_shadow` cache keys in event_reactor_adapter.py. The ΔU ranker (`_select_proof_by_robust_marginal_utility`) was already the live decision surface from S3-fix (commit 2dd3329c78). S7 is a deadwood-removal commit — materialization was INTACT before and after it. The receipt now records `selected_candidate_id` unconditionally (opportunity_book.py:60) and `build_family_opportunity_book` passes `decided_candidate_id` unchanged (adapter line 7251-7255). No regression here.

VERDICT ON S1–S7 AS A WHOLE CAUSING B2 (decision silence):

S1–S7 did not break materialization. The candidate pipeline is structurally intact: `_generate_candidate_proofs` runs, proofs are materialized as `NativeSideCandidate` objects, `_select_proof_by_robust_marginal_utility` runs the ΔU ranker, and `build_family_opportunity_book` records the result. The B2 decision silence (capital_efficiency_lcb_ev, coverage_unlicensed_tail, direction_law, TRADE_SCORE_NON_POSITIVE) is a gate-rejection pattern, NOT a pipeline break. Candidates are being fully materialized and then ADMISSIBLY rejected at the `CandidateEvaluation.admitted` gate (candidate_evaluation.py:155-166), which requires `trade_score > 0` AND `live_capital_efficiency_admissible` AND `live_buy_no_conservative_evidence_admissible`. The rejection labels the live seeding shows (KL 35C+ q_lcb=0.0392 vs price=0.0080 giving ev/$=3.90, yet rejected with capital_efficiency_lcb_ev) point to `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV` — the `trade_score <= 0` or `q_lcb <= price` gate — which is honest in the current regime. These are calibration/q_lcb-computation questions, not a pipeline rupture.

VERDICT ON buy_no IN scan_full_hypothesis_family (the bare `continue`, line 105):

This is a confirmed structural gap, but it is KNOWN AND MITIGATED. `scan_full_hypothesis_family` (market_analysis_family_scan.py:103-105) calls `_supports_buy_no_for_bin` and then immediately does a bare `continue` regardless of the result — so it NEVER emits a buy_no `FullFamilyHypothesis`. This means `no_hyp` is always `None` in `_canonical_probability_and_fdr_proof` (adapter line 10312). However, FINDING-D (commit 99d63a30ac, 2026-06-12) explicitly diagnosed and compensated for this: when `no_hyp is None`, the adapter calls `_native_no_edge_positivity` (adapter lines 10362-10368) which derives the buy_no p_value/prefilter from the native NO cost directly. So the bare `continue` is a pre-existing structural truth about this function — the adapter code explicitly documents it as "scan_full_hypothesis_family NEVER emits a NO hypothesis (its buy_no loop body is a bare continue)" (adapter:10343). The compensation is in place.

VERDICT ON THE PROFITABLE-ERA NO GATE (bfdf5e9c9d, 2026-06-13):

Commit bfdf5e9c9d (merged 2026-06-13T18:35Z) reverted the non-executable-YES buy_no q_lcb path that commit 745aa10c6f introduced. The revert restores q_lcb_no=0.0 / p=1.0 / prefilter=False for bins where the YES side has no executable market (adapter:10379-10386). This is deliberately tight: a forecast-derived buy_no on a non-executable-YES bin was buying NO on 97.8% of winning bins. This affects families where YES is non-executable; for bins with executable YES, FINDING-D still operates and buy_no can be admitted via the native NO cost path. The B2 rejections on longshot YES bins (KL 35C+ buy_yes q_lcb=0.0392) are going through the YES executable path not this gate.

CONFIRMED OPEN ISSUES IN THIS ANGLE:

1. e583af06cd (gate-mass collapse, 2026-06-13) removed `selection_eb` flag but retained the `EB computation` with `authority_on=False`. This is correctly noted in the commit as "BH/FDR is the unconditional live selection gate." No materialization breakage.

2. Gamma empty event-lists (mentioned as secondary blocker) are upstream of this angle — the adapter references Gamma's `executable_allowed` field at adapter:12928-12933. The `scan_full_hypothesis_family` skips non-executable bins via `_is_executable_bin` (line 79). If Gamma marks bins non-executable (wrong-field wall, 7287574378 was the fix), those bins silently skip hypothesis generation. This is load-bearing: if `_is_executable_bin` returns False for all bins, `scan_full_hypothesis_family` returns an empty list, `_generate_candidate_proofs` gets no executable rows, and the family gets a `LIVE_INFERENCE_INPUTS_MISSING` error (adapter:2415). This remains a risk if Gamma family/event classifications are wrong for any city.

3. The `CandidateEvaluation.admitted` property (candidate_evaluation.py:155-166) gates on `live_capital_efficiency_admissible`. The dominant rejection reason (capital_efficiency_lcb_ev) is structural: q_lcb must exceed price for a positive trade_score. The longshot examples (KL 35C+ price=0.0080, ev/$=3.90 implies trade_score positive) — that ev/$=3.90 at price=0.0080 with q_lcb=0.0392 passes 0.0392 > 0.0080, so the capital efficiency gate should pass. If those are being rejected as `capital_efficiency_lcb_ev`, the suspect is the live_capital_efficiency_rejection_reason implementation in `live_admission.py` (imported at candidate_evaluation.py:10-14) — this module is NOT in the specified file list and was not audited here.

SUMMARY TABLE:

b1825c4a07 (S7): Toggle-only removal. Materialization intact. NOT a regression source.
S1-S7 collectively: Architecture sound, single ΔU selection path, opportunity book correctly records ΔU decision via `decided_candidate_id`. Not the B2 cause.
e583af06cd: Shadow module removal only. Live gate (BH/FDR) unchanged. NOT a regression source.
scan_full_hypothesis_family bare `continue` (line 105): Pre-existing, KNOWN, mitigated by FINDING-D (99d63a30ac) in the adapter. Not new regression.
bfdf5e9c9d (NO profitable-era revert, 2026-06-13): Intentional loss-class closure. Correct.
B2 root in this angle: NOT a pipeline break. Materialization runs. Admission gates fire on calibration/q_lcb values. The locus of B2 is in `live_admission.py` (rejection reason logic) and calibration q_lcb values — both OUTSIDE the specified file set.
B1 (ws_gap latch): Completely orthogonal to this angle. The latch is in `src/control/block_adapters/ws_gap_guard.py` probing an in-memory WSGapStatus singleton. The opportunity pipeline doesn't touch it.

---

## angle:Contract Universe / Instrument Identity / Market Lifecycle**

**Agent ID:** `agent-a6cc08ef2f771047a`

### Final Assistant Message

Now I have sufficient evidence to produce the structured output. Let me compile the divergences.

---

## refuter:candidate=DEFECT (angle R16, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a6ce9fe1c13c5d06b`

### Final Assistant Message

I now have enough to render a verdict. The evidence is comprehensive:

1. `authority_on=False` (event_reactor_adapter.py:2811) is confirmed in code
2. `_gate_passed = fdr.passed` at line 2869 - BH/FDR IS the unconditional gate
3. BUT: ALL 62,874 receipts in the DB have `fdr_pass=True`, including all 32 post-06-12 receipts with positive `trade_score` (0.007-0.076 range)
4. The repro instruction (search for `FDR_REJECTED` receipts with `trade_score > 0`) yields **zero rows**
5. The BH math with {0,1} p-values is not vacuous for rejection - it correctly passes any candidate with `q_lcb > cost` (p=0.0) and rejects any family where all candidates fail edge (p=1.0). This IS economically meaningful - it's the capital efficiency gate
6. The actual submission blocker post-06-12T12:12 is B1 (exchange_reconcile_findings latch), NOT FDR
7. The "EB flag hardcoded False" complaint is true but the comment in code explicitly says shadow-only - the BH gate itself is passing real edge candidates

---

## refuter:candidate=DEFECT (angle R7, tag MISSING_CAPABILITY, impact 72):

**Agent ID:** `agent-a7483598c980753d3`

### Final Assistant Message

This is the kill. With `NATIVE_MULTIBIN_BUY_NO_SHADOW=false` (live config), `p_market_no=None` is passed to `MarketAnalysis`. Therefore `supports_buy_no_edges()` returns `False` for every bin (line 462-463). This means `_supports_buy_no_for_bin()` in the scan returns `False`, and the first `if not ... continue` at line 103 already exits the loop — so the unconditional `continue` at line 105 is NEVER reached when the flag is off. The structural defect is unreachable dead code under current live settings.

Furthermore, even if the flag were on, the scan does return buy_yes for every bin — and `find_edges()` with `buy_no_quote_available=None` produces no buy_no edges (market_analysis.py line 462-465, line 665). So there are no buy_no edges to filter in `_filter_executable_selected_edges` anyway. The adapter's FINDING-D compensation at lines 10358-10368 handles the adapter path correctly and is authoritative for that code path.

The defect is real as written — the `continue` at line 105 is structurally dead code that would prevent buy_no hypothesis emission — but it has no current impact because the outer guard (`_supports_buy_no_for_bin` returning False when `p_market_no=None`) already prevents reaching it, and the live flags that would change this are both false.

---

## refuter:candidate=DEFECT (angle R2, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-a77a335f03a623b54`

### Final Assistant Message

The investigation is complete. Here is the verdict:

**Kill found on criterion (c) — empirical claim is factually wrong, and (b) — code already handles expiry fail-closed.**

Three independent kills:

1. The empirical claim is false. The stated "467 LIVE_ELIGIBLE rows expiring 2026-06-14T20:23Z" does not exist in the DB. Actual query shows all 477 LIVE_ELIGIBLE rows have `expires_at` capped at 2026-05-05T18:29Z — they expired over five weeks ago. The repro at 2026-06-14T21:00Z would already observe zero rows, but this predates the claimed June-14 expiry event by months.

2. Code already handles expiry fail-closed. `get_entry_readiness()` in `/Users/leofitz/zeus/src/state/readiness_repo.py:275` returns `UNKNOWN_BLOCKED("READINESS_EXPIRED")` when `expires_at <= now`. The reactor's `_reject_event()` then emits a persisted failure certificate. Expiry is not silent — it produces an observable rejection.

3. The live entry path bypasses this gate entirely. `_read_executable_forecast_bundle_result` in `/Users/leofitz/zeus/src/engine/event_reactor_adapter.py:14176` calls `read_executable_forecast(..., require_entry_readiness=False)`. No production code path passes `require_entry_readiness=True`. The readiness_state LIVE_ELIGIBLE rows (strategy_key='producer_readiness') feed only a diagnostic fallback, not a live order gate. Stage 4 decision_certificates being dark since 2026-06-12 is caused by B1 (submit latch frozen by phantom reconcile finding 5bbc2be2), not by readiness expiry.

---

## refuter:candidate=DEFECT (angle R2, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-a7ad2a30c833ccdc8`

### Final Assistant Message

I now have a complete picture. The key facts:

1. The defect's empirical claim is **factually wrong**: The DB has 477 LIVE_ELIGIBLE rows (not 467), all expiring 2026-05-05 (not 2026-06-14T20:23Z). The specific expiry timestamp cited in the defect does not exist.

2. The system has **always run with all readiness rows expired** during the observable window. Venue_commands extend to 2026-05-19 while readiness expired 2026-05-05. The 40 historical fills happened during this period.

3. The readiness expiry check is **bypassed on the PARTIAL path** (`executable_forecast_reader.py:1000-1004`): `if completeness_status == "COMPLETE"` — PARTIAL coverage skips the expiry check. The scope-level readiness is explicitly a diagnostic fallback only (`src/data/executable_forecast_reader.py:1325-1330`: "do NOT hard-gate on scope-level producer readiness before enumeration… producer_reason is used as a diagnostic fallback ONLY").

4. The "no alert when rows expire" complaint is structurally moot: the rows have been expired for 40+ days while the system continued producing orders — the system is architecturally designed to operate without fresh LIVE_ELIGIBLE readiness rows via the PARTIAL lane.

5. The current order dark period (decision_certificates dark since 2026-06-12) is caused by the B1 submit gate (exchange_reconcile_findings phantom row / ws_gap latch) and B2 decision gate (capital_efficiency_lcb_ev), not by readiness expiry.

6. Adding a watchdog alert for readiness mass-expiry would not unblock a single order — both active blockers (B1: submit latch, B2: no-edge) are independent of readiness state.

---

## agent-a7bc614678c7146c1

**Agent ID:** `agent-a7bc614678c7146c1`

### Final Assistant Message

Here is the complete per-cycle stage funnel, 2026-06-04 through now, from primitive tables only.

---

FUNNEL (all queries against read-only DBs; row counts are exact scalars)

STAGE 1: Active families (selection_family_fact, zeus-world.db)
Zero rows since 2026-06-04. The table exists but the cycle that writes family records never ran or its output was not committed in this window. This stage has been dark throughout the observation period.

STAGE 2: Fresh book snapshots (executable_market_snapshots, zeus_trades.db)
Query: SELECT DATE(captured_at), COUNT(*) WHERE captured_at >= '2026-06-04' GROUP BY day
06-04: 58k, 06-05: 70k, 06-06: 397k, 06-07: 507k, 06-08: 106k, 06-09: 136k, 06-10: 785k, 06-11: 335k, 06-12: 306k, 06-13: 390k, 06-14: 162k (partial).
10,194 unique conditions tracked; freshest snapshot captured 2026-06-14T05:22:31. This stage is healthy and continuously fed.

STAGE 3: Candidate generation (opportunity_fact, opportunity_events, decision_events, no_trade_events — all zeus-world.db)
All four tables: zero rows since 2026-06-04 (opportunity_fact and opportunity_events never populated; decision_events 0 rows ever; no_trade_events' 2,952 rows all predate 2026-05-29 and went silent). Candidates are produced nowhere in this period. This is a critical structural zero — the reactor is fetching books but never generating decision candidates.

STAGE 4: Decision evaluation (decision_certificates, zeus-world.db)
Query: SELECT DATE(decision_time), COUNT(*), SUM(mode='LIVE'), SUM(mode='NO_SUBMIT')
06-04: 29,660 total (0 LIVE / 29,660 NO_SUBMIT), 06-05: 13,620 (0/13k), 06-06: 6,914 (2,072 LIVE / 4,842 NO_SUBMIT), 06-07: 1,040 (320 LIVE), 06-09: 52 (16 LIVE), 06-10: 1,440 (160 LIVE), 06-11: 790 (200 LIVE), 06-12: 1,394 (232 LIVE last day).
06-13 through 06-14: 0 rows. The decision engine went completely dark after 2026-06-12. Last LIVE cert: 2026-06-12, ~232 decisions.

STAGE 5: Submit-lane receipts (edli_no_submit_receipts, zeus-world.db)
62,874 total rows; active through 2026-06-12T12:12:20, then silence. Most recent receipts show buy_no candidates with trade_score 0.007-0.076 (positive edge, not edge-zero rejections) — the receipts stopped because the submit gate froze, not because all candidates were edge-negative.

STAGE 6: Submission envelopes (venue_submission_envelopes, zeus_trades.db)
Signed order blobs: 06-06: 30, 06-07: 35, 06-08: 2, 06-10: 16, 06-11: 2, 06-12: 10. Last envelope: 2026-06-12T13:04:26.

STAGE 7: Venue commands (venue_commands, zeus_trades.db)
50 total; FILLED: 40 (last fill 2026-06-11T17:18:17), EXPIRED: 6 (last 2026-06-12T13:04:26), REJECTED: 2, PARTIAL: 1, SUBMIT_REJECTED: 1. No new commands after 2026-06-12T13:04:26.

STAGE 8: Order lifecycle (venue_order_facts, zeus_trades.db)
55 rows total; 06-06 through 06-13 (one straggler). Last row 2026-06-13 at 1 row.

STAGE 9: Zeus fill bridge (execution_fact, zeus_trades.db)
227 rows; 103 entry fills, 17 exit fills, 60 voided. Last entry fill: 2026-06-14T05:16:10 (opening_inertia, 5.0 shares at price 0.73, status PARTIAL — this is a pre-existing GTC resting order getting matched, not a new submission). Last exit fill: 2026-06-07T17:25:37.

---

FIRST ZERO STAGE: Stage 3 (candidates) — zero rows for the entire 2026-06-04 to 2026-06-14 window in opportunity_fact, opportunity_events, and decision_events. The EMS pipeline (Stage 2) is fully live, but nothing downstream of it is being evaluated. Separately, Stage 4 (decision_certificates) went completely dark after 2026-06-12T22:58 — coincident with B1.

LAST-GOOD / FIRST-BAD:
- Stages 2 and EMS: continuously good through today.
- Stage 3 (candidates): never good in this window (structural gap — those tables are blank; the decision pathway may route through a different mechanism than opportunity_fact/decision_events in the current code).
- Stage 4 (decision_certificates): last good 2026-06-12T23:xx (232 LIVE certs that day), first bad 2026-06-13T00:00 (0 certs).
- Stages 5-7 (receipts/envelopes/commands): last good 2026-06-12T13:04:26, first bad 2026-06-12T22:58:12 (the B1 finding was recorded at 22:58, five minutes after the last EXPIRED command at 13:04).

B1 ROOT CAUSE CONFIRMED: exchange_reconcile_findings finding 5bbc2be2 (position_drift, ws_gap context, token 2599807256..., recorded 2026-06-12T22:58:12) is the sole unresolved finding. This latched allow_submit=False for the entire system. The last venue_command (EXPIRED, 2026-06-12T13:04) predates the finding by ~10 hours, meaning the submit gate was frozen before any new submission was attempted. Since 2026-06-12T22:58 there have been 0 new venue_commands, 0 new envelopes, 0 new LIVE decision_certificates, and 0 new no-submit receipts.

The 2026-06-14T05:16 entry fill in execution_fact is a late-match on a resting GTC order submitted before the blocker; it is not evidence that submission is unblocked.

---

## refuter:candidate=DEFECT (angle R16, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a8168169c0440be09`

### Final Assistant Message

This confirms the kill. With binary {0.0, 1.0} p-values, any candidate with p=0.0 (edge-positive) ALWAYS passes BH regardless of family size. The claim that "BH/FDR is an unconditional live selection gate that is blocking positive-trade-score candidates" is empirically false:

1. The live DB shows zero FDR_REJECTED receipts — not one across the entire table history.
2. The 3 post-06-12T12:12 receipts with positive trade_score show `fdr_pass=1`, reason `event_bound_final_intent_no_submit` (blocked by B1 submit latch, not FDR).
3. The BH math with p=0.0 always passes — the gate is mathematically not a blocker for edge-positive candidates.
4. The `authority_on=False` only controls whether EB-shrinkage replaces BH as the DECISION authority — it is a shadow flag, and the comment at event_reactor_adapter.py:2800-2805 explicitly says the EB computation is SHADOW-ONLY, not a gate.

---

## refuter:candidate=DEFECT (angle R1, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-a8193d7175693a399`

### Final Assistant Message

The opportunity_fact is written per-decision via a queued derived write. The 0 rows since 05-28 means either: (a) the live cycle stopped running, (b) the `log_opportunity_fact` is not being called, or (c) it's failing silently. This is NOT caused by the source_contract filter because the live cycle uses `read_persisted_weather_markets` which hardcodes `source_contract.status = "MATCH"`.

Now I have enough to evaluate the defect claim thoroughly:

**Summary of findings:**

1. **Code is accurately cited**: market_scanner.py:1033-1042 drops non-MATCH source_contract at WARNING level with no ERROR/metric counter. True.

2. **But the empirical anchor is wrong**: The live decision cycle uses `read_persisted_weather_markets` (market_scanner.py:3215), which at line 3328 hardcodes `"source_contract": {"status": "MATCH", ...}` for all EMS-backed markets. The non-MATCH filter at 1033 is ONLY in `_parse_and_persist_weather_events` which is the background market discovery path, not the live cycle path.

3. **The EMS is healthy**: 3.57M rows in zeus_trades.db, fresh through 06-14T06:20. The system is seeing markets just fine.

4. **opportunity_fact gap is NOT caused by source_contract drops**: The gap (0 rows since 05-28) is caused by separate architectural changes/blockers (likely B1 or changes to when _record_opportunity_fact is called), not by markets being silently dropped at the source_contract filter. The EMS confirms markets flow through to the live cycle regardless.

5. **The "WITNESS Stage 3 zero rows" evidence is a false attribution**: opportunity_events has 6.5M rows including recent FORECAST_SNAPSHOT_READY events. The defect conflates the opportunity_fact logging gap (a separate observability issue unrelated to source_contract) with the source_contract drop path.

**Kill verdict**: The empirical claim that "zero rows in opportunity_fact/opportunity_events for 06-04 to 06-14" is caused by this source_contract drop path is refuted. The live cycle bypasses the non-MATCH filter entirely via hardcoded MATCH injection in `read_persisted_weather_markets`. The observability gap (no ERROR signal for drops) is a real code defect, but fixing it would not move a real order since the live cycle path is not affected by the filter.

---

## refuter:candidate=DEFECT (angle R4, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a82f548cc2ed1e019`

### Final Assistant Message

REFUTED on three independent kills:

1. **Flag is True in live.** `_replacement_fused_q_shape_enabled()` returns `True` — the defect's core premise is factually wrong.

2. **SHADOW_ONLY is schema-locked, not flag-controlled.** `v2_schema.py` lines 302-303 have `CHECK (trade_authority_status IN ('SHADOW_ONLY'))` on `forecast_posteriors` — the column cannot hold any other value by design. All 3,417 rows being SHADOW_ONLY is expected and correct, not evidence of suppression.

3. **The live gate reads `provenance_json.replacement_q_mode`, not `trade_authority_status`.** `event_reactor_adapter.py:9350-9371` (`_replacement_q_mode_live_eligibility`) gates on `FUSED_NORMAL_FULL` / `FUSED_NORMAL_PARTIAL` read from `provenance_json`. The DB has 1,553 FUSED_NORMAL_FULL + 294 FUSED_NORMAL_PARTIAL rows already populated in that field. The live path exists and is populated. The SHADOW_ONLY column is an intentional non-authority marker on the artifact layer, orthogonal to submission eligibility.

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-a84bf0412ae7c69b3`

### Final Assistant Message

Now I have enough to write the complete forensic verdict.

---

CONTRACT UNIVERSE / INSTRUMENT IDENTITY / MARKET LIFECYCLE ANGLE

**Scope of files audited:** `src/data/market_scanner.py`, `src/data/polymarket_client.py`, `src/contracts/settlement_semantics.py`, `src/contracts/settlement_resolution.py`, `src/contracts/settlement_outcome.py`, `src/events/opportunity_event.py`, `src/events/candidate_binding.py`.

---

**B1 (SUBMIT GATE) — root cause confirmed at this angle**

The unresolved `exchange_reconcile_findings` row `5bbc2be2-350c-4bdf-ac0e-f080e41f9012` (recorded 2026-06-12T22:58 UTC) is a `position_drift / ws_gap` finding whose `resolved_at` is NULL. This single unresolved row freezes `allow_submit=False` system-wide.

The finding's evidence (queried live from `state/zeus_trades.db`) reads:
- `exchange_size = "0"` — the position is absent from the exchange wallet
- `closed_position_token_size = "17.05"` — a `terminal_position_current_chain_holdings` entry exists
- `settlement_command_token_size = "0"` — no pending redeem command
- `settlement_evidence_class = "unconfirmed_redeem_settlement_commands"`
- `closed_position_evidence_class = "terminal_position_current_chain_holdings"`
- `reason = "exchange_position_differs_from_expected_wallet_facts"`
- `subject_id` is token `2599807256...` (Beijing NO)

This is the exact "swept/redeemed winner whose close was never absorbed" shape. The absorber introduced by commit `57c441049d` (`external-close absorption antibody`, 2026-06-10) at `exchange_reconcile.py:1144–1189` would resolve this — but it is **gated on `_operator_acknowledged_drift_resolution`** (line 1162): it checks for a prior *resolved* `position_drift` finding for this token where `resolved_by LIKE 'session_operator_confirmed%' OR resolution LIKE 'operator_manual%'`. No such row exists for this token (it is the only unresolved finding, count=1). Without the operator-acknowledgement prerequisite, `_absorb_operator_external_close` returns `False`, the reconcile loop falls through to `record_finding`, and the latch stays frozen.

This is the **apparent regression of task #31**: the external-close absorber was designed to prevent exactly this freeze, but it requires an operator-acknowledgement row as the trigger. The token was swept/redeemed externally with no Zeus-originated acknowledgement row, so the absorber never fires.

**This angle's contribution to B1:** the market-lifecycle angle is directly causally connected — a settled/redeemed position (lifecycle state REDEEMED per `SettlementOutcome`) has `exchange_size=0`, which is the correct post-redemption state. The reconciler correctly sees no exchange position, but expects 17.05 from its journal + chain holdings. The absorber's gap (requires operator-ack row, none exists) is what keeps the latch frozen, not a bug in the settlement-outcome state machine itself.

---

**B1825c4a07 — candidate materialization verdict**

`b1825c4a07` (`bin-selection S7`, 2026-06-08) touched only:
- `src/events/opportunity_book.py` — removed the `selector_enabled` cache-flag gate from `to_receipt_dict`, making `selected_candidate_id` unconditional
- `src/engine/event_reactor_adapter.py` — removed dead `selector_enabled`/`selector_shadow` cache keys

It did **NOT** remove candidate materialization. The full `bind_event_to_candidate_family` path in `src/events/candidate_binding.py` is untouched. `build_family_opportunity_book` in `opportunity_book.py` still builds the full candidate topology, evaluates every bin, records `decided_candidate_id` as `selected_candidate_id`. The only thing removed was a runtime flag that could silently null the recorded selection — the removal was a correctness fix, not a materialization removal.

**Verdict: b1825c4a07 removed a toggle only. Candidate materialization survived intact.**

---

**e583af06cd (gate-mass collapse, 2026-06-13)**

Removed 6 shadow strategy modules plus q-shadow/exit flags. None of the removed modules were in the contract-universe/instrument-identity path. `settle_market`, `SettlementSemantics`, `SettlementOutcome`, `SettlementResolution`, `candidate_binding`, `opportunity_event`, `market_scanner` — all untouched. The removed code was telemetry-only shadow compute behind config-False flags. This commit has **no effect on this angle**.

---

**Active/closed/resolved detection — current state**

`_event_has_active_children` (market_scanner.py:1447–1501): correctly gates on child `acceptingOrders=True` plus CLOB cross-check (`archived=False AND enable_order_book=True`). Discovery scan runs `clob_crosscheck=False` for speed; the per-outcome CLOB check fires in `capture_executable_market_snapshot` which raises `ExecutableSnapshotCaptureError(reason="clob_archived")` for archived markets — they never enter `executable_market_snapshots`.

`classify_settlement_outcome` (settlement_outcome.py:164–201): fail-closed. `umaResolutionStatus != "resolved"` → UNRESOLVED. Binary `[1,0]`/`[0,1]` → WIN/LOSE. Anything else → SOURCE_PUBLISHED_VENUE_UNRESOLVED. The Beijing NO position's token (exchange_size=0) would have gone through VENUE_RESOLVED_WIN → REDEEMED per the lifecycle DAG; the reconciler's `terminal_position_current_chain_holdings` evidence confirms this.

**Join from active market to executable snapshot:** scanner produces `support_topology.executable_outcomes` (market_scanner.py:1908), which carry `condition_id`/`token_id`. These feed `executable_condition_ids` on the event dict (line 1947–1950), which drives WS subscription. `_persist_market_events_to_db` writes `(market_slug, condition_id, token_id, ...)` to `market_events`. The reactor reads `market_events` to build `MarketTopologyCandidate` rows passed to `bind_event_to_candidate_family`. The join is `condition_id` as the shared key. This chain is intact and unaffected by S1-S7 or the e583 collapse.

---

**B2/B3 — this angle's verdict**

B2 (decision silence) is not caused by anything in this angle's files. The market-scanner correctly discovers markets, builds support topology, persists to `market_events`, and the candidate-binding layer is intact after b1825c4a07. The B2 decision-gate rejections (`capital_efficiency_lcb_ev`, etc.) are downstream of this angle's responsibility.

B3 (blind exit, Beijing NO, stale belief 87 cycles): the Beijing NO token is the same one frozen in the B1 latch (`subject_id` in the finding is the NO token). The `exchange_size=0` finding means the position was externally closed/redeemed, but Zeus's BeliefService/exit organ hasn't absorbed the close — separate from this angle's scope.

---

**Summary for the operator**

This angle finds one actionable item touching B1: the `_absorb_operator_external_close` absorber at `exchange_reconcile.py:1144` requires a prior operator-acknowledgement row (`resolved_by LIKE 'session_operator_confirmed%'`) for token `2599807256...` (Beijing NO). No such row exists. The fix is either (a) insert the operator-acknowledgement row for this token so the absorber fires on the next reconcile sweep, or (b) widen the absorber to also fire on `terminal_position_current_chain_holdings + exchange_size=0 + settlement_command_token_size=0` as a "self-evident external redemption" class without requiring the ack row. Both paths must be gated on the on-chain evidence that the position is genuinely gone (exchange_size=0 is necessary but the absorber currently also requires journal_long>0 — present here at 17.05 — which is met).

---

## refuter:candidate=DEFECT (angle R15, tag SEMANTIC_CONFLICT, impact 70):

**Agent ID:** `agent-a86fd04d7b38109b2`

### Final Assistant Message

Now I have the full picture. The key insight:

**When `ZEUS_ACCEPT_STALE_DEPLOY=1` and git fails:**
1. `_capture_boot_state()` returns `{"sha": None, "ts": None}`
2. `_write_loaded_sha_state(None)` is called and **skips writing** `state/loaded_sha.json` (line 4442-4447)
3. When `_edli_stage_loaded_sha_reasons()` is called: line 873 checks `if not file_path.exists()` → returns `["EDLI_STAGE_LOADED_SHA_MISSING:{path}"]` (a FAIL reason)
4. This causes `evaluate_edli_stage_readiness()` to return FAIL with `live_entries_allowed=False`

So the SHA "bypass" via `ZEUS_ACCEPT_STALE_DEPLOY=1` does NOT produce a silent no-op on the EDLI gate — it produces a different fail reason (`EDLI_STAGE_LOADED_SHA_MISSING`) rather than no failure. The gate still blocks. The candidate claims the guard "collapses to dead letter" which is incorrect — it converts to a different failure mode (file missing), not a pass.

The mismatch check (line 881) is bypassed, but the file-existence check (line 873) still fires since `_write_loaded_sha_state(None)` early-returns without writing the file. The INV-5 requirement (daemon refuses orders on SHA drift) is still upheld — just via a different failure path.

---

## refuter:candidate=DEFECT (angle R4, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-a897386ad889ed4f3`

### Final Assistant Message

I have enough information. Let me synthesize the findings.

---

## refuter:candidate=DEFECT (angle R2, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-a8afe2971ffcced3e`

### Final Assistant Message

**REFUTED.** The defect collapses on the empirical claim.

The exact repro query returns **0 rows** — there are zero ensemble_snapshots rows where `issue_time > now-24h` but `recorded_at <= now-24h`. Structurally impossible: every ECMWF ingest row has `recorded_at >= issue_time + 8h` (min 8.2h lag observed, avg 21h). Any row with a fresh `issue_time` was necessarily ingested recently, so `recorded_at` is also fresh. The feared exclusion scenario cannot occur.

The `collection_frontier.py` §23-25 mandate governs a diagnostic reporting tool over `source_run`/`readiness_state` — architecturally separate from `_query_metric`'s snapshot-selection job. Using `recorded_at` in `_query_metric` is actually *correct* here: it gates on "was this data ingested recently enough to serve live forecasts," not on model run age. Switching to `issue_time` would be the bug — a stale-cycle backfill written today for a 48h-old model run would then pass the freshness filter.

---

## refuter:candidate=DEFECT (angle R9, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-a8b299e24296ad6e0`

### Final Assistant Message

The investigation is complete. I have all facts needed for the verdict.

Key findings:

1. The code claim is accurate: `_scan_fee_fields` (line 37) only matches keys where `'fee_rate_bps' in lowered_key or key == 'fee'`, and line 66-67 explicitly filters OUT `fee_details.*` paths. So 41 of 42 fills have empty `realized_fee_fields` — confirmed.

2. However, the independent ledger cross-check (cost_basis residuals, lines 85-99) is the refutation: 61 position rows, max absolute residual = **4.7e-05 USDC** (float noise). `cost_basis = price * shares` exactly for every position. If the venue were charging any real fee, `cost_basis` would exceed `price * shares` by the fee amount. It does not.

3. The two fills that DO carry explicit fee data both report **fee_rate_bps = 0** (one via `trade_fact_proof.trade.fee_rate_bps`, one via `trade_payload.fees`). The absence-vs-zero confusion exists in the raw-field scanner but is corroborated: **the fee genuinely is 0** at this venue for these markets.

4. `fee_authority.py` line 93 applies `min(observed, schedule)` — even if `observed` were somehow wrong, it is already capped below schedule. The fail-conservative direction protects against undercounting fees, not overcounting.

5. No real order is blocked or moved by this. The B1/B2 blockers are ws_gap latch and capital_efficiency gate — fee rate does not appear in either path. A fee of 0.0 vs any small positive fee would only make EV slightly worse (smaller edge), which could at most turn marginal candidates from accept to reject — but the dominant rejection reason is `capital_efficiency_lcb_ev`, not fee drag.

The defect is real as a code-quality observation (absent data silently reads as 0), but the **independent accounting cross-check refutes the empirical risk**: the venue is genuinely charging 0, and fixing the field-absence detection would not change the output (`max_bps` would still be 0) and would not move any real order.

---

## angle:Latency / alpha decay / market-lifecycle timing.

**Agent ID:** `agent-a8b39f5693378ab80`

### Final Assistant Message

Now I have a complete picture. Let me do one final check on the `_materialize_stale_gap` self-modification anti-pattern and the `acted_state` persistence implications.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 95):

**Agent ID:** `agent-a8ba72ff6a8a64676`

### Final Assistant Message

The evidence is now conclusive. The factual code claim is confirmed correct: events blocked at `_riskguard_gate` go into `_transient_requeue_reasons` with no persisted record per cycle. The repro numbers hold. However, the critical question is (d): would fixing this move a real order toward a fill?

The current blocker (B1 in the live brief) is the submit latch frozen by `exchange_reconcile_findings` — a separate gate entirely upstream of the RiskGuard block. The RiskGuard block ended (the 57 `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` rows trail off at 16:00 on 06-12, with only 5 more after that). The current no-order state is not caused by RiskGuard blocking — it is caused by B2 (no positive edge, capital_efficiency_lcb_ev rejections dominating) and B1 (latch). Adding per-cycle observability to the RiskGuard transient requeue path writes more records to `no_trade_regret_events` but does not change any submit decision.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-a8bd9ffb6c5fa41f9`

### Final Assistant Message

I now have sufficient evidence. Let me synthesize the kill:

**The candidate's causal claim is REFUTED on multiple grounds:**

1. **The reactor_mode gate is not firing**: `REACTOR_NOT_LIVE` appears zero times in `decision_compile_failures`. The config has `reactor_mode: "live"` (settings.json:77) which IS in `EDLI_PROCESSING_REACTOR_MODES`. The T0-3 rename was for `baseline_bias_correction_enabled`, an unrelated flag — not `reactor_mode`.

2. **The pipeline IS running post-06-12**: 568 `decision_compile_failures` on 06-13, 307 on 06-14, all at stage `TRADE_SCORE` (a late decision stage). Events are passing through the reactor_mode gate, source-truth gate, executable-snapshot gate, and reaching the trade scoring stage — then failing on `capital_efficiency_lcb_ev` (q_lcb=0 after the NO-cripple kill and q_ucb<q_point bugs).

3. **Zero `decision_certificates` is structurally expected**: Certs are only written when `compile_result.status == "VERIFIED"` (both `LIVE` and `NO_SUBMIT` paths). When TRADE_SCORE rejects every candidate, no certs are produced. The "blackout" is the downstream symptom of honest edge rejections, not a mechanical pipeline block. The same 1394 certs on 06-12 correspond to the 32 `edli_no_submit_receipts` + successful submits from that period.

4. **Would fixing it move a real fill?** No — if the cert-dark period were caused by a reactor_mode flag, fixing it would allow events to produce VERIFIED certs. But since compile failures show zero-edge candidates (q_lcb=0 universally), restoring "cert production" without fixing the underlying edge calculation would only produce TRADE_SCORE_NON_POSITIVE certs that still don't fill. The zero-fill root cause is the q_lcb=0 issue (tasks #74 NO-cripple gate, q_ucb<q_point bug), not the pipeline plumbing.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-a8d314f34ae253c70`

### Final Assistant Message

Now I have the complete picture. The cert drought is confirmed real (zero on 06-13+). The defect's causal theory — that the T0-3 config rename created a `reactor_mode` collision causing the gate at reactor.py:1419 to fire — is provably wrong. The running config shows `reactor_mode = "live"`, which IS in `EDLI_PROCESSING_REACTOR_MODES`. The actual symptom is that only `DAY0_EXTREME_UPDATED` events are processed since 06-13 (857 of them, producing no certs), while `FORECAST_SNAPSHOT_READY` events (which previously triggered certs on 06-11/12) are being bulk-ignored (258k) or expired (196k). The mechanism cited in the defect is a misidentified cause.

The cert blackout IS a real defect but the proposed mechanism (reactor_mode gate broken by T0-3 rename) does not hold. The status should be CONTRIBUTING_DEFECT at best given the empirical claim is proven but the causal theory is wrong — but since the mechanism is outright refuted (reactor_mode=live confirmed), this is REFUTED on the proposed mechanism, even though the zero-cert observation is accurate.

The question is: does killing the proposed mechanism (reactor_mode gate) constitute a kill? Yes: the defect says the T0-3 rename created a config collision that froze certs via the REACTOR_NOT_LIVE path. That is false — reactor_mode is live, the gate doesn't fire.

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-a8e6c13529b08c1f3`

### Final Assistant Message

STATE/CONFIG ANGLE — FORENSIC AUDIT FINDINGS

**B1: Submit Gate Frozen (ws_gap_guard allow_submit=False)**

Root cause confirmed. Single unresolved row in exchange_reconcile_findings: finding_id=5bbc2be2, kind=position_drift, context=ws_gap, recorded_at=2026-06-12T22:58Z, resolved_at=NULL. Evidence JSON: exchange_size=0, confirmed_journal_size=17.05, settlement_evidence_class="unconfirmed_redeem_settlement_commands". Token is a swept/redeemed winner where the external-close absorber did not fire.

Mechanism chain (file:line verified):
- src/control/block_adapters/ws_gap_guard.py:31 — allow_submit = bool(ws_summary.get("entry",{}).get("allow_submit", False)) — reads False when any unresolved position_drift row exists
- src/control/block_adapters/ws_gap_guard.py:40-42 — returns BlockState.BLOCKING when allow_submit=False
- src/engine/cycle_runner.py:395-398 — 4-way AND gate; ALL of cutover/heartbeat/ws_gap/governor must be True; one False blocks everything

Resolution path exists: src/execution/exchange_reconcile.py line ~1062 defines _OPERATOR_EXTERNAL_CLOSE_RESOLUTION = "position_drift_operator_external_close_absorbed" — the absorber is the correct fixer. It has not fired. That is the first-bad. Neither b1825c4a07 nor e583af06cd touched this path.

**b1825c4a07 (S7) verdict: TOGGLE-ONLY**

Commit b1825c4a07 (2026-06-08) deleted selector_enabled from opportunity_book.py's to_receipt_dict and removed "selector_enabled"/selector_shadow cache_summary keys from event_reactor_adapter.py. The ΔU marginal-expected-log-utility ranker path was NOT removed. Candidate materialization is intact. This commit does NOT explain B1, B2, or B3.

**e583af06cd verdict: BYTE-IDENTICAL LIVE PATH**

Deleted 6 shadow strategy modules + 4 dead config flags (no_trade_regret_enabled, reports_enabled, forecast_complete_live_enabled, reactor_prune_enabled; also neff_width, james_stein/horse_race/selection_eb, replacement_selection_eb_shrinkage_enabled). All removed flags were config-False at time of deletion. The live trading path is byte-identical post-commit. Does NOT explain B1, B2, or B3.

**B2 (capital_efficiency_lcb_ev + coverage_unlicensed_tail + direction_law + TRADE_SCORE_NON_POSITIVE): NOT this angle**

These are decision-gate rejections, not state/config failures. config/settings.json shows kelly_multiplier=0.125, max_single_position_pct=0.0, edli_live_scope="forecast_plus_day0". No flag change in e583af06cd touches the sizing or direction gate. B2 is a pricing/edge/signal angle, not a state-layer or config-flag defect. Out of scope for this angle.

**B3 (Beijing NO, BELIEF_AUTHORITY_FAULT, stale belief 87 cycles): NOT this angle**

BeliefService staleness is a signal-layer / belief-authority issue, not a config or state-DB issue. No flag removed in e583af06cd, and no setting in config/settings.json, gates the belief refresh path. B3 is out of scope for this angle.

**INV-37 Compliance: No violation found**

connection_pair.py:225 explicitly documents "Cross-DB writes must use get_forecasts_connection_with_world SAVEPOINT (ATTACH atomicity), not two-independent-connection commits (INV-37)." canonical_write.py's commit_then_export operates within a single connection with SAVEPOINT semantics. db_writer_lock.py SQLITE_CONNECT_ALLOWLIST correctly gates exchange_reconcile.py as read_only_ro_uri on zeus-forecasts. No INV-37 violation in read files.

**Concurrent-Edit Hazard: Structural gap, not proximate cause**

db_writer_lock.py is Phase 0 only — production callers not wired to flock (Phase 1+ retrofit, documented in the file). No fcntl serialization is active. This is a structural risk for concurrent agent edits but is NOT the cause of B1/B2/B3.

**boot_sha drift (99050c14 vs ac7a7558): In grace, not a blocker**

Documented as in grace in the live evidence. Not an active gate.

**Summary for orchestrator**

This angle's contribution to the blocker set is entirely B1. The proximate cause is the external-close absorber regression (task #31): the 5bbc2be2 position_drift finding was recorded 2026-06-12T22:58Z and never resolved, freezing ws_gap_guard → cycle_runner 4-way AND gate → all submissions blocked. Neither S7 (b1825c4a07) nor the gate-mass collapse (e583af06cd) caused this. B2 and B3 belong to other angles. INV-37 is clean. Concurrent-edit risk is structural but latent.

---

## angle:Friction / fees / slippage / threshold accounting.

**Agent ID:** `agent-a8f37bac4eac0670e`

### Final Assistant Message

Here are the findings in full:

---

SLIPPAGE (n=29 Zeus edli_exec_cmd fills, zeus_trades.db execution_fact):

SQL: `SELECT AVG(fill_price - submitted_price) FROM execution_fact WHERE decision_id LIKE 'edli_exec_cmd%' AND terminal_exec_status='filled'` — result is 4.3e-18 (machine zero), range [-1.1e-16, +1.1e-16]. fill_quality = 1.0 on all rows. Latency is 25-40 seconds typical, two outliers at 1701s and 8139s. Verdict: zero price slippage. All orders fill at the submitted limit price — these are maker fills.

REALIZED FEES (undetermined — data gap):

`edli_live_profit_audit` has 714 rows; 31 have avg_fill_price populated. The `fees` column is 0.0 for one row and NULL for 30 others. `venue_trade_facts.fee_paid_micro` is NULL for all 41 CONFIRMED rows. Realized fees are not captured in the DB at all.

MODELED FEES (from source):

Fee model is `phi = 0.05 * price * (1-price)` per share (src/strategy/fees.py, src/contracts/execution_price.py). Maker orders pay zero. The ems fee_details_json reports base_fee=1000 bps (0.10) from the CLOB venue — the code uses 0.05. For the 29 fills, total notional is $198.85; if all were taker the fee burden would be $2.52 (1.27% of notional avg). As maker fills: $0. The model is accurate for maker and optimistic only if a fill crosses as taker without being flagged.

B2 LONGSHOT FRICTION VERDICT:

Friction does not kill the KL and Tel Aviv longshots. At p=0.008 (KL 35C+): taker fee per share = 0.000397, fee as fraction of notional = 4.96%, net ev/$ = 3.85 (down from 3.90). At p=0.001 (Tel Aviv): taker fee/notional = 4.99%, net ev/$ = 26.45. Both remain massively positive after the maximum possible friction.

The actual killer is a hardcoded price floor gate. The top rejection reason in no_trade_events (1058 rows) is `strategy_economic_floor` with pattern `STRATEGY_ENTRY_PRICE_BELOW_LIVE_FLOOR(0.001<=0.05)`. This is `min_entry_price=0.05` (src/strategy/strategy_profile.py:120, default=0.05). The gate is policy, not economics. The `allow_ultra_low_tail=False` flag bypasses it — notably the 0.016 buy_yes fill on 2026-06-10 (66.25 shares, $1.06 notional) executed, meaning that strategy profile had the flag enabled at that time.

Munich 26C+ (q_lcb=0.0000, price=0.001) is an honest no-edge case — negative raw edge before any fee.

KEY OPEN RISK: The fee model discount at low prices (p*(1-p) near-zero at extremes) works in Zeus's favor for longshots, but the 5% rate in code vs 10% raw CLOB rate is unvalidated against actual venue invoices. Realized fees are untracked, so the cost model's accuracy against real execution cost is unmeasurable from current data.

---

## refuter:candidate=DEFECT (angle R4, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-a91349c822191963d`

### Final Assistant Message

REFUTED. Two independent kills found:

1. **Flag is True in live settings.** The repro script (with corrected dict access) returns `True` — the flag is enabled. The premise that it "defaults False" in the live system is factually wrong.

2. **SHADOW_ONLY is a schema constraint, not a defect.** The `forecast_posteriors.trade_authority_status` column has a hard `CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY'))` enforced at the DB level (`v2_schema.py:378-379`). All rows being `SHADOW_ONLY` is correct, expected behavior. The live-eligibility gate is a completely separate mechanism: `_replacement_q_mode_live_eligibility()` in `event_reactor_adapter.py:9350` reads `provenance_json.replacement_q_mode`, not the column. The DB since 06-12 shows 1,097 `FUSED_NORMAL_FULL` and 62 `FUSED_NORMAL_PARTIAL` rows — the chain is producing live-eligible rows correctly. The actual submission blocker remains B1 (the ws_gap latch phantom).

---

## agent-a92a267fe819d8e2e

**Agent ID:** `agent-a92a267fe819d8e2e`

### Final Assistant Message

Now I have enough ground truth to deliver the completeness critique. Here are the specific gaps — what is MISSING, what claims are unverified, and the next probe for each:

---

**GAP 1 — The tail gate label the synthesis names does not exist in the live system.**

The synthesis repeatedly names `coverage_unlicensed_tail` (live_admission.py:141, :152-176, :141) as the dominant reject reason. The actual gate labels in `no_trade_events` are `strategy_economic_floor` (1,058), `ultra_low_price_not_authorized` (373), and `confidence_band_insufficient` (379). `coverage_unlicensed_tail` is either a renamed gate, a log-level label not persisted to the table, or fabricated by a prior agent. The synthesis's entire architectural claim — that a licensing check blocks +EV tail candidates — may be attributed to the wrong gate. **Next probe:** `grep -n "coverage_unlicensed_tail" src/execution/live_admission.py src/execution/event_reactor_adapter.py` to confirm whether this string exists at all in live code, and map the actual gate at line 141 to its persisted `reason` string.

**GAP 2 — The EMOS shadow ledger shows `emos_q` populated (6,875 of 10k recent entries) yet the synthesis claims EMOS fires 0 times due to missing license file.**

The synthesis states `emos_ci_license.json ABSENT -> load fail-opens to {} -> every city unlicensed -> emos_ci_k_cov returns None -> override early-returns`. But 6,875 of the last 10k ledger entries have `emos_q not None` and `served='emos'`. This is a direct contradiction: either EMOS is computing and serving (the ledger proves it), or `served='emos'` means something different from `q_lcb_calibration_source='EMOS_ANALYTIC'` in receipts. The two can coexist if the shadow ledger records EMOS-computed values that are then downgraded to FORECAST_BOOTSTRAP at the licensing step — but this path is unverified. **Next probe:** Read `emos_ci_license.py` load function and `event_reactor_adapter.py` ~line 12003-12013 to confirm whether `emos_q` in the ledger means "computed but then rejected by license check" or whether the license file is NOT actually required for shadow-ledger population.

**GAP 3 — The 454 cheap-tail buy_yes receipts ALL pass capital_efficiency (q_lcb_5pct > c_cost_95pct in 454/454 cases) yet none submitted.**

The synthesis claims these are killed by `coverage_unlicensed_tail`. But `q_lcb_calibration_source` is NULL for 439 of the 454 — not `FORECAST_BOOTSTRAP`. A NULL source is a different failure mode than an unlicensed source. Something upstream of the licensing gate is producing receipts with null source stamps, which may mean the receipt path itself is broken for cheap tail candidates before they ever reach a licensing check. **Next probe:** Sample 5 of the 454 cheap-tail buy_yes receipts by receipt_id; trace the receipt_json field to see what gate recorded the rejection reason and whether a licensing check appears.

**GAP 4 — The mid-band (0.2-0.6 cost) receipt count (3,285) is not zero and exceeds the synthesis's claim that this class "collapsed to n=1".**

The synthesis frames buy_yes mid-band as existentially absent. The receipts table shows 3,285 receipts with cost 0.2-0.6 (direction unfiltered). This may be dominated by buy_no, but the n=1 claim is stated for recent fills, not for all receipts. The mid-band edge verdict ("not profitable alpha") rests on whether q_lcb>cost holds here. **Next probe:** `SELECT direction, AVG(q_lcb_5pct - c_cost_95pct), COUNT(*) FROM edli_no_submit_receipts WHERE c_cost_95pct BETWEEN 0.2 AND 0.6 GROUP BY direction` — confirm the q_lcb distribution in the mid-band to verify the "base-rate only" verdict.

**GAP 5 — B1 self-clear time (06-14T06:01:47) is unverified against the current latch state.**

The synthesis (via R13) asserts B1 resolved autonomously. The live evidence at session start says B1 was frozen since 06-12T22:58. The self-clear claim names a specific timestamp but the current `allow_submit` state has not been re-read in this session. If the latch re-froze (another phantom finding arrived), the "B1 clear" verdict is stale. **Next probe:** `SELECT allow_submit, frozen_at, frozen_reason FROM <submit_latch_table>` (or equivalent) to confirm the latch is currently open and no new exchange_reconcile_findings are blocking submission.

**GAP 6 — The opportunity book selector ON/OFF gate deletion (commit b1825c4a07) materialization status is stated as "NOT confirmed" in live evidence but no angle probed it.**

The live evidence explicitly flags this as unresolved: "one path kept — NOT confirmed whether materialization survived." None of R1-R16 appear to have probed this. The opportunity_book.py and opportunity_selector.py exist and are imported by event_reactor_adapter.py, confirming code presence. But materialization = whether `build_family_opportunity_book` is actually called on every reactor cycle and its output persisted. **Next probe:** `grep -n "build_family_opportunity_book" src/engine/event_reactor_adapter.py` and check call-site guard conditions — confirm there is no residual ON/OFF flag or import-guard that silently skips the build.

**GAP 7 — `confidence_band_insufficient` (379 rejections) and `model_conflict` (336) are the 2nd and 3rd largest rejection classes but neither is mentioned in the synthesis.**

These are not small-print. 379 + 336 = 715 rejections that aren't covered by any of the 16 angles. The `confidence_band_insufficient` traces show `yes_ci_lower_nonpositive` — meaning q_lcb at the CI lower bound is ≤0, which kills the candidate before licensing. If calibration is systematically producing zero-floor LCBs on precisely the cheap tail bins that would otherwise be +EV, this is a third independent mechanism killing the same opportunity class, upstream of the licensing gate the synthesis focuses on entirely. **Next probe:** Cross-tabulate `confidence_band_insufficient` rejections by cost bin — confirm whether this rejection class concentrates at cost < 0.05 (tail) or is distributed across the cost range.

---

## refuter:candidate=DEFECT (angle R5, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-a9316254923588ca1`

### Final Assistant Message

The evidence is now complete. Let me assess the four kill criteria:

**(a) Is the ideal naive about a real constraint?** No - there's no venue/settlement/latency constraint preventing writing `q_remaining_day` to receipts. The fix is genuinely a one-line addition to the receipt write path.

**(b) Does existing code already handle it?** Partially - but in the WRONG direction. `settings.json` has `day0_remaining_day_q_enabled: true`, meaning the flag is ON and the system is running in "remaining_day" mode (Source 2). However the adapter's join key (`opportunity_events.event_type = 'DAY0_EXTREME_UPDATED'`) is broken - receipts link to `FORECAST_SNAPSHOT_READY` events, not DAY0 events. Neither `q_remaining_day` nor `q_shadow_remaining_day` are written anywhere in source. So no, existing code does NOT correctly handle the shadow comparison.

**(c) Is the empirical claim under-powered?** No - verified with direct DB queries: 2405 receipts since 06-04, 0 with `q_remaining_day`, 0 with `q_shadow_remaining_day`, 0 with `_edli_day0_q_mode='remaining_day'` via join. The INSUFFICIENT_N is accurate.

**(d) Would fixing it move a real order?** This is where the defect's impact classification matters critically. The retired_comparison_tool is a read-only analytics cron job at 09:20. Its `INSUFFICIENT_N` verdict is NOT read by any execution path (engine, riskguard, control, supervisor_api). The `day0_remaining_day_q_enabled` flag gates which q-computation the reactor uses (full-day vs remaining-day), NOT whether orders submit. The retired_comparison_tool exists to evaluate whether to PROMOTE the flag - but the flag is already ON in settings.json. So fixing the retired_comparison_tool's data gap would improve the analytics/evidence for a future flag change, but since the flag is already on, no order is being blocked by INSUFFICIENT_N.

The defect is real but its impact is purely on the promotion-evidence analytics organ, not the order flow. The flag it evaluates (`day0_remaining_day_q_enabled`) is already enabled. The submission blockers (B1 ws_gap latch, B2 capital_efficiency gate) are entirely separate.

---

## angle:Decision-gate liveness — candidate → intent → receipt/command**

**Agent ID:** `agent-a94e851a435fc1ff2`

### Final Assistant Message

Now I have enough grounded evidence. Let me compile the findings.

---

## refuter:candidate=DEFECT (angle R5, tag MISSING_CAPABILITY, impact 90):

**Agent ID:** `agent-a973502ee6881532e`

### Final Assistant Message

I have enough to classify this. Let me summarize the findings:

**Repro confirmed**: zero hits of `settlement_attribution`, `retired_comparison_tool`, `promotion_readiness` in `candidate_evaluation.py` and `reactor.py`. The loop is genuinely open.

**Key kill attempt results**:

(a) **Naive about a real constraint?** The `calibration/AGENTS.md` line 49 explicitly states: "Shadow instrumentation: blocked_oos.py, effective_sample_size.py — these collect evaluation facts but do NOT gate live execution... Promotion of any shadow metric to a live blocker requires: 30+ days of parallel data, explicit operator approval, and a governance packet." This is an intentional governance constraint, not naivety — the system architects explicitly chose this to prevent premature feedback contamination of q_lcb.

(b) **Already handled?** No. Code confirms the loop is open — skill attribution runs as a daily cron (`main.py:1401-1402`) and writes analytics, never feeds q_lcb. `emos_q_builder.py` and `platt.py` have no skill/shadow references.

(c) **Empirical claim under-powered?** The claim of 23 attributed / 9 SKILL_WIN (n=23) IS genuinely too small for reliable feedback — this is an honest kill: n=23 is not statistically adequate to adjust a probability estimate. The INSUFFICIENT_N in `platt_oos_resolver.py` reflects this same principle: OOS data below threshold returns None (identity map). Closing this loop on n=23 would likely inject noise, not signal.

(d) **Would fixing it move a real fill?** The actual live blocker is B1 (submit latch frozen by phantom reconcile row) and B2 (q_lcb suppressed below price by calibration, not by lack of skill attribution). Even if skill win-rate were somehow fed back to q_lcb, the candidates cited (KL 35C+ q_lcb=0.0392 vs price=0.0080) already have positive edge — they're being blocked by `capital_efficiency_lcb_ev` which requires q_lcb > price after cost, and they already satisfy that (3.90x EV/dollar). The real fill blockers are B1 (submit gate phantom) and the warm-lane/calibration issues, not the open evidence loop.

The defect is **true as a structural observation** but: (1) it is an intentional governance design with explicit 30-day OOS requirement before promotion; (2) the sample size is too small for reliable feedback (n=23 → INSUFFICIENT_N territory); (3) it would NOT unblock the live submit gate or move a real fill (B1 is the blocker; B2 candidates with positive EV are not failing due to skill attribution absence).

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-a99a535e1a51486f6`

### Final Assistant Message

I'll investigate coverage holes in the contract universe — specifically whether settled families have active tradable markets with missing executable_market_snapshot coverage or broken bin-identity joins.

---

## angle:Point-in-time data lineage / look-ahead leakage governance.

**Agent ID:** `agent-a9b7352f9318069f5`

### Final Assistant Message

I now have a complete picture. Here is the full report.

---

**ANGLE: Point-in-time data lineage / look-ahead leakage governance**

---

**1. What this angle ACTUALLY does (mechanism)**

These seven files form a three-layer governance stack:

Layer A — temporal naming and authority (`temporal_provenance.py`, `source_time.py`). `temporal_provenance.py` declares per-family required-field sets and an allow-list of authority tiers; `source_time.py` defines a `TemporalPolicy` dataclass loaded from `config/source_release_calendar.yaml`, giving each source a safe-fetch lag, freshness ladder (degraded / expired as ratios of `max_source_lag_seconds`), and `partial_policy`. The live-reader gate (`live_reader_requires_provenance`) is permanently OFF behind `ZEUS_FRONTIER_READINESS_GATE`. Nothing in this layer writes or filters rows at decision time; it supplies constants the downstream layers should consult.

Layer B — fitted-artifact lineage governance (`calibration_transfer_policy.py`, `replacement_forecast_calibration_quarantine.py`). The transfer-policy file is the most active: it gates whether an ECMWF Opendata forecast may borrow a TIGGE-trained Platt model for live promotion. Its legacy path (`evaluate_calibration_transfer_policy`) is a pure string-map returning `LIVE_ELIGIBLE` iff `live_promotion_approved=True`. Its evidence-path (`evaluate_calibration_transfer_policy_with_evidence`) is behind `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` (default off); when on, it re-derives the OOS Brier score from `calibration_pairs` and validates `platt_models`. `replacement_forecast_calibration_quarantine.py` is a pure classifier that blocks certain calibration artifacts from being used as authority for replacement forecast products.

Layer C — selection-stage shrinkage (`selection_shrinkage.py`). Provides EB shrinkage (`eb_shrink_edges`) and a posterior log-utility license (`select_license`). As of 2026-06-13 the EB flag was removed and `authority_on=False` is hardcoded; it runs in shadow-only mode (event_reactor_adapter.py:2806-2812).

Layer D — freshness routing (`freshness_registry.py`) and expiring assumption (`expiring_assumption.py`). The registry centralises per-source staleness thresholds; `expiring_assumption.py` is a standalone dataclass for self-expiring hardcoded constants.

---

**2. Every defect — file:line + reproduction + blocking verdict**

**DEFECT-1 (SUBMISSION BLOCKER — KILL REAL +EV CANDIDATES)**
`calibration_transfer_policy.py:604` — `return False` on any row whose `recorded_at` exceeds `evidence_time`, inside `target_transfer_cohort_evidence_valid`. This is called inside the legacy flag-OFF path indirectly: no — wait, the `_with_evidence` function's full evidence-recompute path (`target_transfer_cohort_evidence_valid`) only runs when `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true`. Under flag OFF, the legacy path at line 776 is used. So this defect only fires when the flag is on.

However there is a subtler form that IS live: `source_platt_transfer_evidence_valid` (lines 466-469) — if `fitted_at > evidence_time` OR `recorded_at > evidence_time` it returns `False`. These are also inside the flag-gated path. **DEFECT_NO_CURRENT_IMPACT** (flag OFF).

**DEFECT-2 (ACTIVE SUBMISSION BLOCKER)**
`calibration_transfer_policy.py:276-278` — `_transfer_economics_valid` returns `False` if `brier_diff` is not math.isclose to `brier_target - brier_source` (both with `rel_tol=1e-9, abs_tol=1e-9`). This cross-check requires exact floating-point reproducibility of a value that was computed externally and stored as a string in the DB. If the `validated_calibration_transfers` row's `brier_diff` was stored with any rounding (e.g. Python `repr()` at 17 significant figures vs. a script that truncated to fewer), `_transfer_economics_valid` fails closed. This blocks the `_with_evidence` path returning `LIVE_ELIGIBLE`. **Impact: SUBMISSION BLOCKER in the evidence path (flag ON); DEFECT_NO_CURRENT_IMPACT under flag OFF.**

**DEFECT-3 (LEAKAGE — kills +EV in the OOS split)**
`calibration_transfer_policy.py:340` — `select_time_blocked_transfer_pairs` takes the chronologically LAST `ceil(N * 0.2)` groups as the holdout. The ordering key is `forecast_available_at` — correct. However, `target_transfer_cohort_evidence_valid` fetches ALL rows with `causality_status='OK'` and `outcome IN (0, 1)` (line 580), then passes them to `select_time_blocked_transfer_pairs` which re-derives the split. The split uses `max(1, ceil(...))` so with only 1 or 2 groups, the entire dataset becomes the holdout (line 340: `holdout_group_count = max(1, ...)`). With N=1 group, `ceil(1*0.2)=1`, so 100% of rows are the holdout. With N=5, 1 group is held out. This means the OOS cohort has zero training-set filtering — it IS the data the Platt model was fit on when the model used those pairs for training. The `calibration_pairs` query at line 566 does NOT filter out training-set pairs at all; it just requires `training_allowed=1`. The Platt was presumably fit on 80% of this same `training_allowed=1` population. The held-out 20% (chronological tail) is genuinely OOS, but any pair with `recorded_at` inside the training window can reach the held-out split (only `recorded_at <= evidence_time` is checked at line 604, not whether it was in-sample for the model). **This is correct as written** — it uses the chronological tail. The split appears sound for ECMWF vs TIGGE; no leakage. DEFECT_NO_CURRENT_IMPACT.

**DEFECT-4 (DEAD CODE / OVER-BUILT GATE)**
`expiring_assumption.py:24-40` — `ExpiringAssumption` declares a `kill_switch_action: str` field that either raises `RuntimeError` ("halt_trading") or returns a fallback. The `is_valid` comparison at line 29 uses `.days` on a `timedelta`, which truncates to whole days. A `max_lifespan_days=7` assumption verified 7 days and 23 hours ago passes; verified 8 days ago fails. The off-by-one on `.days` vs. `.total_seconds()/86400` introduces a ±1 day slop. More critically, there is zero live usage of `ExpiringAssumption` anywhere in the codebase (confirmed by grep — it was introduced as scaffolding but never instantiated on the live trading path). **DEFECT_NO_CURRENT_IMPACT** — dead code.

**DEFECT-5 (SEMANTIC CONTRADICTION — live-reader gate permanently off)**
`temporal_provenance.py:90-92` — `live_reader_requires_provenance()` returns `True` only when `ZEUS_FRONTIER_READINESS_GATE=1`. This gate was described in the module docstring as "changes no runtime behavior on its own." There is no code path in the codebase that checks this flag and acts on it at decision time (confirmed by grep — it is tested in unit tests only). The PR7 schema migration is "operator-gated and deferred." The entire row-level provenance enforcement layer is therefore **permanently inert in production** — a governance declaration with no runtime enforcement arm. This does not directly block submissions, but it means any row with a wrong `source_issue_time` or mismatched `data_version` passes into the decision engine undetected. **DEFECT_NO_CURRENT_IMPACT on the submission path**, but it means the stated "live-reader provenance gate" does not exist at runtime.

**DEFECT-6 (OVER-BUILT / REDUNDANT GATE)**
`freshness_registry.py:109-121` — `SOURCE_THRESHOLDS` lists two DYNAMIC sources (`heartbeat_status`, `executable_snapshot`) that must receive `override_threshold_seconds` at call time. The registry raises `ValueError` if not supplied. The production call site for `executable_snapshot` is in `polymarket_v2_adapter.py` (per the docstring). If a callsite migrates to the registry but forgets the override, it raises at runtime and the decision is aborted — a new failure mode introduced by centralisation. This is a latent risk, not a current blocker. **DEFECT_NO_CURRENT_IMPACT** — but the DYNAMIC sentinel is an over-engineering pattern (the threshold could just be passed by the caller without the sentinel machinery).

**DEFECT-7 (ACTIVE SUBMISSION BLOCKER — flag-gated EB shrinkage permanently shadow)**
`event_reactor_adapter.py:2811` — `authority_on=False` is hardcoded after the 2026-06-13 gate-mass collapse. The task comment says "the EB-shrinkage decision-replacement flag was REMOVED." This means `selection_shrinkage.py`'s `select_license` is called in shadow, and the live gate is the BH/FDR pass. As documented in the module docstring and code comments (`event_reactor_adapter.py:9854/9876`), the live BH/FDR gate consumes degenerate p-values in {0, 1} — every 0 passes, every 1 fails — which is a literal no-op multiplicity correction. Any candidate that fails `q_lcb > price` gets p=1.0 and is rejected; any that passes gets p=0.0 and passes BH. The "correction" never changes an outcome. **DEFECT_NO_CURRENT_IMPACT** on a +EV candidate (a true +EV candidate has p=0.0 and passes BH), but it means the EB anti-winner's-curse shrinkage is entirely bypassed, so the raw q_lcb is the decision variable with no shrinkage. This is by design currently, not a blocking defect.

**DEFECT-8 (MISSING EMBARGO — potential calibration look-ahead)**
`calibration_transfer_policy.py` — the `calibration_pairs` query (lines 554-589) filters `outcome IN (0, 1)` but does NOT filter `forecast_available_at < decision_time`. The `recorded_at <= evidence_time` check (line 604) verifies the row was written before the evidence was evaluated, but `forecast_available_at` could be a future date relative to when the Platt model was fit. Specifically: a calibration pair can have `forecast_available_at = T` and `recorded_at = T + epsilon` (written at settlement). If the Platt model was fit at time `T_fit < T`, the pair's outcome was unknown at fit time — correct OOS. However if `forecast_available_at < T_platt_fit`, the pair's outcome was KNOWN at fit time, making it in-sample. The check `fitted_at > evidence_time → return False` in `source_platt_transfer_evidence_valid` (line 466) prevents a model fit AFTER the evidence evaluation, but does NOT prevent a model fit BEFORE but trained on pairs that have `target_date` >= model fit date (i.e. the model was trained on data it did not yet have at fit time). **This is a latent leakage path** in the evidence re-derivation if the `platt_models` table was populated with future-outcome rows, but it is flag-gated off and DEFECT_NO_CURRENT_IMPACT on the live submission path.

---

**3. What is notably ABSENT vs. what a sound system needs**

- The `ZEUS_FRONTIER_READINESS_GATE` is declared but never enforced at runtime. A sound system needs the `live_reader_requires_provenance()` return value wired into the actual data-read path (the forecast reader, observation reader) — not just tested in unit tests. Currently the provenance schema declarations are decorative.

- `source_time.py` provides `safe_fetch_not_before()` and explicitly documents that cycle-pair overrides are NOT surfaced (line 219: "06Z/18Z short cycles vs 485min default — Consumers needing cycle-aware safe-fetch must read calendar.entries directly until PR2"). PR2 is referenced but there is no corresponding file in this angle's scope. This means the safe-fetch gate is conservative (later than needed) for some cycles but the actual time-aware gate is not implemented.

- `selection_shrinkage.py` provides `tweedie_shrink` but it is never called from the reactor (only `eb_shrink_edges`). The D3 Tweedie upgrade is implemented but orphaned.

- No embargo field in `calibration_pairs` to mark rows that were in-sample for a specific model fit. The OOS split is purely chronological, relying on the caller to ensure no future outcomes were observed at fit time.

- `ExpiringAssumption` exists but has zero production instantiations. The stated expiry-governance pattern is undeployed.

---

**4. Provenance verdicts**

`src/data/temporal_provenance.py` — Created 2026-05-24, authority PR7 + operator refactor spec §5/§7. Schema declarations are current; the live-reader gate is deliberately deferred and documented as such. **CURRENT_REUSABLE** as a constants/validation library. The flag-OFF runtime posture is by design.

`src/data/source_time.py` — Created 2026-05-24, same authority. `TemporalPolicy` is current; `safe_fetch_not_before` is documented as cycle-unaware pending PR2. **CURRENT_REUSABLE** for the stated scope (PR1 only).

`src/data/replacement_forecast_calibration_quarantine.py` — No header date. Authority: internal docstring describes B0 lineage quarantine for replacement forecast products. The module is a pure classifier; it operates only when `evaluate_replacement_forecast_calibration_quarantine` is explicitly called. No callers found on the live trading path (caller search: not in evaluator.py or event_reactor_adapter.py). **CURRENT_REUSABLE** as a classifier; effectively dormant on the live path.

`src/data/calibration_transfer_policy.py` — Last touched ~2026-05-07 (legacy bridge) / 2026-05-05 (evidence path); authority PRs #55/#56/#64. The legacy path is the live path (flag OFF). The evidence path is elaborate but off. The `_transfer_economics_valid` floating-point equality check is fragile. **CURRENT_REUSABLE** for flag-OFF path; the evidence path is **QUARANTINED** (flag-gated, untested in live conditions, contains the fp-equality defect).

`src/strategy/selection_shrinkage.py` — Created 2026-06-13, authority A2/D3/Fable consult. Mathematics is sound. `authority_on=False` in the caller means it is shadow-only. **CURRENT_REUSABLE** as a math library; the live wiring is explicitly deferred.

`src/contracts/expiring_assumption.py` — No date header, no authority basis. No production callers. Off-by-one in `is_valid` (`.days` truncation). **DEAD_DELETE** or at minimum QUARANTINED — dead code with a latent bug.

`src/contracts/freshness_registry.py` — Created 2026-05-20, authority PHASE_2_ULTRAPLAN §6.2. The registry is wired into `evaluator.py` for `day0_executable_observation`. The DYNAMIC sentinel pattern introduces a new raise-on-miss failure mode. **CURRENT_REUSABLE**; the DYNAMIC sentinel is an over-engineering pattern but not broken.

---

**Summary of submission-blocking vs. +EV-killing findings:**

None of the defects in this angle can on their own explain a 1.5-day no-order window under the current flag posture. The live path uses: legacy calibration transfer (string-map, flag OFF → LIVE_ELIGIBLE if `live_promotion_approved=True`), BH/FDR with {0,1} p-values (no-op multiplicity correction that passes any q_lcb>price candidate), and EB shrinkage shadow-only. The temporal provenance gate is permanently off. The freshness registry is wired only for `day0_executable_observation`. The most significant active issues on this angle are structural (deferred gates, dead code) not operational blockers on the current code path.

---

## angle:Latency / alpha decay / market-lifecycle timing.

**Agent ID:** `agent-a9ebc384be420a261`

### Final Assistant Message

Now I have enough signal. Let me check one more critical thing — whether `buy_no` direction is actually screened in `enqueue_live_redecisions` or only the caller `screen_entry_redecisions`.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-a9f063408a4995514`

### Final Assistant Message

## Decision-Gate Liveness: Candidate to Order or Decline

---

### 1. Objective

Every candidate that clears probability generation must produce a timestamped artifact — either an executable order or a written decline with the exact reason. The system makes money when this pipeline is never silently empty: if there is a real edge, an order exists; if there is no edge, the reason is recorded and auditable. The failure we are guarding against is not wrong trades — it is candidates that evaporate between belief and action, leaving no trace of whether edge existed or not.

---

### 2. Ideal Mechanism

The pipeline has exactly four steps. No step is optional; every candidate traverses all four.

**Step 1 — Edge computation (single number)**
Compute `edge = q_lcb - executable_price`, where `q_lcb` is the calibrated lower bound on your win probability (the pessimistic end of your credible interval, not the point estimate) and `executable_price` is the best resting quote you can cross right now, inclusive of all costs: taker fee, bid-ask half-spread as slippage, and any settlement-delay carry. Edge is signed. If you are considering selling (the NO side), invert: `edge = (1 - q_lcb_for_no) - executable_no_price - costs`. One number. One sign.

**Step 2 — Admission gate (binary)**
If `edge > threshold_E` (a fixed minimum edge in probability units, e.g. 0.02), the candidate is admitted. If not, it is declined with reason `EDGE_INSUFFICIENT: edge={value}, threshold={threshold_E}`. The threshold is a single constant derived from your historical calibration error and the minimum Kelly fraction worth trading. It is not a per-market or per-bin parameter.

**Step 3 — Sizing (deterministic function)**
For admitted candidates, size = `f(edge, bankroll, max_loss_per_trade)`. The function is fractional-Kelly: size proportional to `edge / (1 - q_lcb)`, capped at a fixed fraction of bankroll, and floored at one minimum lot. If the floor exceeds the cap, decline with reason `SIZE_INFEASIBLE: kelly_size={kelly}, lot_floor={lot}`. This is the only place sizing logic lives; it does not appear at the gate.

**Step 4 — Order emission (write or decline)**
Emit a limit order at the executable price (not better — you want fill certainty, not queue risk) for the computed size, or record the final decline. Either way, write a receipt: `{candidate_id, timestamp, decision, edge, size_or_reason}`. No candidate exits without a receipt.

---

### 3. Invariants

- **I1 (Coverage):** Every candidate that enters has a receipt before the next market-open window closes.
- **I2 (Monotonicity):** A candidate with higher edge on identical inputs never produces a smaller order than a lower-edge candidate on the same bankroll.
- **I3 (Cost completeness):** The `executable_price` used in edge computation includes every cost item. If a new fee is introduced, it is added to the cost term, not to a new gate.
- **I4 (Single truth):** The receipt is the authoritative record. Downstream reporting reads receipts, never reconstructs decisions from logs.

---

### 4. Failure Modes That Silently Destroy Edge

- **Ghost candidates:** A candidate is generated but never reaches the gate (dropped in a queue, filtered before edge is computed, exception swallowed). Edge exists but no order is ever emitted. The system looks quiet; it is actually broken.
- **Stale quote:** The `executable_price` is fetched once and cached. By the time the order emits, the book has moved. Edge was real at fetch time, zero or negative at fill time. Cost completeness cannot save you from a stale snapshot.
- **Asymmetric decline logging:** Orders are written to the trade log; declines are dropped. You cannot distinguish a period of genuine no-edge from a period of broken pipeline. Both look like zero activity.
- **Threshold drift:** `threshold_E` is tuned per-market, per-city, per-session. It becomes a parameter forest that can be set to avoid trading entirely without any single gate being obviously wrong.

---

### 5. Three Hardest Design Decisions

**Decision 1 — q_lcb vs. point estimate.** Use the lower bound. A point estimate that beats the price tells you the expected sign is positive; the lower bound tells you the pessimistic case still clears costs. In a market where base rates are already in the price, the distribution of your error matters more than its center. Call: always use `q_lcb`.

**Decision 2 — Limit at executable vs. mid.** Posting at mid risks never filling and generating phantom edge. Crossing at executable sacrifices a tick but produces a real fill or a real rejection. Call: limit at the best resting quote you computed, not mid. Fill certainty beats marginal price improvement when your edge is already after costs.

**Decision 3 — Single threshold vs. per-candidate.** A per-candidate threshold that reflects uncertainty about your model's reliability for that specific city/bin sounds principled but introduces a free parameter that can be tuned post-hoc to avoid any trade that loses. Call: one global `threshold_E`, updated on a weekly cadence from calibration data, never touched intra-day.

---

## angle:Forecast & observation acquisition / freshness.

**Agent ID:** `agent-a9f0dd40408c023e9`

### Final Assistant Message

## Multi-Source Ingestion Design: Same-Day Temperature Forecast

---

### 1. Objective

The ingestion layer must deliver, at every decision point, the lowest achievable uncertainty on the conditional distribution P(T_max | all observable information up to now). The downstream bet wins only when the model's credible interval places probability mass correctly relative to the executable price. Staleness is the primary enemy: a 6-hour-old ensemble that missed a front passage has larger variance than a 1-hour NWP update, but the system cannot know this unless freshness is tracked and penalized explicitly. The ingestion layer's job is to ensure no decision is made against stale evidence, and that each source's contribution to the final distribution is weighted by its current skill, not its historical average skill.

---

### 2. Ideal Mechanism

**Source taxonomy and cadence:**

- **Global ensembles (GFS-ENS, ECMWF-ENS):** Run 4x/day (00Z, 06Z, 12Z, 18Z). Primary source for horizon > 18 hours. At same-day decision time (say, 06-12 local), use the most recent cycle whose initialization is within 6 hours of decision time. Never use a cycle older than 12 hours — staleness penalty grows faster than model spread shrinks.

- **Regional high-res deterministic (NAM 3km, HRRR):** Run hourly (HRRR) or 6-hourly. Primary source for 0-12 hour horizon. HRRR is the workhorse at same-day: ingest every cycle, compute T_max from the hourly output trajectory over the local afternoon window (typically 14Z-22Z local), extract empirical CDF across the output hours.

- **Surface observations / ASOS METARs:** Report every hour, sometimes every 20 minutes. Not a forecast — they anchor where the temperature curve IS right now. Use as a Bayesian update on the current trajectory: if the 10Z obs is already at 28C and the model max was 31C, shrink the upper tail.

- **Nowcast blend (Unidata/vendor):** Some providers blend obs + short-range NWP into a running analysis. Ingest this at 15-minute cadence if available. Treat as corroborating obs, not independent forecast.

**Freshness definition:** Each source datum carries `(cycle_init_time, valid_horizon, ingest_timestamp)`. Staleness = `wall_clock_now - cycle_init_time`. Hard cutoffs: global ensemble > 12h stale → excluded from distribution; HRRR > 2h stale → downweighted 50%; obs > 90 min → excluded from trajectory anchor.

**Weighted BMA:** The final P(T_max) is a Beta-mixture (or kernel density) across sources, weighted by `w_i = base_skill_i * freshness_decay_i * recent_bias_correction_i`. Recent bias correction is a rolling 30-day CRPS ratio on same-city, same-season outcomes.

---

### 3. Invariants

- Every source datum used in a live decision has a verified `ingest_timestamp` within its freshness window. No datum is assumed fresh; freshness is asserted at query time.
- The distribution always integrates to 1.0 over all K bins. No silent mass leakage from clipping or discretization.
- If zero sources pass freshness checks, the system emits NO DECISION, not a stale fallback.
- Obs anchor updates are applied after the forecast blend, never before (obs do not reprice the prior, they condition it).

---

### 4. Silent Failure Modes

- **Ghost cycle:** A provider serves a cached response with a future-looking `valid_time` but a stale `init_time`. If you gate on valid_time rather than init_time, you trade on a stale forecast believing it fresh.
- **Obs station dropout:** The ASOS station stops reporting. If you test for obs freshness by existence rather than explicit timestamp check, a null obs is indistinguishable from an anchor of "no update needed." The trajectory anchor fails open.
- **Model bust event:** A front arrives 4 hours early. All NWP sources agree because they share boundary conditions. Ensemble spread is small but wrong. The only flag is the obs diverging from the NWP trajectory — if obs ingestion lags, you miss the signal entirely until settlement.
- **Timezone/DST corruption:** The local afternoon window (14Z-22Z) shifts by 1 hour on DST change. T_max extraction from the hourly trajectory silently uses the wrong window, biasing the estimated max low.

---

### 5. The Three Hardest Design Decisions

**A. How to weight obs vs. model when they diverge sharply.**
Opinionated call: obs wins for the anchor (current temperature floor), model wins for the trajectory shape. Never let a single obs spike override the ensemble mean for T_max projection — obs have their own errors (sensor malfunction, urban heat pocket). Apply a sigma-clip: if obs deviates > 2 ensemble-std from NWP, flag for human review, reduce obs anchor weight by 80%, do not exclude.

**B. Minimum source set.**
Opinionated call: HRRR (hourly) + one ASOS obs per city + ECMWF deterministic as tie-breaker. Three sources are sufficient for 80% of the skill gain. Adding the 4th and 5th source gives marginal CRPS improvement that is smaller than the operational cost of tracking their freshness contracts.

**C. Cadence of the distribution update vs. cadence of the trading decision.**
Opinionated call: decouple them. Update the distribution on every new source arrival (event-driven). Make the trading decision on a fixed clock (e.g., every 30 minutes), consuming the latest distribution snapshot at decision time. This avoids race conditions where a distribution mid-update is consumed by the order logic, and prevents over-trading on noisy micro-updates.

---

## refuter:candidate=DEFECT (angle R15, tag SEMANTIC_CONFLICT, impact 70):

**Agent ID:** `agent-a9fd8f8d6fc8be9d2`

### Final Assistant Message

The analysis is complete. The gate returns `live_entries_allowed=False` on FAIL from any reason including MISSING. The claim that the mismatch branch skip causes a "dead letter" collapse is incorrect - the missing-file path is a separate, independent fail path that activates precisely when `sha=None` prevents the file write.

---

## angle:Execution submit / ack / fill / reconcile lifecycle.

**Agent ID:** `agent-a9fd94a5b59986f78`

### Final Assistant Message

## Order Lifecycle: Submit → Settlement

### (1) Objective

The lifecycle layer exists to ensure that every signal the edge model generates either becomes a filled position or is provably absent from the book — with no ambiguous state that blocks future signals on the same contract. Secondary: ensure settled positions are redeemed at full value without manual intervention, and that terminal absorption never stalls the signal pipeline.

### (2) Ideal Mechanism

**State machine per order.** Each order is a local record with a client-generated idempotency key (deterministic: hash of contract_id + side + price + intent_timestamp). States: PENDING_SUBMIT → SUBMITTED → LIVE (resting) → PARTIALLY_FILLED → FILLED | CANCELLED | EXPIRED. Transitions are one-directional; no state regresses.

**Submit path.** Before sending to the venue, write the order record (key, params, state=PENDING_SUBMIT) to a durable local log (append-only, fsync'd). Then submit. On venue ack, update state to SUBMITTED + store venue order ID. The idempotency key is sent as a client-order-id header; if the network drops after send but before ack, re-send with the same key — venue deduplicates, returns the original ack. You never send a second real order.

**Fill path.** Venue pushes fill events (or you poll). Each fill carries a fill_id. Write fill records idempotently (fill_id is the dedup key). Accumulate partial fills into a position record keyed on contract_id + side. Position quantity = sum of all fill quantities for that contract; position is ground truth from fills, not from the order's stated quantity.

**Reconciliation loop.** Every N minutes (N≤5 during open session), query venue state for all open orders and open positions. For each venue order, if local state disagrees, venue wins: update local record to match. For each venue position, if local record is missing or differs in quantity, emit a reconciliation event and correct local. This is the safety net; it must be idempotent (running it twice produces the same outcome).

**Cancel / expiry path.** On DAY_CLOSE signal, cancel all resting orders. Send cancel; store cancel_requested timestamp. Reconciliation loop confirms cancellation or re-sends if the order is still live at venue after a timeout. Do not declare an order cancelled until venue confirms or it shows as EXPIRED on venue query.

**Settlement → exit.** At settlement, the winning bin's YES holders are paid 1. For each settled position, record settlement_price from the venue's authoritative settlement feed (not your own model). Compute PnL = sum(fill_price × qty × side_sign) + settlement proceeds. The redeem action (converting settled contracts to cash) is submitted as a separate transaction type, not an order; it has its own idempotency key. Redemption success is confirmed by the cash balance increasing, not by an ack alone.

**Non-blocking absorption.** Redemption runs in a background worker entirely decoupled from the signal pipeline. A position pending redemption does NOT block new orders on other contracts. The only coupling is capital: available_capital = total_cash + unredeemed_settled_value (marked at 1 for confirmed-winner positions, 0 for others).

### (3) Invariants

- INV-1: A client order key is written to durable log before any network call. If the process dies, replay re-submits with the same key.
- INV-2: Venue state beats local state in reconciliation. Local is a cache; venue is truth.
- INV-3: Position quantity is computed from fills, never from order quantity.
- INV-4: No order is declared filled/cancelled until venue confirms it.
- INV-5: Redemption and order submission share no lock; they are independent workers.

### (4) Failure Modes That Silently Destroy Edge

- **Double-submit without idempotency**: process dies between send and ack, re-sends without a dedup key → duplicate position, doubled risk, wrong sizing.
- **Stale-cancelled orders**: process marks order cancelled locally (timeout) but venue still has it live → surprise fill on a stale signal, wrong position.
- **Fill accumulation gap**: a fill event is dropped (poll window miss, stream gap) → position is understated → system thinks it has room, adds more → over-exposed without knowing.
- **Redemption blocking submission**: if redemption uses the same lock or capital ledger without crediting settled wins promptly → capital appears locked → signals are skipped even when edge exists.
- **Settlement feed trust error**: using your own model's final probability as settlement price rather than the venue's authoritative reading → wrong PnL, wrong capital base, compounding error.

### (5) Three Hardest Design Decisions

**A. Reconciliation frequency vs. API rate limits.** More frequent = tighter truth; but venues rate-limit. Call: reconcile on every state transition event (ack, fill, cancel) AND on a 5-minute heartbeat. Event-driven for latency, scheduled for gap-fill. Never rely on the stream alone.

**B. Capital crediting for unredeemed settled positions.** You could wait for cash to clear or credit immediately at mark=1. Call: credit immediately for confirmed-winner positions (venue settlement is objective and irreversible), but track as a separate bucket so it cannot be double-spent. Waiting for cash clearance needlessly starves the next day's session.

**C. Who owns position truth during a partial fill.** Options: order-centric (track fills per order) or position-centric (aggregate across all orders for a contract). Call: position-centric. It naturally handles partial fills from multiple orders, makes risk checks simple (query position table, not order table), and survives order-record loss on restart as long as fills are durable.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-aa05e83bb3652c8b2`

### Final Assistant Message

The Tokyo row is the smoking gun. Now I have the full picture. Let me write the forensic analysis.

---

**FORENSIC AUDIT: Decision-gate liveness — candidate → intent → receipt silence**

---

**Gate gauntlet count**

The full gate gauntlet is 18 checks across three layers:

*Pre-submit (reactor.py `_process_one_pre_submit`, lines 1345–1457):*
1. `assert_available_for_decision` — timing guard
2. FSR completeness status ∉ {COMPLETE, PARTIAL} → dead-letter
3. `reactor_mode not in EDLI_PROCESSING_REACTOR_MODES`
4. DAY0_EXTREME_UPDATED hard-fact live-eligibility
5. `source_truth_gate`
6. `executable_snapshot_gate` → transient requeue + substrate refresh
7. `riskguard_gate` → transient requeue (not terminal) since 2026-06-12

*CandidateEvaluation.admitted (candidate_evaluation.py, lines 155–166):* 3 inline sub-gates applied per-candidate before any receipt exits the adapter:
8. `quote_fresh`
9. `live_lcb_consistency_admissible` — q_lcb ≤ q_direction
10. `live_capital_efficiency_admissible` — (q_lcb − price)/price > 0
11. `live_buy_no_conservative_evidence_admissible` — direction-law (buy_no only)

*Post-submit (reactor.py `_receipt_money_path_blocker`, lines 2071–2123):*
12. `COMMAND_CREATED` side_effect check
13. `not receipt.trade_score_positive`
14. FDR: `not receipt.fdr_pass or not receipt.fdr_family_id or receipt.fdr_hypothesis_count <= 0`
15. Kelly proof type (ExecutionPrice, fee_deducted)
16. `not receipt.kelly_cost_basis_id`
17. `not receipt.kelly_pass or receipt.kelly_size_usd <= 0.0`
18. `not receipt.final_intent_id`

Note: 0b553c90fd (2026-06-14) deleted redundant re-checks of gates 9 and 10 at the post-submit layer — correct collapse; both were already enforced at gate 10.

---

**Why edli_no_submit_receipts stopped writing**

Last receipt: `2026-06-12T12:12:20.258848+00:00` (Atlanta/Sao Paulo/Dallas, q_lcb ~0.76–0.78, trade_score >0). 62,874 total receipts; zero since that timestamp.

Since `2026-06-12T22:00` the regret table has logged 980+ events. Dominant categories:
- `TRADE_SCORE_NON_POSITIVE` — 51 events
- `EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=NN` — 18 events with explicit candidate-level breakdowns
- `LIVE_INFERENCE_INPUTS_MISSING` variants (FORECAST_READER, DAY0_ORACLE_ANOMALY_PAUSED cities) — 65+ events

The receipt write at reactor.py:1635 is only reached when ALL 18 gates pass AND side_effect_status equals "NO_SUBMIT". Every family since 12:12 UTC 06-12 is being consumed (regret events confirm reactor cycling and writing regret rows), but all are failing pre-receipt gates. No candidate has cleared the full gauntlet in ~40 hours.

---

**B2 diagnosis: honest no-edge or calibration suppression**

The `live_capital_efficiency_rejection_reason` function (live_admission.py lines 87–119) fires when:
```
(q_lcb − execution_price) / execution_price ≤ 0
```
That is: `q_lcb ≤ price`. This is structurally identical to "no conservative edge exists." It is the correct, minimal gate — not an arbitrary threshold.

The DB output is diagnostic. The "best" candidate from recent `EVENT_BOUND_ALL_CANDIDATES_REJECTED` rows reveals two distinct failure modes:

**Mode A — genuine no-edge (correct gate firing):**
- Milan 38C: q_lcb=0.0000, price=0.0010, ev_per_dollar=−1.0000. q_lcb is zero, the model assigns zero conservative probability. Honest.
- Paris 32C: q_lcb=0.0000, price=0.0010. Same. Honest.
- Wuhan 28C: q_lcb=0.0413, price=0.0930. q_lcb < price → gate fires correctly.
- Helsinki 15C: q_lcb=0.0658, price=0.1900. Same.

**Mode B — positive-ev candidate still rejected (gate NOT the blocker):**
- Tokyo 19C low temp: q_lcb=0.1165, price=0.0590, ev_per_dollar=**+0.9741**. The `capital_efficiency_lcb_ev` gate should PASS this (0.1165 > 0.0590 → (0.1165−0.059)/0.059 = +0.97). Yet this appears in the "best" slot of `EVENT_BOUND_ALL_CANDIDATES_REJECTED` — meaning ALL candidates, including this one, were rejected. The reason count shows `capital_efficiency_lcb_ev=14 direction_law=1 other=7` for n=22. The Tokyo direction-law kill (1) and other=7 suggest the Tokyo positive-EV candidate was killed by a gate OTHER than capital_efficiency — most likely `live_buy_no_conservative_evidence` (direction law) or `live_win_rate_floor` (q_lcb=0.1165 < 0.51 floor) or `TRADE_SCORE_NON_POSITIVE` at a different layer.

**Critical: the win-rate floor (live_admission.py line 15: `LIVE_DIRECTION_WIN_RATE_FLOOR = 0.51`) is the silent mass-killer.** For Tokyo q_lcb=0.1165 to pass, it needs q_lcb ≥ 0.51. It fails by 0.39. This floor applies to ALL directions uniformly. For any candidate where the bin probability is below 51% (which is the majority of tail bins), `live_win_rate_floor_rejection_reason` fires before capital_efficiency is even reached. But — `live_win_rate_admissible` is NOT in `CandidateEvaluation.admitted`. Checking candidate_evaluation.py lines 155–166: `live_win_rate_admissible` is NOT listed in the `admitted` property. So the floor does not block `admitted` — it is only a diagnostic/receipt field.

Re-reading Mode B: Tokyo q_lcb=0.1165, price=0.0590. Gate 10 (`live_capital_efficiency`): (0.1165−0.059)/0.059 = +0.97 > 0. Gate 10 PASSES. The Tokyo candidate's ev_per_dollar is legitimately positive. It is listed as the "best" candidate in that family but was still rejected. The `direction_law=1` count in the breakdown is a different candidate. The family count is `capital_efficiency_lcb_ev=14 direction_law=1 other=7` — Tokyo's positive-EV bin is counted in the "other=7" bucket, meaning it failed a non-capital-efficiency gate. The most likely gate for a buy_yes with q_lcb=0.1165: `passed_prefilter` from the FDR step (prefilter at adapter line 10001: `yes_edge_lcb_positive = yes_price is not None and yes_lcb > yes_cost` → p_value=0.0 if positive, 1.0 if not). If yes_lcb > yes_cost then prefilter=True, p_value=0.0 → BH-FDR admits it. But the "other" bucket means it failed something: possibly `missing_reason is not None`, or `execution_price is None`, or `quote_fresh=False`. Without the receipt JSON for that specific candidate there's no definitive verdict, but the book-stale (B1-adjacent) or snapshot-missing path is the likely culprit for "other."

**B2 verdict: the capital_efficiency gate as implemented is mathematically honest** — it fires precisely when q_lcb ≤ price (zero or negative conservative edge). The dominant kills (Milan/Paris/Wuhan/Helsinki) are genuine no-edge cases where the fused-bootstrap q_lcb for the winning bin is at or below the market price. This is HONEST_MARKET, not calibration suppression. The scale (14–21 out of 22 candidates per family) reflects the market correctly pricing the center bins while our q_lcb for tail bins is near-zero or below cost — which is correct behavior from a model that has wide uncertainty and doesn't claim false conviction on tails.

**The operative question is whether the q_lcb VALUES are correct,** not whether the gate is over-tight. If q_lcb is correctly computed (fused-bootstrap, k=1.5833, w=0.2811 uniform mixture) and the market is pricing these bins at 5–19c, then zero fills is the honest outcome. The uniform mixture (w=0.2811) deliberately widens the distribution, which pushes q_lcb DOWN toward zero for low-probability bins — this is by design (anti-overconfidence). The issue would be calibration artifact staleness or bundle-blocking (REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_BLOCKED), not gate over-tightness.

---

**B1 relationship to receipt silence**

B1 (ws-gap latch frozen since 2026-06-12T22:58, PHANTOM row 5bbc2be2) fires in `executor.py:_assert_ws_gap_allows_submit` (lines 84–89). This is only reached on the LIVE submit path. In no_submit / live_no_submit mode, the no_submit adapter never invokes the executor. **B1 does not suppress no_submit_receipts.** The receipt silence is entirely from B2 — all candidates failing pre-receipt gates, primarily capital_efficiency (gate 10) indicating honest no-edge across the traded families in the current window.

**B3 (Beijing BELIEF_AUTHORITY_FAULT):** Stale belief 87 cycles. This is an exit-position staleness issue, not an entry-gate blocker. It does not touch the receipt write path.

---

**Commit verdicts**

**b1825c4a07 (2026-06-08):** Removed only the `selector_enabled` cache-flag guard in `opportunity_book.py:to_receipt_dict` that null-out `selected_candidate_id` when the flag was absent/falsy, plus dead `"selector_enabled": True` / `"selector_shadow"` keys from the event_reactor_adapter.py cache summary. **Candidate materialization (the ΔU ranker producing candidates from the opportunity book) was NOT removed.** The ranker survived intact. This commit did not cause the receipt silence. The toggle removal, if anything, un-blocked families that were previously silently dropped when the flag was missing — no regression.

**e583af06cd:** Removed 6 dead shadow strategy modules and their 4 flag accessors. The selection_eb EB-computation was retained with `authority_on=False` hardcoded. BH/FDR is the live gate. The live money path is byte-identical. **No live money-path change; no regression.**

**0b553c90fd (2026-06-14, Wave C/D):** Deleted redundant live_lcb_consistency and live_capital_efficiency re-checks at reactor.py post-submit lines 2094–2098. Both are already enforced in `CandidateEvaluation.admitted` (gates 9 and 10). This is a correct gate-mass collapse with zero behavioral change. **No regression.**

---

**Last-good / first-bad evidence**

Last good receipt: `2026-06-12T12:12:20` — Atlanta/Sao Paulo/Dallas, q_lcb ~0.77, execution_price absent (no quote in those rows — `||` for execution_price column), trade_score >0.02. These receipts have `side_effect_status=NO_SUBMIT`, confirming they cleared all 18 gates.

First-bad window: `2026-06-12T22:00` onward — regret events show `TRADE_SCORE_NON_POSITIVE` (51 hits) and `EVENT_BOUND_ALL_CANDIDATES_REJECTED:capital_efficiency_lcb_ev` as the terminal sink. The gap between 12:12 and 22:00 (9.8 hours) is the window of interest — no receipt written, but regret data confirms the reactor was alive throughout.

The most recent `EVENT_BOUND_ALL_CANDIDATES_REJECTED` rows show q_lcb=0.0000 for the "best" candidates in multiple families. A q_lcb of exactly zero from the fused-bootstrap path is a strong signal that the replacement bundle for those families either has zero-width distribution concentrated elsewhere or the bundle's q_ucb/q_lcb bounds maps are empty/missing — triggering `_replacement_yes_lcb_for_bin` to return 0.0 as a fail-closed result (adapter line 9971: `settlement_floor_lcb=None` path with missing bounds would return 0.0). This is data-state (HONEST_DATA) not gate over-tightness.

**Summary verdict for B2:** The `capital_efficiency_lcb_ev` gate is mathematically honest. The bulk of kills are genuine no-edge (q_lcb ≤ price). The positive-EV Tokyo candidate that still failed was killed by a non-capital-efficiency gate (the "other" bucket). The root cause of receipt silence is not gate over-tightness — it is the q_lcb values themselves being zero or below cost for nearly all families in the current market window, which points to either (a) the fused-bootstrap bundle returning near-zero bounds due to high-uncertainty mid-summer forecasts or (b) bundle-read failures causing `_replacement_yes_lcb_for_bin` to fail-closed at 0.0. The correct diagnostic next step is querying for `REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_BLOCKED` or `REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE` in the regret table's `LIVE_INFERENCE_INPUTS_MISSING` rows — those are the first-order suspicion for why q_lcb resolves to zero for center bins on currently-open families.

---

## reconcile:Execution submit / ack / fill / reconcile lifecycle

**Agent ID:** `agent-aa1109812eb7ba74e`

### Final Assistant Message

Now I have sufficient evidence from all three reports plus direct file reads. Let me compile the divergences.

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-aa2e42caddf553cba`

### Final Assistant Message

Now I have all the evidence needed. Let me compile the findings.

---

## refuter:candidate=DEFECT (angle R2, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-aa38ea8c914c08d0c`

### Final Assistant Message

Flag is **true** in config. Now I have a complete picture. Let me synthesize:

**Summary of findings:**

1. The `readiness_state` table in `zeus-forecasts.db` has 467 non-expired LIVE_ELIGIBLE rows as of now (verified by repro query). All expire at 2026-06-14T20:23:09Z — that is the `source_cycle_time + 30h` bound from a single source cycle (06-13T14:23Z ≈ 12Z cycle).

2. The `expires_at` is intentionally designed to expire at `source_cycle_time + 30h`. This is NOT a daemon-miss failure mode — it's the **normal staleness gate** that fires when the data ages past 30h. The 30h window covers two missed 12Z cycles (designed to survive a single-cycle skip).

3. The materializer fires every 5 min and re-stamps `expires_at` via UPSERT — but it stamps from `source_cycle_time + 30h`, not `now + 3h`. So re-running the materializer on the same cycle does NOT push `expires_at` forward. Fresh rows are only stamped when a **new cycle download** arrives. The next cycle (06-14 12Z) would be available around 06-15T02:00Z.

4. There IS a real gap: from 06-14T20:23Z (expiry) to ~06-15T02:00Z (next cycle available), about a 6h window where zero LIVE_ELIGIBLE rows exist. The claim about "no alert, no NO_DECISION signal" is correct — the evaluator silently returns zero qualified forecasts.

5. However, this is **by design and intended**: the cycle-policy doc explicitly says "a SECOND consecutive miss crosses 30h and fails closed." One missed cycle (06-14 12Z not published on time) = readiness expires. Decision_certificates going dark is the DESIGNED outcome of the staleness gate working correctly.

6. The claim characterizes this as a "missing capability" (watchdog). But the existing code already handles it: `_is_live_readiness` at line 476 returns `READINESS_EXPIRED` — this is an explicit rejection code, not silent failure. The system produces `READINESS_EXPIRED` rejections, not silent zeros.

7. **Would fixing it move a real order?** No — the system is currently blocked by B1 (ws_gap latch freeze), which is the actual fill blocker. The readiness expiry is a separate staleness gate that correctly enforces the 30h data freshness law. Adding a watchdog alert here adds observability but does not unblock orders.

The claim's "no circuit breaker" is wrong about mechanism: the circuit breaker IS the `expires_at` gate (explicit rejection). What's missing is an **alert** when all rows expire simultaneously, but that's an observability gap, not a missing capability that blocks fills. The empirical claim ("evaluator silently produces zero forecasts") is also partially wrong — the evaluator produces `READINESS_EXPIRED` rejections, not silent zeros.

Kill angle: **(b) existing code already handles it** — `_is_live_readiness` explicitly detects and labels expired rows as `READINESS_EXPIRED`. **(d) would fixing it move a real order?** No — B1 submit latch is the active blocker, and even if readiness were live, B2 edge-gate would need to pass.

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 100):

**Agent ID:** `agent-aa578a580a08b2c7a`

### Final Assistant Message

The token has no suppression entry, and the finding remains open despite the settled absorber having a valid code path. The live finding being still open suggests the refresh is either not running or hitting a different failure. But the question is about the DEFECT CLAIM itself.

The candidate defect says: the NO-side phantom case lacks an absorber path because (a) `_absorb_operator_external_close` returns False (no operator ack), and (b) the settled-class absorber requires `calendar_terminal.get(token) != None`, but token has no EMS row so `condition_map` is empty.

Claim (b) is factually wrong: the token HAS 378 EMS rows as no_token_id, so `_condition_ids_for_tokens` WILL find it and populate `condition_map`. The settled-class path IS available. The defect as characterized (missing absorber path) does not exist in the code. There IS an absorber path and it would fire once the 24h terminal buffer passes.

The real situation is a timing issue (finding was recorded before terminal time), not a structural code gap.

---

## angle:Point-in-time data lineage / look-ahead leakage governance**

**Agent ID:** `agent-aa57926bb334af6c6`

### Final Assistant Message

Now I have enough evidence to compose the divergences. Let me do one final check on the sigma artifact's `candidate: true` status being live.

---

## refuter:candidate=DEFECT (angle R4, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-aa5e5917e08bb4565`

### Final Assistant Message

I'll investigate this candidate defect systematically, trying to kill it from all four angles.

---

## refuter:candidate=DEFECT (angle R12, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-aa8a9651d2b5010f5`

### Final Assistant Message

I now have a complete picture. Let me synthesize the kill:

**Evidence summary:**

1. **Code structure**: The `_transient_horizon_terminal` at line 866 governs termination. It uses SEMANTIC horizons (operator disarm, venue close, timeliness floor) — NOT a cycle counter. The `_note_transient_requeue` counter is explicitly LOG HYGIENE ONLY (line 1141). The comment at 1452-1454 ("exhaustion after MAX retries terminates") refers to the OLD behavior that was replaced — the surrounding code is the replacement that uses SEMANTIC horizons. No "MAX retries" exists in the current code path.

2. **No riskguard refresh mechanism**: RISK_GUARD_BLOCKED does NOT call `_record_substrate_block` (only EXECUTABLE_SNAPSHOT does at line 1440). The riskguard gate reads in-process memory (`get_current_level()` from `risk_state.db`) — it doesn't have a refreshable substrate in the same sense as snapshot capture. This part of the defect description is accurate.

3. **But the empirical claim is wrong**: The 2113 RISK_GUARD_BLOCKED events since 2026-06-12 all happened BEFORE 12:10:35 on 06-12. The last such event is 2026-06-12T12:10:35 — the same window as when `edli_no_submit_receipts` went silent (12:12:20). RISK_GUARD_BLOCKED is NOT currently accumulating. Risk is GREEN now. The events were from an earlier incident when risk was genuinely non-GREEN (the `dependency_db_locked` storm fixed in task #65).

4. **Would fixing it move a real order?** No — currently, risk is GREEN, RISK_GUARD_BLOCKED is not running. The current submission blockers are TRADE_SCORE_NON_POSITIVE and B1 (ws_gap finding 5bbc2be2), neither of which is the riskguard queue burn. The 57 `MONEY_PATH_TRANSIENT_EXHAUSTED:RISK_GUARD_BLOCKED` rows show that events eventually DO terminalize via the horizon mechanism — so the system is self-healing.

**Kill**: The empirical claim that RISK_GUARD_BLOCKED "burns entire queue budget" is historically accurate (the 06-12 incident was real), but the defect is already partially addressed — risk is now GREEN, events are not riskguard-blocked, and the 57 exhausted events confirm the horizon does eventually terminate them. The "no readiness probe or cycle counter" observation is code-accurate but points to a missing observability feature rather than an active blocking defect. It would NOT move a real order today.

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 100):

**Agent ID:** `agent-aa8b9cf6f313ac02d`

### Final Assistant Message

I have all the evidence needed. Let me synthesize:

**The defect is real and the candidate's analysis is substantially correct, but with a precision gap:**

The candidate frames this as "NO-side token has no EMS row, so condition_map is empty, so settled-class absorber misses." That's correct in direction. The exact mechanism:

1. `_condition_ids_for_tokens` queries EMS for both YES/NO tokens to build the condition bridge. Denver 90-91F June 12 market (gamma_market_id=2501685) has **zero EMS rows** — the market closed June 12 and EMS rows have rotated out.
2. Without the condition bridge, `_market_calendar_terminal_evidence` cannot find the NO token (25998...) in `market_events` (which stores only YES tokens: 40798...).
3. `calendar_terminal.get(token)` returns None at line 2445, so the settled-class absorber falls through.
4. `_absorb_operator_external_close` at line 1162 also returns False — no operator-ack row exists.
5. The finding stays `resolved_at IS NULL`.
6. `list_unresolved_findings` returns 1 row, blocking `clear_after_m5_reconcile` at line 331.

The fix described would unblock real submit capacity — B1 is the only unresolved finding, and fixing it would allow `allow_submit=True`. But B2 (edge gate: no candidates pass q_lcb > price) is an independent parallel blocker. Fixing B1 unlocks the submit gate, but orders can only flow if B2 also resolves. The finding is architecturally real (not cosmetic), would move the system past a real gate (submit latch), but would not guarantee a fill because B2 is independent.

"Would fixing it move a real order toward a real fill?" — Yes, it removes a concrete mechanical gate that currently blocks ALL orders, but B2 (the edge/capital_efficiency gate) must also be clear. This is a real ROOT_CAUSE of the submit latch freeze, not a cosmetic defect.

The candidate's characterization of `calendar_terminal.get(token) != None` requiring `condition_map` from EMS is accurate. No kill found on any of (a)-(d).

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-aaa09f5865a87bd99`

### Final Assistant Message

Now I have a complete analysis. Let me synthesize:

**The claim:** REVIEW_REQUIRED has no automatic exit path — `find_unknown_command_by_economic_intent` blocks any new submit at the same token/side/price/size indefinitely.

**What I found:**

1. `find_unknown_command_by_economic_intent` (venue_command_repo.py:2587) queries `UNRESOLVED_SIDE_EFFECT_STATES` which INCLUDES `REVIEW_REQUIRED` (line 41-44). So any REVIEW_REQUIRED row with matching economics DOES block a new submit. That part is correct.

2. REVIEW_REQUIRED IS in `IN_FLIGHT_STATES` (command_bus.py:115), so the recovery job (`reconcile_unresolved_commands`) DOES scan it.

3. However, `_reconcile_row` routes REVIEW_REQUIRED to `_review_required_cancel_unknown_live_order_recovery`, which only processes commands that arrived via the cancel-unknown path (requires `CANCEL_REPLACE_BLOCKED` with `requires_m5_reconcile=True`). For all other REVIEW_REQUIRED commands (e.g., ack-lost submits without venue_order_id), the function returns "stayed" immediately (lines 4729-4730, 4732-4733).

4. Specifically, commands where REVIEW_REQUIRED was set via the repro scenario (SUBMITTING ambiguity during venue reliability incident → recovery sets REVIEW_REQUIRED via `recovery_no_venue_order_id` reason) have NO automatic clearance path. The recovery loop sees them each cycle, enters `_review_required_cancel_unknown_live_order_recovery`, finds no cancel-unknown payload, and returns "stayed" every cycle.

5. The pre-SDK path `pre_submit_collateral_reservation_failed` has a manual operator tool (`clear_review_required_no_venue_side_effect`) but no automated clearance.

6. The `recovery_no_venue_order_id` path ALSO lacks automated clearance — there's a manual tool (`gather_review_required_no_exposure_proof_recovery_no_venue_order_id`) that an operator can call, but nothing calls it automatically.

**Kill attempts:**

- (a) Is the "no auto-exit" design justified by a real constraint? Partially: the system cannot safely auto-clear a REVIEW_REQUIRED that MAY have hit the venue — that would risk double-spend or lost fill. But the `recovery_no_venue_order_id` case where there's no `venue_order_id` AND no side-effect evidence could theoretically be auto-cleared by verifying open orders / trades list. The system has the pieces (`gather_review_required_no_exposure_proof...` exists) but nothing calls it automatically. The "must not auto-clear without proof" constraint is real; the "cannot gather proof automatically" is the gap.

- (b) Does existing code handle it? For cancel-unknown lane: yes. For all other REVIEW_REQUIRED: no — the code correctly identifies it as "stayed" forever.

- (c) Empirical claim (blocking real fills)? The defect requires a REVIEW_REQUIRED row with the exact same token/side/price/size. If the system uses different prices each cycle (which it does — limit prices are computed fresh and vary), the economic-intent block would NOT trigger even with a stuck REVIEW_REQUIRED, because price or size rarely exactly matches. The `_economic_decimal` quantizes to 4 decimal places, so a $0.0001 price move bypasses the block entirely.

- (d) Would fixing it move a real order? Only if there's an active REVIEW_REQUIRED with matching economics AND the system would otherwise have edge on exactly that token/side/price/size. The price-recompute-each-cycle mechanism makes exact matches unlikely for most situations.

**The kill:** The empirical claim of "permanently blocks" is overstated for the entry flow. `find_unknown_command_by_economic_intent` matches on exact price AND size (to 4 decimal places). Since prices are recomputed each cycle using fresh market data, a subsequent cycle will typically produce a different limit price, bypassing the economic-intent check entirely. The block is real in the single-cycle window right after the REVIEW_REQUIRED event fires, but it does not "permanently block" new submits on subsequent cycles with fresh pricing. The idempotency key includes timestamp components (the defect's own claim: "can change `decision_id` and therefore derive a different idempotency_key" — this is exactly what happens on the next decision cycle). The economic-intent check only catches the case where a new decision produces the SAME price and size, which is unusual with volatile market data.

The MISSING_CAPABILITY is real but the `would_move_real_fill` claim is weak: the permanent block scenario requires the same exact price/size collision on a subsequent cycle, which is the uncommon case. The more common case resolves naturally via repricing.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-aadbdf508638f874a`

### Final Assistant Message

No live path (events/, decision_kernel/, control/, riskguard/) gates on `opportunity_fact` or `selection_family_fact`. These tables are used only in analysis/evidence_report.py and backtest code.

The kill is solid on prong (d): fixing the opportunity_fact/selection_family_fact write path would NOT move a real order. The live reactor generates commands via a completely independent path (decision_kernel/compiler.py + reactor.py) that has no dependency on these tables. The real blocking issue is B1 (ws_gap submit latch) and B2 (capital_efficiency_lcb_ev rejection), not the observability tables.

The candidate's empirical claim (both tables dark since 06-04) is accurate. But the causal framing — "pipeline never evaluated" — is wrong. The pipeline is evaluating through the EDLI reactor path and producing 67 exec facts + 50 venue commands. The blank tables are a legacy telemetry gap, not a decision gate.

---

## angle:Capital / portfolio / collateral / risk-allocator / account readiness.

**Agent ID:** `agent-aadff90b52a6f50b4`

### Final Assistant Message

ANGLE: Capital / portfolio / collateral / risk-allocator / account readiness

MECHANISM

This angle owns the chain of gates that must pass before any order reaches the venue. In rough submission order: CutoverGuard arbitrates whether the system is in a state that permits live venue commands at all; RiskGuard independently monitors six portfolio health dimensions and produces a GREEN/DATA_DEGRADED/YELLOW/ORANGE/RED level that blocks new entries at anything above GREEN; HeartbeatSupervisor guards the Polymarket CLOB heartbeat lease and blocks resting (GTC/GTD) orders when the lease is unhealthy; CollateralLedger tracks on-chain pUSD balance and ERC20 allowance and blocks buys when either is insufficient or stale; and StrategyPolicy resolves per-strategy manual overrides and hard-safety actions that can gate a strategy independently of the global risk level. Portfolio state provides the bankroll, open-position accounting, and the EDLI entry-proof review chain that can reject candidates before sizing even runs.

DEFECTS

1. Zombie tombstone reader permanently blocks all GTC/GTD orders if a legacy file exists on disk.

heartbeat_supervisor.py line 555-557: `_write_failclosed_tombstone()` is an explicit no-op — the body contains only a pass with a retirement comment dated 2026-05-04. The writer is dead. However, `gate_for_order_type()` at line 548-553 still calls `_failclosed_tombstone_exists()` (line 586-592), which checks for `state/auto_pause_failclosed.tombstone` on disk. If that file was ever created before retirement and was never manually deleted, every call to `gate_for_order_type()` returns False permanently, and `assert_heartbeat_allows_order_type()` at line 595 raises `HeartbeatNotHealthy` on every GTC/GTD order. Reproduction: `touch state/auto_pause_failclosed.tombstone` — no new resting orders will ever pass the gate regardless of heartbeat health. BLOCKS SUBMISSION.

2. Heartbeat status file max_age is 8 seconds — any keeper stall goes LOST.

heartbeat_supervisor.py line 35: `DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS = 8`. `ExternalHeartbeatSupervisor.status()` at line 304 returns LOST if the status file is missing, unreadable, or older than 8 seconds. `HeartbeatStatus.resting_order_safe()` at line 74 returns False when health is not HEALTHY. After any keeper restart gap, pipeline stall, or I/O delay exceeding 8 seconds, all resting orders are blocked until 3 consecutive successful recovery ticks (line 37: `DEFAULT_HEARTBEAT_LEASE_RECOVERY_SUCCESS_TICKS = 3`), adding a 6-second floor to recovery. With 1.5 days of no new orders this is a plausible continuous state if the keeper process died. BLOCKS SUBMISSION on resting order types.

3. CutoverGuard state NORMAL blocks all new entry submits — file-missing defaults to NORMAL, not LIVE_ENABLED.

cutover_guard.py line 154-158: `_load_payload()` returns `{"state": "NORMAL"}` on `FileNotFoundError`. `gate_for_intent()` at line 339 maps NORMAL + ENTRY to `_blocked(intent_kind, state)` with `allow_submit=False`. Only `CutoverState.LIVE_ENABLED` permits new entry submissions (line 335-338). The LIVE_ENABLED transition requires a full HMAC-signed operator token plus a G1 readiness evidence JSON file with exactly 17/17 gates passed (line 255-266). If the state file was never written (fresh deploy) or was deleted, the system sits at NORMAL and refuses all entry submits indefinitely. BLOCKS SUBMISSION.

4. pUSD allowance gate is independent of pUSD balance — allowance=0 blocks all buys even with $1,162 on-chain.

collateral_ledger.py line 325-331: `buy_preflight()` checks `available_pusd_allowance_micro >= required_micro` as a separate condition after the balance check. `available_pusd_allowance_micro` at line 156 is `max(0, pusd_allowance_micro - reserved_pusd_for_buys_micro)`. If the ERC20 approval to the CTF exchange contract has not been set or has been consumed, `pusd_allowance_micro = 0` and every buy fails with `CollateralInsufficient` regardless of wallet balance. On $1,162 at 5% Kelly (~$58 per order), a 0 or sub-$58 allowance blocks every candidate. BLOCKS SUBMISSION.

5. CollateralLedger snapshot staleness (180s) blocks all buys if the refresh cadence lapses.

collateral_ledger.py line 44-47: `COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS = 30.0 + 150.0 = 180.0`. `_assert_snapshot_fresh()` at line 711-724 raises `CollateralInsufficient("collateral_snapshot_stale")` if the snapshot age exceeds this bound. `buy_preflight()` at line 314 calls this before any balance check. If the on-chain refresh process stalls for more than 3 minutes, all buys are blocked. BLOCKS SUBMISSION.

6. CollateralLedger unconfigured raises on every buy if the global was never set.

collateral_ledger.py line 641-644: `assert_buy_preflight()` raises `CollateralInsufficient("collateral_ledger_unconfigured")` when `_GLOBAL_LEDGER is None`. There is no automatic re-initialization path — the ledger must be explicitly wired at startup via `configure_global_ledger()`. A restart that fails to reconfigure the global before the first order cycle blocks all buys indefinitely. BLOCKS SUBMISSION.

7. Any exception in `load_tracker()` elevates strategy signal to YELLOW, blocking all new entries.

riskguard.py line 1566: `strategy_signal_level = RiskLevel.YELLOW if (edge_compression_alerts or strategy_tracker_error) else RiskLevel.GREEN`. The `strategy_tracker_error` flag at line 1545 is set True on any exception from `load_tracker()`. Since YELLOW maps to "No new entries" per risk_level.py's `LEVEL_ACTIONS`, a corrupted, missing, or schema-mismatched tracker DB silently upgrades the risk level to YELLOW and blocks all entries. There is no visibility into whether this is the active cause without querying risk_state.db. BLOCKS SUBMISSION.

8. settlement_quality goes RED when settlement rows exist but none are metric_ready.

riskguard.py line 1549-1553: `if settlement_rows and not metric_ready_rows: settlement_quality_level = RiskLevel.RED`. A settled contract that lacks both `p_posterior` and `outcome` columns populated causes the entire settlement_quality component to go RED, which via `overall_level()` in risk_level.py propagates to the global level and blocks entries. This can silently fire after any new settlement if the metric computation pipeline is behind. BLOCKS SUBMISSION.

9. EDLI entry-proof table absence marks every open position with a blocking rejection.

portfolio.py line 2016: `required_world_tables = ("edli_no_submit_receipts", "decision_certificates", "edli_live_order_events")`. Line 2022-2030: if any of these tables is missing in the world DB, `_query_edli_entry_proof_review_reasons()` returns `EDLI_ENTRY_PROOF_WORLD_TABLE_MISSING` for every open EDLI position. The memory note for this session records that `edli_no_submit_receipts` was dead since 2026-06-06. If this table was dropped or never created in the world DB that the portfolio reads, every open EDLI position gets this rejection on every evaluation cycle, killing candidates that otherwise have positive edge. KILLS +EV CANDIDATES.

10. Legacy collateral.py fallback checks pUSD balance for a CTF sell — wrong economic object.

collateral.py line 39-54: `check_sell_collateral()` without a `token_id` argument falls through to a path at line 40 that calls `clob.get_balance()` (pUSD) and checks it against `(1.0 - entry_price) * shares` at line 45. For binary CTF markets, a sell requires CTF outcome tokens held in inventory, not pUSD. This path can pass the preflight check with sufficient pUSD balance when the position has zero CTF tokens (incorrectly allowing an eventual submission that will fail at the venue), or it can block a valid sell if pUSD is low while CTF tokens are held. The docstring at line 18 acknowledges this. DEFECT that can both produce false-allow and false-block on sells, though the `with token_id` path at line 28-36 is correct and is presumably the live path.

11. Realized-loss breaker fires on settled PnL only — mark-to-market equity loss is invisible to the loss gate.

riskguard.py line 598 (`_realized_window_loss_snapshot()`): the loss level is computed from REALIZED settled PnL in the window, not from equity drawdown. A position that declines 80% in market value but has not yet settled does not trigger the loss breaker. This is the stated post-2026-06-08 design (prior version had mark-to-market path). Not a block but an architectural choice that underweights floating losses. DEFECT_NO_CURRENT_IMPACT on the submission block question.

12. DATA_DEGRADED blocks entries but the label says "acting with YELLOW-equivalent safety."

risk_level.py line 27-29: `LEVEL_ACTIONS` for DATA_DEGRADED states "acting with YELLOW-equivalent safety," and YELLOW_ACTIONS states "No new entries." So DATA_DEGRADED also blocks new entries. The label is slightly misleading — "acting with YELLOW safety" implies degraded caution, but the actual action is full entry block. Not a logic error but a documentation-level semantic confusion. DEFECT_NO_CURRENT_IMPACT on mechanics.

13. `_select_rows()` in policy.py applies first-in-wins per action_type within a single source with no precedence dedup.

policy.py line 238-256: within `_load_risk_actions()`, if two active rows share the same `action_type`, only the first (by DB insertion order or query order) is used. There is no conflict resolution by precedence within the `risk_action` source. A stale active row can shadow a newer one. This does not block submission by itself unless the stale row contains `pause_entries=true`, but it is a correctness hazard for policy resolution. DEFECT_NO_CURRENT_IMPACT unless stale row is present.

NOTABLY ABSENT

There is no ws_gap (WebSocket gap) gate visible anywhere in this angle's files. The brief names it as one of the 4 hard pre-submit gates but it is not implemented in cutover_guard.py, heartbeat_supervisor.py, riskguard.py, collateral_ledger.py, or portfolio.py. Its implementation is presumably in the executor/reactor layer outside this angle.

There is no minimum order size enforcement in this layer. Collateral preflight checks that required <= available but does not enforce a floor against exchange minimum fill sizes. A $1,162 balance with Kelly at 2-5% produces $23-$58 per order — if the exchange minimum is above that on thin books, the order will be rejected at the venue without a pre-gate catch here.

There is no automatic re-initialization path for `_GLOBAL_LEDGER` or `_GLOBAL_SUPERVISOR`. Both require explicit external wiring at startup. A restart that partially completes initialization leaves these as None indefinitely.

There is no explicit drain-wait between CutoverGuard LIVE_ENABLED check and the heartbeat gate. The two gates are checked independently with no ordering guarantee enforced in this layer.

There is no circuit-breaker visibility export from CollateralLedger — the `CollateralInsufficient` exception surfaces at order time but no counter or metric is written, making it hard to distinguish allowance-zero from balance-zero from staleness from unconfigured in observability tooling.

PROVENANCE VERDICTS

src/execution/collateral.py: QUARANTINED. Git header not present but file is 57 lines with the `without token_id` fallback path that is semantically wrong for CTF markets per its own docstring. The correct path exists (with token_id). The legacy fallback should be removed or hard-errored rather than silently applying wrong logic.

src/riskguard/riskguard.py: CURRENT_REUSABLE with noted gaps. Header states last audited 2026-06-08. The 2026-06-08 realized-loss fix is present. The 2026-06-13 WAL-lock absorption fix is present. The DATA_DEGRADED preservation logic for dependency_db_locked rows is post-2026-06-08. The strategy_tracker_error → YELLOW path at line 1566 has no suppression mechanism for transient errors — this is a policy choice, not a bug, but it is worth monitoring.

src/riskguard/policy.py: CURRENT_REUSABLE. No explicit date but `_parse_boolish()` at line 281 comments "gate/ungate removed (K1/#71)" which places it after the K1 DB split. Hard-safety precedence logic is sound. The first-in-wins risk_action dedup is a minor correctness gap but not a rewrite trigger.

src/riskguard/risk_level.py: CURRENT_REUSABLE. 31 lines, no logic gaps. DATA_DEGRADED ordering and LEVEL_ACTIONS are consistent.

src/control/cutover_guard.py: STALE_REWRITE. Created and last audited 2026-04-27 under Z1 CLOB V2 cutover authority. Comment on line 18 states "cancel/redemption decisions exposed here are decision surfaces only until the later direct cancel/redeem side-effect paths are wired through them." Two months have passed since audit. The file-missing → NORMAL default (line 154-158) is a non-fail-closed default for a security-critical gate. If the state file disappears (disk error, misconfigured state_path, container restart with ephemeral volume), the system defaults to NORMAL and silently blocks all new entry submits forever. The LIVE_ENABLED evidence requirement is correct and sound, but the overall file has not been re-audited against post-Z1 law.

src/control/heartbeat_supervisor.py: STALE_REWRITE. The zombie tombstone asymmetry (writer retired 2026-05-04, reader still active at line 551) is an unresolved dead-code hazard that can permanently block all GTC/GTD orders. Any pre-retirement tombstone file on disk causes permanent blockage. The retirement of the writer without removing the reader or unconditionally clearing the file is a correctness defect that must be resolved before this file is reusable.

src/state/collateral_ledger.py: CURRENT_REUSABLE. Header shows last audited 2026-05-17. The pUSD allowance independence check is correct by design (allowance and balance are separate on-chain quantities). Snapshot max_age and micro-USD arithmetic are sound.

src/state/portfolio.py: CURRENT_REUSABLE for the path read (lines 1-2375). EDLI_DECISION_AUDIT_REQUIRED_FROM at line 332 is set to 2026-06-07 03:00 UTC, consistent with the K1 DB split era. The `required_world_tables` check at line 2016 is the most likely active block given the session memory note that `edli_no_submit_receipts` was dead since 2026-06-06 — this would mark every open EDLI position with `EDLI_ENTRY_PROOF_WORLD_TABLE_MISSING` on every evaluation cycle and kill all candidates.

---

## reconcile:Edge existence + settlement grading + evidence provenance (m

**Agent ID:** `agent-aafaba712a2855f17`

### Final Assistant Message

I have enough from the three reports and the code reads to synthesize the divergences. Let me call StructuredOutput now.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-ab1a3a650de8004b0`

### Final Assistant Message

The table name "no_trade_events" may differ. Let me check the actual schema and use the correct DB.

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-ab3cbf5cd081d2972`

### Final Assistant Message

This confirms the double-count. The position is `phase=voided, chain_state=synced` which puts it in `_CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES`. Now I have the complete picture to render judgment.

Summary of kill-angle analysis:

**Kill angle (a) - is the ideal naive?** The ws_gap_guard fail-closed design for unresolved findings is correct and intentional. The system MUST block submission when it can't confirm reconciliation is clean. Not naive.

**Kill angle (b) - does existing code already handle it?** Three paths exist to auto-resolve this finding:
1. `_token_is_suppressed_external` - not registered in token_suppression
2. Settled-class absorber (line 2445-2473) - requires market_events entry for the token OR condition_id bridge via EMS, but the token is absent from both EMS and market_events (market rotated out)
3. Operator-ack absorber - requires a prior RESOLVED finding with operator-session marker; no such row exists

All three doors are closed. The code does NOT already handle this case.

**Kill angle (c) - is the empirical claim survivorship-biased?** The finding (5bbc2be2) exists confirmed in DB, resolved_at IS NULL, recorded 2026-06-12T22:58. The venue_commands state=EXPIRED with partial fill 17.05. The position_current is voided/synced with chain_shares=17.05. The double-count pattern (expected=34.10 = journal 17.05 + closed_holdings 17.05, exchange=0) is verifiable. Not survivorship bias.

**Kill angle (d) - would fixing it move a real order?** The ws_gap_guard in-memory state is blocking (`m5_reconcile_required=True`). The block_adapter at `src/control/block_adapters/ws_gap_guard.py:40` returns `BlockState.BLOCKING` when `allow_submit=False`. The candidate's claim that Stage 5-7 (submit receipts, envelopes, commands) are silent since 2026-06-12 is consistent with the finding timestamp of 2026-06-12T22:58. Resolving this finding is the necessary path to clearing the latch.

The defect is real and the block is real. The candidate's description of the failure class (swept/redeemed winner not absorbed) is correct. However, the specific framing "task #31 (external-close absorber) failed to mark resolved" needs a nuance: task #31 implemented the settled-class absorber for cases where the token IS in market_events/has a condition bridge, but this token has fallen out of EMS and is absent from market_events, so the absorber's fail-closed lookup returns empty — the market_events lookup gap is the specific defect, not the absorber code itself being wrong per se.

---

## refuter:candidate=DEFECT (angle R7, tag MISSING_CAPABILITY, impact 72):

**Agent ID:** `agent-ab42b95dfa556ae10`

### Final Assistant Message

Now I have what I need. The picture is clear.

The defect claim is factually correct: line 105 is a bare `continue` that kills all buy_no hypotheses in `scan_full_hypothesis_family`. The code self-documents this ("its buy_no loop body is a bare `continue``"). However, the critical question is whether the adapter's compensation path at lines 10341-10368 fully handles it.

The adapter explicitly checks `if no_hyp is not None` — knowing `no_hyp` is ALWAYS None because the scan never emits buy_no — and routes to `_native_no_edge_positivity` instead. This is a deliberate, committed workaround. The comment at line 10344 acknowledges the scan's dead NO path and says the fix is in the adapter, not the scan.

The defect claim says "any regression in the adapter's compensation path leaves buy_no with no FDR accounting" — but that is a conditional future risk, not a present defect. Currently, the adapter path IS the compensation and it is active and handling all NO-side FDR accounting.

So: the structural defect in the scan is real, but existing code already handles it via the explicit `else` branch at line 10361-10368. The repro assertion `len([h for h in result if h.direction=='buy_no']) == 0` would be TRUE — but the system does not rely on scan's buy_no output for any downstream decision. The adapter bridges around it deliberately.

---

## refuter:candidate=DEFECT (angle R10, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-ab448eab28361c063`

### Final Assistant Message

I have enough facts. The schema probing failed but the core investigation is complete. Let me render the verdict.

**Summary of findings:**

1. **Empirical claim confirmed:** Zero decision_certificates for 06-13+. Last cert was 2026-06-12T17:04 UTC. Also zero edli_live_order_events, zero decision_events, and edli_no_submit_receipts silent since 06-12T12:12. The blackout is real.

2. **The candidate's causal mechanism is WRONG.** The claim is that `reactor_mode not in EDLI_PROCESSING_REACTOR_MODES` fires and blocks processing. But `config/settings.json:77` has `"reactor_mode": "live"`, and `EDLI_PROCESSING_REACTOR_MODES = frozenset({"live", "live_no_submit", "submit_disabled_live_bridge"})` — "live" is in the set. The reactor_mode gate is NOT firing. The T0-3 config rename (line 152) was a property rename (`bias_correction_enabled` → `baseline_bias_correction_enabled`), not a reactor_mode change.

3. **The actual cause is upstream FSR starvation.** The last `FORECAST_SNAPSHOT_READY` event in `opportunity_events` is 2026-06-13T12:00 UTC. No FSR events have arrived since. Of 41,769 FSR events since 06-13, 41,593 are `expired` (correctly swept as past target_dates by `archive_expired_candidates`) and only 176 are `pending` — all with `available_at` on 06-13. The 857 `processed` events since 06-13 are ALL `DAY0_EXTREME_UPDATED` type, not FSR — they go through the reactor but produce no certs because day0 extreme events produce no decision certificates on their own. No FSR = no certs. The reactor itself is healthy; the ingest/forecast pipeline stopped emitting FSR events after 06-13T12:00.

4. **Would fixing the claimed defect (reactor_mode gate) move a real order?** No — the gate is not firing. Fixing a gate that is not firing does nothing.

---

## refuter:candidate=DEFECT (angle R3, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-ab8114635ab398a65`

### Final Assistant Message

All these parameters are exclusively used within `portfolio.py` itself — specifically in exit logic for existing positions. They are pure exit parameters.

Now I have all the data needed. Let me synthesize:

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-abb3a16966a927bce`

### Final Assistant Message

Now I have a clear picture. Let me synthesize the findings.

The defect claim has two parts:
1. REVIEW_REQUIRED has no automatic exit path - only manual/external proof triggers exits
2. `find_unknown_command_by_economic_intent` blocks any new submit at the same token/side/price/size indefinitely

**What I found:**

**Part 1 is partially wrong.** The code at `command_recovery.py:5990-5991` shows that the scheduled `reconcile_unresolved_commands` (runs every 3 minutes via `_edli_command_recovery_cycle`) DOES call `_review_required_cancel_unknown_live_order_recovery` for REVIEW_REQUIRED rows. This function CAN auto-advance REVIEW_REQUIRED to FILLED, ACKED (via REVIEW_CLEARED_VENUE_ORDER_LIVE), or EXPIRED (via REVIEW_CLEARED_NO_VENUE_EXPOSURE) when specific conditions are met: the command must have a CANCEL_REPLACE_BLOCKED event with `semantic_cancel_status=CANCEL_UNKNOWN` and `requires_m5_reconcile=True`.

**The critical gap:** For REVIEW_REQUIRED rows that originated from SUBMITTING ambiguity (no cancel_unknown payload - e.g., recovery_no_venue_order_id reason at line 6002-6014), `_latest_cancel_unknown_payload` returns None at line 4729, and the function immediately returns "stayed". These rows have NO automatic exit path - they permanently stay REVIEW_REQUIRED.

**Part 2 is confirmed.** `UNRESOLVED_SIDE_EFFECT_STATES` includes REVIEW_REQUIRED (line 44), and `find_unknown_command_by_economic_intent` queries all three states. Any REVIEW_REQUIRED row that stays will block new submits at the same economics indefinitely via `_orderresult_from_economic_unknown`.

**Kill analysis:**

(a) Is the ideal naive about a real constraint? No - the venue CAN be queried for SUBMITTING-ambiguity REVIEW_REQUIRED rows too (they have venue_order_id when the ACK was lost). The constraint is real (don't double-submit), but the code could auto-clear no-exposure cases even for the recovery_no_venue_order_id class.

(b) Does existing code already handle it? Partially - but only for CANCEL_REPLACE_BLOCKED/CANCEL_UNKNOWN sub-class, not for the common SUBMITTING-ambiguity class. The claim is substantively correct that the general REVIEW_REQUIRED case lacks automatic exit.

(c) Empirical bias? Not applicable - this is a pure structural claim about state machine paths, not an empirical observation.

(d) Would fixing it move a real order? The B1 blocker in the live evidence context is exactly a REVIEW_REQUIRED command blocking all submission via the ws_gap latch. This is a live, current blocker. Fixing the auto-exit for the general REVIEW_REQUIRED class would unblock that category. However, the B1 specific case involves `exchange_reconcile_findings` latch, not the economic-intent block from REVIEW_REQUIRED - these are separate mechanisms.

The defect is real for a class of REVIEW_REQUIRED rows, but the claim overstates it: the system DOES have automatic exit for one specific sub-class (cancel_unknown with M5). The "no automatic exit path" is true only for the SUBMITTING-ambiguity class. The economic-intent blocking consequence is real and correct as stated.

---

## angle:Friction / fees / slippage / threshold accounting.

**Agent ID:** `agent-abb4799cea3e1124a`

### Final Assistant Message

Now I have a complete picture. Let me compile the findings:

---

**1. MECHANISM — what this angle actually does**

The cost-accounting pipeline has two separate lanes that each compute a fee-adjusted entry price for the same candidate.

**Evaluator / BinEdge lane (classical path, non-EQE):** `_fee_rate_for_token` at evaluator.py:1757 fetches the schedule fraction from the CLOB `/fee-rate` endpoint (which returns `base_fee=1000 bps = 0.10` for weather markets). It then calls `resolve_taker_fee_fraction` (fee_authority.py:67), which reads `state/fee_reconciliation.json`. With 42 fills and `observed_max_fee_fraction=0.0`, and artifact age of ~1.2 days (well inside the 30-day staleness window), the function returns `fraction = min(max(0.0, 0.0), 0.10) = 0.0` and source `realized_fills_n=42_fitted=2026-06-12`. So the effective fee_rate flowing into `_size_at_execution_price_boundary` and the `ExecutionPrice.with_taker_fee()` call is **0.0**.

`market_analysis.py:582` computes `edge_yes = p_posterior - entry_cost_mean` where `entry_cost_mean = p_market` (no EQE) or `eqe.all_in_entry_price` (EQE path). The `forward_edge` on `BinEdge` is set to this same value. `_expected_profit_usd_for_edge` (evaluator.py:1334) reads `edge.forward_edge` first, then falls back to `float(edge.p_posterior) - float(price)` where price is the raw `edge.entry_price` value.

**capital_efficiency_lcb_ev lane (opportunity book / candidate_evaluation):** `live_capital_efficiency_rejection_reason` (live_admission.py:87) gates on `conservative_ev_per_dollar = (q_lcb - execution_price) / execution_price`. This is the gate cited in the failing log line at event_reactor_adapter.py:7110.

**ExecutableCostCurve (EQE-on path):** When `_evaluator_eqe_enabled()` returns true (env var `ZEUS_EVALUATOR_EQE_ENABLED=1`), `avg_cost_for_shares` (executable_cost_curve.py:359) walks the ask ladder and returns a fee-adjusted `ExecutionPrice` with `fee_deducted=True`. Its `FeeModel.fee_rate` is `Decimal("0")` when `resolve_taker_fee_fraction` returns 0.0. With `fee_rate=0` the `all_in_price = p + 0 = p`, so the cost curve degenerates to raw ask prices — no fee loading, which is correct given observed zero fees.

---

**2. DEFECTS**

**FINDING A — `_expected_profit_usd_for_edge` uses un-fee-adjusted price as denominator, producing inflated expected-profit figures. DEFECT_NO_CURRENT_IMPACT (fee_rate=0.0 so inflated by 0).**

evaluator.py:1343 falls back to `edge.p_posterior - float(price)` where `price = edge.entry_price`. When EQE is OFF and `fee_rate=0`, `entry_price` is the raw VWMP (a `price_type="vwmp"` ExecutionPrice with `fee_deducted=False`). `_size_at_execution_price_boundary` applies `ep.with_taker_fee(fee_rate)` (evaluator.py:1665) to produce the fee-adjusted price before sizing. But `_expected_profit_usd_for_edge` (called just after sizing at evaluator.py:6440) is passed `price=float(edge.entry_price)` — the RAW price, not the fee-adjusted one. The denominator `cost = float(price)` at evaluator.py:1345 is thus the raw ask, not the all-in cost. This means `shares_bought_per_dollar` is overstated and `expected_profit_usd` is slightly inflated. With fee_rate=0.0 currently, the delta is zero. If fees ever become nonzero, this produces an optimistic economic-floor check that allows slightly smaller notionals than a fee-correct denominator would.

**FINDING B — `FeeModel.fee_rate=Decimal("0")` silently makes `avg_cost_for_shares` a raw-ask identity, meaning the EQE path double-applies the zero-fee correction (no actual error but the FeeModel becomes a no-op object carrying dead overhead). DEFECT_NO_CURRENT_IMPACT.**

When `resolve_taker_fee_fraction` returns 0.0, `FeeModel(fee_rate=Decimal("0"))` is constructed. `all_in_price = p + 0 * p * (1-p) = p`, and the depth walk simply sums raw ask prices. The `fee_deducted=True` tag on the returned `ExecutionPrice` (executable_cost_curve.py:379) is then technically correct (fee=0 was deducted) but semantically vacuous. No downstream harm, but the provenance claim is misleading when inspecting receipts.

**FINDING C — `_walk_for_stake` (executable_cost_curve.py:282) is strict about depth exhaustion but `_walk_for_shares` (line 349) is not reached on the evaluator hot-path — the evaluator uses `_buy_entry_price_from_clob` (VWMP scalar) when EQE is OFF. Two separate pricing kernels for the same buy, with no consistency check between them. DEFECT_NO_CURRENT_IMPACT but structural semantic contradiction.**

The classic evaluator path uses a VWMP scalar from `_buy_entry_price_from_clob` (evaluator.py:378). The EQE path uses `avg_cost_for_shares`. When EQE is OFF (default unless `ZEUS_EVALUATOR_EQE_ENABLED=1`), the depth-walk cost curve object is built but only used for `has_executable_ask` detection (native_side_candidate.py:419). The economic floor, Kelly sizing, and expected-profit calculation all proceed off the scalar VWMP. This is correct only because the VWMP at the top-of-book level happens to be the min-order all-in price for a thin book. For a thick book this understates slippage — specifically, the issue identified as "stale-depth taker sizing" in task #58 (`GPT deep-review findings`). On its own this does not block a submission; it can allow a submission with understated cost, not prevent one.

**FINDING D — Redundant gate: `live_capital_efficiency_rejection_reason` (live_admission.py:87) and the evaluator's `ci_lo > 0` gate (market_analysis.py:585) both independently require `q_lcb > price`. The opportunity-book path's `capital_efficiency_lcb_ev` check is the mechanism that was reportedly blocking 12 of 16 candidates (event_reactor_adapter.py:7149). These are not defects — the gate is correct per task #66 noting it is the "honest q_lcb>price gate" — but the question is whether `q_lcb_5pct` and `execution_price` on `CandidateEvaluation` are the same basis. POTENTIAL ASYMMETRY worth flagging.**

`CandidateEvaluation.execution_price` is the raw entry price (not yet fee-adjusted at this point in the opportunity-book lane). `q_lcb_5pct` is the lower-confidence posterior from the bootstrap CI. The gate `(q_lcb - price) / price > 0` reduces to `q_lcb > price`. If `execution_price` is the fee-adjusted all-in cost (≥ raw ask), the gate is stricter than if it is the raw ask. With fee_rate=0.0 the two are identical and no candidates are blocked by this asymmetry. If `execution_price` is being populated with the raw VWMP rather than the fee-adjusted VWMP, the gate is looser than intended for nonzero fees but does not block anything incorrectly at present.

**FINDING E — `_strategy_live_quality_policy` (evaluator.py:1268) falls back to hardcoded floors (`min_entry_price=0.05`, `min_expected_profit_usd=0.05`, `min_strategy_notional_usd=1.0`) when no strategy profile is configured. These are load-bearing constants that gate every trade. They are directly in scope of task #64 (constant-elimination program). The `min_expected_profit_usd=0.05` floor in particular is an unfitted threshold that could reject genuinely +EV small penny-bin trades at tail prices. On its own: blocks certain small-size trades but not provably +EV trades in the general case, since the expected profit floor is computed off an edge that is positive by construction at this point in the path. DEFECT_NO_CURRENT_IMPACT on the macro symptom but a live over-built gate.**

evaluator.py:1275-1276: `min_expected_profit_usd=float(getattr(profile, "min_expected_profit_usd", 0.05) if profile is not None else 0.05)`. The 0.05 USD floor would kill a $0.30 notional trade at a 20% edge (expected profit = $0.06) — a real +EV trade. Reproduction: candidate with `p_posterior=0.72, price=0.60, size_usd=0.30` → `expected_profit = (0.72-0.60)*(0.30/0.60) = $0.06 > $0.05` — passes. At `size_usd=0.25` → `$0.05 = floor` → borderline pass. At `size_usd=0.20` → `$0.04 < $0.05` → BLOCKED despite positive EV. This is specifically an over-built gate.

**FINDING F — `collateral.py` fallback path (lines 37-55): the legacy `clob.get_balance()` branch computes `required = (1.0 - entry_price) * shares`. The comment says "entry_price > 1.0 shouldn't happen" but with fee-adjusted prices the all-in price g(p) = p + fee*p*(1-p) is always < 1 for fee_rate < 1, so this is structurally safe. The `token_id=''` fallback path is dead on the live exit path per its own comment. DEFECT_NO_CURRENT_IMPACT.**

**FINDING G — `SlippageBps` and `TickSize` are contracts built but neither is referenced by `live_capital_efficiency_rejection_reason`, the economic floor, or Kelly sizing. They exist as typed boundary objects with no current wiring into the admission path. Slippage cost is only indirectly captured via `EntryQuoteEvidence.cost_uncertainty` on the EQE path (which is OFF by default). ABSENT: a direct slippage term in the admission/EV gate.**

---

**3. WHAT IS NOTABLY ABSENT vs what a sound system needs**

The current `capital_efficiency_lcb_ev` gate computes `(q_lcb - price) / price` using the raw execution price (VWMP), not the fee-adjusted all-in cost. A sound system would compute conservative EV as `(q_lcb - g(p)) / g(p)` where `g(p) = p + fee_rate*p*(1-p)` is the all-in price from `FeeModel.all_in_price`. With `fee_rate=0.0` currently the two are equal and there is no gap. If fees become nonzero the gate would be 10% looser than intended at mid-market prices (g(0.5) = 0.5 + 0.05*0.25 = 0.5125 vs raw 0.5).

The `_expected_profit_usd_for_edge` function (evaluator.py:1334) does not include the taker fee in its cost denominator, meaning it estimates profitability using the entry price before fee loading. This is structurally sound only when fees are zero.

`SlippageBps` has no consumer on the admission or sizing path. Slippage beyond the top-of-book level is captured by `ExecutableCostCurve.avg_cost_for_shares` (EQE-on) but not wired into the classical VWMP path. The `_strategy_live_quality_policy` min floors are unfitted hardcodes that should be artifacts (task #64 is open for exactly this).

There is no dedicated penny-bin economics check beyond the `min_entry_price=0.05` floor and the `CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY=0.02` block. No quantity-weighted liquidity cost model for sub-penny tail bins.

---

**4. PROVENANCE VERDICTS**

`src/strategy/fees.py` — CURRENT_REUSABLE. Created 2026-05-22, authority STRATEGY_TAXONOMY_DIRECTIVE + math_spec §11.5. Formula `phi = C * r * p * (1-p)` correctly matches Polymarket V2. Fee rate is injected, not hardcoded.

`src/contracts/fee_authority.py` — CURRENT_REUSABLE. Created 2026-06-12, authority statistical_calibration_authority_2026-06-12 Task 2.3. The incident-driven fix is recent, the realized-evidence-first logic is correct, and the 30-day staleness window and 10-fill license threshold are explicit policy choices. The artifact currently has n_fills=42, observed_max=0.0, age ~1.2 days — functioning as designed.

`src/contracts/slippage_bps.py` — CURRENT_REUSABLE as a typed contract, but the contract is unconnected to the admission path. No authority reference date (no Created: header). Provenance gap.

`src/contracts/executable_cost_curve.py` — CURRENT_REUSABLE. Created 2026-06-08, audited 2026-06-09. The depth-walk arithmetic and fee model are sound. The maker-resting zero-fee path (line 218) is a spec-correct Fee Structure V2 implementation. This file is one of the most robustly documented in the codebase.

`src/contracts/tick_size.py` — CURRENT_REUSABLE. No authority date in header (no Created:). All Polymarket weather markets at 0.01 tick is correct per current venue state. The `for_market` classmethod is appropriately future-proofed.

`src/contracts/venue_submission_envelope.py` — CURRENT_REUSABLE. Created 2026-04-27, audited 2026-04-27. Pure provenance carrier; no cost terms. The `assert_live_submit_bound` gate is the correct live-submit antibody. Slightly stale relative to last reused date (the K1/K3 changes did not touch this contract).

`src/contracts/vig_treatment.py` — CURRENT_REUSABLE. Created 2026-04-12, audited 2026-04-24. The D5 violation detection (`applied_before_blend=False` raises) and the T6.3 sparse-impute path with explicit provenance are both correctly implemented.

`src/execution/collateral.py` — STALE_REWRITE candidate. No Created:/authority header. The `token_id=''` fallback path computes a pUSD-balance check that is explicitly dead for live exits. The live path (line 31-33) is correct but the file carries dead legacy code that should be cleaned up. Does not block submission on its own.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-abc97201483ff15b4`

### Final Assistant Message

The investigation is complete. Here is the verdict:

The candidate's claimed mechanism is wrong on both the code analysis and the empirical reproduction.

**Code flaw in the candidate's analysis.** The check at `no_trade_events.py:118` fires only when `0 < user_version < expected_schema_version`. The live DB has `user_version=43` and `expected_schema_version=42`, so the condition `0 < 43 < 42` is `False` — no raise. All other sub-checks (schema_compatibility column, strategy_key, event_source, shadow_runtime, `"42"` in table SQL) also pass because the live schema is current. The repro script produces no exception.

**The logs confirm a different failure mode.** Historical `NO_TRADE_EVENT_WRITE_FAILED` entries (May 21-23) all carry `exc=database is locked`, not `NoTradeEventsSchemaCompatibilityError`. Zero schema-error log entries exist across the full log history.

**The 0-row gap since 05-28 has a different and unrelated cause.** Current reactor output shows `processed=0 rejected=0 retried=119` with all events requeuing as `EXECUTABLE_SNAPSHOT_STALE` — candidates never reach the evaluation path that would invoke `write_no_trade_event` in the first place. The observability gap is real but its cause is the B1 submit latch and upstream stale-snapshot requeue storm, not the schema gate.

**Would fixing the claimed defect move a real order?** No. The schema check is not firing, and even if it were, `no_trade_events` is an instrumentation/learning table. Fixing it would restore decision-rejection audit records, not move any order toward a fill.

---

## angle:Minimal kernel / rebuild boundary / keep-list.

**Agent ID:** `agent-abcae6e4f7d3abe80`

### Final Assistant Message

## Audit Report: Minimal Kernel / Rebuild Boundary / Keep-List

### (1) Mechanism

The kernel angle governs what to KEEP versus collapse. The 14.8k reactor (`event_reactor_adapter.py`) is structured as two sequential layers: Layer A (per-candidate scoring + zeroing gates) feeding Layer B (family-level decision chain). The discovery gate in `cycle_runner.py` is the outer entry guard. The dispatch filter in `dispatch.py` controls which market phases enter. DDD wiring enforces coverage preconditions.

The rebuild boundary question is: how many of these gates constitute honest decisions versus redundant veto accumulation? The kcut authority (docs/archive/2026-Q2/operations_historical/kcut_decision_path_collapse_2026-06-13.md) answers: 174 seams collapse to K=5. What exists is K≫5, every gate independently capable of nullifying a genuinely +EV candidate.

---

### (2) Defects

**D1 — D8 FDR gate is the unconditional live selection authority (BLOCKS SUBMISSION)**

era.py:2811 hardcodes `authority_on=False`, making EB selection shrinkage permanently shadow-only. era.py:2866-2870 gates the entire family on `fdr.passed`, emitting `FDR_REJECTED` unconditionally if not. The BH/FDR test operates on `{0,1}` p-values (binary hypotheses), which the kcut doc explicitly condemns as vacuous. The EB replacement task (#60) is marked "completed" in the task list, but the flag enabling it was REMOVED, creating a semantic contradiction: the task is "done" but the mechanism it was supposed to activate (`authority_on=True`) does not exist in code.

Reproduction: any market family where all candidates have binary p-values (all or none pass FDR) will fire `FDR_REJECTED` and return no order. This is structural, not edge-case. Verdict: **BLOCKS SUBMISSION**.

**D2 — C4 capital_efficiency zeroing gate (KILLS +EV CANDIDATES)**

era.py:7535-7543. Fires when `q_lcb_no` after the market-anchor cap no longer covers execution cost. Sets `score=0.0` and `passed_prefilter=False`. A candidate with genuine edge (q_lcb_no > cost before cap) can be zero'd by C2 lowering q_lcb_no, triggering C4. The two gates compose silently: C2 lowers the bound, C4 rejects on the lowered bound, candidate never enters FDR as a passed hypothesis. Verdict: **KILLS +EV CANDIDATES** (chain C2→C4).

**D3 — C7 buy_no_conservative_evidence zeroing gate (KILLS +EV CANDIDATES)**

era.py:7549-7561. The function `live_buy_no_conservative_evidence_rejection_reason` (imported at era.py:213) fires on a calibration-source or settlement-coverage-status condition. If it fires, `score=0.0`, `passed_prefilter=False`. A buy_no candidate with calibrated q_lcb_no above cost is eliminated before FDR evaluation. Verdict: **KILLS +EV CANDIDATES**.

**D4 — C2 market-anchor cap is live, one-sided, actively suppresses NO-side edge (KILLS +EV CANDIDATES)**

era.py:7484-7488. The `_anchor.capped` branch lowers `q_lcb_no`. No corresponding raise exists (one-sided). This directly reduces the NO-side conservative bound, triggering C4 downstream. The cap flag (`market_anchor_cap_enabled()`, era.py:9625) defaults FALSE per the docstring but is described as live in the kcut doc. The interaction with C4 means a small cap can convert a +EV candidate into a zero-score/zero-prefilter candidate silently. Verdict: **KILLS +EV CANDIDATES**.

**D5 — EB_SHRINKAGE flag removal paradox: removing a feature flag made the gate stricter (BLOCKS SUBMISSION)**

The EB_SHRINKAGE flag was apparently removed 2026-06-13 (kcut doc: "EB-shrinkage decision-replacement flag was REMOVED 2026-06-13"). The intent was presumably to clean up a shadow flag. The effect was to hardcode `authority_on=False` at era.py:2811, making BH/FDR the permanent unconditional gate. This is a governance regression — the flag removal is documented in the comment as the reason BH/FDR is unconditional. Removing the flag did not simplify the gate; it locked it in the wrong position. Verdict: **BLOCKS SUBMISSION** (same mechanism as D1, distinct governance root cause).

**D6 — Layer A compound veto: four zeroing gates share a single `passed_prefilter=False` enforcement (KILLS +EV CANDIDATES)**

era.py:7599-7605. After four independent `if reason is not None: score=0.0` blocks, a compound `if (C4_reason or C7_reason or C5_reason or C6_reason): passed_prefilter=False`. Any single gate firing poisons the candidate for all downstream selection. This is correct in principle but means an over-sensitive gate (e.g., C7 misfiring on a calibration-source flag that's not actually operative) eliminates the candidate from FDR admission entirely. The gates are NOT independently reviewable in the output — the `missing_reason` captures the FIRST reason, masking which gate actually fired. Verdict: **KILLS +EV CANDIDATES** (observability defect compounds the blocking effect).

**D7 — Dispatch silent drop on market-phase parse failure under ZEUS_MARKET_PHASE_DISPATCH=ON (KILLS +EV CANDIDATES)**

dispatch.py:397-412. When `_is_settlement_day_phase(...)` returns None (parse error on the Gamma payload), `filter_market_to_settlement_day` returns False, dropping the candidate with a WARNING log. The flag is default ON (post-A6 comment). A market with a malformed or missing Gamma metadata payload is permanently excluded from dispatch. No error is raised. No counter is incremented. The drop is observable only by reading the WARNING log line. Reproduction: any candidate whose market's Gamma `scheduled_close_time` / `end_date` fails to parse will be silently excluded. Verdict: **KILLS +EV CANDIDATES**.

**D8 — Block registry is a single-point-of-failure for all entries (CONDITIONAL BLOCKER)**

cycle_runner.py:989-1033. `EntriesBlockRegistry.from_runtime(...)` is called once per cycle. If it raises any exception, `_block_registry = None`. At the gate (cr.py:383), `if block_registry is None: return False`. Verdict: any exception in block registry construction silently blocks all entries for that cycle. No explicit error surface to the operator beyond whatever logging the constructor emits. Verdict: **CONDITIONAL BLOCKER** (not confirmed active, but structural).

**D9 — Governor observability asymmetry (DEFECT_NO_CURRENT_IMPACT)**

cycle_runner.py:976. The `entries_blocked_reason` diagnostic string uses `.get("allow_submit", True)` (defaults True = "not the reason"), while the gate at cr.py:399 uses `.get("allow_submit", False)` (defaults False = "blocked"). If `governor_status` is missing the `allow_submit` key, the gate blocks but the reason string says "governor OK." The operator sees no governor block reason while the gate is actually blocking. This is an observability defect that makes diagnosis harder but does not independently cause the current symptom. Verdict: **DEFECT_NO_CURRENT_IMPACT** (but masks the cause when D8/block-registry fires).

**D10 — DDD permanent city exclusions (documented policy, not a code defect, but blocks real families)**

ddd_wiring.py:295-325. Paris (`DDD_EXCLUDED_WORKSTREAM_A`), HK, Istanbul, Moscow, Tel Aviv (`DDD_NO_TRAIN_DATA`) are fail-CLOSED excluded. These cities' families cannot receive orders. This is documented policy, not a bug. However, if the live candidate set is disproportionately these cities, the zero-order symptom is explained entirely at the DDD layer before any of the above gates even run. Verdict: **KILLS +EV CANDIDATES** (by policy, documented, not a defect — but included for completeness as a mechanism that can explain zero orders on certain market mixes).

---

### (3) What is Notably Absent

**K3 — E_log_growth > 0 as the single admission authority.** The kcut doc identifies this as the correct single gate: if Kelly-sized bet has positive expected log growth given the calibrated q and the execution price, admit it. Currently there is no such unified gate. Instead there are 22+ distinct decision points, each of which can independently veto. The system has multiple gates that approximate parts of K3 (C4 checks capital_efficiency, D4-D7 check various edge conditions) but no single authoritative E_log_growth evaluation that, if positive, guarantees admission.

**K4 — Horse-race Kelly applied once.** The kcut doc specifies horse-race Kelly (family total pinned to equity × fractional-Kelly, external-close absorption antibody). What exists is per-candidate Kelly with family-level aggregation. The distinction matters: horse-race Kelly selects the single best bet per family, while per-candidate Kelly can simultaneously size multiple candidates and trigger the FDR gate on the set rather than the winner.

**C1/C3 calibration honesty.** The replacement chain (T2 Bayesian precision fusion, EMOS, fused-normal-direct q-shape) is the strategy of record since 2026-06-09 but C1 (era-EB calibration) and C3 (JS shrinkage) are both operator-gated OFF. Without calibration honesty, `q_lcb_no` is computed from a potentially miscalibrated posterior, making C4 and C7 fire on candidates that are actually +EV under honest calibration.

**Unified K2 maker-quote surfacing.** The test `test_maker_quote_p_fill_not_taker_zero.py` (test_topology.yaml) documents a defect where the maker-quote lane returns `p_fill_lcb=0.0` (taker-shaped), zeroing `trade_score = p_fill * edge` for every maker buy_no. If this defect is live, every buy_no maker quote has trade_score=0 regardless of edge. The test exists but the fix's presence in the live path is not confirmed by the audit.

**Observable gate audit trail.** Each gate emits a `missing_reason` but only the FIRST reason is captured (era.py first-seen idiom). When C2→C4 chain fires, the reason is `C4_capital_efficiency`; C2's contribution is invisible. A sound system needs a reason-list (not first-wins) to diagnose stacked gate fire.

---

### (4) Provenance Verdicts

**`src/engine/event_reactor_adapter.py`** — CURRENT_REUSABLE with active regression at D8. Law regime: `replacement_final_form_2026_06_09` + `regime_unification_2026-06-12`. Last documented audit: 2026-06-12 (Wave-1 A–E fixes). The 2026-06-13 EB_SHRINKAGE flag removal introduced a governance regression (D1/D5). The file itself is current-law but the D8 gate is currently locked in a broken state. Do not delete; do not rewrite. Surgical fix: restore the EB replacement path or replace BH/FDR with K3 E_log_growth gate.

**`src/engine/cycle_runner.py`** — CURRENT_REUSABLE with gate-accumulation concern. Law regime: replacement chain + posture/risk level governance. Last meaningful gate changes: 2026-06-12 (Wave-1 canary deletion). The 13-condition discovery gate (cr.py:383-399) is individually correct but compound-fail-closed with no partial-pass path. Block registry single-point-of-failure (D8) is structural. Do not rewrite; the outer gate logic is sound. The governor asymmetry (D9) warrants a one-line fix.

**`src/engine/dispatch.py`** — CURRENT_REUSABLE with silent-drop risk. Law regime: ZEUS_MARKET_PHASE_DISPATCH post-A6. Created 2026-05-04. The silent-drop on parse failure (D7) is a latent +EV killer that is hard to observe. The file is not wrong in concept but the `result is None` → `False` path needs an explicit counter/alert. No rewrite needed; targeted fix at dispatch.py:409-412.

**`src/engine/ddd_wiring.py`** — CURRENT_REUSABLE. Law regime: DDD v2 fail-closed policy. Created 2026-05-03, audited 2026-05-15. The `observation_instants_current` VIEW inactive gap (D10 footnote) is a latent schema issue but does not affect current execution since the VIEW is not on the live path yet. The exclusion policy for Paris/HK/Istanbul/Moscow/Tel Aviv is documented and intentional.

**`architecture/test_topology.yaml`** — CURRENT_REUSABLE. The trust policy (lifecycle headers) and the 2026-06-13 test registrations are current-law. The `test_selection_shrinkage_wiring.py` note explicitly documents the EB_SHRINKAGE default-False contract, confirming D1/D5 is a known test-documented state.

**`docs/archive/2026-Q2/operations_historical/kcut_decision_path_collapse_2026-06-13.md`** — CURRENT_REUSABLE as design authority. Written today. It is the authoritative source for the K=5 target architecture and the Stage 1 unblock prescription. Status: design-only, no code changed. This document is the correct playbook for the surgical fixes.

---

**The chain most likely causing zero new orders today:** C2 (market-anchor cap lowers q_lcb_no) → C4 (capital_efficiency fires on the lowered bound, score=0, passed_prefilter=False) → D8 FDR gate runs on an empty/failed passed-hypothesis set → `FDR_REJECTED` → no order emitted. Breaking either D8 (replace BH/FDR with K3) or C4 (audit whether the post-cap q_lcb_no is genuinely below cost for all current candidates) is the Stage 1 unblock.

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 100):

**Agent ID:** `agent-abe34477aae99c099`

### Final Assistant Message

ROOT_CAUSE confirmed, no kill found. The defect survives all four kill angles:

The finding (5bbc2be2) is verifiably unresolved in DB with the double-count geometry (`expected=34.10, exchange=0`). All three absorber paths are blocked for this specific NO-side token:

- Settled-class absorber needs `calendar_terminal.get(token) != None` — EMS has 0 rows for this NO token (confirmed), so the condition_id bridge returns empty, the market_events lookup fails, the absorber skips.
- Operator-ack absorber needs a prior resolved row with `resolved_by LIKE 'session_operator_confirmed%'` — none exists.
- Ghost-migration paths only handle `exchange_ghost_order` kind.
- Token suppression has 0 rows.

Every reconcile cycle records the finding, `list_unresolved_findings` returns 1 row, `run_ws_gap_reconcile_and_clear` returns `"blocked"` (line 331), and the submit latch never clears. Fixing it would directly reopen the submit lane — the only confirmed barrier between the system and live order emission.

---

## angle:Latency / alpha decay / market-lifecycle timing.

**Agent ID:** `agent-abe6eafbcd277a812`

### Final Assistant Message

I have everything I need for the full report. Here it is.

---

ANGLE: Latency / Alpha Decay / Market-Lifecycle Timing

---

MECHANISM (what these five files actually do)

freshness_gate.py evaluates the age of per-source ingest signals against fixed budgets (6h–36h). Its mid-run path degrades to STALE or all-STALE when source_health.json is absent, but never blocks the cycle outright — the cycle_runner at cycle_runner.py:624 short-circuits only when day0_capture_disabled is true AND the mode is a settlement-day or IMMINENT_OPEN_CAPTURE mode. In OPENING_HUNT it continues with a degraded flag.

ws_gap_guard.py is a process-global in-memory latch (module-level `_status` singleton). It starts with `m5_reconcile_required=True` and `gap_reason="not_configured"`. It is the submit gate: `assert_ws_allows_submit` (executor.py:88) raises `WSGapSubmitBlocked` when `blocks_market` returns True. The gate clears on the first inbound PING/PONG from the user channel if and only if the local side-effect surface is empty (no unresolved venue_commands, no unresolved lots, no open reconcile findings). If ANY of those conditions is non-empty, it stays armed and only `clear_after_m5_reconcile` — a full venue sweep with zero unresolved findings — can release it.

replacement_current_value_serving.py is the single authority for which model row feeds the multi-model fusion. It serves single_runs over previous_runs for the same natural key, with an unknowable captured_at failing open (age treated as 0.0 rather than rejected). This file is structurally sound for the timing angle.

continuous_redecision.py implements the inter-forecast re-decision screen (P2). It reads cached beliefs from probability_trace_fact, joins them to the freshest executable_market_snapshots rows via condition_ids, runs a cheap edge test, and emits EDLI_REDECISION_PENDING events. The scheduler job at main.py:6303 arms it only when `edli.enabled`, `edli.event_writer_enabled`, and `edli.reactor_mode == "live"` are all set.

time_context.py computes city-local lead times. It is clean utility code with no submission logic.

---

DEFECTS

DEFECT 1 — buy_no direction dead in the P2 continuous-redecision screen. BLOCKS SUBMISSION of NO-side entries between forecast cycles.

continuous_redecision.py:367: `for direction, posterior in (("buy_yes", yes_post),):` — the `for` loop iterates over exactly one element. The buy_no direction is built into `read_freshest_executable_prices` (it computes the implied NO ask at line 549) and into `screen_entry_redecisions`'s price_lookup keying at continuous_redecision.py:586 (`for direction in ("buy_yes", "buy_no"):`), but `enqueue_live_redecisions` only ever generates a `buy_yes` key. The buy_no entries placed in price_lookup are never consumed by the screen. Any NO-side candidate that would fire on price movement between forecasts is silently dropped. The reconciliation in `screen_entry_redecisions` is correct on the price side but the upstream screen is deaf to it.

Recipe: call `enqueue_live_redecisions` with a `price_lookup` that has `buy_no` entries with positive edge; the function returns an empty list for them. On its own this would only block the *continuous-redecision* path for NO entries — the main FSR-driven path is unaffected. If the system's current live edge is predominantly on NO (operator-noted buy_no ~90% fill class in memory notes), this gate kills the ~5–6h inter-forecast re-decision lift for the entire NO side. BLOCKS SUBMISSION (for the redecision lane only; FSR cycles still see NO).

DEFECT 2 — acted_state perpetual suppression: once a family's edge fires, its entry in `_edli_redecision_acted_state` is never evicted.

continuous_redecision.py:377–381 and main.py:2215. `acted_state[key] = edge` is written on every fire. The anti-twitch guard at line 379 checks `edge <= last + IMPROVE_DELTA`. This means a family that fired at edge=0.05 will not re-fire unless the new edge exceeds 0.07 (0.05 + 0.02). If price moves adversely after a fire (edge falls back below the threshold), and then recovers to exactly 0.05 again, the family is permanently suppressed — the guard only re-arms when edge strictly exceeds `last + IMPROVE_DELTA`, not when the edge dropped and recovered. There is no TTL, no eviction on family-level state change, no purge after a fill, and no purge after a forecast update (the belief snapshot_id is not factored into the acted_state key). A family that was previously profitable, filled, had the edge narrow, and then re-opened identical edge can be permanently silenced by this module-level dict growing stale. KILLS REAL +EV CANDIDATE (slow-onset, session-length accumulation; severity scales with runtime without restart).

DEFECT 3 — buy_no P2 price key mismatch (a corollary of Defect 1, independently present).

`screen_entry_redecisions` at continuous_redecision.py:585–588 keys `price_lookup` as `(belief.family_id, label, direction)`, correctly inserting buy_no entries. `enqueue_live_redecisions` consumes price_lookup with key `(belief.family_id, label, direction)` at line 368. But because the outer loop only ever sets `direction="buy_yes"`, the buy_no keys in price_lookup go to waste. This is the same root as Defect 1 — listed separately because it also means any caller who passes a price_lookup with buy_no entries directly to `enqueue_live_redecisions` (not via `screen_entry_redecisions`) gets the same dead screen. DEFECT_NO_CURRENT_IMPACT beyond Defect 1 (same code path).

DEFECT 4 — ws_gap latch stuck permanently if any open lot or unresolved finding exists at boot.

ws_gap_guard.py:95–102: module boots with `m5_reconcile_required=True`. The fast-clear path in `_record_subscribed_message` (polymarket_user_channel.py:613–614) gates on `_local_side_effect_surface_empty()`. If any `position_lots` row has an unresolved state OR any `exchange_reconcile_findings` row has `resolved_at IS NULL`, the surface is non-empty, the fast-clear is skipped, and `clear_after_m5_reconcile` (exchange_reconcile.py:336) is the only exit. `clear_after_m5_reconcile` itself requires both `subscription_state in {"AUTHED","SUBSCRIBED"}` AND `not is_stale()` AND `findings_count=0`. The reconcile sweep at exchange_reconcile.py:306–343 only runs when called explicitly; it is not guaranteed to run on every cycle. The 2026-06-09 deadlock-fix comment (polymarket_user_channel.py:636–646) documents the "two-proof" design but the code requires a non-stale subscription to call `clear_after_m5_reconcile`, and if the reconcile sweep fires between pong refreshes (>30s gap), the guard goes stale first. The two proofs form an ordering dependency: pong must arrive before the sweep evaluates subscription health. Under load or slow reconnect the window can be missed repeatedly. BLOCKS SUBMISSION (transient but can be multi-hour if reconcile sweep is slow or sweep findings > 0).

DEFECT 5 — `_materialize_stale_gap` mutates global state as a side effect of `summary()`.

ws_gap_guard.py:120–129. `summary()` calls `_materialize_stale_gap()`, which calls `record_gap("stale_last_message")` if the guard is stale and m5_reconcile_required is False. That `record_gap` call writes a new global `_status` with `m5_reconcile_required=True`. This means ANY read of `ws_gap_guard.summary()` (cycle_runner.py:699, 898; status_summary.py) that fires when the last message timestamp is >30 seconds old silently re-arms `m5_reconcile_required`. The promotion is permanent until M5 clears. This is a read-path that has write side effects — a semantic contradiction. Any observability poll or logging call can trigger a submission block. DEFECT_NO_CURRENT_IMPACT if the WS stream is healthy (last_message_at stays fresh), but in any period of stream latency it converts a transient staleness into a durable M5-required block via a path the operator would not expect to be mutating.

DEFECT 6 — FRESH verdict when a source has no last_success_at at all is silently treated as STALE rather than ABSENT.

freshness_gate.py:206: `is_fresh = (age is not None) and (age <= budget_seconds)`. When `last_success_at` is missing from the source's dict (`age is None`), `is_fresh=False` and `is_stale = not is_fresh and not is_overridden = True`. The budget check is semantically "this source has never succeeded" but is classified identical to "succeeded too long ago." The stale-source list includes it without a distinct label, so an operator reading logs cannot distinguish "GFS has not succeeded for 36h" from "GFS has no success record at all (ingest never ran for this source)." DEFECT_NO_CURRENT_IMPACT (does not block or kill more than it already does by marking STALE), but masks a root-cause diagnostic.

---

ABSENT: what a sound system needs and lacks

The P2 redecision screen has no connection back to the cycle's alpha-decay window. `freshness_deadline` on each price quote is used as a stale-price guard, but there is no gate that asks: "how many hours until this family's settlement window closes?" A family with 2h until settlement should not be re-decided on a price-screen-driven redecision if the residual alpha horizon is shorter than the round-trip cost of a cert + fill. `time_context.py` provides `lead_hours_to_settlement_close` but nothing in this angle calls it. Result: the redecision screen can emit events for markets that are effectively at settlement (alpha = 0) with only the 30-second WS stale threshold as a de facto lifecycle gate.

The acted_state dict has no cross-cycle coherence with the belief snapshot. When a new FSR lands (new snapshot_id), the P2 screen should treat the edge as "new evidence → act regardless of prior acted_state," but the key is `(family_id, label, direction)` with no snapshot_id component. A family that upgraded from a weak belief to a strong one in a new FSR can be suppressed by an old high acted edge. The FSR-driven re-emission at main.py:5702–5727 bypasses the acted_state dict entirely (it goes through `scan_committed_snapshots`), so this only affects the between-FSR price-screen path — but that is exactly the path designed to fill the inter-forecast gap.

---

PROVENANCE VERDICTS

freshness_gate.py: Created 2026-04-30, authority basis 2026-05-01 directive. The written_at-as-ABSENT logic was correctly fixed. The per-source budget constants (6h–36h) are operator-set, not fitted. CURRENT_REUSABLE for its stated purpose; the STALE-vs-ABSENT ambiguity (Defect 6) is cosmetic. Law regime: S-4 / 2026-05-01 live directive.

ws_gap_guard.py: Created 2026-04-27, last audited 2026-05-16, authority M3.yaml. The module carries several layers of patches (live-blockers fix, 2026-05-01; 2026-06-09 deadlock fix). The `_materialize_stale_gap` write-on-read side effect (Defect 5) and the ordering dependency in the two-proof clear path (Defect 4) are unresolved. CURRENT_REUSABLE for the core submit-gate function; QUARANTINED for the summary/observation side-channel (Defect 5 is a live mutation hazard).

replacement_current_value_serving.py: Created 2026-06-11, last audited 2026-06-11. Cleanly implements the 没有新的就用老的 law. CURRENT_REUSABLE.

continuous_redecision.py: Created 2026-05-31, last audited 2026-06-12. The buy_yes-only loop (Defect 1) and the unbound acted_state (Defect 2) are live defects introduced or preserved in the 2026-06-12 resurrection. STALE_REWRITE for the `enqueue_live_redecisions` loop body (one-line fix: add `buy_no` to the direction tuple) and for the acted_state key (snapshot_id must be part of the key or acted_state must clear on FSR receipt).

time_context.py: No creation date in header. Logic is a stateless UTC/ZoneInfo helper. CURRENT_REUSABLE. No submission logic — not a blocking vector.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-abead220fc58accff`

### Final Assistant Message

Now I have enough. Let me synthesize the kill attempt across all four axes.

**Axis (a) — naive about real constraint?** The position_drift finding exists for a real reconciliation discrepancy: journal says 17.05 tokens exist, exchange says 0. A zero-threshold IS sensible risk policy when book integrity is unknown, so the gate itself is not naive. No kill here.

**Axis (b) — does existing code already handle it?** The auto-resolve paths in `exchange_reconcile.py` would handle it IF the token were in `token_suppression`, OR if exchange matched available_wallet, OR visibility floor (only covers <0.01 drift, not 17.05). None apply. Token is NOT in `token_suppression` (verified live). The code intended for this case (task #31 "Settled-class external-close absorber") requires the token to be in `token_suppression` — but it isn't. The absorber was supposed to insert it, not the other way around. No kill here.

**Axis (c) — empirical claim under-powered?** The COUNT(*) query returns exactly 1. The finding details exactly match the described token (5bbc2be2..., position_drift, ws_gap, exchange_size=0). No ambiguity. No kill here.

**Axis (d) — would fixing it move a real order to a real fill?** The governor kill switch is in the direct path to `allow_submit`. `count_open_reconcile_findings` returns 1 > `reconcile_finding_limit=0` → `kill_switch_reason()` returns `"reconcile_finding_threshold"` → `allow_submit=False` for ALL entries. The WITNESS confirms decision_certificates went dark coinciding exactly. Fixing this (INSERT into token_suppression to trigger the auto-resolve path in the reconciler, or direct UPDATE resolved_at) would unblock the submission gate and allow candidates with real edge to proceed. The B2 edge question (are there real winners) is separate — but B1 is the mechanical block preventing even that determination from reaching submission. This is a real order blocker.

The only potential kill angle I can see: could the B1 block be a coincidence, and something else is blocking? But the code path is unambiguous — `count_open_reconcile_findings=1 > 0 = reconcile_finding_limit` → kill switch fires. The timing correlation with 2026-06-12T22:58 is exact. No other path makes allow_submit=False given GREEN heartbeat, no drawdown threshold breach, no unknown side effects mentioned as blocking.

---

## angle:Observability / no-trade provenance / debuggability.

**Agent ID:** `agent-abf0a3409bcedf85b`

### Final Assistant Message

I now have enough data for a complete picture. Let me compile the findings.

---

FINDINGS — Observability / no-trade provenance / debuggability angle

---

**1. What these modules actually do**

The six files form two largely disconnected provenance systems running in parallel:

**System A (cycle-layer, pre-reactor):** `no_trade_events.py` is a writer for a `no_trade_events` world-DB table. `decision_chain.py` provides the `CycleArtifact`/`NoTradeCase` dataclasses that `cycle_runtime.py` accumulates during candidate evaluation, then stores via `store_artifact` into `decision_log`. `chronicler.py` is an append-only event log for the chronicle table. `regret_decomposer.py` is a post-hoc realized-advantage decomposer — write-only, purely analytical, no role in live decisions.

**System B (reactor-layer, post-event):** `no_submit_receipts.py` (`EdliNoSubmitReceiptLedger`) writes `edli_no_submit_receipts`, a rich per-event-intent receipt table. `no_submit_projection.py` provides a single read function (`no_submit_projection_rows`) that JOINs receipts to verified `decision_certificates`.

**2. Defects**

**DEFECT 1 — `no_trade_events` has been silent since 2026-05-28; the write path fires but nothing lands (BLOCKS PROVENANCE, not orders)**

`write_no_trade_event` at `src/state/no_trade_events.py:241` calls `assert_no_trade_events_schema_current_for_live` unconditionally unless `allow_schema_compatibility_downgrade=True`. The call site at `src/engine/cycle_runtime.py:5475` never passes `allow_schema_compatibility_downgrade`, so the default `False` applies. The assertion checks `SCHEMA_VERSION=42` against the DB's `user_version` and the CHECK constraint in the table DDL. The DB's last `no_trade_events` row is dated `2026-05-28T06:10:11`. If the live DB was migrated or the schema fingerprint check (`str(expected_schema_version) not in table_sql`, line 125) fails — for example because the B2a fingerprint commit (`414cdfdd7c`) bumped SCHEMA_VERSION in code but a migration was not applied — every call to `write_no_trade_event` raises `NoTradeEventsSchemaCompatibilityError`. The cycle-runtime wraps this with a bare `except Exception` at line 5485 and logs `[NO_TRADE_EVENT_WRITE_FAILED]`, swallowing the failure. The outer `except Exception` at line 5492 covers connection-open failures. **Effect: every rejection since 05-28 is silently discarded from `no_trade_events` without crashing the cycle. Does NOT block a submission, but eliminates the primary pre-reactor provenance table.**

Reproduction: call `assert_no_trade_events_schema_current_for_live(conn, expected_schema_version=42)` against the live world DB — if it raises, every no_trade_events write has been silently eaten for ~17 days.

**DEFECT 2 — `edli_no_submit_receipts` went silent at `2026-06-12T12:12:20`; the reactor has 2,113 RISK_GUARD_BLOCKED events but zero new receipts since (POSSIBLE SUBMISSION BLOCK)**

The live DB shows `edli_no_submit_receipts` last row at `2026-06-12T12:12:20` and only 32 rows total post-06-12 (all pre-12:12 that day). Yet `no_trade_regret_events` has 14,164 rows active through `2026-06-14T04:31:57`. The dominant regret reason is `RISK_GUARD_BLOCKED` (2,113 rows). In the reactor at `src/events/reactor.py:1455`, `RISK_GUARD_BLOCKED` causes `_transient_requeue_reasons[event.event_id] = "RISK_GUARD_BLOCKED"` and `return _EXECUTABLE_SNAPSHOT_RETRY, False` — the event requeues, never reaching the point where a receipt is written to `edli_no_submit_receipts`. A requeued event only reaches `insert_idempotent` when it exits the transient lane (via a semantic market-horizon or operator disarm). If the RiskGuard has been persistently RED or stale-RED since ~12:12 on 06-12, every pending event bounces as RISK_GUARD_BLOCKED forever and no receipt is written. The `edli_no_submit_receipts` silence is therefore a downstream symptom of the RiskGuard gate refusing events rather than a write-path bug. **This DIRECTLY blocks submission: no event that is RISK_GUARD_BLOCKED gets to the executor.**

Reproduction: query `SELECT rejection_reason, COUNT(*) FROM no_trade_regret_events WHERE rejection_stage='RISK_GUARD' AND created_at > '2026-06-12T12:00:00'` — 2,113 rows, all `RISK_GUARD_BLOCKED`, confirms the gate has been firing continuously for ~40 hours.

**DEFECT 3 — `no_submit_projection_rows` (src/events/no_submit_projection.py:12) is dead code with no production caller (DEFECT_NO_CURRENT_IMPACT)**

The function is defined and exported but the only call sites found are: zero in production paths. It appears once in `src/engine/event_reactor_adapter.py` as the name `projection` (line 6132) passing a dict, not calling this function. The function itself requires `decision_certificates` with `verifier_status='VERIFIED'`, which means it silently returns 0 rows if no certificates exist. No diagnostic tool, operator script, or module calls it for live "why no trade" queries. **Effect: the one JOIN-based provenance surface that links receipts to certificates is unreachable from any running code path.**

Reproduction: `grep -rn "no_submit_projection_rows" src/` — one definition, zero callers.

**DEFECT 4 — `decision_chain.py:query_no_trade_cases` reads from `decision_log` blobs, but `decision_log` has no rows with `mode='live'` (DEFECT_NO_CURRENT_IMPACT for now, structurally broken provenance)**

`query_no_trade_cases` at `src/state/decision_chain.py:407` scans `decision_log` rows and extracts `no_trade_cases` from the `artifact_json` blob. The DB shows `decision_log` has no rows at all (`MAX(timestamp)` = NULL). The cycle-runtime writes `NoTradeCase` items to `CycleArtifact` (line 5436 context) and `store_artifact` at `src/state/decision_chain.py:196` inserts to `decision_log`, but the comment at line 206 says "does NOT commit internally — caller owns the commit." If the caller never commits or the cycle no longer calls `store_artifact`, these blobs are lost. With a dead `decision_log`, `query_no_trade_cases` returns empty, `query_lifecycle_funnel_report` returns `certified_empty` for the no-trade lane, and operators see a false `"empty_trade_tables_certified": True` if `source_errors` is also empty. The "why no trade" question answered by `decision_chain` is unanswerable because its data source has never been populated in the live DB.

**DEFECT 5 — `chronicler.log_event` silently returns when `_canonical_position_surface_available` is True and the `chronicle` table doesn't exist (DEFECT_NO_CURRENT_IMPACT)**

At `src/state/chronicler.py:56-58`, if `chronicle` table is absent but `position_events`+`position_current` have the canonical columns, the function silently returns instead of raising. This is an intentional silent-migration path but it means chronicle events are dropped without any log message when the table was removed/never migrated. The chronicle is not queried by any of the analysis functions in scope, so there's no provenance gap from it in the current system, but absence of chronicle entries for trade events means audit trails are thinner.

**DEFECT 6 — `regret_decomposer.py` has no callers in the live system (DEAD_DELETE candidate, DEFECT_NO_CURRENT_IMPACT)**

`decompose_regret` and `write_regret_decomposition` are defined at `src/analysis/regret_decomposer.py`. The `regret_decompositions` table is not in the schema files queried by any live cycle path. This is entirely a placeholder from the Phase 6 PLAN dated 2026-05-21; the plan reference (`07_PHASE_6_EVIDENCE_LADDER.md`) is from that same day. No live code writes to this table. **Does not block submissions or kill candidates.**

---

**3. What is notably ABSENT**

The system has no single queryable surface that can answer "why was candidate X (slug, bin, direction) not submitted at time T" end-to-end:

- `no_trade_events` is silent since 05-28 (Defect 1).
- `decision_log` (backing `query_no_trade_cases`) has never been populated in the live DB.
- `edli_no_submit_receipts` only gets rows AFTER events survive the RiskGuard transient gate, so RiskGuard-blocked events leave no receipt.
- `no_trade_regret_events` IS active (14k rows through 06-14) and contains the rejection stage + reason per event, plus `q_live`, `q_lcb_5pct`, `c_fee_adjusted` — this is currently the ONLY queryable provenance for "why was event E rejected," but it is keyed by `event_id`, not by `(market_slug, target_date, bin_label)`, making "why no trade for market X at T" require a join through `opportunity_events`.
- No operator-facing tool surfaces `no_trade_regret_events` as the primary diagnostic surface. `query_lifecycle_funnel_report` (decision_chain.py:559) queries `position_events` and `decision_log.no_trade_cases` — neither is populated — and emits `certified_empty`, which is a false-clean signal.
- `live_health.py`'s `_business_plane_surface` reads `status_summary.json` counters but does not query `no_trade_regret_events` or `edli_no_submit_receipts` directly, so it cannot distinguish "no trades because RiskGuard is RED" from "no trades because all edges negative."

---

**4. Provenance verdicts**

`src/state/no_trade_events.py` — **STALE_REWRITE**. Written 2026-05-20 under Phase 2 T2 law. The schema fingerprint/version check (SCHEMA_VERSION=42, B2a) silently breaks live writes via fail-soft wrapping in cycle_runtime. The file's mechanism is correct but its schema check fails closed against the live DB and the write path absorbs the error without surfacing it.

`src/state/decision_chain.py` — **STALE_REWRITE**. Written pre-Phase-4 (no creation header). Assumes `decision_log` is populated by a `store_artifact` commit. That commit path is either absent or failing in the live system (decision_log has 0 rows). `query_no_trade_cases` and `query_lifecycle_funnel_report` return analytically wrong results (false empty) against the live DB.

`src/state/chronicler.py` — **CURRENT_REUSABLE** for the chronicle write path; the silent-return on missing table is a known intentional migration bridge. Does not affect current submissions.

`src/events/no_submit_receipts.py` — **CURRENT_REUSABLE**. Last audited 2026-06-12 per reactor header. The write mechanism is sound. The silence since 12:12 on 06-12 is caused by the RiskGuard gate upstream, not by this module.

`src/events/no_submit_projection.py` — **DEAD_DELETE**. No production caller. The JOIN requires VERIFIED certificates; under any conditions where receipts are absent (current state), returns 0 rows. Provides no diagnostic value and no coverage over the current no-trade period.

`src/analysis/regret_decomposer.py` — **DEAD_DELETE**. Written 2026-05-21, never integrated into a live cycle. The `regret_decompositions` table is not populated by any production path. Zero impact on submissions or candidates.

`src/control/live_health.py` — **CURRENT_REUSABLE** but **INCOMPLETE**. The business-plane check at line 191 fires `CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS` if intents=0 and no no-trade reason proof is in the status_summary JSON counters. If cycle_runtime's summary dict omits the right counter names (the checks alias `no_trades`, `no_trade_count`, `top_no_trade_reasons`, `no_trade_reasons`, `rejection_reason_counts` but NOT `no_trade_regret_events`), the health check may report `ok=True` even while 2,100 events are blocked at RISK_GUARD. The gate reads a JSON file, not the DB, so RiskGuard storms invisible to the DB-based provenance chain are also invisible here unless cycle_runtime correctly populates the status JSON keys.

---

**Summary of submission-blocking findings:**

The dominant live blocker with evidence is the RISK_GUARD gate: 2,113 `RISK_GUARD_BLOCKED` entries in `no_trade_regret_events` since 2026-06-12T12:12, with zero `edli_no_submit_receipts` rows after that timestamp. Every pending event requeues at `src/events/reactor.py:1455-1456` and never reaches the executor. The observability infrastructure cannot confirm or deny this independently because `no_trade_events` has been silent since 05-28 (Defect 1), `decision_log` is empty (Defect 4), and `no_submit_projection_rows` has no callers (Defect 3) — so "why no trade for X at T" cannot be answered from persisted data. The only live provenance source is `no_trade_regret_events`, keyed by event_id, showing the system is evaluating events but blocking them all at RISK_GUARD before any receipt is produced.

---

## refuter:candidate=DEFECT (angle R13, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-ac17e4e0e881a1f79`

### Final Assistant Message

Now I have a complete picture. Let me synthesize the findings:

**Key facts:**

1. `list_unresolved_findings` at line 1800 truly has no context/age filter — confirmed by reading the code.

2. `run_ws_gap_reconcile_and_clear` at line 320 calls `list_unresolved_findings(conn)` with NO kind filter — confirmed. Line 331: `if findings or unresolved: block`.

3. The specific blocking finding (5bbc2be2, position_drift, ws_gap context, token 2599807256...) — DB query shows `resolved_at = 2026-06-14T06:01:47` — it IS NOW RESOLVED. The LIVE EVIDENCE seed was recorded from 06-13 state; the finding was resolved between then and now.

4. The auto-resolution path exists: `run_reconcile_sweep` (line 699) calls `_resolve_foreign_wallet_ghost_findings` and `_resolve_operator_acknowledged_ghost_findings`, and the settled-class external-close absorber at line 2467 calls `_resolve_open_position_drift_findings`. These run BEFORE `list_unresolved_findings` is checked at line 320. So for the specific token_suppression + settled-class case (which is what 5bbc2be2 was), the finding auto-resolves within `run_reconcile_sweep` on the very next sweep.

5. Current unresolved count: 0. The system is not presently blocked by any stale finding.

**Assessment of the defect claim:**

The claim is structurally TRUE — `list_unresolved_findings` has no age/context filter and any unresolved row globally blocks the latch. The described scenario is mechanically possible. However:

- The specific instance (5bbc2be2) was auto-resolved by the built-in absorbers (settled-class external-close absorber, task #31). The absorbers run WITHIN `run_reconcile_sweep` before the gate check, meaning swept winners auto-resolve on the next sweep cycle.
- The repro scenario (INSERT an old `exchange_ghost_order` finding) would work mechanically — but ghost order findings also go through `_resolve_disappeared_ghost_order_findings` (line 700-702) on every sweep, which would auto-resolve them too if the order is no longer resting.
- There IS a residual gap: a finding of a kind that has NO auto-resolver (e.g., `unrecorded_trade` from a prior operator manual trade that is not linkable) could persist indefinitely and block the latch. But ghost_order findings are actively swept.

The real issue is: as of now there are 0 unresolved findings and B1 is not blocking. The defect exists in the abstract but the existing auto-resolution machinery covers the dominant cases. The repro's `exchange_ghost_order` example would actually be resolved by `_resolve_disappeared_ghost_order_findings` on the next sweep (since the order doesn't exist at venue). The weaker residual risk is `unrecorded_trade` from a foreign wallet trade — but that is handled by `_resolve_foreign_wallet_ghost_findings` and `_resolve_operator_acknowledged_ghost_findings`.

The defect is a real structural gap (no time-window on the global unresolved query) but the dominant concrete scenarios are auto-healed by the sweep itself. The LIVE EVIDENCE seed "B1 VERIFIED PHANTOM" was resolved by the absorber. This is EDGE_DEFECT — the machinery is incomplete (a sufficiently exotic unresolvable finding from another context/kind could permanently block), but the claim as stated (any stale finding permanently blocks) overstates because auto-resolvers cover the enumerated cases. Fixing it would not have moved the 06-12 order since the absorber already handled it.

---

## refuter:candidate=DEFECT (angle R8, tag MECHANICAL_BLOCK, impact 99):

**Agent ID:** `agent-ac48154c7ca4bd8f8`

### Final Assistant Message

I now have sufficient evidence. Let me synthesize.

The repro confirms the empirical facts but the candidate's framing has a critical architectural flaw. Here is my kill analysis:

**What the data shows:**
- `decision_certificates` MAX decision_time: 2026-06-12T17:04:15 UTC. Frozen. Confirmed.
- `edli_no_submit_receipts` last row: 2026-06-12T12:12:20 UTC. Confirmed.
- `venue_commands` last row: 2026-06-12T13:04:26 UTC. Confirmed.
- EMS (`executable_market_snapshots`) still healthy: 556k rows after 2026-06-13, latest captured_at 2026-06-14T05:44 UTC.
- One active `exchange_reconcile_findings` row (5bbc2be2, ws_gap, position_drift, unresolved) from 2026-06-12T22:58 UTC.

**The architectural kill (criterion b):**

`decision_certificates` are written by `reactor.py:1594` (`persist_all`) only when the reactor processes an event and produces a receipt. The reactor is invoked from `src/events/reactor.py` by the EDLI event pipeline. The B1 ws_gap latch froze `allow_submit=False` at 22:58 UTC on 06-12, but the last `decision_certificates` row was at 17:04 UTC, over 5 hours BEFORE the B1 latch frozen. So B1 cannot explain the decision_certificates gap starting at 17:04.

More critically: `decision_certificates` are written for BOTH `NO_SUBMIT` paths (reactor line 1594) and execution receipt paths (line 1647). They are NOT exclusively post-submit artifacts — they are also written for `NO_SUBMIT` processing. The last `edli_no_submit_receipts` write was at 12:12 UTC, also before the decision_certificates freeze. This means the reactor itself stopped processing events somewhere between 12:12 and 17:04 UTC (the last cert batch was at 17:04 but for a prior decision_time).

**The candidate claim's framing issue:** "Decision engine completely dark after 2026-06-12" describes the symptom accurately but attributes it loosely to a MECHANICAL_BLOCK without specifying the mechanism. The evidence shows the reactor went silent (no new receipts, no new certificates) BEFORE the B1 ws_gap latch was frozen (22:58 UTC). The real cause is likely the reactor's event queue being drained/exhausted or a daemon crash rather than the submit gate. The submit gate being frozen is a separate secondary blocker even if the reactor were running.

However, the claim does NOT say B1 is the cause — it just names MECHANICAL_BLOCK. The actual empirical claim (zero certs after 17:04 despite EMS ingestion) is CONFIRMED. The "would fixing it move a real fill" criterion (d): fixing the submit gate (B1) would unblock submission IF the reactor were processing events, but since no receipts have been written since 12:12, something upstream of submission is broken first.

The defect as stated (decision engine dark, mechanical block) is factually correct. The reactor is not emitting any new decision records (no certs, no receipts) since 06-12. This IS a real blocker that would need to be fixed before any order could reach a fill. The B1 submit gate being frozen is a second-layer blocker, but the primary issue is no decision processing at all.

**Kill verdict:** No valid kill. The empirical claim survives. The repro is confirmed with the corrected timestamp (17:04, not "2026-06-13"). The decision engine being dark is a genuine ROOT_CAUSE that blocks fills.

---

## refuter:candidate=DEFECT (angle R9, tag CALIBRATION_EDGE_DEFECT, impact 72):

**Agent ID:** `agent-ac58f34fabe32482d`

### Final Assistant Message

The cost-basis cross-check confirms: all position residuals are essentially zero (max 4.7e-05, which is floating-point rounding), meaning `cost_basis_usd = entry_price * shares` exactly for every position. This is the independent ledger proof that zero fee was charged on all fills.

The defect is REFUTED. Here is the complete kill chain:

**(b) Existing code already handles the distinction correctly.** The `fee_details` exclusion filter (line 65-67 of the reconciler) is not a bug — it is the correct semantic separation between the venue's schedule cap and the realized trade-level charge. The reconciler's header (lines 62-64) explicitly documents why: `fee_details.*` = schedule cap, NOT the charged fee. That's the entire point of the artifact.

**(c) The empirical claim is based on a misread of field semantics.** The one fill that carries `fee_rate_bps=0` explicitly at trade level (path `trade_fact_proof.trade.fee_rate_bps`) is not survivorship bias — it's the only fill where the venue included a trade-level fee field. The 41 "absent" fills are consistent with the venue not repeating the fee on the trade record. The independent cross-check (cost_basis residuals, all < 5e-5) confirms zero fee charged across all 42 fills. Absence of the field and absence of a charge are indistinguishable here, but the residual arithmetic settles it.

**(d) Fixing it would not move a real order.** The current `observed_max_fee_fraction=0.0` produces the lowest possible fee deduction in EV math, making entry conditions EASIER to satisfy (lower cost), not harder. If the "fix" correctly detected non-zero fees, it would SUPPRESS orders. The current system is correctly calibrated for zero realized fees on this venue.

---

## refuter:candidate=DEFECT (angle R14, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-ac62e4c88ff32e63a`

### Final Assistant Message

I have enough now. Let me verify the exact check on user_version vs expected_schema_version with actual values to confirm the assert does NOT fire:

The logic is: `if 0 < user_version < expected_schema_version` = `if 0 < 43 < 42` = False. The assert does NOT raise. The repro the defect report proposed would only raise if user_version were 0 < x < 42, but the actual DB has user_version=43 which is ABOVE 42, so the check passes silently.

The table SQL contains `42` in the CHECK constraint `schema_version IN (14,...,42)`, so `str(42) not in table_sql` is False. All columns present. The assert passes cleanly.

The empirical silence (no rows since 05-28) is real, but the proposed mechanism (schema version mismatch raising NoTradeEventsSchemaCompatibilityError) is wrong. The schema check does NOT fire with user_version=43. The actual cause of the write silence is something else — but that is a different defect.

Now for the kill test (d): would fixing the proposed defect (schema mismatch raising exception) move a real order toward a fill? No — `no_trade_events` is observability/provenance only. It records WHY a candidate was rejected, not whether it gets evaluated or submitted. The write is inside `except Exception` fail-soft per the comment at line 5437 ("Fail-soft: logging/learning infrastructure must not crash the cycle"). Even if writes were fully restored, orders would not change — the rejection decisions exist and are counted in math_frontier regardless.

---

## angle:Fusion / calibration / probability arithmetic.

**Agent ID:** `agent-aca70e3fa8bee9c4a`

### Final Assistant Message

I now have a comprehensive picture. Let me synthesize the findings:

---

FUSION / CALIBRATION / PROBABILITY ARITHMETIC ANGLE — FINDINGS

---

MECHANISM (what this angle actually does)

The chain is: market data -> compute_alpha (market_fusion.py, authority-gated) -> compute_posterior (alpha-weighted blend or model-only) -> replacement_forecast_materializer.py (fused Normal q point from BPF fusion or AIFS soft-anchor fallback) -> per-bin q_lcb/q_ucb (200-draw center-uncertainty bootstrap or Wilson-over-AIFS-votes fallback) -> probability_uncertainty.py (separate q_lcb from edge_lcb separation contract) -> selection_shrinkage.py (EB shrinkage + posterior-utility licensing, C2). Calibration is managed by settlement sigma floor + fitted sigma-scale artifact (k, w, floor_steps). Correlation is handled by Pearson matrix (or haversine fallback), used only in portfolio-exposure accounting, not in q construction.

---

DEFECTS

DEFECT 1 — KILLS SUBMISSION: selection_shrinkage.py / select_license pi_min=0.90 default is UNCONNECTED to the live trading path.

selection_shrinkage.py is the C2 replacement math for the BH/FDR gate. Its module docstring (line 18-20) explicitly states: "The BH gate consumes degenerate p-values in {0,1} ... a literal no-op multiplicity correction; see src/engine/event_reactor_adapter.py lines 9854/9876". The module also says "This module is pure (numpy only) and has no Zeus runtime imports". Whether select_license / eb_shrink_edges / lfsr are actually WIRED into the live reactor is not determinable from these files alone — but the module header and its self-description as a "pure ... no runtime imports" helper, combined with the task list entry T60 "completed", and the file's creation date of 2026-06-13 (today), suggest it is freshly written but not necessarily wired. If the live reactor still runs the old BH/{0,1} p-value gate (described as a "no-op" in this file's own docstring), then every candidate the system thinks it is evaluating is actually being evaluated by a gate that passes everything (p=0 passes) or nothing (p=1 fails). File: selection_shrinkage.py line 18. This is potentially KILLS_SUBMISSION or PASSES_ALL_BAD_CANDIDATES depending on which p-value is fed, but the critical finding is: if C2 is not wired, the module cannot help.

DEFECT 2 — KILLS SUBMISSION on far-tail bins: q_lcb bootstrap center_sigma vs predictive_sigma conflation in the fused-q bounds path.

In _build_fused_q_bounds (materializer line 1359-1441), the center draws are from N(mu*, center_sigma_c) and the integration uses predictive_sigma_c for the bin probabilities. However the bootstrap is specifically described as "CENTER-ONLY: we do NOT re-add sigma_resid here; that would double-count the residual spread already inside sigma_pred" (materializer line 1176-1178). This is architecturally sound. But the resulting q_lcb_map is then passed as-is to the DB. The fused-center bootstrap for far/longshot bins produces a 5th percentile by shifting the Gaussian center: for a bin that is only reachable when the center moves toward it, the 5th percentile of its probability across 200 draws can legitimately be near zero — because most of the 200 draws from N(mu*, center_sigma=~anchor_sigma_c≈3C) don't push mass into a bin 3-4 bins away from the mode. The clip at line 1437 `lcb = min(max(lcb, 0.0), max(q_pt, 0.0))` is fine. But the fundamental problem: far-bin q_lcb IS artificially compressed toward 0 by construction — the bootstrap is designed to produce q_lcb≈0 for bins not reachable by the center-uncertainty draws. This is not a bug per se (the comments acknowledge it), but it means that for far/longshot bins q_lcb/q_point could be extremely low even when the point q is meaningful. If the live gate requires q_lcb > ask_price, far bins will systematically fail to trade. This is correct behavior if the center uncertainty genuinely justifies it; it is a problem if the center_sigma (anchor_sigma_c≈3C) is being used as the wrong quantity. Check: if the fused posterior sd (fused.sd) is being passed as anchor_sigma_c rather than the predictive sigma, far-bin bounds may be tighter or looser than they should be. The code path is: bayes_precision_fusion_override.anchor_sigma_c (which is fused.sd per line 1132 `anchor_sigma_c=float(fused.sd)`) as center_sigma_c in _build_fused_q_bounds. fused.sd is the posterior sd of the MEAN, not the predictive spread; this is intentional per the comment "we do NOT re-add sigma_resid". No defect here per the spec, but the far-bin compression is structural and intentional.

DEFECT 3 — KILLS SUBMISSION: The default replacement_q_mode at line 1503 is BAYES_PRECISION_FUSION_CAPTURE_MISSING, which is NOT live-eligible. The code only upgrades this to FUSED_NORMAL_FULL/PARTIAL if BOTH the BPF override is present AND replacement_0_1_fused_q_shape_enabled flag is True. If that flag is off (default False per line 601), rows with a BPF override stay in the fused-center-only no-live mode. That mode is also not live-eligible per "FUSED_NORMAL_{FULL,PARTIAL} are live-eligible; every other mode is no-submit" (materializer line 1789). So if replacement_0_1_fused_q_shape_enabled=False, NO replacement forecast row is ever live-eligible, regardless of how good the fusion is. This is a known flag-gate, not accidental dead code. But it confirms: if the flag is currently false in settings, the replacement path writes zero live-eligible rows. This KILLS SUBMISSION on the replacement chain.

File/line: materializer line 585-603 (`_replacement_fused_q_shape_enabled()`), line 1537-1538, line 1789-1805. Whether this flag is live-set is a DB/config question outside these files.

DEFECT 4 — DEFECT_NO_CURRENT_IMPACT: The tail-alpha in compute_posterior / alpha_for_bin (market_fusion.py line 372-385) uses `is_shoulder` attribute detection plus label-string matching ("or below", "or higher", "or above"). The double-check by label at line 381 is defensive but the combination of three separate detection paths creates a risk of inconsistency: a bin with `is_shoulder=False` and `low=None` but a label not containing those exact strings would be detected as a tail by the second check but NOT by label. These three checks are additive (any one fires), so the real risk is the opposite: a bin that is NOT a tail but whose label happens to contain "or above" would have its alpha halved. For example, a label like "Probability or above 30C" at an interior boundary. DEFECT_NO_CURRENT_IMPACT (the set of labels in production appears to follow a consistent pattern; no evidence of misclassification).

File: market_fusion.py lines 373-385.

DEFECT 5 — DEFECT_NO_CURRENT_IMPACT: In correlation.py, `correlated_exposure` (line 118-148) clamps anti-correlation to zero at line 111 (`max(0.0, ...)`). This means two cities that are NEGATIVELY correlated do not provide a hedge — the risk accumulator treats them as uncorrelated. This is documented as a conservative choice ("anti-correlation is treated as uncorrelated for risk accumulation purposes"). It does not kill trades but causes the system to over-restrict on negatively-correlated city pairs, reducing opportunities. DEFECT_NO_CURRENT_IMPACT in isolation (does not block submission once a candidate clears the correlated_exposure gate; it may over-restrict some pairs).

File: correlation.py line 111.

DEFECT 6 — KILLS SUBMISSION FOR SOME CITIES: The sigma_scale_fit.json lookup in `_replacement_sigma_scale_lookup` (materializer lines 646-700) fails open with (k=1.0, w=0.0, floor_steps=0.0) when the artifact is missing or the family is unfitted. The F-unit family is explicitly documented as unfitted today (n=47 < 60). For F-unit cities, k=1, w=0, floor_steps=0, meaning NO calibration correction is applied. If F-unit cities' raw predictive sigma is systematically too narrow, their q will be overconfident on the mode bin and too low on far bins. This produces q_lcb values that may be above ask on the mode and zero on far bins — in both cases, no +EV trade clears the gate. This is an absence rather than a bug. KILLS_EV_CAPTURE_NO_BUG.

File: materializer line 672-673, lines 1573-1574 (defense-in-depth C-only gate).

DEFECT 7 — DEFECT_NO_CURRENT_IMPACT: probability_uncertainty.py carries a header "DEFAULT-OFF / SHADOW (operator directive 2026-06-08): this is a pure contract + helpers. Importing it changes NO live trading behavior; it is not wired into the live decision path." (lines 16-18). The C3 n_eff_override shadow correction (lines 325-346) populates q_lcb_neff_corrected but NEVER updates q_lcb. This is architecturally correct (the comment says callers must opt-in by reading q_lcb_neff_corrected explicitly). DEFECT_NO_CURRENT_IMPACT as long as callers don't exist that accidentally read q_lcb thinking it will be n_eff-corrected.

DEFECT 8 — KILLS SUBMISSION for CAPTURE_MISSING rows: The Wilson soft-anchor LCB (materializer lines 1273-1299) computes successes = aifs_prob * member_count and calls Wilson with those inputs. But AIFS has 51 members. For a far/tail bin with zero AIFS member votes (aifs_prob=0), successes=0 and the Wilson lower bound is 0.0 (line 1296 `out[bin_id] = 0.0`). A q_lcb of 0.0 on a longshot bin means q_lcb < ANY nonzero ask price, so the bet fails q_lcb > ask_price. This is technically honest (zero support from 51 members IS strong evidence against the bin), but it structurally prevents any CAPTURE_MISSING row from clearing the live gate on far/longshot bins. Since CAPTURE_MISSING rows are also ineligible by q_mode, this is a redundant kill — DEFECT_NO_CURRENT_IMPACT at the margin, but confirms the entire CAPTURE_MISSING class is dead to trading regardless of whether q_lcb is 0 or not.

---

NOTABLY ABSENT

1. No market-anchor cap is implemented anywhere in these files. The docstring of compute_posterior (line 262-331) mentions vig removal before blending but there is no per-bin q_lcb >= 0 OR q_lcb <= market_price cap logic anywhere in this angle's files. If the downstream gate requires q_lcb > price (ask), the only protection against q_lcb being wildly above fair-value is the alpha-weight blending toward the market prior. For the model-only path (MODEL_ONLY_POSTERIOR_MODE, line 294-301), there is no market anchor at all — q_lcb is purely from the bootstrap, uncapped by market price. If the system systematically runs in model_only mode, a very confident model can produce q_lcb well above market and the bet is taken at any ask. No defect per se — this is the intended behavior — but no cap guard exists.

2. No sigma floor for the ORACLE/EMOS path is visible in these files. The settlement sigma floor lookup (materializer lines 606-640) is tied to the BPF fused-q path only. Classic EMOS calibration (the baseline) would have its own sigma floor, but that code is in `src/calibration/emos.py`, not in scope here.

3. The `selection_shrinkage.py` (C2) module has no integration test or caller that can be verified from these files alone. If it is wired, it introduces a DEFAULT_PI_MIN=0.90 gate (line 64) — at least 90% posterior probability the shrunk edge clears e_min=0. Given that the EB shrinkage drives shrunk edges toward the grand mean (which for a small true-edge universe will be near zero), this gate may over-reject. No test for this can be found in this file set.

---

PROVENANCE VERDICTS

market_fusion.py — CURRENT_REUSABLE. No creation date in header but substantive content (AUTH_VIOLATION gate, TAIL_ALPHA_SCALE validated 2026-03-31, D3/D4 analysis, TemperatureDelta unit-typing). The law regime it operates under is the corrected pricing semantics regime (legacy modes are explicitly quarantined). No violations detected.

probability_uncertainty.py — CURRENT_REUSABLE. Created 2026-06-08, audited 2026-06-08. Authority: spec Phase 2 §14.4 + hidden issues #2/#3. DEFAULT-OFF shadow contract with no live wiring per its own header. Mathematically sound; the q_ucb degeneracy fix (lines 321-323) is recent and correct.

correlation_shrinkage.py — CURRENT_REUSABLE but unused on the live trading path (only correlation.py / get_correlation is called by correlated_exposure; Ledoit-Wolf is a pipeline utility for offline matrix construction). Created 2026-05-21. Math is correct per Ledoit & Wolf 2003/2004.

correlation.py — CURRENT_REUSABLE. No creation date but K3-revised per header. Conservative anti-correlation zeroing is documented. Haversine fallback is a safe default.

oracle_estimator.py — CURRENT_REUSABLE. Created 2026-05-04. Beta-binomial posterior correct; the m=0 vs m>=1 split justified and pinned as regression antibody. Classification table matches the module docstring.

selection_shrinkage.py — CURRENT_REUSABLE as math but SHADOW/UNWIRED. Created 2026-06-13 (today). Authority: A2 + Fable double-review. The math (EB shrinkage, Tweedie, select_license) is sound. The critical open question — whether it is wired into the live reactor — is not answerable from these files. Until it is wired and verified to replace the BH/{0,1}-p-value gate, it is math that exists but does not affect live behavior.

replacement_forecast_emos_identity.py — CURRENT_REUSABLE for its scope (identity verification only). No creation date. Narrow contract, no trading impact directly.

replacement_forecast_production.py — CURRENT_REUSABLE. Created 2026-06-08, audited 2026-06-13. The probe-resolved cycle authority (2026-06-11 fix) and the per-leg fetching are correct. The R4b extras fan-out gate (_extras_cycle_incomplete) is fresh (today).

replacement_forecast_materializer.py — CURRENT_REUSABLE with caution. Created 2026-06-08, audited 2026-06-13. The file is operationally complex (2298 lines). The core finding is that FUSED_NORMAL live-eligibility requires replacement_0_1_fused_q_shape_enabled=True (flag) AND a non-None BPF override; both must be true or the row is not live-eligible. This is the intended design and is correctly implemented. CAUTION because the flag-state is not verifiable from the file set.

probability_arithmetic.py — CURRENT_REUSABLE. Created 2026-06-07. Narrow helpers (one_minus, payout_odds). No trading impact beyond preventing the AST-complement obfuscation.

---

SUMMARY OF SUBMISSION-BLOCKING FINDINGS (highest impact first)

DEFECT 3 is the most likely active submission blocker within this angle: if `replacement_0_1_fused_q_shape_enabled` is False in the live settings (its documented default), every replacement-chain posterior is non-live-eligible by q_mode, and the replacement forecast path contributes zero candidates. Check `settings["edli"]["replacement_0_1_fused_q_shape_enabled"]` against the live config.

DEFECT 1 (C2 unwired) blocks the intended EB-shrinkage licensing path if the old BH/{0,1} gate is still in place in event_reactor_adapter; this is plausible given the C2 module was created today.

DEFECT 6 (F-unit cities undertreated) reduces viable candidates systematically for Fahrenheit cities.

Everything else in this angle is either structurally correct, a documented shadow/unwired module, or a conservative design choice that does not constitute a bug.

---

## reconcile:Capital / portfolio / collateral / risk-allocator / account 

**Agent ID:** `agent-aca7f0ec2d2ee0793`

### Final Assistant Message

Now I have all the evidence I need. Let me compose the structured output.

---

## agent-acb7dcd4f508dbf98

**Agent ID:** `agent-acb7dcd4f508dbf98`

### Final Assistant Message

I now have the complete end-to-end picture. Here is the full analysis:

---

**B1 LATCH MECHANISM — END-TO-END**

**How the latch freezes:**

`run_ws_gap_reconcile_and_clear` (`exchange_reconcile.py:290`) runs every M5 cycle. It calls `list_unresolved_findings(conn)` and checks `if findings or unresolved: return {"status": "blocked", "reason": "m5_findings_unresolved"}`. The latch clear (`ws_guard.clear_after_m5_reconcile`) is only called if both `findings` (new from the sweep) and `unresolved` (pre-existing unresolved rows) are empty. One unresolved row is enough to keep the latch closed permanently — every sweep re-reads it and returns blocked. Confirmed by logs: same message every ~5–10 min from 2026-06-12T22:58 through 2026-06-13T23:32, always `unresolved_findings=1`.

**The specific finding row:**

- `finding_id` 5bbc2be2-350c-4bdf-ac0e-f080e41f9012
- `kind` position_drift, `context` ws_gap, recorded 2026-06-12T22:58:12 UTC
- `subject_id` = token `2599807256...` (a NO-side token for market `2501685`, command `2f5ed5ce...`, intent=ENTRY/BUY/EXPIRED, created 2026-06-11)
- Evidence: `exchange_size=0`, `confirmed_journal_size=17.05`, `closed_position_size=17.05`, `expected_wallet_size=34.10`

This is the exact double-count pattern the external-close absorber (task #31) was supposed to fix: position phase=`voided`, chain_state=`synced`, `no_token_id` holding 17.05 → `closed_position_holdings` adds 17.05 on top of the confirmed journal 17.05 → expected_wallet 34.10 vs exchange 0 → drift recorded.

**Why the absorber does NOT auto-resolve this:**

The variant-3 absorber (`_absorb_operator_external_close`, line 1144) requires an operator-acknowledged drift resolution for the SAME `subject_id` token — a prior RESOLVED position_drift finding with `resolved_by LIKE 'session_operator_confirmed%'` or `resolution LIKE 'operator_manual%'`. There is NONE for token `2599807256...`. The only `_finding_from_row` for this token is the unresolved 5bbc2be2 row itself (no prior resolution history). So the absorber's gate fails silently and the finding stays open.

The settled-external absorber (lines 2445–2473) would also resolve it, but it requires `_market_calendar_terminal_evidence` to classify the market as terminal (target local day ended >= 24h ago). The EMS has no row for this token (market 2501685 is absent from current `executable_market_snapshots`), and the forecasts DB `market_events` query uses token_id/condition_id lookup — this token is a NO side that may not match the YES-keyed registry, so `calendar_terminal.get(token)` returns None. The suppression path never fires.

**Is there any code path that would auto-resolve a terminal-chain-closed phantom?**

No automatic code path resolves this without one of:
1. The token appearing in `token_suppression` (which it does not)
2. `_market_calendar_terminal_evidence` returning terminal evidence for it via the condition_id bridge (requires the forecasts DB `market_events` to have a row linking to this condition, which is unconfirmed for an EXPIRED NO-side order)
3. An operator-acknowledged resolution row existing for this token (there is none)
4. The exchange position returning non-zero again (impossible — tokens are swept/redeemed)

**Recurring class assessment:**

Yes, this is a recurring class, not a one-off. The swept_winners query shows 35+ historical `exchange_size=0` position_drift ws_gap findings, all resolved — but those resolved via `position_drift_cleared` (exchange temporarily showed non-zero), `position_drift_recent_fill_suppressed`, or operator-manual paths. The current token is stuck because: (a) it has NO operator-ack row, (b) the EXPIRED command means it was never an operator-manual close — it expired on-chain and the tokens were auto-redeemed by the third-party redeemer without Zeus having a settlement_command or token_suppression entry. This is the same class as the HK 06-09 incident (11h freeze on swept winners) which task #31 was meant to fix — but the fix (settled_external_absorber) only fires when `_market_calendar_terminal_evidence` classifies the market as terminal, and that classification is failing for this NO token on market 2501685 (likely a condition_id bridge miss in the forecasts DB).

**Summary of the gap:** The `settled_external_absorber` path in `_resolve_position_drift_tokens_from_current_truth` (line 2445) should have caught this as a swept/redeemed winner. It didn't, because either (a) the NO token's condition_id is not bridged in the `zeus-forecasts.db` market_events table, or (b) the market's `target_date` has not yet exceeded the 24h buffer from the token's perspective. The command was placed 2026-06-11, EXPIRED — making it a legitimate swept redeem candidate — but the market-calendar bridge failed to classify it terminal. The latch will stay frozen until operator manually resolves the finding or the absorber is repaired to handle NO-token condition misses.

---

## agent-accc9b8f8f8d9c2a7

**Agent ID:** `agent-accc9b8f8f8d9c2a7`

### Final Assistant Message

Now I have all the data needed for a complete, accurate witness reconstruction. Here is the decision trace:

---

EXECUTION WITNESS: B2 DECISION TRACE FOR KL 35C+, TEL AVIV 32C, MUNICH 26C+ (2026-06-14)

All data from /Users/leofitz/zeus/logs/zeus-live.log and /Users/leofitz/zeus/src/strategy/live_inference/live_admission.py. Verbatim log lines are quoted throughout.

---

GATE DEFINITION (live_admission.py lines 87-119)

`capital_efficiency_lcb_ev` is the rejection class for the test:

    conservative_ev_per_dollar = (q_lcb - price) / price
    if conservative_ev_per_dollar <= 0.0:  → REJECT

This is `q_lcb > price` after subtracting cost, i.e., honest EV > 0. If q_lcb <= price, the trade has non-positive expected value at the conservative lower bound and is rejected. The log label `ev_per_dollar` in the EDLI reactor cycle summary is `(q_lcb - price) / price`, NOT a separate threshold — it is the EV-per-dollar at the conservative q. The gate is binary: positive = pass, non-positive = `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV`.

The summary format is:

    EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=<N> capital_efficiency_lcb_ev=<K> [other flags]; best=<bin> <dir> q_lcb=<v> price=<v> ev_per_dollar=<v>

The `best` line is the highest-EV rejected candidate in that event family (what would have traded if the gate passed). The count `capital_efficiency_lcb_ev=<K>` is the number of candidates in the family killed by the EV<=0 gate (out of n=22 total bins).

---

CANDIDATE 1: KL 35C OR HIGHER, 2026-06-14, BUY YES

The brief cites: q_lcb=0.0392, price=0.0080, ev/$=3.90

The best-candidate log appears in multiple reactor cycles. Most representative verbatim line:

From 2026-06-13 20:06:11:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=16 direction_law=2 other=4; best=Will the highest temperature in Kuala Lumpur be 35°C or higher on June 14? buy_yes q_lcb=0.0392 price=0.0080 ev_per_dollar=3.9020"

From 2026-06-13 23:07:44:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=13 coverage_unlicensed_tail=1 direction_law=1 other=7; best=Will the highest temperature in Kuala Lumpur be 35°C or higher on June 14? buy_yes q_lcb=0.0392 price=0.0080 ev_per_dollar=3.9020"

Decision trace:
- q_lcb = 0.0392
- execution_price = 0.0080
- ev_per_dollar = (0.0392 - 0.0080) / 0.0080 = 3.9020 — POSITIVE

The `capital_efficiency_lcb_ev` gate passes for this bin (ev > 0). KL 35C+ is not killed by the EV gate. It is the BEST candidate in the family (highest EV), meaning every other bin in the family failed. The count breakdown shows `capital_efficiency_lcb_ev=13-16` (other bins with negative EV), `direction_law=1-2` (one or two bins rejected by direction-law mismatch), and `coverage_unlicensed_tail=1` in some cycles (one bin rejected as unlicensed-tail).

The family-level rejection `EVENT_BOUND_ALL_CANDIDATES_REJECTED` means even though KL 35C+ itself passes the EV gate, all 22 candidates in the family are collectively blocked. The log prefix "n=22 capital_efficiency_lcb_ev=13..." sums all per-candidate rejections across bins; the `best` is the one surviving candidate that would have been selected. What kills the family is that the surviving candidate (KL 35C+ buy_yes) is still subject to `direction_law` (2 counts at 20:06, 1 count at 23:07), and the event family as a whole is declared rejected.

Specifically: KL 35C+ buy_yes passes the EV gate individually (ev=$3.90 per dollar is strongly positive) but the direction_law gate fires on it separately. From 2026-06-13 18:05:
"best=Will the highest temperature in Kuala Lumpur be 35°C or higher on June 14? buy_yes q_lcb=0.0392 price=0.0090 ev_per_dollar=3.3573" combined with "direction_law=2 other=3" at family level.

Verdict for KL 35C+: The EV is honest and large (+$3.90/$). The blocking gate is `direction_law`, not EV negativity. The q_lcb of 0.0392 against price 0.0080 represents real positive expected value — this is a potentially over-tight cut if the direction-law gate is miscalibrated or firing on a genuinely bullish tail bin.

---

CANDIDATE 2: TEL AVIV 32C, 2026-06-14, BUY YES

The brief cites: q_lcb=0.0275, price=0.0010, ev/$=26.5

Multiple verbatim cycles, most representative:

From 2026-06-13 19:54:29:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=14 coverage_unlicensed_tail=2 other=6; best=Will the highest temperature in Tel Aviv be 32°C on June 14? buy_yes q_lcb=0.0275 price=0.0010 ev_per_dollar=26.5000"

From 2026-06-13 23:07:44:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=14 coverage_unlicensed_tail=2 other=6; best=Will the highest temperature in Tel Aviv be 32°C on June 14? buy_yes q_lcb=0.0275 price=0.0010 ev_per_dollar=26.5000"

From 2026-06-13 22:28:00:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=14 coverage_unlicensed_tail=2 other=6; best=Will the highest temperature in Tel Aviv be 32°C on June 14? buy_yes q_lcb=0.0275 price=0.0010 ev_per_dollar=26.5000"

Decision trace:
- q_lcb = 0.0275
- execution_price = 0.0010
- ev_per_dollar = (0.0275 - 0.0010) / 0.0010 = 26.50 — STRONGLY POSITIVE

Like KL 35C+, the EV gate itself passes for this specific bin. The bin is the best candidate in the family. The breakdown is consistently `capital_efficiency_lcb_ev=14 coverage_unlicensed_tail=2 other=6` across multiple cycles (14 + 2 + 6 = 22 bins total). Tel Aviv 32C itself passes the EV gate; it is blocked by `coverage_unlicensed_tail=2` — the unlicensed-tail guard at live_admission.py lines 122+. This gate fires when price < 0.05 and q_lcb > 2.0x market price without settlement-licensed calibration source. At price=0.0010 and q_lcb=0.0275, the ratio is 27.5x, well above the 2.0x disagreement threshold; and q_lcb source is likely FORECAST_BOOTSTRAP (not in COVERAGE_LICENSED_LCB_SOURCES = {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}).

Verdict for Tel Aviv 32C: The ev/$=26.5 is enormous and structurally real at the computed q_lcb. The gate firing is `COVERAGE_UNLICENSED_TAIL`, not EV negativity. Whether this is honest rejection depends on whether q_lcb=0.0275 is a credible FORECAST_BOOTSTRAP estimate for a very deep tail bin (price=0.0010 means market assigns 0.1% probability). The unlicensed-tail gate is intentionally a fail-closed antibody for uncalibrated longshot claims — it is conceptually sound but conservative; the question is whether Tel Aviv 32C on 06-14 has real settlement coverage in its band to earn the licensed label.

---

CANDIDATE 3: MUNICH 26C OR HIGHER, 2026-06-14, BUY YES

The brief cites: q_lcb=0.0000, price=0.0010, ev/$=-1.0

Verbatim log from 2026-06-13 23:07:44:
"EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=19 other=3; best=Will the highest temperature in Munich be 26°C or higher on June 14? buy_yes q_lcb=0.0000 price=0.0010 ev_per_dollar=-1.0000"

Multiple confirming cycles:
- 2026-06-13 21:16:18: "best=Will the highest temperature in Munich be 26°C or higher on June 14? buy_yes q_lcb=0.0000 price=0.0030 ev_per_dollar=-1.0000"
- 2026-06-13 20:36:51: "best=Will the highest temperature in Munich be 26°C or higher on June 14? buy_yes q_lcb=0.0000 price=0.0030 ev_per_dollar=-1.0000"
- 2026-06-13 19:49:28: "best=Will the highest temperature in Munich be 26°C or higher on June 14? buy_yes q_lcb=0.0000 price=0.0030 ev_per_dollar=-1.0000"

Decision trace:
- q_lcb = 0.0000
- execution_price = 0.0010 to 0.0030 (varies by cycle)
- ev_per_dollar = (0.0000 - price) / price = -1.0000 in all observations

Gate test: conservative_ev_per_dollar = (0.0 - 0.001) / 0.001 = -1.0 <= 0.0 → ADMISSION_CAPITAL_EFFICIENCY_LCB_EV fires.

This is the only candidate of the three where the EV gate itself is the direct rejection. q_lcb=0.0 means the conservative lower-bound probability is zero — the model assigns zero credible mass to this bin at the LCB level. The `capital_efficiency_lcb_ev=19` count means 19 of 22 bins fail the EV gate, with the 26C+ bin being the "best" of the rejects (least negative EV, since all others are further negative). The earlier log from 2026-06-12 (for June 13 market) showed the shadow: "replacement q_lcb floor city=Munich ... claimed=0.003396 floored=0.003396 floor_enabled=False" — suggesting the LCB for the 27C+ bin was 0.34% before floor, and the 26C+ bin has an even smaller or zero LCB by the time June 14 markets are processed.

Verdict for Munich 26C+: Honest rejection. q_lcb=0.0 is not a calibration artifact — it is the model's conservative lower bound genuinely at zero (likely because the model's distribution has essentially zero mass above 26C for Munich in mid-June in this run). There is no "over-tight gate" here; EV is -100% by construction.

---

SUMMARY TABLE

City/Bin | q_lcb | price | ev/$ | Actual blocking gate | Honest or suspect
KL 35C+ | 0.0392 | 0.0080 | +3.90 | direction_law (1-2 bins in family) | SUSPECT — EV gate passes, direction_law fires on this or sibling bin
Tel Aviv 32C | 0.0275 | 0.0010 | +26.5 | coverage_unlicensed_tail (q/price ratio 27.5x, unlicensed source) | CONDITIONALLY HONEST — depends on whether settlement coverage exists for band
Munich 26C+ | 0.0000 | 0.0010 | -1.0 | capital_efficiency_lcb_ev (q_lcb=0, EV=-100%) | HONEST — zero LCB, no expected value

The B2 brief's characterization of all three as `capital_efficiency_lcb_ev` rejections is not precise: only Munich is killed at the EV gate directly. KL 35C+ is killed by `direction_law` on a sibling bin dragging the family, and Tel Aviv 32C is killed by `coverage_unlicensed_tail`. The ev/$ values in the EDLI summary line reflect the best candidate's EV (often positive) — they do not indicate the gate that fired, they are a diagnostic of which bin was closest to trading.

---

## angle:Market efficiency / counterparty / adverse selection.

**Agent ID:** `agent-ace1dfddc5dfec779`

### Final Assistant Message

## Market Efficiency / Counterparty / Adverse Selection

### (1) Objective

Extract the spread between your calibrated probability lower bound and the executable price, net of fees and adverse selection cost. The system makes money if and only if: `q_lcb(bin B) > ask_price(B) + fee + E[adverse_selection_cost]` for buys, symmetrically for sells. The primary threat is not being wrong about temperature — it is filling against a counterparty who is MORE right than you at the moment of fill. The objective is therefore two-part: (a) identify when your edge is genuine rather than base-rate illusion, and (b) size positions only when the adverse selection premium is small enough that real edge survives it.

---

### (2) Ideal Mechanism

**Counterparty taxonomy.** The venue has three liquidity profiles:

- *Noise/retail makers*: post wide quotes, uninformed, fill frequently, provide exploitable edge when you are the informed taker
- *Base-rate arbers*: price from settlement history alone, predictably stale on unusual forecast days
- *Informed makers*: track NWP model output, update quotes when ensemble runs publish; filling against these is the adverse selection event

**Regime clock.** Market efficiency evolves intraday against a hard anchor: the official reading at roughly 5-7pm local. Ensemble model runs publish at fixed UTC times (00Z, 06Z, 12Z, 18Z). Between runs the book is informationally stale. At run publication, informed makers re-quote within minutes. Therefore:

- *Pre-run window*: book reflects prior run; your fresh NWP read is an information asymmetry. Taker aggression is justified if your q_lcb clears the ask.
- *Post-convergence window*: all sophisticated makers have repriced; your read is no longer asymmetric. Maker resting is now appropriate because the residual edge is carry (collect spread from noise flow), not forecast.

**Adverse selection proxy.** When a resting quote fills, the fill timing relative to model run publication is the signal. A fill arriving 2-15 minutes after a 12Z run publishes — before you have digested it — is an adverse selection indicator. Maintain a rolling fill-timing distribution; if recent fills cluster near model-run boundaries, the market is repricing around you, not into you.

**Edge verification gate.** Before submitting any order:
1. Compute q_lcb from ensemble spread (lower confidence bound, not point estimate — Kelly/sizing must use the conservative tail)
2. Pull the current book; compute executable price at your intended size (walking the book if size > top-of-book)
3. Compute net edge = q_lcb - executable_price - fee - adverse_selection_buffer
4. Gate: submit only if net edge > 0 with strict inequality

---

### (3) Invariants

- **I1**: Edge is quoted in probability space net of ALL costs before size is determined. Net edge < 0 → no position, regardless of confidence.
- **I2**: Adverse selection buffer is derived from empirical fill-timing data, not assumed. It updates each session.
- **I3**: The winning bin's NO (~0.10 price) is NOT automatically edge — base rate explains it. Edge requires q_lcb to differ from the base rate by more than fee + adverse selection.
- **I4**: Position sizing is a function of edge magnitude and Kelly fraction, never of "confidence" alone.

---

### (4) Silent Failure Modes

- **Base-rate illusion**: buying losing-bin NO at 0.90 because it "usually" settles at 1. The price already reflects this. This is zero-edge carry masquerading as forecasting alpha.
- **Stale adverse selection buffer**: using a buffer estimated on thin early-market fills to trade the liquid final hour. The counterparty mix changes drastically; adverse selection spikes near settlement.
- **Quote-fill selection bias**: your fills are not a random sample of your quotes. They are selected by the counterparty's information signal. Any calibration done on filled trades is systematically biased toward your worst-timing fills.
- **Model-run timing blindness**: submitting immediately after your NWP refresh without checking whether market makers refreshed first. If they were faster, you are the uninformed side.

---

### (5) Three Hardest Design Decisions

**Decision 1 — Taker vs maker by regime.** Opinionated call: be a taker only in the pre-run window when you have a fresh model read and the book has not yet updated. Be a maker only post-convergence to collect noise spread. Never mix the strategies within the same regime — mixing contaminates the adverse selection signal.

**Decision 2 — How to set the adverse selection buffer.** Opinionated call: derive it from fill-timing empirics, updated session-by-session, but floor it at a conservatively large value until you have 50+ fills in the relevant regime. Underestimating this in early sessions is the single largest silent edge-destroyer.

**Decision 3 — How to define q_lcb.** Opinionated call: use the 15th percentile of ensemble-member probabilities for that bin, not the ensemble mean. The mean is the best estimate; the lower bound protects against ensemble overconfidence and against the fact that your observed fill is selected against you. A system that sizes from the mean will be correctly sized in expectation but ruined by adverse selection in the tail.

---

## refuter:candidate=DEFECT (angle R9, tag MECHANICAL_BLOCK, impact 68):

**Agent ID:** `agent-acf22fc2b0d456d39`

### Final Assistant Message

B1 is resolved (0 unresolved findings). The submit gate is now open. Now I have enough to synthesize the verdict.

Key findings:

1. **Empirical claim is accurate**: 1,058 `strategy_economic_floor` rejections confirmed. The column is `reason` not `rejection_reason` but the count matches exactly.

2. **The 0.05 floor is a pure policy constant**: `strategy_profile.py:120` default, no venue-imposed constraint. The venue tick_size is 0.01, min_order_size is 5 shares — nothing stops submitting at 0.005 or 0.02. The actual order book shows fills/asks at 0.005, 0.013, 0.02, 0.024, etc. Prices like 0.001 are below the 0.01 tick resolution, but prices in the 0.01-0.04 range are fully valid on the venue.

3. **B1 is now resolved** (0 unresolved exchange_reconcile_findings), so the submit gate is open and fixing this floor would reach the submission layer.

4. **The floor passes on EV, not on friction**: the code at evaluator.py:1280-1318 rejects on `entry_price <= min_entry_price` before any EV calculation. The separate `_live_entry_economic_floor_rejection` (line 1341) does check expected profit in USD — but the price check at line 1286 fires first and short-circuits, so an EV-positive trade at price=0.02 never reaches the profit check.

5. **Allow path exists but is strategy-gated**: `allow_ultra_low_tail` would bypass for tail topology, but it defaults False and no strategy has it configured. The floor is a blanket policy default, not a fitted friction threshold.

6. **"Would fixing it move a real fill?" — partially yes**: Some blocked candidates are at 0.046-0.049 (near-floor, legitimate prices in tick-increment range). Many are at 0.001 which is below the 0.01 venue tick — those couldn't fill anyway regardless of the floor. The 0.001-priced rejections are the dominant sub-class (134+92+84=310 of 1058 for p=0.001 alone) and represent entries that are below the venue minimum tick and would be rejected at the venue level regardless. However, the candidates blocked in the 0.01-0.049 range (real, fillable prices) would genuinely reach the venue.

The defect survives on the near-floor candidates (0.01-0.049) but is substantially weaker than claimed because the majority of the 1,058 rejections are at p=0.001, which is below the venue tick resolution and would hit a downstream venue constraint anyway. The floor is correctly described as a policy constant not justified by venue friction, but its empirical impact on real fillable orders is smaller than 1,058 implies.

The floor does deviate from EV-based reasoning (it is not a friction computation), and there are real candidates in the 0.01-0.049 range that it blocks unnecessarily. However, the dominant blocked class (p=0.001) would fail at the venue regardless. The defect is real but impact-overestimated.

This is a CONTRIBUTING_DEFECT, not a ROOT_CAUSE. It does not survive the kill attempt on criterion (d) for the near-floor range, but the "1,058 high-EV candidates" framing is survivorship-biased — most are at p=0.001 which is below tick resolution.

---

## angle:Minimal kernel / rebuild boundary / keep-list.

**Agent ID:** `agent-acfaf252bff0e00b3`

### Final Assistant Message

## Minimal Kernel / Rebuild Boundary / Keep-List

### (1) Objective

The system must produce a signed, auditable edge decision — q_lcb(bin) > ask_price + total_cost, or equivalent for NO-side — for every tradeable contract, while maintaining the invariant that each stage's output is independently verifiable against its inputs without reading adjacent stages. A 14k-line decision file fails this: it is untestable by surface, unfalsifiable by component, and its failure modes are invisible until PnL degrades. The structural objective is to make the boundary between stages the only place where bugs can hide, and to make each boundary narrow enough to instrument exhaustively.

### (2) Ideal Mechanism

Seven stages, each a pure function with a typed contract at its boundary:

**Contract** — emits (city, date, bin_id, bin_lo, bin_hi, ask, bid, min_tick, min_size, fee_rate, settlement_source_id). Stateless lookup. No forecast logic here.

**Source** — given (city, date, valid_time), fetches ensemble members from each provider and emits a raw sample set: {provider_id, member_id, temperature_value}[]. No aggregation, no clipping. Preserves disagreement between models as signal rather than noise.

**Forecast** — maps raw samples to a discrete probability vector p[K] over bins, summing to 1. The only correct unit is a calibrated probability — not a point estimate, not a z-score. Method: kernel density over ensemble members, evaluated at bin midpoints, then normalized. Two separate outputs: p_center (the MLE estimate) and p_lcb (the 5th-percentile lower-confidence bound from bootstrap resampling across providers). The spread between them is the honest uncertainty signal.

**Calibration** — maps (provider_id, city, season, horizon_days) to a calibration correction table learned from settled history. Applied multiplicatively to p_center before bootstrap. Calibration is a slow-moving correction, updated at most weekly; it must never be re-fit intraday (look-ahead contamination).

**Edge** — for each bin: edge_yes = p_lcb[bin] - (ask + fee_rate); edge_no = (1 - p_lcb[bin]) - (ask_no + fee_rate). Signal threshold is edge > 0. Sizing is fractional Kelly on portfolio equity, capped only by minimum lot size and available liquidity at the quoted price. This function is four arithmetic operations. It must not contain model logic.

**Execution** — translates a signed edge and a Kelly size into a limit order at a specified price. Responsible for: time-in-force, re-quote logic on partial fill, and recording the actual fill price and cost. Does not decide whether to trade — that is Edge's output.

**Settlement / Learning** — at settlement: records (bin_id, settled_outcome, fill_price, fill_cost, edge_at_entry, p_lcb_at_entry, provider_weights). Learning pass weekly: re-estimates calibration tables, re-weights providers by Brier score over trailing N=200 markets. Writes a new calibration table and a new provider-weight vector. Both are versioned artifacts, not in-place updates.

### (3) Invariants

I-1: p[K] sums to 1.000 ± 1e-9 at every stage boundary.
I-2: calibration tables are never read during the same calendar day they are written.
I-3: edge is computed from p_lcb, never p_center.
I-4: fill_cost is recorded before position is logged (no position without cost attribution).
I-5: the settlement source is recorded at Contract stage and must match the source used in learning — never inferred post-hoc.

### (4) Silent Failure Modes

**Calibration look-ahead**: re-fitting on the day's data before settlement completes inflates apparent edge by 3-8 percentage points. Completely invisible in backtest.

**Provider weight collapse**: if one provider dominates weight, ensemble spread collapses, p_lcb approaches p_center, and the honest uncertainty buffer disappears. Bets appear high-conviction when they are merely concentrated.

**Fee omission in edge**: even a 2% round-trip fee moved inside the threshold produces a false signal on roughly 40% of near-edge contracts.

**Bin boundary misalignment**: if the forecast KDE uses different bin boundaries than the contract spec (e.g., inclusive vs exclusive at the boundary), probability mass leaks between adjacent bins. Undetectable without an explicit boundary-match assertion.

**Settlement source drift**: if the station used for calibration differs from the station that settles the contract, calibration correction applies a bias from the wrong signal.

### (5) Three Hardest Decisions

**Decision 1 — p_lcb vs p_center for sizing.** Opinionated call: p_lcb for the threshold, Kelly sizing proportional to edge at p_lcb. Using p_center for sizing systematically overbets uncertain positions. The cost is smaller average position; the benefit is that you do not blow up on a forecaster disagreement day.

**Decision 2 — where to handle provider disagreement.** The tempting answer is to average early (weighted mean of point estimates). Wrong: averaging destroys the tail information. Keep all members through Forecast stage; let the bootstrap see provider disagreement as width in the confidence interval. This is the architectural reason Source must emit raw members, not summaries.

**Decision 3 — learning update frequency.** Daily re-fit looks responsive; it is actually noise amplification with N too small per (city, season, horizon) cell. Weekly is the minimum credible window. The correct answer is event-triggered on N_settled >= 20 per cell, with a hard weekly maximum. Calibration that updates faster than its data warrants is calibration that chases the last outcome.

**What a 14k-line decision file signifies:** stages 2-5 collapsed into one untestable blob. The invariant boundaries no longer exist as code artifacts, so no stage can be replaced, verified, or blamed in isolation. This is the architectural failure that makes silent failure modes invisible.

---

## refuter:candidate=DEFECT (angle R16, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-acfb1309e6a877edf`

### Final Assistant Message

The empirical kill is decisive. Zero `FDR_REJECTED` rows exist in the database from 06-10 through 06-14. All positive-trade_score receipts show `fdr_pass: true`. The defect is code-true but has no current order impact — the BH/FDR gate is passing everything reaching it. Fixing `authority_on=False` to `True` would not move a real order because the gate isn't blocking any.

The witness claim ("trade_score 0.007-0.076 stopping at the submit gate after 06-12T12:12, consistent with candidates clearing Layer A but hitting FDR") is factually wrong: those receipts show `fdr_pass: true`, stopped by `live_submit_effective: null` (the B1 allow_submit latch), not FDR. The B1 latch (exchange_reconcile_findings with unresolved row) now shows 0 unresolved findings, so that blocker has also cleared.

The p-value degeneracy claim is also correct in code (lines 10000, 10022 still emit `{0,1}`) but is not causing `FDR_REJECTED` outcomes in practice — the prefilter mechanics ensure non-edge candidates (p=1.0) never get selected, so BH with these p-values passes the selected candidate whenever it has positive edge.

---

## angle:Opportunity universe / candidate generation / bin-action selection.

**Agent ID:** `agent-ad14f3dd1ca1f1d2e`

### Final Assistant Message

I now have a complete read of all required files. Here is my report.

---

**ANGLE MECHANISM (what this angle actually does)**

Events arrive as `OpportunityEvent` rows in the EDLI queue. `reactor.process_pending` fetches them, claims each, and runs a two-window pre/post-submit gate chain. The pre-submit phase (`_process_one_pre_submit`) filters on: source-truth structural validity, executable-snapshot freshness, and riskguard. Passing events go to the `event_reactor_adapter` (injected as `self._submit`), which does the actual candidate-generation pipeline: binds the event to a market-topology family via `bind_event_to_candidate_family`, builds a `CandidateEvaluation` per bin/direction via `event_reactor_adapter._generate_candidate_proofs`, runs the ΔU ranker (`_select_proof_by_robust_marginal_utility`), picks a winner, builds a `NoSubmitProofBundle`, and returns an `EventSubmissionReceipt`. The post-submit phase (`_process_one_post_submit`) validates the receipt and persists via certificate + no-submit ledger.

`opportunity_book.build_family_opportunity_book` and `opportunity_selector.select_best_family_candidate` are display-only provenance surfaces since S7; they never gate or decide the live leg. `market_analysis.py` produces per-bin edge CI + p-values. `market_analysis_family_scan.scan_full_hypothesis_family` records the full tested hypothesis family for FDR accounting. `selection_family.py` is the BH/family-ID grammar.

---

**DEFECTS**

**FINDING 1 — DEAD BUY_NO HYPOTHESIS PATH IN scan_full_hypothesis_family (BLOCKS NO-SIDE CANDIDATES)**

File: `src/strategy/market_analysis_family_scan.py`, lines 103–106.

```python
if not _supports_buy_no_for_bin(analysis, idx):
    continue
continue  # <-- THIS LINE kills every buy_no hypothesis
```

Line 105 is a bare `continue` that executes unconditionally. The logic enters the `if` block if the bin does not support buy_no, skips to next bin. Otherwise it falls through to line 105, which is also `continue` — so it ALWAYS goes to the next bin without ever appending a `buy_no` `FullFamilyHypothesis`. The body of the hypothetical buy_no branch was never written. No buy_no hypothesis ever enters the FDR family set. Recipe: call `scan_full_hypothesis_family(analysis, n_bootstrap=100)` on any two-or-more-bin analysis; count `buy_no` entries in the result — the count is zero regardless of whether buy_no trades are supported.

**Whether it blocks orders:** Directly YES for no-side candidates. The FDR gate (`receipt.fdr_pass`, enforced in `_receipt_money_path_blocker` at `reactor.py:2079`) requires `fdr_hypothesis_count > 0`. If buy_no hypotheses are silently absent from the FDR family, a buy_no candidate enters the BH gate with its hypothesis missing from the denominator. On the adapter path the FDR hypothesis count is computed from the full-family scan, not from `scan_full_hypothesis_family` directly — so the final impact depends on where the adapter sources the hypothesis list. But the bug guarantees zero buy_no hypotheses are ever recorded in this function's output, which is the ground-truth FDR accounting surface. If the adapter consumes this output to count hypotheses, every buy_no candidate fails `fdr_hypothesis_count <= 0` → `FDR_REJECTED`, terminal. If the adapter has a separate path that does not call this function, buy_no hypotheses still have no FDR accounting entry, meaning any buy_no that happens to pass FDR is doing so against an incomplete denominator (BH denominator too small → threshold inflated → false discoveries).

**FINDING 2 — `live_win_rate_floor_rejection_reason` IS COMPUTED BUT NEVER CHECKED IN `admitted`**

File: `src/events/candidate_evaluation.py`, lines 100–106, and the `admitted` property at lines 155–166.

`CandidateEvaluation` exposes `live_win_rate_floor_reason` and `live_win_rate_admissible` as public properties (lines 100–106) and calls them in `to_receipt_dict` (line 207). But the `admitted` property at line 155 does NOT include `self.live_win_rate_admissible` in its conjunction — it checks `live_lcb_consistency_admissible`, `live_capital_efficiency_admissible`, and `live_buy_no_conservative_evidence_admissible`, but not the win-rate floor.

Recipe: construct a `CandidateEvaluation` with `q_lcb_5pct=0.45` (below the 0.51 floor), `q_posterior=0.46`, `execution_price=0.40`, `trade_score=0.05`. `admitted` returns `True` because `live_win_rate_admissible` is excluded; `live_win_rate_floor_reason` returns a rejection string. The receipt `to_receipt_dict` will print `live_win_rate_admissible: False` while `admitted: True` — contradicting itself.

**Whether it blocks orders:** This is NOT an active blocker today because the win-rate floor check also lives in `_receipt_money_path_blocker` at the receipt level — but only via the `live_buy_no_conservative_evidence_rejection_reason` branch, not an explicit win-rate floor re-check. The `admitted` property is used as the gate for display ordering in `select_best_family_candidate` (line 52), so a sub-floor candidate can be admitted into the display ranking while showing contradictory receipt fields. Not a current submission blocker, but a genuine logical contradiction and a silent correctness hole if the `admitted` gate is ever relied on directly.

Tag: DEFECT_NO_CURRENT_IMPACT on submissions; logical contradiction that would become a blocker if `admitted` is used as the candidate gating surface (and it is the clear semantic intent given the other four gates present).

**FINDING 3 — `_day0_hard_fact_payload_live_eligible` HARDCODES `dst_status="UNAMBIGUOUS"` BUT `candidate_binding._validate_day0_event` ACCEPTS BOTH `"UNAMBIGUOUS"` AND `"MATCH"`**

File: `src/events/reactor.py` lines 2371–2382 vs `src/events/candidate_binding.py` lines 221–236.

`candidate_binding._validate_day0_event` permits `dst_status in {"UNAMBIGUOUS", "MATCH"}`. The reactor's `_day0_hard_fact_payload_live_eligible` at line 2378 checks only `dst_status == "UNAMBIGUOUS"` — rejecting any Day0 event with `dst_status="MATCH"` at the pre-submit gate before binding even runs.

Recipe: a Day0 event where an ASOS sensor observation unambiguously matches the local date (hence `dst_status="MATCH"`) would be dead-lettered by the reactor with `DAY0_HARD_FACT_AUTHORITY_BLOCKED` while the binding layer would have accepted it. The binding validation was written with explicit knowledge that "MATCH" is valid (comment in the binding source); the reactor function was written later and only copied "UNAMBIGUOUS".

**Whether it blocks orders:** Directly YES — any Day0 event with `dst_status="MATCH"` is rejected at `reactor.py:1423` before reaching the adapter, even though the binding contract allows it. This silently eliminates a category of valid Day0 candidates.

**FINDING 4 — REDUNDANT `buy_no_conservative_evidence` RE-CHECK IN `_receipt_money_path_blocker` WITH DELETED TWIN CHECKS**

File: `src/events/reactor.py`, lines 2089–2117.

The comment at lines 2094–2098 notes that `live_lcb_consistency` and `live_capital_efficiency` stanzas were deleted from `_receipt_money_path_blocker` on 2026-06-14 because they duplicate what `CandidateEvaluation.admitted` enforces upstream. The `buy_no_conservative_evidence` re-check at lines 2100–2117 is retained on the stated ground that `same_bin_yes_posterior` and `settlement_coverage_status` arrive via a "distinct receipt-provenance path." This claim is architecturally correct (the receipt carries these from the adapter, not from `CandidateEvaluation`), so the re-check is not strictly redundant. However, it introduces a second evaluation of the same function with the same inputs if the adapter and the candidate-evaluation path wire the same values — and there is no structural guarantee they will always agree. This is a twin-authority maintenance burden, not a blocker.

Tag: DEFECT_NO_CURRENT_IMPACT on submissions; over-built maintenance surface that can diverge if wiring changes.

**FINDING 5 — `_receipt_money_path_blocker` FDR CHECK ACCEPTS DEGENERATE p-VALUES (pre-existing note in code)**

File: `src/events/reactor.py`, comment at lines 2288–2296 (`EventSubmissionReceipt.selection_authority`).

The code documents that the BH gate on the trading path consumes `{0, 1}` p-values (degenerate, a no-op multiplicity correction), as noted in the C2 shadow comment. The BH denominator over mutually-exclusive bins also violates PRDS, making BH invalid for the statistical guarantee it claims. The `selection_authority` field shadows the EB shrinkage replacement, which is OFF. This means FDR is evaluated as a formality (passes for any positive-edge candidate with a positive p-value below BH threshold = trivially any p < q * rank/m).

**Whether it blocks orders:** In the failing direction — no, FDR passes too easily (always-pass with {0,1} p-values). It cannot be the cause of zero new orders. Tag: DEFECT_NO_CURRENT_IMPACT on submission blocking; the gate is too weak, not too strong.

---

**WHAT IS NOTABLY ABSENT**

1. There is no `market_analysis_family_scan.py` buy_no hypothesis body. The comment above the `continue` at line 105 ("if not _supports_buy_no_for_bin…") implies intent, but the actual buy_no scoring (`_bootstrap_bin` equivalent on the NO side, `_buy_no_market_price_for_bin`, price computation, `passed_prefilter` for NO) is entirely absent. A complete implementation would mirror the buy_yes block, calling `_buy_no_market_price_for_bin(analysis, idx)` and computing a buy_no edge as `q_no_posterior - buy_no_price`.

2. The `admitted` property in `CandidateEvaluation` has no win-rate floor gate in its conjunction despite advertising it as a live admission criterion. A sound system's `admitted` would include `self.live_win_rate_admissible`.

3. There is no structural test that `_day0_hard_fact_payload_live_eligible` and `candidate_binding._validate_day0_event` agree on their `dst_status` vocabulary. The two validation surfaces are silently inconsistent.

---

**PROVENANCE VERDICTS**

`src/events/opportunity_book.py` — **CURRENT_REUSABLE**. Audited 2026-06-08. S7 removed the last selector-gate artifact. Mechanism is display-only recording of the ΔU decision. No live selection authority. Current law: "bin selection.md" §14.7/§14.8 + operator directive 2026-06-08.

`src/events/opportunity_selector.py` — **CURRENT_REUSABLE**. Audited 2026-06-08. Demoted to display-ordering + loser-reason provenance. No live selection. Current law: "bin selection.md" §6 + §14.7/§14.8.

`src/events/opportunity_event.py` — **CURRENT_REUSABLE** (no provenance date; structure is stable immutable event model; no audit-critical law references in header). The file has no `Last reused or audited` header (minor provenance gap) but no evidence of staleness.

`src/events/candidate_binding.py` — **CURRENT_REUSABLE** with the dst_status gap noted (Finding 3). The MECE/topology validation, causal-snapshot checks, and serve-freshest advisory (updated inline at line 204 for the 2026-06-11 incident) reflect current law. The `_validate_day0_event` dst_status="MATCH" allowance is current; the reactor's complementary check is stale.

`src/events/candidate_evaluation.py` — **CURRENT_REUSABLE** with the `admitted` gap noted (Finding 2). The field inventory through execution-mode, maker/taker, and settlement-coverage fields is current (last substantive additions: twin-authority #7, 2026-06-11; C2 selection shrinkage shadow, 2026-06-13). The win-rate floor absent from `admitted` is a law violation from the moment the property was written.

`src/events/reactor.py` — **CURRENT_REUSABLE** with Finding 3 (dst_status hardcode). Audited 2026-06-12 (external deep-review). Contains the ALWAYS-DECIDABLE infrastructure (Build 1, Build 2), submit-lane invariant, venue-close horizon (b) added 2026-06-13. Large file, well-maintained commentary. The `_day0_hard_fact_payload_live_eligible` mismatch with binding is stale relative to the binding layer's accepted vocabulary.

`src/engine/event_reactor_adapter.py` — **CURRENT_REUSABLE**. Audited 2026-06-12 (external deep-review, fixes A–E). S1–S7 overhaul from 2026-06-08 is fully reflected in the header. This is the most complex file in the angle; header comments are comprehensive and accurate.

`src/strategy/market_analysis.py` — **CURRENT_REUSABLE**. Audited 2026-06-08. `bin_yes_probability_samples` added for S2 q_lcb authority. Bootstrap logic byte-identical to prior. Law: "bin selection.md" §5.6 + §9 Hidden #2 + §14.4.

`src/strategy/market_analysis_family_scan.py` — **STALE_REWRITE** on the buy_no branch. The buy_yes path is correct; the buy_no path is structurally dead (bare `continue` at line 105 kills all buy_no hypotheses). Wave-1 comment on line 77 accurately notes the sharpness-suppression deletion, so the file was touched recently but the buy_no body was never completed. No authority-basis header. The bare `continue` cannot be an intentional removal — the `_supports_buy_no_for_bin` helper, `_buy_no_market_price_for_bin` helper, and the `if not _supports...` guard are all present, implying the implementation was planned and the body was accidentally left empty.

`src/strategy/selection_family.py` — **CURRENT_REUSABLE**. Phase 4 T1 (2026-05-21) is the last update. BH helper `benjamini_hochberg_mask` and family-ID grammar are correct and stable. No law-regime conflicts.

`src/strategy/live_inference/live_admission.py` — **CURRENT_REUSABLE**. SETTLEMENT_COVERAGE_LICENSING_STATUSES single-authority (twin-authority #7, 2026-06-11) is current. The win-rate floor constant (0.51) and the buy_no evidence-gate vocabulary are aligned with current law.

---

**SUMMARY OF SUBMISSION-BLOCKING FINDINGS**

Finding 1 (buy_no hypotheses absent from family scan, `market_analysis_family_scan.py:105`) — **BLOCKS buy_no submissions** if the adapter relies on this function's output for `fdr_hypothesis_count`; corrupts FDR denominator otherwise.

Finding 3 (`dst_status="MATCH"` rejected by reactor but accepted by binding, `reactor.py:2378`) — **BLOCKS Day0 MATCH-status candidates** outright; those events dead-letter at pre-submit.

Findings 2, 4, 5 — DEFECT_NO_CURRENT_IMPACT on submission blocking; logical contradictions or over-built surfaces that do not explain zero new orders by themselves.

---

## refuter:candidate=DEFECT (angle R13, tag MISSING_CAPABILITY, impact 70):

**Agent ID:** `agent-ad1db85d67c500322`

### Final Assistant Message

The key facts are now established. Let me synthesize:

**What the defect claims:**
1. REVIEW_REQUIRED has no automatic exit path — only manual/external proof transitions
2. `find_unknown_command_by_economic_intent` blocks new submits at same token/side/price/size indefinitely while a REVIEW_REQUIRED command exists
3. No periodic recovery job queries venue for REVIEW_REQUIRED commands to auto-apply a terminal event

**What the code actually shows:**

1. REVIEW_REQUIRED IS in `IN_FLIGHT_STATES` (`command_bus.py` line 115) — the recovery cycle DOES scan it every cycle.

2. `_reconcile_row` (line 5990-5991) handles REVIEW_REQUIRED by calling `_review_required_cancel_unknown_live_order_recovery`, which queries the venue for the live order status and CAN auto-advance to FILLED or auto-terminalize to EXPIRED/ACKED — but ONLY when there is a `_latest_cancel_unknown_payload`. Without that payload (i.e., REVIEW_REQUIRED reached from SUBMITTING via `recovery_no_venue_order_id` or from CONFIRMED venue status), recovery returns "stayed" immediately (line 4729-4730), with no auto-exit path.

3. The `clear_review_required_no_venue_side_effect` function (line 5689) exists as a proof-backed clearance but requires explicit call with `source_commit`, `source_function`, and decision log evidence — it is NOT invoked by the recovery loop automatically.

4. `find_unknown_command_by_economic_intent` checks `UNRESOLVED_SIDE_EFFECT_STATES` which includes REVIEW_REQUIRED — so YES, a REVIEW_REQUIRED command blocks economic-intent-duplicate submits.

5. **Live DB: zero REVIEW_REQUIRED commands currently.** The described "indefinite block" scenario is not currently triggered against any real order.

**Kill verdict:** The defect is TRUE in code structure — the claim holds for most REVIEW_REQUIRED causes (non-cancel-unknown path returns "stayed" forever without operator intervention). However, the B1 blocker in the live system is an `exchange_reconcile_findings` row (position_drift), not a REVIEW_REQUIRED command. There are zero REVIEW_REQUIRED rows in the live DB. The defect is a real structural gap but does not currently block any fill path.

---

## reconcile:Friction / fees / slippage / threshold accounting

**Agent ID:** `agent-ad2f4515f18c60c48`

### Final Assistant Message

Now I have all the evidence needed. Let me synthesize the findings:

---

## reconcile:Market efficiency / counterparty / adverse selection

**Agent ID:** `agent-ad4101c1570e72d80`

### Final Assistant Message

Now I have a clear picture. The `_adverse_selection_proxy` in the adapter computes `|q_now - q_prev| * spread` and feeds it into the evaluator. When `passive_adverse_selection_score` is None (which the docstring says happens when no trustworthy prior-cycle belief is available), the evaluator uses 0 adverse selection cost. This is the key gap: in the early cycles or cold-start, this returns None and no adverse selection cost is applied.

Let me compile the actual divergences grounded in file:line evidence.

---

## refuter:candidate=DEFECT (angle R9, tag MECHANICAL_BLOCK, impact 68):

**Agent ID:** `agent-ad42945b4f9015097`

### Final Assistant Message

Now I have the complete picture. The defect is real but the empirical claim is significantly overstated:

- **997/1058 rejections** (94%) have prices below 0.01 — sub-tick, venue-unsubmittable regardless of the floor. These are NOT fixable by lowering min_entry_price.
- **61/1058 rejections** (6%) have prices in [0.01, 0.05) — tick-valid prices that ARE genuinely blocked by the hardcoded 0.05 floor with no EV justification.

The defect headline ("1,058 candidates suppressed") is 94% survivorship-biased: most of those candidates cannot produce a real fill because the price is sub-tick (0.001, 0.002, 0.003 etc). The valid kernel is 61 candidates, and those could move toward fills only if they also pass capital_efficiency (q_lcb > price) — which is separately likely given their low price.

The defect exists but the empirical scope is inflated by ~16x. The title claim of "1,058 candidates" is misleading; the actionable set is ~61.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-ad895e9a2c55a05e0`

### Final Assistant Message

I have enough evidence. The full picture is clear:

**The defect claim is real and valid.** The facts:

1. Repro query returns 1 — exactly 1 open finding (finding_id 5bbc2be2, kind=position_drift, context=ws_gap).
2. The governor at /Users/leofitz/zeus/src/risk_allocator/governor.py:244-245 fires `reconcile_finding_threshold` whenever `reconcile_finding_count > reconcile_finding_limit` (default 0), which is `1 > 0` = true. This arms the kill switch.
3. The auto-resolver path at exchange_reconcile.py:2432-2473 exists and runs on every reconcile cycle, BUT it requires either (a) the token to be in `token_suppression` (it is NOT — count=0), or (b) the market to be found in `executable_market_snapshots` for the settled-class absorber to fire (condition_id=2501685 has 0 rows in EMS), or (c) a prior operator-ack resolved finding (none — only this one unresolved row exists for the token).
4. The venue_command for this token has state=EXPIRED (not FILLED), meaning the trade exists as an EXPIRED order, confirming exchange_size=0 is genuine but the journal still shows confirmed_journal_size=17.05 because the position was held and then swept externally.
5. Task #31 (settled-class external-close absorber) was supposed to fix exactly this but the resolver CANNOT fire because the market fell out of EMS (condition_id=2501685 has no rows) so `_condition_ids_for_tokens` returns empty, `_market_calendar_terminal_evidence` returns empty, and `settled_terminal` is None.
6. This is a genuine regression of task #31's fix — the absorber works only for tokens whose market is still cached in EMS. Old/expired markets get evicted from EMS, leaving their findings permanently unresolvable.
7. Fixing it (resolving this finding, or restoring the EMS entry for 2501685, or patching the absorber to use venus-forecasts registry as fallback) would directly clear the governor kill switch, unblocking ALL new submissions — a real order path consequence.

---

## angle:Friction / fees / slippage / threshold accounting.

**Agent ID:** `agent-adb157ea5d2c49b28`

### Final Assistant Message

Now I have enough to understand the fee authority path. Let me check the penny-bin concern and the `conservative_ev_per_dollar` formula:

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-add5674397803b75b`

### Final Assistant Message

Confirmed. The ws_gap check runs at entry (line 2375) and another call site (line 3308), both before any venue command is issued. The latch is genuinely blocking ALL new submissions.

The defect claim survives all four kill tests:

(a) Is the ideal naive? No — a ws_gap latch on genuinely unresolved position drift is a correct safety mechanism. The problem is the stuck row is NOT real drift — it's a resolved/voided/swept position counted twice by a position_id join miss.

(b) Does existing code already handle it? The `_token_is_suppressed_external` check would handle it IF the token were in `token_suppression`, but it isn't. The `closed_position_holdings` path would handle it IF the journal didn't also count 17.05 (making expected_wallet_size double the exchange size). No existing path resolves it.

(c) Empirical bias? Single unambiguous DB row, not a statistical claim.

(d) Would fixing it move a real order? Yes — removing the unresolved finding (either by fixing the journal double-count, adding the token to suppression, or the reconciler correctly recognizing voided|synced with exchange_size=0 as resolved) would allow `run_ws_gap_reconcile_and_clear` to call `clear_after_m5_reconcile`, which sets `m5_reconcile_required=False`, which makes `blocks_market()` return False, which makes `allow_submit=True`, unblocking all entry submissions.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-addf992d8811952b2`

### Final Assistant Message

I have enough evidence. Let me now synthesize the kill verdict.

The candidate claim has two components:

**Component 1 (mechanism):** `record_message()` only clears `m5_reconcile_required` when `gap_reason in {'not_configured', None}`. This is literally true in the code at ws_gap_guard.py:160-165.

**Component 2 (consequence — "permanent latch"):** This is where the kill lands. The defect claims the latch is permanent. It is NOT — `clear_after_m5_reconcile()` (ws_gap_guard.py:217-257) is an entirely separate clear path that checks `subscription_state` and `unresolved_findings_count`, NOT `gap_reason`. It sets `m5_reconcile_required=False` directly. This path is actively called from `exchange_reconcile.py:336` after M5 sweep finds zero unresolved findings, which is triggered post-reactor when `m5_reconcile_required=True` (main.py:2520-2561).

**DB evidence:** Finding 5bbc2be2 (the only one cited as blocking) has `resolved_at=2026-06-14T06:01:47`, `resolution=position_drift_settled_external_suppressed`. Current unresolved finding count is **0**. The premise that this finding was blocking the latch is now moot — it's resolved. The latch mechanism R11 describes is working as designed: it waits for M5 sweep with zero findings, then clears.

The candidate incorrectly frames a deliberately conditional clear (wait for M5 proof before allowing submits) as a permanent defect. The `record_message()` limitation is intentional and documented in the code's own comment (ws_gap_guard.py:141-153): "true mid-run reconnect-after-gap could have missed fills that need REST reconciliation."

---

## refuter:candidate=DEFECT (angle R15, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-ade2e2c1394993045`

### Final Assistant Message

REFUTED. The defect claim fails on multiple independent kills:

**Kill 1 (code already handles it, correctly):** `_write_loaded_sha_state(real_sha)` executes at `main.py:8942` before `_assert_edli_stage_readiness` at line 9135. The file holds the real git SHA when the gate checks it. The `abc123` appeared at 20:48 (37 min post-boot), written by an external process after the gate already passed.

**Kill 2 (gate is one-shot, not per-cycle):** `_assert_edli_stage_readiness` is called once at startup only. The per-cycle `allow_submit` decision comes from `ws_gap_guard`, `heartbeat_supervisor`, and `cutover_guard`, none of which read `loaded_sha.json`. Even with `abc123` on disk right now, it has zero effect on running submission decisions.

**Kill 3 (empirical: no instances in DB, daemon still alive):** Zero LOADED_SHA_MISMATCH rows in `edli_no_submit_receipts` or `no_trade_events`. A real mismatch at boot raises `RuntimeError` and exits the process — the daemon has run continuously since 20:11 without crashing, proving the gate passed cleanly.

The active B1 blocker is the ws_gap_guard latch from `exchange_reconcile_findings` row 5bbc2be2, confirmed by the live evidence.

---

## angle:Execution submit / ack / fill / reconcile lifecycle.

**Agent ID:** `agent-ade9b38e49eea460f`

### Final Assistant Message

The findings table query returned no results (file is in zeus_trades.db but perhaps different path). Let me probe the actual DB path and read command_recovery in sections.

---

## refuter:candidate=DEFECT (angle R2, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-ae0d932cdf49db02c`

### Final Assistant Message

Critical finding: the `collection_frontier.py` law (line 23-25) explicitly refers to `source_run` pipeline fields (`captured_at/imported_at`) as the prohibited write-time fields. It operates on `source_run`, `readiness_state`, `source_run_coverage`, `job_run`, `data_coverage` — NOT on `ensemble_snapshots.recorded_at`.

The defect misidentifies the law target. `collection_frontier.py`'s freshness rule applies to its own inputs (source_run tables), not to the ensemble_snapshots query in `ecmwf_open_data_ingest.py`. These are two completely separate subsystems.

Furthermore, the `_query_metric` freshness gate using `recorded_at` is functioning as a correct INGEST RECENCY GUARD, not a source event-time freshness check. The purpose is: "was this data ingested recently?" — which is what matters for the trade decision (you want data fetched in the current cycle, not data from a 2-day-old ingest run). Using `recorded_at` here is correct because the ingest pipeline does NOT backfill old cycles — it only pulls current releases.

The repro query returned 0 rows, confirming the postulated failure mode does not exist in the live DB.

---

## angle:Order book / executable price / microstructure.

**Agent ID:** `agent-ae2dbd99873866416`

### Final Assistant Message

I now have all the material needed. Here is the full report.

---

MECHANISM (what this angle actually does)

The six files form a pipeline with three stages: (1) raw orderbook fetch and normalization, (2) book parsing into a typed cost curve, (3) price evaluation and guards.

Stage 1. `polymarket_client.get_orderbook_snapshot` fetches the raw CLOB `/book` JSON and stores it in `executable_market_snapshots.orderbook_depth_json` in `zeus_trades.db`. The stored shape is a single-token response: `{"asks": [...], "asset_id": "<token>", "bids": [...], ...}` where entries carry string prices and sizes. The fee is fetched separately and cached for 30 min; since 2026-06-12 a `fee_authority` module overrides the venue schedule fraction (1000 bps) with the realized reconciliation artifact (`state/fee_reconciliation.json`, n=42 fills, `observed_max_fee_fraction=0.0`), so the effective fee rate fed into the cost curve is 0.0.

Stage 2. `_native_quote_book_from_snapshot_row` (event_reactor_adapter.py:13460) parses the stored JSON into a `NativeQuoteBook`. It calls `_depth_for_token_or_label` once for YES and once for NO. The 2026-06-09 "single-token CLOB format" fix (line 13541) matches on `asset_id` equality. The stored depth JSON has exactly ONE `asset_id` — always the `yes_token_id` in the sampled live rows. For `buy_no` the NO-token lookup fails the `asset_id` check and falls through to `_explicit_depth_for_selected_token` (line 13600), which returns `None` unless `selected_outcome_token_id == no_token_id`. All sampled live rows have `outcome_label=YES`, so this fallback also returns `None`. Result: `no_asks` is an empty tuple.

Stage 3. `_native_side_cost_curve_from_snapshot_row` (line 13397) checks `if not asks: raise ValueError(...)`. For a `buy_no` direction against a YES-labeled snapshot with no NO depth stored, this raises, which routes to the maker-quote fallback (`_maker_quote_execution_price_from_snapshot`). That function returns `None` if `direction != "buy_no"` (so `buy_yes` on a NO-depth-missing book would re-raise directly). For `buy_no` it computes a complementary cap, but only if `comp_best_bid` is available. `_complementary_best_bid_for_direction` (called via `book.yes_bids`) has no YES bids in `no_depth` since `no_depth` is empty, so it returns `None`. It then uses `complementary_top_bid` passed by the caller. If the caller does not supply this scalar, the maker path also returns `None` and the original `ValueError` is re-raised, leaving the candidate rejected.

`ExecutableMarketSnapshot.__post_init__` (line 287) enforces that `executable_allowed=True` requires `accepting_orders=True`, `clob_archived=False`, `clob_enable_order_book=True` — except when `reason=="legacy_snapshot_flags"`. This is correct but is a construction-time gate, not a submission-time gate; real submissions go through `assert_snapshot_executable` which checks freshness (30 s default window) and `tradeability.executable_allowed`. Stale snapshots raise `StaleMarketSnapshotError` and block submission.

`walk_asks_for_target_shares` (orderbook_depth_walk.py) is a correct pure function, never called on the live candidate-proof path — it is documented as future Wave 5 input. It is dead on the current order path.

`project_rest_snapshot` and `ProjectedBook` (orderbook_projector.py) are also dead on the current live path — nothing in `src/` outside tests imports from `orderbook_projector`.

---

FINDINGS

**F1 — buy_no depth missing: NO-token ask ladder is always empty in the cost curve when the stored snapshot is YES-labeled.**
File/line: `event_reactor_adapter.py:13476–13486` (depth routing) and `13541` (asset_id match).
Mechanism: The materializer stores one token's CLOB `/book` response under `orderbook_depth_json`. Live rows have `asset_id = yes_token_id`. When the reactor prices a `buy_no` candidate, `_depth_for_token_or_label` is called with `token_id=no_token_id`; the `asset_id` check on line 13541 fails; the `_explicit_depth_for_selected_token` fallback on line 13606 also returns `None` because `selected_outcome_token_id != no_token_id` (the row is YES-labeled). So `no_asks=()`. The curve construction raises `ValueError("native NO ask ladder is empty")`. The candidate routes to the maker-quote lane, which requires `comp_best_bid`, and if that is absent, the candidate is rejected with `NATIVE_ASK_MISSING`.
Reproduction: any `buy_no` candidate against a YES-labeled snapshot with no sibling NO row in the warm buffer.
Verdict: **can kill a real +EV buy_no candidate** if the sibling YES `orderbook_top_bid` is not forwarded as `complementary_top_bid`. Whether it is depends on whether the proof-generation path always wires it — needs checking in `_generate_candidate_proofs`, but the data shows all 10 sampled recent snapshots are YES-labeled.

**F2 — fee rate is 0.0 from realized-reconciliation artifact.**
File/line: `fee_authority.py:93` + `state/fee_reconciliation.json`.
Mechanism: `resolve_taker_fee_fraction` returns `(0.0, "realized_fills_n=42_fitted=2026-06-12")`. The `FeeModel.fee_per_share` and `FeeModel.all_in_price` therefore add zero fee to every ask price. This makes `avg_cost_for_shares` return the raw ask price, not the fee-inclusive cost. Kelly sizes on an understated cost (EV is overstated) and the submission price floor is lower. This is not currently a submission blocker but is a systematic EV inflation: every candidate looks cheaper than it is if the venue ever charges fees.
Today's fills carried 0 bps realized, so the artifact is empirically correct for the current regime. If the venue turns fees on, the next reconciliation run (not automated) will catch it, but until then the system will operate with zero fee.
Verdict: DEFECT_NO_CURRENT_IMPACT (fees genuinely zero today), but a governance gap: the reconciliation script is not on an automated schedule, so a fee switch-on is not automatically picked up.

**F3 — `walk_asks_for_target_shares` (orderbook_depth_walk.py) is completely dead code on the live path.**
File/line: `orderbook_depth_walk.py:55–129`, module header line 14: "Wave 5 will consume it."
No production caller: `grep` finds it imported only in `src/data/orderbook_depth_walk.py` itself and test files. The live path uses `ExecutableCostCurve._walk_for_shares` directly. The module is not harmful but carries maintenance surface.
Verdict: DEFECT_NO_CURRENT_IMPACT.

**F4 — `ProjectedBook` / `orderbook_projector.py` is also dead on the live order path.**
File/line: `src/events/orderbook_projector.py` — no non-test importer in `src/`.
`real_submit_blocked` property (line 53) and `BookContinuityStatus.EXECUTION_FACTS_MISSING_FAIL_CLOSED` (line 17) were designed for a WebSocket projection / incremental update path that was never wired in. The `FRESHNESS_WINDOW_DEFAULT=30s` staleness trigger in `executable_market_snapshot.py` now covers the REST-path freshness gate instead.
Verdict: DEFECT_NO_CURRENT_IMPACT (dead design, not exercised by live orders).

**F5 — Semantic contradiction: `assert_snapshot_executable` (executable_market_snapshot.py:430–433) rejects a BUY command when `orderbook_top_ask is None`, but the warm path stores YES-token-only depth, leaving the top-ask field populated only for the YES outcome.**
File/line: `executable_market_snapshot.py:430–432`.
For a `buy_no` intent, `snapshot.orderbook_top_ask` is the value loaded from the YES-token row's top ask — which is the YES top ask, not the NO top ask. If the row was captured for YES and then `with_selected_outcome` is called to re-label it as NO, the ask in the snapshot is the wrong side's ask. The gate at line 430–432 passes (because the field is non-None) but it is the wrong value.
This does not block a submission but means the snapshot-level ask evidence is systematically wrong for the NO side if the same row is reused across outcome labels.
Verdict: semantic error, not a current submission blocker (the actual price comes from the cost-curve walk, not from `orderbook_top_ask`), but it corrupts `c_cost_95pct` derivation at event_reactor_adapter.py:13350 when the YES-row's ask is used for a NO-direction candidate.

**F6 — `_explicit_level` gate conflates size < min_order_size with size == 0 (line 13628), silently dropping thin top levels.**
File/line: `event_reactor_adapter.py:13628`.
If the depth fallback runs and `ask_size < min_order_size`, `_explicit_level` returns `[]`, so the single-level fallback produces no asks and the curve raises empty-book. A book with a real top-level ask that is thinner than `min_order_size` (a common thin market) causes a NATIVE_ASK_MISSING rejection even though the market has quotes. The candidate is killed.
Reproduction: any market where `depth_at_best_ask < min_order_size` (e.g. 0.5 shares at best ask, min_order_size=1).
Verdict: **can kill a real +EV candidate** on the single-level fallback path (though the full multi-level depth path via `asset_id` match is preferred).

**F7 — `_public_get` / `_public_post` fall through to a bare `httpx.get/post` when `_public_http_client` attribute has never been set (not just when it is None).**
File/line: `polymarket_client.py:320–323`, `325–328`.
`hasattr(self, "_public_http_client")` returns `False` before `_public_http()` is called for the first time — but the `__init__` at line 279 explicitly sets `self._public_http_client = None`, so `hasattr` always returns `True` after construction. The bare-httpx path is therefore unreachable in practice. Dead branch but inconsistent with the lazy init pattern.
Verdict: DEFECT_NO_CURRENT_IMPACT.

**F8 — `BOUND_ENVELOPE_REQUIRED` is the effective return when `place_limit_order` is called without a bound envelope, and the call site may silently succeed (returning a failed dict) rather than raising.**
File/line: `polymarket_client.py:692–703`.
The function returns `{"success": False, "status": "rejected", "errorCode": "BOUND_ENVELOPE_REQUIRED", ...}` — it does not raise. Any caller that checks `result["success"]` would catch it, but a caller that checks only `result.get("status") == "accepted"` would pass it to the executor as a rejection without an exception trace. Not a submission path issue (the V2 adapter is the authoritative path) but a latent hazard.
Verdict: DEFECT_NO_CURRENT_IMPACT on the current V2 path.

---

WHAT IS NOTABLY ABSENT

A sound microstructure angle would have: (a) a NO-token book stored alongside the YES-token book so both sides can be priced from the same snapshot; (b) per-token depth indexed by `token_id` at capture time rather than raw single-token format; (c) automated fee reconciliation (not operator-run script); (d) a submission-time re-fetch of the NO-side ask that is independent of whether the warm snapshot is YES- or NO-labeled. The `ProjectedBook` / WebSocket projection layer (designed for continuous book updates) was never connected, so the system operates on periodic REST snapshots whose 30 s freshness window can mean stale books during fast-moving weather windows. The `DepthWalkResult.depth_sufficient` flag has no consumer on the live path (it is only used in tests).

---

PROVENANCE VERDICTS

`src/data/orderbook_depth_walk.py` — Created 2026-05-27, audited 2026-05-27. Law regime: Wave-3 spec §5.3/§5.4. Correct as a math module; dead on the live path (Wave 5 was never wired). Verdict: **CURRENT_REUSABLE** as a library but effectively **DEAD_DELETE** from the live call graph.

`src/data/polymarket_client.py` — Last audited 2026-06-04. Law regime: CLOB V2 + INV-24/25. The V2 adapter path, keychain memoization, and submission envelope contract are current. The legacy `_ensure_client` / bare-httpx branches are stale dead code. Overall: **CURRENT_REUSABLE** on the V2 path; legacy branches are **DEAD_DELETE**.

`src/events/orderbook_projector.py` — No creation date header, no audit date. Law regime unknown (designed for a WebSocket incremental path never activated). `real_submit_blocked` and `EXECUTION_FACTS_MISSING_FAIL_CLOSED` are logically sound but unreachable. Verdict: **DEAD_DELETE** from the live path (safe to delete, not currently imported).

`src/contracts/executable_market_snapshot.py` — Last audited 2026-06-11. Law regime: K=1 + Fee Structure V2 + tradeability_status provenance. Current. The `tradeability_status` invariant at line 287–293 is correct and enforced. Verdict: **CURRENT_REUSABLE**.

`src/contracts/executable_cost_curve.py` — Created 2026-06-08, audited 2026-06-09. Law regime: bin-selection §5.3/§5.4 + S1 operator directive. Fee zero via `fee_authority`. Monotonicity, grid validation, maker-resting zero-fee model are all correctly implemented. Verdict: **CURRENT_REUSABLE** (the math is sound; the fee being 0.0 is a data regime fact, not a code defect).

`src/contracts/execution_price.py` — No creation date. Law regime: D3/INV-12 resolution. The typed boundary, `assert_kelly_safe`, `with_taker_fee` double-fee guard, and numeric dunders are all correct. Verdict: **CURRENT_REUSABLE**.

---

## angle:State integrity / config / flags / deployment / concurrent-edit.

**Agent ID:** `agent-ae349318910014939`

### Final Assistant Message

I have enough to form a complete picture. Let me do one final targeted read to check the `append_many_and_project` import path and the `connect_or_degrade` usage pattern, which is key to my finding on silent write-path degradation.

---

## refuter:candidate=DEFECT (angle R1, tag MECHANICAL_BLOCK, impact 100):

**Agent ID:** `agent-ae3a7b3b37451713a`

### Final Assistant Message

Now I have everything needed to render the verdict.

Key findings:

1. **The unresolved finding is confirmed real**: Token `25998072565711727698258544609688934677406873903623466853003437606533488235694`, position_drift, ws_gap context, recorded 2026-06-12T22:58, `resolved_at IS NULL`, `resolution IS NULL`. Exactly one unresolved finding system-wide.

2. **The governor blocks with `reconcile_finding_limit = 0` default**: Any count > 0 (currently count = 1) sets `kill_reason = "reconcile_finding_threshold"` → `allow_submit = False`. This is the actual latch mechanism.

3. **The operator-ack absorber path is correctly dead**: `_operator_acknowledged_drift_resolution` requires `resolved_at IS NOT NULL AND (resolved_by LIKE 'session_operator_confirmed%' OR resolution LIKE 'operator_manual%')`. The row has both NULL. The absorber returns `None`, returns `False`. Confirmed dead.

4. **The settled-class absorber also cannot fire for two independent reasons**: (a) Token `25998072565711...` does not appear in `executable_market_snapshots` as either `yes_token_id` or `no_token_id` — so `_condition_ids_for_tokens` returns empty, no condition bridge is possible. (b) Even if it could bridge, Beijing 2026-06-14 is the current calendar date; `terminal_after` = 2026-06-15T16:00 UTC (midnight CST + 24h buffer) has not passed yet.

5. **Fixing this would directly unblock real fills**: The only thing holding `allow_submit = False` is this single unresolved position_drift finding. B2 (edge rejection) and B3 (blind exit) are secondary. If this finding resolves, the governor kill_reason clears and submission resumes. The defect is not cosmetic — orders are confirmed to have gone dark at exactly 2026-06-12T22:58, coincident with this finding's recording. No alternative path exists to clear it automatically.

6. **No kill on any of the four axes**: (a) The fail-closed operator-ack design is intentional for non-terminal unexplained drifts, but this token IS a swept/redeemed winner — the code just can't prove it because the token is absent from EMS. That's a genuine gap in the absorber's reach, not a deliberate constraint. (b) Existing code does NOT already handle it. (c) The empirical claim (latch frozen since 22:58, 1 unresolved finding) is directly confirmed by DB query, not survivorship bias. (d) Fixing it would directly move orders toward fills.

---

## angle:Fusion / calibration / probability arithmetic.

**Agent ID:** `agent-ae3ad18ad4be0549c`

### Final Assistant Message

Now I have a complete picture. Let me compile the full calibration findings.

CALIBRATION RELIABILITY FINDINGS

Queries executed, databases probed, all claims carry exact SQL + row counts.

---

DATA INVENTORY

Population anchor: settlement_outcomes has 7,385 rows covering 6,190 unique city-date events across 51 cities, 2024-01-01 to 2026-06-12. SQL: `SELECT count(*), count(distinct city||'|'||target_date), count(distinct city) FROM settlement_outcomes` -> 7385 / 6190 / 51.

Calibration training data: calibration_pairs (zeus-forecasts.db) has pair_id range 12,845,575 to 81,314,490 (~68.5M rows estimated, all source_id='tigge_mars'). Full table scan exceeds the 25s timeout on the 36GB DB; analysis uses block-range indexed sampling.

Production posteriors: forecast_posteriors covers only 2026-06-08 to 2026-06-15 (3,390 rows, all SHADOW_ONLY). This is the replacement-chain table, not the historical calibration corpus.

platt_models: 0 rows. The Platt/sigmoid calibration model table is empty — no model is fitted there.

---

RAW PROBABILITY (p_raw) ECE — calibration_pairs block sample

SQL: `SELECT p_raw, outcome, ... FROM calibration_pairs WHERE pair_id BETWEEN <blocks> AND training_allowed=1 AND causality_status='OK'` — 5 blocks, 49,000 rows total.

Structural finding: 92.6% of training-eligible rows have p_raw < 0.001 (structural zeros from tail bins). This is expected given that each city-date event has ~15-20 bins and most probability mass sits in 2-3 central bins. The ECE on all rows is 0.006 (dominated by the zero-outcome zeros). The meaningful analysis is on the non-trivial p_raw >= 0.001 subset (n=3,627, 7.4%).

ECE by decile on non-trivial p_raw (n=3,627):

- [0-10%]: mean_q=0.032 obs=0.081 bias=-0.049 (UNDERCONFIDENT at low end)
- [10-20%]: mean_q=0.147 obs=0.162 bias=-0.015 (good)
- [20-30%]: mean_q=0.253 obs=0.165 bias=+0.088 (overconfident)
- [30-40%]: mean_q=0.340 obs=0.202 bias=+0.138 (overconfident)
- [40-50%]: mean_q=0.451 obs=0.211 bias=+0.240 (severely overconfident)
- [50-60%]: mean_q=0.545 obs=0.200 bias=+0.345 (severely overconfident, n=60)
- [60-70%]: mean_q=0.634 obs=0.067 bias=+0.567 (catastrophic, n=15)
- [80-90%]: mean_q=0.821 obs=0.000 bias=+0.821 (catastrophic, n=2)
- ECE (weighted, nonzero): 0.067

This is the PRE-bias-correction raw tigge_mars probability. The bias_corrected=1 subset (n=1,158) shows near-perfect calibration: mean_q=0.110, obs=0.108, bias=+0.002. The uncorrected subset (bias_corrected=0, n=2,469) shows modest overconfidence: bias=+0.012. The severe overconfidence in high bins appears in the uncorrected raw output — the tigge_mars ensemble is sharper than it should be above 30%.

Bias-by-lead-days (non-trivial p, bias_corrected=1): all lead days 0-7 show bias < 0.02. Lead-day bias is well-controlled after correction.

---

OPERATIONAL CALIBRATION (sigma_scale_fit.json, 2026-06-08 to 06-12)

Source: state/sigma_scale_fit.json, calibration_at_fit section. Fitted 2026-06-13T17:06Z by MLE (logloss + ring_calibration_penalty). The candidate and live files are byte-for-byte identical (both 8,407 bytes), confirming the candidate was promoted. Live parameters: k=1.0, w=0.0, floor_steps=1.8002 (C) / 1.8037 (F).

Family C (Celsius), n=3,344 bins:
- dist=0 (ring bin): mean_q=0.214 realized=0.223 ratio=1.04 — good
- dist=1: mean_q=0.185 realized=0.213 ratio=1.15 — slight underconfidence
- dist=2: mean_q=0.120 realized=0.119 ratio=1.00 — perfect
- dist=3: mean_q=0.059 realized=0.061 ratio=1.05 — good
- dist>=4: mean_q=0.012 realized=0.017 ratio=1.42 — underconfident at far bins
- tail (cumulative end bins): mean_q=0.056 realized=0.016 ratio=0.30 — OVERCONFIDENT 3.4x
- ECE: 0.014

Family F (Fahrenheit), n=759 bins:
- Tail (dist=tail): mean_q=0.053 realized=0.007 ratio=0.14 — OVERCONFIDENT 7x
- ECE: 0.016

The ring bins (dist=0, the central prediction bin) are well-calibrated. The main defect is the tail cumulative bins: the model assigns ~5% probability to the "above highest bin" and "below lowest bin" categories, but these win at only 0.7-1.6%. This is a known hard-to-calibrate component because these are unbounded events.

The uncalibrated baseline (k=1, w=0, no sigma scale) has ECE 0.036-0.042 and catastrophic overconfidence at dist=0 (q=0.38 vs realized=0.22) with severe underconfidence at dist>=2. The current k=1.0 fit eliminated the dist=0 overconfidence but did not change k from baseline — the MLE found k=1.0 optimal, meaning the ensemble sigma_impl is already well-scaled for central bins after floor_steps applies.

---

PRODUCTION POSTERIOR q (forecast_posteriors vs settlements)

SQL: JOIN forecast_posteriors to settlement_outcomes on city+date+metric, target_date 2026-06-08 to 06-12. Result: 1,789 rows returned, 1,430 matched (359 failed key match due to Fahrenheit range-format bin names in winning_bin vs q_json keys — a presentation mismatch, not a data gap).

Matched events (n=1,430, all 2026-06 only since forecast_posteriors only covers 8 days):
- mean q(winning_bin)=0.205, median=0.178, stdev=0.164
- Log-score: -2.56 vs uniform 15-bin baseline of -2.71 — skill of +0.15 nats
- 18% of events have q(winning_bin) < 0.05 (uncertain cases where the model spread across many bins)
- 5.5% of events have q > 0.50 (highly concentrated forecasts)
- High metric log-score: -2.70, low metric: -1.50 (low-temperature forecasts are sharper)

q_lcb coverage: only 417 of 1,430 matched events (29%) have q_lcb populated in the winning bin. Mean q_lcb=0.068, mean q_lcb/q ratio=0.39 — the LCB applies a 61% discount on average. This is the James-Stein / N_eff=3.71 conservative floor (task #61).

---

LIVE NO-SUBMIT RECEIPTS (2026-06-12, the last pre-freeze window)

SQL: `SELECT direction, q_live, q_lcb_5pct, c_fee_adjusted, trade_score FROM edli_no_submit_receipts WHERE decision_time >= '2026-06-12'` -> 32 rows (21 unique after dedup). ALL are buy_no. q_live mean=0.83, q_lcb mean=0.80, c_fee mean=0.72, trade_score (edge) mean=0.036. These are legitimate NO-side edges of 1-8 cents per share. They were blocked by the submit-gate freeze (exchange_reconcile_findings row 5bbc2be2), not by calibration failure.

The LIVE EVIDENCE longshot YES candidates (KL 35C+: q_lcb=0.039, Tel Aviv 32C: q_lcb=0.028, Munich 26C+: q_lcb=0.000) are rejected by capital_efficiency_lcb_ev, not present in no_submit_receipts. Munich q_lcb=0.000 is arithmetically correct: with floor_steps=1.8 and a bin 5+ steps from the mode, N(0, 1.8-step sigma) places essentially zero mass there. Floor_steps=1.8 does not protect extreme tail bins.

---

IN-SAMPLE OVERFIT CHECK

The sigma_scale_fit.json was fitted on 2026-06-08..06-12 settlements (5 days, 304 C-family cells, 69 F-family cells). The meta warns "the magnitude is non-stationary on 5 days." The k=1.0 result is suspiciously at the boundary — it means the MLE found no benefit from scaling, which could indicate the penalty term dominated over the likelihood at this sample size. The CI for k spans [1.0, 1.0] (pinned at lower bound), which is a red flag for overfitting to a short window.

---

VERDICT SUMMARY

The raw tigge_mars p_raw probabilities are severely overconfident above 30% (ECE 0.067 before correction). After bias correction (bias_corrected=1), this collapses to near-perfect (bias=+0.002). The operational sigma_scale calibration on 5-day settled data is good in ring bins (ECE ~0.015) but overconfident 3-7x in tail/cumulative end bins, with too few settled events for reliable k estimation (CI pinned at boundary). The production q_lcb applies a ~61% conservative discount (q_lcb/q=0.39) which is the main gating mechanism. The system is NOT rejecting trades because q_lcb is systematically near-zero everywhere — the live no-submit receipts show q_lcb of 0.73-0.85 for buy_no candidates with real edge. The far-YES longshots (Munich 26C+) have q_lcb=0 because the normal distribution model legitimately assigns near-zero probability there, and floor_steps=1.8 is too narrow to protect extreme events. The dominant rejection mechanism is the B1 submit-gate freeze, not a calibration defect.

---

## angle:Opportunity universe / candidate generation / bin-action selection.

**Agent ID:** `agent-ae3ef94dba2c7f1f3`

### Final Assistant Message

## Opportunity Generation: From Market Universe to Ranked Candidates

---

### (1) Objective

Convert every active order book into a comparable, risk-adjusted signal so the downstream sizing engine can allocate capital to exactly the positions where your probability estimate dominates the executable price by more than round-trip cost. Nothing silently drops. Every miss is auditable.

---

### (2) Ideal Mechanism

**Step 1 — Universe enumeration.**
Pull every live contract: city C, date D, bin index k in {0..K-1}. Each (C, D, k) is a FAMILY. Each family has K YES contracts and K NO contracts — 2K order books total. Enumerate all (C, D, k, side) tuples. This is your candidate set before any filtering.

**Step 2 — Executable price extraction.**
For each (C, D, k, side), extract the best resting quote on the opposing side: for a YES taker buy, that is the lowest ask; for a NO taker buy, the lowest ask on that NO book. Compute the all-in cost: tick-adjusted quote + fee_per_unit. Call this p_exec. If the book is empty or the spread is wider than one tick from mid, mark p_exec = NaN and flag illiquid — but KEEP the candidate, do not drop it.

**Step 3 — Probability estimation.**
For each (C, D, k), produce a raw probability estimate q_raw from the ensemble blend: weight each model by its recent calibration score (Brier or log-loss on held-out settled outcomes for this city-bin stratum), then mixture-average. Apply a temperature correction for the bin width (wide bins near the mode of the forecast distribution get more probability mass, narrow wing bins less). This gives q_raw.

**Step 4 — Confidence lower bound.**
q_raw is a point estimate on a distribution of ensemble forecasts. Compute q_lcb as a one-sided confidence lower bound (e.g., 90th percentile downside) using bootstrap resampling across ensemble members or a parametric normal approximation on the logit scale. q_lcb is conservative: you are only claiming edge when even a pessimistic reading of your model exceeds cost.

For a YES buy: edge = q_lcb - p_exec - cost. For a NO buy: edge = (1 - q_lcb_of_bin_k_yes) - p_exec_no - cost. Note that NO on bin k is equivalent to YES on the complement, so q for the NO side is derived from the same ensemble, not re-estimated independently.

**Step 5 — Score and rank.**
Score each candidate by: edge x liquidity_scalar x confidence_weight. Liquidity_scalar = min(available_depth_at_touch, size_cap) / size_cap — penalizes thin books. Confidence_weight = 1 - CI_width_on_logit_scale — penalizes high-variance forecasts even when the point estimate is attractive. Rank descending. Every candidate — including those with negative edge or NaN liquidity — appears in the output with its score and a rejection flag. Rejection must be explicit, not silent.

---

### (3) Invariants

- Partition integrity: sum of q_raw across all K YES bins for a given (C, D) must equal 1.0 ± calibration_tolerance. Violation means the ensemble is miscalibrated or you are reading the wrong forecast slice.
- Side consistency: edge_YES + edge_NO for the same bin, at simultaneous prices, cannot both be positive (arbitrage). If they are, the book data is stale or mis-fetched — reject both candidates and flag the family.
- Coverage: |output candidates| = |active (C,D,k,side) tuples|. Any shortfall is a pipeline bug, not a market signal.

---

### (4) Silent Failure Modes

- **Stale book data.** Using yesterday's quote as today's executable price makes edge look larger than it is. Every p_exec must carry a timestamp; reject if age exceeds one tick interval.
- **Ignoring the base rate dominance of NO contracts.** Cheap NO contracts (p ~ 0.9) look like "easy short of YES" but their apparent edge is priced in. Edge only exists if your q_lcb places YES probability meaningfully below (1 - p_exec_no - cost).
- **Silent zero.** Returning no candidate for illiquid or low-edge families instead of flagging them causes persistent blind spots — you never know whether a city is skipped because it is unedgeable or because of a pipeline error.
- **Cross-contaminating ensemble runs.** If two weather models share upstream GFS initialization, they are not independent. Treating them as independent inflates confidence and compresses q_lcb toward q_raw.

---

### (5) Three Hardest Design Decisions

**A. Ensemble weighting scheme.** Uniform weighting is safe but wastes signal from better-calibrated models. Per-stratum weighting (by city, bin, season) is ideal but needs enough settled history per stratum to avoid overfitting the weights. Call: use global Brier-score weights initially, with a per-city shrinkage prior that pulls toward uniform when stratum N < 200 settled outcomes.

**B. q_lcb confidence level.** Too conservative (99%) and you never trade; too loose (80%) and you trade noise. The right level depends on position size relative to your bank. Call: tie it to sizing — use 90% for the ranking pass, then tighten to 95% at the sizing gate so large positions require higher conviction.

**C. Synchronous vs. asynchronous candidate refresh.** Markets move continuously; a single batch snapshot creates latency between q_lcb computation and execution. Call: generate candidates on a fixed schedule (e.g., every 15 minutes) but tag each candidate with the book snapshot timestamp and enforce a hard p_exec staleness gate at submission time. The ranking pass is batch; the staleness guard is real-time.

---

## refuter:candidate=DEFECT (angle R12, tag MECHANICAL_BLOCK, impact 95):

**Agent ID:** `agent-aea0d8045a4b0e175`

### Final Assistant Message

Zero RISK_GUARD_BLOCKED since 2026-06-13 — the block was a historical incident tied to the dependency_db_locked storm, already fixed in task #65. The 2113 rows are from the past incident, not current behavior.

---

## refuter:candidate=DEFECT (angle R11, tag MECHANICAL_BLOCK, impact 80):

**Agent ID:** `agent-aec96aa39eb046c61`

### Final Assistant Message

The log evidence is decisive. The M5 reconcile successfully cleared the ws_gap latch repeatedly throughout 06-11 and 06-12 (last clear at 06-12 17:24), and again at 06-14 01:06. The latch is NOT permanent — it clears via `clear_after_m5_reconcile()` which is the designed escape path. The DB also shows zero unresolved findings right now.

The defect claim is refuted on multiple grounds:

1. **Existing code already handles it** (kill criterion b): `clear_after_m5_reconcile()` at line 217 is a designed escape hatch that clears `m5_reconcile_required=False` regardless of `gap_reason`, and logs show it fired successfully dozens of times including after the 2026-06-12 event. The latch is NOT permanent — it has a documented and working clearing path via M5 reconciliation.

2. **The empirical claim is wrong** (kill criterion c): The logs show "M5 WS-gap reconcile cleared submit latch" at 06-14 01:06. The live system clears this latch; it is not permanently latched. The B1 blocker finding row (5bbc2be2) also shows resolved_at now set (zero unresolved findings in DB). The described "permanent latch" does not exist as described.

3. **Would not move a real fill independently** (kill criterion d): Even in the scenario the candidate describes, the permanent block was driven by the `exchange_reconcile_findings` row (reconcile_finding_count > 0 → independent kill), not just the ws_gap latch. The ws_gap latch only adds reduce-only mode until it clears; the reconcile finding is what arms the kill switch.

---

## reconcile:Order book / executable price / microstructure

**Agent ID:** `agent-aed62b66c8836ab88`

### Final Assistant Message

I now have sufficient evidence to assemble the final reconciled output. Let me map each finding:

**Confirmed real divergences (from IDEAL standard, evidenced by ACTUAL/EMPIRICAL):**

1. **MECHANICAL_BLOCK — Missing NO-token row in warm buffer kills buy_no when only YES row was fetched.** The `_snapshot_rows_by_condition_and_direction` at line 12849 routes `buy_no` to the `(condition_id, "buy_no")` key, which only matches rows where `selected_label == "NO"`. If the materializer only stored a YES-labeled row (which the WITNESS empirically confirms is the case — all 10 sampled rows are YES-labeled), `rows_by_direction.get((condition_id, "buy_no"))` returns `None` at line 7417, and the candidate gets `missing_reason = "missing executable snapshot row"` at 7433. The _depth_for_token_or_label fix (line 13541) only helps when a NO-labeled row IS present with a single-token book. This is not a code bug but a materializer data gap — NO-labeled rows are not being stored. The WITNESS shows 40 fills (all likely buy_no given strategy) happened through mid-June via GTC resting orders, not via new submissions.

2. **MECHANICAL_BLOCK — `_explicit_level` drops thin top-levels (size < min_order_size).** Line 13628 confirmed. For markets where depth_at_best_ask < min_order_size (e.g. 0.5 shares at ask, min_order_size=1), the fallback path returns empty asks, triggering NATIVE_QUOTE_MISSING. The full multi-level depth path via `asset_id` match is only available when the NO-labeled row is present.

3. **OBSERVABILITY_GAP — Fee reconciliation artifact is not on an automated schedule.** Line 93 in fee_authority.py: `observed_max_fee_fraction=0.0` from n=42 fills. The WITNESS confirms today's fills carry 0 bps. Real defect is governance, not math.

4. **OBSERVABILITY_GAP — Decision certificates completely dark after 2026-06-12T23:xx (WITNESS stage 4).** The WITNESS shows 0 new decision certificates on 2026-06-13 and 2026-06-14, coincident with the last LIVE receipts at 2026-06-12. This is the dominant live blocker — the decision engine stopped emitting, not a book pricing defect.

5. **OVER_ENGINEERING — `orderbook_depth_walk.walk_asks_for_target_shares` and `EntryQuoteEvidence` exist behind a default-OFF flag (`ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED` defaults to "0" at evaluator.py:315).** These are feature-gated dead code on the live path. The BLIND report was wrong that they were completely dead — they are gated-off, not unimported.

6. **OVER_ENGINEERING — `orderbook_projector.py` (`ProjectedBook`, `project_rest_snapshot`) has no non-test importer in production src.** Confirmed dead on the live path.

7. **CALIBRATION_EDGE_DEFECT — F5 (wrong-side top_ask in snapshot for maker BUY).** For maker-mode buy_no, the snapshot is created with `clob_no_ask_illiquid` and `executable_allowed=False`. `assert_snapshot_executable` at line 410 rejects it before reaching line 430. So the F5 semantic error is blocked at the tradeability gate, NOT at the ask-presence gate. The line-430 check is unreachable for maker-route orders because they never have `executable_allowed=True` with a null ask — they're always routed through the `clob_no_ask_illiquid` path. F5 as described is a false positive — the real gate blocks earlier.

---

## angle:Decision-gate liveness: candidate -> intent -> receipt/command.

**Agent ID:** `agent-af2fe7a8673b3f248`

### Final Assistant Message

I now have enough data to write the complete funnel analysis. Let me compile the final findings.

---

DECISION-GATE LIVENESS FUNNEL: 06-04 to 2026-06-14T05:31 UTC

All queries run against read-only DBs. Exact SQL included below each finding. Row counts are the denominator for every claim.

---

STAGE-BY-STAGE FUNNEL

Stage 1 — Market feed (EMS)
ALIVE. 3,563,610 total EMS rows; latest captured_at = 2026-06-14T05:31 UTC. The book-feed is fully live.
SQL: `SELECT COUNT(*), MAX(captured_at) FROM executable_market_snapshots` on zeus_trades.db.

Stage 2 — Opportunity events (BOOK_SNAPSHOT, FORECAST_SNAPSHOT_READY, DAY0_EXTREME_UPDATED)
ALIVE but not progressing to decisions. 6,477,614 total opportunity_events. The reactor consumes them as:
- BOOK_SNAPSHOT: 29,594 "ignored" + 2,112 "pending" + 78 "expired" since 06-13T20 — zero "processed". BOOK_SNAPSHOT events are superseded by the coalescer (`ignore_channel_cache_events` in `src/events/event_store.py:559`) before the reactor claims them. This is expected — BOOK_SNAPSHOTs are not the primary decision trigger.
- FORECAST_SNAPSHOT_READY (FSR): last "processed" = 2026-06-12T17:05:56 (1 row). After that: ALL expire without processing. 477,931 expired, 284,318 processed (all pre-06-12T17), 14,038 dead_letter (up to 06-12T04:47), 231 pending right now.
- DAY0_EXTREME_UPDATED: 421 processed since 06-13T20, latest 06-14T04:31. Day0 lane is live but shadow-only (RETIRED_DAY0_NO_SUBMIT_MARKER — no orders from this path).
SQL for FSR: `SELECT processing_status, COUNT(*), MAX(updated_at) FROM opportunity_event_processing WHERE event_id IN (SELECT event_id FROM opportunity_events WHERE event_type='FORECAST_SNAPSHOT_READY') GROUP BY processing_status` on zeus-world.db.

Stage 3 — edli_no_submit_receipts (decision evaluations)
DEAD since 06-12T12:12. 62,874 total receipts, all NO_SUBMIT, span 2026-05-31 to 2026-06-12T12:12. Zero receipts written in the 42 hours since.
SQL: `SELECT side_effect_status, COUNT(*), MIN(created_at), MAX(created_at) FROM edli_no_submit_receipts GROUP BY side_effect_status` on zeus-world.db.

Stage 4 — no_trade_events
DEAD since 2026-05-28. 2,952 rows, latest observed_at = 2026-05-28T06:10. This table stopped 17 days ago — consistent with migration to the event-reactor-adapter path which writes receipts directly.
SQL: `SELECT MAX(observed_at), MIN(observed_at), COUNT(*) FROM no_trade_events` on zeus-world.db.

Stage 5 — venue_commands
50 rows in zeus_trades.db. Last FILLED = 2026-06-11T17:18. Last row created = 2026-06-12T13:04 (EXPIRED). Nothing created in 43 hours.
SQL: `SELECT state, COUNT(*), MAX(created_at) FROM venue_commands WHERE created_at >= '2026-06-04' GROUP BY state ORDER BY cnt DESC` on zeus_trades.db.

Stage 6 — venue_order_facts / fills
40 historical fills (all pre-06-12). Zero new fills since 2026-06-11T17:18.

---

FIRST ZERO STAGE: FORECAST_SNAPSHOT_READY events stopped being processed at 2026-06-12T17:05:56 UTC.

LAST-GOOD / FIRST-BAD boundary:
- Last FSR processed: 2026-06-12T17 (1 row)
- Last receipt written: 2026-06-12T12:12
- Last venue_command created: 2026-06-12T13:04
- B1 position_drift finding 5bbc2be2: created 2026-06-12T22:58 — TEN HOURS AFTER the FSR processing and receipt gap started. B1 did not cause the gap; it arrived after the fact.

---

WHY DO CURRENT REJECTIONS WRITE NO RECEIPT?

Because no FSR events reach the "processed" state. The funnel breaks at the event-claim stage:

1. FSR events are created with `available_at = 2026-06-13T12:00:00` (the 06-13T12Z ECMWF cycle, captured 06-13T20:23). The reactor's `archive_expired_candidates` (`src/events/event_store.py:292`) marks any FSR whose target local date has fully ended in its city timezone as "expired". Markets for 06-13 targets are now past, so these FSR events expire without ever being claimed (attempt_count = 0).

2. No 06-14 source_run entries exist yet. The latest COMPLETE source_run is for 06-13T12Z, captured at 06-13T20:23. The 06-14T00Z ECMWF cycle has not yet produced a COMPLETE source_run (expected ~06-14T08-10 UTC). Zeus has 4,860 raw_model_forecasts rows as recently as 06-14T02:22, but they have not yet consolidated into a source_run entry.

3. Therefore: the forecast pipeline has no current-date data to trigger new FSR events. The 231 pending FSR events in the queue are all for 06-13 targets and will expire immediately when the sweeper runs.

4. The dead_letter errors from the 06-04 to 06-12 window show the prior rejection chain: "executable snapshot not captured after 8 attempts" (4,246 rows), "CALIBRATION_AUTHORITY_EVIDENCE_MISSING:model" (3,346), "FSR source_run_completeness_status='PARTIAL' must be COMPLETE" (2,830), "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:READINESS_EXPIRED" (568), "RISK_GUARD_BLOCKED" (57). These reflect the earlier gauntlet of gate failures before the chain stabilized on 06-12.

---

B1 STATUS (CONFIRMED PHANTOM, STILL UNRESOLVED)

Finding 5bbc2be2-350c-4bdf-ac0e-f080e41f9012 in zeus_trades.db exchange_reconcile_findings: kind=position_drift, context=ws_gap, recorded_at=2026-06-12T22:58, resolved_at=NULL. Evidence shows exchange_size=0, closed_position_evidence_class=terminal_position_current_chain_holdings. This is the ws_gap latch frozen at allow_submit=False via `m5_reconcile_required=True` in `src/control/ws_gap_guard.py:69`. However this only blocks submission — the receipt writing gap predates it by 10 hours and has a separate cause (FSR expiry / no 06-14 source_run).

There is exactly 1 unresolved finding. It will block order submission once FSR processing resumes.

---

POPULATION SUMMARY

| Stage | Population | Count | Last-good |
|---|---|---|---|
| EMS (book feed) | all snapshots | 3,563,610 | 06-14T05:31 (LIVE) |
| Opportunity events total | all events | 6,477,614 | 06-14T05:29 (LIVE) |
| FSR events processed | reactor claim | 284,319 | 06-12T17:05 (DEAD 42h) |
| No-submit receipts | decisions evaluated | 62,874 | 06-12T12:12 (DEAD 42h) |
| venue_commands created | orders attempted | 50 | 06-12T13:04 (DEAD 43h) |
| FILLED venue_commands | orders filled | 40 | 06-11T17:18 (DEAD ~60h) |

Bias risk: the zero-filled period 06-12 to present means the edge/rejection assessment relies entirely on the pre-06-12 cohort for empirical calibration. The current "rejected on capital_efficiency_lcb_ev" verdicts are unobservable in the funnel because no candidates even reach the receipt stage.

---

VERDICT: The pipeline is in a structural wait state, not a gate-rejection state. No candidates reach the decision compiler because all FSR events for current targets expire before the reactor claims them. The 06-14T00Z ECMWF run (expected ~06-14T08-10 UTC) is the next material event. Once that produces a COMPLETE source_run, new FSR events will fire for 06-14 targets, the reactor will process them, and receipts will resume. B1 (5bbc2be2) must then be manually resolved before those receipts can convert to venue_commands.

---

## angle:Friction / fees / slippage / threshold accounting.

**Agent ID:** `agent-af3fd52f4138e6e73`

### Final Assistant Message

Now I have all the material I need. Let me check the git log dates for the files I haven't yet:

---

## angle:Observability / no-trade provenance / debuggability.

**Agent ID:** `agent-af40e467b0fb0c3ee`

### Final Assistant Message

Now I have the full picture. The `decision_log` table is empty (0 rows total), meaning `query_no_trade_cases` in `decision_chain.py` always returns an empty list and `query_lifecycle_funnel_report` always shows `certified_empty`. This is a structural gap. Let me confirm it is the world DB being queried:

---

## angle:Market efficiency / counterparty / adverse selection.

**Agent ID:** `agent-af414918a3806850b`

### Final Assistant Message

**(1) What this angle actually does**

These three files together implement two orthogonal mechanisms that are on the market-efficiency / adverseselection axis.

**mainstream_agreement.py** is a four-check signal that asks "does our forecast agree with an external consensus (Open-Meteo), and is the trade direction consistent with both?" The four checks are: (1) external point available, (2) |our_point - mainstream_point| within tolerance (1.5C / 2F), (3) trade direction is consistent with where mainstream places probability (tolerance-aware, not bin-equality), (4) trade direction is consistent with our own modal bin. The verdict is REFERENCE-ONLY — it is recorded on receipts as observational provenance and is explicitly forbidden from blocking candidates (event_reactor_adapter.py:9105-9108, 10498-10502).

**market_phase.py + market_phase_evidence.py** are the market lifecycle gate. Only `PRE_SETTLEMENT_DAY` is admitted for forecast-only candidates (event_reactor_adapter.py:2379, market_phase.py:288-290). This is a real submission block: a candidate for a family whose local target day has already started (SETTLEMENT_DAY or later) is rejected with `EVENT_BOUND_MARKET_PHASE_CLOSED` at event_reactor_adapter.py:2379-2395.

Neither file touches adverse selection of the filled-edge variety (no fill-price vs quoted-price comparison, no execution VWAP vs limit comparison). The adverse selection machinery is in the adapter itself (_adverse_selection_proxy at line 5387, p_fill_lcb, maker/taker mode selection, spread guard), not in these three files.

---

**(2) Defects**

**D1 — `our_point=0.0` silent corruption when members are absent** (event_reactor_adapter.py:11176). The call is `our_point=our_point if our_point is not None else 0.0`. When `analysis.member_maxes` is None (no forecast members), `our_point` is None, so the gate is called with `our_point=0.0`. This is semantically wrong: 0.0°C / 0°F is not a valid "no point available" sentinel — it is the freezing point, which is a real value in the bin family. Reproduction: any city whose forecast snapshot has no member array (Day0 path with a missing members blob) will call `evaluate_mainstream_agreement` with `our_point=0.0` and `members=None`. Check 2 (`mainstream_close`) will then compare |0.0 - mainstream_point| against tolerance; for any city whose mainstream point is not near 0°, this produces `FAIL_NOT_CLOSE`. Because the verdict is reference-only (event_reactor_adapter.py:10498-10502), this does NOT block a submission on its own, but it silently poisons all receipt annotations for those families with a spurious `FAIL_NOT_CLOSE` rather than an honest `MAINSTREAM_FAIL_CLOSED`. **DEFECT_NO_CURRENT_IMPACT on submission, but corrupts audit data.**

**D2 — `modal_bin_from_members` returns None silently when all members round outside all bins** (mainstream_agreement.py:207-234). If the ensemble contains members that all round to values not covered by any bin (e.g. extreme outliers or a mismatch in the bins list), `best_i` stays -1 and the function returns None. The caller then sets `our_modal_label = None` and passes `our_modal=None` to `_same_bin`, which returns False regardless of direction. For `buy_yes` direction, `direction_agrees_our_modal` becomes False (line 347-350), causing `FAIL_DIR_VS_OUR_MODAL`. Again reference-only so **DEFECT_NO_CURRENT_IMPACT on submission**, but a complete-members city with extreme-tail members gets a phantom direction-inversion annotation.

**D3 — `_same_bin` None-guard masks a real direction inversion** (mainstream_agreement.py:237-242). When `our_modal` is None, `_same_bin(traded_bin, None)` returns False unconditionally. For `buy_no`, `direction_agrees_our_modal = not False = True` — the check silently passes even though we have no modal information. For `buy_yes`, it silently fails. The asymmetry is semantically backwards for `buy_no`: the check is supposed to catch direction inversions, but when we have no modal the correct result is "unknown / cannot check", not "pass". This is a semantic contradiction. **DEFECT_NO_CURRENT_IMPACT on submission** (reference-only), but the `buy_no` path silently marks "direction consistent with our modal" when there is no modal to be consistent with.

**D4 — Phase admissibility: `PRE_TRADING` markets are admitted as `PRE_SETTLEMENT_DAY` when `polymarket_start_utc` is None** (market_phase.py:175-178). When `polymarket_start_utc` is not in the market dict, `market_phase_from_market_dict` sets it to None. In `market_phase_for_decision`, when `polymarket_start_utc is None`, the `PRE_TRADING` branch at line 175 never fires, so a family whose Gamma record has no `market_start_at` and whose current time is before the market actually opens is classified as `PRE_SETTLEMENT_DAY` and admitted. The docstring acknowledges this ("treated as already trading by default — the caller must upstream-filter PRE_TRADING markets when start time is unavailable") but no upstream filter is evident in the reactor. A market that has not yet opened would be evaluated for trading and could receive a submission attempt, though the Polymarket CLOBs would reject the order at venue. **Potential wasted order attempts; unlikely to produce a fill but uses the submit budget.** Not a submission blocker in the sense of preventing +EV candidates from reaching submission.

**D5 — `family_venue_closed` uses only the F1 fallback (12:00 UTC) even when a verified Gamma `market_end_at` is available** (market_phase.py:414). The warm-lane skip in `family_venue_closed` always calls `_f1_fallback_end_utc` rather than reading the market dict — it does not accept a market dict. For any city whose actual `polymarket_end_utc` differs from the F1 12:00 UTC anchor, the venue-closed skip fires at the wrong time. The comment acknowledges the F1 anchor is "uniform across 13 verified cities", so if F1 genuinely holds this is not a defect in practice. But there is no in-code enforcement that the Gamma `market_end_at` must equal F1 — the anchor is an empirical observation, not a structural contract. If a market ever ends at a non-12:00 UTC time, `family_venue_closed` will disagree with the phase gate. **DEFECT_NO_CURRENT_IMPACT** as long as F1 holds; the gap is that F1 is asserted by comment, not by code.

**D6 — Over-built: `_maybe_bias_decay_kelly_haircut` has a dead XOR-branch tombstone comment** (event_reactor_adapter.py:11266-11268). The comment says "v2 typed-BiasTreatment XOR-haircut branch is DELETED (settlement-refuted; flag was permanently OFF)". The code path no longer exists but the comment is load-bearing for understanding. This is not a submission block but is an accumulation of dead-law narrative that must be read through to understand the current haircut path. **DEFECT_NO_CURRENT_IMPACT.**

---

**(3) What is notably absent**

These three files contain no:

- **Filled-edge adverse selection model.** There is no comparison of execution fill price to the midpoint or to a fair-value estimate derived from settled outcomes. The `_adverse_selection_proxy` in the adapter is a proxy (`|q_now - q_prev| * spread`) not grounded in actual fills. Whether historical fills on maker-quoted orders were adversely selected (i.e. the counterparty knew something about settlement before Zeus did) is neither measured nor fed back into these modules.

- **Counterparty model.** No tracking of whether the orders that fill Zeus's maker rests are informed (settlement-day weather observers) vs noise traders. A sound adverse-selection system for weather derivatives would use the pattern: maker fills on hot settlement days vs cold ones to estimate whether the fill side is correlated with settlement outcome.

- **Taker vs maker edge differentiation in q.** The same `q_lcb` is used for taker and maker mode selection (event_reactor_adapter.py:7896). There is no markup for the fact that maker fills are adversely selected (the counterparty chose to lift your quote, meaning they had a reason) while taker fills are not in the same way. A sound system would compute `q_lcb_maker` with an adverse-selection discount applied to the fill-probability-weighted posterior.

- **Mainstream staleness feedback into q.** When the Open-Meteo cache is cold (`mainstream_pt=None`), the gate is annotated as unknown but q is not haircut. A system concerned with counterparty edge would discount q when external consensus is absent (because absence of external consensus means we cannot check whether our model is diverging from what informed counterparties know).

- **Phase-based q adjustment.** `market_phase_evidence.py:104` specifies a 0.7x Kelly haircut for `fallback_f1` phase source. The code comment at line 22-24 says "A6 wires this in the resolver" — but `_maybe_bias_decay_kelly_haircut` does not implement it; the 0.7x `fallback_f1` haircut specified in the evidence module docstring is unimplemented. This is the most significant absence on the adverse-selection axis: `fallback_f1` phase evidence (which applies when Gamma has not supplied a verified endDate) is supposed to trigger a Kelly reduction but does not. **This would kill real +EV sizing on markets where we only have F1 phase evidence** — except the haircut is absent, so sizing is not reduced, meaning the risk goes unmitigated rather than the trade being blocked.

---

**(4) Provenance verdict**

**mainstream_agreement.py** — Created 2026-06-03, last audited 2026-06-03. Authority basis: Task #135 + operator directive 2026-06-03. The module accurately reflects its current role (reference-only, never gates). The demotion branch for `#135-B` was explicitly retired on 2026-06-03 and the constant is kept for back-compat only (line 76-82). No evidence of staleness. Verdict: **CURRENT_REUSABLE** for its stated purpose as an observational gate.

**market_phase.py** — Created 2026-05-04, last audited 2026-06-13 (header). The 2026-06-13 addition of `family_venue_closed` is documented. Core phase logic is unchanged since 05-04 (PLAN_v3 §2). D4 (PRE_TRADING admission when start unknown) is known and documented in the code; D5 (family_venue_closed ignores Gamma end_at) is a structural gap. Verdict: **CURRENT_REUSABLE** with the caveat that D4 is an acknowledged open gap and D5 is a latent risk if F1 ever breaks.

**market_phase_evidence.py** — Created 2026-05-04, last audited 2026-05-04 (header has not been updated since creation). The docstring at lines 22-24 promises a 0.7x Kelly haircut for `fallback_f1` that is never implemented anywhere reachable from the adapter. The file's own provenance stamp is 9 weeks stale relative to the adapter's current law. This module documents a design intent (A6 Kelly resolver) that was never built. Verdict: **STALE_REWRITE** — the intent-to-implement `fallback_f1` haircut is a broken invariant between what the evidence type promises and what the sizing path delivers. The file itself is structurally fine but the authority basis it was written under (A5/A6 oracle-Kelly plan) has been superseded by the ΔU ranker without updating the evidence module's stated downstream contract.

---

## refuter:candidate=DEFECT (angle R13, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-af61248816a3a28fd`

### Final Assistant Message

I now have a complete picture. Let me assemble the finding:

**What the defect claims:** `list_unresolved_findings` has no context/age filter, so any stale finding from any prior cycle permanently blocks the WS latch — including the specific B1 finding (5bbc2be2, position_drift on token 2599807256...).

**What the code and DB actually show:**

1. The specific B1 token's finding (5bbc2be2) was recorded 2026-06-12T22:58 and **resolved at 2026-06-14T06:01** with resolution `position_drift_settled_external_suppressed`. It was resolved by the suppression path at line 1888-1895, which runs INSIDE `_record_position_drift_findings` during the next sweep — meaning the sweep itself, when called via `run_ws_gap_reconcile_and_clear`, resolves the stale finding before `list_unresolved_findings` is called at line 320.

2. Current unresolved count: **0**. The latch-blocker finding was resolved.

3. The defect's core claim — that `list_unresolved_findings` has no filter, so stale findings accumulate — is **structurally true** of the API signature. But it misses the critical antibody: the `run_ws_gap_reconcile_and_clear` flow calls `run_reconcile_sweep` first (line 319), which includes `_record_position_drift_findings`, which attempts to auto-resolve existing findings for any suppressed or cleared token. Findings that should be cleared get cleared inside the sweep pass, BEFORE `list_unresolved_findings` is called.

4. The B1 blocker persisted because task #31 (external-close absorber) had a gap: the `_token_is_suppressed_external` path only works if the token is in `token_suppression`. The token needed to be added to `token_suppression` (by the harvester's `settled_position` path) for the sweep to auto-resolve. Once the suppression entry was added, the next sweep run auto-resolved it. This is precisely what task #31 addressed.

5. The defect is describing the SYMPTOM (stale finding blocked latch), not a novel structural root cause. The actual root cause was the harvester's suppression duty not being re-homed (task #31, already completed). The `list_unresolved_findings` lack of filter is a design choice: it intentionally treats ALL unresolved findings as blockers regardless of context, which is correct — a ghost_order from a prior week is still a valid signal unless the sweep resolves it.

**Kill verdict:** The defect is refuted. The code already handles it via the sweep's auto-resolution mechanism inside `_record_position_drift_findings`. A stale finding only persists if its token is genuinely still ambiguous (not suppressed, not cleared by re-sweep). The B1 blocker was a `position_drift_settled_external_suppressed` case where the antibody (task #31) was the correct fix. The `list_unresolved_findings` breadth is not the defect — it is correct behavior. The real resolution was adding the token to `token_suppression` so the sweep's auto-resolve path fires. Current unresolved count is 0, confirming the latch is clear.

---

## refuter:candidate=DEFECT (angle R2, tag SEMANTIC_CONFLICT, impact 85):

**Agent ID:** `agent-af79af09c163541b2`

### Final Assistant Message

The investigation is complete. Here are the findings:

**Repro query: 0 rows.** No live-valid data is currently excluded by the `recorded_at` gate that would pass an `issue_time` gate.

**Structural impossibility.** For ECMWF open data, `recorded_at >= issue_time` always holds by ingest design — you cannot record a model run before the model runs. Therefore the claimed scenario (fresh `issue_time` data silently excluded because `recorded_at` is stale) is logically impossible: `issue_time > cutoff` implies `recorded_at >= issue_time > cutoff`, so both gates agree.

**Direction of the real semantic error is inverted.** The `collection_frontier.py` "backfill can look fresh" doctrine concerns the opposite failure mode: old-cycle data re-ingested now gets a fresh `recorded_at`, passes the gate, and appears current when it shouldn't. The defect candidate claims the gate _excludes_ valid fresh data; the actual risk is that it _accepts_ stale-cycle data as fresh. These are opposite problems.

**`collection_frontier.py` does not govern `ecmwf_open_data_ingest.py`.** The frontier module is explicitly READ-ONLY and in-memory (its own docstring says so), with zero import relationship to the ingest file. Its "mandate" is documentary guidance for that diagnostic tool, not a contract binding on ingest queries.

**`readiness_state` is a red herring here.** The 463 expiring ECMWF rows live in `zeus-world.db` (via `WorldConnection`); `_query_metric` reads `zeus-forecasts.db`. No cross-DB readiness gating is architecturally possible at this call site, and none exists.

**Would fixing it move a real order?** No. Current ECMWF ingest has been dark since 2026-06-12; both `recorded_at` and `issue_time` are >24h stale. Swapping the predicate returns identical (zero) rows. Even prospectively, when ingest resumes, the gates produce the same result because `recorded_at ≈ fetch_time ≈ now` for freshly ingested data.

---

## angle:Contract universe / instrument identity / market lifecycle.

**Agent ID:** `agent-af9ce5bba8c58afd1`

### Final Assistant Message

Now I have enough to write the forensic audit. Let me check the `_token_is_suppressed_external` logic and the B1 finding evidence one more time:

---

## refuter:candidate=DEFECT (angle R4, tag MECHANICAL_BLOCK, impact 90):

**Agent ID:** `agent-afa1972de2bf13b32`

### Final Assistant Message

The flag is `true` in the live `config/settings.json`. The repro command failed (AttributeError on `.get`) with wrong syntax, so the "FLAG VALUE: True" result from the corrected call is the authoritative answer.

Summary of findings:

1. **Flag is True in live config** (`config/settings.json:130`). The defect premise is false.

2. **Empirical claim of 3,390 SHADOW_ONLY rows is a misread.** The `trade_authority_status` column is hard-enforced to `SHADOW_ONLY` for ALL replacement posteriors by `__post_init__` validation (bundle_reader.py line 112-113). This column has NOTHING to do with live-eligibility — it's a design invariant. Live eligibility is determined at runtime by `provenance_json.replacement_q_mode`. The DB has 1,553 FUSED_NORMAL_FULL + 294 FUSED_NORMAL_PARTIAL rows, 1,159 of which have bounds since 2026-06-12.

3. **The actual blocker is B1** (submit gate frozen by phantom reconcile finding row 5bbc2be2), not the replacement flag.

---

## angle:Forecast & observation acquisition / freshness.

**Agent ID:** `agent-afb6a6b258a9f6848`

### Final Assistant Message

I now have all the facts needed. Here is the full report.

---

**FORECAST & OBSERVATION ACQUISITION — ANGLE REPORT**

---

**1. WHAT THIS ANGLE ACTUALLY DOES**

The pipeline has two independent ingest planes for the live trade path.

Forecast plane: `forecast_live_daemon.py` owns the ECMWF OpenData scheduler. It runs two cron triggers per day (00Z at 08:10 UTC, 12Z at 20:10 UTC) plus a 5-minute poll job (`_opendata_safe_cycle_poll`), each calling `run_opendata_track` for `mx2t6_high` and `mn2t6_low`. `_forecast_work_identity` → `select_source_run_for_target_horizon` decides whether the cycle is released (`FetchDecision.FETCH_ALLOWED`); if so, `collect_open_ens_cycle` downloads and ingests the ECMWF GRIB files, writes `ensemble_snapshots` rows with `source_id='ecmwf_open_data'`, writes a `source_run` record, and then writes per-target `source_run_coverage` and `readiness_state` rows. The evaluator reads forecasts through `ECMWFOpenDataIngest._query_metric`, which is a fail-closed DB query on `ensemble_snapshots` filtered to `contributes_to_target_extrema=1`, `authority='VERIFIED'`, `causality_status='OK'`, `dataset_id LIKE 'ecmwf_opendata_%'`, `datetime(recorded_at) > datetime(cutoff)`, and the FULL_CONTRIBUTOR conditions. The 24-hour freshness window is `_FRESHNESS_WINDOW_HOURS = 24` (`ecmwf_open_data_ingest.py:65`).

Observation plane: `get_current_observation` in `observation_client.py` serves Day0 signals via WU ICAO timeseries (primary), with METAR fast-lane fallback and then IEM ASOS / Open-Meteo as non-settlement fallbacks. Coverage gating (`_compute_day0_coverage_status`) rejects the WU result if the first sample arrives more than 2 hours after local midnight.

Source health: `source_health_probe.py` writes `state/source_health.json` with a HEAD-only `ecmwf_open_data` probe (checks `https://data.ecmwf.int/forecasts/` is reachable — does NOT verify any actual data freshness in the DB). This file is consumed by `collection_frontier.py` to classify source health as degraded if `consecutive_failures >= 3`.

`collection_frontier.py` and `source_watermarks.py` are read-only diagnostic surfaces; they do not affect the live trade path directly but gate the `_classify` / `BLOCK_COVERAGE_UNKNOWN` verdict which is what the evaluator's readiness gate consults.

---

**2. DEFECTS — FILE:LINE + REPRODUCTION + BLOCKING VERDICT**

**DEFECT F-1 — CRITICAL LIVE BLOCKER: `_query_metric` queries `dataset_id` but the schema column is also `dataset_id` (correct) yet the LIKE clause uses the prefix `'ecmwf_opendata_%'` — this is correct; however the freshness cutoff is measured on `recorded_at` (write time) not on `source_issue_time` (event time).**

File: `src/data/ecmwf_open_data_ingest.py:405-443`. The 24-hour window `cutoff = (fetch_time - timedelta(hours=24)).isoformat()` is applied against `datetime(recorded_at) > datetime(?)` at line 408. `recorded_at` is the wall-clock time the row was inserted, not the ECMWF cycle issue time. The system-wide correctness rule (stated in `collection_frontier.py:26`: "freshness age is measured on the SOURCE/EVENT-time plane (source_issue_time), NEVER on a write-time plane") is violated here. A backfill or re-ingest of an old cycle written within the last 24 hours would pass this gate falsely; conversely, a legitimately valid and current cycle that was ingested more than 24 hours ago (e.g. the previous 12Z if the daemon missed the current 00Z and no fresh backfill ran) is excluded even though the data is otherwise usable for targets within its forecast horizon. In the real DB: the 2026-06-13 12Z cycle was ingested at `2026-06-13 20:24:33`, so it is still within 24h of now and passes. But if it had been ingested 25+ hours ago, any city still in horizon would be silently refused by the evaluator. **This does not block today's run (current cycles are fresh), but is an invariant violation that will silently starve the evaluator the next time a fetch is delayed by more than 24h relative to query time.**

**DEFECT F-2 — REDUNDANT / OVER-BUILT GATE: `ensemble_client.py:150-161` hard-refuses any call to `fetch_ensemble` for `source_id='ecmwf_open_data'` regardless of role.**

File: `src/data/ensemble_client.py:150-161`. The guard is: `if source_id == "ecmwf_open_data" and source_spec.ingest_class is None: raise SourceNotEnabled(...)`. But `ecmwf_open_data` has `ingest_class=ECMWFOpenDataIngest` in the registry (`forecast_source_registry.py:304-311`), so this branch is unreachable in practice. The comment was written before `ECMWFOpenDataIngest` existed; the guard is now dead code that documents a fixed bug but can never fire. Tag: DEFECT_NO_CURRENT_IMPACT (the live path routes through `ingest_class`, so the guard is bypassed every time). The comment's "future role-specific exceptions" guidance is misleading because the guard is unreachable.

**DEFECT F-3 — SOURCE HEALTH PROBE CHECKS INFRASTRUCTURE, NOT DATA FRESHNESS.**

File: `src/data/source_health_probe.py:271-304` (`_probe_ecmwf_open_data`). The probe does an HTTP HEAD against `https://data.ecmwf.int/forecasts/` and declares success if status < 500. It does NOT check whether any usable rows exist in `ensemble_snapshots` for the current cycle, whether `readiness_state` is non-expired for live targets, or whether the ingest daemon has actually run recently. `ECMWFOpenDataIngest.health_check()` (`ecmwf_open_data_ingest.py:124-158`) does exactly that DB check, but it is never called from the probe loop. Result: the `consecutive_failures` accumulator in `collection_frontier._classify` (`line 184`) will never reach >= 3 due to an ECMWF fetch failure (the data CDN can be up while the actual GRIB cycle download fails). The `_BLOCK_DOWN` classifier is structurally unreachable for this source even during real data outages. Tag: DEFECT_NO_CURRENT_IMPACT (the frontier is diagnostic, not a trade blocker on its own), but misleads the operator's `_BLOCK_DOWN` diagnosis path.

**DEFECT F-4 — `_query_metric` SUBQUERY REPEATS FULL FILTER, CREATING O(N²) COST AND A DUAL-BIND PARAMETER BUG RISK.**

File: `src/data/ecmwf_open_data_ingest.py:391-443`. The correlated subquery at lines 412-430 binds parameters `(SOURCE_ID, _DATA_VERSION_PREFIX + '%', cutoff)` again (same three values already in the outer WHERE). If a caller accidentally passes a different `cutoff` to the outer and inner, they will silently disagree. Currently safe because the same variables are used, but it is fragile. The 8-parameter bind is error-prone. The correlated subquery runs per row of the outer scan. Tag: DEFECT_NO_CURRENT_IMPACT on current data sizes, but a latent correctness trap.

**DEFECT F-5 — `_CURRENT_SOURCE_CYCLE_STATUSES = frozenset({"SUCCESS"})` EXCLUDES PARTIAL, CREATING A STALE-JOURNAL DEAD ZONE.**

File: `src/ingest/forecast_live_daemon.py:72-82` with `_latest_job_run_current_for_identity` at line 499-534. If the ingest daemon runs, downloads a partial set of steps, and writes status `PARTIAL` to `job_run`, the next poll in `_run_opendata_track_if_due` (line 677) sees the cycle is NOT current (PARTIAL not in `_CURRENT_SOURCE_CYCLE_STATUSES`) and calls `run_opendata_track` again. The comment at line 73-81 explains this is intentional: PARTIAL means more GRIB steps may still publish, and idempotent refetch handles it. This is correct design, not a defect. However, the `_job_status_from_result` function at line 373 classifies `source_run_status == "PARTIAL"` as returning status `"PARTIAL"` — this flows into `_write_job_run` with status `"PARTIAL"`. The daemon then sees it on the next poll and re-runs (correct). No blocking defect here.

**DEFECT F-6 — OBSERVATION FAST-LANE: `_fuse_wu_prefix_with_metar_tail` GATE IS COVERAGE-STATUS-ONLY, IGNORING WU SAMPLE COUNT.**

File: `src/data/observation_client.py:345-369`. The fuse function checks `wu_cov in ("OK", "LOW_COVERAGE")` to decide whether WU proves the prefix, then copies WU's `coverage_status` directly to the fused result (line 364). If WU has `coverage_status="LOW_COVERAGE"` (sample count < 4, `_DAY0_MIN_SAMPLE_COUNT=4`), the fused context carries `coverage_status="LOW_COVERAGE"`. Downstream, if the evaluator requires `coverage_status == "OK"` to treat the observation as executable, a LOW_COVERAGE prefix will still result in a non-executable fused observation. This is arguably correct behavior (LOW_COVERAGE is declared), but the operator commentary in the `_fuse_wu_prefix_with_metar_tail` docstring (line 328) implies the intent is to restore a full-coverage context; if the evaluator then rejects LOW_COVERAGE, the whole fusion is silent-no-op from the trader's perspective. Tag: DEFECT_NO_CURRENT_IMPACT unless the evaluator rejects LOW_COVERAGE; ambiguous intent.

**DEFECT F-7 — `source_watermarks.py:compute_watermark` BUILDS WRONG TRACK QUERY FOR `horizon_profile` == 'full' or 'short'.**

File: `src/data/source_watermarks.py:76-82`. When `horizon_profile` is `'full'` or `'short'`, the params tuple is `(source_id, f"{track}_{horizon_profile}_horizon", f"{source_id}:{track}:{horizon_profile}")` but `track_clause` is `(track = ? OR release_calendar_key = ?)` — that is two `?` placeholders, but the params have three values (source_id at position 0 is the WHERE `source_id = ?` bind). The outer WHERE already has `source_id = ?` as the first param, and then `track_clause` uses two more. So params=(source_id, expanded_track, release_key) maps correctly to three binds. On inspection this is correct. DEFECT_NO_CURRENT_IMPACT (watermarks are diagnostic only).

---

**3. WHAT IS NOTABLY ABSENT vs a SOUND SYSTEM**

a. **No event-time freshness gate in `_query_metric`.** The evaluator asks "do I have a valid forecast for this city/date?" but the DB query filters only on `recorded_at` (write time, line 408). A sound system would filter on `source_issue_time >= (now - max_age)` or on `readiness_state.status = 'LIVE_ELIGIBLE' AND expires_at > now`. The `readiness_state` table exists and has 467 non-expired LIVE_ELIGIBLE rows right now, but `ECMWFOpenDataIngest._query_metric` never consults it — it re-derives freshness purely from `recorded_at`.

b. **No per-scope staleness signal in the evaluator-facing ingest path.** When `ECMWFOpenDataIngest.fetch()` raises `ValueError` (no rows found), the evaluator logs `ENS_FETCH_FAILED` but gets no structured reason (expired vs genuinely missing vs coverage-blocked). The `readiness_state` table could supply that distinction; it is not used.

c. **`source_health_probe` for `ecmwf_open_data` is infrastructure-only (HEAD check), not data-plane.** `ECMWFOpenDataIngest.health_check()` exists but is not wired into the probe loop, so `consecutive_failures` never accumulates for real data failures, and `_BLOCK_DOWN` in the frontier is unreachable for this source.

d. **WU observation: 23-hour limit with no explicit gap-fill for hours 23-00 local.** `_fetch_wu_observation` requests `hours=23` (line 592). On standard days this covers the full local day when sampled mid-day; on settlement days near midnight it may miss the final observation hour. The `_DAY0_COVERAGE_WINDOW_GRACE_HOURS = 2` gate (line 142) guards against a missing opening window but there is no guard for a truncated closing window.

e. **No automatic staleness alert or circuit-breaker when `readiness_state` expires en masse.** The 467 currently non-expired LIVE_ELIGIBLE rows expire by `2026-06-14T20:23:09Z`. If the daemon does not ingest the next cycle (06-14 00Z) by that time, the frontier will show 0 live-eligible rows for all targets and the evaluator will have no valid forecast — silently. There is no watchdog that escalates when `expires_at` mass-expiry is imminent.

---

**4. PROVENANCE VERDICTS**

`src/data/forecast_source_registry.py` — Created 2026-04-27, last audited 2026-04-27. Written under R3/F1 slice authority. The `SOURCES` dict, `calibration_source_id_for_lookup`, and `ENSEMBLE_MODEL_SOURCE_MAP` are current and match the live routing fix documented in comments (Phase 3, 2026-05-04). The `select_empirical_replacement_strategy` logic is internally consistent. **CURRENT_REUSABLE** with the note that the header date is stale (no re-audit since Apr 27 despite multiple routing changes since).

`src/data/forecast_fetch_plan.py` — No provenance header. Minimal pure-dataclass module; no significant logic risk. **CURRENT_REUSABLE** (no law-sensitive logic).

`src/data/ecmwf_open_data_ingest.py` — Created 2026-05-19, last audited 2026-05-23. Carries a clear live-trade-blocker fix note. The `_query_metric` DAY0-P1 run-selection update (2026-05-23) is present. The `recorded_at`-based freshness window (Defect F-1) predates the `collection_frontier` event-time correctness mandate (2026-05-24). **STALE_REWRITE** on the freshness filter (the `recorded_at` window should be replaced with source-event-time or readiness-state-gated query to match the 2026-05-24 frontier law).

`src/data/openmeteo_client.py` — No provenance header. Simple HTTP retry wrapper; no law-sensitive logic. **CURRENT_REUSABLE**.

`src/data/ensemble_client.py` — No provenance header, but internally references Phase 2.6 (2026-05-04) and Phase 3 (2026-05-04) hardening. Defect F-2 (unreachable guard) is a documentation artifact. **CURRENT_REUSABLE** with the dead-code annotation on lines 150-161.

`src/data/observation_client.py` — Created 2026-04-21, last audited 2026-05-24. Contains the 2026-06-12 Denver incident fusion fix. The `_fuse_wu_prefix_with_metar_tail` function was added after the last audit date (the incident was 2026-06-12); the header was not updated. **CURRENT_REUSABLE** (the logic is sound and the fusion is documented); header audit date is stale.

`src/data/source_health_probe.py` — Created 2026-04-30, last audited 2026-05-31. Defect F-3 (HEAD-only probe for `ecmwf_open_data`). Written before `ECMWFOpenDataIngest.health_check()` existed. **STALE_REWRITE** for the `_probe_ecmwf_open_data` function — it should call `ECMWFOpenDataIngest().health_check()` to report actual DB freshness, not just CDN reachability.

`src/data/dual_run_lock.py` — Created 2026-04-30, last reused 2026-05-14. Pure POSIX advisory lock; no law-sensitive logic. **CURRENT_REUSABLE**.

`src/data/collection_frontier.py` — Created 2026-05-24, last audited 2026-05-24. Implements the correct event-time freshness law. The `_BLOCK_DOWN` path for `ecmwf_open_data` is structurally unreachable (Defect F-3) but the frontier itself is read-only diagnostic. **CURRENT_REUSABLE** as a diagnostic surface; the `_BLOCK_DOWN` gap is a probe-side defect, not a frontier defect.

`src/data/tier_resolver.py` — Created 2026-04-21, last audited 2026-04-24. Pure city→tier mapping; no live-decision logic. **CURRENT_REUSABLE**.

`src/data/source_watermarks.py` — Created 2026-05-24, last audited 2026-05-24. Read-only diagnostic. **CURRENT_REUSABLE**.

`src/ingest/forecast_live_daemon.py` — Created 2026-05-14, last audited 2026-05-23. Multiple post-audit changes documented in inline comments (2026-06-08, 2026-06-10, 2026-06-11). The replacement-forecast cron fix (four cycles instead of two, 2026-06-10 dead-zone incident) is present. `_CURRENT_SOURCE_CYCLE_STATUSES = frozenset({"SUCCESS"})` is correct per the PARTIAL-refetch intent. **CURRENT_REUSABLE**; header audit date is stale relative to 2026-06-08/10/11 changes.

---

**SUMMARY OF BLOCKING vs NON-BLOCKING FINDINGS**

| Finding | Would block submission / kill +EV candidate? |
|---|---|
| F-1: `recorded_at` freshness window in `_query_metric` | YES — in the next outage scenario (fetch delayed >24h), evaluator silently gets no forecast, all non-day0 candidates rejected |
| F-2: Unreachable `fetch_ensemble` guard for `ecmwf_open_data` | No (dead code, bypassed by `ingest_class`) |
| F-3: `_probe_ecmwf_open_data` checks CDN HEAD not DB freshness | No direct block today; masks real data failures from `_BLOCK_DOWN` classifier |
| F-4: Correlated subquery dual-bind fragility | No current impact |
| F-5: PARTIAL not in current-cycle set (by design) | No block |
| F-6: LOW_COVERAGE prefix propagation in WU+METAR fusion | Potential silent rejection of Day0 observations with sparse early WU coverage |
| Absent: no mass-expiry watchdog for `readiness_state` | Latent: 467 non-expired rows expire by 2026-06-14T20:23Z; if next ingest misses, silent zero-forecast condition |

---

## refuter:candidate=DEFECT (angle R1, tag OBSERVABILITY_GAP, impact 72):

**Agent ID:** `agent-afcf2bf01ec39c2cf`

### Final Assistant Message

opportunity_fact had 261 rows in the 2026-05-25 to 2026-05-28 window (not zero as claimed). The zero since 2026-05-28 is unrelated to this defect. The critical finding is that the live cycle path bypasses the MATCH gate entirely: `read_persisted_weather_markets` injects `"source_contract": {"status": "MATCH"}` unconditionally at line 3328.

Now answer the four kill-criteria:

(a) Is the ideal naive about a real constraint? Partially — the MATCH check is a legitimate instrument-identity validation. But the observability gap (WARNING vs ERROR) is a valid concern in principle.

(b) Does existing code already handle it? YES. The live cycle path (`env=="live"`) reads via `read_persisted_weather_markets` which hardcodes `status="MATCH"` at line 3328. The MATCH gate in `_parse_and_persist_weather_events` is only exercised by the background discovery path that writes to EMS — it never silently drops markets from live evaluation because the live path reads EMS directly, bypassing the gate entirely.

(c) Is the empirical claim survivorship-biased? YES. The claim that "opportunity_fact and opportunity_events have zero rows" is factually wrong: opportunity_events has 5.5M rows since 06-04, and opportunity_fact has 38k rows total (last written 05-28, which itself predates the claim window but is not zero). The absence of opportunity_fact rows since 05-28 is a separate issue unrelated to the MATCH gate.

(d) Would fixing it move a real order? NO. The MATCH gate cannot silently drop live market evaluations because the live reader constructs its own MATCH status unconditionally. Adding an ERROR counter would only improve observability on the background scanner path, which doesn't directly feed live decisions.

---

## angle:Execution submit / ack / fill / reconcile lifecycle.

**Agent ID:** `agent-afda3ed129c9f1774`

### Final Assistant Message

I now have all the material needed for the complete report. Here it is:

---

SECTION 1: WHAT THIS ANGLE ACTUALLY DOES (MECHANISM)

The execution submit/ack/fill/reconcile lifecycle forms a two-loop structure around a fail-closed gate chain.

Submit path: When the engine selects a +EV candidate, executor.py calls `_assert_ws_gap_allows_submit()` (line 3308) which raises `WSGapSubmitBlocked` if the ws_gap_guard latch is closed. Before that, it checks economic-unknown blocking via `find_unknown_command_by_economic_intent()` (lines 3357–3400), which blocks any new submit on the same token/side/price/size if a prior command is in `SUBMIT_UNKNOWN_SIDE_EFFECT`, `UNKNOWN`, or `REVIEW_REQUIRED`. Passing the ws_gap gate, the executor runs a corrected-identity component (line 3241), then calls `insert_command()` in venue_command_repo.py (line 783) which asserts snapshot freshness and envelope gates. Only on a clean write does it call the venue adapter's `submit()`.

Reconcile path: exchange_reconcile.py runs `run_ws_gap_reconcile_and_clear()` (line 290), which calls `run_reconcile_sweep()` then checks `list_unresolved_findings()`. If ANY finding has `resolved_at IS NULL`, it returns `status="blocked"` and does not call `ws_guard.clear_after_m5_reconcile()`. The latch stays closed. The refresh path `refresh_unresolved_reconcile_findings()` (line 347) runs narrower position-drift re-checks on an interval; it calls `_resolve_position_drift_tokens_from_current_truth()`, which contains every auto-resolver including the settled-external absorber (line 2445) and the operator-external-close absorber (line 2477).

Fill/ack path: fill_tracker.py promotes `CONFIRMED` trade facts to `entered` state. command_recovery.py has multiple projection-repair loops that find commands with fill facts but no position_current row and attempt to retroactively create the position record.

ws_gap_guard.py is a module-level singleton. `_status` is initialized at module load (lines 95–102) with `m5_reconcile_required=True, gap_reason="not_configured"`. This means every daemon boot starts with the latch CLOSED, blocking all new submits until an M5 sweep completes with zero unresolved findings.

---

SECTION 2: DEFECTS

**DEFECT-1 (SUBMISSION BLOCKER — CONFIRMED ROOT CAUSE OF THE FREEZE):**
ws_gap_guard.py lines 95–102 + 132–177 + 290 (exchange_reconcile.py).

The boot latch `m5_reconcile_required=True` clears via `record_message()` only when `gap_reason in {"not_configured", None}` (line 160–165). Once a real disconnect gap has been recorded, `gap_reason` becomes a non-null string like `"ws_disconnect"` and `record_message()` no longer clears the flag. Only `clear_after_m5_reconcile()` can clear it, and `clear_after_m5_reconcile()` raises `WSGapSubmitBlocked` unless `findings_count == 0 AND unresolved_findings_count == 0` (lines 240–245). Any single unresolved row in `exchange_reconcile_findings` keeps the latch permanently closed on every cycle. This is the mechanism: one unresolved finding → latch stays latched → no new submits forever.

Reproduction: insert a row into `exchange_reconcile_findings` with `resolved_at IS NULL`, trigger a WS gap, watch `assert_ws_allows_submit()` raise `WSGapSubmitBlocked` for every subsequent submit attempt regardless of opportunity quality.

**DEFECT-2 (SUBMISSION BLOCKER — PHANTOM 5bbc2be2 NOT ABSORBED):**
exchange_reconcile.py lines 1068–1104, 1144–1164, 2445–2473.

Terminal phantom 5bbc2be2 is a position where the operator sold (externally filled) the tokens on the shared wallet. This creates a `position_drift` finding: journal confirms a long, exchange shows 0, but no SELL exit fact exists in Zeus's journal. Two absorbers could resolve it:

(a) The settled-external absorber (line 2445) requires `calendar_terminal.get(token)` returns non-None, meaning the market's target local day must have ended >= 24h ago. If 5bbc2be2's market had not yet crossed the 24-hour settled terminal buffer at the time of the M5 sweep, this absorber fires on `False` and falls through.

(b) The operator-external-close absorber (line 2477) calls `_operator_acknowledged_drift_resolution()` (line 1068), which requires a prior RESOLVED `position_drift` finding for the same token with `resolved_by LIKE 'session_operator_confirmed%' OR resolution LIKE 'operator_manual%'`. For phantom 5bbc2be2 this is a FIRST occurrence — no prior ack row exists — so `_operator_acknowledged_drift_resolution()` returns `None` (line 1103), `_absorb_operator_external_close()` returns `False` immediately (line 1163–1164), and the finding stays unresolved.

Neither absorber fires for a first-occurrence externally-sold position on a market still within its 24h terminal buffer. The finding remains unresolved and the latch stays closed.

Reproduction: on the shared wallet, sell a Zeus-held position token externally; within 24h of market end; observe `exchange_reconcile_findings` accumulates an unresolved `position_drift` finding; ws_gap latch never clears.

**DEFECT-3 (DEFECT_NO_CURRENT_IMPACT but makes future phantom-like events undetectable):**
command_recovery.py line 586 and line 897.

`_json_dict` is defined twice. The second definition at line 897 is dead code (the first at line 586 is the active one — confirmed by the call at line 2513 reading `_json_dict(candidate.get("ack_payload_json"))`). No impact on blocking but violates single-definition convention; next refactor risks using the wrong one.

**DEFECT-4 (SUBMISSION BLOCKER — REVIEW_REQUIRED permanent freeze):**
venue_command_repo.py line 41, command_bus.py (IN_FLIGHT_STATES), venue_command_repo.py `_TRANSITIONS`.

`REVIEW_REQUIRED` is in `UNRESOLVED_SIDE_EFFECT_STATES` (line 41). `find_unknown_command_by_economic_intent()` (line 2587) blocks any new submit on the same token/side/price/size when a prior command is in `REVIEW_REQUIRED`. `REVIEW_REQUIRED` has no automatic exit path — `_TRANSITIONS` only exits it via `REVIEW_CLEARED_*` events or `FILL_CONFIRMED`, both requiring manual or external proof. If a command lands in `REVIEW_REQUIRED` and no operator clears it, the same economic intent is blocked permanently (not just for this submit cycle, but for every cycle until operator intervention).

Reproduction: let any command reach `REVIEW_REQUIRED` state (e.g., via venue ambiguity during SUBMITTING); attempt a new order at the same token/side/price/size; observe `_orderresult_from_economic_unknown` blocks it indefinitely.

**DEFECT-5 (SILENT RECOVERY FAILURE — can mask a stuck position that keeps WS latch open):**
command_recovery.py lines 2602–2625 (`reconcile_filled_entry_projection_repairs`) and 2348–2369 (`reconcile_live_entry_projection_repairs`).

The outermost `reconcile_filled_entry_projection_repairs` loop wraps `_append_filled_entry_projection_repair` in a proper SAVEPOINT/rollback with explicit error logging (lines 2615–2624). However, `_append_filled_entry_projection_repair` (line 2188) raises `ValueError` at line 2200 if `_decision_log_trade_case_for_command()` returns no trade_case. That ValueError is caught by the loop's outer `except Exception` (line 2616), logged, and counted as `errors`. The filled ENTRY command remains without a `position_current` row. Subsequent exchange_reconcile sweeps may find that the journal has a position but the exchange does not (because the fill was external), creating a `position_drift` finding that feeds back into Defect-1.

Note: the earlier read of command_recovery.py lines 1–2000 found `_append_matched_order_fill_projection()` at line 1207 wrapping `_ensure_entry_fill_position_event` in a bare `try/except` that swallows failures silently without a SAVEPOINT rollback. This is distinct from — and weaker than — the outer loop's handler above. The inner try/except has no `conn.execute("ROLLBACK TO SAVEPOINT ...")`, so partial DB state can be committed even on exception.

**DEFECT-6 (SUBMISSION BLOCKER — list_unresolved_findings scope is global):**
exchange_reconcile.py lines 1800–1825, run_ws_gap_reconcile_and_clear line 320.

`list_unresolved_findings(conn)` returns ALL rows where `resolved_at IS NULL`, regardless of `kind` or `context`. This means a stale `exchange_ghost_order` finding from a week ago, or a `unrecorded_trade` finding for an operator's manually-placed non-weather trade, contributes to the unresolved count that keeps the WS latch closed. The gate at line 331 (`if findings or unresolved`) blocks latch clearance if ANY historical finding is unresolved even if it has nothing to do with the current gap.

Reproduction: have any one stale unresolved finding from any prior M5 context; trigger a new WS gap; observe M5 clears 0 new findings but the old stale one prevents latch clearance.

**DEFECT-7 (DEFECT_NO_CURRENT_IMPACT — expired decorator):**
live_executor.py (as summarized from pre-compaction read).

`@untyped_for_compat` expired 2026-06-05. The decorator is dead code present on production execution paths. No submission impact currently; creates drift between type annotations and runtime behavior in future refactors.

**DEFECT-8 (DEFECT_NO_CURRENT_IMPACT — order_truth_reducer default):**
order_truth_reducer.py line 154.

When no order facts, trade facts, and no `open_order_present` and no command_state are provided, `VenueOrderTruthReducer.reduce()` returns `CanonicalOrderTruth("UNKNOWN", None, Decimal("0"), UNKNOWN_SIDE_EFFECT)`. A caller passing an empty `order_facts` iterable without an explicit `command_state` for a FILLED command would get `UNKNOWN` back, potentially escalating the command into a blocking unknown state. Not confirmed as an active trigger but is a semantic ambiguity at the bottom fallthrough.

**DEFECT-9 (SUBMISSION BLOCKER RISK — _resolve_position_drift_tokens_from_current_truth missing continue on external-close absorber):**
exchange_reconcile.py lines 2477–2491.

In `_resolve_position_drift_tokens_from_current_truth`, after `_absorb_operator_external_close()` returns `True`, the code calls `_resolve_open_position_drift_findings()` (line 2485) but then falls through to the end of the loop body — there is no `continue` statement. If there happens to be another absorber check after this (there isn't currently, but the code structure doesn't guard against it), the finding could be re-examined. More critically, even when `_absorb_operator_external_close()` returns `False` (the common case for 5bbc2be2), execution reaches end-of-loop without recording any new finding — so the finding created in the FULL sweep (run_reconcile_sweep path) is left open, which is the correct behavior, but it means the refresh path provides no new information for this case. Not a current additional blocker beyond Defect-2, but the missing `continue` is a fragile code pattern.

---

SECTION 3: WHAT IS ABSENT VS WHAT A SOUND SYSTEM NEEDS

**ABSENT: First-occurrence external-close pathway.** The external-close absorber requires a prior operator-acknowledged drift resolution row. For the first time a token appears as a drift (the only time it matters), no such row exists. A sound system would have either: (a) an operator-acknowledgment mechanism during the current live session (e.g., a session command `operator ack external close token=<X>` that writes the necessary `session_operator_confirmed` row before the absorber can fire), or (b) an automatic evidence-based absorber for the case where exchange_size==0 + journal_long>0 + no open SELL command + market has ANY external trade record for that token on that side. Currently only (b) exists for the settled-terminal case (24h buffer), leaving non-settled recent external closes permanently unresolved.

**ABSENT: Per-kind or per-context unresolved-finding filter in the ws_gap latch gate.** `list_unresolved_findings(conn)` is globally scoped. The ws_gap clearance check uses the full global unresolved count. A sound system would distinguish between latch-relevant findings (live exchange ghost orders, position drifts for tokens Zeus actively holds, unrecorded trades for Zeus's domain) and non-blocking stale findings (prior cycles' ghost orders already proven benign but not yet garbage-collected). An unresolved count scoped to `context='ws_gap'` or `kind IN ('position_drift', 'exchange_ghost_order')` with an active-window filter would prevent stale rows from perpetually blocking new submits.

**ABSENT: Automatic REVIEW_REQUIRED clearance pathway.** When a command lands in `REVIEW_REQUIRED`, the only exit is manual operator intervention. A sound system would have a proof-backed auto-resolver that: (a) queries the venue for the order by ID, (b) if the order is in a terminal state, applies the corresponding terminalizing event; (c) if the order is filled, creates the fill projection. Without this, a single ambiguous submit in a venue reliability incident permanently blocks that token/side/price/size.

**ABSENT: Position-drift finding for operator co-trades should be distinguished from Zeus own-trades.** The shared wallet (operator manual co-trading) means foreign fills show up as Zeus's position drifts. The foreign-wallet ghost absorber only applies to open orders on non-Zeus-domain markets. There is no equivalent filter for position_drift findings caused by operator manual fills on Zeus-domain tokens. A sound system would track which positions are Zeus-originated vs operator-manual, and suppress drifts accordingly without requiring the operator-ack ceremony.

---

SECTION 4: PROVENANCE VERDICTS

**src/control/ws_gap_guard.py** — Created 2026-04-27, audited 2026-05-16. Authority: M3.yaml. CURRENT_REUSABLE for the stated authority. The gate mechanics are correct as specified — the freeze behavior is by design (fail-closed). The defect is in what keeps the latch open (unresolved findings), not in the guard itself.

**src/execution/exchange_reconcile.py** — Created 2026-05 (R3 M5), last audited 2026-06-10. Authority: R3 M5 (ws_gap reconcile authority). CURRENT_REUSABLE. The 2026-06-10 update added the settled-external absorber (task #31). The operator-external-close absorber predates that. Both are present. The gap is not a code bug in these functions — it is a protocol gap (first-occurrence external close requires a prior ack row that does not exist for the first incident).

**src/execution/command_recovery.py** — Created 2026-04-26, last reviewed 2026-05-21, last reused 2026-06-11. Authority: R3 operator-recovery law. CURRENT_REUSABLE per audit date, but the silent-swallow inner try/except at line 1207 (inside `_append_matched_order_fill_projection`) is a known fragile pattern. The outer savepoint/rollback loops added in the 2600-line range are safer. Flag as PARTIAL_STALE on the inner projection helpers — they were written before the savepoint discipline was established and have never been upgraded.

**src/execution/executor.py** — Created 2026-05, last updated 2026-06-12 (unconditional conn.commit() fix, CRITICAL-2). CURRENT_REUSABLE.

**src/execution/live_executor.py** — Created 2026-05-06, audited 2026-05-21. CURRENT_REUSABLE modulo the dead `@untyped_for_compat` decorator.

**src/execution/venue_adapter.py** — Created 2026-05-06, audited 2026-05-21. CURRENT_REUSABLE.

**src/execution/fill_tracker.py** — Last audited pre-compaction. CURRENT_REUSABLE.

**src/execution/exit_lifecycle.py** — Created pre-2026-06-12; the `handle_exit_pending_missing` env var fix landed 2026-06-12. CURRENT_REUSABLE post-fix.

**src/execution/command_bus.py** — Created 2026-04-26, audited 2026-05-15. CURRENT_REUSABLE.

**src/execution/order_truth_reducer.py** — Created 2026-05-21, audited 2026-05-21. CURRENT_REUSABLE. The file is compact and self-contained.

**src/state/venue_command_repo.py** — Created 2026-04-26, audited 2026-05-21. CURRENT_REUSABLE. The UNRESOLVED_SIDE_EFFECT_STATES at line 41 including REVIEW_REQUIRED is intentional design, not a bug.

**src/venue/polymarket_v2_adapter.py** — Created 2026-04-27, last audited 2026-06-12 (redeem forbidden law applied, HTTP/1.1 timeout bound applied 2026-05-31). CURRENT_REUSABLE.

---

SUMMARY: The no-new-orders symptom has one primary mechanistic cause (Defect-1 + Defect-2 working together). The WS gap latch boots CLOSED. It clears ONLY when `list_unresolved_findings()` returns zero. Phantom 5bbc2be2 created a `position_drift` finding that neither absorber could clear (settled-terminal absorber requires 24h post-market-end; operator-external-close absorber requires a prior ack row that doesn't exist for first occurrences). That one unresolved finding kept the latch permanently closed. The correct fix is to either: write an operator-session ack row for token 5bbc2be2 (unlocks immediately), or add a first-occurrence external-close absorber backed by market-calendar evidence without requiring a prior ack. The secondary structural defect (Defect-6) means any other stale finding from any past context could reproduce the freeze independently.

---

## angle:Friction / fees / slippage / threshold accounting.

**Agent ID:** `agent-aff11489a4bfb327e`

### Final Assistant Message

# Friction Accounting: First-Principles Design

## (1) Objective

The angle must ensure that every submitted order has a positive expected value *after* all frictions are subtracted, computed at the moment of order submission using the executable price — not the mid, not a theoretical fair value, not yesterday's close. The system makes money when and only when the sum of (post-cost edge) x (volume) over all filled orders is positive. The objective is therefore a tight, honest filter: suppress every trade where friction consumes the gross edge.

---

## (2) Ideal Mechanism

**The cost stack** — enumerate in order of certainty:

1. **Taker fee**: flat rate f_t on notional. Known exactly pre-trade.
2. **Maker rebate** (if resting): negative cost -f_m. Known exactly; but fill is uncertain.
3. **Spread cost**: if crossing, you pay (ask - mid) on entry and again on exit if you close before settlement. If holding to settlement, exit spread cost = 0 (settlement is binary; no exit needed). One-sided spread cost = (ask - fair_value) for YES buys, (fair_value - bid) for NO buys.
4. **Slippage / market impact**: for a limit-order book, if your size exceeds the best-level depth, you walk the book. Cost = sum over levels consumed of (level_price - best_price) x level_size / total_size. For small size on liquid markets this is near-zero; for illiquid penny bins it can dominate.
5. **Min-size rounding loss**: if the minimum size is S_min and your Kelly-optimal size is s < S_min, you either pass (zero trade) or round up to S_min, which increases variance relative to Kelly. The cost is the variance penalty of over-sizing: a real, non-zero expected-value drag when you are fractionally over-sizing into an edge that barely clears the threshold.
6. **Settlement timing slippage**: none, because settlement is objective and automatic. This cost is zero by design.

**The threshold formula**: Let p be your probability lower bound (conservative, not point estimate) on the YES outcome. The contract pays 1 if YES settles, 0 otherwise. You buy YES at price a (the executable ask). Post-cost EV per unit:

```
EV = p * (1 - a) - (1 - p) * a - f_t - slippage_per_unit
   = p - a - f_t - slippage_per_unit
```

Trade if and only if:

```
p > a + f_t + slippage_per_unit + epsilon
```

where epsilon is a small buffer (1-2 ticks) for adverse selection: the market may move against you in the time between signal computation and fill. For maker orders substitute f_t with -f_m and add a fill-uncertainty penalty (you only fill when someone wants to trade against you — a mild adverse selection signal).

**The penny-bin / longshot problem**: For a bin with fair value near 0 (say, p_fair = 0.03), the NO contract trades near 0.97. Buying NO at 0.97 looks like a 3% gross edge. But: spread on a penny market is often 2-4 ticks wide, fee eats another 0.5-1%, and size is constrained by min-size forcing over-sizing. The after-cost edge on a 3% gross position can be zero or negative. The mechanism must apply the full cost stack — not just fee — before clearing a trade.

---

## (3) Invariants

- **INV-1**: The executable price used in the threshold is the actual ask (or bid for NO), pulled from the live order book at submission time — never mid, never last trade.
- **INV-2**: The probability lower bound is strictly conservative: use the 10th percentile of ensemble spread, not the ensemble mean.
- **INV-3**: Slippage is estimated from observed book depth, not assumed zero. If depth is unobservable, assume worst-case (full size walks to next level).
- **INV-4**: The fee rate used is the *taker* rate unless the system has a confirmed resting-order strategy with fill probability modeled.
- **INV-5**: A trade that clears threshold at order time but whose book has moved by fill time must be re-evaluated or cancelled — stale-price fills are forbidden.

---

## (4) Silent Failure Modes

- **Using mid instead of ask**: systematically understates the cost by half the spread. Generates apparent edge in backtests that evaporates live, especially in wide markets.
- **Ignoring slippage on small-cap bins**: penny bins often have 1-2 contracts at best level. A 10-contract order walks 5 levels. If slippage is hard-coded to zero, every penny-bin trade is mispriced.
- **Conservative probability not conservative enough**: using ensemble mean instead of lower bound means half of all trades are entered with a negative-EV probability. The mean beats the threshold; the realized distribution does not.
- **Fee asymmetry on maker vs taker**: assuming taker fee for all orders inflates costs and suppresses profitable maker orders; assuming maker rebate for all orders understates costs on market orders. Must branch on order type.
- **Min-size over-ride without edge scaling**: rounding up to S_min when s_optimal < S_min amplifies loss when the edge is thin. A trade with 0.1% after-cost edge at optimal size becomes negative-EV when forced to 10x the optimal size. The check must be: does the edge at S_min still clear the threshold?

---

## (5) Three Hardest Design Decisions

**Decision 1 — Where to set epsilon (the adverse-selection buffer)**. Too small: you win the threshold test but fill at a worse price because the market moved in the milliseconds between signal and order. Too large: you suppress genuine edges. Opinionated call: set epsilon to the observed 90th-percentile of price movement between signal generation and fill confirmation, computed from a rolling window of recent fills. This is empirical and self-calibrating, not a fixed constant.

**Decision 2 — How to handle maker orders**. Maker orders earn the rebate but only fill on adverse selection: someone crosses your resting quote when they believe the price will move against you. The expected fill is biased. Opinionated call: do not rest maker orders at all unless the system has a specific, separately-validated model of fill-conditional-on-adverse-selection. The default strategy is taker-only. The rebate is not worth the selection risk without a fill model.

**Decision 3 — The penny-bin / longshot cutoff**. Some bins are so thin that no realistic edge clears after cost. Opinionated call: derive a minimum-tradeable edge per bin dynamically as (spread/2 + fee + slippage_estimate). If the maximum plausible gross edge (from the ensemble) is below this floor, the bin is structurally untradeable and is suppressed before the probability model even runs. This is a per-bin liquidity gate, not a global threshold, and it must be recomputed each day from observed book state — not hardcoded.

---

## agent-affa978d156df361e

**Agent ID:** `agent-affa978d156df361e`

### Final Assistant Message

Synthesis complete. The investigation overturned the framing I was handed.

Decisive finding: the three "hardened" B1 defects are stale — finding `5bbc2be2` self-cleared at `2026-06-14T06:01:47` via the settled-class reconcile path (the R13 refuter was right). Zero unresolved findings now. B1 was never the binding constraint for a *profitable* fill.

The real, live root cause is calibration-licensing starvation that masquerades as no-edge:
- The reactor itself flags cheap +EV longshots (Manila buy_yes ev/$ = +97, Wellington +41) every 60s, then rejects the whole cycle.
- Those candidates die on `coverage_unlicensed_tail`, not `capital_efficiency` — the log conflates the two gates, which is why prior refuters mislabeled it.
- The only path to license a cheap tail q_lcb is unreachable: `state/emos_ci_license.json` does not exist → 0 EMOS overrides ever fire (vs 19,715 K3 shrinks); EMOS stamps `buy_no=0.0`; SETTLEMENT_ISOTONIC only shrinks and needs 30+ settled obs tail bins don't have. All 85 recent receipts are `FORECAST_BOOTSTRAP`, zero ever licensed.

Verdict: TARGETED_FIX, not rebuild. Three-item first cut: (1) fix the lying cycle-summary log [zero risk, gates the rest], (2) re-route `coverage_unlicensed_tail`'s licensing test to the existing settlement-coverage verdict authority [collapses two licensing vocabularies into one], (3) adjudicate the dead buy_no=0.0 stub.

The one decisive unverified check — and the explicit answer to the operator's "would unblocking even produce a profitable fill" question: NO for the current candidate set (the only historically-filled band is >0.6 base-rate buy_no, which is not alpha). Whether any cheap tail bin on a current target date carries a LICENSED settlement verdict is the single test that decides targeted-fix vs calibration-rebuild, and it was not run in this pass — it is rank-2's kill-criteria.

---

## journal

**Agent ID:** `journal`

### Final Assistant Message

[No assistant messages found]

---
