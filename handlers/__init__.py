"""main.py 拆分出的 handlers 包：各功能域的 Mixin，由 main.py 的最终类多重继承组合。

依赖单向无环：handlers 各模块只依赖 core/prompts/base，模块间互不 import（character/learn 共用
base 里的模块级工具函数除外）；main.py 只 import handlers。
"""
from .base import BaseMixin, PLUGIN_ID
from .character import CharacterMixin
from .learn import LearnMixin
from .misc import MiscMixin
from .outline import OutlineMixin
from .write import WriteMixin

__all__ = [
    "BaseMixin",
    "CharacterMixin",
    "LearnMixin",
    "MiscMixin",
    "OutlineMixin",
    "WriteMixin",
    "PLUGIN_ID",
]
