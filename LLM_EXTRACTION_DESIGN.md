# LLM Extraction Design

## 目标

本文档定义 `llmwiki` 中 LLM 抽取与判断逻辑的归属位置，解决下面这个长期挂账问题：

- LLM 抽取到底放在脚本层，还是放在 Claude Code 会话层
- 哪些步骤必须结构化、可重复
- 哪些步骤允许保留人工监督

结论先行：

- **Notion API 调用、schema 检查、raw 读取、wiki 写回** 放在脚本层
- **实体提取、候选判断、冲突解释、摘要重写建议** 放在 LLM 层
- **当前阶段的 LLM 层优先放在 Claude Code 会话内，由 agent 调工具完成**
- **暂不把 LLM 调用直接写死到 `scripts/notion_wiki_compiler.py`**

这是当前阶段最稳的方案。

## 为什么不先把 LLM 写进脚本

如果现在就把 Anthropic 或其他模型调用直接塞进脚本，会带来几个问题：

1. schema 和 prompt 都还不稳定
2. 冲突处理策略还没定稿
3. 用户仍在快速调整 Notion 字段和知识对象结构
4. 一旦脚本内 prompt 固化，后续每次改策略都要同步改代码、配置和评审文档

当前项目还处在“把最小闭环跑通后，开始定义判断策略”的阶段。这个阶段更适合：

- 把 API / IO / 写回逻辑脚本化
- 把判断和解释保留在 Claude Code 会话中

等判断标准稳定后，再把可重复部分下沉到脚本或服务。

## 分层结论

### 1. 脚本层负责什么

脚本层负责确定性、结构化、可重放的动作：

- 读取 `.env`
- 检查 raw / wiki schema
- 从 Raw Inbox 读取 page body
- 在 Wiki 数据库中查询候选页面
- 创建或更新 Wiki page
- 回写 Raw 状态
- 输出结构化 JSON 结果

这些事情的特点是：

- 有明确输入输出
- 不依赖主观判断
- 适合写成可测试代码

### 2. LLM 层负责什么

LLM 层负责高不确定性、需要语义判断的动作：

- 从 raw 正文中提取实体、别名、主题
- 判断某条资料是在补充旧页，还是应该新建页面
- 判断是否发生“同名不同实体”
- 解释新旧内容冲突在哪里
- 重写摘要段
- 生成更高质量的增量更新文本

这些事情的特点是：

- 依赖上下文理解
- 很难用纯规则写好
- 需要保留解释能力

### 3. 当前推荐的执行方式

当前阶段推荐：

- **Claude Code 会话 = LLM 决策层**
- **`scripts/notion_wiki_compiler.py` = API 执行层**

也就是：

1. Claude Code 读取 raw 内容
2. Claude Code 基于 prompt 做判断
3. Claude Code 再调用本地脚本执行：
   - `search`
   - `compile-from-raw`
   - 未来的更细粒度 update 命令

这是一种“人和 agent 共同监督、脚本负责落盘”的模式。

## 当前阶段推荐工作流

### 模式 A：人审 + agent 执行

适合现在：

1. 用户说“处理这条 raw”
2. Claude Code 读取 raw 内容
3. Claude Code 做以下判断：
   - 候选实体
   - 可能命中的 wiki 页面
   - 是更新还是新建
   - 是否存在冲突
4. Claude Code 把判断告诉用户
5. 用户确认后，再调用脚本真正写入

优点：

- 风险低
- 解释清楚
- 适合 schema 还在变化的阶段

缺点：

- 自动化程度不高

### 模式 B：agent 自动判断 + 脚本执行

适合下一阶段：

1. Claude Code 自动读取 raw
2. 自动判断目标 wiki 页
3. 自动生成摘要与增量块
4. 自动调用脚本落盘
5. 只在高风险冲突时停下来询问用户

要进入这个阶段，至少要先满足：

- 候选排序规则稳定
- 冲突策略稳定
- smoke test 不止一轮
- raw / wiki schema 基本固定

## 什么时候再把 LLM 下沉到脚本

只有在下面条件满足后，才建议把 LLM API 调用写进脚本：

1. prompt 已稳定
2. 冲突处理策略稳定
3. 需要定时任务或批处理无人值守运行
4. 用户接受把“模型判断”放到自动流程里

到那时，才适合新增例如：

- `compile-from-raw --auto`
- `lint --auto-review`
- `resolve-conflicts --model`

当前还不建议这么做。

## 对下一阶段实现的影响

基于这个决定，接下来应按下面顺序推进：

1. 保持脚本层专注于查询、更新、回写
2. 在 Claude Code 会话里先定义“候选排序 / 冲突判断 / 合并策略”
3. 先跑几轮人工监督的真实编译
4. 观察哪些判断足够稳定，再决定是否下沉到脚本

## 当前决定

**2026-04-23 更新**：在 2026-04-22 的"模式 B 有限形式"基础上扩展为**双 provider 校验闭环**：

