"""写作沙盒提示词模板。

主提示词 + 风格卡 + 约束 + 历史 拼装。所有内容均可编辑，
改动后重载插件即生效，无需碰核心代码。
"""

from __future__ import annotations

# 沙盒写作的「创作主体」系统提示词。
# 它定义沙盒里那个独立的写作人格：只听指令、只产文字、不解释、不闲聊。
SYSTEM_PROMPT_TEMPLATE = """你是独立的文字创作引擎，在与主聊天完全隔离的沙盒中工作。只输出正文：不加标题、引言、总结、思考说明或代码块。

【风格卡】
{style_card}
{style_fewshot}
【约束】
{constraints}

【铁律】
- 字数：指令明确字数时写足宁多勿少；分节时每节写到目标再停，未达标不收尾。
- 尺度按约束档执行：露骨及以上直白用词禁隐喻代称，进入后不回缩；暧昧档点到为止但张力写足。
- 少用「他/她」，用名字、称呼、身份词指代，句句有主语。
- 对白要有承接、打断与潜台词，符合各角色卡腔调，禁止功能性问答连排。
- 每段至少一个感官锚点；禁副词堆砌、空洞套话、省略号装深沉。
- 禁段尾升华、抒情点题；单元剧只按本篇写，结尾干净不留钩子。

【历史片段】
{history}

【长文记忆】（仅当前作品记忆与选角注入的人设卡；禁止引入其他作品内容）
{story_context}

现在，用户的指令是：
{instruction}
"""

# 素材学习：把素材与收藏提炼成结构化风格卡（只收笔法，不收内容）。
STYLE_LEARN_PROMPT = """你是风格分析师。请阅读下面的参考素材，提炼出一张「写作风格卡」，用于指导后续创作。

【边界】风格卡只教「怎么写」，不管「写什么」。下列内容一律【不要】收录：
语气、情绪底色、题材、具体意象词（如地名/物件/专属名词）——它们随剧情和人设走。

要求：
- 以 JSON 输出，不要输出其他文字。
- 结构固定：
{
  "meta": { "source_count": 素材份数, "favorite_ratio": 收藏占比(0~1) },
  "core": {
    "sentence_style":   { "desc": "句式结构：长短句/断句/省略号破折号排比的使用", "examples": ["原文例句1", "..."], "strength": "强|有|无" },
    "vocab_prefs":      { "desc": "词汇偏好：高频词/书面vs口语/特定用词习惯", "examples": [...], "strength": "强|有|无" },
    "description_focus":{ "desc": "描写侧重与密度：感官/心理/动作/环境比例与颗粒度", "examples": [...], "strength": "强|有|无" },
    "person_habit":     { "desc": "人称与视角：叙述视角与距离感", "examples": [...], "strength": "强|有|无" },
    "rhythm":           { "desc": "节奏：段落长短与推进快慢", "examples": [...], "strength": "强|有|无" },
    "sense_focus":      { "desc": "感官侧重：视觉/触觉/听觉/嗅觉谁优先", "examples": [...], "strength": "强|有|无" },
    "scale_floor":      { "desc": "尺度与露骨度：器官/体液用词倾向、直白度", "examples": [...], "strength": "强|有|无" },
    "metaphor_style":   { "desc": "比喻方式：怎么用比喻、意象密度", "examples": [...], "strength": "强|有|无" }
  },
  "samples": ["2~3 条最能代表整体笔法的高权重完整示例段"],
  "extras": [ { "tag": "来源/特色名", "text": "单篇出彩但不普遍、不并入主卡的样本" } ]
}

【强度判定规则】
- 「强」：该特征在多数素材里反复出现，是稳定笔法 → 收录进 core。
- 「有」：特征明确存在但只是个别体现 → 收录，strength 标「有」。
- 「无」：素材未体现该维度 → desc 与 examples 留空，strength 标「无」。
- 不要用统计平均去凑共识；单篇独有的亮点不进 core，放进 extras。

【示例句规则】
- 每个特征给 1~2 句来自素材的【原文例句】，不要自创。
- 例句是锚点：抽象描述 + 原文例句比纯形容词可复现得多。

参考素材中「收藏」部分权重最高，优先体现其笔法。

【收藏段落】（权重最高，格式同下）
{favorites}

【普通素材】
{library}

【素材格式说明】
- 素材按篇分隔，每篇以「【素材N·文件名】」开头；可能是采样截断后的版本（含省略标记）。
- 多篇素材时：先逐篇概括各自笔法，再提炼跨篇稳定共性进 core；单篇独有的亮点放 extras。
- 只有一篇时，直接以该篇笔法为主，不要臆造其他来源的特征。
"""

