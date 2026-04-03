File: docs/governance/zeus_foundation_package_map.md
Disposition: NEW
Authority basis: docs/governance/zeus_autonomous_delivery_constitution.md; architecture/self_check/authority_index.md; foundation package files; current repo runtime truth surfaces; Session 2 package decomposition.
Supersedes / harmonizes: dossier-only package decomposition; informal packet scope.
Why this file exists now: Zeus needs one package map that agents and operators can use without reopening architecture direction.
Current-phase or long-lived: Long-lived, with packet catalog review.

# Zeus Foundation Package Map

## 0. Package rules
- A package may touch only the files listed under **allowed files** unless it is explicitly superseded by a narrower packet.
- **Forbidden files** are hard stop boundaries for that package.
- Team execution is allowed only where marked and only after packet approval.
- “Long-lived” means the package family will survive beyond transition. “Current-phase only” means it exists to get Zeus through authority cutover.

## 1. Package table

| Package | Objective | Class | Allowed files | Forbidden files | Required reads | Owner role | Support roles | Mandatory evidence | Gates to pass | Sequencing / dependencies | Team allowed? | Current-phase only or long-lived |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P-AUTH-01 | Install principal authority stack | governance / architecture | `docs/architecture/**`, `docs/governance/**`, `architecture/**` | `src/**`, `migrations/**`, `.github/workflows/**` unless explicitly included | authority index, spec, constitution, manifests, maturity model | tribunal lead | verifier, critic | precedence note, supersede map, uncertainty note | manifest consistency, architecture review | first | No | long-lived |
| P-INSTR-01 | Install root/scoped `AGENTS.md` and `.claude` shim | governance / delivery | `AGENTS.md`, scoped `AGENTS.md`, `.claude/CLAUDE.md` | runtime state and schema files | authority index, constitution, boundary note | repo-law lead | verifier | instruction-surface map, shim note | manual review, packet grammar if packetized | after P-AUTH-01 | Root docs no; scoped reviews yes | long-lived for AGENTS, current-phase for shim |
| P-BOUND-01 | Install Zeus ↔ Venus/OpenClaw boundary | governance / integration | `docs/governance/zeus_openclaw_venus_delivery_boundary.md`, `scripts/audit_architecture_alignment.py`, `src/supervisor_api/contracts.py` (narrow) | `architecture/**` unless boundary law changes | authority index, constitution, supervisor contracts | boundary owner | operator, verifier | contract map, external-surface assumptions | tests/manual contract review | after P-AUTH-01, P-INSTR-01 | Advisory-only subreviews | long-lived |
| P-GATE-01 | Install CI / enforcement package | governance / verification | `.github/workflows/**`, `scripts/check_*`, `scripts/replay_parity.py`, `tests/test_architecture_contracts.py`, `tests/test_cross_module_invariants.py` | live runtime files | spec, constitution, manifests, maturity model | gate owner | verifier, critic | blocking/advisory verdict, maintenance cost note | self-check scripts, workflow review | after P-BOUND-01 | No for workflow authorship; yes for test drafting | long-lived |
| P-ROLL-01 | Record migration delta and archive plan | rollout / governance | `docs/rollout/**`, `docs/governance/zeus_runtime_delta_ledger.md` | canonical runtime code unless paired packet exists | constitution, decision register, current runtime files | rollout owner | operator, critic | delta ledger, archive order, rollback | review only | after P-GATE-01 | No | current-phase only |
| P-STATE-01 | Remove highest-risk state drift now | architecture / runtime | `src/state/strategy_tracker.py`, `src/data/observation_client.py`, targeted tests/docs | `migrations/**`, authority files unless packet says so | state AGENTS, invariants, negative constraints, delta ledger | state-kernel owner | verifier | before/after behavior note, no-scope-widening note | targeted tests, architecture contracts | after P-ROLL-01 | Yes, but owner-led | current-phase only |
| P-MATH-01 | Bounded math iteration inside existing contracts | math | `src/signal/**`, `src/strategy/**`, `src/calibration/**`, tests | `architecture/**`, `docs/governance/**`, `migrations/**`, `src/control/**`, `src/supervisor_api/**` | root AGENTS, relevant scoped AGENTS, invariants | math owner | verifier | contract-nonimpact note | targeted tests | anytime after authority install | Yes | long-lived |
| P-MIG-01 | Canonical event/projection cutover | schema / architecture | `migrations/**`, `src/state/**`, `scripts/replay_parity.py`, relevant tests/docs | historical doc demotions, broad engine refactor beyond packet | constitution, spec P1/P7, state AGENTS, boundary note | migration lead | verifier, critic, human gate | rollback, parity, cutover checklist | migration smoke, replay parity, reviewer signoff | after P-GATE-01 and P-STATE-01 | Review lanes only | current-phase only |
| P-OPS-01 | Operator runbook and cookbook maintenance | governance / operator | `docs/governance/zeus_omx_omc_*`, runbook, first-phase plan | manifests, schema, runtime truth | constitution, decision register, current tool docs | operator lead | critic | command/source update note | manual review | after P-INSTR-01 | Advisory review only | long-lived |

## 2. Package family notes

### Packages that may use team lanes
- P-MATH-01
- bounded portions of P-STATE-01
- verifier and critic sub-lanes for P-GATE-01 or P-MIG-01

### Packages that should stay single-owner
- P-AUTH-01
- P-INSTR-01 root instruction work
- P-BOUND-01 final contract text
- any irreversible cutover packet

## 3. Package evidence shorthand

- **Authority basis:** which files gave permission
- **Truth layer:** descriptive, normative, or both
- **Zones touched:** K0–K4
- **Gates:** what must pass now
- **Waivers:** what is advisory only and why
- **Rollback:** how to undo without guesswork