- **Primary generator（默认 Kimi / `kimi-k2.6`）**：`llm-refine` / `llm-refine-page` 负责写入"有解读"内容。不等校验、直接落盘。
- **Post-hoc validator（默认 DeepSeek / `deepseek-reasoner`）**：`llm-validate [--annotate]` 子命令对已写入的 wiki 页事后评估，按 5 项标准（有解读 vs 提要 / 段职责遵守 / 类比质量 / 风格合规 / 内在一致性）输出 JSON `{pass, score 0-10, issues, strengths, suggestion}`。`--annotate` 时把结果作为 callout block append 到页面底部做批注，不动正文。
- **双 provider 独立运行**：不阻塞、不级联失败。validator 通过 provider 交叉（另一家模型看另一家的输出）降低单一模型偏好带来的盲点。
- **脚本 provider 抽象**：`LLM_PROVIDERS` dict 注册 provider（`endpoint` / `default_model` / `env_key` / `env_key_file` / `fixed_temperature`）；key 从 `KIMI_API_KEY` / `DEEPSEEK_API_KEY` inline 值或 `*_API_KEY_FILE` 路径读取；`fixed_temperature` 用于 kimi-k2.6 这种强制 temperature=1 的模型覆盖用户传值。
- **llm-refine-page 的整页模式**：单次 API 调用对多段做重写，注入 cross-section directive 强制各段用不同锚点 / 不同类比，从根上治理"段间同质化"（实测比多次单段调用更能避免所有段以同一引子开场）。
- **默认 prompt 固化为 Style J**：锚点 + 费曼深入浅出 + 日常类比 + 类比回溯解说；按 heading 注入 section role guidance；条目型 section（关键机制 / 实现信号）自动走 JSON list mode。
- **reasoning + 生成内容完整落盘**：prompt / reasoning_content / content / usage / provider / model 写入 `raw/notion_dumps/YYYY-MM-DD-llm-refine-log.jsonl`；validator 结果单独追加到同一 log 或 callout。

### 仍归会话层的工作

以下语义判断仍不下沉到脚本：

- 候选选择（canonical_id 命中或 alias 命中时是否为同一对象）
- 冲突解释（新旧结论冲突时如何保留证据）
- 高风险 tier 4 的 merge/split 决策
- 样板页（如 QueryLoop）的 editorial 质量把关
- 跨页面的结构一致性审查（通过 `reference-check` 后的人工判断）
- validator 批注的最终采信或驳回（`llm-validate --annotate` 只留评审，不自动改正文）

### 原立场（2026-04-22 前）

保留作为历史记录：

- **短期**：LLM 抽取放在 Claude Code 会话层；Notion API 执行放在本地脚本层
- **中期**：待候选排序与冲突策略稳定后，再考虑脚本内模型调用
- **当前不做**：不在 `scripts/notion_wiki_compiler.py` 中直接接入模型 API

这份立场已被 `llm-refine` / `llm-refine-page` / `llm-validate` 三路 LLM 入口突破；其他语义工作（候选选择、冲突解释、tier 4 merge/split）仍按原立场留在会话层。

## 会话层留痕约定

既然 LLM 判断放在 Claude Code 会话层，每次判断（选候选、合并 vs 新建、风险等级）必须**可追溯**。否则后续无法回归、无法在模式 A 跑够轮数时决定是否切到模式 B。

### 落盘方式

脚本提供了 `log-session-event` 子命令。会话层每做一次对 raw 的语义判断，应立即调用它：

```bash
python scripts/notion_wiki_compiler.py log-session-event \
  --model claude-opus-4-7 \
  --raw-page-id <raw-uuid> \
  --wiki-page-id <wiki-uuid or 空> \
  --tier canonical_id|title|alias|fuzzy|none \
  --decision update|create|ask_user|skip \
  --risk low|medium|high \
  --notes "为什么这么判断，关键证据"
```

命令会把事件 append 到 `raw/notion_dumps/YYYY-MM-DD-session-log.jsonl`。该文件不入 git（在 `.gitignore` 中），只作为本地审计，需要时上传到团队共享位置。

### 字段定义

| 字段 | 含义 |
|---|---|
| `timestamp` | UTC iso8601，脚本自动填 |
| `model` | 做判断的模型 id，如 `claude-opus-4-7` / `gpt-5-codex` |
| `raw_page_id` | 被判断的 Raw Inbox page（可空，若判断与特定 raw 无关） |
| `wiki_page_id` | 判断涉及的 Wiki page（创建前可为空） |
| `tier` | 对应 `MERGE_STRATEGY.md` 的候选排序层级，或 `none` 表示未命中 |
| `decision` | `update`（追加到已有页）/ `create`（新建）/ `ask_user`（停下来询问用户）/ `skip`（跳过） |
| `risk` | 低 / 中 / 高，对应 MERGE_STRATEGY 的冲突分级 |
| `notes` | 自由文本解释，必填。越详细越利于回归 |
| `input` | 可选结构化输入：候选列表、原文片段、命中正则 |

### 什么时候必须记

以下场景**必须**留痕，否则跑完无法回溯：

1. `compile-from-raw` 返回 `match_strategy: alias`（触发 tier 3 review）——即便脚本已经写入，会话层要登记为什么认为这次 alias 命中是可接受的。
2. `compile-from-raw` 返回 `action: skipped_duplicate_body`——登记发现了跨 raw 重复，决定保持现状。
3. 会话层主动跳过某条 raw（`decision: skip`）。
4. 对 MERGE_STRATEGY tier 4 的场景（主题相近无明确标识），会话层判断"不应自动合并"时。

### 与 `compile-log.jsonl` / `audit-log.jsonl` 的区别

| 文件 | 谁写 | 内容 |
|---|---|---|
| `audit-log.jsonl` | 脚本 | 所有命令的执行事件（含成功/失败） |
| `compile-log.jsonl` | 脚本 | compile-from-raw 的结构化结果 |
| `session-log.jsonl` | 会话层通过 `log-session-event` | 语义判断的 why + 依据 |

三者互补：审计问"发生了什么"、编译问"这条 raw 变成了什么"、session 问"为什么这样判断"。
