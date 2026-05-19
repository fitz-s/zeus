# PR 5 Scaffold Report: BoundClassification + CelsiusBox Propagation + DST Audit

**Date**: 2026-05-19
**Branch**: feat/phase0-pr5-day0-bound-classification-20260519
**Authority**: PHASE_0_V4_ADDENDUM.md PR 5 row; INV-09, INV-16
**Status**: SCAFFOLD ONLY — production code pending

---

## Topology Admission

| Check | Result |
|---|---|
| `topology_doctor --task-boot-profiles --json` | `{"ok": true, "issues": []}` |
| `topology_doctor --fatal-misreads --json` | Pre-existing issues (stale `architecture/fatal_misreads.yaml` refs); unrelated to PR 5 scope |
| Task class | `day0_monitoring` (trigger_terms: Day0, monitor, nowcast, observed so far) |
| Plan-lock required | NO (additive in `src/signal/`, `src/contracts/`) |

---

## Deliverables

| File | Type | Status |
|---|---|---|
| `src/contracts/day0_observation_context.py` | New contract | SCAFFOLD (stubs, `raise NotImplementedError`) |
| `tests/test_day0_bound_classification.py` | R-5.1 tests | SCAFFOLD (31 tests, all `@pytest.mark.skip`) |
| `tests/test_diurnal_dst_property.py` | R-5.2 tests | SCAFFOLD (5 tests, all `@pytest.mark.skip`) |
| `tests/test_day0_unit_box_propagation.py` | R-5.3 tests | SCAFFOLD (6 tests, all `@pytest.mark.skip`) |

Test collection: **31 tests collected, 31 skipped, 0 failures**.

---

## BoundClassification Enum

```python
class BoundClassification(str, Enum):
    DETERMINISTIC      = "DETERMINISTIC"        # obs already determines settlement
    BOUNDED_LIVE       = "BOUNDED_LIVE"          # obs present, outcome not yet set
    UNBOUNDED_NO_OBS_YET = "UNBOUNDED_NO_OBS_YET"  # no obs yet, pure ensemble
```

Location: `src/contracts/day0_observation_context.py`

---

## 12-Cell Matrix (3 BoundClassification × 4 Daypart)

| | pre_sunrise | morning | afternoon | post_peak |
|---|---|---|---|---|
| DETERMINISTIC | obs already past peak; signal locked | obs at morning extreme; all members beaten | obs floor (HIGH) / ceiling (LOW) set | confirmed — residual near zero |
| BOUNDED_LIVE | obs present; members span wide | obs present; members narrow toward peak | standard live signal; ensemble active | obs present; members winding down |
| UNBOUNDED_NO_OBS_YET | no obs yet; pure ensemble | no obs; members drive probability | no obs; ensemble coverage full | no obs; late-day — unusual, flag |

Note: `observation_state` is NOT a third axis; it is implicit in `BoundClassification`:
- `UNBOUNDED_NO_OBS_YET` ↔ `observed_extreme_so_far is None`
- `BOUNDED_LIVE` / `DETERMINISTIC` ↔ `observed_extreme_so_far is not None`

---

## DST Archetype Set (R-5.2)

| Label | City | Date | Event | Missing/Ambiguous hour | UTC of risk instant |
|---|---|---|---|---|---|
| A | London | **2026-03-29** | Spring-forward GMT→BST | Missing: 01:00–01:59 local | 2026-03-29T01:00–01:59Z |
| B | Sydney | 2025-10-05 | Spring-forward AEST→AEDT | Missing: 02:00–02:59 local | 2025-10-04T16:00–16:59Z |
| C | New York | 2026-11-01 | Fall-back EDT→EST | Ambiguous: 01:00–01:59 local (fold=0/1) | 2026-11-01T05:00–06:59Z |

### DST Audit Sites in `src/signal/diurnal.py`

| File:line | Risk | Notes |
|---|---|---|
| `diurnal.py:298–334` | HIGH | `_instant_from_local_hour`: `datetime.combine(target_date, time(hour%24, min, sec), tzinfo=tz)` constructs local datetimes without missing-hour branch |
| `diurnal.py:~340` | MEDIUM | `is_missing_local_hour` flag computed via `src.contracts.dst_semantics._is_missing_local_hour` but may not be branched downstream |
| `diurnal.py:~400–420` | MEDIUM | `build_day0_temporal_context`: propagation of `is_missing_local_hour` into `Day0TemporalContext.is_missing_local_hour` field |

---

