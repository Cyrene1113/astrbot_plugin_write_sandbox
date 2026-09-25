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
