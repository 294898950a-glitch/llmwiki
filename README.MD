# LLM Wiki

`llmwiki` 是一个以 Notion 为中心的 LLM Wiki 项目，采用双数据库结构：

- `Raw Inbox Database`：原始资料入口，数据库属性负责索引，page body 存放原始材料正文。
- `Wiki Database`：最终知识库，负责沉淀经过编译和累积更新后的知识对象。

本项目当前目标不是生成本地 Markdown Wiki，而是实现 `raw -> compile -> wiki -> raw 回写` 的最小闭环，并在此之上逐步引入 LLM 精修与事后校验。

## Current Position

**alpha+ · 知识编译器**。当前已具备三层：

1. **Notion I/O 层**：双库配置、schema 探测、数据库内查询、属性回写。
2. **启发式编译层**：`compile-from-raw` / `compile-queue` / `--auto-refine` 的规则化"骨架 + 摘要 + 启发式正文"；匹配按 `Canonical ID → 标题 → Aliases` 逐级降级，冲突有 tier 3 / tier 4 门控。
3. **LLM refine / validate 层**：Primary generator（默认 Kimi / `kimi-k2.6`）通过 `llm-refine` / `llm-refine-page` 写入"有解读"内容；Post-hoc validator（默认 DeepSeek / `deepseek-reasoner`）通过 `llm-validate --annotate` 以 callout 形式批注，不阻塞写入。

项目已越过"能不能写进去"，正在回答"写进去之后能不能形成持续复利的知识对象库"。单页质量、对象级 compounding、概念网络自增长仍在推进中，完整路线图见 [`V15_EXECUTION_PLAN.md`](./V15_EXECUTION_PLAN.md)。

## Implemented

### 子命令

由 `scripts/notion_wiki_compiler.py` 的 `build_parser()` 注册，权威清单以代码为准：

| 分类 | 子命令 |
|---|---|
| Schema / 查询 | `inspect-schema` · `search` |
| 编译核心 | `compile-from-raw` · `compile-queue` · `upsert-note` |
| 审计 / 留痕 | `log-session-event` · `lint` |
| 结构体检与清理 | `check-editorial` · `consolidate-evidence` · `reference-check` · `cleanup-wiki-page` |
| 概念网络 | `seed-related-pages` · `rewrite-section` · `link-pages` · `link-concepts-in-page` |
| LLM 层 | `llm-refine` · `llm-refine-page` · `llm-validate` |

### 能力概览

- **编译闭环**：raw body 递归读取 + `body_hash` 幂等（同 raw 跳过 / 跨 raw 同 hash 跳过）+ `Status` / `Processed At` / `Target Wiki Page` 回写
- **匹配分层**：`canonical_id` / `title` / `alias` / `fuzzy` 逐级降级；同策略多候选直接报错；`alias` / `fuzzy` 有 `--strict-*` 硬停
- **merge 模式**：`--merge-mode {append,propose,replace}` 覆盖追加 / 预览 / 替换三种 compounding 路径
- **冲突证据**：`--emit-diff` 产出段落级 unified diff 作为 `差异分析 YYYY-MM-DD` block 追加
- **LLM 层**：`llm-refine` 单段重写 / `llm-refine-page` 整页重写 / `llm-validate --annotate` 事后 callout 批注；默认 primary = Kimi、validator = DeepSeek；prompt / reasoning / content / usage 全量落盘
- **体检与整理**：`check-editorial` / `reference-check` 给 green / yellow / red 分级；`consolidate-evidence` / `cleanup-wiki-page` 做结构清理；`seed-related-pages` 建占位页
- **概念网络**：`Related Pages` self-referencing relation（`link-pages`）+ 正文 page mention（`rewrite-section --mention-map`）双实现

能力细节（prompt style / JSON schema / editorial checklist / provider 抽象字段 / 落盘格式等）见下列二级文档：
- `LLM_EXTRACTION_DESIGN.md`（LLM 层 prompt / provider / 校验协议）
- `EDITORIAL_POLICY.md`（永久笔记结构 checklist）
- `MERGE_STRATEGY.md`（候选排序 / 冲突分级）
- `CLAUDE.md`（子命令完整签名与 flag 列表）

### 配置

- Notion：`NOTION_API_KEY` / `NOTION_RAW_INBOX_DB_ID` / `NOTION_WIKI_DB_ID`
- LLM（按需）：`KIMI_API_KEY` / `DEEPSEEK_API_KEY`（或对应 `*_API_KEY_FILE`）
- 非 LLM 命令不依赖 provider key

## Live Examples

两条已验证、不会漂移的真实引用；进行中的精修状态与占位页进度见 [`V15_EXECUTION_PLAN.md`](./V15_EXECUTION_PLAN.md) §1.1–§1.2。

- **Smoke test 闭环**：raw `3496e2cd-6e4f-81a2-988a-c9fd757c1b42` → wiki `3496e2cd-6e4f-816e-919b-fd80753dcc9b`，`raw → wiki → raw 回写` 闭环已验证
- **QueryLoop 样板页**：wiki `3496e2cd-6e4f-81b2-b037-d76d653b9c1f`，用作 `reference-check` 的对标基准

## Known Limits

按影响排序：

