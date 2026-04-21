# README 评审记录

本文档追踪 `README.MD` 中关于「当前状态」「已实现能力」「下一步」等叙述与代码、文件系统、git 历史之间的一致性。每一版评审需标注评审日期、被评审文件状态、以及评审模型版本。

---

## v1 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交）
- **对照对象**：
  - `CLAUDE.md` (Version 2026-04-21.r1)
  - `scripts/notion_wiki_compiler.py`（工作树）
  - `schema/notion_wiki_mapping.example.json`（工作树）
  - `.env.example`（工作树）
  - 文件系统实际状态
- **锚定 git 状态**：`c5f452b`（HEAD） + 4 个未提交修改 + 1 个未追踪新文件
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`

### 1. 「当前状态」声明 vs 实际核对

| README 声明 | 实际状态 | 判定 |
|---|---|---|
| 双库配置 `NOTION_RAW_INBOX_DB_ID` / `NOTION_WIKI_DB_ID` 已建立 | `.env.example:7-10` 已拆，`main()` 两个变量都读 | ✅ 一致 |
| 脚本命令：`inspect-schema --database raw\|wiki` | `build_parser()` line 477-478 支持 | ✅ 一致 |
| 脚本命令：`search` / `upsert-note` / `compile-from-raw` / `lint` | 全部在 `build_parser` 中注册 | ✅ 一致 |
| 「检查 Raw Inbox 或 Wiki 数据库 schema」 | `inspect_schema()` 接受 `database_role`，主流程按 `--database` 选库 | ✅ 一致 |
| 「在 Wiki 数据库中搜索候选页面」 | `command_search` 固定走 `NOTION_WIKI_DB_ID` | ✅ 一致 |
| 「按标题执行最小 upsert」 | `upsert_note_to_wiki:293-296` 仍是 normalize-equal 标题比对 | ✅ 一致（也符合"限制"段自述） |
| 「从 Raw Inbox 指定 page 读取正文」 | `retrieve_block_children` + `read_page_body_text:186-193` | ✅ 一致 |
| 「编译后回写 `Status` / `Processed At` / `Target Wiki Page`」 | `command_compile_from_raw:406-420` 实现了三字段回写 | ✅ 一致 |

**结论**：「当前状态」和「已实现能力」两段与代码一致，未发现虚报。

### 2. 「当前限制」段自述 vs 实际核对

| 自述的限制 | 核对 | 判定 |
|---|---|---|
| 未做 LLM 抽取 | 脚本全是 `urllib` + 规则，无任何 LLM 调用点 | ✅ 准确 |
| 主要按标题匹配，未做 Canonical ID + Aliases 优先匹配 | `upsert_note_to_wiki` 仅 normalize-equal 标题，未查 canonical/aliases | ✅ 准确 |
| Raw body 只读第一层 block | `read_page_body_text` 无递归，遇到 toggle / column / children 会漏读 | ✅ 准确 |
| 无批量队列 | `compile-from-raw` 要求 `page_id` 单条 | ✅ 准确 |

**结论**：自述的限制诚实，无隐瞒。

### 3. DESIGN_REVIEW v1 修复清单进度

| v1 意见 | 状态 | 证据 |
|---|---|---|
| ① 拆双 DB id | ✅ 完成 | `.env.example` + `main()` |
| ② 加 `retrieve_block_children` + `compile-from-raw` | ✅ 完成 | `scripts/notion_wiki_compiler.py:86-90, 361-434` |
| ③ canonical_id / aliases 匹配 | ❌ 未做 | `upsert_note_to_wiki:293-296` 仍只看标题 |
| ④ Raw 状态回写 | ✅ 完成 | `command_compile_from_raw:406-420` |
| ⑤ 明确 LLM 抽取位置 | ❌ 未做 | 架构图、CLAUDE.md、README 都未标注 |
| ⑥ `search` 换成 `databases/{id}/query` | ❌ 未做 | `search_in_database:197` 仍用全局 `/search` |
| ⑦ 澄清 `raw/.sync_state.json` 与 Raw Inbox 关系 | ❌ 未做 | CLAUDE.md 目录树仍列该文件但实际不存在 |
| ⑧ 冲突 diff 标记 | ❌ 未做 | 仍只追加 heading + paragraph |

**完成率**：3/8。按影响权重实际覆盖了「让最小闭环能跑」的核心两项（①②④），剩下 5 项里 ③⑥ 才是下一阶段真正的瓶颈。

### 4. 「下一步」合理性评估

README 列的 5 步：

1. 跑 Raw / Wiki schema 检查
2. 根据真实字段名更新 mapping
3. 选一条 raw page 做第一次真实 `compile-from-raw`
4. 补 Canonical ID 和 Aliases 匹配
5. 再决定是否接入真正的 LLM 抽取与冲突处理

**合理的部分**：
- 顺序对。先对齐真实 schema → 再小规模跑通 → 再加强匹配 → 最后引入 LLM，属于典型「先把管道焊死，再往上游加智能」的路径。
- 步骤 3 是非常必要的烟雾测试，能一次性暴露 schema 不对、字段名不对、权限不对、分页不对等多个隐患，放在这个位置合适。

**需要调整的部分**：
- **步骤 4 隐含依赖步骤 6（未列出）**：Canonical ID / Aliases 匹配需要 `databases/{id}/query` + 属性过滤，这意味着要先完成 DESIGN_REVIEW v1 第⑥条。建议把「把 `search_in_database` 换成 `query_database` + 属性过滤」作为步骤 3.5 或并入步骤 4。否则在 `/search` 的基础上做 canonical id 匹配会越做越别扭。
- **步骤 5 的"再决定"措辞太软**：这是本项目叫"LLM Wiki"的唯一理由。应该在步骤 4 完成前明确：LLM 抽取放在（a）脚本内 call Anthropic API、（b）Claude Code 会话内由 agent 调工具、（c）其他路径，三选一。否则后续每次新增功能都要重新讨论归属。
- **缺一步「文档同步 + 提交」**：当前工作树已有 4 个文件改动未提交（`.env.example`、`CLAUDE.md`、`schema/...`、`scripts/...`）+ `README.MD` 未追踪。按 README 自己定的「每完成一个清晰阶段就提交一次」原则，此刻就应该有一笔 commit，把"双库拆分 + compile-from-raw + raw 回写"作为一个阶段封盘，再开下一步。建议把这一步列为**步骤 0**。

### 5. 未覆盖但需要指出的文档漂移

独立于「下一步」之外，几个需要同步修的小坑：

- **CLAUDE.md `Version` 未 bump**：`.env.example` 叙述、`Inspect Schema` 段措辞都已被改过，但版本头仍是 `2026-04-21.r1`。按 CLAUDE.md 自己的规则（line 4），这次修改应 bump 到 `2026-04-21.r2`。
- **CLAUDE.md 目录树虚报文件**：line 32 列了 `raw/.sync_state.json`，实际文件系统里不存在；line 22-38 也未列 `DESIGN_REVIEW.md`、`README.MD`、`.clinerules-*`、`wiki/index.md`。目录树应与真实状态对齐，或明确标注「规划中 / 运行时生成」。
- **DESIGN_REVIEW v1 的"评审对象"快照已过时**：v1 锚定 `cd4d3ee`，但针对它提的 8 条里有 3 条已落地，本文件就是 v1 的事实更新。建议后续在 `DESIGN_REVIEW.md` 追加 v2 块，以新的 commit SHA 为锚，重新盘点剩余 5 条。

### 6. 本版未覆盖

- 未实际调用 Notion API 验证 `compile-from-raw` 端到端行为，判断基于代码静读。
- 未评估 Notion rate limit、分页（`has_more` / `next_cursor`）、错误重试等运行时问题。
- 未评估 `extract_property_text` 对 relation / people / files 等类型的处理（当前会静默返回空串，可能藏坑）。

---

## v2 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— 较 v1 版本大幅改写，新增"当前已确认的数据库结构"、细化限制与下一步）
- **对照对象**：
  - `CLAUDE.md` (Version **2026-04-21.r2**)
  - `scripts/notion_wiki_compiler.py`（HEAD = `86e75bc`）
  - `schema/notion_wiki_mapping.example.json`（HEAD = `86e75bc`，未更新）
  - `.env.example`（HEAD = `86e75bc`）
  - 文件系统实际状态
- **锚定 git 状态**：`86e75bc`（HEAD，未推送到 origin） + README.MD 单文件未提交
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **与 v1 差异**：Wiki 数据库已被切换到另一个库，字段拓扑完全不同；Raw 回写字段类型已从 `rich_text` 升级为 `status / date / relation`；下一步从 7 步细化为 8 步，并吸收了 v1 的"先换 `query_database` 再做 canonical 匹配"建议。

### 1. README 新增事实 vs 实际核对

| README 声明 | 实际状态 | 判定 |
|---|---|---|
| Raw 库字段 `Name / Status / Processed At / Target Wiki Page / Source URL` | 仅以 README 自述为准（未经 API 验证） | ⚠️ 未独立验证 |
| Raw 回写字段类型已是 `status / date / relation` | 与脚本 `property_payload_for_value:203-225` 的 `relation` / `status` / `date` 支持吻合 | ✅ 技术前提成立 |
| 新 Wiki 标题字段为 `Source`（title），同时存在 `Source `（尾部空格、relation） | README 自述（未经 API 验证）；若属实则是命名缺陷 | ⚠️ 未独立验证 |
| 新 Wiki 已有 `Canonical ID / Aliases / Compounded Level / Topic` | 仅以 README 自述为准 | ⚠️ 未独立验证 |
| `Compounded Level` 当前是 `rich_text`（非 `number`） | 若属实，则 `command_compile_from_raw` 里 `--increment-compounded-level` 的 `{"number": current_number + 1}` 路径会在 Notion 端报 `validation_error` | ⚠️ 潜在运行时 bug |

### 2. README 未自报、但我发现的新漂移

1. **`schema/notion_wiki_mapping.example.json` 已与新 Wiki 严重脱钩**（核心问题）：
   - `title_property: "Name"` — 但 README 说新 Wiki 标题是 `Source`。按现状跑 `upsert-note` 或 `compile-from-raw`，`resolve_title_property_name` 会回退到 `detect_title_property`（读 schema 拿 title 类型字段），**运行时应能兜住**，但一旦有人把 mapping 显式传给脚本就会直接 fail。
   - `source_property: "Source"` — 和新 Wiki 的 title 字段同名。如果后续谁把 `Source` 当作关联字段来写，会误操作 title。
   - `verification_property: "Verification"`、`last_compounded_at_property: "Last Compounded At"` — 这两个字段新 Wiki 都还没有，README 已明示缺失，但 mapping 仍指向它们。一旦运行 `lint` 就会抛 `"Verification property not found"`。

2. **README "当前限制" 没把 schema mapping 脱钩列为限制**：这是目前最近的运行时风险点，应该和"Raw page body 只读第一层"并列写出。

3. **「推荐 commit 粒度」示例 vs 实际 git log 有出入**：README 示例里分了 `refactor: split raw and wiki env config`、`feat: add compile-from-raw command`、`feat: add raw status writeback` 三笔，但实际 `86e75bc` 一笔把三件事并在了一起。示例与现实的教训方向不同——要么修示例，要么下一笔真按粒度拆。

4. **CLAUDE.md 目录树仍存在 v1 指出的漂移**：`raw/.sync_state.json` 还列着但不存在，`DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `.clinerules-*` / `wiki/index.md` 仍未列入。虽然 `Version` 已按规则 bump 到 r2，但目录树内容未跟上。

