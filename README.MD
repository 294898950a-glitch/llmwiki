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
- Notion API 执行层脚本已落地（9 个子命令）：
  - [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)
- 字段映射模板已建立：
  - [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)
- 三份设计文档已分别定稿：
  - [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)：LLM 抽取归属 + 会话层留痕约定
  - [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)：候选排序 / 冲突分级 / 合并策略
  - [`EDITORIAL_POLICY.md`](./EDITORIAL_POLICY.md)：永久笔记结构 checklist
- Raw / Wiki 两个 Notion 数据库的 schema 已完成首次探测
- 第一次真实 `compile-from-raw` smoke test 已通过
- `raw/notion_dumps/` 已开始落盘多类运行产物：
  - `*-inspect-schema-<role>.json`（schema 快照，入库）
  - `YYYY-MM-DD-audit-log.jsonl`（命令执行事件，本地）
  - `YYYY-MM-DD-compile-log.jsonl`（compile 结构化结果，本地）
  - `YYYY-MM-DD-session-log.jsonl`（会话层语义判断留痕，本地）

当前脚本支持的命令：

- `inspect-schema --database raw|wiki`
- `search <query>`
- `upsert-note`
- `compile-from-raw <page_id>` ——支持 `--force` / `--auto-refine` / `--strict-alias` / `--strict-fuzzy` / `--emit-diff`
- `compile-queue --status <S> --limit N` ——支持 `--retry-failed` / `--filter PROP=VALUE`（可重复）/ 同样的 strict/emit-diff flags
- `log-session-event` ——会话层留痕入口（写 `session-log.jsonl`）
- `cleanup-wiki-page <page_id>` ——去重页面内重复的 `增量更新` section，支持 `--dry-run`
- `check-editorial [<page_id>] [--all --limit N]` ——按 `EDITORIAL_POLICY.md` 的 checklist 机器化评估 wiki 页的永久笔记达标度，返回 green/yellow/red 分级
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
  - `compile-queue --retry-failed` 从最近一次 queue audit 记录中拉失败条目重跑
- 跨 raw 的 `body_hash` 级幂等：
  - 若同一 body_hash 已从别的 raw 页面编译过，当前 raw 会被 `skipped_duplicate_body` 跳过，并记录 `originating_raw_page_id` / 关联 wiki
  - 显式加 `--force` 可绕过
- 匹配策略层的审查门控：
  - 命中 `alias` 时输出 stderr warning，payload 附带 `review_required: true`
  - 传入 `--strict-alias` 时 alias 命中直接 raise，不写入
- 会话层留痕入口：
  - `log-session-event --model --raw-page-id --wiki-page-id --tier --decision --risk --notes`
  - 写入 `raw/notion_dumps/YYYY-MM-DD-session-log.jsonl`，配合 `LLM_EXTRACTION_DESIGN.md` 的"会话层留痕约定"
- 对已有 wiki 页面做重复 `增量更新` 清理：
  - `cleanup-wiki-page <page_id>`，支持 `--dry-run`
- tier 4 审查：无 canonical/title/alias 命中但存在 fuzzy 同主题候选时：
  - 默认输出 stderr warning，继续建新页
  - `--strict-fuzzy` 直接 raise，由人或会话层决定
- 冲突 diff 证据：`compile-from-raw --emit-diff`：
  - compile-log 里现在始终存 raw body 截取（≤ 8000 字符）
  - update 路径下若上一次 compile 留有 body_text，则计算段落级 unified diff 并 append 为 `差异分析 YYYY-MM-DD` 代码块
- Raw Inbox 多维过滤：`compile-queue --filter PROP=VALUE`（可重复）与 `--status` 合成 AND 过滤，覆盖 status/select/rich_text/title/checkbox/number/multi_select 常见类型
- `check-editorial` 按 `EDITORIAL_POLICY.md` 的 checklist 对 wiki 页做机器化评估：
  - 检查必填属性（Canonical ID / Verification / Compounded Level / Last Compounded At）
  - 检查 title 是否已收束（无"第N章"前缀、无冒号切分残留）
  - 检查正文是否至少含 `定义 / 核心判断 / 关联概念 / 原文证据` 四个 heading_2
  - 检查 `原文证据` 下的条数 ≤ 4
  - 检查 `增量更新` section 是否存在重复
  - 输出 green（全绿）/ yellow（≤2 问题）/ red（>2 问题）；exit code 0/1 反映 green 与否
