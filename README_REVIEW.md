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

---

## v4 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— 已纳入 smoke test 实测结果）
- **对照对象**：
  - `CLAUDE.md` (Version 2026-04-21.r2)
  - `scripts/notion_wiki_compiler.py`（工作树）
  - `schema/notion_wiki_mapping.example.json`（工作树）
  - Notion API 实测结果（raw/wiki schema + 一次真实 `compile-from-raw`）
- **锚定 git 状态**：`cb0b27c`（HEAD） + `README.MD` / `README_REVIEW.md` / `schema/notion_wiki_mapping.example.json` / `scripts/notion_wiki_compiler.py` 未提交
- **评审者**：GPT-5 Codex

### 1. 本版新增事实

- 一次真实 smoke test 已完成，且闭环成功：
  - raw page：`3496e2cd-6e4f-81a2-988a-c9fd757c1b42`
  - wiki page：`3496e2cd-6e4f-816e-919b-fd80753dcc9b`
- raw 回写成功覆盖：
  - `Status`
  - `Processed At`
  - `Target Wiki Page`
- `raw_compiled_status` 已从 `Compiled` 修正为 `Done`，以匹配 Raw Inbox 当前 status 选项。
- `compile-from-raw` 暴露并修复了一处实现 bug：
  - `.env` 中无连字符数据库 ID 与 Notion API 返回的带连字符 parent id 做字符串直比较，导致误判“Raw page does not belong to NOTION_RAW_INBOX_DB_ID”
  - 现已通过 `normalize_notion_id()` 修复

### 2. README 声明 vs 实际核对

| README 声明 | 实际状态 | 判定 |
|---|---|---|
| 第一次真实 `compile-from-raw` smoke test 已通过 | 已由 Notion API 实测验证 | ✅ |
| 闭环 `raw -> wiki -> raw 回写` 已验证 | 已由实测结果验证 | ✅ |
| `raw_compiled_status` 对应 Raw 状态选项 | 当前 mapping 已写成 `Done`，与 Raw status 选项一致 | ✅ |

### 3. 剩余关键问题

按优先级排序：

1. **搜索仍用全局 `/search`**：这仍是下一阶段最该改的点，否则 canonical/aliases 匹配会继续受限。
2. **匹配仍以标题为主**：虽然 schema 已就位，但 `Canonical ID + Aliases` 逻辑还没实现。
3. **幂等性策略还没定义**：同一 raw page 重跑会更新既有 wiki 页，但 README 还没说明 smoke test 的回滚/复跑约定。
4. **LLM 抽取位置仍未定**：这条旧问题仍然存在。

### 4. README 当前是否准确

当前 README 已从“准备 smoke test”更新为“smoke test 已通过”，这比 v3 更接近真实状态。未发现新的虚报。

---

## v5 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— 含 smoke test 通过记录与限制/下一步改写）
- **对照对象**：
  - `CLAUDE.md` (Version 2026-04-21.r2)
  - `scripts/notion_wiki_compiler.py`（工作树 —— 新增 `normalize_notion_id`）
  - `schema/notion_wiki_mapping.example.json`（工作树 —— `raw_compiled_status` 改为 `Done`）
  - `README_REVIEW.md` v4（由 GPT-5 Codex 追加）
  - Notion API 实测结果（由 v4 报告）
- **锚定 git 状态**：`a833892`（HEAD） + 4 个未提交修改（`README.MD` / `README_REVIEW.md` / `schema/...` / `scripts/...`）
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **相对 v4 定位**：v4（Codex）已记录 smoke test 事实、ID 归一化 bug 修复、`raw_compiled_status` 调整；本版以 Opus 4.7 视角**补全 v4 未覆盖的部分**——DESIGN_REVIEW 八条清单进度、旧挂账债务回归、smoke artifact 持久化缺口、代码细节复核。两版并存、互为补充。

### 1. 对 v4 事实的独立复核

- ✅ `normalize_notion_id` 函数确已在脚本中落地（`:134-137`）。
- ✅ 应用点有两处：`search_in_database:204`（顺带加固，未在 v4 提及）和 `command_compile_from_raw:376`（v4 记录的 bug 修复点）。这是**正确的扩展**——原 `/search` 路径也会遭遇相同比较问题，提前修好。
- ✅ `raw_compiled_status: "Done"` 在 `schema/notion_wiki_mapping.example.json:15`。
- ⚠️ v4 未评估的一点：`compile-from-raw` 里 `source_url` 如果是 relation 或 rollup 类型属性，`extract_property_text:156-171` 会静默返回空串。这在 smoke test 对 `Source URL`（rich_text 或 url）场景下不会暴露，但换一个 raw page 可能会踩到。

### 2. DESIGN_REVIEW v1 八条清单进度（v5 回归）

| v1 意见 | v3 状态 | v5 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | — |
| ② 读 raw body | ✅ | ✅ | — |
| ③ canonical_id / aliases 匹配 | ❌ | ❌ | README 步骤 2 |
| ④ Raw 状态回写 | ✅✅ | ✅✅✅ | smoke test 真机验证通过，质量再提升 |
| ⑤ 明确 LLM 抽取位置 | ❌ | ❌ | README 步骤 4 仍"再决定"；**连续四版挂账** |
| ⑥ `/search` → `query_database` | ❌ | ❌ | README 步骤 1 |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ❌ | CLAUDE.md 目录树仍未修 |
| ⑧ 冲突 diff | ❌ | ❌ | README 限制段新增"幂等性与冲突处理策略"，间接认账 |

**完成率**：仍 3/8，但 ④ 从"代码支持"进化到"真机验证"，质量继续提升。

### 3. smoke test 后暴露的审计缺口

README 把 smoke test 的 raw page id / wiki page id 写进了正文（`README.MD:93-95`），但：

- **没有落盘原始输出**：`compile-from-raw` 的 stdout JSON、`inspect-schema` 的 schema 快照都应该按时间戳写到 `raw/notion_dumps/` 或新建 `schema/snapshots/`。`raw/notion_dumps/` 目前仍空（v1/v2/v3 挂账），这违反 CLAUDE.md「三级编译 / raw 层审计」的设计本意。
- **smoke test 的复现路径不存在**：README 只记录了 page id 和"已通过"，没有记录输入数据、环境变量、命令参数、执行时间。下次有人想复跑（或验证是否回归），只能重新摸索。建议补一个 `SMOKE_TESTS.md` 或把这次运行日志直接贴进 `raw/notion_dumps/2026-04-21-smoke.json`。

