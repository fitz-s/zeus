# Zeus Workspace Authority Reconstruction Package — V2

This package is a **superseding V2** reconstruction proposal for Zeus workspace authority, topology, and context packaging on `data-improve`.
It is built for **local Codex** to unzip, read, implement in phases, and verify **without re-deriving the architecture from scratch**.

## What this package is

- A decisive ruling on what Zeus authority should become for **online Pro/review agents** and **local Codex**.
- A reconstruction of the repo around an **objective systems model**, not around the repo's existing bureaucracy.
- A concrete proposal that distinguishes:
  - runtime truth,
  - machine-checkable workspace law,
  - human boot surfaces,
  - derived context engines,
  - historical evidence.
- A phased execution plan that keeps authority cleanup separate from runtime/source/data behavior.

## What this package is not

- Not a source-behavior refactor.
- Not permission to rebuild data, mutate live DBs, or run migrations.
- Not permission to make `docs/archives` default-read.
- Not permission to make `.code-review-graph/graph.db` authority.
- Not a blanket rewrite order for every scoped `AGENTS.md` file in one pass.

## What changed from V1

V2 changes the center of gravity.
It does **not** merely say “trim docs and keep manifests.”
It explicitly redefines Zeus as two coupled systems:

1. a **prediction/trading machine** that needs canonical point-in-time truth and explicit control law; and
2. an **agentic coding workspace** that needs a thin boot surface, machine-routable law, first-class structural retrieval, and compressed historical memory.

That shift matters because V2 promotes Code Review Graph from “adjacent derived cache” to **first-class derived context engine** while still refusing to let it become authority.

## How local Codex should apply it

1. Read `16_apply_order.md`.
2. Read `00_executive_ruling.md`, `01_mental_model.md`, `02_authority_order_rewrite.md`, and `04_code_review_graph_policy.md` before touching files.
3. Execute **P0 only** first using `10_codex_prompts/codex_p0_execute.md`.
4. Preserve unrelated dirty work. Stage only the files explicitly allowed by the active packet.
5. After P0, run the Pro review pass in `11_pro_followup_prompt.md` before starting P1.

## What must be reviewed before applying

- Branch and baseline assumptions.
- Whether the local repo tip differs materially from the online state inspected for this package.
- `architecture/change_receipt_schema.yaml` for exact receipt requirements.
- Local graph build/update workflow before attempting P2.
- Whether any local patch already changed root docs, topology, or graph tooling.

## Decisions that are safe to apply without product/runtime review

- P0 boot-surface rewrite and archive routing cleanup.
- Adding a tracked `docs/archive_registry.md`.
- Slimming `docs/operations/current_state.md` into a live control pointer.
- Reframing Code Review Graph as a first-class **derived context plane**, while keeping it non-authority.

## Decisions that require human approval

- Tracking any archive bodies in the main repo.
- Any destructive cleanup of historical archive material.
- Any committed regeneration of `.code-review-graph/graph.db` or introduction of a tracked `.code-review-graph/graph_meta.json` sidecar.
- Any changes that cross from workspace authority into runtime behavior, DB truth, or execution-state architecture.

## Package assumptions

- **ASSUMPTION:** `data-improve` remains the working branch, but local Codex may be ahead of the online snapshot used for this package.
- **ASSUMPTION:** online-only Pro/review workflows are now a first-class repo requirement, not an optional convenience.
- **ASSUMPTION:** `.code-review-graph/graph.db` remains tracked unless a concrete security or integrity blocker is found.
- **ASSUMPTION:** unrelated dirty work may exist; every packet in this package stages only named files.

## Local verification requirements

- **LOCAL_VERIFICATION_REQUIRED:** re-check the local checkout before P0 if root docs already drift from the inspected online state.
- **LOCAL_VERIFICATION_REQUIRED:** inspect the exact local `architecture/change_receipt_schema.yaml` before finalizing any receipt payload.
- **LOCAL_VERIFICATION_REQUIRED:** for P2, verify the local graph builder, path mode, freshness, and compatibility before committing any graph artifact.
- **LOCAL_VERIFICATION_REQUIRED:** if you add `.code-review-graph/graph_meta.json`, verify that it truthfully reflects the committed DB and is not hand-edited fiction.
