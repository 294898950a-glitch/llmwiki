# Editorial Policy · 永久笔记结构

本文件定义 Wiki 库里"永久笔记（Permanent Note）"的最小结构约束。目的是让同一个知识对象被多次 compile、多次会话编辑后仍然**收敛**，而不是随着来源材料堆积越写越散。

这份策略是 `scripts/notion_wiki_compiler.py` `--auto-refine` 与会话层 `log-session-event` 共同对齐的目标形态。

## 目标形态

一条符合永久笔记标准的 Wiki 页面应包含：

### 必填属性

| 字段 | 类型 | 含义 |
|---|---|---|
| `Name` | title | 主题名，已收束（去章节号、去来源标识） |
| `Canonical ID` | unique_id 或 rich_text | 该对象的唯一稳定 id |
| `Verification` | status | 当前可信度（如 `Fresh` / `Needs Review` / `Expired`） |
| `Compounded Level` | number | 累计合并的 raw 次数 |
| `Last Compounded At` | date | 最近一次合并时间 |

### 正文结构

按顺序至少包含下面这些 heading_2 段（缺段视为未完成永久笔记化）：

1. **定义**
   - 单句或两句话把该对象"是什么"说清楚
   - 不引用 raw 标题，不以"本材料讨论……"开头
2. **核心判断**
   - 这条笔记想留下的最重要结论
   - 必须是陈述性命题，不是问题或未决事项
3. **关联概念**
   - 与该对象在同一概念空间的其他 Wiki 页，最好以 Notion internal link 形式插入
   - 若不存在对应 Wiki 页，用 Canonical ID 或主题名占位
4. **原文证据**
   - 不超过 4 条来自原始材料的关键引文
   - 每条附 source URL 或 raw page id
5. **增量更新**（可多条）
   - 每次 compile 追加一段，带日期戳
   - 保留但不作为主要判断来源；主要判断应被折叠回"定义 / 核心判断"两段
6. **差异分析**（可选，有冲突时才出现）
   - 新资料推翻旧结论时追加，标注新旧差异
   - 不覆盖旧的"核心判断"段；由会话层评估后决定是否重写核心判断

### 可选段落

按主题密度决定是否出现：

- **为什么重要**：对高优先级主题可加
- **关键机制**：对机制/系统类主题可加
- **实现信号**：对工程/架构类主题可加
- **与相邻概念的区别**：当存在容易混淆的邻近对象时加

## 非目标

下列**不应**出现在永久笔记正文：

- raw 来源的完整正文复制（应放在 raw 层，不进 wiki）
- 单条 compile 触发时脚本生成的"启发式判断"原句（应被会话层替换为真正的核心判断）
- 多条几乎相同的"增量更新"段（应由 `cleanup-wiki-page` 去重）
- 会话层的个人风格注释（应落到 session-log，不进 wiki 正文）

## 分工

| 动作 | 由谁执行 | 工具 |
|---|---|---|
| 补齐 heading 骨架 + 原文摘要 | 脚本 | `compile-from-raw --auto-refine` |
| 填充真正的"定义"、"核心判断"正文 | 会话层 | 人工或 agent call，配合 `log-session-event` 留痕 |
| 去重增量更新 block | 脚本 | `cleanup-wiki-page` |
| 决定 Verification 状态变更 | 会话层 | 手动修改 Notion 或走未来的 `refine-wiki-page` 子命令 |
| 差异分析 block | 脚本（可选） | `compile-from-raw --emit-diff`，只在 raw 内容变化后触发 |

## 验证 checklist

想检查某条 Wiki 页是否达到永久笔记状态时，按以下顺序确认：

1. Title 是否已收束为主题名（不含章节前缀、不含冒号切分左右乱）？
2. 是否有 `Canonical ID` / `Verification` / `Compounded Level` / `Last Compounded At` 四个属性？
3. 正文是否同时存在"定义 / 核心判断 / 关联概念 / 原文证据"四段？
4. "定义"与"核心判断"是否由会话层填过（不是脚本启发式原句）？
5. "原文证据"是否 ≤ 4 条，且每条带 source？
6. "增量更新"是否去过重（同内容 block 没有多份）？

全部满足 → 该条可标为 `Verification: Fresh`。任何一条不满足 → 标为 `Needs Review`，并在 session-log 登记缺口。

## 机器化检查

本策略**写入 compile 路径不强制**，但已经有机器化评估入口：

```
python scripts/notion_wiki_compiler.py check-editorial <wiki_page_id>
python scripts/notion_wiki_compiler.py check-editorial --all --limit 50
```

脚本检查：必填属性 4 项是否为空、title 是否仍带"第N章"前缀或未切分的冒号、正文是否包含 `定义 / 核心判断 / 关联概念 / 原文证据` 四个 heading_2、`原文证据` 条目是否 ≤ 4、`增量更新` section 是否有重复。

`原文证据` 条数超过 4 时可直接用 `consolidate-evidence <page_id> --keep 4` 裁剪；若要替换某个 heading（如 `定义 / 核心判断`）下的内容，用 `compile-from-raw --merge-mode replace --replace-heading <text>` 由脚本做删-加操作。

输出 `green`（零问题）/ `yellow`（≤2 问题）/ `red`（>2 问题），exit code 0（green）或 1（其他）。会话层应把 `yellow` / `red` 页面作为下一轮 live editorial 的候选队列。

## 后续演进

- **暂不强制**：当前 compile / upsert 不会因为 check-editorial 为非 green 而阻塞写入；后续可考虑加 `--enforce-editorial` flag 做硬门禁。
- **自动补段**：未来可基于 `check-editorial` 结果，结合 Claude Code 会话层做"按缺段 append 骨架 + 留占位"的动作。
- **与 `--auto-refine` 的耦合**：`--auto-refine` 的 heading 模板应与本策略保持同步；如果 heading 名字或顺序在这里调整，脚本里的 `build_structured_refinement_blocks` / `build_deepening_blocks` / `REQUIRED_EDITORIAL_HEADINGS` 三处需要同步改。
