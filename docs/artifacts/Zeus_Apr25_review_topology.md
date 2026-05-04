Zeus topology 仍值得保留，但当前 digest/routing 子系统存在严重设计缺陷：它把“自然语言提示生成器”伪装成了“范围授权/治理内核”。最危险的不是它没有强制拦截，而是它输出了像强制边界一样的字段，让 agent 误以为已经被安全约束

## 1. 总判定：问题比“匹配 bug”深很多

你的四个 finding 不是四个独立 bug，而是同一个根问题的不同外显：

**topology digest 同时承担了 discovery、profile selection、permission hint、forbidden warning、context hydration、gate hint、agent onboarding 几个角色，但没有一个真正的 admission/authorization decision layer。**

所以它现在不是一个可靠的 routing kernel，而是：

1. 用 substring 从 task text 猜 profile；
2. 把 profile 的 allowed/forbidden 信息拼进 JSON；
3. 对调用者传入的 files 不做真实 reconciliation；
4. generic fallback 甚至把调用者传入的 files 提升成 allowed files；
5. 最后用 `ok: true` 表示 digest 成功生成，而不是 scope 被授权。

这会造成一个治理系统里非常危险的错觉：**它看起来在做边界控制，实际上只是在输出建议。**

这和 repo 顶层 agent contract 已经冲突。`AGENTS.md` 明确把 digest 输出描述成：`allowed_files` 是“files you may change”，`forbidden_files` 是“do not touch”，并说 digest 会返回 safety gates 和 stop conditions。([GitHub][2]) 但 `topology_doctor_digest.py` 目前的 profile matching 是 `if any(match in task_l for match in matches)`，generic fallback 又把传入的 `files` 直接放进 `allowed_files`，并同时固定塞入 `state/*.db` 等 forbidden globs。([GitHub][3])

这不是实现细节失误，而是**contract/design mismatch**。

---

## 2. 上一版计划真正漏掉的东西

上一版计划抓住了很多正确方向：scope-aware closeout、navigation advisory-first、typed issue、deterministic fixtures、repair drafts、manifest ownership、graph derived-only 等。上传的计划已经明确写了“scope before strictness”“每个 blocking issue 必须有 owner 和 repair route”“topology/graph 是 derived routing context，不是 authority”。

但它漏掉了一个更底层的层：

> **route admission kernel：给定 task + requested files + current packet state，系统必须明确判断这些 requested files 是否被授权、是否越界、是否 forbidden、是否需要 scope expansion、是否只是 advisory digest。**

上一版把问题主要看成“global repo-health noise 误阻塞 scoped work”。现在你发现的问题更深：

| 旧计划关注                                        | 新暴露的根问题                                               |
| -------------------------------------------- | ----------------------------------------------------- |
| navigation 不应被 unrelated drift 阻塞            | navigation/digest 自己可能错误路由到完全错误 profile               |
| closeout 应 changed-file scoped               | changed-file 本身没有 admission policy                    |
| typed issues 应更 machine-routable             | route decision 本身不是 typed issue，缺 admission status    |
| allowed/forbidden 应帮 agent 减少 cognitive load | allowed/forbidden 被输出成 contract，但没有 enforce/reconcile |
| repair drafts 降低维护成本                         | profile trigger 和 policy matrix 本身会产生持续维护负债           |

所以现在的设计评价必须升级：**不是 topology 太 noisy，而是 topology 的最前端 route selection/admission 语义不成立。**

---

## 3. 你的四个 finding 的深层解释

### Finding A：`source` 命中 data ingestion profile

这证明 profile selection 不能基于普通英文 substring。

`topology_doctor_digest.py` 对 profile 的 `match` 列表做的是 lower-case substring containment。([GitHub][3]) 而 `topology.yaml` 里的 digest profiles 本来就含有很多普通词或短 token，例如 settlement profile 里有 `oracle`、script profile 里有 `script`，其他 profile 包含 backfill/replay/script 等自然语言触发词。([GitHub][4])

