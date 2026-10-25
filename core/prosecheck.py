"""完稿文本检查纯函数（P2）：代词密度、段尾升华检测。

不依赖 astrbot。原则：高精度低召回——宁可漏报，绝不误伤正常叙述。
"""
from __future__ import annotations

import re
from typing import Any

_SENT_SPLIT = re.compile(r"[。！？…]+")
# 段尾升华模式：只匹配「段末句」且整句 ≤60 字（长句多为正常叙述）
_SUBLIMATION_PATTERNS = (
    re.compile(r"(?:仿佛|宛如)(?:整个|一切|世界)"),
    re.compile(r"世界(?:都|仿佛|好像)"),
    re.compile(r"(?:这就|这便)是.{0,6}(?:答案|成长|命运|爱情|结局)"),
    re.compile(r"命运的(?:齿轮|安排|捉弄)"),
    re.compile(r"(?:无声|安静)地?(?:守护|守望|陪伴)着"),
    re.compile(r"照亮了(?:整个|她|他)"),
    re.compile(r"(?:刻进|写进|藏进)(?:了)?(?:她|他|心底|骨子里)"),
    re.compile(r"成了(?:她|他)(?:生命|岁月|记忆)里"),
    re.compile(r"(?:或许|也许|大概)，?这就"),
    re.compile(r"温柔了?(?:了)?(?:整个|岁月|时光)"),
)


def pronoun_stats(text: str) -> dict[str, Any]:
    """人称代词密度（次/千字）：他/她/你 都计。advisory 指标，不自动改。"""
    clean = re.sub(r"\s+", "", text or "")
    total = len(clean)
    if total < 100:
        return {"total_chars": total, "he": 0, "she": 0, "you": 0, "per_kilo": 0.0}
    he = clean.count("他")
    she = clean.count("她")
    you = clean.count("你")
    return {"total_chars": total, "he": he, "she": she, "you": you,
            "per_kilo": round((he + she + you) * 1000 / total, 1)}


def detect_sublimation(text: str) -> list[dict[str, Any]]:
    """检测段尾升华：段末句命中 _SUBLIMATION_PATTERNS 且句长 ≤60 字。
    返回 [{no(段号), tail(末句 ≤50 字), pattern}]。"""
    hits: list[dict[str, Any]] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]
    for i, p in enumerate(paras, 1):
        sents = [s for s in _SENT_SPLIT.split(p) if s.strip()]
        if not sents:
            continue
        tail = sents[-1].strip()
        if len(tail) > 60:
            continue
        for pat in _SUBLIMATION_PATTERNS:
            if pat.search(tail):
                hits.append({"no": i, "tail": tail[:50], "pattern": pat.pattern})
                break
    return hits


def check_prose(text: str) -> dict[str, Any]:
    """完稿检查汇总：{"pronoun": stats, "sublimation": hits}。"""
    return {"pronoun": pronoun_stats(text), "sublimation": detect_sublimation(text)}