### 3. DESIGN_REVIEW v1 八条清单进度（v2 回归）

| v1 意见 | v1 状态 | v2 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | 无变化 |
| ② 读 raw body | ✅ | ✅ | 无变化 |
| ③ canonical_id / aliases 匹配 | ❌ | ❌ | 未做，但新 Wiki 已有这俩字段，**技术前提具备** |
| ④ Raw 状态回写 | ✅ | ✅✅ | 字段类型从 `rich_text` 升级到 `status/date/relation`，**质量提升** |
| ⑤ 明确 LLM 抽取位置 | ❌ | ❌ | 未做 |
| ⑥ `/search` → `query_database` | ❌ | ❌ | 未做，但 README 步骤 6 已列入计划 |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ❌ | 未做 |
| ⑧ 冲突 diff | ❌ | ❌ | 未做 |

**完成率**：仍为 3/8，但④的落地质量显著提升；③的前置条件已具备。

### 4. 「下一步」8 步方案合理性

**合理的部分**：
- 整体节奏依然是「先整理 schema 再下游推进」，方向正确。
- 步骤 2（重命名 `Source `）、步骤 3（补 Verification / Last Compounded At）、步骤 4（`Compounded Level` 改 number）三件事在步骤 5（首条真实 compile-from-raw）之前做完，顺序合理——否则 smoke test 一定挂。
- 步骤 6 明确把 `/search` → `query_database` 前置到 canonical/aliases 匹配之前，采纳了 v1 review 建议。

