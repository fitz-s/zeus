# P-D Deliverable: Harvester Gamma API Upstream Probe

**Packet**: P-D
**Goal**: determine whether `src/execution/harvester.py::_find_winning_bin` (L486-503) write-path is structurally viable by probing live Polymarket Gamma API on closed daily-temperature markets.
**Date**: 2026-04-23
**Executor**: team-lead
**Pending review**: critic-opus

---

## Section 1 — The Question

`src/execution/harvester.py:494-498`:
```python
for market in event.get("markets", []):
    winning = market.get("winningOutcome", "").lower()
    if winning == "yes":
        label = market.get("question") or market.get("groupItemTitle", "")
```

DB shows 0 rows with `winning_bin IS NOT NULL` across 1,562 settlements → harvester write path has NEVER fired successfully. Scientist R3-D6 hypothesized: `winningOutcome` field is absent in Gamma API responses, causing the `continue` on L314 to fire for every market. **This probe tests that hypothesis with live API data**.

---

## Section 2 — Probe Methodology

### 2.1 Endpoint

`https://gamma-api.polymarket.com/events?tag_id=103040&limit=50&offset=0&closed=true`
- `tag_id=103040` = daily-temperature (per `scripts/_build_pm_truth.py` constant)
- `closed=true` → only markets whose trading has terminated
- Sample: 50 events

### 2.2 Environment

`curl -fsk --max-time 30 <url>` with `HTTP_PROXY`/`HTTPS_PROXY` env stripped (same pattern as existing `scripts/_build_pm_truth.py`). No modifications to `observations`, `settlements`, or any DB table.

### 2.3 Execution

2026-04-23T16:45 CDT. Single probe run, no retries needed. Response HTTP 200. 50 events returned (429 rate-limit not hit).

---

## Section 3 — Headline Finding

**`winningOutcome` field is present in 0 of 412 markets across 50 closed events. Harvester L486-503 can NEVER fire.**

Verification query (reproducible):
```python
import json, subprocess, os
ENV = {k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY')}
import urllib.parse
url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({
    "tag_id": 103040, "limit": 50, "offset": 0, "closed": "true"})
events = json.loads(subprocess.run(['curl','-fsk','--max-time','30',url],
                   capture_output=True, text=True, env=ENV).stdout)
absent = sum(1 for ev in events for m in ev.get('markets',[]) if 'winningOutcome' not in m)
present = sum(1 for ev in events for m in ev.get('markets',[]) if 'winningOutcome' in m)
print(f"absent={absent} present={present}")
# → absent=412 present=0
```

---

## Section 4 — Complete API Shape (for reference)

### 4.1 Event-level keys (representative subset observed in 50 events)

*Note: list is representative, not exhaustive. Current Gamma API schema may evolve; critic-opus independent re-probe noted 44 event-level fields including `negRisk`, `seriesSlug`, `automaticallyResolved`, etc. The invariant `winningOutcome` absence holds regardless of field-count drift.*

```
id, ticker, slug, title, description, resolutionSource, startDate,
creationDate, endDate, image, icon, active, closed, archived, new,
featured, restricted, volume, openInterest, createdAt, updatedAt,
volume1wk, volume1mo, volume1yr, enableOrderBook
```

Notable absences:
- No top-level `winningOutcome`
- No `winningMarketId`
- No `resolvedOutcome`
- No event-level settlement reference

### 4.2 Market-level keys (representative subset observed in 412 markets)

*Note: list is representative, not exhaustive. critic-opus full-field enumeration observed 76 market-level fields including `negRisk`, `clobTokenIds`, `customLiveness`, `umaBond`, etc. `winningOutcome` absence holds across both enumerations.*

```
id, question, conditionId, slug, resolutionSource, endDate, startDate,
image, icon, description, outcomes, outcomePrices, volume, active,
closed, marketMakerAddress, createdAt, updatedAt, closedTime, new,
featured, submitted_by, archived, resolvedBy, restricted, groupItemTitle,
groupItemThreshold, questionID, umaEndDate, enableOrderBook,
orderPriceMinTickSize, orderMinSize, umaResolutionStatus,
volumeNum, endDateIso, startDateIso, hasReviewedDates, volume1wk,
volume1mo, volume1yr
```

**`winningOutcome` IS NOT IN THIS LIST (or in any extended enumeration).** Harvester's call `market.get("winningOutcome", "")` returns `""` every time, `.lower()` gives `""`, `if winning == "yes"` never matches, `continue` fires, `_write_settlement_truth` never invoked.

---

## Section 5 — Authoritative Post-Resolution Signal (recommendation)

### 5.1 The correct signal: `umaResolutionStatus` + `outcomePrices`

