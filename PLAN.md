# 写作沙盒 · 昔涟 — 开发方案（工作对照文件）

> 本文件是插件的唯一方案基准，开发时随时对照；需求变更先改这里，再动代码。

## 1. 定位

私聊专属的「委托式写作沙盒」：汐小姐用自然语言吩咐，昔涟作为背后的手，以
**独立上下文 + 指定提示词 + 约束 + 风格卡** 产出期望文字，并**自动落盘为 txt**。

与主聊天、关系本、陪伴记忆完全隔离，互不污染。

## 2. 交互方式（委托式，无命令前缀）

不需要记忆任何指令。自然说话即可：

- 「写一段她推门进来，雨还没干」 → 按当前风格卡产出，落盘 txt
- 「这段太素了，尺度拉高，重点写触感」 → 调整提示词参数并重写
- 「换第一人称，节奏快一点」 → 改约束，下次立即生效

所有「调提示词 / 改约束 / 换风格」封装为工具动作，由主模型（昔涟）根据对话
内容自主调用，用户只描述「要什么效果」。

### 工具清单（llm_tool，主模型可调用）

| 工具 | 作用 |
|------|------|
| `沙盒写作` | 按风格卡+约束+沙盒历史生成/续写/改写一段文字，落盘 txt |
| `沙盒设置` | 修改约束项（长度/视角/尺度/忌讳词/描写偏好等） |
| `沙盒收藏` | 把上一段输出存入 `favorites/`，供风格学习加权 |
| `沙盒学习素材` | 从 `library/` + `favorites/` 提炼风格卡 |
| `沙盒状态` | 查看当前风格卡与约束 |
| `沙盒大纲` | 为长篇作品编写/微调/重写/查看大纲（action=write 全新生成 / revise 基于现有大纲局部微调 / confirm 确认草稿 / discard 丢弃 / view 查看 / note 挂灵感便签 / link 章节接续） |
| `沙盒人设` | 统一格式人设卡生成/查看/删除/复核（scope=global 跨作品 / work 仅当前作品；action=audit 复核人物卡与大纲/正文一致性） |
| `sandbox_cleanup` | 旧稿清理：list 列超过 N 天未修改的文件（带编号），delete 按编号删（需先过目） |
| `沙盒重置` | 清空沙盒历史（可连约束风格卡一起清） |

## 3. 独立沙盒

- 沙盒历史存 `sandbox_history.jsonl`（role/content 序列），只属于沙盒会话。
- 每次生成 = 系统提示词 + 风格卡 + 约束 + 沙盒历史 + 本次指令 → 独立请求。
- 产出文本落盘 `outputs/YYYY-MM-DD_HHMM_标题.txt`，回复中给摘要+路径。

## 4. 提示词与约束（可编辑，不改代码）

约束表存 `constraints.json`，提示词模板存 `prompts.py`：

| 约束项 | 默认 |
|--------|------|
| 长度 | 300~800 字 |
| 人称视角 | 第二人称主视角 |
| 尺度档位 | 露骨（可调：暧昧/露骨/极限） |
| 忌讳词 | []（生成前过滤） |
| 描写偏好 | 感官细节六成、心理三成 |
| 节奏 | 慢热 |
| 固定人格特征 | 保留「♪」「人家」等昔涟底色 |

## 5. 素材学习 · 风格卡（2026-09-11 定稿）

**边界**：风格卡只教「怎么写」，不管「写什么」。语气、情绪底色、题材、
具体意象词一律**不收录**——它们跟剧情和人设走，收了反而适配度低。

**方式A · 静态风格卡（先做）**
`library/` 放素材（txt/md）→ `沙盒学习素材` 把素材交模型提炼为 JSON 风格卡。
每次生成注入风格卡，稳定省 token；素材更新后重新学习即换风格。

### 5.1 捕获维度（仅笔法，跨剧情稳定）

