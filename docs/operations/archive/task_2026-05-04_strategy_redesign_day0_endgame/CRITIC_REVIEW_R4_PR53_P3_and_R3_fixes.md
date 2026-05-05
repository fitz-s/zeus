# critic-opus R4 review — PR #53 P3 D-B migration + R3 fix bundle

HEAD: 3068d4e3850e656905f08a6f700c24726c56c43f
Reviewer: critic-opus
Date: 2026-05-03

## Subject

Adversarial review of two new commits on `strategy-day0-redesign-p2-2026-05-04` (PR #53):
- `eed05873` — R3 critic-fix bundle (ATTACK 8 HIGH + 5/6 MED + LOWs)
- `3068d4e3` — P3 D-B mode→phase dispatch migration (4 sites, flag-gated)

## VERDICT: APPROVED-WITH-CAVEATS

## BOTTOM LINE

P3 ships a clean, flag-OFF-byte-equal migration with 21/21 dispatch tests passing and the R3 ATTACK 8/6 fixes correctly landed in the production data flow. Two MEDIUM caveats prevent unconditional approval: (1) the cycle_runtime obs-fetch gate (P3 site 4) is NOT covered by any integration test — the dispatch-helper tests only exercise the helper and the 3 evaluator sites, leaving site 4 free to drift on refactor; (2) `attribution_drift._infer_strategy_from_signature` will generate spurious drift signals when the flag flips, because it re-applies `discovery_mode→strategy_key` rules without P3 awareness. Five LOW issues (dead helper, unused import, weak antibody test, stale `_require_utc` docstring, PLAN_v2.md/v3-header drift) are non-blocking but accumulate translation loss.

---

## ATTACK 1 [VERDICT: PASS] — R3 ATTACK 8 production data flow

**Evidence**: data flow traced end-to-end.

- `src/data/market_scanner.py:1083-1084` — `_parse_event` returns dict carrying `market_start_at` ← `event.get("startDate")`, `market_end_at` ← `event.get("endDate")`.
- `src/data/market_scanner.py:712` — `_parse_event` is called inside `find_weather_markets`, the production discovery entry.
- `src/engine/cycle_runtime.py:1998` — `markets = deps.find_weather_markets(...)`.
- `src/engine/cycle_runtime.py:2089-2094` — passes `market` dict into `market_phase_from_market_dict`.
- `src/strategy/market_phase.py:229,234` — adapter reads `market.get("market_end_at") or market.get("endDate") or market.get("end_date")` and same for start.

Production now consumes Gamma's explicit timestamps. F1 12:00 UTC fallback only fires when both keys absent (operational misread or upstream regression). No other call sites of `market_phase_from_market_dict` in `src/` were missed (verified via grep).

## ATTACK 2 [VERDICT: PASS-WITH-LOW] — R3 ATTACK 6 rename completeness

**PASS**: All `src/` references to the old name have been renamed. Grep for `_require_utc` in `src/` returns only the new function (`_require_zero_utc_offset`). No callers outside `src/strategy/market_phase.py`.

**LOW finding**: `tests/test_market_phase.py:466-467` docstring still references `_require_utc`:

> `"""``_require_utc`` (used inside ``market_phase_for_decision``) enforces ``utcoffset() == timedelta(0)``..."""`

The test body uses `market_phase_for_decision` (not the renamed helper directly), so functionally the test still validates the right thing, but the doc-string reference rots the symbol-anchor for future agents.

**Remediation**: edit `tests/test_market_phase.py:466` — replace `` `_require_utc` `` with `` `_require_zero_utc_offset` ``.

## ATTACK 3 [VERDICT: PASS] — Flag-OFF byte-equal preservation

**Evidence**: walked all 4 sites.

| Site | Pre-P3 | Post-P3 (flag OFF via `_is_day0_capture_legacy`) |
|------|--------|---------------------------------------------------|
| `evaluator.py::_edge_source_for` | `candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value` | `getattr(candidate, "discovery_mode", "") == DiscoveryMode.DAY0_CAPTURE.value` |
| `evaluator.py::_strategy_key_for` | same | same |
| `evaluator.py::_strategy_key_for_hypothesis` | same | same |
| `cycle_runtime.py` obs gate | `mode == DiscoveryMode.DAY0_CAPTURE` | `mode == deps.DiscoveryMode.DAY0_CAPTURE` (NOT routed through helper — see ATTACK 7) |

Edge cases:
- `discovery_mode == ""` (default per dataclass `field` at `evaluator.py:174`): both return False → byte-equal.
- `discovery_mode is None` (impossible given dataclass type `str`): `getattr` fallback would return `""`; pre-P3 would have raised `AttributeError`. Slight semantic loosening but not in any production path.

3 dedicated T6 tests (`test_t6_flag_off_*` at `tests/test_market_phase_dispatch.py:64-99`) actively check this. PASS.

## ATTACK 4 [VERDICT: APPROVED-WITH-CAVEATS] — Flag-ON safety

**(a) Mode/phase divergence semantics**: a candidate with `discovery_mode=OPENING_HUNT, market_phase=SETTLEMENT_DAY` would, with flag ON, be routed to `settlement_capture` strategy_key. This IS the documented design intent (`tests/test_market_phase_dispatch.py:213-229`). The schema accepts the mismatched combination — `state/db.py:663-668` has CHECK only on the strategy_key set, no cross-column constraint with `discovery_mode`. Backward-compat OK at schema level.

**MEDIUM finding A4-M1**: `src/state/attribution_drift.py:128-131` re-applies the dispatch rule based on `discovery_mode` ONLY:

```python
if sig.discovery_mode == "day0_capture":
    return "settlement_capture"
if sig.discovery_mode == "opening_hunt":
    return "opening_inertia"
```

When P3 flag is ON, the live evaluator may legitimately write `strategy_key=settlement_capture` for a candidate whose `discovery_mode=opening_hunt`. The drift checker would re-apply the legacy rule and report `inferred=opening_inertia` vs `persisted=settlement_capture` → spurious drift signal at every flip-on cycle. This is the classic "compaction survival" failure: P3 migrates the read-side dispatch but does not touch the post-hoc audit module.

**Remediation**: either
- (preferred) update `_infer_strategy_from_signature` to consult the P3 flag (or the post-flip canonical rule) and read `market_phase` if available;
- (acceptable interim) add a flag-gate guard at the attribution module entry: when `ZEUS_MARKET_PHASE_DISPATCH=1`, refuse to infer or skip these two clauses;
- (minimum) document the known-divergence in `attribution_drift.py` header so post-flip operators understand the spurious signals.

**(b) Fail-soft when `candidate.market_phase is None`**: in production, `cycle_runtime.py:2089` always tags. But the helper's fail-soft branch (`dispatch.py:71-76`) protects fixtures and any future direct construction. No leak. PASS.

## ATTACK 5 [VERDICT: APPROVED-WITH-CAVEATS] — Cycle-axis fallback honesty

**Evidence**: grep confirms `settlement_day_dispatch_for_mode` has zero callers in `src/` (only `dispatch.py` definition + `tests/test_market_phase_dispatch.py:175-176`).

`cycle_runner.py:317-326` and `cycle_runner.py:428-435` (freshness short-circuit) still use raw `mode == DiscoveryMode.DAY0_CAPTURE`, NOT the helper. The PLAN docs say these sites are intentionally NOT migrated by P3 — fine — but if the helper is the canonical "is this DAY0_CAPTURE-class?" question, those sites should call it for the grep-symmetry value the docstring promises.

**LOW finding A5-L1** (Fitz Constraint #2 — translation loss): `settlement_day_dispatch_for_mode` is dead-on-arrival scaffolding. Dead helpers rot fast: a future reader greps for `DAY0_CAPTURE` and finds the cycle_runner sites, doesn't find the helper (because it's only used in tests), and assumes the helper isn't relevant. The "single grep target" claim in the dispatch.py docstring is false today.

**Remediation**: choose one:
- migrate the 2 cycle_runner sites to call `settlement_day_dispatch_for_mode(mode)` so the symmetry actually holds;
- or delete `settlement_day_dispatch_for_mode` and update PLAN/dispatch.py to drop the "single grep target" claim.

## ATTACK 6 [VERDICT: PASS] — cycle_runtime market_phase relocation

**Evidence**: `git show 3068d4e3 -- src/engine/cycle_runtime.py` confirms relocation was part of P3 commit (NOT R3 fix bundle as the team-lead bootstrap claimed). The old block (post-obs-fetch) was removed; the new block (pre-obs-fetch) replaces it. Single computation, no duplication.

Exception path walked: if `market_phase_from_market_dict` raises (Gamma payload tz error), the warning logs and `market_phase` stays `None`. Obs gate then takes the `else` branch (`mode == DiscoveryMode.DAY0_CAPTURE`) regardless of flag state, because the gate condition is `if market_phase_dispatch_enabled() and market_phase is not None`. This is the documented fail-soft fallback. PASS.

(Minor doc-quality note: the team-lead bootstrap context attributed the relocation to `eed05873` when it was actually `3068d4e3`. Not a code issue, but illustrates how cite-CONTENT discipline rots in 24h.)

## ATTACK 7 [VERDICT: FAIL — MEDIUM] — Test coverage gap on obs-fetch gate

**Evidence**: grep `should_fetch_observation\|market_phase_dispatch_enabled` in `tests/` returns hits only in `tests/test_market_phase_dispatch.py:30,47,56` (helper-level tests). Zero tests exercise `cycle_runtime.execute_discovery_phase`'s obs-fetch gate end-to-end with the flag toggled.

**MEDIUM finding A7-M2**: P3 site 4 of 4 (the obs-fetch gate at `cycle_runtime.py:2103-2112`) has no integration test. Header of `test_market_phase_dispatch.py:17-19` even acknowledges this:

> `4. ``cycle_runtime.execute_discovery_phase`` obs-fetch gate (covered by integration test scaffolds; this file exercises the helper invariants).`

But no such integration test exists. A refactor that, e.g., moved `should_fetch_observation = mode == ...` to read a different variable, or that deleted the `market_phase is not None` guard, would not fail any test.

This matters for T6 byte-equal: 3 evaluator sites are individually verified, but the obs-gate is the most operationally-load-bearing site (it controls whether `fetch_day0_observation` runs, which determines whether the candidate has `obs` for downstream Day0 logic). A flag-OFF regression here changes the actual data fetched.

**Remediation**: add at least 2 integration tests at `tests/test_runtime_guards.py` or a new file:
- flag OFF + `mode=DAY0_CAPTURE` ⇒ `fetch_day0_observation` called
- flag ON + `market_phase=SETTLEMENT_DAY` (mode=OPENING_HUNT) ⇒ `fetch_day0_observation` called
- flag ON + `market_phase=PRE_SETTLEMENT_DAY` (mode=DAY0_CAPTURE) ⇒ `fetch_day0_observation` NOT called

Mocked `fetch_day0_observation` invocation count is sufficient — no need to wire real obs fetch.

**LOW finding A7-L2**: `cycle_runtime.py:2106` imports `is_settlement_day_dispatch` but never calls it. The obs-gate uses a hand-rolled `mode == DiscoveryMode.DAY0_CAPTURE` instead of `is_settlement_day_dispatch(candidate)`. This is unused-import slop AND breaks the "single locus" invariant the dispatch.py docstring claims (lines 25-27: "single locus for the dispatch decision so the four call sites all read the same flag and the same logic"). Site 4 actually reads its own logic.

**Remediation**: either remove the import OR refactor to use `is_settlement_day_dispatch(candidate)` once the candidate object exists. Note that at line 2109 the candidate is not yet constructed (line 2164), so a literal `is_settlement_day_dispatch(candidate)` call requires reordering. The clean fix: `should_fetch_observation = market_phase_dispatch_enabled() and market_phase == "settlement_day"` if `market_phase is not None` else `mode == DiscoveryMode.DAY0_CAPTURE` — same logic, but no unused import and no duplicated knowledge.

## ATTACK 8 [VERDICT: LOW] — PLAN/PR drift

**Evidence**:
- `docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v2.md:1` header: `# Strategy Redesign v3 — Day0-as-Endgame + Global-Tiled Scheduling`
- `PLAN_v2.md:24` sub-header: `## §0.1 Changelog v2 → v3`
- `PLAN_v2.md:37` sub-header: `### v3 → v3.1 amendments (post-critic-R3 on PR #53, 2026-05-04)`
- Filename: `PLAN_v2.md`
- Commit body `3068d4e3` says: `"PLAN_v3 §6.P3 D-B mode→phase migration"` and `"PLAN_v3 §6.P3"` (2× references)

**LOW finding A8-L3**: filename = v2, header = v3, content = v3.1, commit body = v3. Four-way translation drift in a single PR. A future agent grepping for `PLAN_v3.md` finds nothing; one grepping for `PLAN_v2.md` reads "v3.1 amendments". Ten months from now this becomes a hard-to-trace puzzle.

**Remediation**: choose one and apply consistently:
- (cleanest) `git mv PLAN_v2.md PLAN_v3.md`, update commit-history-onward references to v3.1 in the body of the file.
- (cheap) update header to `# Strategy Redesign v3.1 — ...` AND drop the v2 → v3 framing, reframe as v3.1; keep filename for backwards link compat.
- Either way, future commits should say "PLAN §6.P3" without a version prefix in the body to avoid this trap.

## ATTACK 9 [VERDICT: PASS] — Multi-flag interaction

**Evidence**: read `src/control/entry_forecast_rollout.py::evaluate_entry_forecast_rollout_gate` (lines 34-65). The function consumes `config: EntryForecastConfig` and `evidence: EntryForecastPromotionEvidence`, NOT `discovery_mode` or `strategy_key`. It returns a status string keyed off rollout_mode + evidence completeness. No interaction with the P3 phase axis.

The two flags are orthogonal: `ZEUS_MARKET_PHASE_DISPATCH` controls dispatch axis; `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` controls promotion eligibility. Neither reads the other. No finding.

## ATTACK 10 [VERDICT: LOW] — P4/P5 deferral disclosure

**Evidence**: PR title (per branch name) is `strategy-day0-redesign-p2-2026-05-04`. PR body not directly accessible via local checkout, but commit messages tell the story:
- `3068d4e3`: "P3(D-B mode→phase migration): 4-site flag-gated dispatch [skip-invariant]"
- No mention of P4 / P5 status in commit body.

**LOW finding A10-L4**: PLAN §0.1 lists P0-P5 as the spine. Commits ship P0 (merged via PR #51), P1 (`adf56154`), P2 stages 1-3 (4 commits), and P3 (this commit). P4 (D-A two-clock unification) and P5 (phase-aware Kelly) are not in this PR. The PR description should state explicitly: "Ships P3; P4+P5 follow in subsequent PRs."

This matters because P3 dispatch with flag OFF is a no-op behaviorally, but the FRAME the PR creates (mode→phase migration is "done") could mask that the actual behavior change (P4, P5) is still in flight. An operator who reads the PR title and merges may think the strategy redesign is closer to complete than it is.

**Remediation**: update PR description to explicitly call out:
> "This PR delivers P0+P1+P2+P3 (scaffolding + read-side migration, flag default OFF). P4 (D-A two-clock unification) and P5 (phase-aware Kelly) ship in follow-up PRs. With this PR merged + flag OFF, no behavior change."

## Required fixes summary

| # | Severity | Finding | Remediation |
|---|----------|---------|-------------|
| A4-M1 | MEDIUM | `attribution_drift._infer_strategy_from_signature` not P3-aware → spurious drift on flag flip | Update inference to read flag + market_phase, OR add module-level flag guard, OR document the divergence inline |
| A7-M2 | MEDIUM | No integration test for cycle_runtime obs-fetch gate (P3 site 4) | Add 3 tests in tests/test_runtime_guards.py exercising mock fetch_day0_observation under flag OFF/ON and varying market_phase |
| A5-L1 | LOW | `settlement_day_dispatch_for_mode` is dead scaffolding (zero callers in src/) | Migrate 2 cycle_runner sites to call it, OR delete it + update PLAN/dispatch.py docstring |
| A7-L2 | LOW | `cycle_runtime.py:2106` imports `is_settlement_day_dispatch` but doesn't use it | Remove the import; refactor obs-gate to call it through a single line |
| A2-LOW | LOW | `tests/test_market_phase.py:466` docstring still says `_require_utc` | Rename to `_require_zero_utc_offset` |
| A8-L3 | LOW | Filename PLAN_v2.md / header v3 / content v3.1 / commit body v3 — four-way drift | Rename file to PLAN_v3.md and update header to v3.1 (or pick one canonical version label) |
| A10-L4 | LOW | PR doesn't disclose P4+P5 deferral | Update PR body with explicit deferral statement |

## Honest disclosure

- The full regression suite was NOT rerun against `3068d4e3` for this review. Only the 21 dispatch tests + 19 market_phase tests were exercised (40/40 pass at HEAD via `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv`). The team-lead must confirm zero new failures vs. the pre-P3 baseline before merge per memory L22+L28.
- I did NOT verify that `attribution_drift._infer_strategy_from_signature` is currently called in any active production path. If the function is itself dormant, A4-M1 is observability-only and the severity could downgrade to LOW. Recommend the team-lead grep callers of `_infer_strategy_from_signature` before deciding.
- The 21 dispatch tests are well-constructed (parametrized truthy/falsy, T6 invariants explicit, 3-site consistency check). I did not find any rubber-stamp false-positives — they would actually fail on a real regression of `is_settlement_day_dispatch`. The site-4 gap (A7-M2) is the antibody hole, not test quality.
- ATTACK 8 antibody test (`test_parse_event_dict_carries_endDate_keys` at tests/test_market_phase.py:391) is a TEXT-grep on the source file, not a runtime invocation. It would pass even if the keys moved out of `_parse_event` to a different function. Weak antibody — flagging here for future hardening but not a blocking caveat for THIS PR (the keys ARE in `_parse_event` today and the data flow is intact).
- Fitz Constraint #4 (data provenance) check: `market_end_at`/`market_start_at` carry from Gamma → `_parse_event` → market dict → `market_phase_from_market_dict`. Intermediate steps do not preserve a `source: "gamma_event_field"` tag. If a future code path injects a synthetic value from elsewhere (e.g., Polymarket REST mirror, internal cache), the adapter would consume it without authority verification. Out-of-scope for this PR but worth a follow-up `source` field on the market dict.

End of R4 review.
