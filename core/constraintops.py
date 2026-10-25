"""约束三层合并纯函数（P2）：章节指令（调用方处理）> 作品级 > 全局。"""
from __future__ import annotations

from typing import Any

CONSTRAINT_KEYS = ("length", "perspective", "scale", "banned_words",
                   "description_focus", "rhythm", "keep_persona")


def overlay_constraints(global_c: dict[str, Any], work_c: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    """返回 {key: (生效值, 来源)}；来源 "work"/"global"。
    作品级仅在值非空时覆盖；banned_words 空列表视为未设置。"""
    out: dict[str, tuple[Any, str]] = {}
    for k in CONSTRAINT_KEYS:
        w = work_c.get(k) if isinstance(work_c, dict) else None
        g = global_c.get(k) if isinstance(global_c, dict) else None
        w_set = (w is not None and w != "" and w != []) if True else False
        if w_set:
            out[k] = (w, "work")
        else:
            out[k] = (g, "global")
    return out


def render_layered(overlay: dict[str, tuple[Any, str]]) -> str:
    """渲染合并约束，作品级带「（作品级）」标注。"""
    lines: list[str] = []
    for k, (v, src) in overlay.items():
        if isinstance(v, list):
            v = "、".join(str(x) for x in v) if v else "无"
        mark = "（作品级）" if src == "work" else ""
        lines.append(f"- {k}: {v}{mark}")
    return "\n".join(lines)