这类词在 Zeus 里有领域含义，但在普通任务描述里也极常见。
`source` 在 Zeus 可表示 data source / source truth / ingestion source；但在 “improve source code quality” 中只是普通 software phrase。用 substring 或 even word-boundary 都不够，因为问题不是字符边界，而是**语义边界**。

正确结论：

> 单词级 trigger 不能作为 hard profile selector。最多只能作为 weak evidence，而且必须被 file evidence、phrase evidence、negative context、confidence threshold 共同约束。

---

### Finding B：`allowed_files` advisory only，但 `ok: true`

这是最危险的问题。

如果 agent 问 settlement rounding，但附带 `src/state/lifecycle_manager.py`，digest 返回 `ok: true`，同时 allowed_files 不包含该 file，那么系统实际表达的是：

> “我没有拦你；这里只是建议你看这些 allowed files。”

但 agent contract 表达的是：

> “allowed_files 是你可以改的文件；forbidden_files 是你不能碰的文件。”([GitHub][2])

这会导致 online-only agent 误读。它可能看到 `ok: true` 就继续做越界修改，而 topology 系统没有把 out-of-profile requested file 转成 blocker、scope expansion、或 planning-lock request。

正确结论：

> `ok` 不能再承载 admission 语义。必须拆成 `command_ok` 和 `admission_status`。
> `allowed_files` 也不能同时表示 profile recommendation 和 actual authorization。

---

### Finding C：generic fallback 同时 allowed 和 forbidden

这个是设计上最纯粹的反例。

当前 fallback 是：

```json
allowed_files = files
forbidden_files = [".claude/worktrees/**", ".omx/**", "state/*.db"]
```

如果调用者传 `state/zeus_trades.db`，它同时在 allowed 和 forbidden。代码没有 reconciliation。([GitHub][3])

而 `topology.yaml` 已经把 `state/zeus_trades.db`、`state/zeus-world.db` 标成 canonical truth database，并且明确 diagnostics 不得 mutate。([GitHub][4]) `zeus_current_architecture.md` 也强调 runtime semantic law、truth ownership、topology/graph 不能当语义真相。([GitHub][5])

所以 generic fallback 不是“保守 fallback”，而是**权限提升漏洞**：

> 用户/agent 传入什么，它就把什么回显成 allowed。

正确结论：

> generic fallback 必须 fail closed：可以生成 advisory digest，但不得授权任何 requested file。
> forbidden 永远优先于 allowed。
> allowed ∩ forbidden 非空时必须 `blocked` 或 `invalid_route_contract`。

---

### Finding D：digest tests 旧期望失败

这说明测试套件验证了太多“输出形状”和“CLI parity”，但没有足够验证“治理安全不变量”。

`tests/test_topology_doctor.py` 已经非常大，覆盖 topology_doctor lanes、CLI parity、closeout、graph fixture、context packs、compiled topology 等。([GitHub][6]) 但你的 finding 说明 digest 的关键 adversarial cases 没有成为第一等测试对象：

* common English collision；
* single-token trigger false positive；
* requested file outside selected profile；
* forbidden file inside fallback；
* allowed/forbidden contradiction；
* `ok:true` 与 admission blocked 的语义冲突；
* negative context，例如 `source code` 不等于 data ingestion source。

正确结论：

> digest tests 不能只是 “settlement rounding profile 被命中”。
> digest tests 必须成为 adversarial admission tests。

---

## 4. 当前系统真正的设计缺陷：五层混淆

### 4.1 Digest 和 Admission 混淆

Digest 应该回答：

> “为了理解这个任务，agent 应该读什么？可能属于哪个 profile？有哪些 likely gates？”

Admission 应该回答：

> “这些 requested files 是否允许在当前 packet/scope 下修改？哪些 forbidden？哪些需要 scope expansion？哪些必须先 planning-lock？”

当前系统把这两个东西混在一个 JSON 里，导致 `allowed_files` 既像 recommendation，又像 permission。

---

### 4.2 Profile selection 和 file authority 混淆