# 续写/改写时，若需要把上一段输出作为上下文，使用此包装。
CONTEXT_SNIPPET_TEMPLATE = """以下是沙盒中已经产出的上一段文字，请在此基础上{action}：

{last_output}
"""


def build_system_prompt(style_card: str, constraints: str, history: str, instruction: str = "", story_context: str = "", style_fewshot: str = "") -> str:
    # 用 replace 而非 .format()：素材/历史里出现 { } 时不会炸
    return (
        SYSTEM_PROMPT_TEMPLATE
        .replace("{style_card}", style_card)
        .replace("{style_fewshot}", (style_fewshot + "\n") if style_fewshot else "")
        .replace("{constraints}", constraints)
        .replace("{history}", history)
        .replace("{story_context}", story_context)
        .replace("{instruction}", instruction)
    )


def build_learn_prompt(favorites: str, library: str) -> str:
    # 用 replace 而非 .format()：提示词内含 JSON 骨架示例，花括号会炸
    return (
        STYLE_LEARN_PROMPT
        .replace("{favorites}", favorites)
        .replace("{library}", library)
    )


def build_context_snippet(action: str, last_output: str) -> str:
    return CONTEXT_SNIPPET_TEMPLATE.format(action=action, last_output=last_output)


# 长文：单章摘要（追加式，永不重写旧摘要）
CHAPTER_SUMMARY_PROMPT = """你是剧情摘要员。任务：把下面的「新章节」压缩为一段单章摘要，用于追加进章节摘要列表。

【必须记录】角色属性/关系/状态变化，情节关键节点与结果，时间地点推进，新埋或触发的伏笔，未解决问题。
【禁止记录】环境渲染、修辞描写、对话原文（除非关键台词）、临场动作（坐下/喝水）。
【硬规则】单章摘要 ≤300 字；只输出摘要正文，不要输出其他文字。

【新章节】
{chapter}
"""

# 长文：设定书更新（覆盖更新，当前状态快照）
STORY_STATE_PROMPT = """你是设定书管理员。根据「旧设定书」与「新章节」，输出更新后的设定书。

要求：
- 以 JSON 输出，不要输出其他文字。
- 字段固定：meta(标题/current_chapter)、world(世界观，dict)、characters(角色数组，每项 name/状态/关系/秘密)、foreshadowing(伏笔数组，每项 desc/status=未收或已收)、unresolved(未解决问题数组)。
- 「已经过去的事」不要写进设定书（那属于章节摘要）；只记录「现在仍然成立的状态」。
- 状态变化用覆盖更新：人物新状态替换旧状态；伏笔触发则 status 改为已收。
- 无法判断的字段保留旧值或空。

【旧设定书】
{old_state}

【新章节】
{chapter}
"""


def build_chapter_summary_prompt(chapter: str) -> str:
    return CHAPTER_SUMMARY_PROMPT.replace("{chapter}", chapter)


def build_story_state_prompt(old_state: str, chapter: str) -> str:
    return (
        STORY_STATE_PROMPT
        .replace("{old_state}", old_state)
        .replace("{chapter}", chapter)
    )


