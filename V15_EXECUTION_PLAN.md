# V15 执行方案 · 从 Alpha 到 Karpathy-style

本方案响应 `README_REVIEW.md` v15（GPT-5 Codex, 2026-04-22）的成熟度判断。v15 给出 55/100 整体成熟度、四阶段路线图、三项最值得做的事。本文件把那份判断拆成**谁、做什么、什么时候做**的具体清单。

- **锚定 git 状态**：`49f8235`（HEAD）
- **制定者**：Claude Opus 4.7（code / README 维护角色）
- **覆盖范围**：脚本、主文档、会话层协作约定；review 文档仍归 Codex 维护

## 0. 先对齐定位（立即、小改动）

v15 §8.1 要求"收缩对当前状态的宣传，避免把 alpha 说成成熟 wiki"。

- [ ] **README 顶部 alpha 声明**：在第一段后加一行"当前是 alpha 系统，已能跑通 raw→wiki 闭环但尚未具备稳定的知识复利"。文字版，不改代码。
- [ ] **CLAUDE.md 系统定位段对齐**：把"最终产物层"改成"当前是 alpha 编译器；knowledge compounding 是目标，不是已达成的状态"。
- [ ] **Version bump**：CLAUDE.md `r6 → r7`。
- [ ] **产出**：1 笔 `docs: downgrade self-claim from mature wiki to alpha compiler`。

## 1. v15 §6 最值得做的三件事

### 1.1 把 `QueryLoop` 打成真正样板页

**目标**：让后续所有 wiki 页都能对齐到同一套单页标准。

- [ ] **会话层**（用户或 agent）：真人把 `QueryLoop`（wiki id `3496e2cd-6e4f-81b2-b037-d76d653b9c1f`）手工精修到"green"——所有 4 个必需 heading 都有真实段落、原文证据 ≤ 4、无重复增量更新、Canonical ID 填到位。
- [x] **代码侧**（我，2026-04-22）：
  - `reference-check <reference_page_id> [<target_page_id>] [--all --limit N]` 子命令
  - 新增 `extract_heading_structure` / `compare_page_to_reference` helper
  - 对比 heading_2 集合 / 必填属性 / 证据数；输出 `conformance: green|yellow|red` + `missing_headings_vs_reference` / `missing_properties_vs_reference` / `extra_headings_vs_reference` 清单
  - `--all` 模式扫全库，汇总 green/yellow/red 计数
  - 识别占位页（`<placeholder>` marker 触发 `compliance: placeholder` 豁免）
- [x] **文档侧**（我）：
  - `EDITORIAL_POLICY.md` 加"占位页豁免"段 + reference-check 与 check-editorial 关系说明
  - `CLAUDE.md` / `README.MD` 脚本清单补 `reference-check`
- [ ] **会话层剩余**：QueryLoop 精修到 green，然后以它为 reference 对其他 wiki 页跑 `reference-check --all`。脚本就绪，等样板内容。

### 1.2 长出相邻页面（QueryEngine / Context Governance / Recovery Logic 等）

**目标**：让概念网络从点变图。

- [x] **代码侧**（我，2026-04-22）：
  - `seed-related-pages <source_page_id> [--dry-run]` 子命令
  - 读 source 页 body，走 `infer_related_concepts` 拿 topic map 命中的概念标签
  - 对每个概念 `search_in_database` 精确匹配标题；已存在就记录到 `existing_concept_pages`
  - 不存在则 `create_page` 占位页：title = 概念名 / `Verification = Needs Review` / children = `build_placeholder_blocks`
  - `build_placeholder_blocks` 输出：`<placeholder>` marker 段 + 定义 / 核心判断 / 关联概念 / 原文证据 四个 heading_2 + 各段 TBD 占位文字
  - 跳过自引用（inferred concept 与 source title 同名）
  - `--dry-run` 不真建，只输出计划
- [x] **文档侧**（我）：
  - `EDITORIAL_POLICY.md` "占位页豁免"段：`check-editorial` 识别 `<placeholder>` → `compliance: placeholder`
  - `CLAUDE.md` / `README.MD` 脚本清单补 `seed-related-pages`
