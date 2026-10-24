"""角色卡 schema v2 纯函数（P1）：规范化/校验/身份/合并/元视角检测。

不依赖 astrbot，可被 tests/test_p1.py 与 scripts/migrate_v1_v2.py 直接使用。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

# ── schema v2 常量 ──────────────────────────────────────────
PERSONA_KEYS = ("appearance_anchors", "sample_dialogue", "speech_rhythm")
PLOT_KEYS = ("arc", "plot_hooks")  # v2 起从 story 段移除，剧情归大纲/设定书

# 元视角词（正文不该出现的"作者语言"）；「第N章」单独用正则，避免误伤"第一次/第二天"
_RE_CHAPTER_REF = re.compile(r"第[0-9零一二两三四五六七八九十百千]+\s*章")
_META_WORDS = ("伏笔", "主线", "支线", "大纲", "剧情线", "故事线", "悬念",
               "读者", "作者", "前文", "后文", "本章", "下一章", "后续章节",
               "铺垫", "钩子", "剧情安排")
_QUOTE_CHARS = ("“", "”", "「", "」", "『", "』", "\"")
# 网游/系统流小说的游戏面板是故事内信息，不算元视角
_RE_GAME_PANEL = re.compile(r"^(?:主线|支线)(?:任务|剧情|进度)?[：:]")
_RE_NOTE_LIKE = re.compile(r"^[（(【\[·\-\s]")  # 注记形态行首


def persona_skeleton() -> dict[str, Any]:
    return {"appearance_anchors": [], "sample_dialogue": [], "speech_rhythm": ""}


def normalize_card(card: dict) -> tuple[dict, dict[str, Any]]:
    """v1 卡 → v2：剥掉 story.arc/plot_hooks（返回 (新卡, 被移出的内容)），
    补 persona 骨架、meta.schema_version/canonical_id/inferred。不改入参。"""
    out = copy.deepcopy(card)
    moved: dict[str, Any] = {}
    story = out.get("story") if isinstance(out.get("story"), dict) else {}
    for k in PLOT_KEYS:
        if story.get(k):
            moved[k] = story.pop(k)
        else:
            story.pop(k, None)
    if story:
        out["story"] = story
    else:
        out.pop("story", None)
    if not isinstance(out.get("persona"), dict):
        out["persona"] = persona_skeleton()
    else:
        for k in PERSONA_KEYS:
            out["persona"].setdefault(k, [] if k != "speech_rhythm" else "")
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    meta["schema_version"] = "2.0"
    basic = out.get("basic") if isinstance(out.get("basic"), dict) else {}
    meta.setdefault("canonical_id", str(basic.get("name") or "").strip() or "未命名")
    meta.setdefault("inferred", [])
    out["meta"] = meta
    return out, moved


def _personality_count(v: Any) -> int:
    if isinstance(v, list):
        return len([x for x in v if str(x).strip()])
    if isinstance(v, str) and v.strip():
        return len([s for s in re.split(r"[、;；，,\n]", v) if s.strip()])
    return 0


def validate_card_v2(card: dict) -> tuple[bool, list[str]]:
    """结构 + 丰富度校验。丰富度按 story.role 分档：
    主角：外貌锚点≥3、性格≥3、示例台词≥2；其余（配角/反派/未标）：≥1/≥1/≥1。
    允许 persona.sample_dialogue 为空但 speech.catchphrases 非空（台词回退档）。"""
    problems: list[str] = []
    if not isinstance(card, dict):
        return False, ["卡片必须是 JSON 对象"]
    for sec in ("basic", "persona", "background", "story", "speech"):
        v = card.get(sec)
        if v is not None and not isinstance(v, dict):
            problems.append(f"「{sec}」应为对象")
    basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
    if not str(basic.get("name") or "").strip():
        problems.append("basic.name 必填")
    persona = card.get("persona") if isinstance(card.get("persona"), dict) else {}
    anchors = [a for a in persona.get("appearance_anchors") or [] if str(a).strip()] \
        if isinstance(persona.get("appearance_anchors"), list) else []
    dialogue = [d for d in persona.get("sample_dialogue") or [] if str(d).strip()] \
        if isinstance(persona.get("sample_dialogue"), list) else []
    speech = card.get("speech") if isinstance(card.get("speech"), dict) else {}
    phrases = [p for p in speech.get("catchphrases") or [] if str(p).strip()] \
        if isinstance(speech.get("catchphrases"), list) else []
    story = card.get("story") if isinstance(card.get("story"), dict) else {}
    if "plot_hooks" in story or "arc" in story:
        problems.append("story.arc/plot_hooks 已移除（v2）：剧情安排写进大纲或设定书，不要放卡里")
    role = str(story.get("role") or "")
    is_protagonist = "主角" in role
    need = (3, 3, 2) if is_protagonist else (1, 1, 1)
    if len(anchors) < need[0]:
        problems.append(f"外貌锚点 persona.appearance_anchors 至少 {need[0]} 条（现 {len(anchors)}）——正文反复引用的视觉细节")
    if _personality_count(basic.get("personality")) < need[1]:
        problems.append(f"性格 basic.personality 至少 {need[1]} 条（现 {_personality_count(basic.get('personality'))}）")
    if len(dialogue) + (len(phrases) if not dialogue else 0) < need[2]:
        problems.append(f"示例台词 persona.sample_dialogue 至少 {need[2]} 句（现 {len(dialogue)}，口头禅回退 {len(phrases)}）——锚定角色腔调最有效的字段")
    bg = card.get("background")
    if isinstance(bg, dict):
        rels = bg.get("relations")
        if isinstance(rels, list):
            for i, r in enumerate(rels, 1):
                if not isinstance(r, dict) or not str(r.get("name") or "").strip():
                    problems.append(f'relations 第 {i} 项缺少 name')
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    inf = meta.get("inferred")
    if inf is not None and not isinstance(inf, list):
        problems.append("meta.inferred 应为字段路径数组")
    return len(problems) == 0, problems


def identity_names(card: dict) -> set[str]:
    """一张卡的全部身份名：canonical_id + 本名 + 别名（去空白）。"""
    names: set[str] = set()
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
    for v in (meta.get("canonical_id"), basic.get("name")):
        s = str(v or "").strip()
        if s:
            names.add(s)
    aliases = basic.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    for a in aliases:
        s = str(a or "").strip()
        if s:
            names.add(s)
    return names


def merge_cards(primary: dict, secondary: dict) -> dict:
    """把 secondary 并入 primary：别名吸收、空位补值、列表追加去重。
    不动 canonical_id（以 primary 为准）；被并卡的本名进 aliases。"""
    out = copy.deepcopy(primary)
    out.setdefault("basic", {}); out.setdefault("persona", persona_skeleton())
    out.setdefault("background", {}); out.setdefault("speech", {})
    s_basic = secondary.get("basic") if isinstance(secondary.get("basic"), dict) else {}
    s_persona = secondary.get("persona") if isinstance(secondary.get("persona"), dict) else {}
    s_bg = secondary.get("background") if isinstance(secondary.get("background"), dict) else {}
    s_speech = secondary.get("speech") if isinstance(secondary.get("speech"), dict) else {}
    # 别名吸收
    aliases = [str(a) for a in (out["basic"].get("aliases") or []) if str(a).strip()]
    for n in [s_basic.get("name")] + list(s_basic.get("aliases") or []):
        s = str(n or "").strip()
        if s and s != out["basic"].get("name") and s not in aliases:
            aliases.append(s)
    out["basic"]["aliases"] = aliases
    # 字符串空位补值
    for sec_s, sec_p, keys in (
        (s_basic, out["basic"], ("gender", "age", "identity", "appearance", "personality")),
        (s_persona, out["persona"], ("speech_rhythm",)),
        (s_speech, out["speech"], ("tone", "address_style")),
        (s_bg, out["background"], ("origin", "motivation")),
    ):
        for k in keys:
            cur = sec_p.get(k)
            empty = not cur or (isinstance(cur, list) and not [x for x in cur if str(x).strip()])
            if empty and sec_s.get(k):
                sec_p[k] = copy.deepcopy(sec_s[k])
    # 列表追加去重
    def _merge_list(sec_p, sec_s, path):
        cur = [str(x) for x in (sec_p.get(path) or []) if str(x).strip()]
        for x in (sec_s.get(path) or []):
            s = str(x or "").strip()
            if s and s not in cur:
                cur.append(s)
        if cur:
            sec_p[path] = cur
    _merge_list(out["basic"], s_basic, "quirks")
    _merge_list(out["persona"], s_persona, "appearance_anchors")
    _merge_list(out["persona"], s_persona, "sample_dialogue")
    _merge_list(out["speech"], s_speech, "catchphrases")
    _merge_list(out["background"], s_bg, "secrets")
    # relations 追加去重（按 name）
    rels = [r for r in (out["background"].get("relations") or []) if isinstance(r, dict)]
    known = {str(r.get("name")) for r in rels}
    for r in (s_bg.get("relations") or []):
        if isinstance(r, dict) and str(r.get("name") or "").strip() not in known:
            rels.append(r)
            known.add(str(r.get("name")))
    out["background"]["relations"] = rels
    # inferred 并集
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    inf = [str(x) for x in (meta.get("inferred") or [])]
    s_meta = secondary.get("meta") if isinstance(secondary.get("meta"), dict) else {}
    for x in (s_meta.get("inferred") or []):
        if str(x) not in inf:
            inf.append(str(x))
    meta["inferred"] = inf
    out["meta"] = meta
    return out


def detect_meta_sentences(text: str) -> list[dict[str, Any]]:
    """逐行找作者视角泄漏。返回 [{no, line, kind}]，kind:
    auto=独立注记行（无引号、非游戏面板）可整行删；suspect=只报告不改。"""
    hits: list[dict[str, Any]] = []
    lines = text.split("\n")
    n = len(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 120:
            continue
        has_chapter = bool(_RE_CHAPTER_REF.search(s))
        has_word = any(w in s for w in _META_WORDS)
        if not (has_chapter or has_word):
            continue
        quoted = any(q in s for q in _QUOTE_CHARS)
        panel = bool(_RE_GAME_PANEL.match(s))
        # 面板行是故事内信息：只有同时引用章号才值得怀疑
        if panel and not has_chapter:
            continue
        prev_blank = (i == 0) or (not lines[i - 1].strip())
        next_blank = (i == n - 1) or (not lines[i + 1].strip())
        note_like = bool(_RE_NOTE_LIKE.match(s))
        if not quoted and not panel and (note_like or (prev_blank and next_blank)):
            hits.append({"no": i + 1, "line": s, "kind": "auto"})
        else:
            hits.append({"no": i + 1, "line": s, "kind": "suspect"})
    return hits


def scrub_meta_lines(text: str) -> tuple[str, int]:
    """删除 detect 标记为 auto 的行（渲染注入与完稿共用的保险带）。返回 (新文本, 删除数)。"""
    hits = {h["no"] for h in detect_meta_sentences(text) if h["kind"] == "auto"}
    if not hits:
        return text, 0
    kept = [ln for i, ln in enumerate(text.split("\n"), 1) if i not in hits]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return out, len(hits)
