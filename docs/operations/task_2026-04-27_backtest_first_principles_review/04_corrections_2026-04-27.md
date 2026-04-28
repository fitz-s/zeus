# 04 — Corrections to Earlier Claims (2026-04-27)

Created: 2026-04-27 (later in the same audit window as plan.md / 01 / 02 / 03)
Status: audit-trail evidence; corrects errors in plan.md, 01, 02, 03, and `evidence/reality_calibration.md`.
Trigger: operator pushback that "若有这么多 false positive，你之前的假设也许都建立在错误结论". Systematic re-verification of every web-derived claim.

---

## 0. Why this file exists separately

Per Fitz Constraint #2 (translation loss), I MUST encode my errors as durable artifacts so future readers can see what was claimed, what was wrong, and what the verified truth is. Silent overwrite would be a Constraint #2 violation in itself.

This doc is the antibody against the error pattern: **WebSearch summaries are unreliable for quantitative claims**. Even when the search returns the right primary-source URL, the LLM-generated summary may hallucinate specific numbers. **Every quantitative claim must be verified by WebFetch on the primary-source URL itself, AND cross-referenced against at least one secondary source.**

---

## 1. Confirmed corrections (with verbatim sources)

### C1. ECMWF ENS dissemination lag

**False claim (in 01 §5):** "ECMWF's 40-minute dissemination schedule (verified at https://www.ecmwf.int/en/forecasts/datasets/set-iii on 2026-04-27)"

**Source of error:** WebFetch summary on `set-iii` dataset page returned "available approximately 40-41 minutes after the base time". This summary was a **misinterpretation** by the WebFetch summarizer — the actual ECMWF source says ENS dissemination was **moved 40 minutes earlier in 2017**, NOT that the absolute lag is 40 min.

**Verified truth (verbatim from confluence.ecmwf.int/display/DAC/Dissemination+schedule, 2026-04-27):**

| Base time | Day 0 ENS available | Day 1 | Day 15 |
|---|---|---|---|
| **00 UTC** | **06:40 UTC** (+6h40m) | 06:44 UTC | 07:40 UTC |
| **06 UTC** | **12:40 UTC** (+6h40m) | 12:44 UTC | — |
| **12 UTC** | **18:40 UTC** (+6h40m) | 18:44 UTC | 19:40 UTC |
| **18 UTC** | **00:40 UTC** (+6h40m) | 00:44 UTC | — |

**Cross-reference:** ecmwf.int 2017 news article says "clients receiving a 15-day ensemble forecast based on initial conditions at 0000 UTC as early as 0800 UTC" — that's the Day 15 product (longer range adds time). The 2017 article was a delta-of-improvement announcement, not the absolute schedule.

**Correct formula for `DERIVED_FROM_DISSEMINATION` provenance:**

```python
# ECMWF ENS, Day N forecast available at:
#   base_time + 6h40min + (N × 4min)
# (Day 0 = +6h40m, Day 15 = +7h40m, scaling linearly)
def ecmwf_ens_available_at(base_time: datetime, lead_day: int) -> datetime:
    return base_time + timedelta(hours=6, minutes=40 + 4 * lead_day)
```

**Impact on backtest design:**
- The `decision_time_truth.AvailabilityProvenance.DERIVED_FROM_DISSEMINATION` tier in 01 §5 is still valid — the deterministic derivation is real, just with the corrected formula.
- F11 hindsight risk: a forecast row with `forecast_basis_date=2026-04-20` and `lead_days=3` has `available_at = 2026-04-20T00:00 + 6h40m + 12min = 2026-04-20T06:52:00 UTC`. Any backtest using this row for "decision-time truth" earlier than 06:52 UTC on 2026-04-20 commits hindsight leakage.

**Lesson:** I generalized "40 minutes" without realizing the source said "40 minutes earlier". Defensive process: when a number appears in a delta-shaped phrase ("X minutes earlier", "Y% better"), always look for the absolute schedule before quoting.

---

### C2. ECMWF ENS member count