现在先看 task text，再选 profile，再输出 allowed files。
但对于安全系统，顺序应该反过来：

1. 先看 requested files 的 repo zone / authority role / scoped AGENTS / manifest ownership；
2. 再用 task text 作为辅助 evidence；
3. 如果 text profile 和 file authority 冲突，必须报 ambiguity 或 scope expansion；
4. forbidden file 命中时直接 blocked。

`workspace_map.md` 也把 topology digest 作为 route narrowing 工具，但同时警告不要默认读 graph/archives/module books，要先路由再读。([GitHub][7]) 这意味着 route narrowing 不能随便猜错，否则后续整个 cognition path 都会偏。

---

### 4.3 Hard boundary 和 soft hint 混淆

`allowed_files` 这个名字已经是 hard boundary 语义。
如果它只是 hint，应改名为：

* `profile_suggested_files`
* `read_first_files`
* `likely_change_files`
* `advisory_allowed_files`

如果它要继续叫 `allowed_files`，那就必须 enforce requested files：

* requested file 命中 forbidden：blocked；
* requested file 不在 allowed/companion/downstream expansion：scope_expansion_required；
* requested file 在 allowed：admitted；
* no profile：advisory_only / needs_profile，不得 allowed。

---

### 4.4 Generic fallback 和 authorization 混淆

Generic fallback 的唯一安全用途是：

> “我不能识别 profile，因此只能提供 root boot docs、global safety rules、forbidden globs、以及要求 agent 获取 explicit plan。”

它不应该输出 allowed files，更不能把 caller-provided files 视为 allowed。

---

### 4.5 Topology issue model 和 route decision 混淆

`topology_schema.yaml` 现在已经有较丰富的 issue JSON contract：legacy fields 加 typed fields，包括 `lane`、`scope`、`owner_manifest`、`repair_kind`、`blocking_modes`、`related_paths`、`confidence`、`authority_status`、`repair_hint` 等。([GitHub][8])

但 digest/admission 不是普通 validator issue。它需要一个独立的 route decision object。否则所有东西都会被塞进 `issues[]`，agent 仍不知道最终应该停、继续、扩 scope、还是只当 advisory。

---

## 5. 新目标架构：Route Admission Kernel

我建议把 topology 前端重构为三层，而不是继续扩大 digest。

### Layer A：Evidence classifier

输入：

* `task`
* `requested_files`
* current branch / current packet state
* topology path roles
* map maintenance companion rules
* manifest ownership
* scoped AGENTS
* explicit mode：navigation / closeout / strict / packet-prefill / review

输出：

```json
{
  "task_evidence": {
    "positive_phrases": [],
    "weak_terms": [],
    "negative_phrases": [],
    "ambiguous_terms": [],
    "matched_profiles": []
  },
  "file_evidence": {
    "requested_files": [],
    "zones": [],
    "authority_roles": [],
    "manifest_owners": [],
    "forbidden_hits": [],
    "companion_requirements": []
  }
}
```

这里的关键是：**task text 不再直接决定 profile；它只是 evidence。**

---

### Layer B：Profile resolver

Profile resolver 不允许 substring hard route。每个 profile 必须有 typed match policy：

```yaml
digest_profiles:
  - id: modify_data_ingestion
    display_name: modify data ingestion
    match_policy:
      required_any:
        phrases:
          - "data ingestion"
          - "data source"
          - "ingestion guard"
        file_globs:
          - "src/data/**"
          - "src/ingestion/**"
      weak_terms:
        - "source"
        - "daily"
        - "history"
      negative_phrases:
        - "source code"
        - "code quality"
        - "refactor source"
        - "source map"
      min_confidence: 0.75
      single_terms_can_select: false
```

规则：

1. phrase > file evidence > weak term；
2. single token 默认不能 select profile；
3. weak term 只能加分，不能单独决定；
4. negative phrase 可以 veto；
5. 多 profile 接近时输出 `ambiguous`；
6. 低 confidence 时输出 `needs_profile`；
7. profile resolver 必须输出 why，而不是只输出 selected。

