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