**需要收紧的部分**：
- **步骤 1（更新 mapping）是紧迫工程债，建议标注"阻塞项"**：现状下 mapping 文件里至少 5 个属性名已和新 Wiki 不对应（title、source、verification、last_compounded_at、还有 Source 同名歧义）。建议把"更新 mapping"拆成 1a（先把 mapping 里已不存在的属性改成新 Wiki 字段或留空）+ 1b（等步骤 2/3/4 建完字段后再填完整）。
- **步骤 2 要同时改 mapping 和脚本**：重命名 Wiki 里的 `Source `（relation）时，如果后续 mapping 用它，还要确定新名字是什么（如 `Raw Sources`）。README 只说"改成一个不带尾部空格、语义明确的名字"但没点出下游的 mapping key 是 `source_property`（且当前指向 title 的 `Source`）——这是个连动问题。
- **步骤 5 建议加"dry-run"**：第一次真实 compile-from-raw 之前，先对 raw 和 wiki 各跑一次 `inspect-schema` 把当前属性类型打印对照，确认 mapping 命中正确，再实际写入；否则可能在 Notion 上留下脏页面要手工清理。
- **步骤 8"再决定"措辞不变**：v1 已指出；v2 继续不清晰。若这版仍不定，至少标注"到步骤 7 结束前必须定，否则阻塞 v3 planning"。