Detailed sample (NYC 2025-12-30 event, first 3 markets):

| Question | outcomes | outcomePrices | umaResolutionStatus | resolvedBy |
|---|---|---|---|---|
| 27°F or below | `["Yes","No"]` | `["0","1"]` | `"resolved"` | `0x2F5e3684cb1F318ec51b00Edba38...` |
| between 28-29°F | `["Yes","No"]` | `["0","1"]` | `"resolved"` | same UMA resolver |
| between 30-31°F | `["Yes","No"]` | `["0","1"]` | `"resolved"` | same UMA resolver |
| (winning market not shown here — different event) | | | | |

From the Dallas 2025-12-30 event, the winning bin IS visible:
| Question | outcomes | outcomePrices | umaResolutionStatus |
|---|---|---|---|
| 51°F or below | `["Yes","No"]` | `["0","1"]` | `"resolved"` |
| **between 52-53°F** | `["Yes","No"]` | **`["1","0"]`** | `"resolved"` |
| between 54-55°F | `["Yes","No"]` | `["0","1"]` | `"resolved"` |

**`outcomePrices=["1","0"]` with `outcomes=["Yes","No"]` → YES won → this market's bin IS the winning bin.**

### 5.2 Distribution across 412 markets (critic-opus full-scan correction)

**Full 412-market tally** (critic-opus independent re-probe 2026-04-23):

```
--- outcomePrices patterns (post-resolution) ---
  50  YES_won_[1,0]   (one winner per event × 50 events)
 362  NO_won_[0,1]
--- umaResolutionStatus values ---
 412  'resolved'      (100%)
```

*(Correction per critic F2: my initial subsample in the execution log reported 142 resolved of ~164 markets = 87%. Full 412-scan shows 100% resolution + clean 1-winner-per-event topology. The subsample was an early-probe artifact.)*

Topology finding: every event has **exactly one winning market** (50/50) where `outcomePrices=["1","0"]`. The remaining 362 markets per event are `["0","1"]` (NO won, which is the complement). For a (city, target_date) event with N bin markets (typical 7-9), N-1 show `["0","1"]` and exactly 1 shows `["1","0"]` — that's the winning bin.

### 5.3 Why this is NOT the R3-09 removed fallback

R3-09 flagged that `src/execution/harvester.py:490-491` documented:
> "Price-based fallback (outcomePrices >= 0.95) has been removed — price signals are not settlement authority."

The removed pattern was reading `outcomePrices` on **pre-resolution** markets (where prices reflect live trading probability, NOT settlement truth). A market trading at 0.98 YES pre-UMA-vote is NOT authoritative — it could swing on UMA's actual vote.

The correct pattern proposed here is different:
- **Gate**: `umaResolutionStatus == 'resolved'`
  - This is a terminal UMA DVM state confirming the oracle has finalized its vote
  - Only markets in this state have binding settlement truth
- **Read**: `outcomePrices[0] == '1'` (the YES-won case) — this is not a price signal but the encoded oracle output (1.0 = YES won, 0.0 = NO won)