| 字段 | 捕获什么 |
|------|----------|
| sentence_style 句式结构 | 长短句偏好、断句、省略号/破折号/排比的使用 |
| vocab_prefs 词汇偏好 | 高频词、书面 vs 口语、特定用词习惯 |
| description_focus 描写侧重与密度 | 感官/心理/动作/环境比例、颗粒度粗细 |
| person_habit 人称与视角 | 叙述视角、距离感 |
| rhythm 节奏 | 段落长短、推进快慢 |
| sense_focus 感官侧重 | 视觉/触觉/听觉/嗅觉谁优先 |
| scale_floor 尺度与露骨度 | 器官/体液用词倾向、直白度 |
| metaphor_style 比喻方式 | 怎么用比喻、意象密度 |

**明确不收录**：语气/情绪底色 ❌ 题材 ❌ 具体意象词（雾都/旧船票这类）❌

### 5.2 学习方法（不做统计共识）

- **主卡只收稳定笔法**：某特征在素材里反复出现 → 收录；单篇出彩但不普遍 →
  不进主卡，单独归档进 extras 按需调用。
- **强度标记**：每字段标「强 / 有 / 无」，不是统计式置信度。「有」就是有，
  一篇明确的示例句就能锚定，不需要凑共识。
- **示例句锚定**：每个特征配 1~2 句原文例句，抽象描述不如原文例句可复现。

### 5.3 落地形态

```json
style_card.json
{
  "meta": { "learned_at": "...", "source_count": 3, "favorite_ratio": 0.5 },
  "core":  { 字段: { "desc": "...", "examples": [...], "strength": "强/有/无" } },
  "samples": [ "2~3 条高权重完整示例段（few-shot）" ],
  "extras": [ { "tag": "来源", "text": "单篇特色样本，不并主卡" } ]
}
```

- 生成时注入 `core` + `samples`（few-shot）；`extras` 只在点名时用，省 token 不丢风味。
- 收藏 `favorites/` 素材权重最高，优先体现其笔法。

**方式B · 动态检索（二期，暂不做）**
素材量大后切块向量化入 knowledge_base，按主题检索 few-shot。

> 风格卡「如何提炼才算合适」的细节，待沙盒跑通后与汐小姐专门讨论再定。

## 6. 目录结构

```
astrbot_plugin_write_sandbox/
├── PLAN.md            ← 本文件（方案基准）
├── metadata.yaml
├── main.py            ← 插件入口 + llm_tool 注册 + 执行逻辑（handlers 拆分暂缓）
├── core/              ← 沙盒核心包（2026-09-21 由 core.py 拆分）
│   ├── __init__.py    ← 导出 Sandbox（门面）
│   ├── sandbox.py     ← class Sandbox（7 个 Mixin 组合）
│   ├── constants.py   ← 全部常量（目录名/默认配置/维度标签）
│   ├── storage.py     ← 状态读写、history、outputs
│   ├── style.py       ← 风格卡、组合卡、约束
│   ├── works.py       ← 作品工作区、大纲、章节、设定书
│   ├── characters.py  ← 人设卡全套（含字段合并 update）
│   ├── library.py     ← 素材库、收藏
│   ├── analysis.py    ← 重复检测、对话提取、白名单
│   └── cleanup.py     ← 过期文件清理
├── prompts.py         ← 系统提示词模板
└── data/plugin_data/astrbot_plugin_write_sandbox/
    ├── sandbox_history.jsonl
    ├── style_card.json
    ├── constraints.json
    ├── outputs/*.txt
    ├── library/*.txt
    ├── favorites/*.txt
    ├── style_cards/*.json       ← 风格卡库（组合卡用）
    ├── style_card.json / style_combo.json / constraints.json
    └── stories/
        └── 《作品名》/   ← 长文工作区（每篇独立，互相隔离）
            ├── chapter_summaries.json   ← 逐章摘要（追加流水，不可变）
            ├── story_state.json         ← 设定书（当前状态，覆盖更新）
            ├── outline.json             ← 大纲（全局脉络 + 分章节点）
            ├── outline.pending.json     ← 大纲待确认草稿
            ├── story_config.json        ← 章节接续模式等配置
            ├── characters/*.json        ← 作品级人设卡（仅本作品可见）
            ├── drafts/*.txt             ← 分节草稿
            └── chapters/*.txt           ← 各章原文
```
（另有全局人设卡目录 `用户目录/characters/*.json`，跨作品可调）

## 7. 里程碑