---

### Layer C：Admission reconciler

这层才决定能不能动文件。

建议输出：

```json
{
  "command_ok": true,
  "admission": {
    "status": "blocked | admitted | advisory_only | ambiguous | scope_expansion_required | needs_planning_lock",
    "profile_id": "change_settlement_rounding",
    "confidence": 0.91,
    "requested_files": [],
    "admitted_files": [],
    "profile_suggested_files": [],
    "out_of_scope_files": [],
    "forbidden_hits": [],
    "contradictions": [],
    "companion_required": [],
    "downstream_readonly_files": [],
    "decision_basis": {
      "task_phrases": [],
      "file_globs": [],
      "negative_hits": [],
      "authority_sources": []
    }
  }
}
```

Legacy `ok` 可以保留，但必须重新定义清楚：

* `command_ok`: command ran successfully；
* `admission.status`: safety decision；
* old `ok`: for backward compatibility only，不能被 agent 当 permission。

最好加：

```json
"ok_semantics": "command_success_only_not_write_authorization"
```

或者在过渡期让 navigation 在 `blocked/ambiguous/scope_expansion_required` 时 `ok:false`，防止 agent 继续误读。

---

## 6. allowed / forbidden 的新语义

这是必须写进 AGENTS、workspace_map、topology module book、schema 的核心 contract。

### 新定义

| 字段                          | 新语义                                               |
| --------------------------- | ------------------------------------------------- |
| `requested_files`           | caller 声称要读/改/审的文件                                |
| `profile_suggested_files`   | profile 推荐优先查看或常见改动文件，不是授权                        |
| `admitted_files`            | 当前 task/profile/packet 下可改文件                      |
| `forbidden_files`           | hard forbidden patterns；永远优先于 admitted            |
| `out_of_scope_files`        | requested 但未被当前 route admitted                    |
| `companion_required`        | 如果改 admitted files，必须同步检查/更新的 files/manifests     |
| `downstream_readonly_files` | 可作为 impact/read context，不代表可改                     |
| `scope_expansion_required`  | 不是硬 forbidden，但需要新的 packet/planning-lock/approval |

### 核心规则

1. **forbidden wins**：任何 file 同时 allowed 和 forbidden，结果必须 blocked。
2. **generic fallback never admits**：fallback 只能生成 advisory route，不得把 caller files 当 allowed。
3. **out-of-profile requested file never silently passes**：必须是 `scope_expansion_required`、`ambiguous`、或 `blocked`。
4. **`ok:true` 不得暗示 write permission**。
5. **allowed files 不能表达“所有可能需要改的文件”**；它只表达当前 route 已授权的文件。
6. **hidden companion obligations 必须独立于 allowed list**；companion 是“必须检查/可能更新”，不是“随便改”。

---

## 7. 为什么 `src/state/lifecycle_manager.py` 这个例子特别重要

你给的例子不是普通 out-of-scope file。
settlement rounding 任务中出现 `src/state/lifecycle_manager.py`，系统不应该简单说 “not in allowed list but ok”。

这个文件名暗示 state/lifecycle authority zone。结合 Zeus 架构，state truth、runtime law、settlement/source semantics 都是高风险边界。`zeus_current_architecture.md` 已经把 topology/graph 限定为 structure/routing only，不可作为 semantic truth；也列出了 source/date/settlement/current-fact 等 category errors 是主要失败源。([GitHub][5])

所以正确 response 应该类似：

```json
"admission": {
  "status": "scope_expansion_required",
  "reason": "requested file is outside settlement_rounding profile and appears to touch lifecycle/state boundary",
  "out_of_scope_files": ["src/state/lifecycle_manager.py"],
  "required_action": "open/freeze explicit packet or planning-lock expansion before editing"
}
```

如果当前 packet 没 frozen，`current_state` 还要求 fresh phase-entry planning。([GitHub][1])

---

## 8. 长期维护成本：不能继续靠人记 trigger

