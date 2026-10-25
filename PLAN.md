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
| 沙盒写作 | sandbox_write | 按风格卡+约束+作品记忆 生成/续写/改写/重写/局部修订，落盘 txt；mode=generate/continue/rewrite/revise；resume=断点续跑 |
| 写作进度 | sandbox_progress | 查后台分节任务进度 |
| 写作取消 | sandbox_cancel | 中断后台任务，已写部分保留 |
| 约束设置 | sandbox_settings | 改长度/人称/尺度/忌讳词/描写偏好/节奏/人格保留；work=作品名 时写入作品级约束（仅本书生效，优先于全局） |
| 沙盒状态 | sandbox_status | 查看当前风格卡与约束 |
| 风格学习 | sandbox_learn | 从 library/ + favorites/ 提炼风格卡；有激活作品则存作品级 |
| 素材收藏 | sandbox_favorite | 收藏上一段输出，风格学习里权重最高 |
| 大纲 | sandbox_outline | write/revise 走说明书 → save 校验 → confirm/discard；save_batch 分批收单；note 灵感便签；link 章节接续 |
| 人设卡 | sandbox_character | create/view/update/delete/audit/save/merge；全局/作品双存储；save 走 v2 结构+丰富度校验（主角 锚点≥3/性格≥3/台词≥2；配角各≥1；口头禅可回退台词档） |
| 角色提炼 | sandbox_extract_characters | 从文本提炼人设卡，阈值 0.6/3，结果标注 新建/跳过/龙套 |
| 风格组合 | sandbox_style_combo | 多卡融合 + 单维度指定来源，按激活作品隔离 |
| 作品工作区 | sandbox_work | 作品切换/新建；action=config 调注入与提炼开关 |
| 旧稿清理 | sandbox_cleanup | 列 30 天前旧稿 → 用户过目 → 按编号删；scrub 元信息清洗 |
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
| 流式调用+错误分类 | handlers/base.py `_llm_chat_ex` + core/llmerrors.py | 流式+空闲超时，慢生成不误杀；失败报真实类别（超时/限流/上下文超限） |
| 引擎配置 | _conf_schema.yaml → BaseMixin._ENGINE | 引擎参数可配置（每节默认 2000 字），WebUI 插件配置页调整 |
| 任务落盘断点续跑 | handlers/base.py `_scan_interrupted_jobs` + handlers/write.py `_persist_job`/`_resume_write_job` | 任务逐节写 jobs.json，重启后 sandbox_write(resume=jobN) 接续 |
| 完稿深层清洗 | core/textclean.py | write.py 每节捕获即清洗 + misc.py action=scrub 清存量（留 .bak 备份） |
| 大纲分批合并 | core/outlineops.py + handlers/outline.py | save_batch 每批 ≤3 章合并收单，confirm 做完整连续性校验 |
| 卡 schema v2 与注入裁剪 | core/cardops.py + core/characters.py render_character(purpose="write") | secrets/relations.note/剧情字段不进写作上下文，示例台词必注入 |
| 身份合并与查重 | core/characters.py find_character_by_identity + core/cardops.py merge_cards | canonical_id+本名+别名精确匹配；extract 查重与 action=merge 共用；rename_in_outline 同步大纲名单 |
| 完稿元视角哨兵 | core/cardops.py detect_meta_sentences/scrub_meta_lines + handlers/write.py | 独立注记行自动剥除+落盘，叙述内嵌只报告进 meta_leak_report |
| 约束三层 overlay | core/constraintops.py + core/works.py | 章节指令>作品级>全局，同名键就近覆盖、空值透传，渲染带「（作品级）」标注 |
| 完稿文风校验闭环 | core/prosecheck.py + handlers/write.py | 代词密度 advisory 只进报告；段尾升华 ≥2 处经 _apply_edits 定向修订，双轨落盘 |

## 2. 功能代码导航

### 2.1 目录结构（2026-10-24 现状）