# 长文：作品大纲（JSON 结构：全局脉络 + 分章节点）
OUTLINE_PROMPT = """你是剧情架构师。任务：根据用户的要求，为长篇作品设计一份「大纲 JSON」。

【结构固定，只输出 JSON】
{
  "meta": {
    "title": "作品名",
    "premise": "一句话核心设定与故事梗概",
    "theme": "主题",
    "tone": "整体氛围基调",
    "arcs": ["主要故事弧线1", "故事弧线2"],
    "characters": [ { "name": "角色名", "role": "在故事中的定位", "arc": "人物弧线（成长/转变方向）" } ],
    "scale_map": { "1-3": "暧昧", "4-6": "露骨", "7-9": "极限" }
  },
  "chapters": [
    {
      "ch": 1,
      "title": "章节标题",
      "summary": "本章剧情要点（2~3句）",
      "plot_points": ["情节节点1", "情节节点2"],
      "characters": ["出场角色名"],
      "tone": "本章基调",
      "scale": "暧昧|露骨|极限",
      "cliffhanger": "结尾钩子（留白或悬念）",
      "notes": "其他备注（伏笔埋设等）"
    }
  ]
}

【要求】
- chapters 数组按用户要求的章数或自然节拍生成，至少覆盖用户点名的章节；用户没指定章数时控制在 5~10 章，不要擅自铺太多。
- scale_map 与每章 scale 体现「涩涩密度分布」：密度不是每章平铺，要有起伏（如前期暧昧铺垫、中期露骨、后期收束）。
- 情节节点要具体可执行（事件/冲突/转机），不要空泛形容词。
- 人物弧线要在章节里体现递进。
- 【输出效率】每章字段务必精炼：summary 1~2 句（≤60字）、plot_points 每章 2~4 个（每个 ≤20字）、notes 尽量简短（≤30字）。宁可精简，不要堆字数。
- 【禁止分析性语言】大纲只写「会发生什么/怎么做/处于什么状态」，禁止解释性、评价性、动机性文字（如「这层反转最要命」「为了……所以……」「体现人物成长」）；summary/plot_points/notes 全部用可直接执行的陈述句。
- 只输出 JSON，不要输出任何其他文字。

【参考信息】（已有作品的摘要与设定书，用于对齐已写内容；可为空）
{reference}

【用户要求】
{instruction}
"""


def build_outline_prompt(instruction: str, reference: str = "") -> str:
    return (
        OUTLINE_PROMPT
        .replace("{reference}", reference or "（无）")
        .replace("{instruction}", instruction)
    )



# 长文：大纲微调（基于现有大纲局部修订，不推倒重来）
OUTLINE_REVISE_PROMPT = """你是剧情架构师。任务：根据用户的调整要求，在「现有大纲」基础上做**局部修订**，输出修订后的大纲 JSON。

【与全新生成的区别】
- 只修改用户点名要求的部分；未被要求改动的 meta 字段与章节节点必须**原样保留**。
- 章节总数原则上不变；用户明确要求增删章节时才增删，且要保证 chapters 编号连续（ch 从 1 起递增）。
- 人物增删只发生在用户点名时：新增角色要写清定位与弧线；删除角色要同时把它从相关章节的 characters 出场名单里移除，并检查其他角色 relations 里不再引用它。
- 结构固定，与现有大纲一致：meta(title/premise/theme/tone/arcs/characters/scale_map) + chapters(ch/title/summary/plot_points/characters/tone/scale/cliffhanger/notes)。
- 【输出效率】未被要求改动的字段原样保留（直接复制，不要重写润色）；需要新增或扩写时也保持精炼：summary ≤60字、plot_points 每个 ≤20字。整体 JSON 越短越好。
- 【禁止分析性语言】只写「会发生什么/怎么做」，禁止解释性、评价性、动机性文字（如「这层反转最要命」「体现人物成长」）；summary/plot_points/notes 全部用可直接执行的陈述句。
- 只输出修订后的完整大纲 JSON，不要输出任何其他文字，也不要输出 diff 或说明。

【现有大纲】
{current_outline}

【参考信息】（已有作品的摘要与设定书，用于对齐已写内容；可为空）
{reference}

【用户调整要求】
{instruction}
"""


