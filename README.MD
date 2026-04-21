# LLM Wiki

`llmwiki` 是一个以 Notion 为中心的 LLM Wiki 项目，采用双数据库结构：

- `Raw Inbox Database`：原始资料入口，数据库属性负责索引，page body 存放原始材料正文。
- `Wiki Database`：最终知识库，负责沉淀经过编译和累积更新后的知识对象。

本项目当前目标不是生成本地 Markdown Wiki，而是实现 `raw -> compile -> wiki -> raw 回写` 的最小闭环。

## 当前状态

截至 `2026-04-21`，项目已经完成：

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
  - 标题字段：`Source`
  - 当前字段：
    - `Source`
    - `Source `（relation，字段名尾部带空格）
    - `Canonical ID`
    - `Aliases`
    - `Compounded Level`
    - `Topic`

## 已实现能力

当前已经可以：

- 读取 `.env` 中的 Notion 配置
- 检查 Raw Inbox 或 Wiki 数据库 schema
- 在 Wiki 数据库中搜索候选页面
- 按标题执行最小 upsert：
  - 命中已有页面则更新
  - 未命中则新建页面
- 从 Raw Inbox 指定 page 读取正文
- 将 raw page body 内容编译到 Wiki
- 编译后回写 Raw 记录的：
  - `Status`
  - `Processed At`
  - `Target Wiki Page`

说明：

- 脚本层已经支持上述回写字段
- Raw 数据库的关键回写字段现在已经是合适类型：
  - `Status`: `status`
  - `Processed At`: `date`
  - `Target Wiki Page`: `relation`

## 当前限制

这还是第一版骨架，下面这些能力还没有完成：

- 还没有真正的 LLM 抽取：
  - 实体识别
  - 别名提取
  - 主题提取
  - 冲突差异总结
- 匹配逻辑仍然偏弱：
  - 目前主要按标题匹配
  - 还没有完整实现 `Canonical ID + Aliases` 优先匹配
- Raw page body 读取还比较浅：
  - 当前只读取第一层 block 文本
  - 还没有递归处理深层嵌套 block
- 还没有批量编译队列：
  - 目前是 `compile-from-raw <page_id>` 单条运行
- 虽然已经跑过 `inspect-schema`，但数据库字段还没长齐：
  - 新 Wiki 的标题字段是 `Source`，不是原先假设的 `Name` 或 `Page`
  - Wiki 里存在两个非常接近的字段：
    - `Source`：title
    - `Source `：relation，字段名尾部带空格
  - 这会给 mapping 和脚本调用带来歧义，建议改名
  - Wiki 当前仍缺：
    - `Verification`
    - `Last Compounded At`
  - `Compounded Level` 当前是 `rich_text`，后续更适合改成 `number`

## 下一步

建议按这个顺序继续：

1. 根据真实字段名更新 `schema/notion_wiki_mapping.example.json`。
2. 在 Wiki 数据库中把 `Source ` 这个 relation 字段改成一个不带尾部空格、语义明确的名字。
3. 在 Wiki 数据库中补：
   - `Verification`
   - `Last Compounded At`
4. 如果要更稳，把 `Compounded Level` 从 `rich_text` 改成 `number`
5. 选一条 raw page 做第一次真实 `compile-from-raw`
6. 将 `search_in_database` 改成 `query_database` 属性过滤
7. 补 `Canonical ID` 和 `Aliases` 匹配
8. 再决定是否接入真正的 LLM 抽取与冲突处理

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
- [`README.MD`](./README.MD)：项目状态说明
- [`README_REVIEW.md`](./README_REVIEW.md)：README 与实际状态一致性评审
- [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)：字段映射模板
- [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)：最小编译器脚本
