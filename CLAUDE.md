# LLM Wiki · Notion Wiki 运行蓝图

> **Version**: 2026-04-22.r9
> 每次实质性修改本文件需要 bump 版本号（日期.rN），并在 git 中提交。`DESIGN_REVIEW.md` 的评审锚点同时引用本版本号与对应 commit SHA。

这是一个以 Notion Wiki 为主库的 LLM Wiki 系统。目标不是把资料归档成越来越多的文件，而是把新资料持续编译进已有知识对象，让知识密度随着时间增加。

## 系统定位

这是一个三级编译模型：

1. `raw/`：原始资料层。存放从 Notion Inbox、网页剪藏、PDF 或其他来源抓下来的只读快照，用于审计、追溯和重跑。
2. `schema/` 与 `scripts/`：逻辑层。负责定义属性映射、判定规则、upsert 策略和 lint 规则。
3. Notion Wiki：最终产物层。当前是 **alpha 编译器**，`raw → wiki` 写入链路已打通；"知识复利"仍是目标而非已达成的状态，单页质量、对象级 compounding、概念网络生长都依赖 Claude Code 会话层介入。完整路线图见 `V15_EXECUTION_PLAN.md`。

## 当前阶段

**手动迭代期 + alpha**。最小闭环 `inspect-schema → search → compile-from-raw → check-editorial` 已跑通，但不等于系统成熟。不要过早把字段、分类法或写作模板定死；每次运行前先观察真实数据库结构，再决定映射。

**核心原则**：你是 Wiki 维护者，不是笔记归档器。默认优先更新既有页面，而不是新建页面。只有在找不到可信候选页面时，才允许新建页面。

## 目录结构

```text
llmwiki/
├── .env                             # Notion 凭证（不入 git）
├── .env.example                     # 模板
├── .gitignore                       # 忽略 .env、raw/notion_dumps/*.jsonl 等
├── CLAUDE.md                        # 本文件
├── DESIGN_REVIEW.md                 # 设计与实现差距评审（含 v1 八条清单）
├── EDITORIAL_POLICY.md              # 永久笔记结构 checklist 与脚本/会话分工
├── LLM_EXTRACTION_DESIGN.md         # LLM 抽取归属、执行策略、会话层留痕约定
├── MERGE_STRATEGY.md                # 候选排序、冲突分级与合并策略
├── V15_EXECUTION_PLAN.md            # 基于 review v15 成熟度评估的四阶段执行方案
├── README.MD                        # 项目状态说明
├── README_REVIEW.md                 # README 与实际状态一致性评审
├── raw/
│   └── notion_dumps/                # 运行期产物：
│                                    #   - YYYY-MM-DDTHHmmSSZ-inspect-schema-<role>.json（入库）
│                                    #   - YYYY-MM-DD-audit-log.jsonl（本地，.gitignore 忽略）
│                                    #   - YYYY-MM-DD-compile-log.jsonl（本地，.gitignore 忽略）
│                                    #   - YYYY-MM-DD-session-log.jsonl（本地，.gitignore 忽略）
├── schema/
│   └── notion_wiki_mapping.example.json
├── scripts/
│   └── notion_wiki_compiler.py      # Notion API 执行层，含 6 个子命令
└── wiki/
    └── index.md                     # 历史调试遗留目录，当前不是主产物
```

注：仓库中还存在 `.clinerules-*` 系列文件（早期 cline 角色规则），与本项目无实际绑定，未来可裁剪。

## 主流程

### Raw Inbox · 数据库做索引，Page Body 放原始材料

`raw` 层采用 Notion 双层结构：

- `Raw Inbox Database`：负责索引、状态、优先级、relation 和自动化队列。
- `Raw Entry Page Body`：负责承载原始材料本体，例如网页剪藏、PDF 摘录、手工随记、附件和引用片段。

默认工作方式：
1. 用户在 `Raw Inbox` 数据库中新建一条 page。
2. 用户把原始材料粘贴或保存到该 page 的正文中。
3. 用户或脚本把 `Status` 置为待处理态（当前约定为 `Not started`），并可手工补 `Source URL` 等索引字段。
4. 脚本按 `Status` 过滤出队列，递归读取 page body 原始内容，送入 Wiki 编译流程。
5. 编译完成后，脚本回写 raw 记录的 `Status = Done` / `Processed At` / `Target Wiki Page`，并在本地 `raw/notion_dumps/*-compile-log.jsonl` 登记本次编译的 `body_hash`，用于后续 `body_hash` 幂等跳过。

当前脚本**尚未**自动补 `Captured At` / `Type` / `Priority` 等索引字段——这些仍由用户在 Notion 中维护。

这意味着 raw 层不是“一个字段装全部内容”，而是“数据库属性管索引，page 正文管内容本体”。