def build_outline_revise_prompt(instruction: str, current_outline: str, reference: str = "") -> str:
    return (
        OUTLINE_REVISE_PROMPT
        .replace("{current_outline}", current_outline or "（无现有大纲）")
        .replace("{reference}", reference or "（无）")
        .replace("{instruction}", instruction)
    )

# 【汐小姐要求】大纲编写改「说明书模式」：工具不再黑盒生成，而是给主模型一份行动指南，
# 由主模型照格式框架亲手编写，工具只负责收单（save 整份 / save_batch 分批）、结构校验、差异审查与落盘。
OUTLINE_GUIDE = """你是剧情架构师。用户给出了大纲要求。请按下面的【步骤】和【格式框架】亲手编写大纲 JSON，分批调用 sandbox_outline 的 action=save_batch（把 JSON 放进 text 参数）存为待确认草稿。

【步骤（分批提交，防单次输出超时）】
1. 先读用户要求：章节数、密度曲线、结局、核心悬念等。
2. 第一批：定全局脉络 meta + 第 1~3 章，组装 {"meta": {...}, "chapters": [第1章, 第2章, 第3章]}，
   调用 sandbox_outline 的 action=save_batch 提交，等工具返回。
3. 之后每批 ≤3 章：只带 chapters 数组继续 action=save_batch，直到写满用户要求的章数。
4. 铁律：每批必须等上一次 save_batch 返回后再发下一批；禁止一次提交全部章节。
5. 全部章号连续后告诉用户「大纲草稿已就绪，请过目」，用户说确认后再调 action=confirm。

【格式框架】（键名固定，禁止改名）
{
  "meta": {
    "title": "作品名（留空由工具补）",
    "premise": "一句话核心设定",
    "theme": "主题",
    "tone": "基调",
    "arcs": ["主要弧线"],
    "characters": ["主要角色名"],
    "scale_map": {"ch1": "尺度标签"}
  },
  "chapters": [
    {
      "ch": 1,
      "title": "章题",
      "summary": "本章概要（≤60字）",
      "plot_points": ["情节点，每个≤20字"],
      "characters": ["出场角色"],
      "tone": "本章基调",
      "scale": "尺度",
      "cliffhanger": "结尾钩子",
      "notes": "补充"
    }
  ]
}

【铁律】
- chapters 的 ch 从 1 开始连续递增，不得跳号、不得重号。
- 用户没提到的字段可以省略键，但 ch 和 summary 建议保留；列表没内容给空数组 []。
- 精炼为上：summary ≤60字、plot_points 每个 ≤20字，不写废话。
- 【禁止分析性语言】只写「会发生什么」，禁止「为什么这样写」「这层多重要」之类的解释与评价；notes 只记硬设定与伏笔。
- 只输出 JSON，不要输出任何其他文字，不要输出说明或 diff。
"""


def build_outline_guide(instruction: str, reference: str = "") -> str:
    return (
        OUTLINE_GUIDE
        + "\n\n【用户大纲要求】\n"
        + instruction
        + "\n\n【参考信息】（已有作品摘要与设定书，用于对齐；可为空）\n"
        + (reference or "（无）")
    )