- [x] M0 方案讨论与落盘
- [x] M1 插件骨架：注册 + 沙盒核心 + 生成落盘 txt
- [x] M2 约束配置 + 提示词文件（可改不碰代码）
- [x] M3 素材学习：风格卡提炼 + 收藏闭环
- [x] M4 打磨：续写/改写/边界兜底（超长、空素材、报错、重置）
- [x] M5 长文连贯：设定书 + 逐章摘要 + 大纲 + 人设卡（见第 9 节，已全部上线）
- [x] M6 模块化拆分：core.py → core/ 包（见第 10 节，已完成并重载验证）
- [x] M7 局部修改能力优化：正文局部修订 + 角色卡/大纲「说明书模式」（见第 11 节，已上线）

## 8. 开发铁律（对照 dev-workflow）

1. 改代码一律 safe_edit / multi_edit，自动备份+语法检查+失败回滚。
2. 改前 git_status / safe_read 确认现状；改后 lint_runner 检查。
3. 需求变更先改本文件，再动代码。
4. 写作类产出先给规划（已完成/还差/本轮接哪一截），禁止每次从头。

## 9. M5 长文连贯机制：设定书 + 逐章摘要（2026-09-11 定稿）

长篇写作的连贯性靠「逐章摘要 + 设定书」双文件维护，写入前先与汐小姐讨论定稿，
本轮落盘；摘要预算等遇到实际问题再调。

### 9.1 摘要：逐章追加，永不重写

`chapter_summaries.json` 是**追加流水，不可变**：

```json
{ "summaries": [ { "ch": 1, "summary": "第1章摘要(≤300字)" }, ... ] }
```

- 每章完稿后，只做一次「原文 → 摘要」生成，**追加**新条目，旧条目一个字节不动。
- **没有总量上限，只有单章上限**（默认 300 字）→ 不存在为了塞预算而二次压缩
  旧摘要，避免信息逐层丢失。
- 不写超长文时，写新章全量注入全部逐章摘要；真写到上百章再对「已完结早期弧线」
  做一次性归档（只归档一次，不进循环）——列为扩展点，现在不做。

### 9.2 设定书：当前状态快照，覆盖更新

`story_state.json` 是**当前状态快照**，记录「现在仍然成立的状态」：

```json
{
  "meta": { "title": "...", "current_chapter": 3 },
  "world": { "地点": "...", "规则": "..." },
  "characters": [ { "name": "...", "状态": "...", "关系": "...", "秘密": "..." } ],
  "foreshadowing": [ { "desc": "...", "status": "未收/已收" } ],
  "unresolved": [ "...未解决问题..." ]
}
```

- 每章完稿后，用「最新状态」**覆盖更新**旧值（如：人物状态、伏笔账本未收→已收）。
- 摘要是流水、设定书是快照，**各管各的，不重复写**：
  - 「已经过去的事」→ 进摘要（发生了什么、关键台词、时间地点推进）
  - 「现在仍然成立的状态」→ 进设定书（人物当前状态/关系/秘密、世界观、伏笔账本、未解决问题）
  - 摘要不写人物当前外貌；设定书不写剧情流水。

### 9.3 每篇文独立工作区，严格隔离

```
stories/
├── 《作品A》/ chapter_summaries.json + story_state.json + chapters/
└── 《作品B》/ ...  各自独立，互不可见
```

- 写文指令带作品名，沙盒切换到对应工作区，**只加载它自己的摘要 + 设定书**。
- 提示词加防串句：「只参考《作品A》的资料，禁止引入其他作品内容」。
- 唯一跨文共享的是**风格卡 / 学习素材**（写作风格本就跨文复用），剧情记忆一律按文隔离。
- 正式长文**不再依赖「最近 40 条聊天历史」窗口**（短篇即兴可继续用），
  连贯性只认作品工作区里的摘要 + 设定书，防止 A 文历史串进 B 文。

### 9.4 摘要提示词骨架（防漏防混）

```text
你是剧情摘要员。任务：把「新章节」压缩为单章摘要，追加进章节摘要列表。
【必须记录】角色属性/关系/状态变化，情节关键节点与结果，时间地点推进，
新埋或触发的伏笔，未解决问题。
【禁止记录】环境渲染、修辞描写、对话原文（除非关键台词）、临场动作（坐下/喝水）。
【硬规则】单章摘要 ≤300 字；不修改任何旧摘要；输出只含新增一条。
输出前自检：是否遗漏新增角色、角色受伤/死亡、关系变化、伏笔、时间跳跃？遗漏则补上。
```

