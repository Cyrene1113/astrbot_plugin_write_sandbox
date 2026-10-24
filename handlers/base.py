"""基础设施 Mixin：沙盒获取、LLM 调用、文本清理、续写上下文、忌讳词过滤。"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..core import Sandbox

PLUGIN_ID = "astrbot_plugin_write_sandbox"


class BaseMixin:
    """所有功能域 Mixin 的基座：实例状态 + 跨域共享的基础设施方法。"""

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config = config or {}
        data_root = Path(os.environ.get("ASTRBOT_DATA_DIR", "data"))
        self._plugin_data_dir = data_root / "plugin_data" / PLUGIN_ID
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self._sandboxes: dict[str, Sandbox] = {}
        self._MAX_RETURN_CHARS = 1500  # 回复预览上限，全文仍完整落盘
        # ── 后台写作任务 ──────────────────────────
        self._jobs: dict[str, dict] = {}   # uid -> 任务状态
        self._job_seq = 0
        self._SECTION_TARGET_CHARS = 4000  # 每节目标字数
        self._MAX_SECTIONS = 30            # 单次任务最多分节数
        self._SECTION_RETRY = 2            # 单节失败额外重试次数
        self._SECTION_LENGTH_EXTRA = 3     # 单节字数不足时自动续写补足的轮数上限
        self._bind_own_llm_tools()

    def _bind_own_llm_tools(self) -> None:
        """把本插件注册的 LLM 工具绑定到当前插件实例上。

        框架的绑定循环要求 handler.__module__ 等于插件主模块路径；
        工具拆分到 handlers/ 子模块后路径不匹配，导致实例绑定失败，
        调用时 event 被当成 self 吞掉而报 missing 'event'。
        这里在 __init__ 里手动补齐：把 handler 包成 partial(raw, self)
        并设置 handler_module_path。框架绑定循环遇到「partial 且路径
        匹配」时会把 partial 解包重绑到 metadata.star_cls（同一个实例），
        幂等安全；不经过框架路径时 partial 本身也已绑好实例。
        """
        try:
            import functools

            from astrbot.core.provider.register import llm_tools

            plugin_root = f"data.plugins.{PLUGIN_ID}"
            main_path = f"{plugin_root}.main"
            bound = 0
            logger.info(f"[沙盒诊断] 全局工具数={len(llm_tools.func_list)} root={plugin_root}")
            for func_tool in llm_tools.func_list:
                handler = getattr(func_tool, "handler", None)
                hmod = getattr(handler, "__module__", "") if handler else ""
                hmp = getattr(func_tool, "handler_module_path", None)
                logger.info(
                    f"[沙盒诊断] tool={getattr(func_tool, 'name', '?')} "
                    f"handler={type(handler).__name__} hmod={hmod!r} hmp={hmp!r}"
                )
                if handler is None:
                    continue
                handler_module = getattr(handler, "__module__", "") or ""
                if not handler_module.startswith(plugin_root):
                    continue
                raw = handler.func if isinstance(handler, functools.partial) else handler
                func_tool.handler = functools.partial(raw, self)
                func_tool.handler_module_path = main_path
                bound += 1
            if bound:
                logger.info(f"写作沙盒已手动绑定 {bound} 个 LLM 工具到插件实例。")
            else:
                logger.warning("[沙盒诊断] 没有匹配到任何本插件工具！")
        except Exception as e:
            logger.error(f"写作沙盒工具绑定失败: {e}")

    # ── 沙盒获取 ──────────────────────────────
    def _get_sandbox(self, event: AstrMessageEvent) -> Optional[Sandbox]:
        uid = event.get_sender_id()
        if not uid:
            return None
        uid = str(uid)
        if uid not in self._sandboxes:
            self._sandboxes[uid] = Sandbox(self._plugin_data_dir / uid)
        return self._sandboxes[uid]

    # ── LLM 调用 ──────────────────────────────
    async def _llm_chat(self, prompt: str, system_prompt: str = "", timeout: float = 110.0) -> str:
        """向星星诉说，等待回音（带超时保护，防止模型卡死拖垮整个工具调用）"""
        provider = None
        try:
            provider = self.context.get_using_provider()
        except Exception:
            provider = None
        if not provider:
            return ""
        try:
            result = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt=system_prompt or None,
                    request_max_retries=2,
                ),
                timeout=timeout,
            )
            if hasattr(result, "completion_text"):
                return result.completion_text or ""
            if isinstance(result, str):
                return result
            return str(result)
        except asyncio.TimeoutError:
            logger.error(f"写作沙盒调用模型超时（>{timeout}s），已中止等待。")
            return ""
        except Exception as e:
            logger.error(f"写作沙盒调用模型失败: {e}")
            return ""

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip()
        # 去掉可能的 markdown 代码块围栏
        m = re.match(r"^```(?:txt|text|markdown|md)?\s*(.*?)\s*```$", text, re.S)
        if m:
            text = m.group(1).strip()
        # 剥离模型可能混入的非正文前缀（思考/说明/引导语）与独立括号注记行
        text = BaseMixin._strip_non_story(text)
        return text

    @staticmethod
    def _strip_non_story(text: str) -> str:
        """剥离模型输出开头可能混入的非正文内容：
        思考/说明前缀、引导语（如「以下为正文」）以及独立成行的括号注记。
        只处理前 8 行；剥离后若无剩余内容则保留原文，防止误删。"""
        lines = text.split("\n")
        cut = 0
        meta_kw = r"思考|思考过程|思路|心理活动|说明|注释|备注|写作说明"
        for i, ln in enumerate(lines[:8]):
            s = ln.strip()
            if not s:
                cut = i + 1
                continue
            # ① 关键词+冒号开头：思考：/说明：/注： 等
            if re.match(rf"^\s*(?:{meta_kw})[：:，,。\s]", s):
                cut = i + 1
                continue
            # ② 括号/方括号包裹的标记块：【思考过程】/（写作说明）等
            if re.match(rf"^[（(【\[]\s*(?:{meta_kw})\s*[）)】\]]", s):
                cut = i + 1
                continue
            # ③ 引导语整行：好的，以下为正文：/正文如下：/以下内容为正文
            if re.match(
                r"^\s*(?:好的|好|嗯|那么|ok|OK)?[，,:：\s]*(?:以下为正文|以下是正文|正文如下|正文开始|正文内容如下|以下内容为正文)[：:，,。\s]?\s*$",
                s,
            ):
                cut = i + 1
                continue
            # ④ 独立括号注记行：（注：xxx）（说明：xxx）
            if re.match(r"^[（(]\s*(?:注|说明|备注|提示|小记)[：:][^）)]{0,40}[）)]\s*$", s):
                cut = i + 1
                continue
            break
        if cut:
            rest = "\n".join(lines[cut:]).strip()
            if rest:
                return rest
        return text

    def _last_assistant_output(self, sandbox: Sandbox, max_chars: int = 800) -> str:
        """从沙盒历史往前找最近一条沙盒产出，避免末尾是用户指令时取空。"""
        for h in reversed(sandbox.load_history()):
            if h["role"] == "assistant":
                return h["content"][-max_chars:]
        return ""

    def _build_continue_context(self, sandbox: Sandbox, work: str, ch: int) -> str:
        """长文续写时按作品章节接续设置决定上下文：
        continuous 贴上一章结尾三行；break 只给本章氛围引子；auto 有引子则断章式、否则贴结尾。"""
        mode = sandbox.get_chapter_link_mode(work)
        prev_tail = sandbox.read_chapter_tail(work, ch - 1) if ch > 1 else ""
        mood = sandbox.render_chapter_mood(work, ch)
        if mode == "break":
            return mood or ""
        if mode == "continuous":
            return prev_tail or mood or self._last_assistant_output(sandbox)
        # auto：有灵感便签/结尾钩子就断章式，否则贴上一章结尾
        return mood or prev_tail or self._last_assistant_output(sandbox)

    def _apply_banned_words(self, text: str, sandbox: Sandbox) -> str:
        banned = sandbox.constraints.get("banned_words") or []
        for w in banned:
            if w:
                text = text.replace(w, "×" * len(w))
        return text



def _sample_source_text(text: str, limit: int = 4000) -> str:
    """素材超长时采样：保留开头 60% 与结尾 40%，中间省略，风格特征不丢。"""
    if len(text) <= limit:
        return text
    head_len = int(limit * 0.6)
    tail_len = limit - head_len
    return text[:head_len] + "\n……（中段省略，篇幅可控）……\n" + text[-tail_len:]


def _pack_sources(sources: list[tuple[str, str]], limit: int = 4000, max_count: int = 8) -> str:
    """把多篇素材打包成带文件名的文本：逐篇截断采样，防止整体超长。

    sources 为 [(文件名, 内容)] 列表。每篇以「【素材N·文件名】」开头，
    让模型能区分来源；超长篇目自动采样，避免把几十万字全塞进上下文。
    """
    parts: list[str] = []
    for i, (name, text) in enumerate(sources[:max_count], start=1):
        sampled = _sample_source_text(text, limit)
        parts.append(f"【素材{i}·{name}】\n{sampled}")
    return "\n\n".join(parts)
