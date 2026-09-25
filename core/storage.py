"""写作沙盒 · storage 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .constants import ACTIVE_WORK_FILE, CHARACTERS_DIR_NAME, COMBO_FILE, CONSTRAINTS_FILE, DEFAULT_CONSTRAINTS, DEFAULT_STYLE_CARD, FAVORITES_DIR_NAME, HISTORY_FILE, LIBRARY_DIR_NAME, OUTPUT_DIR_NAME, STORIES_DIR_NAME, STYLE_CARDS_DIR_NAME, STYLE_CARD_FILE, _LOCK, _safe_title


class StorageMixin:
    """storage 域方法。"""

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