### 9.5 M5 里程碑拆分

- [x] M5.1 作品工作区：目录切换 + 隔离 + 防串提示词
- [x] M5.2 逐章摘要：完稿后生成单章摘要并追加
- [x] M5.3 设定书：完稿后覆盖更新状态/伏笔账本
- [x] M5.4 注入：写新章时全量注入摘要 + 设定书
- [x] M5.5 大纲（2026-09-12）：`outline.json` 全局脉络+分章节点，写正文注入当前章节点

### 9.6 大纲机制（2026-09-12 定稿，JSON 结构）

`outline.json` 与摘要/设定书并列存放，管「接下来要写什么」：

```json
{
  "meta": {
    "title": "作品名", "premise": "一句话核心设定", "theme": "主题",
    "tone": "整体氛围基调",
    "arcs": ["故事弧线1", "故事弧线2"],
    "characters": [ { "name": "...", "role": "定位", "arc": "人物弧线" } ],
    "scale_map": { "1-3": "暧昧", "4-6": "露骨" }
  },
  "chapters": [
    { "ch": 1, "title": "...", "summary": "本章要点",
      "plot_points": ["情节节点"], "characters": ["出场角色"],
      "tone": "基调", "scale": "暧昧|露骨|极限",
      "cliffhanger": "结尾钩子", "notes": "伏笔备注" }
  ]
}
```

- **写入（草稿先行，2026-09-13 调整）**：`沙盒大纲` 工具，独立提示词（剧情架构师）生成 JSON，
  先落盘为**待确认草稿**（`outline.pending.json`），返回完整大纲供审核；用户确认后再
  `action=confirm` 才落盘为正式 `outline.json`。未确认前写正文会被拦截，
  避免「大纲没过目就直接开写」导致设定漏写/逻辑硬伤。
- **确认/丢弃**：`沙盒大纲` action=confirm 把草稿转正式；action=discard 丢弃草稿。
  action=view 时优先展示待确认草稿（带「待确认」提示），无草稿才展示正式大纲。
- **正文不受影响**：正文一章直接写完、整章交付（不拆成一截截交），拆细只针对大纲设定审核。
- **读取分两级**：`render_outline` 看全局（全部章节节点）；`render_chapter_outline` 写正文时只注入
  当前章节点 + 全局脉络概要（premise/arcs/scale_map），专注本章又不丢全局。
- **与摘要/设定书分工**：摘要是「已发生的过去」、设定书是「现在的状态」、大纲是「将要写什么」，
  三者各管一段，不重复写入。
- 章节节点缺失时回退到编号最近节点，避免注入空；大纲可随时重写覆盖。

### 9.7 人设卡机制（2026-09-12 定稿，统一格式 + 双存储）

人设卡提供统一固定条目，支持「一句话描述 → 一键生成整套卡」，供配角批量建卡。

```json
{
  "basic":     { "name", "aliases", "gender", "age", "identity", "appearance", "personality", "quirks" },
  "background":{ "origin", "motivation", "secrets", "relations": [{name, relation, note}] },
  "story":     { "role", "arc", "plot_hooks", "scale_role" },
  "speech":    { "tone", "catchphrases", "address_style" },
  "meta":      { "scope": "global|work", "work", "schema_version", "created_at" }
}
```

- **双存储**：全局卡 → `用户目录/characters/`；作品卡 → `stories/《作品》/characters/`。
- **读取优先级**：作品卡 > 全局卡（近的盖远的）。
- **工具 `沙盒人设`**：create/list/view/update/delete，scope 决定存哪，work 指定作品。
- **写作联动**：写正文时按大纲章节节点的「出场角色」自动加载人设卡注入，
  只注入本章出场角色，不撑爆上下文；没建卡的出场角色提示按上下文自由处理。
- **与大纲/摘要/设定书分工**：大纲管「接下来写什么」、人设卡管「角色是谁」，
  摘要管已发生、设定书管当前状态，四者各管一段。

