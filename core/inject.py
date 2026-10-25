"""注入装配与摘要分层纯函数（P3）。不依赖 astrbot。"""
from __future__ import annotations

from typing import Any

_CUT_MARK = "\n…（ injected 部分因预算截断 ）…"


def assemble_injection(parts: list[tuple[int, str, str]], budget: int) -> str:
    """按优先级装配注入文本。parts=[(priority, name, text)]，priority 数字越小越重要。
    budget≤0 → 全部拼接不截断。超限时从优先级最低的未处理项开始整项丢弃；
    若最低项是唯一项且仍超限则就地截断。返回拼好的文本（段间空行）。"""
    if budget is not None and budget <= 0:
        budget = 10 ** 9
    items = sorted((p, n, t or "") for p, n, t in parts if (t or "").strip())
    if not items:
        return ""
    kept: list[tuple[str, str]] = []
    used = 0
    for prio, name, text in items:
        need = len(text) + (2 if kept else 0)
        if used + need <= budget:
            kept.append((name, text))
            used += need
            continue
        if not kept:
            # 第一项就超预算：截断保底
            kept.append((name, text[: max(200, budget - 10)] + _CUT_MARK))
            used = budget
            continue
        # 超预算：此项丢弃；若它是剩余项里优先级最低的，标记截断事实
        rest = [(p, n) for p, n, _ in items if (n, _) not in [(k[0], k[1]) for k in kept]]
        # 简化：直接注明省略来源名
        dropped = [n for p, n, t in items if t.strip() and (n not in [k[0] for k in kept])]
        tail = f"\n…（因注入预算省略：{'、'.join(dict.fromkeys(dropped))}）…"
        base = "\n\n".join(t for _n, t in kept)
        return base + tail
    return "\n\n".join(t for _n, t in kept)


def pick_to_archive(chs: list[int], keep: int = 5, threshold: int = 8) -> list[int]:
    """未归档章号中，除最近 keep 章外全部归档；未超 threshold 返回空。"""
    uniq = sorted({int(c) for c in chs if int(c) > 0})
    if len(uniq) <= threshold:
        return []
    return uniq[:-keep]