- 可选的低风险整理（默认关闭，`--auto-refine` 开启）：
  - 章节标题收束为主题标题
  - 自动补 `Aliases / Topic / Last Compounded At`
  - 首次创建时写入启发式正文：
    - heading 骨架（定义 / 为什么重要 / 核心判断 / ...）
    - 原文摘要 / 原文要点（取前几段自动截取）
    - 规则抽取的关键机制 / 关联概念 / 实现信号 / 原文证据（匹配硬编码 topic map 时才出现）
  - 这些启发式内容覆盖不到的领域会自然跳过对应段落
  - 脚本不直接调用 LLM，但 `infer_*` 族是基于硬编码规则的"启发式判断"，不是单纯的中性骨架；要升级到真正语义判断仍需 Claude Code 会话层介入

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
  - 脚本先产出启发式正文：
    - heading 骨架 `定义 / 为什么重要 / 关键机制 / 核心判断 / 实现信号 / 关联概念 / 与相邻概念的区别 / 原文证据`
    - 每段正文由 `infer_*` 族基于规则填充（原文摘录 + 关键词命中）
  - 会话层随后对这份启发式正文做了一轮 live editorial 收敛：
    - 把启发式判断替换成真正永久笔记级别的定义与判断
    - 把机械抽取的原文证据收敛成少量高价值引文
    - 清理重复 / 噪音 block
  - 当前该页面已从"原文入库页"推进为"主题对象页雏形"；脚本启发式先落第一版正文，会话层再收敛到可用形态，两者缺一不可。
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
  - 把章节式标题自动收束成主题式标题
  - 在 wiki 页正文 append 一整版**启发式正文**（不仅是骨架 + 摘录）：
    - heading 骨架（定义 / 为什么重要 / 核心判断 / ...）
    - 原文摘要 / 原文要点（取前几段自动截取）
    - 硬编码 topic map 命中时：关键机制 / 关联概念 / 实现信号 / 原文证据的具体 bullet 列表
    - Topic 属性按 topic map 推断并写回
  - 核心判断 / 相邻概念区别当前是中性兜底句（不再含领域专属论断）
  - 所有"启发式判断"都是硬编码规则产物，不是真 LLM；要得到真正主题级判断，仍须 Claude Code 会话层介入后覆盖或追加

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
  - 若显式传入 `--auto-refine`，再追加一版启发式正文（heading 骨架 + 原文摘要 + topic map 命中时的 bullet 列表 + 回写 Topic 属性）；默认关闭
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

当前仍没有"对象级"严格幂等（同一 wiki 对象 + 同一内容状态 = 跳过），也没有自动回滚。但跨 raw 的 body_hash 重复已能识别并跳过，见 `skipped_duplicate_body`。

## 当前限制

当前闭环已跑通，下列能力仍未到位：

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
  - 匹配仍缺少主题级语义判断（仅规则命中，不是真 LLM）
  - 幂等当前覆盖 raw 自身重跑 + 跨 raw 同 body_hash 两种情况；尚未做"同 wiki 对象同内容"严格对象级幂等
  - LLM 归属与合并策略已在文档层明确：
    - [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)（含"会话层留痕约定"）
    - [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)
  - 脚本层已部分实现 MERGE_STRATEGY 的 tier 3 / 冲突证据：
    - alias 命中会触发 `review_required: true` 或 `--strict-alias` 硬停
    - 会话层可通过 `log-session-event` 登记判断
  - 脚本层当前具备：
    - tier 4 fuzzy 候选感知（`--strict-fuzzy` 可硬停）
    - 段落级 unified diff 证据（`--emit-diff`，需上次 compile 留有 body_text）
    - `check-editorial` 按 `EDITORIAL_POLICY.md` checklist 对 wiki 页做机器化评估
  - 仍待推进：
    - 基于 diff 的"核心判断是否需要重写"决策（归会话层）
    - `check-editorial` 结果驱动的自动补段（会话层协作）

## 下一步