**缺的一步**：
- **步骤 0：把 HEAD 推到 origin，并提交 README.MD 这版改动**。当前 `86e75bc` 本地一个提交未推，README.MD 单文件未提交。按 README 自己定的 version-control 原则应先清工作树。

### 5. 同步建议 —— 顺带该修的旁事

- `schema/notion_wiki_mapping.example.json` 的 `title_property` 至少改成 `Source`（或删掉由 `detect_title_property` 自动发现）——mapping 文件目前是 v1 时代留下来的。
- `DESIGN_REVIEW.md` 建议新增 v2 块，以 `86e75bc` 为锚，把上面的 8 条进度表搬过去，作为评审的主文档（目前 v2 进度只写在 `README_REVIEW.md` 里）。
- 若 README 里关于字段结构的声明是从 `inspect-schema` 实际输出复制的，建议把那次输出也存一份在 `raw/notion_dumps/` 或 `schema/` 下，作为当时的只读快照——这是 CLAUDE.md 三级编译里 raw 层本来就该承担的职责，目前被"跳过"了。

### 6. 本版未覆盖

- 未调用 Notion API 验证 README 中声明的字段拓扑（title=`Source`、`Source ` 尾部带空格等）。
- 未评估 `Compounded Level` 为 `rich_text` 时 `--increment-compounded-level` 的实际失败模式。
- 未评估 relation 回写时 `property_payload_for_value` 对 id 合法性的防御（当前代码没做 id 格式校验）。