1. **对象级 compounding 还未做实**：当前更像"把新材料写进页"，还不像"对已有知识对象做修订 / 压缩 / 整合"；同 wiki 对象"已达目标内容即便 raw 变了也应跳过"的严格幂等尚无实现（需先定义"目标内容"）。
2. **决策层仍偏弱**：`same-object / new-facet / distinct-object / ambiguous` 分流不够稳；高风险 merge / split / review 仍留在会话层。
3. **深度整理依赖会话层**：脚本能给骨架 + 启发式段落 + LLM 单段 / 整页重写，但高质量永久笔记仍需会话层做取舍；Style J prompt 已固化，但候选选择 / 冲突解释 / 核心判断是否重写不下沉。
4. **概念网络未进入自增长**：占位页需手动 `seed-related-pages` + 会话层精修；还没有稳定的"页派生页"机制。
5. **validator 批注不自动改正文**：`llm-validate --annotate` 只留 callout，采信或驳回仍归会话层。
6. **`check-editorial` 驱动的自动补段**：当前只评估不行动，缺段页面报 yellow / red，脚本尚无"按 check-editorial 缺口批量补骨架"能力。
7. **基于 diff 的核心判断重写**：`--emit-diff` 只记录差异，摘要重写是会话层 editorial 决策，脚本不代做。
8. **自动回滚缺失**：手工回滚见下文"幂等性与回滚"。

## 幂等性与回滚

`compile-from-raw` 运行语义：

- 相同 raw body（按 `body_hash` 比对）再跑默认 `skipped_unchanged`；若 Raw Status 仍非完成态，仍会推进到 `Done` 避免 queue 重复拉取
- 跨 raw 同 hash → `skipped_duplicate_body`（记录 `originating_raw_page_id`），`--force` 可绕过
- 命中已有 wiki 页面时更新属性 + 追加 `增量更新` block；`--auto-refine` 附加启发式正文；不做整页覆盖
- 重跑语义：**同内容跳过、内容变化继续追加而非覆盖**

手工回滚：到 Notion 删除目标 wiki 页 → 把 Raw 记录 `Status` 改回 `Not started` / 清空 `Processed At` / 清空 `Target Wiki Page`。若要重新做 smoke test，建议新建 raw page 而不是复用旧条目。

## Roadmap

基于 `README_REVIEW.md` v15 成熟度评估（基础设施 75 / 编译闭环 70 / 知识质量 40 / 复利能力 35 / 整体 55）的四阶段路线：

- **Alpha → Usable**：压低 raw 输入摩擦、收缩 auto-refine 为保守模式、把单页产物做到"可读"
- **Usable → Compounding**：对象级幂等 / section-level 更新规则 / update vs new-facet vs new-page vs needs-review 分流
- **Compounding → Reviewable**：decision log / evidence-preserving update / review queue
- **Reviewable → Productized**：capture UI / review UI / task / job 状态流

具体执行清单见 [`V15_EXECUTION_PLAN.md`](./V15_EXECUTION_PLAN.md)。当前最值得做的三件事（v15 建议）：把 QueryLoop 打成真正样板页 / 长出相邻页（QueryEngine · Context Governance · Recovery Logic）/ 把"更新已有知识对象"做稳。

## 版本控制

本项目在 git 仓库中管理。当前分工（基于多模型协作）：

- **Claude Opus 4.7 维护**：`README.MD` / `CLAUDE.md` / `scripts/` / `schema/` / `LLM_EXTRACTION_DESIGN.md` / `MERGE_STRATEGY.md` / `EDITORIAL_POLICY.md`
- **GPT-5 Codex 维护**：`README_REVIEW.md` / `DESIGN_REVIEW.md` 两份评审文档
- `CLAUDE.md` 每次实质性修改要 bump 版本号（日期.rN），便于 review 锚定

提交约定：
- 每完成一个清晰阶段就提交一次，不要把多类改动混成一个大 commit
- 代码改动和文档同步分开提交，review 文档独立提交

敏感信息约束：
- `.env` 不入 git
- 不要把真实 `NOTION_API_KEY` / `KIMI_API_KEY` / `DEEPSEEK_API_KEY` 提交到仓库
- `raw/notion_dumps/*.jsonl` 已在 `.gitignore` 中忽略；schema 快照 JSON 入库，便于追溯字段变更

## 当前关键文件

- [`CLAUDE.md`](./CLAUDE.md)：系统设计蓝图
- [`DESIGN_REVIEW.md`](./DESIGN_REVIEW.md)：设计与实现差距评审
- [`EDITORIAL_POLICY.md`](./EDITORIAL_POLICY.md)：永久笔记结构与分工 checklist
- [`LLM_EXTRACTION_DESIGN.md`](./LLM_EXTRACTION_DESIGN.md)：LLM 抽取归属、执行策略、双 provider 校验闭环
- [`MERGE_STRATEGY.md`](./MERGE_STRATEGY.md)：候选排序、冲突处理与合并策略
- [`V15_EXECUTION_PLAN.md`](./V15_EXECUTION_PLAN.md)：基于 review v15 成熟度评估的四阶段执行方案
- [`README_REVIEW.md`](./README_REVIEW.md)：README 与实际状态一致性评审
- [`schema/notion_wiki_mapping.example.json`](./schema/notion_wiki_mapping.example.json)：字段映射模板
- [`scripts/notion_wiki_compiler.py`](./scripts/notion_wiki_compiler.py)：Notion API 执行层 + 多 provider LLM 封装