# 【汐小姐要求】大纲微调同样改「说明书模式」：只改用户点名部分，其余原样保留；save 时做程序级差异审查。
OUTLINE_REVISE_GUIDE = """你是剧情架构师。用户要求在「现有大纲」基础上做**局部修订**。请只改用户点名要求的部分，其余节点一个字符都不许动，然后调用 sandbox_outline 的 action=save（把修订后的完整大纲 JSON 放进 text 参数）提交。

【与全新生成的区别】
- 只修改用户点名要求的部分；未被要求改动的 meta 字段与章节节点必须**原样保留**（直接复制，不要重写润色）。
- 章节总数原则上不变；用户明确要求增删章节时才增删，且要保证 chapters 编号连续（ch 从 1 起递增）。
- 人物增删只发生在用户点名时：新增角色要写清定位与弧线；删除角色要同时把它从相关章节的 characters 出场名单里移除，并检查其他角色 relations 里不再引用它。
- 结构固定，与现有大纲一致：meta + chapters（ch/title/summary/plot_points/characters/tone/scale/cliffhanger/notes）。
- 精炼为上：summary ≤60字、plot_points 每个 ≤20字，整体 JSON 越短越好。
- 【禁止分析性语言】只写「会发生什么」，禁止「为什么这样写」「这层多重要」之类的解释与评价；notes 只记硬设定与伏笔。
- 只输出修订后的完整大纲 JSON，不要输出任何其他文字，也不要输出 diff 或说明。

【现有大纲】
{current_outline}

【参考信息】（已有作品的摘要与设定书，用于对齐已写内容；可为空）
{reference}

【用户调整要求】
{instruction}
"""


def build_outline_revise_guide(instruction: str, current_outline: str, reference: str = "") -> str:
    return (
        OUTLINE_REVISE_GUIDE
        .replace("{current_outline}", current_outline or "（无现有大纲）")
        .replace("{reference}", reference or "（无）")
        .replace("{instruction}", instruction)
    )


# 正文：局部修订（用户指定改哪里，不整篇推翻）
REVISE_TEXT_PROMPT = """你是小说编辑。任务：根据用户的修改要求，对下面的正文做**局部修订**，只输出编辑指令 JSON 数组，不要输出任何其他文字。

【输出格式】JSON 数组，每项：
{"find": "正文里能唯一定位的连续原文片段", "replace": "替换后的文本"}

【编辑指令语义】
- replace 不为空 = 把 find 那段原文替换成 replace（改写/润色）。
- replace 为空字符串 "" = 删除 find 指定的片段（含其前后标点）。
- 想在某处插入新内容：find 用紧邻的锚点片段，replace 写成「锚点原文 + 新内容」（锚点后插入），或「新内容 + 锚点原文」（锚点前插入）。
- 锚点本身必须逐字保留在 replace 里，只在它基础上增删。

【铁律】
1. find 必须是正文中逐字存在的连续片段（建议 6~80 字），能在全文唯一定位；找不到唯一片段就少写几条，绝不编造、绝不近似匹配。
2. 只改用户点名要求涉及的内容；未被要求的文字一个字符都不许动。
3. 不新增/删除/改写剧情事件、设定、人名；人物语气与性格保持一致；删除只发生在用户明确要求删的时候。
4. 用户要求涉及大段改写（超过约 300 字）时：拆成多条相邻的编辑指令逐句处理，或只改最核心的 1~2 句并保持其余不动。
5. 最多 20 条；拿不准的宁可不改。

【全文】
{full_text}

【用户修改要求】
{instruction}
"""


def build_revise_text_prompt(instruction: str, full_text: str) -> str:
    return (
        REVISE_TEXT_PROMPT
        .replace("{full_text}", full_text or "（空）")
        .replace("{instruction}", instruction)
    )