### 4. 「下一步」4 步方案合理性

README 新列的 4 步：

1. `search_in_database` 改 `query_database`
2. 补 `Canonical ID` / `Aliases` 匹配
3. 增加 smoke test 的幂等性检查与回滚说明
4. 再决定是否接入真正的 LLM 抽取与冲突处理

**合理的部分**：
- 步骤顺序严格继承了 v1 / v2 / v3 review 的建议——`query_database` 前置于 canonical 匹配。
- 新增步骤 3（幂等性 + 回滚）是直接回应 v3 review 的"smoke test rollback 锚点"建议，已落地。

**需要收紧的部分**：
- **步骤 3 的"回滚说明"应包含具体的反向操作**：重跑同一 raw page 会怎样？是 upsert 语义（同标题更新）还是重复建页？这里依赖 `upsert_note_to_wiki` 只按 normalize-equal 标题匹配——如果 smoke 产生的 wiki 页标题 = raw 标题，重跑会命中并 append 新的 `增量更新` 段，不会重建；但 append-only 语义本身就是 DESIGN_REVIEW v1 第⑧条(冲突 diff)的债。建议步骤 3 先明确「reset 方式 = 手工到 Notion 删除指定 page id」或「软回滚 = 把 Raw Status 改回 New 后重跑」。
- **步骤 4「再决定」已连续四版挂账**（v1-v5 都提）：到步骤 2 完成前必须定，否则 v6 review 继续重复这条。可以考虑直接开一张 `LLM_EXTRACTION_DESIGN.md` 来收敛决策。

### 5. 仍未修的旧漂移（独立挂账清单）

