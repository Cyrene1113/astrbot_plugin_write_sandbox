"""写作沙盒 · Sandbox 门面（Mixin 组合，2026-09-21 拆分）。

对外接口与拆分前完全一致：方法名、属性、行为零变化。
"""
from __future__ import annotations

from .storage import StorageMixin
from .style import StyleMixin
from .works import WorksMixin
from .characters import CharactersMixin
from .library import LibraryMixin
from .analysis import AnalysisMixin
from .cleanup import CleanupMixin


class Sandbox(
    StorageMixin,
    StyleMixin,
    WorksMixin,
    CharactersMixin,
    LibraryMixin,
    AnalysisMixin,
    CleanupMixin,
):
    """单个用户的独立写作沙盒（多 Mixin 组合门面）。"""