```
astrbot_plugin_write_sandbox/
├── main.py            ← 插件入口：@register + 14 个工具注册壳（39 行）
├── handlers/          ← 业务逻辑 Mixin（2026-09-25 由 main.py 拆分）
│   ├── base.py        ← BaseMixin：__init__/_get_sandbox/_llm_chat_ex/_llm_chat/_clean + 引擎配置 _ENGINE + 任务落盘
│   ├── write.py       ← WriteMixin：sandbox_write/progress/cancel + 断点续跑（最肥）
│   ├── outline.py     ← OutlineMixin：sandbox_outline（save_batch/confirm）+ _validate_outline + _diff_outline_chapters
│   ├── character.py   ← CharacterMixin：sandbox_character/extract_characters + 校验
│   ├── learn.py       ← LearnMixin：sandbox_learn/favorite + _sample_source_text/_pack_sources
│   └── misc.py        ← MiscMixin：settings/status/style_combo/work/cleanup(list/delete/scrub)/reset
├── core/              ← 沙盒核心包（2026-09-21 由 core.py 拆分）
│   ├── __init__.py    ← 导出 Sandbox（门面）
│   ├── sandbox.py     ← class Sandbox（Mixin 组合）
│   ├── constants.py   ← 常量；DEFAULT_STORY_CONFIG 含三开关
│   ├── storage.py     ← 状态读写、history、outputs
│   ├── style.py       ← 风格卡/组合卡/约束（含 work 作品级读写、回退全局）
│   ├── works.py       ← 作品工作区/大纲/章节/设定书/story_config
│   ├── characters.py  ← 人设卡全套（含字段合并 update、foreign 检测、audit）
│   ├── cardops.py     ← 人设卡 schema v2 纯函数（normalize/validate/身份/合并/元视角检测，P1 新增）
│   ├── library.py     ← 素材库、收藏
│   ├── analysis.py    ← 重复检测、对话提取、白名单
│   ├── cleanup.py     ← 过期文件清理
│   ├── llmerrors.py   ← LLM 错误分类（超时/限流/上下文超限/供应商/空响应）
│   ├── textclean.py   ← 完稿深层清洗（分节标题/状态标记/注记行剥离）
│   └── outlineops.py  ← 大纲分批合并与结构校验
├── tests/
│   ├── test_p0.py     ← P0 纯函数单测（python tests/test_p0.py，无需依赖）
│   └── test_p1.py     ← P1 纯函数+Sandbox 级单测（cardops schema v2/合并/元视角，python tests/test_p1.py，无需依赖）
├── scripts/
│   └── migrate_v1_v2.py ← 存量卡 v1→v2 迁移（默认 dry-run 只出报告，--apply 实迁留 .bak_v1；报告 migration_report.md 已 gitignore）
├── _conf_schema.yaml  ← 引擎参数配置（注入 BaseMixin._ENGINE）
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
| 人设卡 schema/注入裁剪/合并 | core/cardops.py + core/characters.py + handlers/character.py | python tests/test_p1.py + 迁移 dry-run |
| 风格卡/组合/约束 | core/style.py + handlers/learn.py + misc.py | 学习→状态→组合 set/clear，验证作品回退全局 |
| 作品工作区/摘要/设定书 | core/works.py | 长文写章→摘要追加→设定书覆盖 |
| LLM 调用/超时/任务续跑 | handlers/base.py + handlers/write.py + core/llmerrors.py | python tests/test_p0.py + py_compile |
| 提示词/说明书 | prompts.py | lint + 生成冒烟 |
| 约束三层/文风校验/提示词 | core/constraintops.py + core/prosecheck.py + core/works.py + handlers/write.py + prompts.py | python tests/test_p2.py + 模板字符数断言 + 迁移 dry-run |

## 3. 未完成的未来任务

| 任务 | 内容 | 验收标准 |
|------|------|---------|
| P3 长文质量（评估方案 §四 P3） | 章→场景 beat 细纲、大纲动态修订（DOC 式）、摘要分层（近 3 章详摘+弧线压缩）、per-character 当前状态、注入 token 预算 | 10 章以上连载人设/伏笔一致性可查；百章级作品注入上下文规模稳定 |
| 素材动态检索（二期） | 素材量大后切块向量化入 knowledge_base，按主题检索 few-shot（原第 5 节方式B） | 素材量触发后风格仍稳定、省 token |
| 上百章早期弧线归档 | 已完结早期弧线的摘要一次性归档，只归档一次不进循环（原 9.1 扩展点） | 上百章长文上下文不爆，旧弧线信息不丢 |

## 4. 最近改动小尾巴

1. 2026-10-24 P2 约束与提示词改造【汐小姐要求】：新增 core/prosecheck.py + core/constraintops.py 纯函数、约束三层 overlay（sandbox_settings 传 work 参数写作品级约束，章节指令>作品级>全局，同名就近覆盖空值透传）+ 存量迁移脚本 scripts/migrate_constraints.py（description_focus/rhythm/length/perspective 四键迁作品级，默认 dry-run、全局留 .bak）、系统提示词瘦身（1313→429 字符，-67%）、风格示范 few-shot 注入（激活卡/组合卡收藏样本首段 ≤500 字）、完稿文风校验闭环（代词密度 advisory 进报告；段尾升华 ≥2 处自动一轮定向修订，双轨落盘）、修复 style.py 缺 DEFAULT_STYLE_CARD 导入（无卡用户 NameError 隐患），新增 tests/test_p2.py，版本 0.5.0。【补记】同日 --apply 已执行：四键迁入《返场》story_constraints.json、全局恢复默认（scale/banned_words/keep_persona 留全局）、constraints.json.bak_p2 备份留存、复跑幂等
2. 2026-10-24 P1 角色卡 schema v2 与剧情解耦【汐小姐要求】：卡 schema v2（persona 段+示例台词+inferred 演绎标注+canonical_id）、剧情退出人设卡（arc/plot_hooks 迁设定书）、写作注入裁剪（secrets/relations.note/剧情字段不进写作上下文，示例台词必注入）、完稿元视角哨兵（独立注记行自动剥除+落盘，叙述内嵌只报告进 meta_leak_report）、sandbox_character action=merge 身份合并（别名吸收/空位互补/大纲出场名单同步）、存量卡迁移脚本 scripts/migrate_v1_v2.py（默认 dry-run 只出报告、--apply 实迁且源卡留 .bak_v1 备份，全局卡剧情字段进 migration_dropped.json），新增 core/cardops.py、tests/test_p1.py，版本 0.4.0。【补记】同日 --apply 已执行：16 卡全部 v2（备份 *.json.bak_v1）、设定书收编 10 arc+23 伏笔、两张汐卡 role 章号残留已清；备份命名后缀在后防 glob 污染（95352cc）；P0/P1 均已快进合并 main（未 push），真机冒烟与 merge 汐双卡待跑
3. 2026-10-24 P0 稳定性改造【汐小姐要求】：_llm_chat 流式化+空闲超时+错误分类（超时/限流/上下文超限可辨）、引擎参数配置化（每节 4000→2000）、大纲 save_batch 分批收单+confirm 严格校验、写作任务 jobs.json 落盘+resume 断点续跑、完稿深层清洗（分节标题/状态标记/注记行剥离，存量走 sandbox_cleanup scrub），新增 core/llmerrors.py、core/textclean.py、core/outlineops.py、tests/test_p0.py、_conf_schema.yaml，版本 0.3.0
4. 2026-09-27 修复口水词哨兵失效【汐小姐要求】：core/analysis.py `_extract_dialogues`、core/library.py `_read_sources`/`_read_texts` 三个方法在模块化拆分后丢失 `@staticmethod` 装饰器（无 self 亦无装饰器），经 `self.xxx()` 实例调用即 TypeError，且被 write.py 的 try/except 静默吞掉，导致完稿哨兵「检测+自动修订」整条链路不生效。补回 `@staticmethod` 后恢复；已对 Sandbox 全方法做签名级体检 + 全域冒烟通过。
5. 2026-09-25 风格卡作品级绑定 + 注入/提炼三开关（inject_memory/inject_characters/auto_extract）+ 角色卡提取收紧 0.6/3
6. 2026-09-25 main.py → handlers/ 包拆分（Mixin 式，56c205b）
7. 2026-09-21 局部修改优化：正文 mode=revise + 角色卡/大纲说明书模式
8. 2026-09-21 core.py → core/ 包拆分（Mixin 式）
9. 2026-09-13 写作体验小部件：章节接续 / 废稿清理 / 口水词哨兵 / 灵感便签