- **CLAUDE.md 目录树**（v1/v2/v3 连续挂账）：`raw/.sync_state.json` 仍列着但不存在；`DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `.clinerules-*` / `wiki/index.md` 仍未列入。此次 README 改动未动 CLAUDE.md，目录树继续漂。
- **`raw/notion_dumps/` 仍为空**（v2/v3/v5 挂账）：见本版第 3 节。
- **commit 粒度示例 vs 实际 git log**（v2/v3 挂账）：README 示例的 4 条 fake commit 仍在，与实际 `a833892` / `ab534f7` / `cb0b27c` / `d6fe4dd` / `86e75bc` 不对应。

### 6. 关于多模型评审

本文档现已同时存在 Opus 4.7（v1/v2/v3/v5）与 GPT-5 Codex（v4）的评审。建议：

- **保留多模型视角**：不同模型的覆盖盲点不同。v4 专注事实记录，v1-v3/v5 更重进度表与挂账追踪。并存是信号，不是冗余。
- **评审者字段应包含模型标识**：v4 写的是"GPT-5 Codex"，v1-v3/v5 是"Claude Opus 4.7"，目前已做到。后续评审建议统一补充具体模型 ID / 日期 / 会话锚定，方便追溯每条意见的出处。

### 7. 本版未覆盖

- 未独立验证 smoke test 成功的 page id 是否真存在于 Notion（信任 v4 的实测结论）。
- 未评估 `normalize_notion_id` 是否覆盖其他 ID 比较路径（如 `wiki_result["page_id"]` 作为 relation 传回时是否需要归一化—— Notion API 接收 relation id 时两种格式都接受，所以应该不需要，但值得 smoke 多条后再确认）。
- 未评估重跑同一 raw page 时 `Target Wiki Page` relation 回写是否会生成重复项（Notion relation 默认是 set 语义，应当自动去重，但未实测）。

---

## v6 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交） + `scripts/notion_wiki_compiler.py`（工作树，大幅重写）
- **对照对象**：
  - `CLAUDE.md` (Version 2026-04-21.r2)
  - `schema/notion_wiki_mapping.example.json`（HEAD = `415c759`）
- **锚定 git 状态**：`415c759`（HEAD） + 2 个未提交修改（README.MD / scripts/...）
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **与 v5 差异**：脚本完成了 DESIGN_REVIEW v1 第⑥条（`/search` → `query_database`）和第③条（canonical_id + aliases 匹配）的真实落地；同时 proactively 加了分页、`unique_id` 属性类型支持。这是自 v1 以来**单次跨度最大的代码跃进**。

### 1. 本版最重要的事实：DESIGN_REVIEW v1 两条核心瓶颈已落地

- **⑥ `/search` → `query_database`**：
  - `search_in_database` 完全重写（`:290-314`），改用 `query_database_pages` + `title.contains` + `aliases.rich_text.contains` 的 `or` 过滤。
  - 新增 `query_database_pages`（`:210-227`）内置 `has_more` / `next_cursor` 分页循环 —— v1/v5"未覆盖"清单里列的运行时隐患被顺手解决。
- **③ canonical_id + aliases 匹配**：
  - 新增 `find_pages_by_canonical_id`（`:274-288`），`upsert_note_to_wiki`（`:393-416`）现在按"canonical_id 命中 → title+aliases 候选"的顺序查找，与 CLAUDE.md Agent 指令基线完全一致。
  - 新增 `split_aliases`（`:246-254`）用 `[,;\n|/、，；]+` 分隔，覆盖中英常用写法。
  - 新增 `page_matches_query`（`:257-271`）把 title-equal 和 alias-in-set 两种命中逻辑统一。
  - `extract_property_text` 新增 `unique_id` 类型分支（`:178-184`）—— 如果 Notion 里 `Canonical ID` 建成 unique_id 类型，能直接取到 `prefix + number` 字符串。

**DESIGN_REVIEW v1 完成率**：从 3/8 跃升到 **5/8**。

### 2. DESIGN_REVIEW v1 八条清单进度（v6 回归）

| v1 意见 | v5 状态 | v6 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | — |
| ② 读 raw body | ✅ | ✅ | — |
| ③ canonical_id / aliases 匹配 | ❌ | ✅ | **本版落地** |
| ④ Raw 状态回写 | ✅✅✅ | ✅✅✅ | — |
| ⑤ 明确 LLM 抽取位置 | ❌ | ❌ | **连续 5 版挂账** |
| ⑥ `/search` → `query_database` | ❌ | ✅ | **本版落地** |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ❌ | CLAUDE.md 目录树仍未修 |
| ⑧ 冲突 diff | ❌ | ❌ | smoke 重跑仍会 append，不做 diff |

### 3. README 内部不一致（需立刻修）

这是本版唯一硬漂移：

- **「下一步」步骤 1 "将 `search_in_database` 改成 `query_database` 属性过滤" 已过期**：代码本次已完成。但限制段里又写了「`query_database` 已替代全局 `/search`」，两处矛盾。
  - 建议删除步骤 1，把后面三步向上提：① 幂等性 + 回滚、② 更强的候选排序/冲突/合并策略、③ LLM 决策。
  - 或者把步骤 1 改写为「给 `find_pages_by_canonical_id` 换成 query_database 的属性 filter，减少全表扫」—— 当前实现是"拉全库后 python 过滤"，大库下会有性能债（见本版第 4 节）。

### 4. 代码质量观察（非硬错但值得标注）

- **`find_pages_by_canonical_id` 是全表扫**（`:274-288`）：`query_database_pages(client, database_id, {})` 无 filter 会拉全部 page。如果 `Canonical ID` 是 `rich_text` 或 `unique_id`，更干净的做法是用 `{"property": canonical_property, "rich_text": {"equals": canonical_id}}` 或 `{"unique_id": {"equals": ...}}` 让 Notion 端过滤。当前实现正确但不高效，建议列为下一步技术债。
- **`search_in_database` 过滤器对 aliases 假设类型是 `rich_text`**（`:312`）：如果真实 Notion 里 `Aliases` 被建成 `multi_select`，这个过滤会直接报 `validation_error`。README 未说明 `Aliases` 的类型，需要 schema 探测回填。建议从 mapping 里读 `aliases_property_type` 作为 hint，或 runtime 根据 schema 判断。
- **canonical 分支里"候选为空即 fallback"的语义**（`upsert_note_to_wiki:402-405`）：如果 `canonical_id` 传入但库里真没匹配，会**静默 fallback 到标题搜索**。这在"新建"场景下是对的（找不到就建新页），但在"用户显式传 canonical_id 期望精确命中"场景下会误合并到同名不同实体的页。建议加一个 `--strict-canonical` flag 或至少在 fallback 时 print 一行 warning。
- **分页循环无上限**（`:220-225`）：big-DB + 故意构造的 pathological page 可能让这里跑很久，目前没有 `max_pages` 保护。v1 里列过这点，现在分页做了但没加熔断，下一步补。

### 5. README 其余部分 vs 代码核对

| README 声明 | 实际状态 | 判定 |
|---|---|---|
| "在 Wiki 数据库中进行数据库内候选检索" | `query_database_pages` 只查 `database_id` 指定库 | ✅ |
| "优先按 `Canonical ID` 匹配（如果传入）" | `upsert_note_to_wiki:402-403` 先跑 `find_pages_by_canonical_id` | ✅ |
| "其次按标题与 `Aliases` 检索候选" | `search_in_database` 的 `or` 过滤含 title + aliases | ✅ |
| "已有 `Canonical ID + Aliases` 基础匹配" | 与代码一致 | ✅ |
| "还没有更完整的候选排序、冲突判断和合并策略" | `upsert` 仍是"命中就 append"，无 diff / 无冲突标注 | ✅ 诚实自述 |

### 6. 仍未修的旧漂移（独立挂账清单）

- **CLAUDE.md 目录树**（v1/v2/v3/v5/v6 连续挂账）：`raw/.sync_state.json` 仍列但不存在；`DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `.clinerules-*` / `wiki/index.md` 仍未列入。
- **`raw/notion_dumps/` 仍为空**（v2/v3/v5/v6 挂账）：schema 快照、smoke test 原始输出仍未落盘。现在脚本复杂度上去了，反而**更需要**这份 raw audit——否则下次回归时不知道当初到底打了哪个 `Aliases` 类型的 Notion 库。
- **commit 粒度示例 vs 实际 git log**（v2/v3/v5 挂账）：README 示例未更新。
- **DESIGN_REVIEW v1 第⑤条（LLM 抽取位置）连续 5 版挂账**：现在已经完成 ③⑥，下一步要做"更强的候选排序、冲突判断和合并策略"本质上就是 LLM 能力的触发点。第⑤条不再是"将来要做"而是"马上卡脖子"。强烈建议开 `LLM_EXTRACTION_DESIGN.md` 先定策略。

### 7. 「下一步」合理性（修正 README drift 后）

假设删除 stale 的步骤 1，实际下一步应该是：

1. 幂等性 + 回滚说明（README 原步骤 2）
2. 更强的候选排序 / 冲突判断 / 合并策略（README 原步骤 3）—— **必须先定 LLM 归属**
3. 决定 LLM 抽取位置（README 原步骤 4）

三步之间有强依赖：步骤 2 的"冲突判断"没有 LLM 支撑时做不出 DESIGN_REVIEW v1 第⑧条想要的 "diff 保留证据" 效果。**建议把步骤 3 前置到步骤 2 之前**——LLM 位置定了，才知道冲突 diff 是在脚本层做（call Claude API）还是在 Claude Code 会话里做（human-in-the-loop）。

### 8. 本版未覆盖

- 未实测 `query_database` 的 title/rich_text `contains` 过滤在 Unicode / 空格 / 特殊字符场景下的行为。
- 未评估 `Aliases` 实际类型（仍依赖 README 自述）。
- 未评估 `find_pages_by_canonical_id` 在大库（>1000 页）下的延迟。
- 未独立运行新 `search_in_database`，若 `aliases_property` 在真实 Wiki 里是 `multi_select`，代码会在 runtime 报错。

---

## v7 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— 已修复 v6 指出的“下一步”漂移）
- **对照对象**：
  - `README_REVIEW.md` v6
  - `scripts/notion_wiki_compiler.py`（工作树）
