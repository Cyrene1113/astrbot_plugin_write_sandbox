"""基础设施 Mixin：沙盒获取、LLM 调用、文本清理、续写上下文、忌讳词过滤。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..core import Sandbox
from ..core.llmerrors import LLMCallError, classify_llm_error

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
        # ── 引擎配置（_conf_schema.yaml 注入，config 缺失时用默认值） ──
        cfg = self.config or {}

        def _cfg_int(key: str, default: int) -> int:
            try:
                return int(cfg.get(key, default))
            except (TypeError, ValueError):
                return default

        self._SECTION_TARGET_CHARS = _cfg_int("section_target_chars", 2000)
        self._MAX_SECTIONS = _cfg_int("max_sections", 30)
        self._SECTION_RETRY = _cfg_int("section_retry", 2)
        self._SECTION_LENGTH_EXTRA = 3     # 单节字数不足时自动续写补足的轮数上限
        self._ENGINE = {
            "stream_first": str(cfg.get("stream_first", True)).lower() not in ("0", "false", "no", "关", "off"),
            "stream_idle_timeout": _cfg_int("stream_idle_timeout", 60),
            "llm_total_timeout": _cfg_int("llm_total_timeout", 300),
        }
        self._stream_ok_cache: dict[type, bool] = {}
        # ── 后台写作任务 ──────────────────────────
        self._jobs: dict[str, dict] = {}   # uid -> 任务状态
        self._job_seq = 0
        self._MAX_RETURN_CHARS = 1500  # 回复预览上限，全文仍完整落盘
        self._bind_own_llm_tools()
        self._scan_interrupted_jobs()   # T6：把重启前残留的 running 任务标记为 interrupted

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

    # ── 任务落盘（P0-T6：断点续跑的持久化侧） ──────────────
    def _jobs_file(self, uid: str) -> Path:
        return self._plugin_data_dir / uid / "jobs.json"

    def _scan_interrupted_jobs(self) -> None:
        """插件加载时把残留 running 任务标记为 interrupted（进程/插件重启所致）。"""
        if not self._plugin_data_dir.exists():
            return
        for d in self._plugin_data_dir.iterdir():
            if not d.is_dir():
                continue
            f = d / "jobs.json"
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("status") == "running":
                data["status"] = "interrupted"
                data["error"] = data.get("error") or "AstrBot 重启/插件重载导致任务中断"
                try:
                    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.info(f"[沙盒] 任务 {data.get('job_id')}（用户 {d.name}）已标记为 interrupted")
                except Exception:
                    pass

    def _load_persisted_job(self, uid: str) -> Optional[dict]:
        f = self._jobs_file(uid)
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # ── LLM 调用（P0 重构：流式优先 + 空闲超时 + 错误分类） ─────────────

    def _provider_stream_ok(self, provider) -> bool:
        """探测 provider.text_chat 是否支持 stream 参数（按 provider 类缓存）。"""
        cls = type(provider)
        if cls not in self._stream_ok_cache:
            try:
                import inspect
                ok = "stream" in inspect.signature(provider.text_chat).parameters
            except Exception:
                ok = False
            self._stream_ok_cache[cls] = ok
        return self._stream_ok_cache[cls]

    @staticmethod
    def _chunk_delta(chunk) -> str:
        """流式块 → 增量文本（只认 delta/delta_text 字段；completion_text 归 _chunk_full）。"""
        if isinstance(chunk, str):
            return chunk
        for attr in ("delta", "delta_text"):
            v = getattr(chunk, attr, None)
            if isinstance(v, str) and v:
                return v
        return ""

    @staticmethod
    def _chunk_full(chunk) -> str:
        """流式块 → 截至当前的完整文本（若实现携带的是累计值）。"""
        if isinstance(chunk, str):
            return ""
        v = getattr(chunk, "completion_text", None)
        return v if isinstance(v, str) else ""

    async def _llm_stream(self, provider, prompt: str, system_prompt: str,
                          idle: float, total: float) -> str:
        """流式消费：空闲超时 idle、总超时 total；只有正常推进就不掐。"""
        agen = provider.text_chat(
            prompt=prompt, system_prompt=system_prompt or None,
            stream=True, request_max_retries=1,
        )
        parts: list[str] = []           # 增量拼接（delta 语义）
        all_full_concat: list[str] = [] # completion_text 逐块拼接（两种语义都可能）
        last_full = ""
        got_any = False
        deadline = time.monotonic() + total
        while True:
            if time.monotonic() > deadline:
                raise LLMCallError(
                    LLMCallError.TIMEOUT,
                    f"流式总超时 >{total:.0f}s（已收约 {sum(len(p) for p in parts)} 字符后中止）",
                )
            try:
                nxt = await asyncio.wait_for(agen.__anext__(), timeout=idle)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise LLMCallError(
                    LLMCallError.TIMEOUT,
                    f"流式空闲超时 >{idle:.0f}s（已收约 {sum(len(p) for p in parts)} 字符后中止）",
                )
            except Exception as e:
                if not got_any:
                    # 首块前失败：标记 setup 失败，允许上层回退非流式
                    raise LLMCallError(LLMCallError.PROVIDER, f"stream_setup {type(e).__name__}: {e}")
                raise classify_llm_error(e)
            got_any = True
            piece = self._chunk_delta(nxt)
            if piece:
                parts.append(piece)
            full = self._chunk_full(nxt)
            if full:
                all_full_concat.append(full)
                if len(full) > len(last_full):
                    last_full = full
        # 兼容三种流式实现语义：delta 块 / 累计 completion_text / 每块自带一小段 completion_text
        delta_text = "".join(parts)
        concat_text = "".join(all_full_concat)
        if delta_text.strip():
            best = delta_text                      # delta 语义：增量拼接即全文
        elif len(all_full_concat) <= 1:
            best = last_full                        # 单块：末块即全文
        elif len(last_full) >= 0.8 * len(concat_text):
            best = last_full                        # 累计语义：末块≈全量
        else:
            best = concat_text                      # 每块一小段：拼接还原
        if not best.strip():
            raise LLMCallError(LLMCallError.EMPTY, "流式完成但未取到任何文本")
        return best

    async def _llm_nonstream(self, provider, prompt: str, system_prompt: str,
                             total: float) -> str:
        """非流式调用（回退路径 / stream_first=false）。"""
        try:
            result = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt=system_prompt or None,
                    request_max_retries=2,
                ),
                timeout=total,
            )
        except asyncio.TimeoutError:
            raise LLMCallError(LLMCallError.TIMEOUT, f"非流式总超时 >{total:.0f}s")
        except Exception as e:
            raise classify_llm_error(e)
        if hasattr(result, "completion_text"):
            return result.completion_text or ""
        if isinstance(result, str):
            return result
        return str(result)

    async def _llm_chat_ex(
        self, prompt: str, system_prompt: str = "", timeout: float = 0.0
    ) -> tuple[str, Optional[LLMCallError]]:
        """统一 LLM 调用入口：返回 (文本, 错误)，二者互斥，永不抛异常。
        timeout<=0 用引擎默认 llm_total_timeout。"""
        started = time.monotonic()
        try:
            provider = self.context.get_using_provider()
        except Exception as e:
            return "", LLMCallError(LLMCallError.PROVIDER, f"获取 provider 失败: {e}")
        if not provider:
            return "", LLMCallError(LLMCallError.PROVIDER, "没有可用的 provider")

        total = float(timeout) if timeout and timeout > 0 else float(self._ENGINE["llm_total_timeout"])
        text, err = "", None
        if self._ENGINE["stream_first"] and self._provider_stream_ok(provider):
            try:
                text = await self._llm_stream(
                    provider, prompt, system_prompt,
                    idle=float(self._ENGINE["stream_idle_timeout"]), total=total,
                )
            except LLMCallError as e:
                err, text = e, ""
                # 只有「首块前的 setup 失败」（如实现不支持流）才回退非流式；
                # 超时/限流不回退，避免同一次请求双倍计费
                if e.detail.startswith("stream_setup"):
                    logger.warning(f"[沙盒] 流式通路不可用（{e}），本进程内回退非流式")
                    self._stream_ok_cache[type(provider)] = False
                    try:
                        text = await self._llm_nonstream(provider, prompt, system_prompt, total)
                        err = None
                    except LLMCallError as e2:
                        err, text = e2, ""
        else:
            try:
                text = await self._llm_nonstream(provider, prompt, system_prompt, total)
            except LLMCallError as e:
                err, text = e, ""

        dur = time.monotonic() - started
        if err is None and not text.strip():
            err = LLMCallError(LLMCallError.EMPTY, f"耗时 {dur:.0f}s")
        if err is not None:
            logger.error(f"[沙盒] LLM 调用失败 dur={dur:.0f}s in={len(prompt)}chars :: {err}")
        else:
            logger.info(f"[沙盒] LLM 调用完成 dur={dur:.0f}s in={len(prompt)}chars out={len(text)}chars")
        return text, err

    async def _llm_chat(self, prompt: str, system_prompt: str = "", timeout: float = 0.0) -> str:
        """兼容壳：老调用点继续用。失败返回空串（错误详情见日志）。新代码一律用 _llm_chat_ex。"""
        text, err = await self._llm_chat_ex(prompt, system_prompt, timeout=timeout)
        return text if err is None else ""

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
