# CLAUDE.md — Zeus

## What This Is

Zeus is a Polymarket weather market trading system. It exploits structural market microstructure inefficiencies (favorite-longshot bias, opening price inertia, bin boundary discretization), NOT weather forecasting superiority.

## Design Authority

**Read these BEFORE writing any code. Every design decision must trace to one of them.**

| Document | Location | Domain |
|----------|----------|--------|
| Quantitative Research | `~/.openclaw/project level docs/rainstorm_quantitative_research.md` | Calibration math, Kelly, sample sizes, overfitting |
| Architecture Blueprint | `~/.openclaw/project level docs/rainstorm_architecture_blueprint.md` | Risk guard, cost layering, failure modes |
| Market Microstructure | `~/.openclaw/project level docs/rainstorm_market_microstructure.md` | Edge thesis, participant types, entry timing |
| Statistical Methodology | `~/.openclaw/project level docs/rainstorm_statistical_methodology.md` | Three σ, instrument noise, FDR, data versioning |
| **Zeus Spec** | `~/.openclaw/project level docs/ZEUS_SPEC.md` | Complete system specification |
| **Implementation Plan** | `~/.openclaw/project level docs/ZEUS_IMPLEMENTATION_PLAN.md` | Build order, session management, what to copy vs write |

If your implementation contradicts a research doc, you MUST document WHY in a code comment with a citation.

---

## Types

### Temperature
All temperatures are `float` internally, in the city's settlement unit.
- US cities: °F (float)
- European cities: °C (float)
- NEVER: bare `int`. Rounding to integer happens ONLY in `EnsembleSignal.p_raw_vector()` during WU settlement simulation.

### Probability
- `p_raw`: `np.ndarray`, shape `(n_bins,)`, sums to 1.0. Unnormalized is a bug.
- `p_cal`: `np.ndarray`, shape `(n_bins,)`, sums to 1.0 AFTER normalization.
- `p_market`: `np.ndarray`, shape `(n_bins,)`. Sum is the vig (typically 0.95-1.05).
- `p_posterior`: `np.ndarray`, shape `(n_bins,)`, sums to 1.0.
- All probabilities are `float` in [0.0, 1.0]. Values outside this range are bugs.

### Money
- All USD amounts are `float`. No cents rounding until display.
- `entry_price`: cost per share, [0.01, 0.99].
- `size_usd`: total dollar amount of order.
- `pnl`: signed float. Positive = profit.

### Time
- All timestamps are UTC `datetime` objects internally.
- Convert to local time ONLY for Wunderground day boundary calculation.
- Use `zoneinfo.ZoneInfo`, not `pytz`.

---

## Module Interfaces (quick reference — do NOT re-read source files for this)

```
EnsembleSignal(members_hourly, city, target_date)
  .p_raw_vector(bins, n_mc=5000) → np.ndarray (n_bins,)
  .spread() → float
  .is_bimodal() → bool
  .boundary_sensitivity(boundary) → float
  .member_maxes → np.ndarray (51,)

ExtendedPlattCalibrator()
  .fit(p_raw, lead_days, outcomes, n_bootstrap=200)
  .predict(p_raw, lead_days) → float
  .bootstrap_params → list[tuple[float, float, float]]  # (A, B, C)
  .n_samples → int

calibrate_and_normalize(p_raw_vector, calibrator, lead_days) → np.ndarray

compute_alpha(calibration_level, ensemble_spread, model_agreement,
              lead_days, hours_since_open) → float

MarketAnalysis(ens, bins, calibrator, alpha)
  .find_edges(n_bootstrap=500) → list[BinEdge]
  .p_raw, .p_cal, .p_market, .p_posterior → np.ndarray
  .vig → float

kelly_size(p_posterior, entry_price, bankroll, kelly_mult) → float

vwmp(best_bid, best_ask, bid_size, ask_size) → float
```

---

## Calibration Levels