这类系统最大的长期风险不是今天这个 `source` bug，而是未来每新增一个 profile，就新增一组 silent false positive。

你列出的 `source, daily, append, coverage, types, frozen, signal, oracle, history, script` 都说明：**profile vocabulary 必须有 governance。**

我建议增加一个 profile trigger governance 机制：

### 8.1 禁用 hard single-word selectors

默认禁用：

```yaml
single_terms_can_select: false
```

例外必须显式说明：

```yaml
single_term_exceptions:
  - term: "HKO"
    reason: "Zeus-specific station/source identifier, low collision risk"
    owner: "architecture/topology.yaml"
    maturity: "stable"
```

### 8.2 每个 weak term 必须有 negative examples

例如：

```yaml
weak_terms:
  source:
    positive_examples:
      - "change data source mapping"
      - "fix HKO source ingestion"
    negative_examples:
      - "improve source code quality"
      - "source control"
      - "source map"
      - "source file"
```

### 8.3 每个 profile 必须有 adversarial tests

不是 optional。每个 profile 至少：

* 3 positive phrase tests；
* 5 negative common-English collision tests；
* 2 file-evidence tests；
* 1 ambiguity test；
* 1 forbidden requested-file test；
* 1 generic fallback safety test。

### 8.4 Trigger audit 成为维护任务

新增/修改 digest profile 时，必须跑：

```bash
python scripts/topology_doctor.py --navigation --task "<negative phrase>" --files <file> --json
pytest -q tests/test_topology_doctor.py -k "digest_admission or profile_trigger"
```

---

## 9. 该改哪些文件，不该改哪些文件

### P0 先做：Digest Admission Safety Repair

**目标**：让 digest 不再输出虚假授权。

Allowed files：

* `scripts/topology_doctor_digest.py`
* `scripts/topology_doctor.py`
* `scripts/topology_doctor_cli.py`，只改 JSON/render passthrough
* `tests/test_topology_doctor.py`
* 必要时 `architecture/topology_schema.yaml`
* 必要时 `docs/reference/modules/topology_doctor_system.md`
* 必要时 `AGENTS.md` / `workspace_map.md`，只修正 digest contract

Forbidden files：

* `src/**` runtime behavior
* `state/*.db`
* `docs/authority/**`，除非先有 planning-lock
* `.code-review-graph/graph.db`
* archives
* unrelated module books

P0 acceptance criteria：

1. `"improve source code quality"` 不得命中 data ingestion profile。
2. `"improve source code quality"` 如果没有 file evidence，应返回 `advisory_only` 或 `needs_profile`。
3. settlement rounding + `src/state/lifecycle_manager.py` 必须返回 `scope_expansion_required` 或 `needs_planning_lock`。
4. `state/zeus_trades.db` 在任何 fallback/requested_files 中必须 blocked。
5. allowed/forbidden intersection 必须产生 `route_contract_conflict`。
6. generic fallback 不得设置 `allowed_files = files`。
7. JSON 必须区分 `command_ok` 和 `admission.status`。
8. legacy `allowed_files` 若保留，必须标注 `legacy_advisory`.
9. 所有 digest tests 更新为新语义。
10. CLI human output 必须明确：“route generated” 不等于 “write authorized”。

---

## 10. P1：Profile Match Policy Repair

**目标**：把 profiles 从 flat trigger list 变成 typed matching grammar。

改动方向：

```yaml
digest_profiles:
  - id: change_settlement_rounding
    display_name: change settlement rounding
    match_policy:
      strong_phrases:
        - "settlement rounding"
        - "oracle truncate"
      weak_terms:
        - "rounding"
        - "oracle"
      required_any:
        file_globs:
          - "src/settlement/**"
          - "tests/**settlement**"
        strong_phrases:
          - "settlement rounding"
      negative_phrases:
        - "rounding out code style"
        - "oracle database"
      single_terms_can_select: false
      min_confidence: 0.8
```

并且新增 schema validation：

