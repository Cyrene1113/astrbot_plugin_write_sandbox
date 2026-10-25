"""写作沙盒 · works 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .constants import ARC_SUMMARIES_FILE, CHAPTERS_DIR_NAME, CHAPTER_SUMMARIES_FILE, DEFAULT_STORY_CONFIG, DEFAULT_STORY_STATE, DRAFTS_DIR_NAME, OUTLINE_FILE, PENDING_OUTLINE_FILE, STORY_CONFIG_FILE, STORY_CONSTRAINTS_FILE, STORY_STATE_FILE, _LOCK, _safe_title
from .inject import pick_to_archive


class WorksMixin:
    """works 域方法。"""

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
        # 切换作品后刷新风格作用域：作品有自己的卡则用作品，否则回退全局
        self._refresh_style_scope(work)
        return work

    def clear_active_work(self) -> None:
        self.active_work = None
        with _LOCK:
            self.active_work_file.write_text(
                json.dumps({"work": ""}, ensure_ascii=False), encoding="utf-8"
            )
        self._refresh_style_scope()

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

    def load_work_constraints(self, work: str) -> dict[str, Any]:
        """作品级约束（仅存用户显式设置的键；空/缺省回退全局）。"""
        f = self.work_dir(work) / STORY_CONSTRAINTS_FILE
        if not f.exists():
            return {}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_work_constraints(self, work: str, patch: dict[str, Any]) -> None:
        """合并写入作品级约束：值为空串/空列表/None 的键删除（回退全局）。"""
        cur = self.load_work_constraints(work)
        for k, v in (patch or {}).items():
            if v is None or v == "" or v == []:
                cur.pop(k, None)
            else:
                cur[k] = v
        with _LOCK:
            (self.work_dir(work) / STORY_CONSTRAINTS_FILE).write_text(
                json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    def render_layered_constraints(self, work: str = "") -> str:
        """章节指令（另行注入）> 作品级 > 全局 的合并渲染。"""
        from .constraintops import overlay_constraints, render_layered
        work_c = self.load_work_constraints(work) if work else {}
        return render_layered(overlay_constraints(self.constraints, work_c))

    # ── 场景 beat（P3）──────────────────────────
    def get_chapter_beats(self, work: str, ch: int) -> list[dict[str, Any]]:
        """读大纲章节节点的 beats；无返回空列表。"""
        outline = self.load_outline(work)
        cur = next((it for it in (outline.get("chapters") or [])
                    if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        beats = (cur or {}).get("beats")
        return beats if isinstance(beats, list) else []

    def set_chapter_beats(self, work: str, ch: int, beats: list[dict[str, Any]]) -> None:
        """把 beat 细纲写进大纲章节节点（覆盖）；节点不存在时创建最小节点——
        写「大纲没有的章号」时 beats 也能落盘，断点续跑才能复读同一份细纲。"""
        outline = self.load_outline(work)
        for it in outline.get("chapters") or []:
            if isinstance(it, dict) and int(it.get("ch", 0)) == ch:
                it["beats"] = beats
                self.save_outline(work, outline)
                return
        outline.setdefault("chapters", []).append({"ch": ch, "beats": beats})
        outline["chapters"].sort(key=lambda it: int(it.get("ch", 0)) if isinstance(it, dict) else 0)
        self.save_outline(work, outline)

    def chapter_character_names(self, work: str, ch: int) -> list[str]:
        """本章大纲出场角色名列表（focus_chars 用）。"""
        outline = self.load_outline(work)
        cur = next((it for it in (outline.get("chapters") or [])
                    if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        names = (cur or {}).get("characters")
        return [str(n).strip() for n in names if str(n).strip()] if isinstance(names, list) else []

    # ── 摘要分层（P3）──────────────────────────
    def load_arc_summaries(self, work: str) -> list[dict[str, Any]]:
        f = self.work_dir(work) / ARC_SUMMARIES_FILE
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_arc_summary(self, work: str, ch_start: int, ch_end: int, text: str) -> None:
        arcs = self.load_arc_summaries(work)
        arcs = [a for a in arcs if not (a.get("ch_start") == ch_start and a.get("ch_end") == ch_end)]
        arcs.append({"ch_start": ch_start, "ch_end": ch_end, "text": text})
        arcs.sort(key=lambda a: int(a.get("ch_start", 0)))
        with _LOCK:
            (self.work_dir(work) / ARC_SUMMARIES_FILE).write_text(
                json.dumps(arcs, ensure_ascii=False, indent=2), encoding="utf-8")

    def pick_to_archive(self, work: str, keep: int = 5, threshold: int = 8) -> list[int]:
        items = self.load_chapter_summaries(work)
        unarch = [int(it.get("ch", 0)) for it in items if not it.get("archived")]
        return pick_to_archive(unarch, keep, threshold)

    def mark_summaries_archived(self, work: str, chs: list[int]) -> None:
        items = self.load_chapter_summaries(work)
        hit = {int(c) for c in chs}
        for it in items:
            if int(it.get("ch", 0)) in hit:
                it["archived"] = True
        with _LOCK:
            (self.work_dir(work) / CHAPTER_SUMMARIES_FILE).write_text(
                json.dumps({"summaries": items}, ensure_ascii=False, indent=2), encoding="utf-8")

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

    def _max_chapter_no(self, work: str) -> int:
        """正式章节文件与摘要并集的当前最大章号；两边都空返回 0。"""
        mx = 0
        d = self.chapters_dir(work)
        if d.exists():
            for p in d.glob("ch*.txt"):
                m = re.match(r"ch(\d+)", p.name)
                if m:
                    mx = max(mx, int(m.group(1)))
        for it in self.load_chapter_summaries(work):
            try:
                mx = max(mx, int(it.get("ch", 0)))
            except Exception:
                continue
        return mx

    def next_chapter_no(self, work: str) -> int:
        return self._max_chapter_no(work) + 1

    def latest_chapter_no(self, work: str) -> int:
        return self._max_chapter_no(work)

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

    def drafts_dir(self, work: str) -> Path:
        """作品草稿独立分区：写作期间先落这里，不占正式章节号。"""
        d = self.work_dir(work) / DRAFTS_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def append_draft(self, work: str, draft_id: str, text: str, tag: str = "") -> Path:
        """追加到作品草稿暂存文件（drafts/ 下，按 draft_id 区分），逐节落盘用。"""
        title = _safe_title(tag, max_len=20)
        filename = f"{draft_id}_{title}.txt" if title else f"{draft_id}.txt"
        path = self.drafts_dir(work) / filename
        with _LOCK:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            merged = (existing.rstrip() + "\n\n" + text) if existing.strip() else text
            path.write_text(merged, encoding="utf-8")
        return path

    def read_draft(self, work: str, draft_id: str) -> str:
        """读取草稿全文；草稿不存在返回空串。"""
        d = self.drafts_dir(work)
        files = sorted(d.glob(f"{draft_id}_*.txt")) + sorted(d.glob(f"{draft_id}.txt"))
        if not files:
            return ""
        try:
            return files[0].read_text(encoding="utf-8")
        except Exception:
            return ""

    def delete_draft(self, work: str, draft_id: str) -> None:
        """删除草稿暂存文件。"""
        d = self.drafts_dir(work)
        for p in list(d.glob(f"{draft_id}_*.txt")) + list(d.glob(f"{draft_id}.txt")):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    def finalize_chapter(self, work: str, draft_id: str, ch: int, tag: str = "",
                         append_mode: bool = False) -> Optional[Path]:
        """写作全部完成后：读取草稿全文 → 按目标章号命名写入正式章节目录 → 删草稿。

        rewrite 场景：目标 ch 已存在则整体覆盖该章（清掉旧文件），不新增章节号。
        continue 场景（append_mode=True）：目标 ch 已存在则把草稿追加到旧文末尾，保留旧内容。
        """
        text = self.read_draft(work, draft_id)
        if not text.strip():
            return None
        title = _safe_title(tag, max_len=20)
        filename = f"ch{ch:02d}_{title}.txt" if title else f"ch{ch:02d}.txt"
        d = self.chapters_dir(work)
        path = d / filename
        with _LOCK:
            if append_mode and path.exists():
                # 续写：保留旧内容，草稿追加到末尾
                old = path.read_text(encoding="utf-8").rstrip()
                merged = (old + "\n\n" + text) if old else text
                path.write_text(merged.rstrip() + "\n", encoding="utf-8")
            else:
                # 覆盖该章所有旧版本文件，保证同章唯一
                for p in list(d.glob(f"ch{ch:02d}_*.txt")) + list(d.glob(f"ch{ch:02d}.txt")):
                    try:
                        if p.exists():
                            p.unlink()
                    except Exception:
                        pass
                path.write_text(text.rstrip() + "\n", encoding="utf-8")
        self.delete_draft(work, draft_id)
        return path

    def render_story_memory(self, work: str, summary_limit: int = 300,
                            focus_chars: Optional[list[str]] = None) -> str:
        """拼接作品记忆：弧线摘要（已归档）+ 近章全量摘要（未归档）+ 设定书快照。
        focus_chars 非空时设定书 characters 只详列焦点角色，其余一行带过；world 值截断 200 字。"""
        items = self.load_chapter_summaries(work)
        lines = [f"【以下记忆仅属于《{work}》，禁止引入其他作品内容】"]
        arcs = self.load_arc_summaries(work)
        if arcs:
            lines.append("——弧线摘要（早期章节归档）——")
            for a in arcs:
                lines.append(f"[第{a.get('ch_start')}~{a.get('ch_end')}章] {a.get('text', '')}")
        unarch = [it for it in items if not it.get("archived")]
        if unarch:
            lines.append("——近章摘要——")
            for it in unarch:
                s = str(it.get("summary", ""))
                if len(s) > summary_limit:
                    s = s[:summary_limit] + "…"
                lines.append(f"[第{it.get('ch')}章] {s}")
        elif not arcs:
            lines.append("（还没有章节摘要）")
        state = self.load_story_state(work)
        state_lines = self._render_state(state, focus_chars)
        if state_lines:
            lines.append("——设定书（当前状态快照）——")
            lines.extend(state_lines)
        return "\n".join(lines)

    def _render_state(self, state: dict[str, Any],
                      focus_chars: Optional[list[str]] = None) -> list[str]:
        lines: list[str] = []
        meta = state.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("title"):
            lines.append(f"标题：{meta['title']}｜当前进度：第{meta.get('current_chapter', 0)}章")
        world = state.get("world") or {}
        if isinstance(world, dict):
            for k, v in world.items():
                v = str(v)
                if len(v) > 200:
                    v = v[:200] + "…"
                lines.append(f"世界观·{k}：{v}")
        chars = state.get("characters") or []
        if isinstance(chars, list):
            focus = {str(f).strip() for f in (focus_chars or []) if str(f).strip()}
            for c in chars:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name") or "").strip()
                if focus and name and name not in focus \
                        and not any(f in name or name in f for f in focus):
                    lines.append(f"角色：{name}（非本章焦点，略）")
                    continue
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