| Level | Condition | Platt Regularization | Edge Threshold × | α |
|-------|-----------|---------------------|-------------------|-----|
| 1 | n ≥ 150 | Standard (C=1.0) | 1× | 0.65 |
| 2 | 50 ≤ n < 150 | Standard (C=1.0) | 1.5× | 0.55 |
| 3 | 15 ≤ n < 50 | Strong (C=0.1) | 2× | 0.40 |
| 4 | n < 15 | No Platt (P_raw) | 3× | 0.25 |

---

## Attribution Fields (MANDATORY on every trade_decision)

```sql
edge_source TEXT,         -- 'favorite_longshot' | 'opening_inertia' | 'boundary' | 'vig_exploit' | 'mixed'
bin_type TEXT,             -- 'shoulder_low' | 'shoulder_high' | 'center' | 'adjacent_boundary'
discovery_mode TEXT,       -- 'opening_hunt' | 'update_reaction' | 'day0_capture'
market_hours_open REAL,    -- hours since market opened when entry placed
fill_quality REAL,         -- (execution_price - vwmp) / vwmp — positive = worse than expected
```

These are NOT optional metadata. They are the only way to know WHY the system makes or loses money. The harvester aggregates P&L by these dimensions weekly to answer:
- Which edge source is actually profitable?
- Which direction (buy_yes vs buy_no) contributes more?
- Is Opening Hunt better than Update Reaction?
- Which cities are losing money?

---

## Failure Taxonomy

Every system anomaly maps to exactly one failure type with a pre-defined response:

| Failure Type | Trigger | Response |
|-------------|---------|----------|
| `DATA_STALE` | ENS data > 6h old | YELLOW: skip new entries this cycle |
| `DATA_CORRUPT` | Members ≠ 51 or temp outside physical range | Skip market entirely |
| `MODEL_DRIFT` | Hosmer-Lemeshow χ² > 7.81 on last 50 pairs | YELLOW + force Platt refit |
| `MARKET_STRUCTURE` | Vig > 1.08 or < 0.92 sustained | Flag for vig arbitrage or skip |
| `EXECUTION_DECAY` | Fill rate < 30% for 7 consecutive days | Alert: adjust limit offset |
| `EDGE_COMPRESSION` | Same edge type's avg magnitude shrinks 50% over 30 days | Alert: this edge may be dying |
| `REGIME_CHANGE` | ENSO state transition or SSW detected | YELLOW: raise edge threshold 2× |
| `COMPETITION` | Shoulder bin avg overpricing ratio drops from 3× to < 1.5× | Alert: market efficiency increasing |

RiskGuard checks for these. Each maps to GREEN/YELLOW/ORANGE/RED per spec §7.3.

---

## Critical Rules

### Data Integrity (ABSOLUTE)
- **NEVER fabricate, synthesize, or generate fake data.**
- **NEVER use data without `available_at <= decision_time` constraint.**
- **Four timestamps mandatory** on every forecast: `issue_time`, `valid_time`, `available_at`, `fetch_time`.
- **Settlement truth = Polymarket settlement result.**

### Statistical Discipline
- **Three σ are distinct:** σ_ensemble, σ_parameter, σ_instrument. All three flow into double bootstrap CI.
- **FDR control:** p-values from `np.mean(edges <= 0)`, NEVER approximated.
- **No per-position stop loss.** Only EDGE_REVERSAL triggers exit.
- **24 calibration buckets** (cluster × season). Lead time is Platt input feature, NOT bucket dimension.

### Execution
- **Limit orders ONLY.** Timeout varies by mode: Opening Hunt 4h, Update Reaction 1h, Day0 15min.
- **VWMP** for all edge calculations. Never mid-price.
- **Whale toxicity detection:** Cancel orders on adjacent bin sweeps.

### Architecture
- Standalone Python daemon (launchd). NOT OpenClaw multi-agent.
- RiskGuard is separate process with own SQLite DB.
- Single config file. No `.get(key, FALLBACK)` pattern.

