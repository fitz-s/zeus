# Phase Plan — P0: Scope and Lane Repair

**Companion to:** `../repair_blueprints/p0_scope_and_lane_repair.md`,
`../prompts/codex_p0_execute_topology_lane_repair.md`,
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P0 row).

This file is the executable atomic checklist. The repair blueprint
states *what* to change; this plan states *the order* and *the anchor
points* to change them at.

## 1. Goal restated

Make `topology_doctor` distinguish four blocking semantics:
`navigation`, `closeout`, `strict_full_repo`, `global_health`. Today
`scripts/topology_doctor.py:1084-1123` (`run_navigation`) treats every
`severity == "error"` from any of 9 broad lanes as a navigation
blocker. After P0, navigation blocks only on direct route failures;
unrelated repo health surfaces as warnings.

## 2. Anchor points in current code

| What | Where |
|------|-------|
| `run_navigation` aggregator | `scripts/topology_doctor.py:1084-1123` |
| `run_strict` | `scripts/topology_doctor.py:368` |
| `_repo_health_for_context_pack` (already separates concerns) | `scripts/topology_doctor.py:904-905` |
| Closeout entry | `scripts/topology_doctor_closeout.py` (whole file, 208 lines) |
| Flat per-lane CLI flags | `scripts/topology_doctor_cli.py:21-66` |
| Test surface | `tests/test_topology_doctor.py` (existing nav/closeout tests, search `def test_navigation`, `def test_closeout`) |

## 3. Pre-decisions

- **Mode taxonomy** (final, used downstream): `navigation`,
  `navigation_strict_health`, `closeout`, `strict_full_repo`,
  `global_health`. Stored as a small constant table inside
  `topology_doctor.py` (no manifest yet — that is P1/P3 work).
- **Lane → blocking-mode-set mapping**: navigation blockers are the
  intersection of (lane is requested-file-relevant) AND
  (issue severity == error). Everything else becomes a warning in
  `repo_health_warnings`. The mapping table is the only new policy
  data added in P0.
- **CLI surface**: do **not** rename existing `--strict`,
  `--navigation`, etc. Add `--strict-health` opt-in flag to navigation
  to re-enable today's blocking behavior, and add `--global-health`
  alias to `--strict` for clarity. No flag removals in P0.

## 4. Ordered atomic todos

1. **Snapshot baseline behavior.** Capture
   `topology_doctor.py --navigation --task "p0 baseline" --files scripts/topology_doctor.py --json`
   and `topology_doctor.py closeout --changed-files scripts/topology_doctor.py --json` to
   `validation/p0_baseline.json` (gitignored or kept as evidence per OQ-3).
2. **Add mode policy helper.** New small dataclass / dict near
   `TopologyIssue` (around `scripts/topology_doctor.py:71`):
   `LANE_BLOCKING_POLICY: dict[str, set[str]]` mapping mode →
   blocking-eligible lanes. No imports added.
3. **Refactor `run_navigation`** at `scripts/topology_doctor.py:1084-1123`:
   - Keep the 9-lane checks dict as-is (no behavior reform downstream).
   - Compute `requested_paths = files or []`.
   - Split issues into `direct_blockers` (lane allowed for `navigation`
     mode AND `severity == error` AND issue is requested-file-relevant
     OR route-generation-related) and `repo_health_warnings`
     (everything else).
   - Add `global_health_counts` summarizing severity per lane.
   - `ok` becomes `not direct_blockers` (was: any error).
   - Add new top-level keys `direct_blockers`, `route_context`,
     `repo_health_warnings`, `global_health_counts`. Keep existing
     `issues`, `checks`, `digest`, `task`, `context_assumption`,
     `excluded_lanes` keys for compatibility.
4. **Add `--strict-health` flag** in
   `scripts/topology_doctor_cli.py` near line 58 (next to
   `--navigation`). When set, `run_navigation` falls back to the legacy
   "any-error blocks" behavior.
5. **Closeout scoping refinement** in
   `scripts/topology_doctor_closeout.py`:
   - Make changed-file + companion path the *only* automatic blocking
     surface.
   - Add a `global_health` sidecar field (advisory only) to closeout
     JSON output.
   - Confirm always-on lanes (planning/work/receipt/map/artifact/
     naming/freshness/graph) still produce *blocking* issues only when
     the issue's path is in `changed_files ∪ companions`; otherwise
     they go to the sidecar.
6. **Add fixtures + targeted tests** in
   `tests/test_topology_doctor.py`:
   - `test_navigation_unrelated_docs_drift_does_not_block_source_route()`
   - `test_navigation_strict_health_re_enables_blocking()`
   - `test_closeout_unrelated_source_drift_does_not_block_docs_packet()`
   - `test_closeout_changed_source_missing_rationale_still_blocks()`
   - `test_closeout_changed_module_book_missing_companions_blocks()`
   - `test_strict_full_repo_still_sees_all_errors()`
7. **Compile + targeted pytest.**
   ```bash
   python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
   pytest -q tests/test_topology_doctor.py -k "navigation or closeout or strict_health"
   ```
8. **Re-run baseline commands** from step 1 and diff. Expected:
   - navigation JSON now has new top-level keys and fewer
     `direct_blockers` than legacy `issues[severity==error]`.
   - closeout JSON now has `global_health` sidecar field.
9. **Add validation matrix row** in
   `validation/validation_matrix.md` for P0 with commit SHA, command,
   ok=true.

## 5. Verification

```bash
python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -k "navigation or closeout or strict_health"
python3 scripts/topology_doctor.py --navigation --task "topology lane repair" --files scripts/topology_doctor.py --json
python3 scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py tests/test_topology_doctor.py --summary-only
python3 scripts/topology_doctor.py --navigation --strict-health --task "p0 strict-health smoke" --files scripts/topology_doctor.py --json
```

## 6. Definition of done

- `run_navigation` returns the four new top-level keys and never
  blocks on unrelated drift in tests.
- `--strict-health` flag re-enables legacy behavior verbatim.
- Closeout output carries an advisory `global_health` sidecar.
- All six new tests pass; no existing tests removed or weakened.
- `--navigation`, `--strict`, `closeout` JSON keys unchanged for
  legacy consumers (additions only).
- Validation matrix row green on this commit.

## 7. Rollback

Revert the lane policy helper, the `run_navigation` rewrite, the
`--strict-health` flag, and the new tests as a single commit. Closeout
sidecar is additive and may stay if revert is partial.

## 8. Critic focus (Review0 gate)

Per `prompts/codex_review_after_p0.md`:

- Did unrelated drift get *hidden* (bad) or *separated* (good)?
- Does navigation still surface every drift in `repo_health_warnings`?
- Does closeout still block changed-file obligations?
- Was any non-policy semantic accidentally changed in any of the
  9 lane runners? (No — P0 only edits the aggregator and CLI.)

## 9. Risks specific to P0

- **R-P0-1**: New mode constants accidentally used by downstream P1
  before P1's typed model lands. Mitigation: keep constants module-private
  (leading underscore) until P1 promotes them.
- **R-P0-2**: Closeout sidecar grows unbounded. Mitigation: cap sidecar
  to per-lane counts only; full issue list stays under `issues`.

## 10. Lore commit message

`Topology P0: separate navigation/closeout/global-health lane policy without runtime behavior changes`
