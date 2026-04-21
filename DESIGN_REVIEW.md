# LLM Wiki · 设计评审记录

本文档追踪对 `CLAUDE.md` 所描述架构与 `scripts/` 实际实现之间的差距评审。每一版评审都要标注评审日期、评审对象版本、以及执行评审的模型版本，方便后续追溯意见是在哪个时点、由哪个模型给出的。

---

## v1 · 2026-04-21

- **评审对象**：`CLAUDE.md` (Version 2026-04-21.r1) + `scripts/notion_wiki_compiler.py` + `schema/notion_wiki_mapping.example.json` + `.env.example`
- **锚定 commit**：`cd4d3ee`（首次提交）
- **评审者**：Claude Opus 4.7 (1M context)，模型 ID `claude-opus-4-7`
- **评审方式**：读 CLAUDE.md → 读实际脚本与 schema → 对照设计与实现差距

### 1. 设计层面：思路是对的

- **三级编译（raw / 逻辑 / Wiki）+「维护者不是归档者」** 这个立场选得好。它把「知识密度」放在优化目标里，直接否定了「新资料 = 新页面」的默认做法，这是长期不烂尾的关键。
- **先 inspect schema 再写映射**、禁止硬编码字段名，是非常务实的约束。`notion_wiki_mapping.example.json` + CLI flag 双通道覆盖也落实到位。
- **Raw Inbox 用「数据库管索引 + page body 管内容」** 是顺着 Notion 产品形态做的，比自己搞存储层轻。
- 选型上以 Notion 为主库而非自建前端，后续协作、移动端、分享成本都外包给 Notion，合理。

### 2. 落地层面：设计与脚本之间的硬缺口

按影响大小排序：

1. **单 `NOTION_DATABASE_ID` vs 双库架构矛盾** — `.env.example` 和 `main()` 都只认一个 DB id，但 CLAUDE.md 的端到端流程需要 Raw Inbox 和 Wiki 两个库同时在手。现在 `upsert-note` 实际上是「往唯一那个库里追加」，raw → wiki 的方向压根没建立。这是第一道必须先修的裂缝。
2. **读 raw page body 的能力为 0** — 设计中「系统读 page body → 抽实体 → 搜 Wiki → 决定合并」的第 4–6 步，脚本完全没有 `blocks/{id}/children` 调用，`--note` 是命令行字符串直传。目前实质是个「手工喂料」工具，不是「编译器」。
3. **匹配策略退化到「标题严格相等」**（`scripts/notion_wiki_compiler.py:263`）— 忽略了 `Canonical ID`、`Aliases` 两个映射字段。CLAUDE.md 说的「优先按唯一标识 → 再按标题别名」只兑现了最弱的那一级，命中率低会直接导致重复建页，跟系统立场自相矛盾。
4. **raw 状态回写完全缺失** — 没有任何代码把 `Status=Compiled`、`Processed At`、`Target Wiki Page` 写回 Raw Inbox。端到端流程第 8 步是空的，意味着「增量」没法闭环，下次运行还会重复处理。
5. **LLM 能力未出现在实现里** — 「提取实体/别名/主题」「冲突展示差异」这些动作都隐含需要模型，但脚本纯 `urllib` + 规则，没有任何 LLM 调用点。文档需要明确这层是靠脚本内 call Claude API，还是靠 Claude Code 会话里人工 + 工具协作。
6. **`search` 用全局 search API 再按 parent 过滤**（`scripts/notion_wiki_compiler.py:141-145`）— 大库下又慢又不准，应换成 `databases/{id}/query` + `title` contains / `canonical_id` equals 的组合过滤。
7. **`raw/.sync_state.json`、`raw/notion_dumps/` 与 Notion Raw Inbox 的关系没讲清** — 文档说「正文本体在 Notion page body」，那本地 `raw/` 的定位就悬空：是镜像？是独立快照？不定义清楚后面一定会两边漂移。
8. **冲突处理只是 append 一个 heading + paragraph** — 没有 diff 或冲突标记，和「保留差异而不是静默抹平」的原则差距最大。