**False claim (in plan.md §1 + various):** "50 perturbed + 1 control + 1 HRES = 52" (Zeus's 51 is "the legacy ENS shape, not current")

**Source of error:** WebFetch summary on `set-iii` dataset page mentioned "52 trajectories" in the **Tropical Cyclone tracks product** (Section III-viii). The summarizer conflated TC tracks (which ADDS HRES + control to 50 perturbed) with the standard ENS forecast.

**Verified truth (verbatim from ecmwf.int/en/forecasts/documentation-and-support/medium-range-forecasts):**
> "ENS is an ensemble of **51 forecasts** comprising **one control forecast (CNTL) plus 50 forecasts** each with slightly altered initial conditions"
> "Our medium-range forecasts consist of **a single forecast (HRES) and our ensemble (ENS)**" — HRES is **separate**, not part of ENS

**Zeus state was always correct:**
- `config/settings.json:32` → `"primary_members": 51` ✓
- `src/signal/ensemble_signal.py:1` → "51 ENS members" ✓
- `src/engine/evaluator.py:886` → "ENS fetch failed or < 51 members" ✓

**`crosscheck_members: 31`** in `settings.json:33` is for a smaller secondary ensemble used for cross-validation. Likely refers to NOAA GEFS (which has 30 perturbed + 1 control = 31 members). Did NOT verify against external — flagged in §3 as still-unverified.

**Impact on backtest design:** zero. Zeus's ENS assumption is correct. **No code changes needed.** My earlier "52 vs 51" framing was a non-issue.

---

### C3. Polymarket US weather market resolution source

**False claim (in plan.md §1 + 02 §3.B + 03 §2.1):** "Polymarket US markets resolution source is NOAA stations, not Wunderground" — implying Zeus's `settlements.settlement_source = wunderground.com URLs` is structurally mismatched.

**Source of error:** First WebSearch result summary on Polymarket weather markets stated "NOAA official station records are the most common resolution source for US temperature markets" — this was a hallucination by the search summarizer. The summarizer probably conflated "WU.com displays NOAA-managed station data" (true: KLGA, KORD, KMIA, KLAX are all NOAA-managed ICAO stations) with "Polymarket reads NOAA directly" (false).

**Verified truth (verbatim from polymarket.com/event/highest-temperature-in-{nyc|chicago|miami|los-angeles}-on-april-27-2026, 2026-04-27):**

| Market | Verbatim resolution rule | Station |
|---|---|---|
| NYC | "Wunderground, specifically the highest temperature recorded for all times on this day by the Forecast for the **LaGuardia Airport Station**" | KLGA |
| Chicago | "Wunderground, specifically the highest temperature recorded ... by the Forecast for the **Chicago O'Hare Intl Airport Station**" | KORD |
| Miami | "Wunderground, specifically the highest temperature recorded ... by the Forecast for the **Miami Intl Airport Station**" | KMIA |
| LA | "Wunderground, specifically the highest temperature recorded ... by the Forecast for the **Los Angeles International Airport Station**" | KLAX |

**Zeus's existing model was correct.** Per [zeus_market_settlement_reference.md:155-162](../../../docs/reference/zeus_market_settlement_reference.md:155):
> "Real temperature → NWP → ASOS sensor → METAR → **Weather Underground display → Polymarket settlement integer**"

**Real (narrower) issues that DO exist** (these are genuine, not retracted):

1. **WU API vs WU website daily summary divergence** (forensic F9, narrow): Zeus reads WU API `max(hourly)`, Polymarket reads WU website daily summary. Both are "WU" but different code paths. ~19 mismatches on SZ/Seoul/SP/KL/Chengdu per [known_gaps.md](../known_gaps.md). NARROW issue.
2. **Taipei period switching** (known_gaps): 03-16~03-22 used CWA → 03-23~04-04 used NOAA Taoyuan → 04-05+ used WU/RCSS Songshan. This IS the only period where Polymarket genuinely used NOAA. Specific to Taipei.
3. **HK 03-13/14**: Polymarket used WU/VHHH (HK Airport), Zeus has HKO Observatory data — still WU, different station.
4. **HKO floor rounding** (known_gaps HK): HK alone uses `oracle_truncate`, not `wmo_half_up`.

**Impact on backtest design:** my claim "Zeus has structural settlement-source mismatch with Polymarket" is RETRACTED. The actual state is "Zeus model matches reality; narrow per-city / per-period exceptions are tracked in known_gaps.md". The blocker_handling_plan §3.B and data_layer_issues §2.6 must be rewritten to reflect this.

---

### C4. Polymarket "no public historical archive API"

**False claim (in 02 §3.B + 03 §2.1):** "No public archive API for orderbook snapshots — bid/ask price history must be captured live via WebSocket or sourced from a third-party archive"

**Source of error:** WebFetch on `docs.polymarket.com/` (homepage) returned "Not documented in this excerpt" for archive endpoints. I generalized that absence-of-evidence to absence-of-archive. WRONG generalization.

**Verified truth (multiple primary sources, 2026-04-27):**

Polymarket has **four** distinct historical-data layers:

1. **Gamma API** (`gamma-api.polymarket.com`) — Zeus already uses this for market discovery per `zeus_market_settlement_reference.md:33`. Provides market metadata, lifecycle events.

2. **Public Subgraph** (open-sourced at `github.com/Polymarket/polymarket-subgraph`). Six sub-subgraphs:
   - `activity-subgraph` — trades and events
   - `fpmm-subgraph` — market maker data
   - `oi-subgraph` — open interest
   - **`orderbook-subgraph`** — order data (KEY for backtest ECONOMICS)
   - `pnl-subgraph` — user positions / P&L
   - `wallet-subgraph` — user data
   - GraphQL via The Graph network: `gateway.thegraph.com/api/{api-key}/subgraphs/id/Bx1W4S7kDVxs9gC3s2G6DS8kdNBJNVhMviCtin2DiBp`

3. **Data API REST** — `GET /trades` for historical trades by market or user (per `docs.polymarket.com/developers/CLOB/trades/trades`).

4. **WebSocket Market Channel** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) — forward-only orderbook snapshots; verified verbatim. PING every 10s; message types `book`, `price_change`, `tick_size_change`, `last_trade_price`, `best_bid_ask`, `new_market`, `market_resolved`.