### 9.8 写作体验小部件（2026-09-13 定稿，四样）

#### 9.8.1 章节接续（按作品开关）

- 每个作品存 `story_config.json`：`{ "chapter_link": "auto" }`。
- `chapter_link` 三档：
  - `continuous` 连续叙事：续写时贴上一章结尾三行，维持连贯。
  - `break` 断章收尾：上一章在情绪最高处收住，续写**不贴旧结尾**，
    只给本章大纲的「氛围引子」（tone / cliffhanger / 灵感便签），避免接错气氛。
  - `auto`（默认）：有灵感便签或结尾钩子就按 break 给引子，否则按 continuous 贴结尾。
- 设置入口：`sandbox_outline` action=link，mode=continuous|break|auto。

#### 9.8.2 废稿清理

- 新工具 `sandbox_cleanup`：
  - action=list：列出 outputs/ 与各作品 chapters/ 里 mtime 超过 30 天的旧文件（编号+时间+大小）。
  - action=delete：按编号删除（需要用户先看过清单、明确点名才删）。
- 原则：沙盒不丢稿，旧稿由汐小姐定期勾选清理；删除前必须过目。

#### 9.8.3 口水词小哨兵

- 整章写完（后台任务完成）后自动扫描全文：
  - 内置口水词表（轻轻/仿佛/缓缓/似乎/笑了笑/微微/一点/有些/像是/终于/慢慢/眼神/嘴角/忍不住/悄悄…）
  - 高频重复的 2-4 字词也提示。
  - 报告出现次数 + 所在段落号，随完成通知附上。
- 定位：只提示不自动改稿，汐小姐决定要不要修。

#### 9.8.4 灵感便签（绑大纲）

- 大纲章节节点新增 `mood` 字段（灵感便签/氛围引子，可多条）。
- `sandbox_outline` action=note 添加/清空某章便签。
- 写该章正文时随本章大纲自动注入【本章灵感便签】；断章续写也拿它当引子。
- 与 cliffhanger 分工：cliffhanger 是「结尾钩子」（写结尾时用），mood 是「起笔氛围」（开写时用）。

#### 9.8.5 全局卡智能选角（2026-09-13）

- 目标：全局卡不用汐小姐点名，也能自然、有需要地引入故事；但不是一股脑全带，而是经过判断地选。
- 机制（写在 `_run_write_job` 开头，整章只选一次）：
  1. `render_global_card_pool(work, ch)` 生成「全局卡池摘要」——每张一行：名字｜身份｜核心特征，
     并排除本章大纲已出场（已由 render_work_characters 注入）与已有作品卡的角色，避免重复/遮蔽。
  2. 用 `GLOBAL_CARD_SELECT_PROMPT` 调一次模型选角：给本章大纲 + 用户写作要求 + 卡池摘要，
     让模型挑「本章真正会出场/被提及/直接影响剧情」的卡。
  3. 规则：默认一张不选；宁缺毋滥；最多 3 张；已在出场名单的不重复选。
  4. 选中的卡以完整人设注入 story_context（【本章选用全局人设卡】段），未选中的完全不出现。
  5. 失败/解析失败/选到不存在的卡：静默降级为空，不阻塞写作。
- 信息边界联动：人物卡生成与提炼提示词带「信息边界铁律」——只准用用户描述/参考信息/正文里
  出现的内容，relations 禁止编造故事外人物；audit 复核时整卡扫描关系句式，揪出
  未出场/未提到人物的关联（`check_card_foreign` 单卡即时自检 + `audit_characters` 全量复核）。

## 10. 模块化拆分（2026-09-21 已完成）

> 背景：原 `core.py`（1719 行，单类 95 方法）与 `main.py`（1832 行）双巨型文件。已将 `core.py` 拆分为 `core/` 包（7 个 Mixin + 门面 + 常量），**对外行为零变化**：Sandbox 方法名、工具名、目录结构、存档格式一律不变。`main.py` 的 handlers 拆分暂缓。

### 10.1 实际结构（2026-09-21 现状）