* `match` flat list deprecated；
* single-token strong selector 默认 schema warning；
* no negative examples => warning；
* profile without file_glob evidence => warning；
* profile without tests => warning；
* overlapping profiles must declare precedence or ambiguity behavior。

`topology_schema.yaml` 已经在 ownership/typed issue 方面很强，可以扩展为 profile contract owner，而不是再开一份不受治理的新 registry。([GitHub][8])

---

## 11. P2：File-first Route Resolution

**目标**：让文件路径和 authority role 优先于 task text。

现在 `topology.yaml` 已经有 path roles：`src` 是 executable law，`tests` 是 regression law，`docs/authority` 是 authority surface，`docs/reference/modules` 是 cognition surface，`.code-review-graph` 是 derived context，state DB 是 canonical truth。([GitHub][4])

所以 admission 应先做：

```text
requested file -> path role -> manifest owner -> scoped AGENTS -> map maintenance companions -> hard forbidden?
```

再结合 profile：

```text
profile says settlement rounding
requested file says lifecycle/state authority
=> conflict/scope expansion, not ok:true
```

这也符合 `map_maintenance.yaml` 的精神：它已经把 source/test/script/docs/module/reference/architecture 的 companion obligations 分 lane 维护。([GitHub][9])

---

## 12. P3：Agent-facing contract repair

这里必须修 AGENTS/workspace_map，否则 online-only agent 会继续误解。

当前 `AGENTS.md` 说 digest 输出的 `allowed_files` 是“files you may change”。([GitHub][2]) 这句话只有两种合法结局：

### 选项 A：保留强语义

那就必须让 code enforce admission。
`allowed_files` 只能包含 admitted files。
out-of-scope requested files 必须使 admission 非 allowed。

### 选项 B：改成弱语义

那就改名：

* `suggested_files`
* `read_first_files`
* `likely_change_files`

并明确：

> digest is advisory; write permission comes from packet scope/admission/closeout.

我建议采用混合：

* `profile_suggested_files`：弱；
* `admitted_files`：强；
* `forbidden_files`：强；
* `admission.status`：强；
* legacy `allowed_files` 过渡期保留但标注 deprecated。

---

## 13. P4：测试体系重分层

现有 test file 已经很大，且承担多种职责：lanes、CLI parity、graph、context pack、compiled topology、live health。([GitHub][6])

现在必须拆出 digest/admission 专用测试组：

### `test_digest_profile_matching.py`

覆盖：

* phrase positive；
* weak term insufficient；
* negative phrase veto；
* multi-profile ambiguity；
* file evidence override；
* no profile fallback。

### `test_digest_admission_policy.py`

覆盖：

* requested outside allowed；
* forbidden wins；
* allowed/forbidden contradiction；
* generic fallback never admits；
* state DB blocked；
* planning-lock zone requires expansion；
* command_ok vs admission.status。

### `test_digest_regression_false_positive.py`

用你的发现直接成为 fixture：

```text
improve source code quality
increase test coverage
append a note to docs
daily cleanup
fix type hints
freeze requirements
scripted review
history summary
oracle database migration
signal handling cleanup
```

其中大部分不应自动命中特定 Zeus domain profile。

---

## 14. P5：长期演化模型，而不是一次修 bug

根部系统需要维护经济学。否则下一次新增 profile，又会回到同一类问题。

我建议加四个长期规则：

### 14.1 Profile budget

每个 profile 不能无限塞 triggers。
超过阈值必须拆成 subprofile 或改成 file-first evidence。

### 14.2 Trigger review gate

任何新增 single token 都必须提供：

* collision analysis；
* negative examples；
* owner；
* maturity；
* tests。

### 14.3 Drift telemetry

digest 输出中记录：

```json
"profile_resolution": {
  "selected_by": "phrase | file | weak_term | fallback",
  "confidence": 0.0,
  "ambiguity": [],
  "negative_hits": []
}
```

长期看这些 telemetry 可以暴露哪些 profile 经常 ambiguous。

### 14.4 Promotion ladder

新 profile 不应立即 hard-block：