---

## Error Handling

### API Failures
- Open-Meteo: retry 3× with 10s backoff. If failing, skip market this cycle. Do NOT use stale ENS.
- Polymarket CLOB: retry 2× with 5s backoff. If failing, cancel pending orders and wait.
- Wunderground: retry 3×. If failing, flag Day0 as unavailable.

### Data Validation
- ENS response < 51 members: **reject entirely**. Do not pad.
- P_raw vector sum ≠ 1.0 (±0.001): normalize + log warning.
- VWMP with total size = 0: fall back to mid-price + log.
- Platt predict() output outside [0.001, 0.999]: clamp + log.

### Never Silently Fail
Every function that can fail must either raise an exception or return `Optional[T]`. NEVER `return 0.0` on error. This is how Rainstorm died.

---

## Continuous Self-Review Protocol

**After completing EVERY .py file, you MUST run a Sonnet subagent to review it.** This is not optional. Do not skip this step even if you are confident the code is correct.

### The Review Cycle

```
WRITE code → WRITE test → RUN test (Sonnet subagent) → REVIEW code (Sonnet subagent) → FIX issues → NEXT file
```

### Review Subagent Template

After writing each file, spawn a Sonnet subagent with this exact prompt pattern:

```
Review zeus/src/{module}.py against the following checklist. Return PASS or FAIL for each item with a one-line explanation. Do NOT suggest style improvements — only flag correctness and contract violations.

Checklist:
1. TYPES: Do all function signatures match the interfaces in CLAUDE.md "Module Interfaces"?
2. UNITS: Is temperature handling unit-safe? Any bare int where float is required? Any °F/°C mixing?
3. FALLBACKS: Are there any `.get(key, HARDCODED_DEFAULT)` patterns? (forbidden)
4. SILENT FAIL: Are there any bare `except: pass` or `return 0.0` on error? (forbidden)
5. TIMESTAMPS: Do all DB writes include all 4 timestamps? (issue_time, valid_time, available_at, fetch_time)
6. NORMALIZATION: After any Platt calibration, is the probability vector re-normalized to sum=1.0?
7. P_VALUE: Is any p-value computed via approximation formula instead of np.mean(edges <= 0)? (forbidden)
8. IMPORTS: Are there any imports from rainstorm/ or references to v1 code? (forbidden)
9. FILE SIZE: Is the file > 250 lines (excluding comments/blanks)? If so, suggest a split point.
10. SPEC TRACE: Can each major function be traced to a specific spec section? List the mapping.
```

### When Review Finds Issues

- **FAIL on items 1-8:** Fix immediately before moving to next file.
- **FAIL on item 9:** Split the file, then re-review both halves.
- **FAIL on item 10:** Add a `# Spec §X.Y` comment to the function.

### Weekly Full-Codebase Review

At the end of each week (or every 3 sessions), run a comprehensive review:

```
Spawn Sonnet subagent:
"Read every .py file in zeus/src/. For each file, check:
1. Does it have a corresponding test file in tests/?
2. Are all functions used? (grep for each function name across the codebase)
3. Are there any circular imports?
4. Are there any TODO comments older than 1 session?
Report: list of files with issues."
```

---

## Testing Contract

Every `.py` file in `src/` MUST have a corresponding `test_*.py` in `tests/`.

### What Tests Must Cover
1. Happy path
2. The specific edge case listed in `ZEUS_IMPLEMENTATION_PLAN.md §4.2`
3. Failure mode (None, empty, out of range inputs)

### What Tests Must NOT Do
- No network calls (mock all API clients)
- No file system writes outside `/tmp`
- No sleeping or timing-dependent assertions
- No randomness without seed (`np.random.seed(42)`)

### Test Running
Always use a Sonnet subagent to run tests:
```
Spawn Sonnet subagent: "cd zeus && source .venv/bin/activate && python -m pytest tests/test_X.py -v"
```
Do NOT run tests in the main context — save context for decision-making.