---

## v3 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— 较 v2 有重大 Wiki schema 清理）
- **对照对象**：
  - `CLAUDE.md` (Version 2026-04-21.r2，`cb0b27c`)
  - `scripts/notion_wiki_compiler.py`（`86e75bc`，未变）
  - `schema/notion_wiki_mapping.example.json`（`86e75bc`，未变）
- **锚定 git 状态**：`cb0b27c`（HEAD） + README.MD 单文件未提交
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **与 v2 差异**：Wiki 数据库 schema 已整理完毕：title 回到 `Name`、`Source` 成为唯一 relation 字段（尾部空格消失）、新增 `Verification` / `Last Compounded At`、`Compounded Level` 升级为 `number`。下一步从 8 步压回 5 步。

### 1. 重大状态变化

- v2 最大的风险点「Wiki schema 与 mapping 脱钩」**已整体消除**。原因：用户把 Wiki 库的字段改得和原 mapping 对齐，而不是改 mapping 去迁就 Wiki —— 这是更彻底的收敛方向。
- 原来的 `Source` (title) vs `Source ` (relation, 尾部带空格) 命名冲突**已消除**：title 回到 `Name`，只剩一个 `Source` 字段且是 relation。
- v2 列出的 Wiki 5 个类型/字段缺陷（`Verification` 缺 / `Last Compounded At` 缺 / `Compounded Level` 类型错 / `Source` 歧义 / title 错位）**全部落地**。

### 2. README 声明 vs 实际核对

| README 声明 | 实际状态 | 判定 |
|---|---|---|
| Wiki 标题 = `Name`（title） | 依赖 README 自述，未经 API 验证 | ⚠️ 未独立验证 |
| Wiki 已有 `Source`(relation) / `Canonical ID` / `Aliases` / `Compounded Level` / `Last Compounded At` / `Topic` / `Verification` | 依赖 README 自述 | ⚠️ 未独立验证 |
| `Compounded Level` 现在是 `number` | 若属实，脚本 `--increment-compounded-level` 路径（`{"number": current + 1}`）可正常工作 | ✅ 技术前提成立 |
| `Verification` 现在是 `status` | 脚本 `property_payload_for_value` 支持 status 类型写入，`lint` 过滤也不会再因字段不存在而崩 | ✅ 技术前提成立 |

### 3. `schema/notion_wiki_mapping.example.json` 实际对齐度（无需修改）

v2 时认定为重大债的 mapping 文件，现在因 Wiki schema 调整**实际已对齐**：

| mapping key | mapping 值 | 新 Wiki 中是否存在 | 判定 |
|---|---|---|---|
| `title_property` | `Name` | 是（title） | ✅ |
| `source_property` | `Source` | 是（relation） | ✅ |
| `canonical_id_property` | `Canonical ID` | 是 | ✅ |
| `verification_property` | `Verification` | 是（status） | ✅ |
| `compounded_level_property` | `Compounded Level` | 是（number） | ✅ |
| `last_compounded_at_property` | `Last Compounded At` | 是（date） | ✅ |
| `aliases_property` | `Aliases` | 是 | ✅ |
| `topic_property` | `Topic` | 是 | ✅ |

**结论**：README 步骤 1「根据真实字段名更新 mapping」其实**已经隐式完成**，下一次 commit 只需要把这句话改成「已核对 mapping 和真实 schema 对齐」即可。除非 `Aliases` / `Canonical ID` / `Topic` 的 Notion 类型和 `property_payload_for_value` 期望不同（例如 `Aliases` 是 multi_select 还是 rich_text 未定），这才需要在 mapping 层加 hint。

### 4. DESIGN_REVIEW v1 八条清单进度（v3 回归）

