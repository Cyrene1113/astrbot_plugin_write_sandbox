"""写作沙盒 · 昔涟 —— 委托式独立写作沙盒插件。

用户用自然语言吩咐，主模型（昔涟）判断需要创作时调用本插件提供的
llm_tool，插件以独立沙盒上下文 + 风格卡 + 约束生成文字，自动落盘 txt，
并把结果返回给主模型转述。
"""
from __future__ import annotations

from astrbot.api.star import Star, register

from .handlers import (
    BaseMixin,
    CharacterMixin,
    LearnMixin,
    MiscMixin,
    OutlineMixin,
    WriteMixin,
)
from .handlers.base import PLUGIN_ID


@register(PLUGIN_ID, "昔涟", "✍️ 委托式独立写作沙盒：自然吩咐、自动落盘 txt、风格学习", "0.1.0")
class WriteSandboxPlugin(
    BaseMixin,
    WriteMixin,
    OutlineMixin,
    CharacterMixin,
    LearnMixin,
    MiscMixin,
    Star,
):
    """委托式独立写作沙盒插件。

    功能实现已按域拆入 handlers/ 包的 6 个 Mixin：
    base（基础设施）/ write（写作全链路）/ outline（大纲）/ character（人设卡）/
    learn（素材学习）/ misc（杂项薄壳工具）。本类仅做组合与注册。
    """

    pass