## Annotation: Files Requiring Production Changes (No Edits in This SCAFFOLD)

### `src/signal/day0_router.py`

| Line(s) | Annotation |
|---|---|
| 7–21 | DESIGN COMMENT (authority: phase6_contract.md R-BA..R-BD): signal layer uses bare `float`; CelsiusBox/FahrenheitBox NOT for this layer. PR 5 OPEN QUESTION #1 applies here. |
| 44–66 | `Day0SignalInputs`: `current_temp: float`, `observed_high_so_far: float | None`, etc. — remain float per design. `BoundClassification` is NOT added here; it lives in `Day0ObservationContext` upstream. |
| 72–104 | `Day0Router.route()`: no `BoundClassification` dispatch needed; classification precedes routing at the ingest seam. |

### `src/signal/day0_signal.py`

| Line(s) | Annotation |
|---|---|
| All | `Day0Signal` is HIGH-only legacy class; only caller is `day0_high_signal.py:66`. For CelsiusBox propagation, the annotation target is `src/signal/day0_high_signal.py` (which is what `day0_router.py` actually routes to), NOT `day0_signal.py`. |

### `src/signal/diurnal.py`

| Line(s) | Annotation |
|---|---|
| 298–334 | `_instant_from_local_hour`: add missing-hour validation branch in production code. |
| DST flag | `build_day0_temporal_context` must propagate `is_missing_local_hour=True` for archetypes A, B; `is_ambiguous_local_hour=True` for archetype C. |

---

## Open Questions (≤5)

**Q1 — CelsiusBox propagation boundary (BLOCKING for production)**

`day0_router.py` lines 7–21 (authority: `phase6_contract.md R-BA..R-BD`) explicitly state
that the signal/evaluator layer uses plain `float` because values are unit-polymorphic at
runtime (Dallas=°F, London=°C share the same code paths). PR 5 states "propagate CelsiusBox/
FahrenheitBox into Day0 entry-points." These conflict.

Scaffold interpretation: boxes live at the IngestAdapter→Day0Router seam; `.value` is
extracted before `Day0SignalInputs` construction. Production code for BoundClassification
is computed BEFORE the `Day0Router.route()` call, using the box `.value` plus `unit` str.

**Needs operator confirmation**: is the production target (a) seam-only extraction (consistent
with existing authority), or (b) boxes inside Day0 signal objects (requires overriding
`phase6_contract.md R-BA..R-BD`)?

---

**Q2 — Annotation vs. inline comments**

Brief says "annotate (but do not edit)" `day0_router.py`, `day0_signal.py`, `diurnal.py`.
This scaffold places all annotations in this report as file:line tables (section above).
No inline `# SCAFFOLD-PR5: ...` comments added to source files.

**Needs confirmation**: are inline source comments required, or is this report sufficient?

---

**Q3 — London spring-forward date (FACTUAL CORRECTION)**

Brief cites "London 2026-03-30" as the spring-forward archetype. The correct date is
**2026-03-29** (last Sunday of March 2026). `dst_semantics.py` line 34 also shows an
example with 2025-03-30, which is correct for 2025 but the 2026 date is 2026-03-29.

This scaffold uses **2026-03-29** in all test fixtures. Confirm if 2026-03-30 was
intentional (perhaps testing a different scenario) or a typo.

---

**Q4 — day0_signal.py vs. day0_high_signal.py**

Brief states "annotate `src/signal/day0_signal.py`" for CelsiusBox propagation.
The actual HIGH evaluation entry point is `src/signal/day0_high_signal.py`
(which `day0_router.py:92` routes to via `Day0HighSignal`). `day0_signal.py` is
only imported by `day0_high_signal.py:66` as an internal detail.

Production CelsiusBox propagation should target `day0_high_signal.py`, not `day0_signal.py`.
This scaffold annotates both in section above. Confirm which file is the intended target.

---

**Q5 — DST audit target: `timedelta` vs `_instant_from_local_hour`**

Brief describes auditing "`timedelta(hours=...)` sites" in `diurnal.py`. Grep confirms
`diurnal.py` has **zero** `timedelta` usages (exit code 1 — no matches). The real DST
risk surface is `_instant_from_local_hour` (lines 298–334) which uses
`datetime.combine(..., tzinfo=tz)` without a missing-hour validation branch.

This scaffold targets `_instant_from_local_hour` as the production fix site.
Confirm this is the correct audit target and that no `timedelta`-based DST code
exists elsewhere in the Day0 signal chain.
