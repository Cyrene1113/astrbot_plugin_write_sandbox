"""大纲分批收单的纯函数（P0-T5）：合并批次、查缺章。不依赖 astrbot。"""
from __future__ import annotations

from typing import Any


def merge_outline_batch(current: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    """把一批 {meta?, chapters?} 合并进当前草稿：
    meta 浅合并（批里出现的键覆盖）；chapters 按 ch 号 upsert（同号替换、新号追加后排序）。
    返回新 dict，不改入参。"""
    merged: dict[str, Any] = {
        "meta": dict(current.get("meta") or {}),
        "chapters": [dict(c) for c in (current.get("chapters") or []) if isinstance(c, dict)],
    }
    b_meta = batch.get("meta")
    if isinstance(b_meta, dict):
        merged["meta"].update(b_meta)
    b_chs = batch.get("chapters")
    if isinstance(b_chs, list):
        by_no = {int(c["ch"]): c for c in merged["chapters"]
                 if isinstance(c, dict) and isinstance(c.get("ch"), int)}
        for c in b_chs:
            if isinstance(c, dict) and isinstance(c.get("ch"), int):
                by_no[int(c["ch"])] = c
        merged["chapters"] = [by_no[k] for k in sorted(by_no)]
    return merged


def missing_chapters(outline: dict[str, Any]) -> list[int]:
    """在已有 1..max 章范围内找出缺失的章号；无章节返回空。"""
    chs = [int(c["ch"]) for c in (outline.get("chapters") or [])
           if isinstance(c, dict) and isinstance(c.get("ch"), int)]
    if not chs:
        return []
    want = set(range(1, max(chs) + 1))
    return sorted(want - set(chs))