### Inspect Schema · 检查 Wiki 数据库结构

**输入**：`.env` 中的 `NOTION_API_KEY` 与目标库 ID
**输出**：Raw Inbox 或 Wiki 数据库属性报告

执行要求：
- 先读数据库 schema，再决定映射。
- 不要预设字段一定叫 `Name`、`Verification`、`Source`。
- 报告至少包括：属性名、类型、是否存在 title、是否存在唯一标识字段、是否存在核验字段。

### Upsert · 把新资料编译进既有知识页面

**目标**：对新资料执行 `search -> decide -> merge/update/create`。

默认决策顺序：
1. 提取候选实体名、别名、主题、时间范围。
2. 优先按 `Canonical ID` 或其他唯一标识匹配。
3. 若无唯一标识，则按标题与别名搜索现有页面。
4. 匹配到同一实体时，优先更新既有页面：
   - 重写顶部摘要，或
   - 追加带日期戳的新结论块，或
   - 在既有页面下新增子主题小节。
5. 只有在没有可信候选页时，才新建页面。

更新原则：
- 不要直接整页覆盖。
- 新内容必须保留来源引用与更新时间。
- 如果新旧结论冲突，展示差异并保留证据，不要静默抹平。
- `Compounded Level` 应在成功合并后递增。
- `Verification` 可根据资料时效性被标记为 `Fresh`、`Needs Review`、`Expired` 或用户定义的状态。

### Lint · 体检既有 Wiki

脚本可定时或手动运行：
- 查询 `Verification == Expired` 或等价状态的页面。
- 读取页面内容与关联 source。
- 结合新资料或人工确认结果，重写摘要、补充证据、重置核验状态。

## 推荐的最小属性集

### Raw Inbox Database

第一版 raw 数据库建议至少有这些字段：
- `Name`：标题
- `Source URL`：原始链接
- `Captured At`：采集时间
- `Type`：资料类型，例如 `Web Clip`、`PDF`、`Memo`
- `Status`：处理状态
- `Processed At`：编译完成时间
- `Target Wiki Page`：relation，指向目标 Wiki 页面
- `Priority`：优先级

推荐状态流转：
- `New`：刚进入收件箱，还未补索引或未处理
- `Indexed`：基础索引已补齐
- `Queued`：已进入待编译队列
- `Compiled`：已完成 Wiki 编译
- `Needs Review`：需要人工复核
- `Dropped`：不进入 Wiki

### Wiki Database

第一版尽量只依赖这些字段：
- `Name`：标题字段
- `Canonical ID`：唯一标识，建议用 rich text 保存稳定 ID
- `Verification`：状态字段
- `Source`：关联回原始资料库
- `Compounded Level`：数字，记录累积更新次数
- `Last Compounded At`：最近一次累积时间
- `Aliases`：别名
- `Topic`：主题分类

如果真实库的字段名不同，以真实字段为准，不要强行改库来适配脚本。

## 端到端工作流

系统按下面的顺序运行：

1. 用户把 raw 内容保存到 `Raw Inbox` 的某条 page 正文，并把 `Status` 置为 `Not started`（或其他约定的待处理态）。
2. 脚本 `compile-queue --status "Not started" --limit N` 按状态过滤，逐条进入编译流程。未来计划按 `Type` / `Priority` 加筛（当前未实现）。
3. 脚本递归读取 raw page body 全文，计算 `body_hash`。若与上次成功编译的 hash 一致则跳过（并将 `Status` 推进到 `Done`，避免 queue 重复拉取）。
4. 脚本按 `Canonical ID → 标题 → Aliases` 顺序在 `Wiki Database` 中查询候选；多候选同策略命中直接报错停止。
5. 命中同一知识对象时更新现有页面（追加 `增量更新` block + 可选 `--auto-refine` 启发式整理）；未命中则新建。
6. 脚本回写 raw 记录：
   - `Status = Done`（由 `raw_compiled_status` 配置）
   - `Processed At = 今日日期`
   - `Target Wiki Page = 对应 Wiki 页面 relation`
7. 脚本把运行事件 append 到本地 `raw/notion_dumps/YYYY-MM-DD-audit-log.jsonl` 与 `YYYY-MM-DD-compile-log.jsonl`，作为可回放的审计日志。

**语义判断归属**：
- 脚本层负责 Notion API、字段映射、候选查询、幂等判断等**确定性动作**。
- 主题级判断（"这条是同一实体吗？"、"新旧结论冲突怎么处理？"、"摘要怎么写？"）归 **Claude Code 会话层**承担；详见 `LLM_EXTRACTION_DESIGN.md`。
- `--auto-refine` 启用时脚本会用硬编码规则填一版"启发式正文"，但**不等于** LLM 判断；真正的永久笔记化仍须会话层介入。

