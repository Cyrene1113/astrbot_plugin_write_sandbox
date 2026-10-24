# 写作沙盒 · 昔涟 — 项目导航（精简版）

> 本文件是插件唯一方案基准。已交付功能只记「位置与用法」，不再记过程；
> 需求变更先改这里，再动代码。详细设计决策见 git 历史（提交信息带节号）。

## 0. 开发铁律

1. 改代码一律 safe_edit / multi_edit，自动备份 + 语法检查 + 失败回滚。
2. 改前 git_status / safe_read 确认现状；改后 lint_runner 检查。
3. 需求变更先改本文件，再动代码。
4. 写作类产出先给规划（已完成/还差/本轮接哪一截），禁止每次从头。
5. 交付打【汐小姐要求】标注；昔涟自行补充标【昔涟补充】，冲突以汐小姐要求为准。

## 1. 功能清单速查表（全部已交付）

### 1.1 工具（14 个 llm_tool）

| 功能 | 工具 | 一句话说明 |
|------|------|-----------|
| 沙盒写作 | sandbox_write | 按风格卡+约束+作品记忆 生成/续写/改写/重写/局部修订，落盘 txt；mode=generate/continue/rewrite/revise |
| 写作进度 | sandbox_progress | 查后台分节任务进度 |
| 写作取消 | sandbox_cancel | 中断后台任务，已写部分保留 |
| 约束设置 | sandbox_settings | 改长度/人称/尺度/忌讳词/描写偏好/节奏/人格保留 |
| 沙盒状态 | sandbox_status | 查看当前风格卡与约束 |
| 风格学习 | sandbox_learn | 从 library/ + favorites/ 提炼风格卡；有激活作品则存作品级 |
| 素材收藏 | sandbox_favorite | 收藏上一段输出，风格学习里权重最高 |
| 大纲 | sandbox_outline | write/revise 走说明书 → save 校验 → confirm/discard；note 灵感便签；link 章节接续 |
| 人设卡 | sandbox_character | create/view/update/delete/audit/save；全局/作品双存储 |
| 角色提炼 | sandbox_extract_characters | 从文本提炼人设卡，阈值 0.6/3，结果标注 新建/跳过/龙套 |
| 风格组合 | sandbox_style_combo | 多卡融合 + 单维度指定来源，按激活作品隔离 |
| 作品工作区 | sandbox_work | 作品切换/新建；action=config 调注入与提炼开关 |
| 旧稿清理 | sandbox_cleanup | 列 30 天前旧稿 → 用户过目 → 按编号删 |
| 沙盒重置 | sandbox_reset | 清空历史（可连约束风格卡一起清） |

### 1.2 核心机制（非工具，已交付）

| 机制 | 位置 | 一句话说明 |
|------|------|-----------|
| 独立沙盒 | core/storage.py | sandbox_history.jsonl，与主聊天完全隔离 |
| 风格卡 | core/style.py | 主卡 core+samples 注入、extras 点名才用；只教写法不教内容 |
| 作品工作区 | core/works.py | stories/《作品》/ 独立目录，篇与篇互不可见 |
| 逐章摘要 | core/works.py | chapter_summaries.json 追加流水不可变，单章 ≤300 字 |
| 设定书 | core/works.py | story_state.json 当前状态快照，覆盖更新 |
| 大纲 | core/works.py | outline.json + pending 草稿先行，未确认拦截开写 |
| 章节接续 | core/works.py | story_config.json chapter_link=continuous/break/auto |
| 人设卡双存储 | core/characters.py | 全局 + 作品两处，作品卡优先（近的盖远的） |
| 全局卡选角 | handlers/write.py | 每章选一次，默认不选、最多 3 张、宁缺毋滥 |
| 口水词哨兵 | handlers/write.py | 完稿自动扫口水词，只提示不自动改 |
| 正文局部修订 | handlers/write.py | 编辑指令 + 唯一定位精确应用，未点名内容不动 |
| 说明书模式 | prompts.py | CHARACTER_GUIDE/OUTLINE_GUIDE；黑盒生成改为主模型编写 + 代码校验收单 |
| 作品级风格卡 | core/style.py | 作品三件套 style_card/style_cards/combo，无卡回退全局 |
| 注入/提炼开关 | core/works.py | story_config：inject_memory / inject_characters / auto_extract（默认关） |

## 2. 功能代码导航

### 2.1 目录结构（2026-09-25 现状）

