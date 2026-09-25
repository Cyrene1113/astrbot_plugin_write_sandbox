"""写作沙盒核心包（由原 core.py 拆分，2026-09-21）。

导出面与原 core.py 完全一致：Sandbox 与全部公共常量。
"""
from __future__ import annotations

from .constants import (
    DEFAULT_CONSTRAINTS,
    DEFAULT_STYLE_CARD,
    LIBRARY_DIR_NAME,
    CORE_DIMS,
    CORE_LABELS,
)
from .sandbox import Sandbox

__all__ = [
    "Sandbox",
    "DEFAULT_CONSTRAINTS",
    "DEFAULT_STYLE_CARD",
    "LIBRARY_DIR_NAME",
    "CORE_DIMS",
    "CORE_LABELS",
]
