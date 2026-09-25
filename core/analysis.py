"""写作沙盒 · analysis 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any


class AnalysisMixin:
    """analysis 域方法。"""

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

    def analyze_repeated(self, text: str, work: str = "") -> dict:
        """口水词小哨兵 v2 结构化版：返回 {dup: [...], freq: [...]}，供报告与自动修订复用。"""
        if not text or not text.strip():
            return {"dup": [], "freq": []}
        paras = [p.strip() for p in text.splitlines() if p.strip()]
        full = "\n".join(paras)
        whitelist = self.collect_whitelist(work)

        # ① 对白：语义重复
        dialogues = self._extract_dialogues(text)
        dup: list[dict] = []
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
                    dup.append({
                        "a_no": ia, "a": ra, "b_no": ib, "b": rb,
                        "ratio": round(ratio, 2),
                    })

        # ② 旁白：高频词（排除对白、白名单、停用词）
        narr = full
        for _, d in dialogues:
            narr = narr.replace(d, " ")
        narr = re.sub(r"[“”\"\s]+", "", narr)
        counts: dict[str, int] = {}
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
        freq: list[dict] = []
        for w in list(counts.keys()):
            if w in whitelist or any(n in w or w in n for n in whitelist):
                continue
            paras_no = [i + 1 for i, p in enumerate(paras) if w in p]
            freq.append({"word": w, "count": counts[w], "paras": paras_no})
        freq.sort(key=lambda kv: -kv["count"])
        return {"dup": dup, "freq": freq}

    def detect_repeated_words(self, text: str, top_n: int = 5, work: str = "") -> str:
        """口水词小哨兵 v2：对白看语义重复，旁白看高频次数，人名白名单保护。"""
        res = self.analyze_repeated(text, work)
        dup_lines = [
            f"· 第{d['a_no']}段「{d['a']}」≈ 第{d['b_no']}段「{d['b']}」（{d['ratio']}）"
            for d in res["dup"]
        ]
        counts = {f["word"]: f["count"] for f in res["freq"]}
        positions = {f["word"]: f["paras"] for f in res["freq"]}
        if not dup_lines and not counts:
            return ""
        lines: list[str] = ["【口水词小哨兵】整章检查（对白看语义 / 旁白看频次）："]
        if dup_lines:
            lines.append(f"· 对白语义重复 {len(dup_lines)} 处：")
            lines.extend(dup_lines[:top_n])
        if counts:
            lines.append("· 旁白高频词：")
            for w, cnt in sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]:
                pos = "、".join(str(p) for p in positions[w][:5])
                lines.append(f"  「{w}」×{cnt}（第 {pos} 段）")
        return "\n".join(lines)
