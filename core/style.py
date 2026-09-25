"""写作沙盒 · style 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import json
from typing import Any, Optional

from .constants import CORE_DIMS, CORE_LABELS, _LOCK


class StyleMixin:
    """style 域方法。"""

    def list_style_cards(self, work: str = "") -> list[str]:
        """列出当前作用域 style_cards/ 目录下所有可点名的风格卡。

        work 传入时按该作品作用域解析（作品有自己的卡库则列作品，否则回退全局）。
        """
        _, cards_dir, _ = self._style_scope_files(work)
        if not cards_dir.exists():
            return []
        return sorted(p.stem for p in cards_dir.glob("*.json"))

    def load_style_card_by_name(self, name: str, work: str = "") -> Optional[dict]:
        """按卡名（不带 .json）加载一张风格卡，找不到返回 None。

        work 传入时按该作品作用域解析卡库。
        """
        name = name.strip()
        if not name:
            return None
        _, cards_dir, _ = self._style_scope_files(work)
        candidates = [p for p in cards_dir.glob("*.json") if p.stem == name]
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
        """设置风格组合：cards 为参与融合的底卡列表；dims 为维度级来源指定。

        作品激活时组合写入作品自己的 style_combo.json（作品独立组合），否则写全局。
        """
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
        _, _, combo_file = self._write_style_scope()
        combo_file.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            combo_file.write_text(
                json.dumps(self.combo, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._refresh_style_scope()
        return True, "风格组合已保存。"

    def clear_combo(self) -> None:
        self.combo = {"cards": [], "dims": {}}
        _, _, combo_file = self._write_style_scope()
        combo_file.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            combo_file.write_text(
                json.dumps(self.combo, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._refresh_style_scope()

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

    def _scope_style_card(self, work: str = "") -> dict:
        """按作用域读取主卡（作品有自己主卡读作品，否则全局）；读不到返回默认卡。"""
        card_file, _, _ = self._style_scope_files(work)
        if card_file.exists():
            try:
                data = json.loads(card_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return dict(DEFAULT_STYLE_CARD)

    def _scope_combo(self, work: str = "") -> dict[str, Any]:
        """按作用域读取组合配置（作品有自己组合读作品，否则全局）；读不到返回空组合。"""
        _, _, combo_file = self._style_scope_files(work)
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
                    return {"cards": cards, "dims": dims}
            except Exception:
                pass
        return {"cards": [], "dims": {}}

    def render_combo_card(self, work: str = "") -> str:
        """组合渲染：每个维度列出其来源卡的笔法（默认全部底卡融合，dims 覆盖），供模型自行融合。

        work 传入时按该作品作用域解析卡库与组合（后台写作按任务作品取风格）。
        """
        combo = self._scope_combo(work)
        cards = combo.get("cards") or []
        dims = combo.get("dims") or {}
        if not cards:
            return "（未启用组合，使用主卡风格）"
        loaded: dict[str, dict] = {}
        for c in cards:
            card = self.load_style_card_by_name(c, work)
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

    def render_style_card(self, work: str = "") -> str:
        if self._scope_combo(work).get("cards"):
            return self.render_combo_card(work)
        card = self._scope_style_card(work)
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