### 3. 推荐的修复顺序

若目标是「把最小闭环真正跑起来」，按这个顺序修：

1. `.env` 拆成 `NOTION_RAW_INBOX_DB_ID` + `NOTION_WIKI_DB_ID`，`upsert-note` 必须显式指定目标库。
2. 加 `retrieve_block_children` + 一个 `compile-from-raw` 子命令，输入是 raw page id，输出是 wiki upsert。
3. 匹配逻辑先加 `canonical_id` equals 查询，再加 `aliases` contains 查询，最后才退化到标题。
4. 加 raw 状态回写（至少 `Status` / `Processed At` / `Target Wiki Page` 三个字段）。
5. 在架构图里明确标出「LLM 抽取」是哪一步、由谁执行。

前 4 步不涉及 LLM，纯 Notion API 就能落地；做完系统从「骨架」升级到「能跑的最小闭环」。第 5 步是让这套设计跟「LLM Wiki」这个名字真正对得上的关键。

### 4. 本版未覆盖

- 未实际调用 Notion API 验证脚本行为，上述判断基于代码静读。
- 未评估 rate limit / 大库分页 / 错误重试等运行时问题。
- 未评估安全面（Notion integration 权限范围是否最小化等）。

下一版评审可在修复前 4 项后回归，重点从「设计—实现一致性」切到「运行时可靠性」。

---

## v2 · 2026-04-21

- **评审对象**：`CLAUDE.md` (Version 2026-04-21.r2) + `README.MD` + `scripts/notion_wiki_compiler.py` + `schema/notion_wiki_mapping.example.json` + `.env.example`
- **锚定 commit**：`工作树，待提交`
- **评审者**：GPT-5 Codex
- **评审方式**：核对 v1 缺口是否已修、检查文档与工作树是否同步

### 1. 已完成项

- **双库配置已落地**：`.env.example` 已拆成 `NOTION_RAW_INBOX_DB_ID` 与 `NOTION_WIKI_DB_ID`，主流程按命令选择 raw 或 wiki。
- **`compile-from-raw` 已落地**：脚本已支持读取 raw page、读取第一层 block 文本、写入 wiki、回写 raw 状态。
- **raw 回写已落地**：支持回写 `Status`、`Processed At`、`Target Wiki Page`。
- **README 状态说明已建立**：`README.MD` 与 `README_REVIEW.md` 已补齐项目现状与评审记录。

### 2. 仍未解决的核心问题

按后续实现优先级排序：

1. **匹配逻辑仍然过弱**：wiki upsert 仍以标题严格相等为主，没有 `Canonical ID` / `Aliases` 优先匹配。
2. **搜索实现仍基于全局 `/search`**：应改成 `databases/{id}/query` + 属性过滤，才能支撑 canonical id 和 alias 查询。
3. **LLM 抽取位置仍未定稿**：当前系统能跑通闭环，但还不是严格意义上的 LLM 编译器。
4. **raw 本地目录的角色仍需收束**：`raw/notion_dumps/` 目前是“可选本地缓存”，但缓存生成策略还没定义。
5. **冲突处理仍未实现**：现在只是增量追加，没有 diff 标记或冲突提示。

### 3. 当前阶段结论

系统已经从“设计骨架”进入“可运行的最小闭环”阶段。下一阶段不该继续扩散功能面，而应先加强：

1. schema 对齐
2. 数据库内查询能力
3. 匹配质量
4. 第一轮真实 raw -> wiki 运行验证

### 4. 建议的下一步

1. 运行 `inspect-schema --database raw`
2. 运行 `inspect-schema --database wiki`
3. 按真实字段名更新 mapping
4. 将 `search_in_database` 改为 `query_database` 属性过滤
5. 增加 `Canonical ID` / `Aliases` 匹配