```
astrbot_plugin_write_sandbox/
├── main.py                 # 插件类 + 14 个工具注册 + 执行逻辑（handlers 拆分暂缓）
├── core/
│   ├── __init__.py         # 导出 Sandbox（门面）
│   ├── sandbox.py          # class Sandbox（7 个 Mixin 组合）
│   ├── constants.py        # 全部常量（目录名/默认配置/维度标签）
│   ├── storage.py          # 状态读写、history、outputs
│   ├── style.py            # 风格卡、组合卡、约束
│   ├── works.py            # 作品工作区、大纲、章节、设定书
│   ├── characters.py       # 人设卡全套（含字段合并 update）
│   ├── library.py          # 素材库、收藏
│   ├── analysis.py         # 重复检测、对话提取、白名单
│   └── cleanup.py          # 过期文件清理
├── prompts.py              # 保留不动
├── metadata.yaml
├── PLAN.md
└── README.md
```

### 10.2 core 拆分映射（Sandbox 95 方法按功能域归位）

| 模块 | 归入的方法域 | 估计方法数 |
|------|-------------|-----------|
| `constants.py` | 全部模块常量（DEFAULT_*/目录名/维度标签/配置默认值）+ `_safe_title` | 常量 |
| `storage.py` | `__init__`、`_load_state`、history 读写、output 读写、latest_output_path | ~10 |
| `style.py` | 风格卡全套（save/list/load/render）、组合卡（combo 相关）、约束（set/render） | ~13 |
| `works.py` | 作品工作区（active_work/list/work_dir/chapters_dir）、章节摘要、设定书、大纲全套（含 pending）、story_config、接续模式、灵感便签、章节读写、drafts、finalize、render_story_memory | ~45 |
| `characters.py` | 人设卡全套（字符文件、load/save/delete、update 合并、render、全局卡池、foreign 检测、audit、extract 辅助） | ~20 |
| `library.py` | library/favorites 文本与来源读取、save_favorite | ~7 |
| `analysis.py` | collect_whitelist、对话提取、重复词检测 | ~6 |
| `cleanup.py` | list_stale_files | ~1 |

拆分实现：每个模块定义对应 **Mixin 类**（如 `StorageMixin`、`StyleMixin`…），`Sandbox` 多重继承组合；对外仍 `from .core import Sandbox`，`main.py` 只改导入路径。

### 10.3 handlers 拆分映射（暂缓执行，保留规划）

| 模块 | 承载 | 说明 |
|------|------|------|
| `write.py` | `sandbox_write` 全链路 | `_run_write_job`、`_write_section`、`_plan_sections`、`_revise_repeated`、`_notify`、`_select_global_cards`、章节摘要/设定书更新、角色提炼 |
| `outline.py` | `sandbox_outline` | 大纲 action 分发 |
| `character.py` | `sandbox_character`、`sandbox_extract_characters` | 人设卡工具 |
| `learn.py` | `sandbox_learn`、`sandbox_favorite` | 素材学习、收藏 |
| `misc.py` | progress/cancel/set/status/style_combo/work/cleanup/reset | 其余小工具 |

实现方式：handlers 以**接收 Sandbox + 事件 + 参数**的模块级函数承载业务，`main.py` 的 llm_tool 方法只做注册壳 + 调用 handler。工具名与方法名不变。

### 10.4 执行顺序与门禁（已完成）

1. ✅ 提交既有修复（人设卡 update），立干净基线（24bdbb6）
2. ✅ 拆 `core.py` → `core/` 包（constants → storage → style → works → characters → library → analysis → cleanup）
3. ⬜ 拆 `main.py` → `main.py` + `handlers/`（暂缓：main.py 功能正常，后续按需再拆）
4. ✅ 每步：`syntax_check` + `load_plugin` 重载冒烟
5. ✅ 类属性补漏：拆分脚本漏搬 8 个类属性（Assign/AnnAssign 未处理），已全部补回 characters.py 4 个 + analysis.py 4 个；AST 核对 8/8、语法检查通过、插件重载零报错
6. ⬜ 全量验证后 `git commit`（refactor: 消息）

### 10.5 风险与对策

- 95 个方法相互调用密集 → 拆分前用 `code_explore` 查调用链，保持 Mixin 内方法可见性
- `Sandbox` 对外方法名与工具名零改动 → 现有对话用法不受影响
- 存档/目录结构零改动 → 历史数据不迁移
- 每模块拆分后立刻重载冒烟 → 问题早暴露早修