- [x] **概念图谱的结构 + 表达双实现**（2026-04-22 补做）：
  - 用户指出原先 seed 产物只是文字、无 Notion 链接 → 概念图谱数据层为零
  - 新增 `Related Pages` self-referencing relation 属性（`ensure_related_pages_property` 程序化创建）
  - 新增 `link-pages` 子命令维护 relation；`seed-related-pages` 自动回指 source
  - `rewrite-section --mention-map LABEL=ID` 支持正文 page mention
  - 给现有 7 个 wiki 页设全 Related Pages；QueryLoop / QueryEngine 的「关联概念」段重写为 mention
- [ ] **会话层剩余**：跑过 `seed-related-pages <QueryLoop_id>` 后，会话层逐一把占位页精修为真实永久笔记；决策走 `log-session-event`。当前 QueryEngine 已精修至 green，剩 Agent Runtime / State Management / Context Governance / Recovery Logic / Interrupt Handling 五页待精修。

### 1.3 "更新已有知识对象"做稳（重写摘要 / 合并证据 / 处理冲突）

**目标**：对象级 compounding——从 append-first 换到 compound-first。

- [x] **代码侧**（我，2026-04-22）：
  - **section-level merge 钩子**：`compile-from-raw --merge-mode {append,propose,replace}`
    - `append`：现状，追加 `增量更新` block
    - `propose`：不写入 wiki，输出结构化预览（候选 wiki 页 / match_strategy / 预期写入 block 数 / existing_body_hash 等）
    - `replace`：与 `--replace-heading <text>` 配合，找到 heading 删原 body block 再 append 新正文
  - **consolidate-evidence 子命令**：`consolidate-evidence <page_id> [--heading <text>] [--keep N] [--dry-run]`
    - 默认对"原文证据" heading 下的证据 block 保留前 4 条（对齐 EDITORIAL_POLICY 上限）
    - 支持 `--heading` 覆盖其他 section、`--keep` 覆盖条数上限、`--dry-run` 预演
  - 新增 `find_upsert_target` helper 把 upsert_note_to_wiki 的候选查找抽成独立函数，供 propose / replace / append 三路共用
  - 新增 `find_section_body` helper 定位 heading_2/heading_3 及其 body block 范围
- [x] **文档侧**（我，2026-04-22）：
  - `MERGE_STRATEGY.md` §冲突处理重写为"三模式对照表 + 通用约束"
  - `EDITORIAL_POLICY.md` §机器化检查段链接 `consolidate-evidence` 和 `--merge-mode replace`
  - `README.MD` 已实现能力段新增 `--merge-mode` 三行 + `consolidate-evidence` 一行
  - `CLAUDE.md` Version r7→r8，脚本清单 9→10 个子命令
- [ ] **会话层**（剩余，非脚本）：接管 `propose` 输出，决定 append / replace / skip；决策走 `log-session-event`
- **产出**：`feat(compile): add --merge-mode` + `feat(cleanup): add consolidate-evidence` + `docs: wire compounding into 5 docs`。

## 2. v15 §5 四阶段路线图落地清单

### 阶段 1：Alpha → Usable

v15 目标："让你愿意每天把材料丢进去 + 系统默认不乱写"。

代码：
- [ ] `--auto-refine` 默认关闭 ✅（`1f53622` 已做）
- [ ] `--strict-alias` / `--strict-fuzzy` 审查门 ✅（`d1acb8c` / `1892ce2` 已做）
- [ ] 新：`inbox-capture <title> [--source-url URL]` 子命令，建一条 Raw page with `Status=Not started`，降低入口摩擦。
- [ ] 新：`check-editorial --all --emit-review-queue`，按 yellow/red 输出待精修清单。

文档：
- [ ] README 顶部 alpha 声明（见 §0）

### 阶段 2：Usable → Compounding

v15 目标："让知识对象真正'变厚'"。

代码：
- [ ] 对象级幂等：扩 `find_prior_compile_by_body_hash` 到按 wiki_page_id 索引，若同一 wiki 页在 `check-editorial=green` 状态下再被命中且 raw body_hash 未新增证据，则 `skipped_object_complete`。
- [ ] section-level 更新（见 §1.3 merge-mode）
- [ ] 决策分流：`compile-from-raw` payload 新增 `suggested_decision: update|new_facet|new_page|needs_review`，由 tier + fuzzy 候选数 + EDITORIAL_POLICY 状态推导。