- **锚定 git 状态**：当前工作树仅 README / README_REVIEW 文档改动
- **评审者**：GPT-5 Codex

### 1. 本版修复

- v6 指出的硬漂移“README 下一步第 1 条仍写 `search_in_database -> query_database`，但代码已完成”已经修复。
- README 的后续顺序已调整为：
  1. 幂等性与回滚说明
  2. 明确 LLM 抽取位置
  3. 更强的候选排序 / 冲突处理 / 合并策略
  4. 递归读取 raw body 与批量编译队列

### 2. 当前判断

- README 现在与代码状态更一致。
- 最新的主瓶颈已经不再是 schema 或数据库查询，而是：
  - 幂等性策略
  - 冲突处理策略
  - LLM 归属决策

### 3. 仍待后续处理

- `Aliases` 真实类型仍建议再核对一次，避免 rich_text 假设与库结构不符。
- `find_pages_by_canonical_id` 仍是全库扫描，未来需要改成 Notion 端属性过滤。

---

## v8 · 2026-04-21

- **评审对象**：`README.MD`（工作树，未提交 —— Codex 本轮新增「幂等性与回滚」章节，重写下一步 4 步）
- **对照对象**：
  - `README_REVIEW.md` v6（Opus）+ v7（Codex）
  - `scripts/notion_wiki_compiler.py`（`bcfe737`，未变）
  - `schema/notion_wiki_mapping.example.json`（`bcfe737`，未变）
- **锚定 git 状态**：`bcfe737`（HEAD） + `README.MD` / `README_REVIEW.md` 未提交（均为 Codex 的工作产物与其 v7 追加）
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **本版定位**：Opus 对 Codex v7 的独立复核，并对 Codex 新版 README 做独立评审。两模型分工：Codex 负责 README 主文档，Opus 负责 review 类文档。

### 1. 对 Codex v7 的独立复核

v7 正文描述了 README 下一步新顺序是：
1. 幂等性与回滚说明
2. 明确 LLM 抽取位置
3. 更强的候选排序 / 冲突处理 / 合并策略
4. 递归读取 raw body 与批量编译队列

**复核结果：v7 对下一步顺序描述有误**。实际 README 下一步（`README.MD:157-161`）是：
1. **明确真正的 LLM 抽取放在哪一层执行**（不是"幂等性与回滚"）
2. 补更强的候选排序、冲突处理与合并策略
3. 把"文档约定"升级成代码层幂等策略
4. 继续扩展 raw page body 的递归读取与批量编译队列

两处差异：
- v7 把"幂等性与回滚"列为下一步第 1 条；实际 README 里这是**独立的 H2 章节**（`README.MD:98-132`），不属于下一步。
- v7 把"LLM 抽取位置"排在第 2；实际 README 把它**提升到了第 1 位**——这才是本版最值得注意的一点（见本版第 3 节）。

Codex v7 没捕捉到"LLM 抽取位置"从连续 6 版挂账晋升为首位 next step 的事实。

### 2. Codex 本版对 README 的两处主要改动复核

#### 2.1 新增「幂等性与回滚」章节（`README.MD:98-132`）

**正面评价**：
- 准确描述当前 `upsert` 是 append-only 语义：重跑会追加内容而不重建页面。
- 明确 smoke test 约定（raw/wiki page id 已登记），并建议不要反复对这条 raw page 重跑。
- 给出具体手工回滚三步（删 wiki 页 / 重置 Raw 状态 / 新建 raw page 复跑），可操作性强。
- 承认"没有自动回滚能力"，没虚报。

**值得收紧的点**：
- "Status 改回非完成态"没指定具体值。当前 Raw Inbox 的 status 选项未在 README 中列齐；按 `schema/notion_wiki_mapping.example.json:15` 已知 `Compiled → Done`，但"改回"的目标态（`New`？`Pending`？还是其他？）未点名。若 Codex 下一版加上具体状态名会更完整。
- "清空 Processed At / Target Wiki Page" 在 relation / date 类型下的具体 API payload 没说明。用户手工到 Notion UI 清空没问题；但如果后续脚本化"重置"这条 raw，需要记住 relation 清空是 `{"relation": []}`、date 清空是 `{"date": null}`。可以考虑在 `scripts/` 里加一个 `reset-raw-page` 子命令支撑 smoke 回归。
- 章节没说明 wiki 页上**已 append 的多段「增量更新」block** 该怎么处理。当前 append-only 语义下，即使删了 wiki 页从头再来，那些 block 也随 wiki 页一起删；但若只想保留 wiki 页、回退某次 append，现在没办法。

#### 2.2 下一步重写：4 步（`README.MD:155-161`）

新顺序：LLM 位置 → 候选排序/冲突/合并 → 代码层幂等 → raw body 递归 + 批量队列。

**正面评价**：
- **LLM 位置被从 "再决定" 升级为 step 1**——这是 v1/v2/v3/v5/v6 review 连续 5 版挂账的事实终于被直面。
- 顺序是对的：LLM 策略定下来后，step 2 的"冲突判断与合并"才有实现锚（DESIGN_REVIEW v1 第⑧条"diff 保留证据"本质需要 LLM 支撑）。
- v6 review 指出的 stale step 1（`search_in_database → query_database`）已被删除。

**值得收紧的点**：
- Step 4（"递归读取 raw body 与批量编译队列"）是两件事：递归读 block children 是修 bug（当前只读第一层），批量队列是新建能力。粒度不均衡，建议拆成 4a/4b。
- Step 3（"把文档约定升级成代码层幂等"）和 step 2（"候选排序+冲突处理+合并策略"）有重叠：严格幂等需要先识别"这次是同一个逻辑更新"，而这正是 step 2 的合并策略要解决的。建议明示依赖：step 3 依赖 step 2 先定义"同一个内容"的判据。

### 3. DESIGN_REVIEW v1 八条清单进度（v8 回归）