---

## Debugging Protocol

1. Test fails → read the FULL error traceback (don't guess)
2. Numeric precision issue: use `pytest.approx()` with tolerance
3. Random seed issue: ALWAYS `np.random.seed(42)` in test setup
4. Do NOT fix a test by weakening the assertion. Fix the CODE or fix the expectation with documented reasoning.
5. Maximum 3 debug attempts per test. After 3 fails, document in `ZEUS_PROGRESS.md` and move on.

---

## Discovery Protocol

If you discover something that contradicts the spec:
1. **STOP implementing.** Do not work around the issue.
2. Document the discovery in `ZEUS_PROGRESS.md` with exact details.
3. Tag it as `[BLOCKING]` or `[NON-BLOCKING]`.
4. `[BLOCKING]`: stop the session, ask Fitz for guidance.
5. `[NON-BLOCKING]`: document it, continue, flag for review.

---

## Session Handoff

### At Session End (MANDATORY)
1. Run Sonnet subagent: `pytest tests/ -v` — all must pass
2. `git add` specific files + `git commit -m "Session N: [summary]"`
3. Update `ZEUS_PROGRESS.md` with completed/in-progress/next tasks
4. If a module is half-written, leave `# TODO(session_N+1): [what's left]` at the exact stop point

### At Session Start (MANDATORY)
1. Read `ZEUS_PROGRESS.md` (< 200 lines)
2. Read this `CLAUDE.md`
3. Run Sonnet subagent: `pytest tests/ -v` to verify baseline
4. Read ONLY the spec sections relevant to today's task (use offset/limit)
5. Do NOT re-read the four research documents

---

## File Size Limit

No single `.py` file should exceed 250 lines (excluding comments and blanks). If approaching this limit, split by responsibility. Known split candidates:

```
market_analysis.py → market_analysis.py (class + edge scan) + bootstrap.py (double bootstrap logic)
riskguard.py → riskguard.py (main loop) + metrics.py (Brier, H-L, etc.)
```

---

## Subagent Rules

- Subagents do mechanical tasks ONLY (scan, verify, run commands, review)
- Subagent output ALWAYS requires main agent review before acting on it
- If subagent returns > 50 lines of code, extract interface + key logic only — main agent writes implementation
- Use `model: "sonnet"` for scanning/review/testing. Use `model: "opus"` for math verification only.

---

## What NOT To Do

- Do NOT reference Rainstorm v1 code for design decisions
- Do NOT use Gaussian CDF for probability estimation
- Do NOT create 72 calibration buckets
- Do NOT implement per-position stop loss
- Do NOT use mid-price for edge calculations
- Do NOT use KL divergence for model agreement
- Do NOT trust any "statistical finding" from Rainstorm v1
- Do NOT skip the Sonnet self-review after writing a file
- Do NOT read research documents during implementation sessions
- Do NOT run tests in the main context (use Sonnet subagent)

---

## Inherited Data

Data in `~/.openclaw/workspace-venus/rainstorm/state/rainstorm.db`. Import via `scripts/migrate_rainstorm_data.py`. NO code from Rainstorm — only data tables.

Key: `settlements` (1,634), `observations` (227K+), `market_events` (14.9K), `token_price_log` (285K).

Data agent continuously running: WU 2,000+ city-days backfilling to 2024, 38 cities.

## Commands

```bash
cd ~/.openclaw/workspace-venus/zeus && source .venv/bin/activate
ZEUS_MODE=paper python -m src.main       # paper trading
ZEUS_MODE=live python -m src.main        # live trading
python -m src.riskguard.riskguard        # risk guard (separate)
python -m pytest tests/                  # all tests
python scripts/baseline_experiment.py    # Phase 0 GO/NO-GO
python scripts/migrate_rainstorm_data.py # import data
python scripts/backfill_ens.py           # ENS P_raw backfill
```
