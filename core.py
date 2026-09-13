"""写作沙盒核心：独立上下文、约束、风格卡、素材学习、自动落盘。

设计目标：与主聊天完全隔离的「子代理」写作空间。
- 每个用户一个独立沙盒目录，历史/约束/风格卡各自存放。
- 产出文本自动落盘为 txt（outputs/）。
- 素材学习：library/ 放素材，favorites/ 放收藏，学习时提炼风格卡。
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONSTRAINTS: dict[str, Any] = {
    "length": "按指令要求，未明确指定时 800~1500 字",
    "perspective": "第二人称主视角",
    "scale": "露骨",  # 暧昧 / 露骨 / 极限
    "banned_words": [],
    "description_focus": "感官细节六成、心理三成、动作与环境一成",
    "rhythm": "慢热",
    "keep_persona": True,  # 保留「♪」「人家」等昔涟底色
}

DEFAULT_STYLE_CARD: dict[str, Any] = {
    "meta": {"learned_at": "", "source_count": 0, "favorite_ratio": 0.0},
    "core": {
        "sentence_style": {"desc": "", "examples": [], "strength": "无"},
        "vocab_prefs": {"desc": "", "examples": [], "strength": "无"},
        "description_focus": {"desc": "", "examples": [], "strength": "无"},
        "person_habit": {"desc": "", "examples": [], "strength": "无"},
        "rhythm": {"desc": "", "examples": [], "strength": "无"},
        "sense_focus": {"desc": "", "examples": [], "strength": "无"},
        "scale_floor": {"desc": "", "examples": [], "strength": "无"},
        "metaphor_style": {"desc": "", "examples": [], "strength": "无"},
    },
    "samples": [],
    "extras": [],
}

OUTPUT_DIR_NAME = "outputs"
LIBRARY_DIR_NAME = "library"
FAVORITES_DIR_NAME = "favorites"
STORIES_DIR_NAME = "stories"
CHAPTERS_DIR_NAME = "chapters"
CHARACTERS_DIR_NAME = "characters"
CHARACTER_SCHEMA_VERSION = "1.0"
HISTORY_FILE = "sandbox_history.jsonl"
STYLE_CARD_FILE = "style_card.json"
STYLE_CARDS_DIR_NAME = "style_cards"
COMBO_FILE = "style_combo.json"
CORE_DIMS = (
    "sentence_style", "vocab_prefs", "description_focus",
    "person_habit", "rhythm", "sense_focus", "scale_floor", "metaphor_style",
)
CORE_LABELS = {
    "sentence_style": "句式结构",
    "vocab_prefs": "词汇偏好",
    "description_focus": "描写侧重与密度",
    "person_habit": "人称与视角",
    "rhythm": "节奏",
    "sense_focus": "感官侧重",
    "scale_floor": "尺度与露骨度",
    "metaphor_style": "比喻方式",
}
CONSTRAINTS_FILE = "constraints.json"
CHAPTER_SUMMARIES_FILE = "chapter_summaries.json"
STORY_STATE_FILE = "story_state.json"
OUTLINE_FILE = "outline.json"
PENDING_OUTLINE_FILE = "outline.pending.json"
ACTIVE_WORK_FILE = "active_work.json"
STORY_CONFIG_FILE = "story_config.json"

DEFAULT_STORY_CONFIG: dict[str, Any] = {
    "chapter_link": "auto",  # auto 自动 / continuous 连续叙事 / break 断章收尾
}

DEFAULT_STORY_STATE: dict[str, Any] = {
    "meta": {"title": "", "current_chapter": 0},
    "world": {},
    "characters": [],
    "foreshadowing": [],
    "unresolved": [],
}

_LOCK = threading.Lock()


def _safe_title(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "", text).strip()
    return cleaned[:max_len] or "未命名"


class Sandbox:
    """单个用户的独立写作沙盒。"""

    def __init__(self, user_dir: Path):
        self.user_dir = user_dir
        self.outputs_dir = user_dir / OUTPUT_DIR_NAME
        self.library_dir = user_dir / LIBRARY_DIR_NAME
        self.favorites_dir = user_dir / FAVORITES_DIR_NAME
        self.stories_dir = user_dir / STORIES_DIR_NAME
        self.characters_dir = user_dir / CHARACTERS_DIR_NAME
        for d in (self.outputs_dir, self.library_dir, self.favorites_dir, self.stories_dir, self.characters_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.history_file = user_dir / HISTORY_FILE
        self.style_card_file = user_dir / STYLE_CARD_FILE
        self.constraints_file = user_dir / CONSTRAINTS_FILE
        self.active_work_file = user_dir / ACTIVE_WORK_FILE
        self.style_cards_dir = user_dir / STYLE_CARDS_DIR_NAME
        self.combo_file = user_dir / COMBO_FILE

        self.constraints: dict[str, Any] = dict(DEFAULT_CONSTRAINTS)
        self.style_card: dict[str, Any] = dict(DEFAULT_STYLE_CARD)
        self.combo: dict[str, Any] = {"cards": [], "dims": {}}
        self.active_work: Optional[str] = None
        self.style_cards_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    # ── 状态读写 ──────────────────────────────
    def _load_state(self) -> None:
        if self.constraints_file.exists():
            try:
                data = json.loads(self.constraints_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged = dict(DEFAULT_CONSTRAINTS)
                    merged.update(data)
                    self.constraints = merged
            except Exception:
                pass
        if self.style_card_file.exists():
            try:
                data = json.loads(self.style_card_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.style_card = data
            except Exception:
                pass
        if self.active_work_file.exists():
            try:
                data = json.loads(self.active_work_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("work"):
                    self.active_work = str(data["work"])
            except Exception:
                pass
        if self.combo_file.exists():
            try:
                data = json.loads(self.combo_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cards = [str(c) for c in (data.get("cards") or []) if str(c)]
                    dims_raw = data.get("dims") or {}
                    dims: dict[str, list[str]] = {}
                    if isinstance(dims_raw, dict):
                        for k, v in dims_raw.items():
                            if isinstance(v, list):
                                dims[str(k)] = [str(x) for x in v if str(x)]
                            elif v:
                                dims[str(k)] = [str(v)]
                    self.combo = {"cards": cards, "dims": dims}
            except Exception:
                pass

    def save_constraints(self) -> None:
        with _LOCK:
            self.constraints_file.write_text(
                json.dumps(self.constraints, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def save_style_card(self) -> None:
        with _LOCK:
            self.style_card_file.write_text(
                json.dumps(self.style_card, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def save_style_card_to_library(self, name: str, card: dict) -> Path:
        """把一张风格卡保存到 style_cards/ 库存，可被 sandbox_style_combo 点名。"""
        safe = _safe_title(name, max_len=32) or "未命名风格"
        path = self.style_cards_dir / f"{safe}.json"
        with _LOCK:
            path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ── 风格卡库与组合 ────────────────────────
    def list_style_cards(self) -> list[str]:
        """列出 style_cards/ 目录下所有可点名的风格卡。"""
        if not self.style_cards_dir.exists():
            return []
        return sorted(p.stem for p in self.style_cards_dir.glob("*.json"))

    def load_style_card_by_name(self, name: str) -> Optional[dict]:
        """按卡名（不带 .json）加载一张风格卡，找不到返回 None。"""
        name = name.strip()
        if not name:
            return None
        candidates = [p for p in self.style_cards_dir.glob("*.json") if p.stem == name]
        if not candidates:
            return None
        try:
            data = json.loads(candidates[0].read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return None

    def set_combo(self, cards: list[str], dims: Optional[dict[str, list[str]]] = None) -> tuple[bool, str]:
        """设置风格组合：cards 为参与融合的底卡列表；dims 为维度级来源指定。"""
        dims = dims or {}
        available = self.list_style_cards()
        avail_set = set(available)
        clean_cards = [c.strip() for c in cards if c.strip()]
        missing = [c for c in clean_cards if c not in avail_set]
        if not clean_cards:
            return False, "组合至少需要一张卡。"
        if missing:
            return False, f"卡库里没有：{'、'.join(missing)}。现有：{'、'.join(available) or '（空）'}"
        clean_dims: dict[str, list[str]] = {}
        for k, vals in dims.items():
            if k not in CORE_DIMS:
                return False, f"未知维度：{k}。可用：{'、'.join(CORE_DIMS)}"
            vs = [v.strip() for v in vals if v.strip()]
            mv = [v for v in vs if v not in avail_set]
            if mv:
                return False, f"维度 {k} 指定的卡不在卡库：{'、'.join(mv)}"
            if vs:
                clean_dims[k] = vs
        self.combo = {"cards": clean_cards, "dims": clean_dims}
        with _LOCK:
            self.combo_file.write_text(
                json.dumps(self.combo, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return True, "风格组合已保存。"

    def clear_combo(self) -> None:
        self.combo = {"cards": [], "dims": {}}
        with _LOCK:
            self.combo_file.write_text(
                json.dumps(self.combo, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get_combo(self) -> dict[str, Any]:
        return self.combo

    def combo_status(self) -> str:
        combo = self.combo
        cards = combo.get("cards") or []
        if not cards:
            return "（未启用组合，写作使用主卡 style_card.json）"
        dims = combo.get("dims") or {}
        lines = [f"组合底卡：{'、'.join(cards)}"]
        if dims:
            for k, vs in dims.items():
                lines.append(f"  {CORE_LABELS.get(k, k)} ← {'、'.join(vs)}")
        else:
            lines.append("  全部维度：融合所有底卡（冲突维度请在 dims 中指定单卡来源）")
        return "\n".join(lines)

    def render_combo_card(self) -> str:
        """组合渲染：每个维度列出其来源卡的笔法（默认全部底卡融合，dims 覆盖），供模型自行融合。"""
        combo = self.combo
        cards = combo.get("cards") or []
        dims = combo.get("dims") or {}
        if not cards:
            return "（未启用组合，使用主卡风格）"
        loaded: dict[str, dict] = {}
        for c in cards:
            card = self.load_style_card_by_name(c)
            if card is not None:
                loaded[c] = card
        if not loaded:
            return "（组合卡加载失败，卡库文件可能已被移动）"

        def _dim_sources(k: str) -> list[str]:
            if k in dims:
                return [s for s in dims[k] if s in loaded]
            return list(loaded.keys())

        lines: list[str] = []
        lines.append(f"（风格组合：{' + '.join(loaded.keys())}；冲突维度按 dims 指定）")
        for k in CORE_DIMS:
            srcs = _dim_sources(k)
            if not srcs:
                continue
            parts: list[str] = []
            for s in srcs:
                core = loaded[s].get("core") or {}
                v = core.get(k) if isinstance(core.get(k), dict) else None
                if not v or not v.get("desc"):
                    continue
                desc = str(v["desc"])
                ex = v.get("examples") or []
                sample = f"（例：{str(ex[0])[:40]}…）" if ex else ""
                parts.append(f"[{s}] {desc}{sample}")
            if parts:
                lines.append(f"- {CORE_LABELS.get(k, k)}：{' ｜ '.join(parts)}")
        samples_total = sum(len(loaded[c].get("samples") or []) for c in loaded)
        extras_total = sum(len(loaded[c].get("extras") or []) for c in loaded)
        if samples_total:
            lines.append(f"- 高权重示例段 ×{samples_total}（按来源标注）")
            for c in loaded:
                for s in loaded[c].get("samples") or []:
                    lines.append(f"  〔{c}〕{str(s)[:60]}")
        if extras_total:
            lines.append(f"- 单篇特色样本 ×{extras_total}（按需调用）")
            for c in loaded:
                for e in loaded[c].get("extras") or []:
                    if isinstance(e, dict):
                        lines.append(f"  〔{c}〕{e.get('tag', '')}: {str(e.get('text', ''))[:60]}")
        return "\n".join(lines)

    # ── 长篇作品工作区 ────────────────────────
    def get_active_work(self) -> Optional[str]:
        return self.active_work

    def set_active_work(self, work: str) -> str:
        work = _safe_title(work, max_len=32) or "未命名作品"
        self.active_work = work
        self.work_dir(work)  # 确保作品目录存在，list_works 立即可见
        with _LOCK:
            self.active_work_file.write_text(
                json.dumps({"work": work}, ensure_ascii=False), encoding="utf-8"
            )
        return work

    def clear_active_work(self) -> None:
        self.active_work = None
        with _LOCK:
            self.active_work_file.write_text(
                json.dumps({"work": ""}, ensure_ascii=False), encoding="utf-8"
            )

    def list_works(self) -> list[str]:
        if not self.stories_dir.exists():
            return []
        return sorted(
            p.name for p in self.stories_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    def work_dir(self, work: str) -> Path:
        d = self.stories_dir / _safe_title(work, max_len=32)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def chapters_dir(self, work: str) -> Path:
        d = self.work_dir(work) / CHAPTERS_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_chapter_summaries(self, work: str) -> list[dict[str, Any]]:
        f = self.work_dir(work) / CHAPTER_SUMMARIES_FILE
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items = data.get("summaries", []) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return []
            return [it for it in items if isinstance(it, dict) and "ch" in it]
        except Exception:
            return []

    def upsert_chapter_summary(self, work: str, ch: int, summary: str) -> None:
        items = self.load_chapter_summaries(work)
        items = [it for it in items if it.get("ch") != ch]
        items.append({"ch": ch, "summary": summary})
        items.sort(key=lambda it: int(it.get("ch", 0)))
        with _LOCK:
            (self.work_dir(work) / CHAPTER_SUMMARIES_FILE).write_text(
                json.dumps({"summaries": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def load_story_state(self, work: str) -> dict[str, Any]:
        f = self.work_dir(work) / STORY_STATE_FILE
        if not f.exists():
            return dict(DEFAULT_STORY_STATE)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return dict(DEFAULT_STORY_STATE)

    def save_story_state(self, work: str, state: dict[str, Any]) -> None:
        with _LOCK:
            (self.work_dir(work) / STORY_STATE_FILE).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # ── 大纲（JSON：全局脉络 + 分章节点）──────────
    def load_outline(self, work: str) -> dict[str, Any]:
        """加载作品大纲；不存在或解析失败返回空结构。"""
        f = self.work_dir(work) / OUTLINE_FILE
        if not f.exists():
            return {"meta": {}, "chapters": []}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"meta": {}, "chapters": []}

    def save_outline(self, work: str, outline: dict[str, Any]) -> None:
        with _LOCK:
            (self.work_dir(work) / OUTLINE_FILE).write_text(
                json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # ── 大纲草稿（待确认）─────────────────────
    def save_pending_outline(self, work: str, outline: dict[str, Any]) -> None:
        """保存大纲草稿（待审核），不覆盖正式大纲。"""
        with _LOCK:
            (self.work_dir(work) / PENDING_OUTLINE_FILE).write_text(
                json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def load_pending_outline(self, work: str) -> dict[str, Any] | None:
        """加载大纲草稿；不存在或解析失败返回 None。"""
        f = self.work_dir(work) / PENDING_OUTLINE_FILE
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return None

    def has_pending_outline(self, work: str) -> bool:
        return self.load_pending_outline(work) is not None

    def discard_pending_outline(self, work: str) -> None:
        """丢弃大纲草稿。"""
        f = self.work_dir(work) / PENDING_OUTLINE_FILE
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

    def confirm_outline(self, work: str) -> bool:
        """把草稿落盘为正式大纲；有草稿且成功返回 True。"""
        pending = self.load_pending_outline(work)
        if pending is None:
            return False
        self.save_outline(work, pending)
        self.discard_pending_outline(work)
        return True

    def render_outline(self, work: str) -> str:
        """完整渲染大纲（全局脉络 + 全部章节节点），用于查看/编辑。"""
        return self._render_outline_data(work, self.load_outline(work), prefix="")

    def render_pending_outline(self, work: str) -> str:
        """渲染大纲草稿（待审核）。"""
        pending = self.load_pending_outline(work)
        if pending is None:
            return "（当前没有待确认的大纲草稿）"
        return self._render_outline_data(work, pending, prefix="【待确认草稿】")

    def _render_outline_data(self, work: str, outline: dict[str, Any], prefix: str = "") -> str:
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []
        lines = [f"【《{work}》大纲】"]
        if prefix:
            lines.append(prefix)
        if meta.get("premise"):
            lines.append(f"核心设定：{meta['premise']}")
        if meta.get("theme"):
            lines.append(f"主题：{meta['theme']}")
        if meta.get("tone"):
            lines.append(f"氛围基调：{meta['tone']}")
        arcs = meta.get("arcs") or []
        if isinstance(arcs, list) and arcs:
            lines.append("故事弧线：" + "；".join(str(a) for a in arcs))
        chars = meta.get("characters") or []
        if isinstance(chars, list) and chars:
            for c in chars:
                if isinstance(c, dict):
                    parts = [str(c.get(k, "")) for k in ("name", "role", "arc") if c.get(k)]
                    if parts:
                        lines.append("人物：" + "｜".join(parts))
        scale_map = meta.get("scale_map")
        if scale_map:
            lines.append(f"涩涩密度分布：{json.dumps(scale_map, ensure_ascii=False)}")
        if not chapters:
            lines.append("（还没有分章节点）")
        for it in chapters:
            if not isinstance(it, dict):
                continue
            ch = it.get("ch", "?")
            title = it.get("title", "")
            head = f"[第{ch}章]" + (f" {title}" if title else "")
            summary = it.get("summary", "")
            if summary:
                head += f" {summary}"
            lines.append(head)
            pts = it.get("plot_points") or []
            if isinstance(pts, list) and pts:
                for p in pts[:4]:
                    lines.append(f"  · {p}")
            tone = it.get("tone", "")
            scale = it.get("scale", "")
            if tone or scale:
                lines.append(f"  （基调：{tone or '未定'}｜尺度：{scale or '未定'}）")
        return "\n".join(lines)

    def render_chapter_outline(self, work: str, ch: int) -> str:
        """只取当前章节节点 + 全局脉络概要，写正文时注入：专注本章又不丢全局。"""
        outline = self.load_outline(work)
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []
        lines = [f"【《{work}》本章大纲·第{ch}章】"]
        if meta.get("premise"):
            lines.append(f"全局·核心设定：{meta['premise']}")
        arcs = meta.get("arcs") or []
        if isinstance(arcs, list) and arcs:
            lines.append("全局·弧线：" + "；".join(str(a) for a in arcs))
        scale_map = meta.get("scale_map")
        if scale_map:
            lines.append(f"全局·密度分布：{json.dumps(scale_map, ensure_ascii=False)}")
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            candidates = [it for it in chapters if isinstance(it, dict)]
            if candidates:
                cur = min(candidates, key=lambda it: abs(int(it.get("ch", 0)) - ch))
        if cur is None:
            lines.append("（大纲里还没有这一章的节点，可按全局脉络自由展开）")
            return "\n".join(lines)
        title = cur.get("title", "")
        if title:
            lines.append(f"章节标题：{title}")
        summary = cur.get("summary", "")
        if summary:
            lines.append(f"本章要点：{summary}")
        pts = cur.get("plot_points") or []
        if isinstance(pts, list) and pts:
            lines.append("情节节点：")
            for p in pts:
                lines.append(f"  · {p}")
        chars = cur.get("characters") or []
        if isinstance(chars, list) and chars:
            lines.append("出场人物：" + "、".join(str(c) for c in chars))
        tone = cur.get("tone", "")
        scale = cur.get("scale", "")
        if tone:
            lines.append(f"本章基调：{tone}")
        if scale:
            lines.append(f"本章尺度：{scale}")
        hook = cur.get("cliffhanger", "")
        if hook:
            lines.append(f"结尾钩子：{hook}")
        moods = cur.get("mood") or []
        if isinstance(moods, str):
            moods = [moods]
        moods = [str(m) for m in moods if str(m).strip()]
        if moods:
            lines.append("本章灵感便签：" + "；".join(moods))
        notes = cur.get("notes", "")
        if notes:
            lines.append(f"备注：{notes}")
        return "\n".join(lines)

    # ── 章节接续配置（按作品开关）──────────────
    def load_story_config(self, work: str) -> dict[str, Any]:
        """加载作品小配置；不存在返回默认。"""
        f = self.work_dir(work) / STORY_CONFIG_FILE
        if not f.exists():
            return dict(DEFAULT_STORY_CONFIG)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = dict(DEFAULT_STORY_CONFIG)
                cfg.update({k: v for k, v in data.items() if k in cfg})
                return cfg
        except Exception:
            pass
        return dict(DEFAULT_STORY_CONFIG)

    def save_story_config(self, work: str, cfg: dict[str, Any]) -> None:
        with _LOCK:
            (self.work_dir(work) / STORY_CONFIG_FILE).write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def get_chapter_link_mode(self, work: str) -> str:
        return self.load_story_config(work).get("chapter_link", "auto")

    def set_chapter_link_mode(self, work: str, mode: str) -> str:
        mode = mode.strip().lower()
        if mode not in ("auto", "continuous", "break"):
            return "invalid"
        cfg = self.load_story_config(work)
        cfg["chapter_link"] = mode
        self.save_story_config(work, cfg)
        return mode

    # ── 灵感便签（绑大纲章节节点）──────────────
    def add_outline_mood(self, work: str, ch: int, text: str) -> bool:
        """给大纲某章节点追加灵感便签；节点不存在返回 False。"""
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            return False
        moods = cur.get("mood") or []
        if isinstance(moods, str):
            moods = [moods]
        moods = [m for m in moods if str(m).strip()]
        moods.append(text.strip())
        cur["mood"] = moods
        self.save_outline(work, outline)
        return True

    def clear_outline_mood(self, work: str, ch: int) -> bool:
        """清空某章灵感便签；节点不存在返回 False。"""
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            return False
        if "mood" in cur:
            cur.pop("mood", None)
            self.save_outline(work, outline)
        return True

    def render_chapter_mood(self, work: str, ch: int) -> str:
        """取本章氛围引子：灵感便签 > 结尾钩子 > 基调，断章续写时用。"""
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            return ""
        parts = []
        moods = cur.get("mood") or []
        if isinstance(moods, str):
            moods = [moods]
        moods = [str(m) for m in moods if str(m).strip()]
        if moods:
            parts.append("灵感便签：" + "；".join(moods))
        hook = cur.get("cliffhanger", "")
        if hook:
            parts.append(f"结尾钩子：{hook}")
        tone = cur.get("tone", "")
        if tone:
            parts.append(f"本章基调：{tone}")
        return "\n".join(parts) if parts else ""

    def chapter_exists(self, work: str, ch: int) -> bool:
        """章节文件是否已存在（用于判断 continue 场景）。"""
        d = self.chapters_dir(work)
        if not d.exists():
            return False
        return bool(list(d.glob(f"ch{int(ch):02d}*.txt")))

    def read_chapter_tail(self, work: str, ch: int, n_lines: int = 3, max_chars: int = 600) -> str:
        """读取章节文件末尾几行，用于连续叙事续写。"""
        d = self.chapters_dir(work)
        if not d.exists():
            return ""
        files = sorted(d.glob(f"ch{int(ch):02d}*.txt"))
        if not files:
            return ""
        try:
            text = files[0].read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        if not text:
            return ""
        lines = text.splitlines()
        tail = "\n".join(lines[-n_lines:])
        if len(tail) > max_chars:
            tail = tail[-max_chars:]
        return tail

    # ── 人设卡（全局 / 作品 双存储）──────────────
    def work_characters_dir(self, work: str) -> Path:
        d = self.work_dir(work) / CHARACTERS_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_characters(self, work: str = "") -> list[str]:
        """列出全局卡；work 非空时并列该作品卡（去重）。"""
        names = sorted(p.stem for p in self.characters_dir.glob("*.json"))
        if work:
            wd = self.work_dir(work) / CHARACTERS_DIR_NAME
            for p in sorted(wd.glob("*.json")):
                if p.stem not in names:
                    names.append(p.stem)
        return names

    def _char_file(self, name: str, scope: str, work: str = "") -> Path:
        safe = f"{_safe_title(name, max_len=32)}.json"
        if scope == "work":
            if not work:
                work = self.get_active_work() or ""
            return self.work_characters_dir(work) / safe
        return self.characters_dir / safe

    def load_character(self, name: str, work: str = "") -> Optional[dict]:
        """加载人设卡：作品卡优先，回退全局卡。"""
        name = name.strip()
        if not name:
            return None
        safe = f"{_safe_title(name, max_len=32)}.json"
        if work:
            wf = self.work_characters_dir(work) / safe
            if wf.exists():
                try:
                    data = json.loads(wf.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        gf = self.characters_dir / safe
        if gf.exists():
            try:
                data = json.loads(gf.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def save_character(self, card: dict, scope: str, work: str = "") -> Path:
        """保存人设卡：scope=global 存全局目录，scope=work 存作品目录。"""
        basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        meta["scope"] = scope
        meta["schema_version"] = CHARACTER_SCHEMA_VERSION
        meta["created_at"] = meta.get("created_at") or datetime.now().isoformat(timespec="seconds")
        if scope == "work":
            meta["work"] = work or self.get_active_work() or ""
        else:
            meta.pop("work", None)
        card["meta"] = meta
        fname = _safe_title(str(basic.get("name") or "未命名"), max_len=32)
        path = (self.work_characters_dir(work) if scope == "work" else self.characters_dir) / f"{fname}.json"
        with _LOCK:
            path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def delete_character(self, name: str, scope: str, work: str = "") -> bool:
        path = self._char_file(name, scope, work)
        if path.exists():
            path.unlink()
            return True
        return False

    def render_character(self, card: dict) -> str:
        """渲染一张人设卡为文本。"""
        lines: list[str] = []
        basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        name = basic.get("name") or meta.get("title") or "未命名"
        lines.append(f"【人设卡·{name}】")
        if basic.get("aliases"):
            lines.append(f"别名：{'、'.join(str(a) for a in basic['aliases'])}")
        for k, label in (("gender", "性别"), ("age", "年龄"), ("identity", "身份"), ("appearance", "外貌"), ("personality", "性格")):
            v = basic.get(k)
            if v:
                lines.append(f"{label}：{v}")
        quirks = basic.get("quirks") or []
        if isinstance(quirks, list) and quirks:
            lines.append("习惯小动作：" + "、".join(str(q) for q in quirks))
        bg = card.get("background") if isinstance(card.get("background"), dict) else {}
        if bg.get("origin"):
            lines.append(f"出身来历：{bg['origin']}")
        if bg.get("motivation"):
            lines.append(f"核心动机：{bg['motivation']}")
        secrets = bg.get("secrets") or []
        if isinstance(secrets, list) and secrets:
            lines.append("秘密/软肋：" + "、".join(str(s) for s in secrets))
        rels = bg.get("relations") or []
        if isinstance(rels, list) and rels:
            lines.append("关系：")
            for r in rels:
                if isinstance(r, dict):
                    parts = [str(r.get(k, "")) for k in ("name", "relation", "note") if r.get(k)]
                    if parts:
                        lines.append(f"  · {'｜'.join(parts)}")
        st = card.get("story") if isinstance(card.get("story"), dict) else {}
        if st.get("role"):
            lines.append(f"定位：{st['role']}")
        if st.get("arc"):
            lines.append(f"人物弧线：{st['arc']}")
        hooks = st.get("plot_hooks") or []
        if isinstance(hooks, list) and hooks:
            lines.append("剧情钩子：" + "、".join(str(h) for h in hooks))
        if st.get("scale_role"):
            lines.append(f"涩涩参与度：{st['scale_role']}")
        sp = card.get("speech") if isinstance(card.get("speech"), dict) else {}
        if sp.get("tone"):
            lines.append(f"说话腔调：{sp['tone']}")
        if sp.get("address_style"):
            lines.append(f"称呼方式：{sp['address_style']}")
        cps = sp.get("catchphrases") or []
        if isinstance(cps, list) and cps:
            lines.append("口头禅：" + "、".join(str(c) for c in cps))
        scope_label = "全局" if meta.get("scope") != "work" else f"作品《{meta.get('work', '')}》"
        lines.append(f"（存储：{scope_label}）")
        return "\n".join(lines)

    def render_global_card_pool(self, work: str, ch: int) -> str:
        """全局卡池摘要：一行一张（名字｜身份｜核心特征），供选角判断。

        排除本章已出场（已由 render_work_characters 注入）与已有作品卡的角色，
        避免重复注入或作品卡被全局卡遮蔽。
        """
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        exclude: set[str] = set()
        if cur and isinstance(cur.get("characters"), list):
            exclude |= {str(c).strip() for c in cur["characters"] if str(c).strip()}
        wd = self.work_dir(work) / CHARACTERS_DIR_NAME
        for p in sorted(wd.glob("*.json")):
            exclude.add(p.stem)
        lines: list[str] = []
        for p in sorted(self.characters_dir.glob("*.json")):
            try:
                card = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(card, dict):
                continue
            basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
            name = str(basic.get("name") or p.stem).strip()
            if not name or name in exclude:
                continue
            identity = str(basic.get("identity") or "").strip()
            personality = str(basic.get("personality") or "").strip()
            st = card.get("story") if isinstance(card.get("story"), dict) else {}
            role = str(st.get("role") or "").strip()
            feat = personality[:40] or identity[:40] or role
            line = f"- {name}"
            if identity:
                line += f"｜{identity}"
            if feat:
                line += f"｜{feat}"
            lines.append(line)
        return "\n".join(lines) if lines else ""

    def render_work_characters(self, work: str, ch: int) -> str:
        """按大纲章节节点的「出场角色」加载人设卡（作品卡优先），拼成注入文本。"""
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            candidates = [it for it in chapters if isinstance(it, dict)]
            if candidates:
                cur = min(candidates, key=lambda it: abs(int(it.get("ch", 0)) - ch))
        names: list[str] = []
        if cur and isinstance(cur.get("characters"), list):
            names = [str(c) for c in cur["characters"] if str(c).strip()]
        if not names:
            return ""
        lines = [f"【本章出场角色人设卡（《{work}》第{ch}章）】"]
        for n in names:
            card = self.load_character(n, work)
            if card:
                lines.append(self.render_character(card))
            else:
                lines.append(f"（角色「{n}」还没有人设卡，可按上下文自由处理）")
        return "\n".join(lines)

    _NON_PERSON_TAIL = set("猫狗鸟鱼店馆楼屋山城镇村院厅房桥路树花月界泉谷川林峰崖湾洲原野漠岭岸河江湖海云霞霜雪风雨星日夜晨昏光影烟雾尘沙石岩玉珠花草树叶枝根藤果实籽瓣潮汐波浪溪涧瀑布潭泽")

    # 关系句式里出现但不算「人名」的常见称谓/泛称（整卡扫描时排除）
    _RELATION_COMMON_WORDS = set(
        "家人朋友兄弟姊妹姐妹父母双亲母亲父亲妻子丈夫恋人情人敌人仇人同伴伙伴同事同学"
        "邻居路人故人旧识恩人仇家师父徒弟师尊弟子老师学生兄长妹妹弟弟哥哥姐姐叔伯姑舅"
        "亲戚长辈晚辈子女儿女乡亲邻里众人大家自己彼此对方谁某一切有些那些这些哪个哪位"
        "熟人新人旧人前人后人外人内人本人亲人挚友密友战友同僚同乡同门同窗心腹亲信下属"
        "上司老板掌柜伙计仆役丫鬟家丁护卫随从红颜蓝颜知己伯乐贵人对手劲敌宿敌情敌冤家"
        "对头死对头陌生人旁人家中老小一家老小孤老孤儿寡妇遗孀弃子继母继父养母养父义父"
        "义母干爹干娘结拜忘年交莫逆生死交患难交泛泛之交点头交一面之缘青梅竹马未婚妻未婚夫前任"
        "他们她们我们你们咱们人家自己彼此对方众人各位诸位"
    )

    # 从整卡文本里抓「明确关系句式」中出现的人名候选（只抓带关系名词/关系动作的结构，降低误报）
    _RELATION_PATTERNS = (
        re.compile(r"(?:与|和|跟|同)([\u4e00-\u9fff]{2,3}?)(?:有|是|为|成)?(?:的)?(?:关系|恩怨|旧情|旧怨|约定|婚约|交情|情谊|羁绊|缘分|师徒|主仆|兄妹|兄弟|姐妹|母女|父子|仇怨|过节|瓜葛|牵连|牵扯|情债|旧识)"),
        re.compile(r"(?:与|和|跟|同)([\u4e00-\u9fff]{2,3}?)(?:有|是|为|成)?(?:一起|重逢|相遇|相识|联手|结盟|结拜|决裂|反目|同居|共事|同行|结伴|成婚|成亲|对峙|交手|并肩)"),
    )

    @staticmethod
    def _looks_like_person_name(rn: str) -> bool:
        """判断一个关系对象名是否「像人名」：2~4字、无「的」字结构、不以常见物名词尾结尾。"""
        if not (2 <= len(rn) <= 4):
            return False
        if "的" in rn:
            return False
        if rn[-1] in Sandbox._NON_PERSON_TAIL:
            return False
        return True

    def _work_body_text(self, work: str, max_chars: int = 200000) -> str:
        """拼接作品全部章节正文（截断上限），用于「正文是否出现过该角色」的判定。"""
        parts: list[str] = []
        total = 0
        for p in sorted(self.chapters_dir(work).glob("*.txt")):
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if total + len(t) > max_chars:
                parts.append(t[: max_chars - total])
                break
            parts.append(t)
            total += len(t)
        return "\n".join(parts)

    @staticmethod
    def _card_all_text(card: dict) -> str:
        """把整张人设卡的所有字段文本拼起来，供整卡扫描。"""
        parts: list[str] = []

        def walk(v):
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                for it in v:
                    walk(it)
            elif isinstance(v, dict):
                for it in v.values():
                    walk(it)

        walk(card)
        return "\n".join(parts)

    def _card_foreign_names(self, card: dict, known: set[str], body_text: str) -> list[str]:
        """从整张卡（所有字段）里提取「像人名但不在 known ∪ 正文」的对象名。

        用于揪出临时人物卡里夹带的未出场/未提到人物关联。
        """
        if not isinstance(card, dict):
            return []
        candidates: set[str] = set()
        bg = card.get("background") if isinstance(card.get("background"), dict) else {}
        rels = bg.get("relations") or []
        if isinstance(rels, list):
            for r in rels:
                if isinstance(r, dict):
                    rn = str(r.get("name") or "").strip()
                    if rn and not rn.startswith("（"):
                        candidates.add(rn)
        text = Sandbox._card_all_text(card)
        for pat in Sandbox._RELATION_PATTERNS:
            for m in pat.finditer(text):
                nm = m.group(1).strip()
                if nm:
                    candidates.add(nm)
        suspects: list[str] = []
        for nm in sorted(candidates):
            if nm.endswith("们"):
                nm = nm[:-1]
            if not nm or nm in Sandbox._RELATION_COMMON_WORDS:
                continue
            if nm in known or nm in body_text:
                continue
            if Sandbox._looks_like_person_name(nm):
                suspects.append(nm)
        return suspects

    def check_card_foreign(self, card: dict, work: str) -> list[str]:
        """单张卡即时自检：卡里提到的、不在大纲名单/卡名/正文里的人物名。"""
        if not isinstance(card, dict) or not work:
            return []
        outline = self.load_outline(work)
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []
        known: set[str] = set()
        for c in meta.get("characters") or []:
            if isinstance(c, dict) and c.get("name"):
                known.add(str(c["name"]).strip())
            elif isinstance(c, str) and c.strip():
                known.add(c.strip())
        for it in chapters:
            if isinstance(it, dict) and isinstance(it.get("characters"), list):
                for c in it["characters"]:
                    if str(c).strip():
                        known.add(str(c).strip())
        known |= set(self.list_characters(work))
        body_text = self._work_body_text(work)
        return self._card_foreign_names(card, known, body_text)

    def audit_characters(self, work: str) -> dict[str, Any]:
        """复核当前作品的人设卡与大纲/正文的一致性。

        返回结构化报告：
        - stale: 有卡但大纲零出场**且正文零出现**的角色（旧卡残留候选）
        - missing: 大纲出场但没建卡的角色（缺卡，写作时只能按上下文自由处理）
        - foreign_relations: {卡名: [无关角色名]}，卡里 relations 引用了像人名但不在
          大纲/正文/卡名名单里的对象（疑似混入旧大纲或别的作品的角色）
        - chapter_chars: 大纲各章出场角色合集（用于对照）
        - meta_chars: 大纲 meta.characters 里的角色名
        """
        outline = self.load_outline(work)
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []

        meta_chars: set[str] = set()
        for c in meta.get("characters") or []:
            if isinstance(c, dict) and c.get("name"):
                meta_chars.add(str(c["name"]).strip())
            elif isinstance(c, str) and c.strip():
                meta_chars.add(c.strip())

        chapter_chars: set[str] = set()
        for it in chapters:
            if isinstance(it, dict) and isinstance(it.get("characters"), list):
                for c in it["characters"]:
                    if str(c).strip():
                        chapter_chars.add(str(c).strip())

        all_roles = meta_chars | chapter_chars
        body_text = self._work_body_text(work)
        card_names = self.list_characters(work)
        stale: list[str] = []
        for n in card_names:
            if n not in all_roles and n not in body_text:
                stale.append(n)

        missing: list[str] = []
        for n in sorted(all_roles):
            if not self.load_character(n, work):
                missing.append(n)

        foreign_relations: dict[str, list[str]] = {}
        known = all_roles | set(card_names)
        for n in card_names:
            card = self.load_character(n, work)
            if not card:
                continue
            bg = card.get("background") if isinstance(card.get("background"), dict) else {}
            rels = bg.get("relations") or []
            if not isinstance(rels, list):
                continue
            suspects: list[str] = []
            for r in rels:
                if not isinstance(r, dict):
                    continue
                rn = str(r.get("name") or "").strip()
                if rn.startswith("（"):
                    continue
                if rn and rn not in known and rn not in body_text:
                    if Sandbox._looks_like_person_name(rn):
                        suspects.append(rn)
            if suspects:
                foreign_relations[n] = suspects

        foreign_mentions: dict[str, list[str]] = {}
        for n in card_names:
            card = self.load_character(n, work)
            if not card:
                continue
            suspects = self._card_foreign_names(card, known, body_text)
            if suspects:
                foreign_mentions[n] = suspects

        return {
            "stale": stale,
            "missing": missing,
            "foreign_relations": foreign_relations,
            "foreign_mentions": foreign_mentions,
            "chapter_chars": sorted(chapter_chars),
            "meta_chars": sorted(meta_chars),
        }

    def render_character_audit(self, work: str) -> str:
        """把人物卡复核报告渲染成可读文本。"""
        rep = self.audit_characters(work)
        lines = [f"【《{work}》人物卡复核】"]
        stale = rep["stale"]
        missing = rep["missing"]
        foreign = rep["foreign_relations"]
        foreign_mentions = rep.get("foreign_mentions") or {}
        if stale:
            lines.append("⚠️ 疑似旧卡残留（有卡但大纲零出场）：")
            lines.append("、".join(f"「{s}」" for s in stale))
        if missing:
            lines.append("📌 大纲有出场但没建卡：")
            lines.append("、".join(f"「{m}」" for m in missing))
        foreign_merged: dict[str, list[str]] = {}
        for card in sorted(set(foreign) | set(foreign_mentions)):
            merged = sorted(set(foreign.get(card, [])) | set(foreign_mentions.get(card, [])))
            if merged:
                foreign_merged[card] = merged
        if foreign_merged:
            lines.append("🔗 卡里引用了名单外的角色（疑似夹带未出场/未提到人物的关联）：")
            for card, names in foreign_merged.items():
                lines.append(f"  「{card}」→ {'、'.join(names)}")
        if not stale and not missing and not foreign_merged:
            lines.append("人设卡与大纲一致，没有发现旧卡残留、缺卡或无关信息♪")
        return "\n".join(lines)

    def next_chapter_no(self, work: str) -> int:
        items = self.load_chapter_summaries(work)
        if items:
            return max(int(it.get("ch", 0)) for it in items) + 1
        return 1

    def latest_chapter_no(self, work: str) -> int:
        items = self.load_chapter_summaries(work)
        if items:
            return max(int(it.get("ch", 0)) for it in items)
        return 0

    def save_chapter(self, work: str, ch: int, text: str, tag: str = "") -> Path:
        title = _safe_title(tag, max_len=20)
        filename = f"ch{ch:02d}_{title}.txt" if title else f"ch{ch:02d}.txt"
        path = self.chapters_dir(work) / filename
        path.write_text(text, encoding="utf-8")
        return path

    def append_chapter(self, work: str, ch: int, text: str, tag: str = "") -> Path:
        """追加到章节文件（不存在则创建），后台分节写作逐节落盘用。"""
        title = _safe_title(tag, max_len=20)
        filename = f"ch{ch:02d}_{title}.txt" if title else f"ch{ch:02d}.txt"
        path = self.chapters_dir(work) / filename
        with _LOCK:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            merged = (existing.rstrip() + "\n\n" + text) if existing.strip() else text
            path.write_text(merged, encoding="utf-8")
        return path

    def render_story_memory(self, work: str, summary_limit: int = 300) -> str:
        """拼接当前作品的摘要流水 + 设定书快照，用于写新章时注入。"""
        items = self.load_chapter_summaries(work)
        lines = [f"【以下记忆仅属于《{work}》，禁止引入其他作品内容】"]
        if items:
            lines.append("——逐章摘要——")
            for it in items:
                s = str(it.get("summary", ""))
                if len(s) > summary_limit:
                    s = s[:summary_limit] + "…"
                lines.append(f"[第{it.get('ch')}章] {s}")
        else:
            lines.append("（还没有章节摘要）")
        state = self.load_story_state(work)
        state_lines = self._render_state(state)
        if state_lines:
            lines.append("——设定书（当前状态快照）——")
            lines.extend(state_lines)
        return "\n".join(lines)

    @staticmethod
    def _render_state(state: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        meta = state.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("title"):
            lines.append(f"标题：{meta['title']}｜当前进度：第{meta.get('current_chapter', 0)}章")
        world = state.get("world") or {}
        if isinstance(world, dict):
            for k, v in world.items():
                lines.append(f"世界观·{k}：{v}")
        chars = state.get("characters") or []
        if isinstance(chars, list):
            for c in chars:
                if isinstance(c, dict):
                    parts = [str(c.get(k, "")) for k in ("name", "状态", "关系", "秘密") if c.get(k)]
                    if parts:
                        lines.append("角色：" + "｜".join(parts))
        fsh = state.get("foreshadowing") or []
        if isinstance(fsh, list):
            for f in fsh:
                if isinstance(f, dict) and f.get("desc"):
                    lines.append(f"伏笔（{f.get('status', '未收')}）：{f['desc']}")
        un = state.get("unresolved") or []
        if isinstance(un, list):
            for u in un:
                lines.append(f"未解决：{u}")
        return lines

    # ── 历史管理 ──────────────────────────────
    def load_history(self, max_items: int = 40) -> list[dict[str, str]]:
        if not self.history_file.exists():
            return []
        items: list[dict[str, str]] = []
        try:
            for line in self.history_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "role" in obj and "content" in obj:
                        items.append({"role": str(obj["role"]), "content": str(obj["content"])})
                except Exception:
                    continue
        except Exception:
            return []
        return items[-max_items:]

    def append_history(self, role: str, content: str) -> None:
        with _LOCK:
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n")

    def clear_history(self) -> None:
        with _LOCK:
            self.history_file.write_text("", encoding="utf-8")

    # ── 落盘 ──────────────────────────────────
    def save_output(self, text: str, tag: str = "") -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        title = _safe_title(tag)
        filename = f"{ts}_{title}.txt" if title else f"{ts}.txt"
        path = self.outputs_dir / filename
        path.write_text(text, encoding="utf-8")
        return path

    def append_output(self, text: str, tag: str = "") -> Path:
        """追加到 outputs 文件（不存在则创建），后台分节写作逐节落盘用。"""
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        title = _safe_title(tag)
        filename = f"{ts}_{title}.txt" if title else f"{ts}.txt"
        path = self.outputs_dir / filename
        with _LOCK:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            merged = (existing.rstrip() + "\n\n" + text) if existing.strip() else text
            path.write_text(merged, encoding="utf-8")
        return path

    def latest_output_path(self) -> Optional[Path]:
        files = sorted(self.outputs_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None

    # ── 素材库 ────────────────────────────────
    def library_texts(self) -> list[str]:
        return self._read_texts(self.library_dir)

    def favorite_texts(self) -> list[str]:
        return self._read_texts(self.favorites_dir)

    def library_sources(self) -> list[tuple[str, str]]:
        return self._read_sources(self.library_dir)

    def favorite_sources(self) -> list[tuple[str, str]]:
        return self._read_sources(self.favorites_dir)

    @staticmethod
    def _read_sources(directory: Path) -> list[tuple[str, str]]:
        """读取目录下所有 txt/md 素材，返回 [(文件名, 内容)]，供逐篇学习。"""
        sources: list[tuple[str, str]] = []
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    sources.append((p.name, content))
            except Exception:
                continue
        return sources

    @staticmethod
    def _read_texts(directory: Path) -> list[str]:
        texts: list[str] = []
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    texts.append(content)
            except Exception:
                continue
        return texts

    def save_favorite(self, text: str, tag: str = "") -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        title = _safe_title(tag)
        filename = f"{ts}_{title}.txt" if title else f"{ts}.txt"
        path = self.favorites_dir / filename
        path.write_text(text, encoding="utf-8")
        return path

    # ── 约束辅助 ──────────────────────────────
    def set_constraint(self, key: str, value: str) -> tuple[bool, str]:
        if key not in self.constraints:
            return False, f"未知约束项 {key}，可用项：{', '.join(self.constraints.keys())}"
        if key == "banned_words":
            words = [w.strip() for w in re.split(r"[,，、\s]+", value) if w.strip()]
            self.constraints[key] = words
        elif key in ("length", "perspective", "scale", "description_focus", "rhythm"):
            self.constraints[key] = value.strip()
        elif key == "keep_persona":
            self.constraints[key] = value.strip().lower() in ("true", "1", "是", "开", "yes", "保留")
        else:
            return False, f"未知约束项 {key}"
        self.save_constraints()
        return True, f"已更新「{key}」→ {self.constraints[key]}"

    def render_constraints(self) -> str:
        lines = []
        for k, v in self.constraints.items():
            if isinstance(v, list):
                v = "、".join(v) if v else "无"
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def render_style_card(self) -> str:
        if self.combo.get("cards"):
            return self.render_combo_card()
        card = self.style_card
        if not card:
            return "（尚未学习风格，使用默认风格）"
        core = card.get("core") if isinstance(card.get("core"), dict) else {}
        has_core = any(isinstance(v, dict) and v.get("desc") for v in core.values())
        samples = card.get("samples") or []
        extras = card.get("extras") or []
        if not has_core and not samples and not extras:
            return "（尚未学习风格，使用默认风格）"
        lines: list[str] = []
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        if meta.get("source_count"):
            lines.append(
                f"（学习自 {meta.get('source_count')} 份素材"
                + (f"，收藏占比 {meta.get('favorite_ratio')}" if meta.get("favorite_ratio") else "")
                + "）"
            )
        core = card.get("core") if isinstance(card.get("core"), dict) else {}
        labels = {
            "sentence_style": "句式结构",
            "vocab_prefs": "词汇偏好",
            "description_focus": "描写侧重与密度",
            "person_habit": "人称与视角",
            "rhythm": "节奏",
            "sense_focus": "感官侧重",
            "scale_floor": "尺度与露骨度",
            "metaphor_style": "比喻方式",
        }
        for k, cn in labels.items():
            v = core.get(k) if isinstance(core.get(k), dict) else None
            if not v or not v.get("desc"):
                continue
            strength = v.get("strength", "有")
            line = f"- {cn}[{strength}]: {v['desc']}"
            ex = v.get("examples") or []
            if ex:
                sample = str(ex[0])
                line += f"（例：{sample[:40]}{"…" if len(sample) > 40 else ""}）"
            lines.append(line)
        samples = card.get("samples") or []
        if samples:
            lines.append(f"- 高权重示例段 ×{len(samples)}")
        extras = card.get("extras") or []
        if extras:
            lines.append(f"- 单篇特色样本 ×{len(extras)}（按需调用）")
        return "\n".join(lines) if lines else "（风格卡为空）"

    # ── 口水词小哨兵 / 旧稿清理 ────────────────
    COMMON_FILLER_WORDS = (
        "轻轻", "仿佛", "缓缓", "似乎", "笑了笑", "微微", "一点", "有些",
        "像是", "终于", "慢慢", "眼神", "嘴角", "忍不住", "悄悄", "淡淡",
        "深深", "忽然", "默默", "静静", "渐渐", "隐隐", "细细", "柔和",
    )
    _FILLER_STOP = frozenset(
        "我们 他们 她们 这个 那个 一个 什么 没有 自己 知道 时候 已经 "
        "因为 所以 但是 可是 然后 这样 那样 起来 出来 过来 觉得 看到 "
        "听到 说道 只是 还是 就是 也是 不是 都是 现在 还是 一直 真的".split()
    )

    # 对白归一化时剥掉的装饰词/语气词（与旁白内置表分开，避免误伤语义）
    _DIALOG_STRIP_WORDS = (
        "轻轻", "仿佛", "缓缓", "似乎", "微微", "一点", "有些", "像是", "终于", "慢慢",
        "眼神", "嘴角", "忍不住", "悄悄", "淡淡", "深深", "忽然", "默默", "静静",
        "渐渐", "隐隐", "细细", "柔和", "叹了口气", "笑了笑", "愣了一下",
    )
    _DIALOG_TONES = "啊呀啦呢吧嘛哦唉嘿嗯唔哈噢哟诶"

    def collect_whitelist(self, work: str = "") -> set[str]:
        """收集全局卡 + 作品卡的人名/别名，作为哨兵白名单（永不参与检测/替换）。"""
        names: set[str] = set()

        def _add(raw: Any) -> None:
            s = re.sub(r"[（(].*?[)）]", "", str(raw or "")).strip()
            s = re.sub(r"[\s·・]+", "", s)
            if len(s) >= 2:
                names.add(s)

        def _scan_dir(d: Path) -> None:
            if not d.exists():
                return
            for p in d.glob("*.json"):
                try:
                    card = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
                _add(p.stem)
                _add(basic.get("name"))
                aliases = basic.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [aliases]
                for a in aliases:
                    _add(a)

        try:
            _scan_dir(self.characters_dir)
            if work:
                _scan_dir(self.work_characters_dir(work))
        except Exception:
            pass
        return names

    @staticmethod
    def _extract_dialogues(text: str) -> list[tuple[int, str]]:
        """抽取引号内对白：返回 [(段落号, 对白文本)]。"""
        out: list[tuple[int, str]] = []
        for i, line in enumerate([p for p in text.splitlines() if p.strip()], 1):
            for d in re.findall(r"[“\"]([^”\"]{2,})[”\"]", line):
                out.append((i, d))
        return out

    def _norm_dialogue(self, s: str, whitelist: set[str]) -> str:
        """对白归一化：人名打码 → 同义词统一 → 剥装饰词/语气词/标点。"""
        s = str(s or "").strip()
        for w in sorted(whitelist, key=len, reverse=True):
            if w:
                s = s.replace(w, "@N@")
        s = re.sub(r"(说道|开口道|开口|说|道)", "@S@", s)
        for f in self._DIALOG_STRIP_WORDS:
            s = s.replace(f, "")
        s = re.sub(f"[{self._DIALOG_TONES}]", "", s)
        s = re.sub(r"[\s\W_]+", "", s)
        return s

    def detect_repeated_words(self, text: str, top_n: int = 5, work: str = "") -> str:
        """口水词小哨兵 v2：对白看语义重复，旁白看高频次数，人名白名单保护。"""
        if not text or not text.strip():
            return ""
        paras = [p.strip() for p in text.splitlines() if p.strip()]
        full = "\n".join(paras)
        whitelist = self.collect_whitelist(work)

        # ① 对白：语义重复
        dialogues = self._extract_dialogues(text)
        dup_lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        norms = [self._norm_dialogue(d, whitelist) for _, d in dialogues]
        for a in range(len(dialogues)):
            for b in range(a + 1, len(dialogues)):
                ia, ra = dialogues[a]
                ib, rb = dialogues[b]
                if ia == ib:
                    continue
                na, nb = norms[a], norms[b]
                if len(na) < 2 or len(nb) < 2:
                    continue
                ratio = difflib.SequenceMatcher(None, na, nb).ratio()
                if ratio >= 0.88:
                    key = tuple(sorted([f"{ia}:{ra}", f"{ib}:{rb}"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    dup_lines.append(
                        f"· 第{ia}段「{ra}」≈ 第{ib}段「{rb}」（{round(ratio, 2)}）"
                    )

        # ② 旁白：高频词（排除对白、白名单、停用词）
        narr = full
        for _, d in dialogues:
            narr = narr.replace(d, " ")
        narr = re.sub(r"[“”\"\s]+", "", narr)
        counts: dict[str, int] = {}
        positions: dict[str, list[int]] = {}
        for w in self.COMMON_FILLER_WORDS:
            if w in narr:
                cnt = narr.count(w)
                if cnt >= 3:
                    counts[w] = cnt
        auto: dict[str, int] = {}
        for m in re.findall(r"[\u4e00-\u9fff]{2,4}", narr):
            auto[m] = auto.get(m, 0) + 1
        for w, cnt in auto.items():
            if cnt >= 5 and w not in counts and w not in self._FILLER_STOP:
                counts[w] = cnt
        for w in list(counts.keys()):
            if w in whitelist or any(n in w or w in n for n in whitelist):
                del counts[w]
                continue
            positions[w] = [i + 1 for i, p in enumerate(paras) if w in p]

        if not dup_lines and not counts:
            return ""
        lines: list[str] = ["【口水词小哨兵】整章检查（对白看语义 / 旁白看频次）："]
        if dup_lines:
            lines.append(f"· 对白语义重复 {len(dup_lines)} 处：")
            lines.extend(dup_lines[:top_n])
        if counts:
            lines.append("· 旁白高频词：")
            ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]
            for w, cnt in ranked:
                pos = "、".join(str(p) for p in positions[w][:5])
                lines.append(f"  「{w}」×{cnt}（第 {pos} 段）")
        return "\n".join(lines)

    def list_stale_files(self, days: int = 30) -> list[dict[str, Any]]:
        """列出 outputs/ 和各作品 chapters/ 下超过 days 天未修改的旧文件。"""
        cutoff = time.time() - max(1, days) * 86400
        results: list[dict[str, Any]] = []

        def _scan(root: Path, label: str) -> None:
            if not root.exists():
                return
            for p in root.rglob("*.txt"):
                try:
                    st = p.stat()
                    if st.st_mtime < cutoff:
                        results.append({
                            "path": str(p),
                            "name": p.name,
                            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                            "size": st.st_size,
                            "label": label,
                        })
                except Exception:
                    continue

        _scan(self.outputs_dir, "短篇输出")
        if self.stories_dir.exists():
            for wd in self.stories_dir.iterdir():
                if wd.is_dir():
                    _scan(wd / CHAPTERS_DIR_NAME, f"作品《{wd.name}》章节")
        results.sort(key=lambda r: r["mtime"], reverse=True)
        return results