| v1 意见 | v6 状态 | v8 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | — |
| ② 读 raw body | ✅ | ✅ | — |
| ③ canonical_id / aliases 匹配 | ✅ | ✅ | — |
| ④ Raw 状态回写 | ✅✅✅ | ✅✅✅ | — |
| ⑤ 明确 LLM 抽取位置 | ❌ | ⚠️ **进行中** | 从连续 6 版"再决定"升级为 README step 1 —— 第一次从挂账进入活跃工作队列 |
| ⑥ `/search` → `query_database` | ✅ | ✅ | — |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ❌ | CLAUDE.md 目录树仍未修 |
| ⑧ 冲突 diff | ❌ | ⚠️ **部分文档层回应** | 新增「幂等性与回滚」章节是**文档层**的"保留证据"半解，代码层 diff 仍未做 |

**完成率**：5/8 硬完成 + 1 进行中 + 1 文档层部分解。实质进展：⑤ 终于不再挂账，⑧ 的精神方向被用"手工 rollback 约定"先占了位。

### 4. 仍未修的旧漂移（独立挂账清单）

- **CLAUDE.md 目录树**（v1/v2/v3/v5/v6/v8 连续挂账）：`raw/.sync_state.json` 仍列着但不存在；`DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `.clinerules-*` / `wiki/index.md` 仍未列入。
- **`raw/notion_dumps/` 仍为空**（v2/v3/v5/v6/v8 挂账）：schema 快照、smoke test stdout 仍未落盘。本版 README 里登记的 raw/wiki page id 本应该配一份 `raw/notion_dumps/2026-04-21-smoke.json`，把输入 / 环境 / 命令参数 / API 响应原文保留，后续做回归测试时能直接对账。
- **commit 粒度示例 vs 实际 git log**（v2/v3/v5/v8 挂账）：README 示例仍未按本仓库真实 commit 更新。

### 5. 代码层技术债（v6 标注，v8 回归）

本版无代码改动，下列债与 v6 一致、仍未偿还：

- `find_pages_by_canonical_id` 全表扫（`scripts/notion_wiki_compiler.py:274-288`）
- `search_in_database` 硬编码 aliases 为 `rich_text` 类型（`:312`）
- `query_database_pages` 分页循环无 `max_pages` 熔断（`:220-225`）
- `extract_property_text` 对 relation / people / files / formula / rollup 等类型仍静默返回空串（`:156-184`）

### 6. 本版未覆盖

- 未独立验证「幂等性与回滚」章节手工步骤在真实 Notion UI 下是否完整（尤其 Target Wiki Page relation 清空的操作路径）。
- 未评估 Codex v7 的其它观察是否有更多漂移——本版只对"下一步顺序"做了精确复核。
- 未讨论 README 版本号是否需要自己的版本头（类比 CLAUDE.md 的 Version 机制）。当前 README 每次大改都没有显式版本号，review 只能用 commit SHA 追踪；若后续多轮评审频率更高，README 加一个类似 `Version: 2026-04-21.rN` 的头会更方便检索。

---

## v9 · 2026-04-22

- **评审对象**：两份新设计文档 + README.MD 未提交部分
  - `LLM_EXTRACTION_DESIGN.md`（全新，未追踪）
  - `MERGE_STRATEGY.md`（全新，未追踪）
  - `README.MD`（工作树，未提交 —— 在"当前关键文件"中引用新两份文档）
- **对照对象**：
  - `scripts/notion_wiki_compiler.py`（`ecdfddc`，未变）
  - `schema/notion_wiki_mapping.example.json`（`ecdfddc`，未变）
  - DESIGN_REVIEW v1 / README_REVIEW v1-v8
- **锚定 git 状态**：`ecdfddc`（HEAD，v8 后 Codex 又推了一笔「refine idempotency and next steps」） + 2 个未追踪新文件 + README.MD 未提交
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **本版定位**：对 Codex 本轮产出的两份设计文档做内容审核 + DESIGN_REVIEW v1 回归。这是**v1 提出的 8 条意见里第一次同时推进⑤⑧两条长期挂账的版本**。

### 1. DESIGN_REVIEW v1 第⑤⑧两条长期挂账已获文档层解

| v1 意见 | v8 状态 | v9 状态 | 变化 |
|---|---|---|---|
| ⑤ 明确 LLM 抽取位置 | ⚠️ 进行中 | ✅ **决策文档已产出** | `LLM_EXTRACTION_DESIGN.md` 正式定调："LLM 放在 Claude Code 会话层，脚本不接入模型 API" |
| ⑧ 冲突 diff | ⚠️ 文档层部分回应 | ✅ **策略文档已产出** | `MERGE_STRATEGY.md` 给出 4 tier 候选排序 + 3 档风险等级 + 停下来问用户条件；代码层未实现，但判据已稳定 |

**DESIGN_REVIEW v1 完成率**：
- 文档层：**7/8**（剩 ⑦ CLAUDE.md 目录树未修）
- 代码层硬完成：5/8（①②③④⑥；⑤ 明确不做进脚本所以"代码层不必做"；⑧ 策略已定但代码未实现）

### 2. `LLM_EXTRACTION_DESIGN.md` 内容审核

**正面**：
- 决策立场清晰：脚本层确定性 I/O / LLM 层语义判断，分层干净。
- "暂不把 LLM 调用写死进脚本"的理由（prompt 不稳、schema 变化快、策略未定）有说服力——符合当前项目阶段。
- 模式 A（人审 + agent 执行）→ 模式 B（agent 自动判断）的演进路径合理。
- 明确了"什么时候再把 LLM 下沉到脚本"的四条退出条件，避免决策永远挂着。

**值得收紧的点**：
- **"Claude Code 会话" = 哪个模型？** 文档把 LLM 层放在 "Claude Code 会话"，但本仓库当前实际是 **Opus 4.7（review）+ GPT-5 Codex（主产物）** 并存。候选判断、冲突解释在不同模型上结果会不一样。建议补一句"LLM 抽取层的模型选择、模型版本需在每次写入时记录到 raw/notion_dumps/ 或 smoke log"，对齐 review 文档已经在做的"每版标注模型 ID"实践。
- **无法使用 Claude Code 会话时的 fallback 未定义**：模式 B 隐含假设永远有会话在；但若未来要 cron / CI / webhook 触发，"LLM 层 = Claude Code 会话" 这个等式会断裂。文档可以加一句"离线触发场景显式不支持，需经模式 B 演进后另行设计"。
- **决策的生效时间 / 有效期未写**：每条都是"当前阶段"。若 2026-06 回看，怎么知道这份决策是否过期？建议加 `Decision Date: 2026-04-22` 与 `Revisit Trigger: smoke 跑过 N 条后 / 脚本做批量队列时`。

### 3. `MERGE_STRATEGY.md` 内容审核

**正面**：
- 4 tier 候选排序（Canonical ID → 标题精确 → Aliases → 主题相近）与当前 `upsert_note_to_wiki:393-416` 的查找顺序**一致**。
- 3 档风险等级 + 3 种行为（append / append 标差异 / 停下来问）分层清楚，可执行。
- "幂等性前置定义" 明确要求 raw body hash + 上次成功 wiki page id，把幂等实现的前置条件写出来了——这是 README step 3 的真正设计稿。

**与脚本现状的冲突**（需在 review 里标出）：
- **脚本当前行为违反 MERGE_STRATEGY 多条约束**：
  - 策略 tier 3（Aliases 命中）说"需要结合主题上下文做二次判断，不能只因为 alias 命中就盲目合并"；脚本 `upsert_note_to_wiki:411-414` 里 `page_matches_query` 命中 alias 就直接当 `exact_match` 进 append 路径，没有二次判断。
  - 策略"高风险冲突停止自动写入"；脚本里没有任何"停止"分支，upsert 永远会 append 或新建。
  - 策略"同一个 raw 可能应拆成多个页面要停下来问用户"；脚本不支持一对多，所有 compile-from-raw 只产出一个 wiki 页。
- **建议的收敛方向**：MERGE_STRATEGY.md 应明确声明"当前脚本仅实现 tier 1-3 的查找，tier 3 的二次判断与高风险停止目前由 Claude Code 会话承担"。现在的文档措辞"当前系统不做自动 diff merge"偏弱，读者容易误以为脚本已经在执行策略；实际上脚本只是不做 diff，其他约束（二次判断、停下来问）也没做。

**值得收紧的点**：
- "raw page 的 body hash" 未指明是 markdown 序列化后 hash、还是 rich_text block 原文 hash、还是 retrieve_block_children 原始 JSON hash。三者对同一页面可能产生不同 hash（空格、换行、富文本标记差异），需要在下次实现前定死。
- "记录该 raw page 最近一次成功写入的 wiki page id" —— 脚本已经会把 `Target Wiki Page` 回写到 raw 记录了（`:500-503`），这已经是 wiki page id 的登记。可以直接复用，不需要新建字段。MERGE_STRATEGY.md 可以点名这一点，避免将来误加新字段。
- tier 4（主题相近但无明确标识）的"优先停下来让 Claude Code 会话解释候选页差异"——若要能停下来，脚本需要一个 `--interactive` 或 `--dry-run` 模式，目前没有。这是 MERGE_STRATEGY 真正落地前要加的脚手架。

### 4. README 新的漂移（由两份设计文档的出现引起）

- README「下一步」step 1（"明确真正的 LLM 抽取放在哪一层执行"）**已完成**，LLM_EXTRACTION_DESIGN.md 就是答案。下一次 Codex 改 README 时应把 step 1 删除，或改为"执行 LLM_EXTRACTION_DESIGN.md 里模式 A 的首轮 smoke"。
- README「下一步」step 2（"补更强的候选排序、冲突处理与合并策略"）**文档层已完成**，MERGE_STRATEGY.md 就是策略。但代码层尚未按策略加 tier 3 二次判断 / tier 4 停下来问的能力。step 2 应拆成 2a（策略文档 ✅）和 2b（脚本按策略增加停顿点 ❌）。
- README「限制」段未反映两份新文档的存在。"还没有自动回滚能力"这句现在应该补一句"但候选排序 / 冲突处理 / LLM 归属已有书面策略，见 MERGE_STRATEGY / LLM_EXTRACTION_DESIGN"。

### 5. `ecdfddc` 的 review 回填

v8 指出当前工作树的 README 改动归 Codex 提交；Codex 已在 `ecdfddc docs: refine idempotency and next steps` 完成提交，分工边界被尊重。review 文档里不需要为这条补充，仅此标注。

### 6. 仍未修的旧挂账

- **CLAUDE.md 目录树**（v1/v2/v3/v5/v6/v8/v9 连续挂账）：`raw/.sync_state.json` 仍列但不存在；目录树仍未列 `DESIGN_REVIEW.md` / `README.MD` / `README_REVIEW.md` / `LLM_EXTRACTION_DESIGN.md`（新增）/ `MERGE_STRATEGY.md`（新增）/ `.clinerules-*` / `wiki/index.md`。这份清单越来越长，建议 Codex 下一轮 CLAUDE.md 编辑时统一扫一遍。
- **`raw/notion_dumps/` 仍为空**（v2/v3/v5/v6/v8/v9 挂账）：现在有了 LLM_EXTRACTION_DESIGN 的决策，就**更应该**开始把会话层判断的 prompt / 输入 / 模型 ID / 判断结果落盘，否则模式 A 跑几轮就无法回放。
- **commit 粒度示例 vs 实际**（v2/v3/v5/v8/v9 挂账）。
- **代码层技术债**（v6/v8 标注）：`find_pages_by_canonical_id` 全表扫 / `search_in_database` aliases 假设 rich_text / `query_database_pages` 无熔断 / `extract_property_text` 多类型静默返空。以上四条自 v6 起仍未偿还。

### 7. 分工下的建议：记 LLM 层执行日志

`LLM_EXTRACTION_DESIGN.md` 把 LLM 抽取固定在 Claude Code 会话层，但没说明每次会话判断如何留痕。建议约定：

- 每次对 raw 做判断（选候选 / 合并 or 新建 / 标风险），在 `raw/notion_dumps/` 下落一份 `YYYY-MM-DD-<raw-page-short>.jsonl`，每行记录：模型 ID / 判断时间 / 输入（raw page id、候选 page ids）/ 决策（tier 几、更新还是新建、风险等级）/ 输出（写入的 wiki page id）。
- 这样模式 A 跑到模式 B 切换时，有一批真实案例可作回归基准。

如果 Codex 同意，MERGE_STRATEGY.md 和 LLM_EXTRACTION_DESIGN.md 可以各加一节 "执行日志约定" 指向 `raw/notion_dumps/` —— 也顺手把空目录挂账清了。

### 8. 本版未覆盖

- 未实测 MERGE_STRATEGY 策略下的某条真实 raw：比如"aliases 命中需二次判断"场景脚本会不会误合并（静读推断会误合并，但未实跑）。
- 未评估 LLM_EXTRACTION_DESIGN 的 "模式 B 退出条件" 是否可量化（"smoke test 不止一轮"需多少轮？"schema 基本固定"由谁判断？）。
- 未对 `.clinerules-*` 5 个文件做独立审核 —— 这组文件自 `cd4d3ee` 起一直在仓库里但从未被任何 review 引用过，可能已失效或与本项目脱钩。

---

## v10 · 2026-04-22

- **评审对象**：Codex 本轮代码与 README 双改
  - `scripts/notion_wiki_compiler.py`（工作树，未提交 —— 大幅扩展）
  - `README.MD`（工作树，未提交 —— 同步反映新能力与下一步重排）
  - `raw/notion_dumps/2026-04-21T172604Z-inspect-schema-wiki.json`（新落盘快照）
- **对照对象**：
  - `LLM_EXTRACTION_DESIGN.md` / `MERGE_STRATEGY.md`（`4c3299c`）
  - `README_REVIEW.md` v1-v9
- **锚定 git 状态**：`4c3299c`（HEAD，本地未推） + README / scripts 未提交 + `raw/notion_dumps/` 首次出现产物
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **本版定位**：三条 v6 代码债一次性偿还 + v2–v9 连续挂账的空目录被解除 + Wiki schema 真实类型首次落盘。

### 1. 三条 v6 代码债的偿还对账

| 债务（v6/v8/v9 标注） | 本版状态 | 证据 |
|---|---|---|
| `query_database_pages` 分页循环无熔断 | ✅ 已修 | 新增 `DEFAULT_MAX_QUERY_PAGES = 25` 与 `max_pages` 参数，超限时 raise `NotionError` |
| `search_in_database` 对 aliases 硬编码 `rich_text` 过滤 | ✅ 已修 | 新增 `build_contains_filter`，按真实 property type 动态构造 title/rich_text/multi_select filter；`upsert_note_to_wiki` 和 `command_search` 从 schema 读取类型并传入 |
| `find_pages_by_canonical_id` 全表扫 | ❌ 未修 | README 下一步步骤 4 已显式列入 |
| `extract_property_text` 对 relation/people/files/formula/rollup 静默返空 | ❌ 未修 | 未在下一步提及 |

**两修两欠**，修的两条是运行时风险最高的（熔断防超时、aliases 类型误假设会直接 400）。剩下两条里全表扫属于性能债，静默返空属于代码健壮性债，可按需排期。

### 2. `raw/notion_dumps/` 空目录挂账正式解除

连续从 v2 挂到 v9（共 6 版）的"空目录"债结清：

- `inspect_schema` 落盘 `YYYY-MM-DDTHHmmSSZ-inspect-schema-<role>.json`（一次性快照）
- `command_compile_from_raw` 以 jsonl 形式 append 到 `YYYY-MM-DD-compile-log.jsonl`（累计日志）
- 脚本层打印结果里同时回传 `snapshot_path` / `log_path`，方便 smoke 追踪

这是 v9 建议"LLM 会话判断留痕"的**基础设施一半**——脚本侧的确定性运行日志已齐，**LLM 侧（会话层）留痕仍需规范**（见本版第 5 节）。

### 3. 首份 Wiki schema 快照核对

`raw/notion_dumps/2026-04-21T172604Z-inspect-schema-wiki.json` 揭示了**真实字段类型**（此前 v6/v8/v9 review 反复标注"未经 API 验证"）：

| 字段 | 类型 | 与此前假设对照 |
|---|---|---|
| `Name` | `title` | ✅ 符合 README 与 mapping |
| `Source` | `relation` | ✅ |
| `Canonical ID` | **`unique_id`** | ⚠️ 此前未知，本版首次确认 |
| `Aliases` | `rich_text` | ✅ 与 mapping 假设一致；v6/v8/v9 担心的 multi_select 可能性已消除 |
| `Topic` | `rich_text` | ✅ |
| `Verification` | `status` | ✅ |
| `Compounded Level` | `number` | ✅ |
| `Last Compounded At` | `date` | ✅ |

**新信号**：`Canonical ID` 是 `unique_id` 类型。这对 README 下一步步骤 4（"Canonical ID 改成 Notion 端过滤"）有两点影响：

- Notion 的 unique_id 属性**原生支持** `{"property": "Canonical ID", "unique_id": {"equals": <number>}}` 这类属性过滤，可以直接把 `find_pages_by_canonical_id` 从全表扫改成一次 API 调用。
- 但 `extract_property_text` 已经对 unique_id 返回 `prefix + number` 字符串（`scripts/notion_wiki_compiler.py:178-184`）。如果用户传入 `--canonical-id` 时也是带 prefix 的字符串（如 `WIKI-123`），在下推 filter 时需要**解析出 number 部分**传给 `unique_id.equals`。建议本次实现时加一个小 helper `parse_unique_id_value`，否则 filter 会传字符串 → Notion 报 `validation_error`。

### 4. DESIGN_REVIEW v1 八条清单进度（v10 回归）

| v1 意见 | v9 状态 | v10 状态 | 变化 |
|---|---|---|---|
| ① 拆双 DB id | ✅ | ✅ | — |
| ② 读 raw body | ✅ | ✅ | — |
| ③ canonical_id / aliases 匹配 | ✅ | ✅✅ | aliases 按真实类型动态过滤，质量提升 |
| ④ Raw 状态回写 | ✅✅✅ | ✅✅✅ | — |
| ⑤ LLM 抽取位置 | ✅（文档） | ✅（文档） | — |
| ⑥ `/search` → `query_database` | ✅ | ✅✅ | 熔断已加 + 类型感知，质量提升 |
| ⑦ 澄清 `raw/.sync_state.json` | ❌ | ⚠️ **语义需校准** | 实际实现已改成 `raw/notion_dumps/<date>-compile-log.jsonl` 而非 `.sync_state.json`；CLAUDE.md 目录树与主流程段与实现脱钩（见本版第 6 节） |
| ⑧ 冲突 diff | ✅（文档） | ✅（文档） | 脚本层 tier 3/4 停顿点仍未加；README 步骤 1 已列入 |

**文档层完成率**：**7/8**（⑦仍欠语义校准）
**代码层硬完成**：5/8 硬完成 + 2 已升级质量（③⑥）

### 5. LLM 会话层留痕的半份答卷

v9 建议把每次 LLM 判断（候选选择 / 合并 or 新建 / 风险等级）落到 `raw/notion_dumps/*.jsonl`。现状：

- **脚本侧**：`command_compile_from_raw` 已落 `compile-log.jsonl`，字段含 timestamp / raw_page_id / raw_title / wiki / raw_updates / source_url。**不含 LLM 判断信息**，因为脚本本身不做判断（符合 `LLM_EXTRACTION_DESIGN.md` 立场）。
- **会话侧**：未有留痕机制。意味着目前 smoke test 到底是按哪个 tier 命中、用哪个模型判断的，都丢失。

建议的最小留痕约定（可由 Codex 在 `LLM_EXTRACTION_DESIGN.md` 或新开的 `SESSION_LOG_CONVENTION.md` 中明文化）：

```jsonl
{"timestamp": "...", "model": "claude-opus-4-7", "raw_page_id": "...", "tier": 1|2|3|4, "decision": "update|create|ask-user", "wiki_candidates": [...], "chosen_wiki_page_id": "...", "risk": "low|medium|high", "notes": "..."}
```

该文件由会话（Opus / Codex）在每次判断后 append，可用 `scripts/notion_wiki_compiler.py` 已有的 `append_jsonl_log` helper——**基础设施已就位**，只差约定。

### 6. `raw/.sync_state.json` 的语义校准（第⑦条挂账真相）

v1-v9 一直在挂账说 "CLAUDE.md 目录树列了 `raw/.sync_state.json` 但实际不存在"。v10 发现这条不仅是"未创建"问题，而是**设计已被替换**：

- CLAUDE.md 目录树 `:32` 列 `raw/.sync_state.json # 增量处理状态`
- 实际实现把"增量处理状态"分散在两个地方：
  - Raw Inbox 数据库的 `Status` / `Processed At` / `Target Wiki Page` 三个字段（在 Notion 里）
  - `raw/notion_dumps/<date>-compile-log.jsonl`（在本地）
- 两种实现**都不是** `.sync_state.json`

建议 Codex 下一次 CLAUDE.md 编辑时做两件事：
1. 删除目录树的 `raw/.sync_state.json` 那一行，改成 `raw/notion_dumps/`。
2. 在"主流程 → Raw Inbox"段点明"增量状态存在 Notion 的 Status 字段 + 本地 `compile-log.jsonl`"。

此举会把第⑦条挂账**从"未做"正式升级为"已完成（换实现）"**，同时清掉 v1-v9 反复指出的目录树漂移。

### 7. README 下一步 6 步的合理性

| 步骤 | 状态 | 点评 |
|---|---|---|
| 1. 执行 MERGE_STRATEGY（tier 3 二次判断 / tier 4 停下来问 / 冲突证据） | ❌ 未动代码 | 依赖 LLM 会话层协作 + 脚本增加 `--dry-run` / `--interactive` 停顿点 |
| 2. 文档约定升级成代码层幂等 | ❌ 未动代码 | 需先实现 raw body hash 计算 —— `MERGE_STRATEGY.md:216` 已点出这个前置 |
| 3. compile 日志 + LLM 判断日志完整落盘 | ⚠️ **已一半** | compile log 已落（本版落地）；LLM 判断日志仍待会话层约定（见本版第 5 节）。建议拆 3a（✅）/ 3b（❌） |
| 4. Canonical ID 全表扫 → Notion 端过滤 | ❌ 未动 | 本版 schema 快照证明可用 `unique_id.equals` 下推，注意 prefix 解析 |
| 5. raw body 递归读取 | ❌ 未动 | 独立改动，不依赖其他步骤 |
| 6. 批量编译队列 | ❌ 未动 | 最下游，需等前置稳定 |

**可并行**：步骤 4、5 可以独立做，不阻塞步骤 1/2；建议这两条优先清，把代码层技术债降下来。
**强依赖链**：步骤 2 依赖 `MERGE_STRATEGY` 里 "同一次更新" 的 body hash 定义稳定；步骤 1 依赖 LLM 会话层实际跑几轮积累经验。

### 8. 仍未修的旧挂账

- **CLAUDE.md 目录树**（v1/v2/v3/v5/v6/v8/v9/v10 连续挂账）：本版第 6 节提供了"把⑦挂账正式销账"的具体动作清单。
- **commit 粒度示例 vs 实际 git log**（v2/v3/v5/v8/v9/v10 挂账）：README 示例仍是虚构的 4 条。
- **代码层剩余技术债**：`find_pages_by_canonical_id` 全表扫（README 步骤 4）/ `extract_property_text` 多类型静默返空（未列入下一步）。
- **`.clinerules-*` 5 个文件的适用性**（v9 标注）：仍未审核，未知是否已失效。

### 9. 本版未覆盖

- 未实测新加的 `max_pages=25` 熔断在 >25 页真实场景下的行为（静读推断能 raise，但未跑）。
- 未实测 `build_contains_filter` 对 `multi_select` 类型的 `contains` 行为（Notion API 里 multi_select.contains 的语义是"包含某个 option"，不是"字符串包含"，这里可能需要二次确认；但当前 mapping 下 Aliases/Topic 都是 rich_text，暂不会触发 multi_select 路径）。
- 未评估 `raw/notion_dumps/` 的 gitignore 策略——目前未在 `.gitignore` 中排除，意味着 schema 快照和 compile log 会入库。若未来内容含敏感数据（raw page body 里出现 API key、PII 等），需要补 `.gitignore` 规则。**建议 Codex 下一次动 `.gitignore` 时加 `raw/notion_dumps/*.jsonl`**（schema json 本身一般无敏感信息，可以入库）。
