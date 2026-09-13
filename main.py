"""写作沙盒 · 昔涟 —— 委托式独立写作沙盒插件。

用户用自然语言吩咐，主模型（昔涟）判断需要创作时调用本插件提供的
llm_tool，插件以独立沙盒上下文 + 风格卡 + 约束生成文字，自动落盘 txt，
并把结果返回给主模型转述。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

from .core import (
    Sandbox,
    LIBRARY_DIR_NAME,
    DEFAULT_CONSTRAINTS,
    DEFAULT_STYLE_CARD,
    CORE_DIMS,
    CORE_LABELS,
)
from .prompts import (
    build_system_prompt,
    build_learn_prompt,
    build_context_snippet,
    build_chapter_summary_prompt,
    build_story_state_prompt,
    build_outline_prompt,
    build_outline_revise_prompt,
    build_character_prompt,
    build_character_extract_prompt,
    build_global_card_select_prompt,
)

PLUGIN_ID = "astrbot_plugin_write_sandbox"


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


@register(PLUGIN_ID, "昔涟", "✍️ 委托式独立写作沙盒：自然吩咐、自动落盘 txt、风格学习", "0.1.0")
class WriteSandboxPlugin(Star):
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
    async def _llm_chat(self, prompt: str, system_prompt: str = "") -> str:
        """向星星诉说，等待回音"""
        provider = None
        try:
            provider = self.context.get_using_provider()
        except Exception:
            provider = None
        if not provider:
            return ""
        try:
            result = await provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt or None,
            )
            if hasattr(result, "completion_text"):
                return result.completion_text or ""
            if isinstance(result, str):
                return result
            return str(result)
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

    # ═══════════════════════════════════════════
    # 工具 1：沙盒写作（生成 / 续写 / 改写）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_write")
    async def sandbox_write(
        self,
        event: AstrMessageEvent,
        instruction: str,
        mode: str = "generate",
        tag: str = "",
        work: str = "",
    ) -> str:
        """在独立写作沙盒中后台分节生成文字，任务开始即返回，全部写完会自动把完整文件推给用户。

        当用户明确要求「写一段文字/故事/涩涩内容/情色描写/小说片段」，
        或要求「按我的素材风格写」「续写/改写上一段」时调用本工具。
        长篇模式：用户提到作品名（如「写《雾都夜行》第3章」）时把作品名传入 work，
        正文会存进该作品的章节目录，并自动生成章节摘要、更新设定书。
        长文会自动分成每节约 4000 字的小节在后台逐节写；用户中途问进度时调用 sandbox_progress 工具。

        Args:
            instruction(string): 用户想要的内容描述，例如「她推门进来，雨还没干」「尺度拉高，重点写触感」。
            mode(string): generate=新写一段（长篇模式下为新章节）；continue=接着上一段续写；rewrite=按新要求重写上一段。
            tag(string): 可选，给这段文字起个名字，会用在文件名上。
            work(string): 可选，长篇作品名。传入则切换/新建该作品工作区；留空沿用当前激活作品。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        uid = str(event.get_sender_id())

        # 作品工作区：传入作品名则切换，否则沿用当前激活作品
        active_work = ""
        if work and work.strip():
            active_work = sandbox.set_active_work(work.strip())
        else:
            active_work = sandbox.get_active_work() or ""

        # 长篇模式：有未确认的大纲草稿先拦下，避免「大纲没过目就直接开写」
        if active_work and sandbox.has_pending_outline(active_work):
            return (
                f"《{active_work}》还有一份大纲草稿没确认呢，"
                "先审核一下再写正文——确认后人家马上开工♪\n\n"
                + sandbox.render_pending_outline(active_work)
            )

        # 长篇模式：写正文前复核人物卡，发现旧卡残留/缺卡/无关信息就随启动消息提示
        char_audit_note = ""
        if active_work:
            try:
                rep = sandbox.render_character_audit(active_work)
                if "没有发现" not in rep:
                    char_audit_note = "\n\n【写前人物卡复核】\n" + rep + "\n（可以先让人家清理旧卡或补建缺卡，也可以直接开写♪）"
            except Exception:
                pass

        # 长篇模式下先确定章节号（generate=新章，continue/rewrite=续写最近一章）
        chapter_no = 0
        if active_work:
            if mode == "generate":
                chapter_no = sandbox.next_chapter_no(active_work)
            else:
                chapter_no = sandbox.latest_chapter_no(active_work) or 1

        # 已有进行中任务：说明情况而不是重复排队
        old = self._jobs.get(uid)
        if old and old.get("status") in ("pending", "running"):
            return (
                f"后台已经有一个写作任务（{old['job_id']}）在进行中啦，"
                "等它写完人家再开新的，或者先查一下进度♪"
            )

        self._job_seq += 1
        job = {
            "job_id": f"job{self._job_seq}",
            "uid": uid,
            "session": event.unified_msg_origin,
            "mode": mode,
            "instruction": instruction,
            "tag": tag,
            "work": active_work,
            "chapter_no": chapter_no,
            "sandbox": sandbox,
            "status": "pending",
            "section_idx": 0,
            "section_total": 0,
            "current_chunk": "",
            "file_path": "",
            "error": "",
            "started_at": time.time(),
            "finished_at": 0.0,
        }
        self._jobs[uid] = job
        job["task"] = asyncio.create_task(self._run_write_job(job))

        lines = [
            f"【已开始后台写作♪】任务 {job['job_id']}",
            f"模式：{mode}",
        ]
        if active_work:
            lines.append(f"作品：《{active_work}》第{chapter_no}章")
        lines.append("人家在后台一节一节写，全部写完会把完整文件推给你，中途想知道进度随时问人家♪")
        if char_audit_note:
            lines.append(char_audit_note)
        return "\n".join(lines)

    # ── 后台写作任务 ────────────────────────────
    def _render_job_status(self, job: dict) -> str:
        """渲染任务进度，供主模型/用户查看。"""
        lines = [f"【后台写作 {job.get('status')}】任务 {job.get('job_id')}"]
        if job.get("work"):
            lines.append(f"作品：《{job['work']}》第{job.get('chapter_no')}章")
        if job.get("section_total"):
            lines.append(f"进度：{job.get('section_idx')}/{job.get('section_total')} 节")
        else:
            lines.append("进度：正在规划节数…")
        if job.get("file_path"):
            lines.append(f"已落盘：{job['file_path']}")
        if job.get("error"):
            lines.append(f"错误：{job['error']}")
        return "\n".join(lines)

    _CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    _CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

    @staticmethod
    def _parse_cn_amount(s: str) -> Optional[int]:
        """把「六千」「一万二千」「3000」解析成整数；失败返回 None。"""
        if s.isdigit():
            return int(s)
        total, section, cur = 0, 0, 0
        for ch in s:
            if ch.isdigit():
                cur = int(ch)
            elif ch in WriteSandboxPlugin._CN_DIGITS:
                cur = WriteSandboxPlugin._CN_DIGITS[ch]
            elif ch in WriteSandboxPlugin._CN_UNITS:
                u = WriteSandboxPlugin._CN_UNITS[ch]
                if cur == 0:
                    cur = 1
                if u == 10000:
                    section = (section + cur) * u
                    total += section
                    section = 0
                else:
                    section += cur * u
                cur = 0
            else:
                return None
        v = total + section + cur
        return v if v > 0 else None

    @staticmethod
    def _parse_target_chars(text: str) -> int:
        """从指令/长度约束里解析目标字数；支持中英文数字（六千/3000）；解析不到返回 0。"""
        CN_ALL = r"[0-9零一二两三四五六七八九十百千万]+"
        # 区间优先（3000~5000字 / 三千至五千字）→ 取上限
        m = re.search(rf"({CN_ALL})[~～\-至到]({CN_ALL})\s*字", text)
        if m:
            a = WriteSandboxPlugin._parse_cn_amount(m.group(1))
            b = WriteSandboxPlugin._parse_cn_amount(m.group(2))
            if a and b:
                return max(a, b)
        # 纯字（6000字 / 六千字 / 一万二千字 / 6千字）
        m = re.search(rf"({CN_ALL})\s*字", text)
        if m:
            v = WriteSandboxPlugin._parse_cn_amount(m.group(1))
            if v:
                return v
        # k 缩写（6k / 6k字）
        m = re.search(r"([0-9零一二两三四五六七八九十百]+)\s*[kK]\s*字?", text)
        if m:
            v = WriteSandboxPlugin._parse_cn_amount(m.group(1))
            if v:
                return v * 1000
        # 裸数字/裸中文数字（超过六千 / 写3000）
        m = re.search(r"([零一二两三四五六七八九十百千万]+万|[零一二两三四五六七八九十百]+千|[0-9]{3,})", text)
        if m:
            v = WriteSandboxPlugin._parse_cn_amount(m.group(1))
            if v:
                return v
        return 0

    def _plan_sections(self, job: dict) -> int:
        """根据指令与长度约束估算分节数；无明确要求时默认 1 节。"""
        instruction = job["instruction"]
        length_cfg = str((job["sandbox"].constraints or {}).get("length", ""))
        target = self._parse_target_chars(f"{instruction}\n{length_cfg}")
        if target > self._SECTION_TARGET_CHARS:
            n = math.ceil(target / self._SECTION_TARGET_CHARS)
            return max(1, min(n, self._MAX_SECTIONS))
        long_hints = (
            "长文", "整章", "全篇", "写满", "连载", "长篇",
            "越多越好", "尽量长", "一段很长的", "把这段写长", "写一整章",
        )
        if any(h in instruction for h in long_hints):
            return min(3, self._MAX_SECTIONS)
        return 1

    async def _run_write_job(self, job: dict) -> None:
        """后台主循环：逐节生成 → 逐节落盘 → 全部完成后更新记忆并通知。"""
        sandbox: Sandbox = job["sandbox"]
        work = job["work"]
        instruction = job["instruction"]
        tag = job["tag"]
        ch = job["chapter_no"]
        try:
            total = self._plan_sections(job)
            job["section_total"] = total
            job["status"] = "running"
            story_context = sandbox.render_story_memory(work) if work else ""
            chapter_outline = sandbox.render_chapter_outline(work, ch) if work else ""
            if chapter_outline:
                story_context = (story_context + "\n\n" + chapter_outline) if story_context else chapter_outline
            work_chars = sandbox.render_work_characters(work, ch) if work else ""
            if work_chars:
                story_context = (story_context + "\n\n" + work_chars) if story_context else work_chars
            # 全局卡智能选角：按本章剧情需要自动挑选，不点名、不全带
            if work:
                try:
                    picked = await self._select_global_cards(sandbox, work, ch, instruction, chapter_outline)
                    if picked:
                        story_context = (story_context + "\n\n" + picked) if story_context else picked
                except Exception as e:
                    logger.warning(f"全局卡智能选角失败，跳过: {e}")
            parts_written: list[str] = []
            file_path = None

            for i in range(1, total + 1):
                chunk = await self._write_section(job, i, total, parts_written, story_context)
                if chunk is None:
                    job["status"] = "failed"
                    job["error"] = f"第 {i} 节连续生成失败（已重试 {self._SECTION_RETRY} 次）"
                    await self._notify(job, failed=True)
                    return
                job["current_chunk"] = chunk
                if work:
                    file_path = sandbox.append_chapter(work, ch, chunk, tag=tag or instruction)
                else:
                    file_path = sandbox.append_output(chunk, tag=tag or instruction)
                job["file_path"] = str(file_path)
                if i == 1:
                    sandbox.append_history("user", instruction)
                sandbox.append_history("assistant", chunk)
                parts_written.append(chunk)
                job["section_idx"] = i

            # 整章完成：摘要 + 设定书（也在后台做，不阻塞任何人）
            if work:
                full = "\n\n".join(parts_written)
                summary = await self._chapter_summary(full)
                if not summary:
                    summary = full[:200]
                sandbox.upsert_chapter_summary(work, ch, summary)
                await self._update_story_state(sandbox, work, full, ch)
                # 自动提炼人设卡：按出现次数/置信度判断，达标且无同名卡才建卡
                try:
                    created = await self._auto_extract_characters(sandbox, work, full)
                    if created:
                        job["extract_report"] = "、".join(created)
                except Exception as e:
                    logger.warning(f"自动提炼人设卡失败: {e}")

            job["status"] = "done"
            # 口水词小哨兵：整章/整段完成后自动扫一遍重复词，随通知附上
            try:
                full_text = "\n\n".join(parts_written)
                repeat_report = sandbox.detect_repeated_words(full_text, work=work)
                if repeat_report:
                    job["repeat_report"] = repeat_report
            except Exception as e:
                logger.warning(f"口水词检测失败: {e}")
            await self._notify(job, failed=False)
        except Exception as e:
            logger.error(f"后台写作任务 {job.get('job_id')} 异常: {e}")
            job["status"] = "failed"
            job["error"] = str(e)
            try:
                await self._notify(job, failed=True)
            except Exception:
                pass
        finally:
            job["finished_at"] = time.time()

    async def _select_global_cards(
        self,
        sandbox: Sandbox,
        work: str,
        ch: int,
        instruction: str,
        chapter_outline: str,
    ) -> str:
        """从全局卡池里挑本章真正需要的卡，返回注入文本；失败/无选择返回空串。"""
        pool = sandbox.render_global_card_pool(work, ch)
        if not pool:
            return ""
        chapter_info = chapter_outline or f"（第 {ch} 章，暂无详细大纲）"
        chapter_info += f"\n用户写作要求：{instruction[:300]}"
        prompt = build_global_card_select_prompt(chapter_info=chapter_info, pool=pool)
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
        raw = self._clean(raw)
        selected: list[str] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                selected = parsed.get("selected") or []
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        selected = parsed.get("selected") or []
                except Exception:
                    pass
        names = [str(n).strip() for n in selected if str(n).strip()][:3]
        if not names:
            return ""
        cards: list[str] = []
        for n in names:
            card = sandbox.load_character(n, work)
            if card:
                cards.append(sandbox.render_character(card))
        if not cards:
            return ""
        return "【本章选用全局人设卡（经选角判断自动引入）】\n" + "\n\n".join(cards)

    async def _write_section(
        self,
        job: dict,
        idx: int,
        total: int,
        parts_written: list[str],
        story_context: str,
    ) -> Optional[str]:
        """生成单个分节，失败自动重试；连续失败返回 None。"""
        sandbox: Sandbox = job["sandbox"]
        instruction = job["instruction"]
        mode = job["mode"]
        work = job.get("work") or ""
        ch = int(job.get("chapter_no") or 0)
        history = sandbox.load_history()
        history_text = "\n".join(
            f"{'用户' if h['role'] == 'user' else '沙盒'}: {h['content'][:300]}"
            for h in history[-6:]
        ) if history else "（暂无历史，这是沙盒中的第一段。）"

        # 指令里有明确字数时，覆盖默认 length 约束，防止「300~800字」压制篇幅
        constraints_text = sandbox.render_constraints()
        target = self._parse_target_chars(f"{instruction}\n{constraints_text}")
        if target > 0:
            constraints_text = re.sub(
                r"(?m)^- length:.*$",
                f"- length: 用户明确要求全篇约 {target} 字，按此写足，宁多勿少",
                constraints_text,
            )

        system_prompt = build_system_prompt(
            style_card=sandbox.render_style_card(),
            constraints=constraints_text,
            history=history_text,
            instruction=instruction,
            story_context=story_context,
        )

        # 分节说明：让模型知道当前在第几节、目标长度、是否收尾
        per_section = math.ceil(target / total) if target > 0 else self._SECTION_TARGET_CHARS
        section_note = (
            f"\n\n【分节写作】本次任务共 {total} 节，每节约 {per_section} 字"
            f"（全篇目标约 {target if target > 0 else '未指定'} 字）。"
            f"现在写第 {idx}/{total} 节。"
        )
        if total > 1:
            if idx < total:
                section_note += (
                    "\n- 非最后一节：写到本节目标长度即可自然暂收，"
                    "不要提前收尾、不要写大结局，留好继续写的余地。"
                )
            else:
                section_note += "\n- 最后一节：在保持风格的前提下把整篇收束完整。"

        if idx == 1:
            if mode == "continue":
                if work:
                    link_ctx = self._build_continue_context(sandbox, work, ch)
                else:
                    link_ctx = self._last_assistant_output(sandbox)
                if link_ctx:
                    prompt = (
                        build_context_snippet("续写这一段，保持风格与设定一致", link_ctx)
                        + "\n\n续写要求：" + instruction + section_note
                    )
                else:
                    prompt = instruction + section_note
            elif mode == "rewrite":
                last_output = self._last_assistant_output(sandbox)
                if last_output:
                    prompt = (
                        build_context_snippet("按新的要求重写这一段", last_output)
                        + "\n\n新要求：" + instruction + section_note
                    )
                else:
                    prompt = instruction + section_note
            else:
                prompt = instruction + section_note
        else:
            # 后续节：带上已写部分的末尾，保证衔接
            prev_tail = parts_written[-1][-1200:] if parts_written else ""
            prompt = (
                f"接着上面已写的内容继续写。上一节结尾：\n{prev_tail}\n\n"
                f"继续要求：{instruction}\n{section_note}"
            )

        for attempt in range(self._SECTION_RETRY + 1):
            if attempt:
                logger.warning(f"写作沙盒第 {idx} 节第 {attempt} 次重试")
                await asyncio.sleep(1)
            raw = await self._llm_chat(prompt, system_prompt=system_prompt)
            text = self._clean(raw)
            if text:
                text = self._apply_banned_words(text, sandbox)
                # 长度兜底：不足本节目标时自动续写补足，防止「写短了就交差」
                if per_section > 0:
                    text = await self._ensure_section_length(
                        text, per_section, system_prompt, sandbox
                    )
                return text
        return None

    async def _ensure_section_length(
        self,
        text: str,
        need_len: int,
        system_prompt: str,
        sandbox: Sandbox,
    ) -> str:
        """本节字数不足时，紧接末尾续写补足；最多补 _SECTION_LENGTH_EXTRA 轮。"""
        for _ in range(self._SECTION_LENGTH_EXTRA):
            cur = len(text.replace("\n", "").replace(" ", ""))
            if cur >= need_len:
                break
            tail = text[-1500:]
            ext_prompt = (
                f"刚才的正文目前只有约 {cur} 字，目标约 {need_len} 字，还差 {need_len - cur} 字。\n"
                "请紧接下面这段的末尾继续写，保持同样的风格、人称与视角；"
                "不要重复已写内容，不要总结，不要另起炉灶，不要提前收尾。\n\n"
                f"{tail}\n\n"
                "【续写要求】直接输出续写正文，不要解释。"
            )
            raw = await self._llm_chat(ext_prompt, system_prompt=system_prompt)
            ext = self._clean(raw)
            if not ext:
                break
            text = text + "\n\n" + ext
        return self._apply_banned_words(text, sandbox)

    async def _notify(self, job: dict, failed: bool) -> None:
        """后台任务完成后主动推送消息到原会话。"""
        try:
            if failed:
                body = (
                    f"【后台写作中断】任务 {job.get('job_id')}\n"
                    f"{job.get('error') or '未知错误'}\n"
                )
                if job.get("file_path"):
                    body += f"已写完的部分已保存在：{job['file_path']}"
                else:
                    body += "这一轮还没有写出可保存的内容，要不要人家重新来一次？"
            else:
                body = f"【后台写作完成♪】任务 {job.get('job_id')}\n"
                if job.get("work"):
                    body += (
                        f"作品《{job['work']}》第{job.get('chapter_no')}章"
                        f"（共 {job.get('section_total')} 节）写完啦，全文已保存：{job.get('file_path')}"
                    )
                else:
                    body += f"全文（共 {job.get('section_total')} 节）写完啦，已保存：{job.get('file_path')}"
                if job.get("extract_report"):
                    body += f"\n\n【自动提炼人设卡】{job['extract_report']}"
                if job.get("repeat_report"):
                    body += "\n\n" + job["repeat_report"]
                chunk = job.get("current_chunk") or ""
                if chunk:
                    body += "\n\n【末尾节选】\n" + chunk[-400:]
            chain = MessageChain([Plain(body)])
            await self.context.send_message(job["session"], chain)
        except Exception as e:
            logger.error(f"后台写作通知发送失败: {e}")

    # ═══════════════════════════════════════════
    # 工具 1.5：沙盒进度（后台任务状态查询）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_progress")
    async def sandbox_progress(
        self,
        event: AstrMessageEvent,
    ) -> str:
        """查看后台写作任务的进度。

        当用户问「写到哪了」「后台任务好了吗」「还有几节」时调用；
        也用于写作完成后确认文件位置。

        Args:
            无参数。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        uid = str(event.get_sender_id())
        job = self._jobs.get(uid)
        if not job:
            return "当前没有后台写作任务。直接说「写一段……」让人家开一个♪"
        return self._render_job_status(job)

    # ═══════════════════════════════════════════
    # 工具 1.6：沙盒取消（中断后台写作任务）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_cancel")
    async def sandbox_cancel(
        self,
        event: AstrMessageEvent,
    ) -> str:
        """取消当前正在进行的后台写作任务。

        当用户说「停」「别写了」「取消写作任务」「把它停掉」时调用；
        中断后已写好的部分会保留在文件里，不会清掉。

        Args:
            无参数。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        uid = str(event.get_sender_id())
        job = self._jobs.get(uid)
        if not job:
            return "当前没有后台写作任务，不用取消啦♪"
        if job.get("status") not in ("pending", "running"):
            return f"任务 {job.get('job_id')} 已经是「{job.get('status')}」状态，不需要取消。"
        task = job.get("task")
        job["status"] = "cancelled"
        if task and not task.done():
            task.cancel()
        self._jobs.pop(uid, None)
        saved = job.get("file_path") or ""
        if saved:
            return f"已取消任务 {job.get('job_id')}，已写好的部分保留在：{saved}"
        return f"已取消任务 {job.get('job_id')}，还没有落盘的内容，随时可以重新开写♪"

    async def _chapter_summary(self, chapter: str) -> str:
        """把新章节压缩为单章摘要（≤300字），失败返回空串。"""
        prompt = build_chapter_summary_prompt(chapter=chapter[:4000])
        raw = await self._llm_chat(prompt, system_prompt="你只输出摘要正文，不要输出其他文字。")
        summary = self._clean(raw)
        if len(summary) > 320:
            summary = summary[:320]
        return summary

    async def _update_story_state(self, sandbox: Sandbox, work: str, chapter: str, ch: int) -> str:
        """用新章节覆盖更新设定书；解析失败保留旧设定书并提示。"""
        old = sandbox.load_story_state(work)
        old_meta = old.get("meta") if isinstance(old.get("meta"), dict) else {}
        old_str = json.dumps(old, ensure_ascii=False, indent=2)
        prompt = build_story_state_prompt(old_state=old_str, chapter=chapter[:4000])
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出其他文字。")
        raw = self._clean(raw)
        state: Optional[dict] = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                state = parsed
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        state = parsed
                except Exception:
                    pass
        if state is None:
            return "（设定书更新失败：模型输出无法解析，已保留旧设定书。）"
        if "meta" not in state or not isinstance(state.get("meta"), dict):
            state["meta"] = {}
        state["meta"]["current_chapter"] = ch
        if not state["meta"].get("title"):
            state["meta"]["title"] = old_meta.get("title", work)
        sandbox.save_story_state(work, state)
        return "（设定书已更新）"

    async def _extract_characters(self, text: str) -> list[dict]:
        """统计正文角色出现频次与置信度；返回角色列表（已按提示词判定线过滤）。"""
        prompt = build_character_extract_prompt(text=text[:6000])
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
        raw = self._clean(raw)
        data = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        data = parsed
                except Exception:
                    pass
        if not data:
            return []
        items = data.get("characters") or []
        chars: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            chars.append({
                "name": name,
                "aliases": [str(a) for a in (it.get("aliases") or []) if str(a)],
                "mention_count": int(it.get("mention_count") or 0),
                "sections_involved": int(it.get("sections_involved") or 0),
                "confidence": float(it.get("confidence") or 0),
                "role_hint": str(it.get("role_hint") or "配角"),
                "traits": str(it.get("traits") or ""),
            })
        return chars

    async def _auto_extract_characters(self, sandbox: Sandbox, work: str, full_text: str) -> list[str]:
        """整章写完后的自动提炼：置信度达标且无同名卡才建卡，返回新建卡名列表。"""
        try:
            chars = await self._extract_characters(full_text)
        except Exception as e:
            logger.warning(f"自动提炼角色统计失败: {e}")
            return []
        created: list[str] = []
        reference = sandbox.render_story_memory(work)[:1500]
        for c in chars:
            nm = str(c.get("name") or "").strip()
            if not nm or c.get("confidence", 0) < 0.45 or c.get("mention_count", 0) < 2:
                continue
            if sandbox.load_character(nm, work):
                continue
            role_hint = str(c.get("role_hint") or "配角")
            traits = str(c.get("traits") or "").strip()
            desc = f"{nm}，{role_hint}。{traits}" if traits else f"{nm}，{role_hint}。"
            try:
                prompt = build_character_prompt(description=desc, reference=reference)
                raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
                card = None
                raw = self._clean(raw)
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        card = parsed
                except Exception:
                    m = re.search(r"\{.*\}", raw, re.S)
                    if m:
                        try:
                            parsed = json.loads(m.group(0))
                            if isinstance(parsed, dict):
                                card = parsed
                        except Exception:
                            pass
                if card is None:
                    continue
                basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
                basic["name"] = nm
                card["basic"] = basic
                sandbox.save_character(card, "work", work)
                created.append(nm)
            except Exception as e:
                logger.warning(f"自动提炼角色卡「{nm}」失败: {e}")
        return created

    # ═══════════════════════════════════════════
    # 工具 2：沙盒设置（调整约束，立即生效）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_settings")
    async def sandbox_set(
        self,
        event: AstrMessageEvent,
        key: str,
        value: str,
    ) -> str:
        """调整写作沙盒的约束项，立即生效，无需重启。

        当用户说「这段太素了，尺度拉高」「换第一人称」「节奏快一点」
        「每段写长一点」「加几个忌讳词」等要求时调用。

        Args:
            key(string): 约束项名称，可选：length(长度)、perspective(人称视角)、scale(尺度：暧昧/露骨/极限)、banned_words(忌讳词，逗号分隔)、description_focus(描写偏好)、rhythm(节奏)、keep_persona(是否保留昔涟人格底色 true/false)。
            value(string): 要设置的新值。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        ok, msg = sandbox.set_constraint(key, value)
        return msg + "\n\n当前约束：\n" + sandbox.render_constraints() if ok else msg

    # ═══════════════════════════════════════════
    # 工具 3：沙盒收藏（满意段落进入 favorites/）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_favorite")
    async def sandbox_favorite(
        self,
        event: AstrMessageEvent,
        tag: str = "",
    ) -> str:
        """把沙盒最近产出的一段文字收藏起来，作为后续风格学习的「高权重样本」。

        当用户说「这段好」「收藏这段」「以后都按这个味道写」时调用。

        Args:
            tag(string): 可选，给收藏的段落起个名字。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        last_output = self._last_assistant_output(sandbox)
        if not last_output:
            return "沙盒里还没有产出可收藏的文字。"
        path = sandbox.save_favorite(last_output, tag=tag)
        return f"已收藏到 {path.name}♪ 下次学习风格时会优先参考它。"

    # ═══════════════════════════════════════════
    # 工具 4：沙盒学习素材（提炼风格卡）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_learn")
    async def sandbox_learn(
        self,
        event: AstrMessageEvent,
        name: str = "",
    ) -> str:
        """从素材库和收藏中提炼写作风格卡。

        当用户说「学一下这些素材的风格」「把素材库学进去」「以后按这个风格写」
        时调用。素材放在插件数据目录的 library/ 文件夹（txt/md），收藏自动优先。
        多篇素材会按篇打包（每篇带文件名，超长自动采样截断），不会把全部内容
        一次性塞给模型；也可传 name 只学名字匹配的那一篇。

        Args:
            name (str): 可选。只学习素材库中文件名包含该关键词的篇目；留空则学习全部。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"

        favs = sandbox.favorite_sources()
        libs = sandbox.library_sources()
        if not favs and not libs:
            return (
                f"素材库还是空的呢。请先把参考素材（txt/md）放进 {sandbox.user_dir / LIBRARY_DIR_NAME} "
                "文件夹，或先收藏几段满意输出，再让人家学习♪"
            )

        if name.strip():
            kw = name.strip().lower()
            matched = [s for s in libs if kw in s[0].lower()]
            if not matched:
                names = "、".join(n for n, _ in libs) or "（空）"
                return f"素材库里没有名字里带「{name.strip()}」的篇目。现有素材：{names}"
            libs = matched

        favorites_txt = _pack_sources(favs) or "（无）"
        library_txt = _pack_sources(libs) or "（无）"
        prompt = build_learn_prompt(favorites=favorites_txt, library=library_txt)
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
        raw = self._clean(raw)
        try:
            card = json.loads(raw)
            if not isinstance(card, dict):
                raise ValueError("不是 JSON 对象")
        except Exception:
            # 尝试提取第一个 { } 块
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    card = json.loads(m.group(0))
                except Exception:
                    return "风格卡解析失败了，请再试一次，或检查素材格式。"
            else:
                return "风格卡解析失败了，请再试一次，或检查素材格式。"

        # 用实际学习到的素材份数校正 meta，避免模型按被截断的输入误填
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        meta["source_count"] = len(libs)
        if favs or libs:
            meta["favorite_ratio"] = round(len(favs) / (len(favs) + len(libs)), 2)
        card["meta"] = meta

        # 修复：学习结果直接存入风格卡库存（style_cards/），可被 sandbox_style_combo 点名；
        # 同时保留「学了即用」，同步设为当前主卡。
        card_name = name.strip() or f"风格_{time.strftime('%m%d_%H%M%S')}"
        sandbox.save_style_card_to_library(card_name, card)
        sandbox.style_card = card
        sandbox.save_style_card()
        lines = [
            f"风格学习完成♪ 已入库存为「{card_name}」并设为当前主卡。",
            "卡库现有：" + ("、".join(sandbox.list_style_cards()) or "（空）"),
            "",
            "【当前风格卡】",
            sandbox.render_style_card(),
        ]
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 工具 5：沙盒状态
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_status")
    async def sandbox_status(
        self,
        event: AstrMessageEvent,
    ) -> str:
        """查看写作沙盒当前的风格卡、约束和历史概况。

        当用户问「现在沙盒是什么设置」「按什么风格写」「看看当前约束」时调用。

        Args:
            无参数。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        history = sandbox.load_history()
        lines = [
            "【风格卡】",
            sandbox.render_style_card(),
            "",
            "【约束】",
            sandbox.render_constraints(),
            "",
            f"【历史】共 {len(history)} 条片段，最近一条：",
        ]
        if history:
            last = history[-1]
            lines.append(f"{'用户' if last['role'] == 'user' else '沙盒'}: {last['content'][:120]}")
        else:
            lines.append("（暂无，沙盒还是新的。）")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 工具 5.2：沙盒风格组合（多卡融合 + 维度指定）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_style_combo")
    async def sandbox_style_combo(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        cards: str = "",
        dims: str = "",
        name: str = "",
    ) -> str:
        """组合多张风格卡：多卡融合 + 单一维度指定来源（如人称只取一张卡）。

        当用户说「把两张卡叠起来用」「人称用A卡、词汇用B卡」「风格组合」时调用。
        卡库 = style_cards/ 目录下的每张 json 卡；组合配置持久化，设置一次后写作自动生效。

        Args:
            action(string): list=列出卡库与当前组合；set=设置组合(cards 必填)；clear=清除组合回到主卡；inspect=查看单卡各维度(name 必填)。
            cards(string): set 时必填，底卡名逗号分隔，如「月歌二号_昔涟系列,知更鸟_清纯」。
            dims(string): set 时可选，维度来源指定，分号分隔，格式「维度=卡名」或「维度=卡1+卡2」，如「person_habit=知更鸟_清纯;sentence_style=月歌二号_昔涟系列」。维度：sentence_style(句式)/vocab_prefs(词汇)/description_focus(描写侧重)/person_habit(人称视角)/rhythm(节奏)/sense_focus(感官侧重)/scale_floor(尺度)/metaphor_style(比喻)。
            name(string): inspect 时必填，要查看的卡名。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"

        if action == "clear":
            sandbox.clear_combo()
            return "已清除风格组合，写作回到主卡模式♪"

        if action == "inspect":
            if not name.strip():
                return "inspect 需要指定卡名，例如 name=知更鸟_清纯。"
            card = sandbox.load_style_card_by_name(name.strip())
            if card is None:
                names = "、".join(sandbox.list_style_cards()) or "（空）"
                return f"卡库中没有「{name.strip()}」。现有卡：{names}"
            lines = [f"【{name.strip()}】"]
            core = card.get("core") if isinstance(card.get("core"), dict) else {}
            for k in CORE_DIMS:
                v = core.get(k) if isinstance(core.get(k), dict) else None
                if not v or not v.get("desc"):
                    continue
                ex = v.get("examples") or []
                sample = f"（例：{str(ex[0])[:40]}…）" if ex else ""
                lines.append(f"- {CORE_LABELS.get(k, k)}[{v.get('strength', '有')}]：{v['desc']}{sample}")
            return "\n".join(lines)

        if action == "set":
            if not cards.strip():
                return "set 需要指定底卡，例如 cards=月歌二号_昔涟系列,知更鸟_清纯。"
            card_list = [c.strip() for c in re.split(r"[,，]", cards) if c.strip()]
            dim_map: dict[str, list[str]] = {}
            if dims.strip():
                for pair in re.split(r"[;；]", dims):
                    if "=" not in pair:
                        continue
                    k, _, v = pair.partition("=")
                    k = k.strip()
                    vals = [x.strip() for x in re.split(r"[+＋]", v) if x.strip()]
                    if k and vals:
                        dim_map[k] = vals
            ok, msg = sandbox.set_combo(card_list, dim_map)
            if not ok:
                return msg
            return msg + "\n\n当前组合：\n" + sandbox.combo_status()

        # 默认 list
        lines = ["【卡库】"]
        names = sandbox.list_style_cards()
        lines.append("、".join(names) if names else "（空，把风格卡 json 放进 style_cards/ 目录）")
        lines.append("")
        lines.append("【当前组合】")
        lines.append(sandbox.combo_status())
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 工具 5.5：沙盒作品（长篇工作区管理）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_work")
    async def sandbox_work(
        self,
        event: AstrMessageEvent,
        action: str = "info",
        name: str = "",
    ) -> str:
        """管理长篇作品工作区（每篇作品独立维护逐章摘要 + 设定书记忆）。

        当用户提到「作品/长篇/章节/设定书」，或说「新建一篇《XX》」「继续写《XX》」
        「看看现在写到哪了」时调用。

        Args:
            action(string): info=查看当前作品与记忆概况；switch=切换/新建作品(name 必填)；list=列出所有作品。
            name(string): switch 时必填，作品名。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"

        if action == "list":
            works = sandbox.list_works()
            if not works:
                return "还没有任何长篇作品。说「写一篇《作品名》」就可以开新篇♪"
            lines = ["已有作品："]
            for w in works:
                n = len(sandbox.load_chapter_summaries(w))
                lines.append(f"- 《{w}》（{n} 章）")
            active = sandbox.get_active_work()
            if active:
                lines.append(f"当前激活：{active}")
            return "\n".join(lines)

        if action == "switch":
            if not name or not name.strip():
                return "切换作品需要作品名，例如「写《雾都夜行》」或指定 name 参数。"
            work = sandbox.set_active_work(name.strip())
            n = len(sandbox.load_chapter_summaries(work))
            state = sandbox.load_story_state(work)
            return (
                f"已切换到作品《{work}》♪ 目前共 {n} 章。\n"
                f"设定书：{json.dumps(state, ensure_ascii=False)[:200]}"
            )

        # 默认 info
        active = sandbox.get_active_work()
        if not active:
            return (
                "当前没有激活的长篇作品。说「写一篇《作品名》」开新篇，"
                "或用 沙盒作品(action=switch, name=作品名) 切换。"
            )
        memory = sandbox.render_story_memory(active)
        return f"【当前作品《{active}】\n{memory}"

    # ═══════════════════════════════════════════
    # 工具 5.6：沙盒大纲（作品大纲 JSON：全局脉络 + 分章节点）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_outline")
    async def sandbox_outline(
        self,
        event: AstrMessageEvent,
        instruction: str = "",
        action: str = "write",
        work: str = "",
        name: str = "",
        ch: int = 0,
        text: str = "",
        link_mode: str = "",
    ) -> str:
        """为长篇作品编写/确认/丢弃/查看大纲，管理章节接续与灵感便签（JSON：全局脉络 + 分章节点）。

        当用户说「给《XX》定个大纲」「规划一下剧情」「看看大纲」时调用。
        大纲生成后先存为**待确认草稿**，不直接落盘生效；用户看完说可以/确认后，
        再用 action=confirm 落盘为正式 outline.json。写正文前若存在未确认草稿会先拦截。
        action=discard 丢弃草稿；action=view 优先展示待确认草稿（带提示），无草稿才展示正式大纲。
        action=note 给某章挂灵感便签（ch + text，写该章时自动注入）；action=clearnote 清空某章便签。
        action=link 设置章节接续模式（link_mode=continuous 贴上一章结尾 / break 只给氛围引子 / auto 自动）。

        Args:
            instruction(string): 大纲要求，如「十章，涩涩密度前期暧昧后期露骨，结局留白」。
            action(string): write=生成草稿（默认）；revise=基于现有大纲微调（局部修改不推倒重来）；confirm=确认草稿落盘；discard=丢弃草稿；view=查看；note=挂灵感便签；clearnote=清空便签；link=设置章节接续。
            work(string): 可选，作品名。传入则切换/新建该作品；留空沿用当前激活作品。
            name(string): 兼容旧参数名，等价于 work。
            ch(int): note/clearnote 时指定章节号。
            text(string): note 时的灵感便签内容。
            link_mode(string): link 时设置 continuous/break/auto。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        work_name = work.strip() or name.strip()
        if work_name:
            active = sandbox.set_active_work(work_name)
        else:
            active = sandbox.get_active_work() or ""
        if not active:
            return "当前没有激活的作品。先指定作品名，例如「给《雾都夜行》定个大纲」。"
        if action == "view":
            if sandbox.has_pending_outline(active):
                return (
                    "（当前有未确认的大纲草稿，先确认或丢弃）\n\n"
                    + sandbox.render_pending_outline(active)
                )
            return sandbox.render_outline(active)
        if action == "confirm":
            if sandbox.confirm_outline(active):
                return f"大纲已确认落盘《{active}》♪\n\n" + sandbox.render_outline(active)
            return "当前没有待确认的大纲草稿，先写一份再确认。"
        if action == "discard":
            if sandbox.has_pending_outline(active):
                sandbox.discard_pending_outline(active)
                return f"已丢弃《{active}》的大纲草稿，正式大纲保持原样。"
            return "当前没有待丢弃的大纲草稿。"
        if action == "note":
            if not ch or ch < 1:
                return "note 需要指定章节号 ch。"
            if not text.strip():
                return "note 需要便签内容 text，例如「雨夜、便利店、旧伞」。"
            if sandbox.add_outline_mood(active, ch, text):
                return f"已给《{active}》第{ch}章挂上灵感便签♪\n\n" + sandbox.render_chapter_outline(active, ch)
            return f"大纲里还没有第{ch}章的节点，先确认大纲再挂便签。"
        if action == "clearnote":
            if not ch or ch < 1:
                return "clearnote 需要指定章节号 ch。"
            if sandbox.clear_outline_mood(active, ch):
                return f"已清空《{active}》第{ch}章的灵感便签♪"
            return f"大纲里没有第{ch}章的节点或便签。"
        if action == "link":
            mode = link_mode.strip().lower()
            if mode not in ("continuous", "break", "auto"):
                return "link 需要 link_mode=continuous（贴上一章结尾）/ break（只给氛围引子）/ auto（自动）。"
            result = sandbox.set_chapter_link_mode(active, mode)
            label = {
                "continuous": "连续叙事：续写时贴上一章结尾三行，维持连贯",
                "break": "断章收尾：续写不贴旧结尾，只给本章氛围引子",
                "auto": "自动：有灵感便签/结尾钩子就断章式，否则贴上一章结尾",
            }[result]
            return f"《{active}》章节接续已设为「{result}」♪\n{label}"
        if action == "revise":
            current = sandbox.load_outline(active)
            if not current.get("chapters"):
                return f"《{active}》还没有正式大纲，先写一份再微调（用 action=write）。"
            if not instruction.strip():
                return "微调大纲需要说明改哪里，例如「把第3章改成雨夜重逢，删掉陆岑的戏份，新增一个女管家角色」。"
            prompt = build_outline_revise_prompt(
                instruction=instruction,
                current_outline=json.dumps(current, ensure_ascii=False, indent=2),
                reference=sandbox.render_story_memory(active)[:3000],
            )
            raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
            raw = self._clean(raw)
            outline: Optional[dict] = None
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    outline = parsed
            except Exception:
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                        if isinstance(parsed, dict):
                            outline = parsed
                    except Exception:
                        pass
            if outline is None:
                return "大纲微调解析失败了，请再试一次，或换个说法描述调整要求。"
            meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
            meta["title"] = meta.get("title") or active
            outline["meta"] = meta
            if not isinstance(outline.get("chapters"), list):
                outline["chapters"] = []
            sandbox.save_pending_outline(active, outline)
            n = len(outline["chapters"])
            audit_note = ""
            try:
                rep = sandbox.render_character_audit(active)
                if "没有发现" not in rep:
                    audit_note = "\n\n【微调后人物卡复核】\n" + rep + "\n（确认大纲后记得清理旧卡/补建缺卡♪）"
            except Exception:
                pass
            return (
                f"《{active}》大纲微调草稿已生成（共 {n} 个章节节点），"
                "先审核一下，确认后人家再落盘生效：\n\n"
                + sandbox.render_pending_outline(active)
                + audit_note
            )
        if not instruction.strip():
            return "写大纲需要说明要求，例如「十章，暧昧转露骨，主角身世是核心悬念」。"
        prompt = build_outline_prompt(
            instruction=instruction,
            reference=sandbox.render_story_memory(active)[:3000],
        )
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
        raw = self._clean(raw)
        outline: Optional[dict] = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                outline = parsed
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        outline = parsed
                except Exception:
                    pass
        if outline is None:
            return "大纲解析失败了，请再试一次，或换个说法描述要求。"
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        meta["title"] = meta.get("title") or active
        outline["meta"] = meta
        if not isinstance(outline.get("chapters"), list):
            outline["chapters"] = []
        sandbox.save_pending_outline(active, outline)
        n = len(outline["chapters"])
        return (
            f"《{active}》大纲草稿已生成（共 {n} 个章节节点），"
            "先审核一下，确认后人家再落盘生效：\n\n"
            + sandbox.render_pending_outline(active)
        )

    # ═══════════════════════════════════════════
    # 工具 5.7：沙盒人设（统一格式人设卡，全局/作品双存储）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_character")
    async def sandbox_character(
        self,
        event: AstrMessageEvent,
        action: str = "create",
        description: str = "",
        name: str = "",
        scope: str = "work",
        work: str = "",
    ) -> str:
        """生成/查看/删除统一格式的人设卡（全局或当前作品存储）。

        当用户说「给配角也设个卡」「按这个格式做张人设卡」「调出这个人设卡」
        「看看有哪些人设卡」时调用。生成后写正文时，会按大纲章节节点的出场角色自动注入人设卡。

        Args:
            action(string): create=按描述生成新卡（默认）；list=列出可用卡；view=查看指定卡；update=按新描述重写指定卡；delete=删除指定卡；audit=复核当前作品人物卡与大纲一致性（找旧卡残留/缺卡/无关信息）。
            description(string): create/update 时必填，一句话角色描述，如「夜总会老板，表面和气，背地里是情报贩子，右眼角有疤」。
            name(string): view/update/delete 时必填，角色名。
            scope(string): global=存全局（跨作品可调）；work=存当前作品（默认，仅本作品可见）。
            work(string): 可选，scope=work 时指定作品；留空沿用当前激活作品。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        work_name = work.strip()
        active = sandbox.get_active_work() or ""
        if work_name:
            active = sandbox.set_active_work(work_name)
        scope = scope.strip().lower()
        if scope not in ("global", "work"):
            scope = "work"
        if scope == "work" and not active:
            return "存作品卡需要先指定作品：scope=work 且给 work 参数，或先激活一个作品。"

        if action == "list":
            names = sandbox.list_characters(active)
            if not names:
                return "还没有任何人设卡。说「给XX设个卡」生成一张♪"
            lines = ["可用人设卡："]
            for n in names:
                card = sandbox.load_character(n, active)
                meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
                scope_label = "全局" if meta.get("scope") != "work" else f"作品《{meta.get('work', active)}》"
                lines.append(f"- {n}（{scope_label}）")
            return "\n".join(lines)

        if action == "view":
            if not name.strip():
                return "view 需要指定角色名。"
            card = sandbox.load_character(name.strip(), active)
            if not card:
                return f"找不到角色「{name.strip()}」的人设卡。"
            return sandbox.render_character(card)

        if action == "delete":
            if not name.strip():
                return "delete 需要指定角色名。"
            if sandbox.delete_character(name.strip(), scope, active):
                return f"已删除人设卡「{name.strip()}」（{scope}）♪"
            return f"没有找到可删除的「{name.strip()}」（{scope}）。"

        if action == "audit":
            if not active:
                return "复核人物卡需要先激活一个作品（给 work 参数或先激活）。"
            return sandbox.render_character_audit(active)

        # create / update
        if not description.strip():
            return "生成人设卡需要角色描述，例如「夜总会老板，表面和气，背地里是情报贩子」。"
        if action == "update" and name.strip():
            sandbox.delete_character(name.strip(), scope, active)
        reference = sandbox.render_story_memory(active)[:1500] if active else ""
        prompt = build_character_prompt(description=description, reference=reference)
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
        raw = self._clean(raw)
        card: Optional[dict] = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                card = parsed
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        card = parsed
                except Exception:
                    pass
        if card is None:
            return "人设卡解析失败了，请再试一次，或换个说法描述角色。"
        basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
        if not basic.get("name"):
            basic["name"] = name.strip() or "未命名"
        if name.strip() and action == "update":
            basic["name"] = name.strip()
        card["basic"] = basic
        sandbox.save_character(card, scope, active)
        extra = ""
        if active:
            foreign = sandbox.check_card_foreign(card, active)
            if foreign:
                extra = (
                    "\n\n⚠️ 这张卡疑似夹带名单外人物关联（故事里未出场/未提到）："
                    + "、".join(f"「{f}」" for f in foreign)
                    + "\n要不要人家把卡改一下，清掉这些关联？"
                )
        return f"人设卡已保存♪（{scope}）\n\n" + sandbox.render_character(card) + extra

    # ═══════════════════════════════════════════
    # 工具 5.8：沙盒提炼角色（按频次/置信度自动建人设卡）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_extract_characters")
    async def sandbox_extract_characters(
        self,
        event: AstrMessageEvent,
        source: str = "recent",
        text: str = "",
        scope: str = "work",
        work: str = "",
        min_confidence: str = "0.45",
    ) -> str:
        """从文本/素材/作品章节中统计角色戏份，出现次数与置信度达标的自动提炼成人设卡并保存。

        当用户说「把这几章的角色提成人设卡」「从这段文字里提取角色」「给戏份多的角色建卡」时调用。
        判断机制：模型评估每个角色的出现次数/涉及段落/置信度，confidence≥阈值（默认0.45）且
        出现≥2次才建卡；已有同名卡不会覆盖，避免破坏手工卡。

        Args:
            source(string): recent=最近产出（默认）；library=素材库全部；work=当前作品全部章节；manual=手动传 text。
            text(string): source=manual 时必填，要分析的原文。
            scope(string): global=存全局；work=存当前作品（默认）。
            work(string): 可选，scope=work 时指定作品。
            min_confidence(string): 提卡最低置信度 0~1，默认 0.45。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        active = sandbox.get_active_work() or ""
        if work.strip():
            active = sandbox.set_active_work(work.strip())
        scope = scope.strip().lower()
        if scope not in ("global", "work"):
            scope = "work"
        if scope == "work" and not active:
            return "存作品卡需要先指定作品：scope=work 且给 work 参数，或先激活一个作品。"
        try:
            threshold = max(0.0, min(1.0, float(min_confidence)))
        except Exception:
            threshold = 0.45

        source = source.strip().lower() or "recent"
        if source == "manual":
            if not text.strip():
                return "source=manual 需要提供 text 参数（要分析的原文）。"
            full_text = text.strip()
        elif source == "library":
            srcs = sandbox.library_sources() or sandbox.favorite_sources()
            if not srcs:
                return "素材库还是空的，先放些素材或收藏再说♪"
            full_text = _pack_sources(srcs, limit=6000)
        elif source == "work":
            if not active:
                return "分析作品章节需要先激活作品（work 参数或沙盒作品切换）。"
            ch_parts: list[str] = []
            for p in sorted(sandbox.chapters_dir(active).glob("*.txt")):
                try:
                    ch_parts.append(p.read_text(encoding="utf-8").strip())
                except Exception:
                    continue
            full_text = "\n\n".join(ch_parts)[:6000]
            if not full_text.strip():
                return f"《{active}》的章节文件为空，先写点内容再说♪"
        else:  # recent
            full_text = self._last_assistant_output(sandbox, max_chars=6000)
            if not full_text:
                return "沙盒还没有产出，先写一段再说♪"

        try:
            chars = await self._extract_characters(full_text)
        except Exception as e:
            logger.error(f"沙盒提炼角色失败: {e}")
            return "角色统计失败了，请再试一次。"

        passed = [
            c for c in chars
            if c.get("confidence", 0) >= threshold and c.get("mention_count", 0) >= 2
        ]
        if not passed:
            return "这一段里没有明显够格的角色（出现次数/置信度不足），换更长的正文再试试？"

        reference = sandbox.render_story_memory(active)[:1500] if active else ""
        created: list[str] = []
        skipped: list[str] = []
        suspect_cards: list[str] = []
        for c in passed:
            nm = str(c.get("name") or "").strip() or "未命名"
            if sandbox.load_character(nm, active):
                skipped.append(nm)
                continue
            role_hint = str(c.get("role_hint") or "配角")
            traits = str(c.get("traits") or "").strip()
            desc = f"{nm}，{role_hint}。{traits}" if traits else f"{nm}，{role_hint}。"
            prompt = build_character_prompt(description=desc, reference=reference)
            raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON，不要输出任何其他文字。")
            card = None
            raw = self._clean(raw)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    card = parsed
            except Exception:
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                        if isinstance(parsed, dict):
                            card = parsed
                    except Exception:
                        pass
            if card is None:
                skipped.append(f"{nm}(解析失败)")
                continue
            basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
            basic["name"] = nm
            card["basic"] = basic
            sandbox.save_character(card, scope, active)
            created.append(nm)
            if active:
                foreign = sandbox.check_card_foreign(card, active)
                if foreign:
                    suspect_cards.append(f"{nm}（{'、'.join(foreign)}）")

        lines = ["【角色提炼完成♪】"]
        lines.append("新建人设卡：" + ("、".join(created) if created else "（无）"))
        if skipped:
            lines.append("跳过（已有卡或失败）：" + "、".join(skipped))
        if suspect_cards:
            lines.append("⚠️ 以下新卡疑似夹带名单外人物关联（未出场/未提到）：" + "；".join(suspect_cards))
        lines.append("卡库现有：" + ("、".join(sandbox.list_characters(active)) or "（空）"))
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 工具 5.9：沙盒废稿清理（旧文件清单 + 按编号删除）
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_cleanup")
    async def sandbox_cleanup(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        days: int = 30,
        targets: str = "",
    ) -> str:
        """列出/清理沙盒里的旧废稿（短篇输出 + 作品章节）。

        当用户说「看看有哪些旧稿」「清理一下旧文件」「把很久没动的稿子列出来」时调用。
        先 action=list 列出超过 days 天未修改的文件（带编号），用户点名编号后
        再 action=delete 删除（targets 传编号，如「1,3」）。

        Args:
            action(string): list=列出旧文件（默认）；delete=按编号删除。
            days(int): 多少天以上算旧稿，默认 30。
            targets(string): delete 时要删的编号，逗号分隔，如「1,3,5」。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        if action == "delete":
            if not targets.strip():
                return "delete 需要先指定要删的编号（targets），例如 targets=\"1,3\"。"
            stale = sandbox.list_stale_files(days=max(1, days))
            nums = [n.strip() for n in re.split(r"[,，、\s]+", targets) if n.strip().isdigit()]
            if not nums:
                return "编号格式不对，像「1,3」这样传。"
            deleted, missed = [], []
            for n in nums:
                i = int(n)
                if 1 <= i <= len(stale):
                    p = Path(stale[i - 1]["path"])
                    try:
                        if p.exists():
                            p.unlink()
                            deleted.append(stale[i - 1]["name"])
                    except Exception as e:
                        missed.append(f"{n}({e})")
                else:
                    missed.append(n)
            body = f"已清理 {len(deleted)} 个旧稿：{'、'.join(deleted) if deleted else '（无）'}"
            if missed:
                body += f"\n没删掉的编号：{', '.join(missed)}（可能越界或删除失败）"
            return body
        # list
        stale = sandbox.list_stale_files(days=max(1, days))
        if not stale:
            return f"超过 {days} 天没动过的旧稿：没有哦，沙盒很干净♪"
        lines = [f"【沙盒旧稿清单】超过 {days} 天未修改（共 {len(stale)} 个）："]
        for i, it in enumerate(stale, 1):
            size_kb = it["size"] / 1024
            lines.append(f"{i}. {it['name']}（{it['label']}｜{it['mtime']}｜{size_kb:.1f}KB）")
        lines.append("要清理哪个就说编号，例如「清理 1、3」，人家确认后动手♪")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 工具 6：沙盒重置
    # ═══════════════════════════════════════════
    @filter.llm_tool(name="sandbox_reset")
    async def sandbox_reset(
        self,
        event: AstrMessageEvent,
        clear_all: str = "false",
    ) -> str:
        """清空写作沙盒，重新开始。

        当用户说「清空沙盒」「重置沙盒」「重新开始写」时调用。

        Args:
            clear_all(string): true=连约束和风格卡一起清回默认；false=只清历史，保留设置。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"
        sandbox.clear_history()
        if clear_all.lower() in ("true", "1", "是", "yes"):
            sandbox.constraints = dict(DEFAULT_CONSTRAINTS)
            sandbox.style_card = dict(DEFAULT_STYLE_CARD)
            sandbox.save_constraints()
            sandbox.save_style_card()
            sandbox.clear_active_work()
            return "沙盒已全部重置：历史、约束、风格卡、当前作品都回到默认♪"
        return "沙盒历史已清空，约束和风格卡保留♪"