```
astrbot_plugin_write_sandbox/
├── main.py            ← 插件入口：@register + 14 个工具注册壳（39 行）
├── handlers/          ← 业务逻辑 Mixin（2026-09-25 由 main.py 拆分）
│   ├── base.py        ← BaseMixin：__init__/_get_sandbox/_llm_chat/_clean + PLUGIN_ID
│   ├── write.py       ← WriteMixin：sandbox_write/progress/cancel 全链路（最肥）
│   ├── outline.py     ← OutlineMixin：sandbox_outline + _validate_outline + _diff_outline_chapters
│   ├── character.py   ← CharacterMixin：sandbox_character/extract_characters + 校验
│   ├── learn.py       ← LearnMixin：sandbox_learn/favorite + _sample_source_text/_pack_sources
│   └── misc.py        ← MiscMixin：settings/status/style_combo/work/cleanup/reset
├── core/              ← 沙盒核心包（2026-09-21 由 core.py 拆分）
│   ├── __init__.py    ← 导出 Sandbox（门面）
│   ├── sandbox.py     ← class Sandbox（Mixin 组合）
│   ├── constants.py   ← 常量；DEFAULT_STORY_CONFIG 含三开关
│   ├── storage.py     ← 状态读写、history、outputs
│   ├── style.py       ← 风格卡/组合卡/约束（含 work 作品级读写、回退全局）
│   ├── works.py       ← 作品工作区/大纲/章节/设定书/story_config
│   ├── characters.py  ← 人设卡全套（含字段合并 update、foreign 检测、audit）
│   ├── library.py     ← 素材库、收藏
│   ├── analysis.py    ← 重复检测、对话提取、白名单
│   └── cleanup.py     ← 过期文件清理
├── prompts.py         ← 提示词模板 + 三份说明书
├── metadata.yaml
└── PLAN.md
```

### 2.2 改哪看哪

| 想改什么 | 去哪个文件 | 验证方式 |
|---------|-----------|---------|
| 工具入口/注册 | main.py（壳）+ 对应 handler | syntax_check + load_plugin 重载，日志确认工具注册 |
| 写作全链路/注入开关/提炼 | handlers/write.py | 冒烟：短篇 generate/continue/revise + 长文开关组合 |
| 大纲/接续/便签 | handlers/outline.py + core/works.py | 说明书 → save 校验 → confirm 链路走通 |
| 人设卡 | handlers/character.py + core/characters.py | create→save 校验→view/update/audit 各走一遍 |
| 风格卡/组合/约束 | core/style.py + handlers/learn.py + misc.py | 学习→状态→组合 set/clear，验证作品回退全局 |
| 作品工作区/摘要/设定书 | core/works.py | 长文写章→摘要追加→设定书覆盖 |
| 提示词/说明书 | prompts.py | lint + 生成冒烟 |

## 3. 未完成的未来任务

| 任务 | 内容 | 验收标准 |
|------|------|---------|
| 素材动态检索（二期） | 素材量大后切块向量化入 knowledge_base，按主题检索 few-shot（原第 5 节方式B） | 素材量触发后风格仍稳定、省 token |
| 上百章早期弧线归档 | 已完结早期弧线的摘要一次性归档，只归档一次不进循环（原 9.1 扩展点） | 上百章长文上下文不爆，旧弧线信息不丢 |

## 4. 最近改动小尾巴

1. 2026-09-27 修复口水词哨兵失效【汐小姐要求】：core/analysis.py `_extract_dialogues`、core/library.py `_read_sources`/`_read_texts` 三个方法在模块化拆分后丢失 `@staticmethod` 装饰器（无 self 亦无装饰器），经 `self.xxx()` 实例调用即 TypeError，且被 write.py 的 try/except 静默吞掉，导致完稿哨兵「检测+自动修订」整条链路不生效。补回 `@staticmethod` 后恢复；已对 Sandbox 全方法做签名级体检 + 全域冒烟通过。
2. 2026-09-25 风格卡作品级绑定 + 注入/提炼三开关（inject_memory/inject_characters/auto_extract）+ 角色卡提取收紧 0.6/3
2. 2026-09-25 main.py → handlers/ 包拆分（Mixin 式，56c205b）
3. 2026-09-21 局部修改优化：正文 mode=revise + 角色卡/大纲说明书模式
4. 2026-09-21 core.py → core/ 包拆分（Mixin 式）
5. 2026-09-13 写作体验小部件：章节接续 / 废稿清理 / 口水词哨兵 / 灵感便签