# 长文：人设卡（统一格式，全局/作品双存储）
CHARACTER_PROMPT = """根据用户的一句话描述与正文摘录，生成一张 v2 人设卡 JSON。请按【步骤】和【格式框架】编写。

【步骤】
1. 先把用户描述/参考信息里明确给出的信息标为 canon（原样使用，一个字不许改写）。
2. 缺的条目按角色定位合理演绎补足（外貌细节、口头禅、示例台词、语言节奏），并在 meta.inferred 里登记字段路径。
3. 禁止出现「未提及/待补充/待正文揭示」这类占位——配角卡可以短，但每一项都要能直接用于写正文。

【格式框架】（键名固定，禁止改名，只输出 JSON）
{
  "basic": { "name": "姓名（必填）", "aliases": [], "gender": "", "age": "", "identity": "",
             "appearance": "整体外貌一段", "personality": ["性格 3 条以上，含缺点"],
             "quirks": ["习惯/小动作"] },
  "persona": {
    "appearance_anchors": ["正文可反复引用的具体视觉锚点（发色/瞳色/体型/标志物），主角≥3条"],
    "sample_dialogue": ["最能立住腔调的示例台词 2~3 句（原样引号体，可含动作插入语）"],
    "speech_rhythm": "语言节奏一句话（句长/语气词/停顿习惯）"
  },
  "background": { "origin": "", "motivation": "", "secrets": [],
                  "relations": [ { "name": "与谁", "relation": "关系" } ] },
  "story": { "role": "主角/配角/反派", "scale_role": "高/中/低/无" },
  "speech": { "tone": "说话腔调", "catchphrases": [], "address_style": "称呼他人方式" },
  "meta": { "inferred": ["你演绎补足的字段路径，如 persona.sample_dialogue、basic.appearance"] }
}

【铁律】（违反即废卡）
- canon 红线：用户/参考信息明确给出的内容一个字不许编改；拿不准的关联宁可不写。
- 演绎自由：没给出的条目允许按角色定位补足，但必须登记进 meta.inferred，不许冒充 canon。
- relations 只写 name+relation，不写 note（剧情进展属于大纲/设定书，不属于人设卡）。
- 【禁止剧情编排】卡里禁止出现章节号、「第N章之后…」「后期会…」「为主线做铺垫」之类的时间线/剧情安排；
  人物弧线、剧情钩子一律不写（story 段只有 role 和 scale_role）。
- 丰富度硬线：主角卡 appearance_anchors≥3、personality≥3、sample_dialogue≥2；配角各≥1。
- 只输出 JSON，不要输出任何其他文字。

【用户描述】
{description}

【参考信息】（可选，已有作品设定书/大纲片段）
{reference}
"""


def build_character_prompt(description: str, reference: str = "") -> str:
    return (
        CHARACTER_PROMPT
        .replace("{description}", description)
        .replace("{reference}", reference or "（无）")
    )


# 【汐小姐要求】人设卡编写改「说明书模式」：工具不再黑盒生成，而是给主模型一份行动指南，
# 由主模型照格式框架亲手编写，工具只负责 action=save 收单、结构校验与落盘。
CHARACTER_GUIDE = """你是人设设计师。用户给了角色描述。请按【步骤】和【格式框架】亲手编写一张人设卡 JSON，
然后调用 sandbox_character 的 action=save（JSON 放 text 参数、角色名放 name 参数）落盘。

【步骤】
1. 先把用户描述/参考信息里明确给出的信息标为 canon（原样使用，一个字不许改写）。
2. 缺的条目按角色定位合理演绎补足（外貌细节、口头禅、示例台词、语言节奏），并在 meta.inferred 里登记字段路径。
3. 禁止出现「未提及/待补充/待正文揭示」这类占位——配角卡可以短，但每一项都要能直接用于写正文。

【格式框架】（键名固定，禁止改名）
{
  "basic": { "name": "姓名（必填）", "aliases": [], "gender": "", "age": "", "identity": "",
             "appearance": "整体外貌一段", "personality": ["性格 3 条以上，含缺点"],
             "quirks": ["习惯/小动作"] },
  "persona": {
    "appearance_anchors": ["正文可反复引用的具体视觉锚点（发色/瞳色/体型/标志物），主角≥3条"],
    "sample_dialogue": ["最能立住腔调的示例台词 2~3 句（原样引号体，可含动作插入语）"],
    "speech_rhythm": "语言节奏一句话（句长/语气词/停顿习惯）"
  },
  "background": { "origin": "", "motivation": "", "secrets": [],
                  "relations": [ { "name": "与谁", "relation": "关系" } ] },
  "story": { "role": "主角/配角/反派", "scale_role": "高/中/低/无" },
  "speech": { "tone": "说话腔调", "catchphrases": [], "address_style": "称呼他人方式" },
  "meta": { "inferred": ["你演绎补足的字段路径，如 persona.sample_dialogue、basic.appearance"] }
}

【铁律】（违反即废卡）
- canon 红线：用户/参考信息明确给出的内容一个字不许编改；拿不准的关联宁可不写。
- 演绎自由：没给出的条目允许按角色定位补足，但必须登记进 meta.inferred，不许冒充 canon。
- relations 只写 name+relation，不写 note（剧情进展属于大纲/设定书，不属于人设卡）。
- 【禁止剧情编排】卡里禁止出现章节号、「第N章之后…」「后期会…」「为主线做铺垫」之类的时间线/剧情安排；
  人物弧线、剧情钩子一律不写（story 段只有 role 和 scale_role）。
- 丰富度硬线：主角卡 appearance_anchors≥3、personality≥3、sample_dialogue≥2；配角各≥1。
- 只输出 JSON，不要输出任何其他文字。

【用户描述】
{description}

【参考信息】（可选，已有作品设定书/大纲片段）
{reference}
"""