## 11. 局部修改能力优化（汐小姐要求，2026-09-21 立项）

> 【汐小姐要求】优化角色卡、大纲与正文的生成/修改逻辑：支持根据要求对**指定内容**进行修改，而非全部推翻重写。

### 11.1 现状调研（2026-09-21 核对）

| 对象 | 当前行为 | 局限 |
|------|----------|------|
| 角色卡 update | `apply_character_edit` 纯代码字段合并：正则识别「删除X」「X改为Y」，只动点名字段 | 只认严格写法；自然语言描述（如「性格改得更阴郁」）进「未识别指令」；relations 只能整体清空 |
| 大纲 revise | 整份大纲 JSON 交模型 + OUTLINE_REVISE_PROMPT 约束「未点名部分原样保留」 | 输出仍为整份大纲，局部性靠提示词约束、无程序保障；章节多时 token 开销大 |
| 正文 rewrite | 上一段/整章作为上下文，让模型「按新要求重写」 | 整段/整章重写，未点名部分可能被自由改动 |

> 可复用雏形（【昔涟补充】调研发现）：`_revise_repeated`（口水词哨兵修订）＝ LLM 输出**编辑指令** → 程序在正文中**唯一定位**并精确应用，未命中则跳过。这是「指定内容修改」的现成机制底座。

### 11.2 规划方向与实施进度（2026-09-21 实施）

- ✅ **正文局部修订（mode=revise）**：新增 `_revise_text`/`_run_revise`/`_read_target_text`；复用「编辑指令 + 精确应用」底座（抽公共 `_parse_edit_json`/`_apply_edits`，哨兵修订同步重构复用）。用户描述改哪段/哪句/改成什么，LLM 输出带定位的编辑指令（替换/删除/插入），程序 `count==1` 唯一定位精确应用，未点名内容不动；改动摘要用 difflib 展示。
- ✅ **角色卡 / 大纲改「说明书模式」（汐小姐追加要求）**：工具不再黑盒生成，而是返回行动指南（`CHARACTER_GUIDE`/`OUTLINE_GUIDE`/`OUTLINE_REVISE_GUIDE`），由主模型照格式框架亲手编写，工具用 `action=save` 收单。收单走纯代码校验器（`_validate_character_card`/`_validate_outline`）与差异审查（`_diff_outline_chapters`，对未点名章节做程序级保障），不合格打回。角色卡 update 保持纯代码字段合并，识别不了时返回 `_build_card_update_guide` 字段编辑说明书。
- 开发顺序：先正文 → 再角色卡/大纲说明书模式；每步 `syntax_check` + `lint_runner` + `load_plugin` 重载冒烟 + 校验器逻辑验证。

### 11.3 实施记录（2026-09-21 已完成）

- ✅ 正文 revise：`sandbox_write` 新增 `mode=revise` 同步分支，读取最近产出/指定章/草稿 → LLM 编辑指令 → 精确应用 → 覆盖落盘 + 改动摘要。
- ✅ 大纲说明书：`sandbox_outline` write/revise 改为返回说明书；新增 `action=save`（text 传 JSON）→ `_validate_outline` 结构校验 + `_diff_outline_chapters` 差异审查 → `save_pending_outline`。
- ✅ 角色卡说明书：`sandbox_character` create 改为返回 `CHARACTER_GUIDE`；新增 `action=save`（text 传 JSON）→ `_validate_character_card` 校验 → `save_character`；update 未识别时返回字段编辑说明书。
- ✅ 提示词：prompts.py 新增 `CHARACTER_GUIDE`/`OUTLINE_GUIDE`/`OUTLINE_REVISE_GUIDE` + build 函数；黑盒版 `build_outline_prompt`/`build_outline_revise_prompt` 已不再被 main.py 调用（定义保留）。
- ✅ 验证：main.py/prompts.py `lint_runner` 0 问题；`load_plugin` 重载成功；校验器 7 组用例全过（合法通过/缺 name 打回/类型打回/连续通过/跳号重复空打回/diff 增删改识别）。
