"""写作沙盒 · 常量与公共工具（拆分自 core.py，2026-09-21）。"""
from __future__ import annotations

import re
import threading
from typing import Any

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
DRAFTS_DIR_NAME = "drafts"
CHARACTERS_DIR_NAME = "characters"
CHARACTER_SCHEMA_VERSION = "2.0"
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
STORY_CONSTRAINTS_FILE = "story_constraints.json"

DEFAULT_STORY_CONFIG: dict[str, Any] = {
    "chapter_link": "auto",  # auto 自动 / continuous 连续叙事 / break 断章收尾
    # 写正文时的记忆注入开关（单元剧/独立短篇可关掉，避免被无关章节串扰）
    "inject_memory": True,  # 是否注入「前文摘要 + 设定书」
    "inject_characters": True,  # 是否注入「本章出场人设卡 + 全局卡选角」
    # 每章写完是否自动提炼人设卡（默认关：只给多章累计反复出现的角色建卡）
    "auto_extract": False,
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