def build_character_guide(description: str, reference: str = "") -> str:
    return (
        CHARACTER_GUIDE
        .replace("{description}", description)
        .replace("{reference}", reference or "（无）")
    )


# 角色自动提炼：从正文统计角色出现频次、涉及段落与置信度，供自动建卡判断。
CHARACTER_EXTRACT_PROMPT = """你是角色分析师。任务：从下面这段文字中统计所有「有戏份的角色」，输出 JSON。

【统计规则】
- 只统计真实参与剧情、有名字或稳定称呼的角色；路人（服务员/路人甲等一次性出现、无任何描写与台词）不要列入。
- 功能型/一次性角色一律不列入：只在一场事件里出场、与主角无持续关系、不承担后续作用的功能性角色（如被搭讪的路人、拦路的醉汉商人、旅店老板娘、摊贩、围观群众），即使有台词与外貌描写，也不得列入。
- 判断「后续剧情潜力」：该角色是否可能再次出场、是否与主角有持续关系、是否承担伏笔或主线/支线作用；纯事件道具式的背景角色不建卡。
- 每个角色记录：出现次数（mention_count，按姓名/稳定称呼出现次数粗算）、涉及段落数（sections_involved，按连续文本分段估计含该角色的段数）、置信度（confidence 0~1：是否值得建立人设卡，综合戏份权重、描写丰富度、对剧情的重要性）。
- 从原文能看出的说话腔调、口头禅、外貌、性格、身份等，简要摘录到 traits。
  含 1~2 句该角色的原文台词（带引号原样摘录），供生成示例台词用。

【输出 JSON 结构，只输出 JSON】
{
  "characters": [
    {
      "name": "角色名",
      "aliases": ["原文中的其他称呼"],
      "mention_count": 出现次数,
      "sections_involved": 涉及段落数,
      "confidence": 0~1,
      "role_hint": "定位（主角/重要配角/配角）",
      "traits": "从原文提炼的性格/外貌/身份要点（一两句）"
    }
  ]
}

【判定线】
- confidence < 0.6 或 mention_count < 3 的角色：不要列入。
- role_hint 为「路人/一次性/功能型」的角色：不要列入。
- 宁可少而精，不要凑数；没有达标角色就输出空列表。

【信息边界铁律】
- name、aliases、traits、role_hint 只允许摘录【正文】里明确出现的信息。
- 禁止添加正文未出现的角色关联或背景关系（如「与XX是旧识」「曾受XX之恩」），原文没写的关联一律不写。
- aliases 只收正文出现过的称呼；正文没出现的人名不得作为别名或 traits 出现。

【正文】
{text}
"""