文档：
- [ ] `MERGE_STRATEGY.md` 补 "section-level merge 规则"段
- [ ] `EDITORIAL_POLICY.md` 加 "修订 / 压缩 / 整合"小节

### 阶段 3：Compounding → Reviewable

v15 目标："让系统能被审计、能解释"。

代码：
- [ ] 已做：`log-session-event` ✅、`--emit-diff` ✅
- [ ] 新：`list-review-queue` 子命令，聚合所有需要会话层介入的来源：
  - `check-editorial` yellow/red 页面
  - 带 `review_required: true` 的 audit-log 条目
  - `Verification = Needs Review` 的 wiki 页
  - compile-queue 最近一次 failures

文档：
- [ ] 新文档 `REVIEW_QUEUE.md`：定义 review queue 的输入源、优先级排序、会话层处理流程

### 阶段 4：Reviewable → Productized

v15 明确说"等前三阶段稳定再做"。本方案**不涵盖此阶段**。当前不应投入前端、capture UI、review UI、webhook / cron 等。理由：

- LLM_EXTRACTION_DESIGN.md 定了 LLM 在会话层；脱离会话做自动化意味着要重新决策。
- 单页质量、对象级 compounding、review queue 未稳前，产品化会把半成品状态放大。

## 3. 按优先级执行顺序

按 v15 §6 明示的优先级，结合代码/会话工作量：

1. **定位对齐**（§0）：文字改动，成本极低。**立即做**。
2. **单页样板**（§1.1）：先要会话层精修 QueryLoop，然后 `reference-check` 才有比对锚。**会话优先、代码次之**。
3. **对象级 compounding**（§1.3）：大工作量，但影响面最大。**会话样板就绪后启动**。
4. **相邻页生长**（§1.2）：在有 1 个样板页 + merge 能力后才值得做。**第三位**。
5. **review queue**（§2 阶段 3）：前几步都跑起来后再聚合。**最后**。

## 4. 分工

| 类别 | 谁 | 文件 |
|---|---|---|
| 代码改动 | Claude Opus 4.7 | `scripts/notion_wiki_compiler.py` / `schema/` |
| 主文档维护 | Claude Opus 4.7 | `README.MD` / `CLAUDE.md` / `EDITORIAL_POLICY.md` / `MERGE_STRATEGY.md` / `LLM_EXTRACTION_DESIGN.md` / 本文件 |
| Review 文档 | GPT-5 Codex | `README_REVIEW.md` / `DESIGN_REVIEW.md` |
| 会话层精修 | 用户 / Claude Code 会话 | Notion 页面正文 + `log-session-event` |
| Notion schema 调整 | 用户 | Raw Inbox / Wiki Database |

## 5. 本方案暂不做的事

- **真正的 LLM 抽取写进脚本**：`LLM_EXTRACTION_DESIGN.md` 立场未改，脚本仍不接入模型 API。会话层承担语义判断。
- **`infer_*` 族重构为"插件式规则"**：当前硬编码规则对 agent 领域过拟合；改成插件式需要先知道要加哪些领域，留给实际积累多个样板页后再做。
- **前端 / webhook**：见 §阶段 4。
- **自动回滚**：`compile-from-raw --emit-diff` 已记录差异；真正的自动回滚需要页面属性版本号或 Notion page history API，当前 punt。

## 6. 验收信号

方案执行完毕的标志：

- README 不再把项目说成"成熟 wiki"；自评维持 v15 的 alpha 定位。
- `QueryLoop` 页被 `check-editorial` 和 `reference-check` 都判为 green。
- 至少 3 个相邻概念页（占位或精修）在 Wiki 库中存在。
- `compile-from-raw --merge-mode propose` 能把 merge 决策交回会话层，决策走 `log-session-event`。
- `list-review-queue` 输出的 yellow/red 页面数量随时间下降。
- 下一版 review（v16）判定"compounding 维度"评分上升。

## 7. 回看

本方案写于 v15 评估之后；下一次 review（v16）应对照 §6 验收信号检查哪些已兑现、哪些仍差距。若 v16 判定 compounding 维度仍低于 50/100，需要回来重新考虑是否有更上游的瓶颈（比如 schema 不够 / 样板页标准太模糊）。
