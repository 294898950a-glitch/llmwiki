# LLM Wiki

`llmwiki` 是一个以 Notion 为中心的 LLM Wiki 项目，采用双数据库结构：

- `Raw Inbox Database`：原始资料入口，数据库属性负责索引，page body 存放原始材料正文。
- `Wiki Database`：最终知识库，负责沉淀经过编译和累积更新后的知识对象。

本项目当前目标不是生成本地 Markdown Wiki，而是实现 `raw -> compile -> wiki -> raw 回写` 的最小闭环。

## 当前状态

截至 `2026-04-22`，项目已经完成：

- 双库配置已建立：
  - `NOTION_RAW_INBOX_DB_ID`
  - `NOTION_WIKI_DB_ID`
- 设计文档已更新为 Notion Wiki 主库方案：
  - [`CLAUDE.md`](./CLAUDE.md)
- 设计评审记录已建立：
  - [`DESIGN_REVIEW.md`](./DESIGN_REVIEW.md)
- README 评审记录已建立：
  - [`README_REVIEW.md`](./README_REVIEW.md)
- 最小脚本骨架已落地：
  - [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)
- 字段映射模板已建立：
  - [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)
- Raw / Wiki 两个 Notion 数据库的 schema 已完成首次探测
- 第一次真实 `compile-from-raw` smoke test 已通过
- `raw/notion_dumps/` 已开始落盘 schema 快照

当前脚本支持的命令：

- `inspect-schema --database raw|wiki`
- `search <query>`
- `upsert-note`
- `compile-from-raw <page_id>`
- `compile-queue`
- `lint`

当前已确认的数据库结构：

- `Raw Inbox Database`
  - 标题字段：`Name`
  - 当前字段：
    - `Name`
    - `Status`
    - `Processed At`
    - `Target Wiki Page`
    - `Source URL`
- `Wiki Database`
  - 当前已切换到新的 Wiki 数据库
  - 标题字段：`Name`
  - 当前字段：
    - `Name`
    - `Source`（relation）
    - `Canonical ID`
    - `Aliases`
    - `Compounded Level`
    - `Last Compounded At`
    - `Topic`
    - `Verification`

## 已实现能力

当前已经可以：

- 读取 `.env` 中的 Notion 配置
- 检查 Raw Inbox 或 Wiki 数据库 schema
- 在 Wiki 数据库中进行数据库内候选检索
- 执行基础 upsert：
  - 优先按 `Canonical ID` 匹配（如果传入）
  - 其次按标题与 `Aliases` 检索候选
  - 命中已有页面则更新
  - 未命中则新建页面
- 从 Raw Inbox 指定 page 读取正文
- 递归读取 raw page body 的嵌套 block
- 将 raw page body 内容编译到 Wiki
- 对相同 raw page 执行最小代码层幂等检查：
  - 基于 `body_hash`
  - 命中本地成功编译历史时默认 `skipped_unchanged`
  - 可用 `--force` 强制重跑
- 编译后回写 Raw 记录的：
  - `Status`
  - `Processed At`
  - `Target Wiki Page`
- 将 `inspect-schema` 的结果落盘到 `raw/notion_dumps/`
- 将命令执行事件统一落盘到 `raw/notion_dumps/YYYY-MM-DD-audit-log.jsonl`
- 将 compile 结果落盘到 `raw/notion_dumps/YYYY-MM-DD-compile-log.jsonl`
- 按 `Raw Inbox` 的状态批量编译：
  - `compile-queue --status "Not started" --limit N`
- 可选的低风险整理（默认关闭，`--auto-refine` 开启）：
  - 章节标题收束为主题标题
  - 自动补 `Aliases / Topic / Last Compounded At`
  - 首次创建时自动补结构化整理模板（heading 骨架 + 原文摘录）
  - 模板段落的具体判断由 Claude Code 会话层补写，脚本不做领域性结论

说明：

