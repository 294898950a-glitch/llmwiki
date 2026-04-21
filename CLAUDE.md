# LLM Wiki · Notion Wiki 运行蓝图

> **Version**: 2026-04-21.r1
> 每次实质性修改本文件需要 bump 版本号（日期.rN），并在 git 中提交。`DESIGN_REVIEW.md` 的评审锚点同时引用本版本号与对应 commit SHA。

这是一个以 Notion Wiki 为主库的 LLM Wiki 系统。目标不是把资料归档成越来越多的文件，而是把新资料持续编译进已有知识对象，让知识密度随着时间增加。

## 系统定位

这是一个三级编译模型：

1. `raw/`：原始资料层。存放从 Notion Inbox、网页剪藏、PDF 或其他来源抓下来的只读快照，用于审计、追溯和重跑。
2. `schema/` 与 `scripts/`：逻辑层。负责定义属性映射、判定规则、upsert 策略和 lint 规则。
3. Notion Wiki：最终产物层。所有正式知识对象以 Wiki 数据库页面形式存在，支持 Verification、Relation、状态管理和后续协作。

## 当前阶段

**手动迭代期**。先把最小闭环跑通：`inspect-schema -> search -> decide -> upsert -> lint`。不要过早把字段、分类法或写作模板定死。每次运行前先观察真实数据库结构，再决定映射。

**核心原则**：你是 Wiki 维护者，不是笔记归档器。默认优先更新既有页面，而不是新建页面。只有在找不到可信候选页面时，才允许新建页面。

## 目录结构

```text
llmwiki/
├── .env                             # Notion 凭证（不入 git）
├── .env.example                     # 模板
├── .gitignore
├── CLAUDE.md                        # 本文件
├── raw/
│   ├── notion_dumps/                # 原始资料或页面快照
│   └── .sync_state.json             # 增量处理状态
├── schema/
│   └── notion_wiki_mapping.example.json
├── scripts/
│   └── notion_wiki_compiler.py      # 最小脚本骨架
└── wiki/                            # 保留为调试输出目录，不再是主产物
```

## 主流程

### Raw Inbox · 数据库做索引，Page Body 放原始材料

`raw` 层采用 Notion 双层结构：

- `Raw Inbox Database`：负责索引、状态、优先级、relation 和自动化队列。
- `Raw Entry Page Body`：负责承载原始材料本体，例如网页剪藏、PDF 摘录、手工随记、附件和引用片段。

默认工作方式：
1. 用户在 `Raw Inbox` 数据库中新建一条 page。
2. 用户把原始材料粘贴或保存到该 page 的正文中。
3. 系统扫描 `Status = New` 的 raw 记录。
4. 系统自动补全或修正索引字段，例如 `Captured At`、`Type`、`Priority`、`Source URL`。
5. 系统读取该 page body 的原始内容，送入 Wiki 编译流程。
6. 编译完成后，系统回写 raw 记录的处理状态与目标 Wiki 页面 relation。

这意味着 raw 层不是“一个字段装全部内容”，而是“数据库属性管索引，page 正文管内容本体”。

### Inspect Schema · 检查 Wiki 数据库结构

**输入**：`.env` 中的 `NOTION_API_KEY` 与 `NOTION_DATABASE_ID`
**输出**：Wiki 数据库属性报告

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

1. 用户把 raw 内容保存到 `Raw Inbox` 的某条 page 正文。
2. 系统识别到新增或待处理记录，自动补索引字段。
3. 系统根据 raw 的 `Status`、`Type`、`Priority` 决定是否进入编译队列。
4. 系统读取 raw page body，提取实体、别名、主题、时间范围。
5. 系统去 `Wiki Database` 搜索候选页面。
6. 若命中同一知识对象，则更新已有 Wiki 页面；若未命中，则新建页面。
7. 系统更新 Wiki 页面的属性和正文内容。
8. 系统回写 raw 记录：
   - `Status = Compiled`
   - `Processed At = 当前时间`
   - `Target Wiki Page = 对应 Wiki 页面`

简化理解：
- 你负责把 raw 材料放进 page body。
- 系统负责补索引、排队、编译、回写状态。

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

1. 每次运行前检查 `.env` 是否完整；若缺失密钥或数据库 ID，直接停止并提醒用户。
2. 不要把密钥写进任何会被提交到 git 或同步分享的文本中。
3. 不要假设 Notion Wiki 一定已经包含某些字段；脚本应报告缺口，而不是硬编码崩溃。
4. 对已存在页面的修改应优先追加或局部更新，避免整页替换。
5. 每次运行结束都要给用户一份简短清单：检查了哪些属性、命中了哪些页面、新建了几页、更新了几页。

## 当前可用脚本

`scripts/notion_wiki_compiler.py` 提供最小能力：
- `inspect-schema`
- `search`
- `upsert-note`
- `lint`

第一版只追求把 Notion API 的最小闭环打通，不追求自动摘要质量。

## 设计评审

设计与实现之间的差距、修复优先级记录在 `DESIGN_REVIEW.md`。每一版评审都需标注评审日期、被评审文件状态、以及评审模型版本（如 Claude Opus 4.7 / `claude-opus-4-7`），用于后续追溯。当前最新版本：**v1 · 2026-04-21**。