1. `experimental_advisory`
2. `stable_advisory`
3. `admission_candidate`
4. `admission_enforced`

每一层需要 false-positive-free cycles。

---

## 15. 现在最优先的 Codex packet

### Packet name

`P0_digest_admission_safety_repair`

### Objective

Stop digest from falsely implying write authorization. Make requested-file reconciliation explicit and fail closed for forbidden/out-of-scope/ambiguous cases.

### Allowed files

```text
scripts/topology_doctor_digest.py
scripts/topology_doctor.py
scripts/topology_doctor_cli.py
tests/test_topology_doctor.py
tests/test_topology_doctor_digest*.py   # if created
architecture/topology_schema.yaml       # only for admission JSON contract
docs/reference/modules/topology_doctor_system.md
AGENTS.md
workspace_map.md
```

### Forbidden files

```text
src/**
state/*.db
docs/authority/**
docs/archives/**
.code-review-graph/graph.db
architecture/topology.yaml              # unless only adding non-breaking profile metadata after schema decision
```

### Required behavior changes

1. Add route/admission reconciliation.
2. Rename or deprecate ambiguous `allowed_files`.
3. Make generic fallback non-authorizing.
4. Add forbidden-wins check.
5. Add out-of-scope requested file check.
6. Add ambiguity status.
7. Add common-English false-positive tests.
8. Update old digest expectations.

### Verification commands

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
python -m pytest -q tests/test_topology_doctor.py -k "digest or navigation"
python scripts/topology_doctor.py --navigation --task "improve source code quality" --files src/foo.py --json
python scripts/topology_doctor.py --navigation --task "change settlement rounding" --files src/state/lifecycle_manager.py --json
python scripts/topology_doctor.py --navigation --task "misc cleanup" --files state/zeus_trades.db --json
```

Expected:

* first command: no data-ingestion hard profile from `source`;
* second: `scope_expansion_required` / `needs_planning_lock`;
* third: blocked because state DB is forbidden/canonical truth.

---

## 16. 绝对不要做的事

1. 不要只把 substring 改成 word-boundary。
   这只能减少一小部分 false positive，不能解决 `source code` 语义冲突。

2. 不要继续让 generic fallback allowed_files = requested files。
   这是权限提升。

3. 不要把更多 triggers 塞进 topology.yaml 来“调参”。
   这会让维护成本爆炸。

4. 不要让 `ok:true` 继续被解释成“scope is safe”。
   `ok` 必须拆语义。

5. 不要把 graph 当依据来补 admission。
   graph protocol 已经明确它是 derived context，不能替代 semantic boot、authority、planning lock、receipts。([GitHub][10])

6. 不要让 topology_doctor 继续用一个巨大测试文件承载所有 law。
   digest/admission 需要独立 adversarial suite。

7. 不要把 allowed_files 改成 hard gate 但不更新 AGENTS/workspace_map。
   online-only agents 会继续按旧 contract 行动。

---

## 17. 我对 Zeus topology 的更新后裁决

**Zeus topology 不是应该删除；它必须更严格地区分“建议我读什么”和“我被允许改什么”。**

现在的 topology system 在 manifest、module books、typed issue、map maintenance、graph protocol 方面已经有很多正确基础：module books 也明确 topology 是 routing kernel，failure modes 包括 compressed manifests、hidden obligations、flat issue shapes；`topology_doctor_system.md` 也已经描述 navigation direct/global drift split 和 typed metadata；`manifests_system.md` 已经强调每个 machine file 应拥有一种 fact type，而不是重复造 registry。([GitHub][11])

但 digest/admission 这层目前不合格。
更准确地说：

> **Zeus topology 的中后层治理方向是对的；前端 routing/admission 层是危险的。**
> 它现在把 route suggestion 当作 permission-looking output，把 text matching 当作 scope inference，把 fallback 当作 implicit authorization。
> 这必须先修，否则后续所有 module book rehydration、issue typing、closeout scoping 都可能建立在错误入口上。