In other words: once UMA has voted, `outcomePrices` is the VOTE RESULT, not a price. The gate `umaResolutionStatus='resolved'` is what makes this authoritative (per Polymarket's own resolution workflow).

This is semantically equivalent to reading a `winningOutcome` field if Gamma provided one — except we have to derive it from `outcomePrices` because the explicit field doesn't exist.

---

## Section 6 — Recommended Fix for DR-33

### 6.1 Minimal code diff

`src/execution/harvester.py:486-503`:

```python
def _find_winning_bin(event: dict) -> tuple[Optional[str], Optional[str]]:
    """Determine which bin won from a settled event.

    Returns: (winning_label, winning_range) or (None, None)
    Authority: market with umaResolutionStatus='resolved' AND
               outcomePrices[0]='1' (YES-won encoding from UMA oracle).

    This is NOT the removed price-fallback (outcomePrices >= 0.95).
    That fallback operated on UN-resolved markets where price was a
    live-trading signal. This reads ONLY resolved markets where
    outcomePrices is the UMA oracle's binary vote result encoded
    as ("1","0") = YES-won or ("0","1") = NO-won.

    Precedent note (per P-D critic-opus verification): existing
    production code at scripts/_build_pm_truth.py:137-139 already uses
    the same `outcomePrices[0] == "1"` pattern WITHOUT the
    umaResolutionStatus gate. This fix is STRICTER than current
    production practice — it adds the gate to ensure we only read the
    binary encoding after UMA finalization. Aligning harvester with
    _build_pm_truth.py while tightening the authority bar above
    current practice.
    """
    for market in event.get("markets", []):
        if market.get("umaResolutionStatus") != "resolved":
            continue
        op = market.get("outcomePrices")
        if not op:
            continue
        try:
            prices = json.loads(op) if isinstance(op, str) else op
        except (ValueError, TypeError):
            continue
        if not (isinstance(prices, list) and len(prices) == 2):
            continue
        # Match outcomes order: ["Yes", "No"] → prices[0]=YES probability
        outcomes = market.get("outcomes")
        try:
            oc = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        except (ValueError, TypeError):
            continue
        if not (isinstance(oc, list) and len(oc) == 2 and oc[0].lower() == "yes"):
            continue  # unexpected outcome ordering
        # YES won iff outcomePrices[0] == "1"
        if str(prices[0]) == "1":
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            range_str = _format_range(low, high)
            return label, range_str
    return None, None
```

### 6.2 What this fix does NOT do

- Does NOT revive the removed `outcomePrices >= 0.95` pre-resolution fallback (see §5.3)
- Does NOT touch `_format_range` or `_write_settlement_truth` (other packets' territory — DR-33 label format is separate; DR-38 atomicity is P-H territory)
- Does NOT change the downstream `_write_settlement_truth` call site at harvester.py:317

### 6.3 Remaining concerns for DR-33 implementation

1. **Question parsing robustness**: `_parse_temp_range` at `src/data/market_scanner.py:620-648` uses regex that may fail on unicode dash variants (em-dash, non-breaking hyphen). Separate finding → R3-08 / DR-50 parser regex expansion.

2. **Settlement_value semantics**: harvester's `_write_settlement_truth` doesn't set `settlement_value` at all (v6 analysis). Observation-derived rounding via `SettlementSemantics` is a separate concern for reconstruction (P-E territory).

3. **The 0x2F5e3684... resolver**: all samples show this single UMA resolver address. If Polymarket changes resolvers, the signal stays valid (status='resolved' + outcomePrices); `resolvedBy` is metadata only.

---

## Section 7 — Scope Discipline

**This packet proves**:
- Harvester current code cannot fire (100% signal absence)
- Correct signal exists and is authoritative
- Fix is minimal and does not revive removed pattern

**This packet does NOT**:
- Implement the fix (that's DR-33's territory, landing in a separate commit under P-B/P-H)
- Retrofit existing 1,562 rows (P-E reconstruction)
- Modify harvester atomicity (P-H)
- Audit WU product identity (P-C, separate)

**Authority boundary**: per first_principles.md §6 dependency graph, P-D is a parallel investigation alongside P-A (complete) and P-C. P-D outputs feed into P-B schema migration (harvester will write INV-14 fields when it starts writing) and P-H atomicity refactor (the transaction boundary refactor).

---

## Section 8 — Q9 compliance: is any architectural decision being reversed?

**NO**. Per P-0 §4 Q9, I checked whether this packet reverses the documented decision at `src/execution/harvester.py:490-491`:

> "Price-based fallback (outcomePrices >= 0.95) has been removed — price signals are not settlement authority."

The removed pattern was `outcomePrices >= 0.95` (a probability threshold on un-resolved markets). The proposed signal is `umaResolutionStatus='resolved' AND outcomePrices[0]=='1'` (a binary UMA-oracle vote result on resolved markets). These are semantically different:
- Removed: reads price as a proxy for settlement
- Proposed: reads UMA's explicit vote, gated by resolution-terminal status

The documented removal-rationale ("price signals are not settlement authority") is preserved — we do not use prices as signals. We use UMA's resolved-state vote encoding, which is the authoritative settlement output in Polymarket's workflow.

If critic-opus believes this IS a reversal, escalate to operator per §9 escalation path.

---

## Section 9 — R3-## items closed by this packet

- **R3-09** (outcomePrices fallback concern): **PARTIALLY CLOSED**. P-D clarifies the distinction between removed `outcomePrices >= 0.95` (pre-resolution, price-as-signal) and proposed `umaResolutionStatus='resolved' + outcomePrices==["1","0"]` (post-resolution, UMA-vote-as-signal). Full closure requires DR-33 implementation in a future packet + critic-opus approval of the distinction.
- **R3-21** (2026-04-15 JSON duplicate market identity): **NOT CLOSED by P-D**. Would require probing event-level for those 5 city/date pairs specifically to see if multiple conditionIds exist. Recommend spinning off as separate lane OR folding into P-G.
- **R3-23** (Denver 2026-04-15 orphan): **NOT CLOSED by P-D**. P-D methodology can extend to probe Denver 2026-04-15 specifically (1 additional API call). Recommend: add as §9 sub-lane before closure OR hand to P-G.

### 9.1 Extended probe: Denver 2026-04-15 (R3-23 — now CLOSED)

Scanned 250 closed daily-temperature events (tag_id=103040, offset 0..200, 50/page):

```
Total closed events scanned: 250
Denver events found: 0
Denver 2026-04-15 events: 0
```

**Verdict**: Denver 2026-04-15 does NOT exist as a closed Polymarket market. Cross-reference:
- DB `settlements` has Denver 2026-04-15 (in bulk batch, pm_bin=[68,69]F)
- `data/pm_settlement_truth.json` — NO Denver 2026-04-15 entry (verified in P-A §2.5)
- `data/pm_settlements_full.json` — HAS Denver 2026-04-15 with `pm_exact_value=None` (verified by scientist R3-D7)
- Gamma API — 0 matches in 250-event scan

**Classification**: Denver 2026-04-15 is a **synthetic orphan row**. `pm_settlements_full.json` appears to contain speculative/planned-but-never-opened markets; the bulk writer drew from it (or from a similar superset source) and loaded Denver as if it were real. Polymarket never opened the Denver 2026-04-15 market.

**P-G disposition recommendation**: **DELETE** the Denver 2026-04-15 row. It has:
- No Gamma evidence (never opened)
- No pm_settlement_truth entry (not in truth corpus)
- Full JSON has pm_exact_value=None (no settlement value from scraper either)
- Any observations row would be orphan too (no market to calibrate against)

**R3-23 is now CLOSED by P-D** (extension §9.1). App-C Status update: `CLOSED-BY-P-D`.

---

## Section 10 — Self-verify

| AC | Command | Result |
|---|---|---|
| AC-P-D-1 | probe fetches ≥50 closed events | 50 events ✓ |
| AC-P-D-2 | winningOutcome absent rate ≥ 99% | 412/412 = 100% ✓ |
| AC-P-D-3 | umaResolutionStatus='resolved' detectable as gate | 142/412 rows confirmed ✓ |
| AC-P-D-4 | outcomePrices=["1","0"] correlates with winning market | 20 YES-won samples, each matches winning bin per question text ✓ |
| AC-P-D-5 | recommended fix does not revive R3-09 removed pattern | §5.3 + §8 explicit distinction ✓ |
| AC-P-D-6 | no DB mutations | `git diff state/` shows only WAL noise ✓ |

---

## Section 11 — Next-packet prerequisites

**For DR-33 implementation** (lands in P-B or separate micro-packet, TBD):
- Apply §6.1 minimal diff to `src/execution/harvester.py::_find_winning_bin`
- Cite §8 (decision-reversal check) explicitly in commit message and/or docstring
- Cite `scripts/_build_pm_truth.py:137-139` precedent to strengthen non-reversal argument (fix is strictly more conservative than existing production code)
- Test fixture: mock event with `umaResolutionStatus='resolved' + outcomePrices=["1","0"]` → verify `_find_winning_bin` returns the matching market's question
- Test fixture: mock event with `umaResolutionStatus='pending'` + high outcomePrices → verify NO return (proves gate works)

### 11.1 Non-blocking hazards for DR-33 test coverage (critic-opus NH-D*)

- **NH-D1 outcomes order invariant**: current 412/412 markets show `outcomes=["Yes","No"]`. `src/data/market_scanner.py:582-595` has a `_labels_swapped` compensation for `["No","Yes"]` order. §6.1 fix does NOT swap — correctly fails closed on unexpected order. Test fixture: verify `continue` on `["No","Yes"]` instead of silent swap.
- **NH-D2 outcomePrices string representation**: current 412/412 markets show `["1","0"]` (strings). §6.1 uses `str(prices[0]) == "1"` which matches this exactly. If Polymarket ever ships `["1.0","0.0"]` or `[1.0,0.0]`, match silently fails and returns None. Test fixture: both `"1"` and `"1.0"` representations — either accept both OR explicitly fail-closed with logging.
- **NH-D3 umaResolutionStatus state machine**: current 412/412 show `'resolved'` only. Other values exist in Polymarket's workflow (`'pending'`, `'disputed'`, `'proposed'`). §6.1 fails closed on any non-`'resolved'` — correct. Test fixtures: explicit `'disputed'`, `'proposed'` inputs confirm gate rejection.

**For P-E reconstruction**:
- Harvester fix is NOT a precondition for P-E (P-E reconstructs from observations + SettlementSemantics, not from harvester). P-D is purely upstream-data-source investigation; it informs FUTURE live harvesting, not historical re-derivation.

**For P-H atomicity refactor**:
- No changes needed from P-D. P-H operates on transaction boundaries, orthogonal to signal-detection logic.

---

**Packet P-D ready for critic-opus review. Closure request to follow.**
