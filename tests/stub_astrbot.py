"""astrbot 桩模块（离线 e2e 测试用）。

在 import handlers 之前调用 install_stub()，即可在本机（无 astrbot 包）导入
handlers 全套 Mixin 并驱动真实业务方法。仅用于测试，不进运行时。
"""
from __future__ import annotations

import logging
import sys
import types


def install_stub() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    mc_mod = types.ModuleType("astrbot.api.message_components")
    core_mod = types.ModuleType("astrbot.core")
    provider_mod = types.ModuleType("astrbot.core.provider")
    register_mod = types.ModuleType("astrbot.core.provider.register")

    api.logger = logging.getLogger("astrbot-stub")

    class AstrMessageEvent:  # 仅类型标注用
        pass

    class MessageChain:
        def __init__(self, chain):
            self.chain = chain

    def _llm_tool(name=None):
        def deco(fn):
            fn._llm_tool_name = name
            return fn
        return deco

    class _Filter:
        llm_tool = staticmethod(_llm_tool)

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter()

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    def _register(*_args, **_kwargs):
        def deco(cls):
            return cls
        return deco

    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = _register

    class Plain:
        def __init__(self, text=""):
            self.text = text

    mc_mod.Plain = Plain

    class _LLMTools:
        func_list = []

    register_mod.llm_tools = _LLMTools()

    astrbot.api = api
    astrbot.core = core_mod
    api.event = event_mod
    api.star = star_mod
    api.message_components = mc_mod
    core_mod.provider = provider_mod
    provider_mod.register = register_mod

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = mc_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.provider"] = provider_mod
    sys.modules["astrbot.core.provider.register"] = register_mod