上几轮挂账已经清：
- `cleanup-wiki-page`：替代手工去重 `增量更新` block（原"live 优化经验沉淀"）
- alias 命中：`review_required: true` + `--strict-alias`（tier 3）
- fuzzy 命中：stderr warning + `--strict-fuzzy` 硬停（tier 4）
- `body_hash` 幂等：跨 raw 同 hash `skipped_duplicate_body`；compile-log 现在留 body_text 截取
- `log-session-event` + `LLM_EXTRACTION_DESIGN.md` 会话层留痕约定（DESIGN_REVIEW v1 第⑤条销账）
- `compile-queue --retry-failed`：从最近一次 audit 里拉失败条目重跑
- `compile-queue --filter PROP=VALUE`：多维过滤，支持 status/select/rich_text/title/checkbox/number/multi_select
- `compile-from-raw --emit-diff`：段落级 unified diff 作为 `差异分析 YYYY-MM-DD` 代码块追加
- `EDITORIAL_POLICY.md`：永久笔记结构 checklist（文档层）
- `check-editorial`：把 EDITORIAL_POLICY checklist 落到代码层自动评估（green/yellow/red 分级）

尚未做（需要专项设计、非一两轮能定稿）：

1. **`check-editorial` 驱动的自动补段**：当前只评估不行动，缺段页面报 yellow/red；真正补内容需要会话层介入，脚本可后续增加"按 check-editorial 缺口批量生成骨架"能力。
2. **对象级"同 wiki 同状态跳过"**：当前跨 raw 同 hash 已能跳过；但"同一 wiki 对象已达到目标内容，即便 raw 变了也应跳过"尚无实现（需先定义"目标内容"）。
3. **基于 diff 的核心判断重写**：`--emit-diff` 只记录差异，没有触发摘要重写；重写是会话层 editorial 决策，脚本不代做。

这三项都属于"把语义判断做得更深"而非"把工具链补齐"，按 `LLM_EXTRACTION_DESIGN.md` 的分工，应留给会话层逐步积累案例后再回收到脚本。

## 版本控制

本项目已经在 git 仓库中，应继续做版本控制。

当前分工（基于多模型协作）：

- **Claude Opus 4.7 维护**：`README.MD` / `CLAUDE.md` / `scripts/` / `schema/` / `LLM_EXTRACTION_DESIGN.md` / `MERGE_STRATEGY.md` / `EDITORIAL_POLICY.md`
- **GPT-5 Codex 维护**：`README_REVIEW.md` / `DESIGN_REVIEW.md` 两份评审文档
- `CLAUDE.md` 每次实质性修改要 bump 版本号（日期.rN），便于 review 锚定

提交约定：

- 每完成一个清晰阶段就提交一次，不要把多类改动混成一个大 commit
- 代码改动和文档同步分开提交，review 文档独立提交

近期真实 commit 示例（2026-04-22 窗口内）：

- `feat(compile): fuzzy tier 4 + --filter + --emit-diff + body_text snapshot`
- `feat(lint): add check-editorial subcommand for EDITORIAL_POLICY checklist`
- `fix(extract): cover multi_select / number / checkbox / date / ... in extract_property_text`
- `docs(editorial): define permanent note structure checklist`
- `docs(llm-design): add session-layer audit trail convention`
- `docs(review): add README_REVIEW v14`

敏感信息约束：

- `.env` 不入 git
- 不要把真实 `NOTION_API_KEY` 提交到仓库
- 数据库 ID 是否入库可以讨论，但 API key 不允许入库
- `raw/notion_dumps/*.jsonl`（运行日志，可能含 raw 标题与 source_url）已在 `.gitignore` 中忽略；schema 快照 JSON 仍入库，便于追溯字段变更

## 当前关键文件

- [`CLAUDE.md`](./CLAUDE.md)：系统设计蓝图
- [`DESIGN_REVIEW.md`](./DESIGN_REVIEW.md)：设计与实现差距评审
- [`EDITORIAL_POLICY.md`](./EDITORIAL_POLICY.md)：永久笔记结构与分工 checklist
- [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)：LLM 抽取归属、执行策略、会话层留痕约定
- [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)：候选排序、冲突处理与合并策略
- [`README.MD`](./README.MD)：项目状态说明
- [`README_REVIEW.md`](./README_REVIEW.md)：README 与实际状态一致性评审
- [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)：字段映射模板
- [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)：Notion API 执行层