- 脚本层已经支持上述回写字段
- Raw 数据库的关键回写字段现在已经是合适类型：
  - `Status`: `status`
  - `Processed At`: `date`
  - `Target Wiki Page`: `relation`
- Wiki 数据库的关键字段也已基本到位：
  - `Name`: `title`
  - `Source`: `relation`
  - `Compounded Level`: `number`
  - `Last Compounded At`: `date`
  - `Verification`: `status`
- 已完成一次真实 smoke test：
  - Raw page：`3496e2cd-6e4f-81a2-988a-c9fd757c1b42`
  - Wiki page：`3496e2cd-6e4f-816e-919b-fd80753dcc9b`
  - 闭环已验证：`raw -> wiki -> raw 回写`
- 已完成一条真实文本案例：
  - Raw page：`3496e2cd-6e4f-8151-a289-dd2233536e30`
  - Wiki page：`3496e2cd-6e4f-81b2-b037-d76d653b9c1f`
  - 主题标题已自动收束为：`QueryLoop`
  - 模板生成的 heading 骨架包括：
    - `定义 / 为什么重要 / 关键机制 / 核心判断 / 实现信号 / 关联概念 / 与相邻概念的区别 / 原文证据`
  - 注意：heading 是脚本模板产物，具体段落内容仍需 Claude Code 会话层介入后才是真正的永久笔记。
  - 该页面后续经过一轮人工 + 会话层整理，才从"原文入库页"推进为"主题对象页雏形"。
- 查询层当前能力：
  - `query_database` 已替代全局 `/search`
  - `Aliases` 查询会根据真实字段类型构造过滤（`rich_text` / `multi_select`）
  - `Canonical ID` 查询优先使用 Notion 端属性过滤，必要时再回退到本地过滤
  - 分页查询已带基础熔断上限
  - `lint` 也已切到分页查询，不再只返回单页结果
- 匹配层当前能力：
  - `Canonical ID / title / alias` 已区分匹配策略
  - 同一策略出现多个候选时直接报错，避免静默误并
- 整理层当前能力（默认关闭，需 `--auto-refine`）：
  - 能自动将章节式标题收束成主题式标题
  - 能自动生成 heading 骨架与原文摘录
  - 所有"判断句"都回退为中性表述；领域性结论不由脚本写入
  - 真正的定义 / 核心判断 / 相邻概念区别，仍须 Claude Code 会话层介入

## 幂等性与回滚

当前 `compile-from-raw` 的运行语义如下：

- 同一条 raw page 再次运行时，会先计算当前 raw body 的 `body_hash`。
- 脚本会读取本地成功编译历史：
  - `raw/notion_dumps/*-audit-log.jsonl`
- 如果同一 raw page 的最新成功编译记录与当前 `body_hash` 一致，则默认：
  - 返回 `skipped_unchanged`
  - 不再重复写入 wiki
  - 但若 Raw 当前 Status 仍非编译完成态，仍会把 Status 推进到 `Done`，避免 `compile-queue` 反复拉取
- 如确需重跑，可显式加：
  - `--force`
- 当前 upsert 主要依赖：
  - `Canonical ID`（如果显式传入）
  - 标题与 `Aliases`
- 如果命中已有 wiki 页面，脚本会：
  - 更新页面属性
  - 再追加一段新的 `增量更新` block
  - 若显式传入 `--auto-refine`，才补 heading 骨架和原文摘录（默认关闭）
- 这意味着当前的“重跑”语义是：
  - **同内容重跑默认跳过**
  - **内容变化后重跑仍会继续追加，而不是覆盖**

对 smoke test 的当前约定：

- 已知测试 raw page：
  - `3496e2cd-6e4f-81a2-988a-c9fd757c1b42`
- 已知测试 wiki page：
  - `3496e2cd-6e4f-816e-919b-fd80753dcc9b`
- 在没有更强对象级幂等策略前，不建议反复拿这条 raw page 做测试：
  - 若 raw body 未变化，当前实现会返回 `skipped_unchanged`
  - 若 raw body 变化，或显式加 `--force`，仍会继续向该 wiki 页面追加测试内容

