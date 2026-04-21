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
- 将 raw page body 内容编译到 Wiki
- 编译后回写 Raw 记录的：
  - `Status`
  - `Processed At`
  - `Target Wiki Page`
- 将 `inspect-schema` 的结果落盘到 `raw/notion_dumps/`

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
- 查询层当前能力：
  - `query_database` 已替代全局 `/search`
  - `Aliases` 查询会根据真实字段类型构造过滤（`rich_text` / `multi_select`）
  - 分页查询已带基础熔断上限

## 幂等性与回滚

当前 `compile-from-raw` 的运行语义如下：

- 同一条 raw page 再次运行时，不会自动按 `source_id` 去重。
- 当前 upsert 主要依赖：
  - `Canonical ID`（如果显式传入）
  - 标题与 `Aliases`
- 如果命中已有 wiki 页面，脚本会：
  - 更新页面属性
  - 再追加一段新的 `增量更新` block
- 这意味着当前的“重跑”语义是：
  - **不一定重建页面**
  - **但大概率会重复追加内容**

对 smoke test 的当前约定：

- 已知测试 raw page：
  - `3496e2cd-6e4f-81a2-988a-c9fd757c1b42`
- 已知测试 wiki page：
  - `3496e2cd-6e4f-816e-919b-fd80753dcc9b`
- 在没有更强幂等策略前，不建议反复对这条 raw page 运行 `compile-from-raw`，否则会继续向该 wiki 页面追加测试内容。

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
- Raw page body 读取还比较浅：
  - 当前只读取第一层 block 文本
  - 还没有递归处理深层嵌套 block
- 还没有批量编译队列：
  - 目前是 `compile-from-raw <page_id>` 单条运行
- 虽然 smoke test 已通过，但还存在实现层限制：
  - `query_database` 已替代全局 `/search`
  - `find_pages_by_canonical_id` 仍是全表扫，尚未下推到 Notion 端属性过滤
  - 匹配仍缺少更强的判定与冲突处理
  - 目前只有文档层的幂等性与回滚约定，还没有代码层的严格幂等策略
  - LLM 归属与合并策略已在文档层明确：
    - [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)
    - [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)
  - 但脚本层还没有实现：
    - tier 3 的二次判断
    - tier 4 的停下来问用户
    - 自动 diff / 冲突保留证据
  - `raw/notion_dumps/` 目前只落了 schema 快照，还没有完整沉淀：
    - 每次 compile 的可回放运行日志
    - LLM 判断日志

## 下一步

建议按这个顺序继续：

1. 把“文档层策略”升级成脚本层行为：
   - tier 3 二次判断
   - tier 4 停下来问用户
   - 冲突保留证据
2. 把“文档约定”升级成代码层幂等策略
3. 把 compile 运行日志和 LLM 判断日志完整落盘到 `raw/notion_dumps/`
4. 把 `Canonical ID` 查询从全表扫改成 Notion 端属性过滤
5. 扩展 raw page body 的递归读取
6. 增加批量编译队列

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

## 当前关键文件

- [`CLAUDE.md`](./CLAUDE.md)：系统设计蓝图
- [`DESIGN_REVIEW.md`](./DESIGN_REVIEW.md)：设计与实现差距评审
- [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)：LLM 抽取归属与执行策略
- [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)：候选排序、冲突处理与合并策略
- [`README.MD`](./README.MD)：项目状态说明
- [`README_REVIEW.md`](./README_REVIEW.md)：README 与实际状态一致性评审
- [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)：字段映射模板
- [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)：最小编译器脚本
