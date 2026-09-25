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
        self.constraints_file = user_dir / CONSTRAINTS_FILE
        # 全局风格三件套（作品未调过卡时回退到这里，老作品行为不变）
        self._global_style_card_file = user_dir / STYLE_CARD_FILE
        self._global_style_cards_dir = user_dir / STYLE_CARDS_DIR_NAME
        self._global_combo_file = user_dir / COMBO_FILE
        # 当前风格作用域路径：随激活作品动态切换（_refresh_style_scope 后指向作品或全局）
        self.style_card_file = self._global_style_card_file
        self.style_cards_dir = self._global_style_cards_dir
        self.combo_file = self._global_combo_file
        self.active_work_file = user_dir / ACTIVE_WORK_FILE

        self.constraints: dict[str, Any] = dict(DEFAULT_CONSTRAINTS)
        self.style_card: dict[str, Any] = dict(DEFAULT_STYLE_CARD)
        self.combo: dict[str, Any] = {"cards": [], "dims": {}}
        self.active_work: Optional[str] = None
        self.style_cards_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()
        # 初始化后按当前激活作品刷新风格作用域（作品有自己的卡则切到作品，否则留在全局）
        self._refresh_style_scope()

    def _style_scope_files(self, work: str = "") -> tuple[Path, Path, Path]:
        """返回风格三件套（主卡/卡库目录/组合）读取作用域。

        作品激活且作品目录下已有任意风格文件 → 用作品自己的三件套（作品独立风格）；
        否则回退全局三件套（作品没调过卡，老行为不变）。
        """
        target = work or self.get_active_work() or ""
        if target:
            wd = self.work_dir(target)
            has_own = (
                (wd / STYLE_CARD_FILE).exists()
                or (wd / STYLE_CARDS_DIR_NAME).exists()
                or (wd / COMBO_FILE).exists()
            )
            if has_own:
                return (wd / STYLE_CARD_FILE, wd / STYLE_CARDS_DIR_NAME, wd / COMBO_FILE)
        return (self._global_style_card_file, self._global_style_cards_dir, self._global_combo_file)

    def _write_style_scope(self, work: str = "") -> tuple[Path, Path, Path]:
        """返回风格三件套写入作用域。

        作品激活时永远写入作品自己的目录（作品没卡也会自动建独立三件套，学习/组合落作品库）；
        未激活作品时写全局。
        """
        target = work or self.get_active_work() or ""
        if target:
            wd = self.work_dir(target)
            return (wd / STYLE_CARD_FILE, wd / STYLE_CARDS_DIR_NAME, wd / COMBO_FILE)
        return (self._global_style_card_file, self._global_style_cards_dir, self._global_combo_file)

    def _refresh_style_scope(self, work: str = "") -> None:
        """按当前激活作品刷新风格作用域路径与内存态。

        - 作品有自己风格 → 切到作品目录（主卡/组合也读作品那份）；
        - 作品没有 → 回到全局路径，并恢复全局内存态（老作品行为不变）。
        """
        card_file, cards_dir, combo_file = self._style_scope_files(work)
        self.style_card_file = card_file
        self.style_cards_dir = cards_dir
        self.combo_file = combo_file
        self.style_cards_dir.mkdir(parents=True, exist_ok=True)
        # 主卡内存态
        if card_file.exists():
            try:
                data = json.loads(card_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.style_card = data
            except Exception:
                pass
        else:
            self.style_card = dict(DEFAULT_STYLE_CARD)
        # 组合内存态
        if combo_file.exists():
            try:
                data = json.loads(combo_file.read_text(encoding="utf-8"))
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
        else:
            self.combo = {"cards": [], "dims": {}}

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
        card_file, _, _ = self._write_style_scope()
        card_file.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            card_file.write_text(
                json.dumps(self.style_card, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def save_style_card_to_library(self, name: str, card: dict) -> Path:
        """把一张风格卡保存到当前作用域的 style_cards/ 库存，可被 sandbox_style_combo 点名。

        作品激活时写入作品自己的卡库（学习素材自动落进作品库），否则写全局卡库。
        """
        _, cards_dir, _ = self._write_style_scope()
        cards_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_title(name, max_len=32) or "未命名风格"
        path = cards_dir / f"{safe}.json"
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
