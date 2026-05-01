# 17 — Exact Apply Order for Local Codex

## Preflight

1. Confirm branch and baseline:
   ```bash
   git status --short
   git rev-parse HEAD
   git branch --show-current
   ```
2. Read:
   - `AGENTS.md`
   - `workspace_map.md`
   - `docs/operations/current_state.md`
   - `docs/authority/zeus_current_architecture.md`
   - `docs/authority/zeus_current_delivery.md`
   - `docs/reference/modules/topology_system.md`
   - this package: `00`, `03`, `04`, `05`, `10`, `12`.

## Apply order

### Step 1 — P0 only

Use `prompts/codex_p0_execute_topology_lane_repair.md`.

Do not edit module books or manifests except tests required to encode lane behavior.

### Step 2 — Review P0

Use `prompts/codex_review_after_p0.md`.

Focus on whether navigation now returns route context with unrelated drift in warnings, and whether closeout still blocks changed-file obligations.

### Step 3 — P1 typed issue model

Use `prompts/codex_p1_execute_issue_model.md`.

Keep old JSON keys.

### Step 4 — P2 module/system book rehydration

Use `prompts/codex_p2_expand_topology_books.md`.

Register new books only through existing docs/module manifest ownership.

### Step 5 — P3 manifest ownership normalization

Use `prompts/codex_p3_normalize_manifest_ownership.md`.

Do not delete data unless an owner is clear and tests cover conflict behavior.

### Step 6 — P4 graph/context-pack extraction

Use `prompts/codex_p4_graph_and_context_pack.md`.

Keep graph derived-only.

### Step 7 — P5 test resegmentation

Use `repair_blueprints/p5_topology_doctor_test_resegmentation.md`.

Add fixture/live split after lane and issue semantics are stable.

### Step 8 — Closeout

Use `prompts/codex_closeout.md`.

## Hard stops

Stop and re-plan if:

- runtime/source behavior files must be edited,
- graph output is being treated as authority,
- archives become default-read,
- tests are weakened without durable law extraction,
- ownership conflicts cannot be assigned to a single canonical manifest.