当前可接受的手工回滚方式：

1. 到 Notion 中删除 smoke test 生成的 wiki 页面：
   - `3496e2cd-6e4f-816e-919b-fd80753dcc9b`
2. 在 Raw Inbox 中把对应 raw 记录重置为未完成状态：
   - `Status` 改回 `Not started`
   - 清空 `Processed At`
   - 清空 `Target Wiki Page`
3. 若要重新做 smoke test，建议新建一条新的 raw page，而不是复用旧的测试条目。

当前还没有自动回滚能力，也还没有“按 source_id 幂等更新”的严格策略，这属于下一阶段要补的实现。

## 当前限制

这还是第一版骨架，下面这些能力还没有完成：

- 还没有真正的 LLM 抽取：
  - 实体识别
  - 别名提取
  - 主题提取
  - 冲突差异总结
- 匹配逻辑仍然偏弱：
  - 已有 `Canonical ID + Aliases` 基础匹配
  - 但还没有更完整的候选排序、冲突判断和合并策略
- 虽然 smoke test 已通过，但还存在实现层限制：
  - `query_database` 已替代全局 `/search`
  - 匹配仍缺少更强的判定与冲突处理
  - 当前幂等仍是“按 raw page 最新 `body_hash` 跳过重复追加”的最小实现，不是基于 source 级别的严格对象幂等
  - LLM 归属与合并策略已在文档层明确：
    - [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)
    - [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)
  - 但脚本层还没有实现：
    - tier 3 的二次判断
    - tier 4 的停下来问用户
    - 自动 diff / 冲突保留证据
    - 基于候选风险的自动/半自动切换
  - `raw/notion_dumps/` 目前已经落了 schema 快照、audit 事件和 compile 结果，但还没有：
    - LLM 判断日志
    - 面向冲突分析的结构化 diff 证据

## 下一步

建议按这个顺序继续：

1. 把当前 `QueryLoop` 这类 live 优化经验沉淀回脚本，减少后续手工清理
2. 把“深度整理”从低风险模板升级成更稳定的永久笔记结构
3. 把候选风险分级真正下沉到脚本：
   - tier 3 二次判断
   - tier 4 停下来问用户
   - 冲突保留证据
4. 把当前最小 `body_hash` 幂等升级成更严格的对象级幂等策略
5. 补 LLM 判断日志和冲突 diff 证据落盘
6. 给 `compile-queue` 增加更细的筛选与失败重试策略

## 版本控制

本项目已经在 git 仓库中，应继续做版本控制。

当前建议：

- 所有设计变更先更新：
  - `CLAUDE.md`
  - `DESIGN_REVIEW.md`
  - `README.MD`
- 所有脚本变更同步更新：
  - `scripts/`
  - `schema/`
- 每完成一个清晰阶段就提交一次，不要把多类改动混成一个大 commit。

推荐 commit 粒度：

- `docs: update notion dual-db architecture`
- `feat: add compile-from-raw command`
- `feat: add raw status writeback`
- `refactor: split raw and wiki env config`

敏感信息约束：

- `.env` 不入 git
- 不要把真实 `NOTION_API_KEY` 提交到仓库
- 数据库 ID 是否入库可以讨论，但 API key 不允许入库
- `raw/notion_dumps/*.jsonl`（运行日志，可能含 raw 标题与 source_url）已在 `.gitignore` 中忽略；schema 快照 JSON 仍入库，便于追溯字段变更

## 当前关键文件

- [`CLAUDE.md`](./CLAUDE.md)：系统设计蓝图
- [`DESIGN_REVIEW.md`](./DESIGN_REVIEW.md)：设计与实现差距评审
- [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)：LLM 抽取归属与执行策略
- [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)：候选排序、冲突处理与合并策略
- [`README.MD`](./README.MD)：项目状态说明
- [`README_REVIEW.md`](./README_REVIEW.md)：README 与实际状态一致性评审
- [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)：字段映射模板
- [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)：最小编译器脚本