| v1 意见 | v2 状态 | v3 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | — |
| ② 读 raw body | ✅ | ✅ | — |
| ③ canonical_id / aliases 匹配 | ❌ | ❌ | 前置条件更完备（两个字段都已在 Wiki 落地） |
| ④ Raw 状态回写 | ✅✅ | ✅✅ | — |
| ⑤ 明确 LLM 抽取位置 | ❌ | ❌ | README 步骤 5 仍"再决定" |
| ⑥ `/search` → `query_database` | ❌ | ❌ | README 步骤 3 |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ❌ | CLAUDE.md 目录树仍未修 |
| ⑧ 冲突 diff | ❌ | ❌ | — |

**完成率**：仍 3/8，但下一步的路径更清晰 —— 现在挡在 ③ 前面的 schema 债已扫除。

### 5. 「下一步」5 步方案合理性

README 现列的 5 步：

1. 根据真实字段名更新 `schema/notion_wiki_mapping.example.json`
2. 选一条 raw page 做第一次真实 `compile-from-raw`
3. 将 `search_in_database` 改成 `query_database` 属性过滤
4. 补 `Canonical ID` 和 `Aliases` 匹配
5. 再决定是否接入真正的 LLM 抽取与冲突处理

**合理的部分**：
- 顺序依然对。schema → smoke → 改查询方式 → 强化匹配 → 引入 LLM。
- 步骤 3 前置于步骤 4 继承了 v1 / v2 review 的意见，保持住了。

**需要收紧的部分**：
- **步骤 1 可能已是 no-op**：见本版第 3 节 —— mapping 文件与真实 Wiki 字段已经对齐。建议把步骤 1 重写为「dry-run：跑一次 `inspect-schema --database wiki` 并对照 mapping，确认类型（特别是 `Aliases` / `Canonical ID` / `Topic` 的 Notion 类型）能被 `property_payload_for_value` 覆盖」—— 这是真正需要动手的事，而不是"改字段名"。
- **步骤 2 smoke test 之前最好先准备 rollback 机制**：`compile-from-raw` 一旦成功，会往 Wiki 里建新页、往 Raw 写 `Compiled` + relation。如果 smoke 页测脏了，没办法一键回滚。建议第一条 smoke 跑完后保留一个"已知 Compiled"的 raw page id 和对应 Wiki page id，用于后续幂等验证。
- **步骤 5「再决定」已连续三版未定**：v1 / v2 / v3 都提了。到步骤 4 完成时必须定，否则再下一版 review 会继续重复这条。

### 6. 仍未修的旧漂移（独立于下一步）

- **CLAUDE.md 目录树**：`raw/.sync_state.json` 仍列着但不存在；`DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `.clinerules-*` / `wiki/index.md` 仍未列入。v1、v2 都点过，v3 继续挂账。
- **`raw/notion_dumps/` 仍为空**：两次 Wiki schema 调整（切库 → 清理）的 `inspect-schema` 输出都没被保存为只读快照。这是 CLAUDE.md 三级编译里 raw 层本应承担的审计职责，但实际被跳过。建议下次跑 `inspect-schema` 时把 JSON 落盘到 `raw/notion_dumps/` 或 `schema/` 下，带时间戳文件名。
- **README.MD 「推荐 commit 粒度」示例与实际 git log 不吻合**：v2 指出过；最新的 2 笔（`d6fe4dd` / `cb0b27c`）倒是按粒度拆了，但示例里列的 4 条命令仍与实际 history 对不上。可以把示例改成本仓库最近的真实 commit 作为参照。

### 7. 本版未覆盖

- 未调用 Notion API 验证新 Wiki 8 个字段的真实类型是否与 README 声明一致（尤其是 `Aliases` / `Topic` 的类型未在 README 里点明 —— 可能是 multi_select、rich_text、relation 之一）。
- 未实际跑 `compile-from-raw` smoke test。
- 未评估 `Compounded Level` 升级为 `number` 后，若已有历史 `rich_text` 数据会如何迁移。