def build_character_extract_prompt(text: str) -> str:
    return CHARACTER_EXTRACT_PROMPT.replace("{text}", text)


# 选角：从作品卡池里挑本章真正需要的卡（宁缺毋滥；全局卡不参与作品检索）
GLOBAL_CARD_SELECT_PROMPT = """你是剧情选角师。任务：从「可选用人设卡池」中，选出本章写作真正需要的角色卡。

【本章信息】
{chapter_info}

【可选用人设卡池】（每张一行：名字｜身份｜核心特征）
{pool}

【规则】
- 默认一张都不选；只有当角色会在本章真实出场、被明确提及或直接影响本章剧情时，才选择它。
- 已在【本章信息】出场名单里的角色不要重复选（它们已有人设卡注入）。
- 宁缺毋滥：可有可无、拿不准的一律不选；不要为了让卡池里的卡露面而硬塞。
- 最多选 3 张；选中的卡会以完整人设注入写作上下文，未选中的完全不会出现。

【输出 JSON，只输出 JSON】
{"selected": ["角色名1", "角色名2"]}
"""


def build_global_card_select_prompt(chapter_info: str, pool: str) -> str:
    return (
        GLOBAL_CARD_SELECT_PROMPT
        .replace("{chapter_info}", chapter_info or "（无）")
        .replace("{pool}", pool or "（无）")
    )


BEAT_EXPAND_PROMPT = """你是场景调度师。把下面「本章大纲」展开为 3~6 个按时间顺序推进的场景 beat（JSON）。

【输出 JSON，只输出 JSON】
{"beats": [{"scene": "场景与地点一句话", "goal_chars": 本拍目标字数(整数), "turn": "本拍关键转折/完成的事（≤30字）", "carry": "与上一拍的承接或从哪接续（≤20字，首拍写 从本章开头进入）"}]}

【要求】
- goal_chars 总和 ≈ 本章目标字数（无明确目标时每拍 1500~2500）。
- beat 是「会发生什么」的可执行场景，不写分析性语言；覆盖大纲全部 plot_points，顺序可微调但不遗漏。
- 每拍要有具体的地点与人物动作，不要只有情绪。

【本章大纲】
{chapter_outline}

【用户本章要求】
{instruction}
"""

ARC_SUMMARY_PROMPT = """你是剧情摘要员。把下面多章摘要压缩为一段「弧线摘要」（≤300 字），
保留贯穿这些章节的主线走向、人物关系变化、遗留伏笔与未解决事项；逐章细节舍弃。

【章节摘要】
{summaries}

只输出弧线摘要正文，不要输出其他文字。
"""

OUTLINE_DIVERGE_PROMPT = """你是剧情连续性检查员。对比「刚写完的章节摘要」与「下一章大纲」，
判断下一章计划是否已被正文走向架空或矛盾（人物状态对不上、事件顺序冲突、伏笔被提前消耗）。

【输出 JSON，只输出 JSON】
{"diverge": true/false, "note": "偏离时用一句话写明改什么（≤60字）；无偏离给空串"}

只输出 JSON。
"""


def build_beat_expand_prompt(chapter_outline: str, instruction: str) -> str:
    return (BEAT_EXPAND_PROMPT
            .replace("{chapter_outline}", chapter_outline or "（无）")
            .replace("{instruction}", instruction or "（无）"))


def build_arc_summary_prompt(summaries: str) -> str:
    return ARC_SUMMARY_PROMPT.replace("{summaries}", summaries)


def build_outline_diverge_prompt(chapter_summary: str, next_outline: str) -> str:
    return (OUTLINE_DIVERGE_PROMPT
            .replace("{chapter_summary}", chapter_summary or "（无）")
            .replace("{next_outline}", next_outline or "（无）"))
