# Archives Policy

## Final ruling

`docs/archives` stays **historical-only**.
It stays **non-default-read**.
It should **not** be promoted back into the visible active docs mesh.

V2 strengthens that rule, not weakens it.

## Why this is the right answer

The uploaded archive bundle confirms three important things at once:

1. the archive is very large and heterogeneous;
2. much of it is valuable as evidence;
3. it is too noisy and risky to be default context.

### Archive inventory from the uploaded bundle

- total files: 397
- markdown files: 295
- Python files: 40
- CSV files: 22
- `.db` files: 4
- `.xlsx` files: 1
- `.pyc` / cache debris / `.DS_Store` also present
- the largest archive category is `work_packets/` with 251 files

That is the profile of **historical cold storage**, not a default boot surface.

## Default-read policy

### Final answer

- **No**, archives are not default-read.
- **No**, archives are not peer authority to `docs/`, `architecture/`, `src/`, or tracked manifests.
- **Yes**, archives may be used deliberately when a task explicitly calls for historical evidence.

## Uploaded zip use case

The uploaded archive zip is a valid review input when:

- reconstructing history,
- identifying repeated failure modes,
- checking whether a “new” idea is just a recycled old workaround,
- extracting dense lessons into `architecture/history_lore.yaml`,
- auditing whether active docs still point into stale history.

It is **not** a reason to re-open the archive body as a default repo path.

## Should archive bodies be tracked in the main repo?

### Final answer

**No.**
Not as a full tree.

### What should be tracked instead?

Track only a thin visible interface:

- `docs/archive_registry.md` — access policy, category index, retrieval instructions, and promotion guardrails
- `architecture/history_lore.yaml` — compressed durable lessons extracted from history

This gives online agents a visible historical protocol without shipping all historical bodies into default context.

## Secret and contamination policy

### Final answer

Any archive body must be treated as **potentially contaminated** until proven otherwise.

### Why

The uploaded archive bundle contains plaintext secret references and mixed binary debris.
Examples found during local archive review include plaintext Weather Underground API key strings inside archived markdown.

### Concrete examples from the uploaded archive review

[Archive evidence]

- `data-rebuild-2026-04-13/_superseded/data-rebuild.md` contains plaintext `WU_API_KEY` references.
- `work_packets/branches/data-improve/dual_track/2026-04-16_refactor_package_v2/.../zeus-system-constitution.md` contains plaintext `WU_API_KEY` references.
- archive bodies also include `.db`, `.xlsx`, `.pyc`, and `.DS_Store` debris.

### Required policy

- never promote archive text into active docs without a secret scan;
- redact secrets before any promotion, export, or re-tracking;
- do not quote or surface secret-bearing lines into active authority;
- if an archive file is needed as evidence, summarize rather than copy raw text unless it has been sanitized.

## `.db`, `.xlsx`, `.pyc`, `.DS_Store`

### Final answer

These stay archive-local or historical-only.
They are **not** default-read and should not be re-tracked into active docs authority.

### Specific policy

- `.db` archive files: provenance only, not runtime truth, not active test fixtures here
- `.xlsx` archive files: evidence only, not authority
- `.pyc` / `__pycache__`: junk in the archive body; never promote
- `.DS_Store`: junk; never promote

## `overlay_packages` and `local_scratch`

### Final answer

Keep them separate and historical.
Do not merge them back into active docs.

### Why

Overlay packages and local scratch material are exactly the sort of “experience loop residue” that can explain past confusion without deserving future authority.
They are evidence of how the system drifted, not necessarily evidence of what the system should become.

## How Pro should use archives

Pro should use archives only as:

- historical evidence,
- prior-failure evidence,
- old-decision evidence,
- provenance evidence.

Pro should label archive-derived claims as **[Archive evidence]** and should never silently blend them into current authority.

## Promotion rule

### Final answer

Archive content may only be promoted into current law if all of the following are true:

1. the content solves a still-live problem;
2. the content is consistent with current runtime/manifests or clearly supersedes them through an explicit packet;
3. the content is sanitized;
4. the promoted result is rewritten into current active form, not copied wholesale.

## Final archive architecture

### Visible layer

- `docs/archive_registry.md`
- `architecture/history_lore.yaml`

### Cold layer

- archive bundle / local `docs/archives/**`
- historical work packets
- retired overlays
- retired artifacts

### Rule

**Visible history should be index + compressed lesson; raw archive bodies stay cold.**
