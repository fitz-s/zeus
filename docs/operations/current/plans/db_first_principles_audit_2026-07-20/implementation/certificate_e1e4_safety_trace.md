# E1/E4 结算身份安全 trace(design-C 的 general-purpose 助手交件)

design-C lane 的 receipt_hash / opportunity_book 溯源。**这是 E1 落地的 load-bearing 安全证据。** 与 design-C 合并版 `certificate_v1_freeze.md` 配套(哈希清单表 + golden-vector harness + v2 envelope 在那份)。

## TRACE 1 — E1(4 个 zlib_b64 字段 → BLOB 列):**SAFE**
零 (C)-类(全 recompute+compare)发现,遍 src/ 与 tests/。

**杀手证据**:`global_batch_runtime.py:1783` 对 **FULL receipt**(zlib_b64 内联)算 receipt_hash,**在** compaction 块(1806-1811 为 no-trade 行剥离同 4 字段)**之前**。receipt_hash 不在 `_GLOBAL_AUCTION_HEAVY_RECEIPT_FIELDS`(94-101)。→ compacted 行今天就持久化一个**无法从自身 stored JSON 重算**的 receipt_hash——construction 时一次性 stamp 的 opaque 值。生产中此不变量已成立;E1 只是把同 4 字段挪到别的存储机制,下游零变化。

**每个 reader 分类**:
- `global_batch_runtime.py:1219-1226` — (A) stored receipt_hash 字符串比对 + (B) sha256(raw) vs component.sha256(解压字节内容)
- `live_health.py:5129-5135` — (A) 仅字符串比对
- `live_health.py:5101-5104,5202-5205` — (B) 内容哈希 vs candidate_evaluations_sha256,不碰 receipt_hash
- `event_reactor_adapter.py:1266-1416` — (B) 内容哈希 vs book_native_side_states_sha256;receipt_hash 根本没 SELECT
- `portfolio.py / command_recovery.py / replay.py` — **零** receipt_hash/zlib_b64/global_single_order_auction(grep 证实,根本不是此 receipt 的 reader)
- `tests/integration/test_w3_solve_seam_g3.py:1347` — tamper 测试证 (A) 防御活着;`:1668-1675` 唯一全重算+比对处是**独立的 preflight receipt**(无 zlib_b64 字段,E1 范围外)

**同名但不相交的 receipt_hash 系统**(读 preimage 证实非仅 grep):`FamilyDecision.receipt_hash`(family_decision_engine.py:2479-2534 + solver.py:5272-5275,流入 qkernel_receipt_hash 与 ~130 字段 current_state_identity_hash,**无 zlib**);no_submit_receipts sha256(receipt_json);execution_receipt_hash/certificate_hash 链;peer 提的 cost_basis_hash/executor_native_intent_hash/replacement_probability_bundle_hash——全部 preimage 不相交,无 zlib/receipt_hash 重叠。

**receipt_hash 是否被拷进 decision_certificates**:global-auction 自身 receipt_hash **NO**(`_store_global_auction_receipt` 唯一 caller global_batch_runtime.py:3632 丢弃 hash 只留 row_id;GlobalBatchSubmitResult.receipts 是不同类型无此字段)。被拷进证书的是不相关的 FamilyDecision hash——所以"只绑字符串,E1 无碍"的 caveat 都用不上,根本不是同一个 hash。

**非阻塞机械改动**(非安全相关):需存储路径重接的是 live_health.py 的 3 个 decode/reference 函数 + event_reactor_adapter.py:1281-1317 对 book_native_side_states_zlib_b64 的原生 SQL json_extract。

## TRACE 2 — E4(opportunity_book 摘要):**确认 read-only evidence**
payload 在 event_reactor_adapter.py:18242(`_actionable_payload_from_receipt` 18084-18089)得该 key,流入 build_actionable_trade_certificate(17178-17182 → certificates/action.py:14-23,certificate_type=ACTIONABLE_TRADE)。唯一构造路径(claims.ACTIONABLE_TRADE 全 src 仅现一次,action.py:15;build_actionable_trade_certificate 仅一 caller)。`grep -in opportunity_book decision_kernel/verifier.py` = 零(查两次)。正面佐证:verify_actionable_trade(verifier.py:212-246)与 _verify_execution_command/_verify_final_intent_payload 从同一 payload dict cherry-pick ~15 个别的字段,从不碰 opportunity_book。

两条 trace 均无 UNCERTAIN。

## team-lead 合流裁决
- **E1 SAFE 落地**(仍先冻 golden vectors 兜底其它 receipt 系统)——W3 新写/relocate,不需等 v2 全套。
- **E4 仍需 v2 版本化**:trace 证 verifier 不读该字段(evidence),但 round-2 已定 `payload_hash=stable_hash(payload)` 承诺整个 payload 含 opportunity_book → 原地摘要改 payload_hash→certificate_hash→毁身份。故 E4 = 证书 v2 迁移,v1 永不原地重写。evidence 性质只是说明摘要**语义上安全**(无 verifier 依赖),哈希绑定要求它走版本化。