**What's still unclear (legitimate uncertainty):** whether the `orderbook-subgraph` indexes orderbook **snapshots at arbitrary timestamps** vs only events. The github README describes it as "order data" without specifying snapshot retention. Verifying this requires reading the actual subgraph schema (`schema.graphql`) — DEFERRED to a separate verification slice.

**Impact on backtest design (HUGE):**

- The ECONOMICS purpose is NOT structurally impossible. It is **gated by ingestion code work**, not by absence of upstream data.
- Polymarket subgraph is FREE (via The Graph, you pay only for your queries beyond the free tier).
- The earlier "ECONOMICS may need paid third-party archive" in 02 §3.B is OVERSTATED.
- The economics tombstone in `01_backtest_upgrade_design.md` §3.C still stands UNTIL Zeus has actually ingested from one of these sources, but the ingestion path is now explicit and free.

---

### C5. Polymarket weather market taker fee

**Stale-but-now-corrected claim (in known_gaps.md D3 mitigation):** "5% taker fee assumed in Zeus" — verified CORRECT today.

**My intermediate (also wrong) claim during this audit:** WebSearch summary said "1.25% for Weather markets" — also a hallucination.

**Verified truth (verbatim from docs.polymarket.com/trading/fees, 2026-04-27):**
> "0.05" (5% fee rate for Weather category markets)
> "fee = C × feeRate × p × (1 - p)" where C = shares traded, p = share price
> "25% maker rebate"
> "The smallest fee charged is 0.00001 USDC"

**Confirmation that Zeus's existing 5% assumption is correct.** Zeus's fee model in known_gaps.md D3 (`polymarket_fee(p) = fee_rate × p × (1-p)` with `fee_rate = 0.05`) matches the official spec exactly.

**Impact on backtest design:** the typed `ExecutionPrice` work in `evaluator.py` (per known_gaps.md D3 mitigation) is correct. Zeus is right. No design change.

**Open verification:** "Polymarket Expands Taker Fees to 8 New Market Categories Starting March 30, 2026" (per a recent search result link). Need to verify whether weather fees changed in March 2026, and whether the docs.polymarket.com/trading/fees page reflects post-change rates. Flagged in §3.

---

## 2. Confirmed-correct claims (verified during this audit)

These claims passed verification:

| Claim | Source verification |
|---|---|
| `wu_icao_history` 39,431/39,437 = 99% empty provenance | Direct SQL probe 2026-04-27 ✓ |
| `forecasts.forecast_issue_time` NULL on every row | Direct SQL probe ✓ |
| `market_events*` / `market_price_history` 0 rows | Direct SQL probe ✓ |
| `zeus_trades.db` all tables 0 rows | Direct SQL probe ✓ |
| `oracle_shadow_snapshots`: 480 files, 48 cities × 10 dates | Direct disk ls ✓ |
| `replay.py` is 2382 lines, function index | Direct Read ✓ |
| Zeus `BACKTEST_AUTHORITY_SCOPE = "diagnostic_non_promotion"` | Direct grep ✓ |
| Zeus assumes 51 ENS members | Direct grep settings.json:32 ✓ |
| Polymarket weather taker fee = 5% | docs.polymarket.com/trading/fees verbatim ✓ |
| Polymarket fee formula `C × fee_rate × p × (1-p)` | docs.polymarket.com verbatim ✓ |
| Polymarket WebSocket URL `wss://ws-subscriptions-clob.polymarket.com/ws/market` | docs.polymarket.com/market-data/websocket/overview verbatim ✓ |
| Polymarket WebSocket message types | docs.polymarket.com verbatim ✓ |
| Polymarket subgraph repo + 6 sub-subgraphs | github.com/Polymarket/polymarket-subgraph README verbatim ✓ |
| ECMWF ENS members = 51 (50 perturbed + 1 control), HRES separate | ecmwf.int/en/forecasts/documentation-and-support/medium-range-forecasts verbatim ✓ |
| ECMWF Day 0 ENS dissemination = base + 6h40min | confluence.ecmwf.int/display/DAC/Dissemination+schedule verbatim ✓ |
| Polymarket weather US markets use Wunderground (4 cities verified) | polymarket.com/event/* verbatim ✓ |

---

## 3. Verification status (updated 2026-04-28)

Originally flagged as still-unverified; subsequent probes have resolved several:

| # | Claim | Status | Evidence |
|---|---|---|---|
| U1 | Zeus's `crosscheck_members: 31` corresponds to NOAA GEFS (30 perturbed + 1 control) | **PARTIAL** | `evaluator.py:1192` consumes `ensemble_crosscheck_member_count()` as `expected_members=...` for Day0 ensemble verification. The number 31 matches NOAA GEFS canonical shape (30 perturbed + 1 control); no other major NWP ensemble has this count. Full data-source trace (which feed populates the crosscheck path) deferred — not load-bearing for backtest design. |
| U2 | Polymarket has 361 live temperature markets right now | **VERIFIED-WITH-CONTEXT** | polymarket.com/weather/temperature 2026-04-28 shows ~60-70 daily-temperature events. Each event has multiple bin markets (typically 6-10), so 60-70 × 6-10 = 360-700 bin markets ≈ "361 markets" reported earlier. Unit confusion (events vs markets), not a factual error. |
| U3 | Polymarket fee structure changed materially in March/April 2026 | **STILL UNVERIFIED** | `docs.polymarket.com/trading/fees` returns "0.05" (5%) for Weather as of 2026-04-28 verbatim. Whether this changed from a different value in March 2026 is unverified; not load-bearing because Zeus's existing 5% assumption matches current truth. |
| U4 | `orderbook-subgraph` indexes orderbook snapshots at arbitrary timestamps (vs only events) | **STILL UNVERIFIED** | `github.com/Polymarket/polymarket-subgraph` README confirms orderbook-subgraph exists; schema.graphql contents not read. Affects whether ECONOMICS purpose can backfill orderbook history vs only forward-WebSocket capture. |
| U5 | All 5 forecast sources have known dissemination schedules | **PARTIAL — RESOLVED** | F11.1 slice (commit 14d87ae 2026-04-28) registers all 5 sources with verified ECMWF (confluence wiki) + verified GFS (NCEP production status); ICON/UKMO/OpenMeteo carry RECONSTRUCTED tier until primary-source schedule captured. See [src/data/dissemination_schedules.py](../../../src/data/dissemination_schedules.py). |
| U6 | Polymarket Data API REST `/trades` is publicly queryable without auth | **RETRACTED — AUTH REQUIRED** | `curl -s -o /dev/null -w "%{http_code}" "https://clob.polymarket.com/data/trades?limit=1"` returns **HTTP 401** on 2026-04-28. Data API REST `/trades` requires authenticated access; not anonymously queryable. Subgraph (via The Graph) remains the unauthenticated path. |
| U7 | TIGGE archive on the cloud VM matches the standard ENS 51-member shape | **VERIFIED** | gcloud SSH probe to tigge-runner 2026-04-27 — actual JSON files have `member_count: 51` and `members: list len=51` (member 0 = control + members 1-50 perturbed). See [evidence/vm_probe_2026-04-27.md](evidence/vm_probe_2026-04-27.md) §5. |

---

## 4. Methodology lessons (encoded as future-self instructions)

### L-A. WebSearch summaries are unreliable for quantitative claims

The summarizer often paraphrases or fabricates specific numbers. NEVER quote a number from a WebSearch summary without WebFetch on the primary-source URL.

### L-B. WebFetch summaries can also misinterpret

Even WebFetch on the right URL can return wrong numbers if the page uses delta-shaped phrases ("40 minutes earlier", "X% better"). When in doubt, fetch the source table or schedule, not the news article.

### L-C. Cross-reference at least two primary sources

For any load-bearing claim:
1. WebFetch on the official documentation URL.
2. WebFetch on a second primary source (project README, technical spec, official FAQ).
3. If they disagree, find the canonical authority (e.g., for ECMWF, the Confluence wiki dissemination schedule beats news articles).

### L-D. Disk-grounded probes are high-trust; web summaries are low-trust

In the upgrade design, the disk-grounded findings (row counts, code structure, replay.py line numbers) survived this audit unchanged. The web-derived findings (5 of them) all required correction. **Anchor designs in disk truth; treat web facts as hypotheses until WebFetch-verified.**

### L-E. Encode false positives as antibodies

This corrections doc IS the antibody. If the same error pattern recurs in a future session, the future agent can look at this doc, see the failure mode, and not repeat it. Constraint #3 (immune system > security guard) — the antibody is a typed/structured artifact, not a memory note.

---

## 5. Files affected by these corrections

| File | Sections needing edit |
|---|---|
| [plan.md](plan.md) | §1 reality calibration table (3 rows: Polymarket source, ENS members, dissemination lag) |
| [01_backtest_upgrade_design.md](01_backtest_upgrade_design.md) | §5 D4 antibody (correct lag formula); footnote on ENS member count |
| [02_blocker_handling_plan.md](02_blocker_handling_plan.md) | §3.B (Polymarket data sources are richer than claimed); §3.C (ECMWF lag formula correction) |
| [03_data_layer_issues.md](03_data_layer_issues.md) | §2.1 (data sources); §2.3 (dissemination lag) |
| [evidence/reality_calibration.md](evidence/reality_calibration.md) | replace incorrect rows; add correction pointer |

After this corrections doc is reviewed, those edits land in a follow-up commit (single commit, scoped to this packet folder per memory L24).

---

## 6. Net impact on backtest upgrade design

After all corrections:

- **Purpose-split (D1)** — UNCHANGED. Sound design.
- **Sentinel sizing (D3)** — UNCHANGED. Sound design.
- **Decision-time provenance typing (D4)** — UNCHANGED in shape; the lag formula moves from `+40min` to `+6h40min × leadDay-scaled`. Cite confluence wiki, not the 2017 news article.
- **Economics tombstone (01 §3.C)** — UNCHANGED in shape but **the unblock path is materially clearer**: Polymarket's own subgraph + Data API + Gamma API are sufficient (no third-party paid archive needed for trades; orderbook snapshot retention pending U4 verification).
- **Settlement-source mismatch fear (03 §2.6)** — RETRACTED for general US case. Narrow exceptions remain (Taipei periods, HK 03-13/14, WU API vs website divergence). These are already in `known_gaps.md` and need no special backtest treatment beyond what's already logged.
- **Polymarket fee model** — Zeus's existing 5% assumption is CORRECT. Aligns with docs.polymarket.com/trading/fees.

**The structural design is robust to these corrections.** The blockers shrink:
- ECONOMICS unblock path: clearer (Polymarket subgraph available)
- F11 antibody formula: more precise (6h40m, not 40m)
- US-source mismatch panic: retracted

This is a cleaner picture than 02/03 originally painted. The next implementation packets should base their preflight checks on this corrected reality.