简化理解：
- 你负责把 raw 材料放进 page body 并置 `Not started`。
- 脚本负责过滤队列、编译、幂等、回写状态、落盘日志。
- 会话层负责主题判断、冲突解释、精修内容。

## Agent 指令基线

默认把下面这条指令视为 upsert 的行为准则：

```text
你是 Notion Wiki 的维护者，不是笔记归档器。

当收到一条新资料时，先执行：
1. 提取候选实体名、别名、时间范围、主题标签。
2. 在 Notion Wiki 中搜索标题、唯一标识、别名是否匹配现有页面。
3. 如果匹配到同一实体且新资料只是在补充事实、更新状态、增加证据，则更新现有页面。
4. 如果匹配到同一实体但新资料代表一个独立主题切面，则在原页面新增小节或建立子页面，并建立 Relation。
5. 只有在找不到可信候选页面时，才允许新建页面。

默认原则：宁可合并并保留差异，不要因为措辞不同就重复建页。
更新时必须保留新增内容来自哪个 source、更新时间、与旧结论差异是什么。
禁止直接整页覆盖；优先重写摘要段，追加证据块，更新 Verification 和 Compounded Level。
```

## 关键约束

1. 每次运行前检查 `.env` 是否完整；若缺失密钥或目标库 ID，直接停止并提醒用户。
2. 不要把密钥写进任何会被提交到 git 或同步分享的文本中。
3. 不要假设 Notion Wiki 一定已经包含某些字段；脚本应报告缺口，而不是硬编码崩溃。
4. 对已存在页面的修改应优先追加或局部更新，避免整页替换。
5. 每次运行结束都要给用户一份简短清单：检查了哪些属性、命中了哪些页面、新建了几页、更新了几页。

## 当前可用脚本

`scripts/notion_wiki_compiler.py` 提供 12 个子命令：

- `inspect-schema --database raw|wiki`：读数据库 schema，落盘到 `raw/notion_dumps/`
- `search <query>`：在 Wiki 库中按标题 / Aliases 查候选
- `upsert-note`：显式传入 title/note/canonical 等直接写入 Wiki；支持 `--strict-alias`
- `compile-from-raw <raw_page_id>`：从指定 raw page 编译到 Wiki，含 `body_hash` 幂等（含跨 raw 同 hash 的 `skipped_duplicate_body`）、raw 状态回写、可选 `--auto-refine` / `--strict-alias` / `--strict-fuzzy` / `--emit-diff` / `--force`，以及 `--merge-mode {append,propose,replace}`（propose 只输出预览不写；replace 需配 `--replace-heading <text>` 替换指定 section 内容）
- `compile-queue --status <S> --limit N`：按 Raw Inbox Status 批量编译，失败不中断；支持 `--retry-failed` / `--filter PROP=VALUE`（可重复，和 Status 共同组成 AND 过滤）/ 同样的 strict/emit-diff/merge-mode flags
- `log-session-event --model --tier --decision --risk --notes ...`：会话层留痕入口，写 `session-log.jsonl`，用于记录语义判断的 why
- `cleanup-wiki-page <page_id>`：去重页面内的重复 `增量更新` section，支持 `--dry-run`
- `check-editorial [<page_id>] [--all --limit N]`：按 `EDITORIAL_POLICY.md` checklist 机器化评估 wiki 页永久笔记达标度，返回 green/yellow/red
- `consolidate-evidence <page_id> [--heading <text>] [--keep N] [--dry-run]`：对指定 heading（默认"原文证据"）下的证据 block 做截断（默认保留前 4 条，对齐 EDITORIAL_POLICY）
- `reference-check <reference_page_id> [<target_page_id>] [--all --limit N]`：以 reference 页（如 QueryLoop 样板）为基准比对其他页的结构 / 属性 / 证据数，输出 conformance green/yellow/red + 差距清单
- `seed-related-pages <source_page_id> [--dry-run]`：读取 source 页的 `infer_related_concepts` 命中（硬编码 topic map），对未在 Wiki 中存在的概念建占位页（带 `<placeholder>` marker / `Verification = Needs Review`），等会话层精修
- `lint`：按 `Verification` 列出 Expired / Needs Review 的 Wiki 页

所有子命令均写 `raw/notion_dumps/YYYY-MM-DD-audit-log.jsonl`（含 error 记录）。

脚本本身不调用 LLM API；主题级判断由 Claude Code 会话层承担（见 `LLM_EXTRACTION_DESIGN.md`，包含"会话层留痕约定"段）。

## 设计评审

设计与实现之间的差距、修复优先级记录在 `DESIGN_REVIEW.md`。每一版评审都需标注评审日期、被评审文件状态、以及评审模型版本（如 Claude Opus 4.7 / `claude-opus-4-7`），用于后续追溯。当前最新版本：**v2 · 2026-04-21**。
