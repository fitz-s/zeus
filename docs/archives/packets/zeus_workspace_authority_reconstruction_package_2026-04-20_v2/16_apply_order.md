# Apply Order

## Read order

1. `README.md`
2. `00_executive_ruling.md`
3. `01_mental_model.md`
4. `02_authority_order_rewrite.md`
5. `04_code_review_graph_policy.md`
6. `05_archives_policy.md`
7. `07_execution_packets.md`
8. `08_patch_blueprints/p0_patch_blueprint.md`
9. `09_validation_matrix.md`
10. `10_codex_prompts/codex_p0_execute.md`

## Branch / commit assumptions

- **ASSUMPTION:** working branch is `data-improve`
- baseline commit `97acd96f500d6560de82cec9497c61d8358bab62` is a comparison anchor, not a reset target
- local checkout may be ahead of the online snapshot inspected for this package
- unrelated dirty work may exist and must be preserved

## Preflight checks

Run before editing:

```bash
git rev-parse --abbrev-ref HEAD
git status --short
python scripts/topology_doctor.py --docs --json
```

If the local branch is not `data-improve`, or if root docs/topology already differ materially, adapt carefully and label deviations `LOCAL_ADAPTATION`.

## Implementation steps

### Step 1 — Execute P0 only

Use `10_codex_prompts/codex_p0_execute.md`.

### Step 2 — Validate P0

Run the P0 validation set from `09_validation_matrix.md`.

### Step 3 — Stage carefully

Stage only:

- AGENTS.md
- workspace_map.md
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- docs/operations/AGENTS.md
- docs/operations/current_state.md
- architecture/topology.yaml
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

Do not use blanket staging.

### Step 4 — Commit P0

Use the expected Lore Commit Protocol message from `07_execution_packets.md`.

### Step 5 — Push P0 branch state

Push normally for review.
Do not squash unrelated dirty work into the packet.

### Step 6 — Run Pro follow-up review

Use `11_pro_followup_prompt.md` against the actual diff.

### Step 7 — Decide next packet

- If Pro says `proceed_to_p1`, run `10_codex_prompts/codex_p1_execute.md`.
- If Pro says `p0_needs_fixups`, do only the named fixups first.
- If Pro says `stop_and_rethink`, stop the sequence and rewrite the package assumptions locally before touching P1.

## Staging rules for every packet

- never use `git add -A`
- never stage archive bodies accidentally
- never stage runtime-local or shadow-local files
- always run `git diff --cached --check`
- always confirm `git status --short` before commit

## Post-push prompt to Pro

After P0 push, send the text from `11_pro_followup_prompt.md` with the real diff context.
