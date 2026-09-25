"""写作域 Mixin：sandbox_write 全链路（生成/续写/改写/局部修订）+ 后台任务 + 摘要/设定书/角色提炼。"""
from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain

from ..core import Sandbox
from ..prompts import (
    build_chapter_summary_prompt,
    build_character_extract_prompt,
    build_character_prompt,
    build_context_snippet,
    build_global_card_select_prompt,
    build_revise_text_prompt,
    build_story_state_prompt,
    build_system_prompt,
)


class WriteMixin:
    """写作全链路：后台分节生成、续写、改写、局部修订、章节摘要/设定书/角色提炼。"""

    _CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    _CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


    @filter.llm_tool(name="sandbox_write")
    async def sandbox_write(
        self,
        event: AstrMessageEvent,
        instruction: str,
        mode: str = "generate",
        tag: str = "",
        work: str = "",
        chapter: int = 0,
    ) -> str:
        """在独立写作沙盒中后台分节生成文字，任务开始即返回，全部写完会自动把完整文件推给用户。

        当用户明确要求「写一段文字/故事/涩涩内容/情色描写/小说片段」，
        或要求「按我的素材风格写」「续写/改写上一段」时调用本工具。
        用户要求「把某句/某段改成……」「删掉那句话」「在某处加一句」等指定内容的修改时：
        用 mode=revise，只精确改动点名位置，不整篇推翻。
        长篇模式：用户提到作品名（如「写《雾都夜行》第3章」）时把作品名传入 work，
        正文会存进该作品的章节目录，并自动生成章节摘要、更新设定书。
        长文会自动分成每节约 4000 字的小节在后台逐节写；用户中途问进度时调用 sandbox_progress 工具。
        重写指定章节时（如「重写第8章」）：mode=rewrite 且传入 chapter=8，写完后会覆盖该章原文件，
        不会新增章节号。

        Args:
            instruction(string): 用户想要的内容描述，例如「她推门进来，雨还没干」「尺度拉高，重点写触感」。
            mode(string): generate=新写一段（长篇模式下为新章节）；continue=接着上一段续写；rewrite=按新要求重写上一段/指定章节；revise=按指定内容局部修订（只改点名部分，不整篇推翻）。
            tag(string): 可选，给这段文字起个名字，会用在文件名上。
            work(string): 可选，长篇作品名。传入则切换/新建该作品工作区；留空沿用当前激活作品。
            chapter(int): 可选，目标章节号。rewrite 指定章节（如重写第8章→8）或 generate 指定位置时用；0=自动判断。
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
                    char_audit_note = "\n\n【写前人物卡复核】\n" + rep + "\n（可以先让人家清理无关卡，也可以直接开写♪）"
            except Exception:
                pass

        # 长篇模式下先确定章节号（generate=新章，continue/rewrite=续写/重写目标章）
        chapter_no = 0
        if active_work:
            # 目标章号优先级：工具参数 chapter > 指令文本解析（如「重写第8章」）
            if not (chapter and chapter > 0):
                chapter = self._parse_chapter_no(instruction)
            if chapter and chapter > 0:
                # 用户/主模型明确指定目标章号（如「重写第8章」→8）
                chapter_no = int(chapter)
            elif mode == "generate":
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

        # 局部修订模式：不重写整段，按用户指定内容精确修改（复用哨兵「编辑指令+精确应用」机制），同步执行
        if mode == "revise":
            return await self._run_revise(sandbox, instruction, work, chapter)

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

    @classmethod
    def _parse_chapter_no(cls, text: str) -> int:
        """从「重写第8章」「第八章」「第 12 章」等指令里解析目标章号；解析不到返回 0。"""
        if not text:
            return 0
        m = re.search(r"(?:第|重写|写)([0-9零一二两三四五六七八九十百千]+)\s*章", text)
        if not m:
            m = re.search(r"([0-9零一二两三四五六七八九十百千]+)\s*章", text)
        if not m:
            return 0
        s = m.group(1)
        if s.isdigit():
            return int(s)
        return cls._parse_cn_amount(s) or 0

    @staticmethod
    def _parse_cn_amount(s: str) -> Optional[int]:
        """把「六千」「一万二千」「3000」解析成整数；失败返回 None。"""
        if s.isdigit():
            return int(s)
        total, section, cur = 0, 0, 0
        for ch in s:
            if ch.isdigit():
                cur = int(ch)
            elif ch in WriteMixin._CN_DIGITS:
                cur = WriteMixin._CN_DIGITS[ch]
            elif ch in WriteMixin._CN_UNITS:
                u = WriteMixin._CN_UNITS[ch]
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
            a = WriteMixin._parse_cn_amount(m.group(1))
            b = WriteMixin._parse_cn_amount(m.group(2))
            if a and b:
                return max(a, b)
        # 纯字（6000字 / 六千字 / 一万二千字 / 6千字）
        m = re.search(rf"({CN_ALL})\s*字", text)
        if m:
            v = WriteMixin._parse_cn_amount(m.group(1))
            if v:
                return v
        # k 缩写（6k / 6k字）
        m = re.search(r"([0-9零一二两三四五六七八九十百]+)\s*[kK]\s*字?", text)
        if m:
            v = WriteMixin._parse_cn_amount(m.group(1))
            if v:
                return v * 1000
        # 裸数字/裸中文数字（超过六千 / 写3000）
        m = re.search(r"([零一二两三四五六七八九十百千万]+万|[零一二两三四五六七八九十百]+千|[0-9]{3,})", text)
        if m:
            v = WriteMixin._parse_cn_amount(m.group(1))
            if v:
                return v
        return 0

    def _plan_sections(self, job: dict) -> int:
        """估算起始分节数。有明确字数按字数拆（每节约4000字）；无明确字数从1节起步，由剧情驱动动态续写。"""
        instruction = job["instruction"]
        length_cfg = str((job["sandbox"].constraints or {}).get("length", ""))
        target = self._parse_target_chars(f"{instruction}\n{length_cfg}")
        job["_target_chars"] = target
        if target > 0:
            n = math.ceil(target / self._SECTION_TARGET_CHARS)
            return max(1, min(n, self._MAX_SECTIONS))
        # 无明确字数：不预设节数，每节写完由剧情是否收尾决定是否新增下一节
        return 1

    async def _run_write_job(self, job: dict) -> None:
        """后台主循环：逐节生成 → 逐节落盘 → 全部完成后更新记忆并通知。"""
        sandbox: Sandbox = job["sandbox"]
        work = job["work"]
        instruction = job["instruction"]
        tag = job["tag"]
        ch = job["chapter_no"]
        mode = job.get("mode") or "generate"
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

            i = 1
            while True:
                chunk, ended_mark = await self._write_section(job, i, total, parts_written, story_context)
                if chunk is None:
                    job["status"] = "failed"
                    job["error"] = f"第 {i} 节连续生成失败（已重试 {self._SECTION_RETRY} 次）"
                    await self._notify(job, failed=True)
                    return
                job["current_chunk"] = chunk
                if work:
                    # 写作期间先落到独立暂存区（drafts/），不占正式章节号
                    file_path = sandbox.append_draft(work, job["job_id"], chunk, tag=tag or instruction)
                else:
                    file_path = sandbox.append_output(chunk, tag=tag or instruction)
                job["file_path"] = str(file_path)
                if i == 1:
                    sandbox.append_history("user", instruction)
                sandbox.append_history("assistant", chunk)
                parts_written.append(chunk)
                job["section_idx"] = i
                # 剧情驱动：每节写完判断是否自然收尾；收尾或触顶即停，否则自动新增一节
                if ended_mark is None:
                    ended_mark = await self._check_story_ended(job, chunk)
                job["section_total"] = i
                if ended_mark or i >= self._MAX_SECTIONS:
                    break
                i += 1

            # 整章完成：按模式落位 → 摘要 + 设定书（也在后台做，不阻塞任何人）
            if work:
                full = "\n\n".join(parts_written)
                # 目标章节号在任务启动时已确定（参数/指令解析/自动判断）：
                #   rewrite=目标章号覆盖原文件（不新增章号）；generate=新章；continue=续写目标章
                final_ch = job["chapter_no"] or 1
                job["chapter_no"] = final_ch
                # rewrite 时覆盖目标章；continue 追加旧文末尾；generate 新建
                final_path = sandbox.finalize_chapter(
                    work, job["job_id"], final_ch, tag=tag or instruction,
                    append_mode=(mode == "continue"),
                )
                if final_path:
                    file_path = final_path
                    job["file_path"] = str(final_path)
                summary = await self._chapter_summary(full)
                if not summary:
                    summary = full[:200]
                sandbox.upsert_chapter_summary(work, final_ch, summary)
                await self._update_story_state(sandbox, work, full, final_ch)
                # 自动提炼人设卡：按出现次数/置信度判断，达标且无同名卡才建卡
                try:
                    created = await self._auto_extract_characters(sandbox, work, full)
                    if created:
                        job["extract_report"] = "、".join(created)
                except Exception as e:
                    logger.warning(f"自动提炼人设卡失败: {e}")

            job["status"] = "done"
            # 口水词小哨兵：整章/整段完成后自动扫一遍重复词，随通知附上；
            # 检出问题时交给 LLM 判断并做「一轮」自动修订（防无限返工，不再二次检查）
            try:
                full_text = "\n\n".join(parts_written)
                repeat_report = sandbox.detect_repeated_words(full_text, work=work)
                if repeat_report:
                    job["repeat_report"] = repeat_report
                    revised, revise_note = await self._revise_repeated(sandbox, full_text, work)
                    if revise_note and revised != full_text:
                        job["revise_report"] = revise_note
                        # 落位：generate/rewrite/短篇直接覆盖正式文件；
                        # continue 追加模式另存修订版，避免冲掉旧文
                        try:
                            if job.get("file_path"):
                                fp = Path(job["file_path"])
                                if fp.exists():
                                    cur = fp.read_text(encoding="utf-8").rstrip()
                                    if cur != full_text.rstrip():
                                        # 续写/追加场景：文件里还有本次之外的内容 → 另存修订版，避免冲掉旧文
                                        rev_path = fp.with_name(fp.stem + "_哨兵修订版" + fp.suffix)
                                        rev_path.write_text(revised.rstrip() + "\n", encoding="utf-8")
                                        job["revise_path"] = str(rev_path)
                                    else:
                                        fp.write_text(revised.rstrip() + "\n", encoding="utf-8")
                        except Exception as e:
                            logger.warning(f"哨兵修订落盘失败: {e}")
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
            card = sandbox.load_character(n, work, work_only=True)
            if card:
                cards.append(sandbox.render_character(card))
        if not cards:
            return ""
        return "【本章选用人设卡（经选角判断自动引入）】\n" + "\n\n".join(cards)

    async def _write_section(
        self,
        job: dict,
        idx: int,
        total: int,
        parts_written: list[str],
        story_context: str,
    ) -> tuple[Optional[str], Optional[bool]]:
        """生成单个分节，失败自动重试；连续失败返回 (None, None)。附带剧情是否收尾的标记。"""
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
        dynamic = target <= 0
        per_section = math.ceil(target / total) if target > 0 else self._SECTION_TARGET_CHARS
        if dynamic:
            section_note = (
                f"\n\n【分节写作】本次不预设总节数与总长，由剧情自然推进。"
                f"现在写第 {idx} 节，写到本节自然段落即可停笔。"
            )
        else:
            section_note = (
                f"\n\n【分节写作】本次任务共 {total} 节，每节约 {per_section} 字"
                f"（全篇目标约 {target} 字）。"
                f"现在写第 {idx}/{total} 节。"
            )
            if idx < total:
                section_note += (
                    "\n- 非最后一节：写到本节目标长度即可自然暂收，"
                    "不要提前收尾、不要写大结局，留好继续写的余地。"
                )
            else:
                section_note += "\n- 最后一节：在保持风格的前提下把整篇收束完整。"
        # 剧情收尾标记：每节都要求，系统据此决定是否自动新增下一节
        section_note += (
            "\n- 本节正文写完后，另起一行单独输出状态标记（不要混进正文）："
            "剧情已经自然收尾就输出【STORY_END】，还没收尾就输出【STORY_CONTINUE】。"
        )

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
                text, ended_mark = self._strip_story_mark(text)
                # 长度兜底：仅明确字数模式启用；剧情驱动模式尊重自然收尾，不硬凑字数
                if not dynamic and per_section > 0:
                    text = await self._ensure_section_length(
                        text, per_section, system_prompt, sandbox
                    )
                    text, m2 = self._strip_story_mark(text)
                    if m2 is not None:
                        ended_mark = m2
                if text.strip():
                    return text, ended_mark
        return None, None

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

    @staticmethod
    def _strip_story_mark(text: str) -> tuple[str, Optional[bool]]:
        """剥离文末剧情状态标记行，返回 (正文, 是否已收尾)；无标记时返回 (原文, None)。"""
        m = re.search(r"\s*【(STORY_END|STORY_CONTINUE)】\s*$", text)
        if m:
            body = text[: m.start()].rstrip()
            return body, (m.group(1) == "STORY_END")
        return text, None

    async def _check_story_ended(self, job: dict, chunk: str) -> bool:
        """模型未输出状态标记时的兜底：轻量询问剧情是否已自然收尾。"""
        tail = (chunk or "")[-1200:]
        if not tail.strip():
            return True
        prompt = (
            f"下面是一篇小说当前写到的一段结尾：\n\n{tail}\n\n"
            "请判断故事到这里是否已经自然收尾"
            "（情节完整落幕、没有明显未交代的走向）。只回答两个字：是 或 否。"
        )
        raw = await self._llm_chat(prompt, timeout=60.0)
        return "是" in (raw or "")[:20]

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
                if job.get("revise_report"):
                    body += f"\n\n【口水词小哨兵·自动修订】{job['revise_report']}"
                    if job.get("revise_path"):
                        body += f"\n修订版另存：{job['revise_path']}"
                chunk = job.get("current_chunk") or ""
                if chunk:
                    body += "\n\n【末尾节选】\n" + chunk[-400:]
            chain = MessageChain([Plain(body)])
            await self.context.send_message(job["session"], chain)
        except Exception as e:
            logger.error(f"后台写作通知发送失败: {e}")

    @staticmethod
    def _parse_edit_json(raw: str) -> list[dict]:
        """容错解析 LLM 输出的编辑指令 JSON 数组（[{find, replace}, ...]）。"""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [e for e in parsed if isinstance(e, dict)]
        except Exception:
            pass
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, list):
                    return [e for e in parsed if isinstance(e, dict)]
            except Exception:
                pass
        return []

    @staticmethod
    def _apply_edits(text: str, edits: list[dict]) -> tuple[str, int, int]:
        """把编辑指令精确应用到正文：find 必须在文本中唯一定位（count==1）才替换，否则跳过。
        返回 (新文本, 应用数, 跳过数)。"""
        new_text = text
        applied = skipped = 0
        for ed in edits:
            find = str(ed.get("find", "") or "").strip()
            replace = str(ed.get("replace", "") or "")
            if not find or find == replace:
                skipped += 1
                continue
            if new_text.count(find) == 1:
                new_text = new_text.replace(find, replace)
                applied += 1
            else:
                skipped += 1
        return new_text, applied, skipped

    async def _revise_repeated(self, sandbox: Sandbox, full_text: str, work: str) -> tuple[str, str]:
        """口水词小哨兵自动修订：把检查结果交给 LLM 判断，输出编辑指令后由程序精确应用。
        只执行一轮（防无限返工）；仅替换能在正文中唯一定位的片段。返回 (修正后全文, 修订说明)。"""
        res = sandbox.analyze_repeated(full_text, work)
        if not res["dup"] and not res["freq"]:
            return full_text, ""
        paras = [p.strip() for p in full_text.splitlines() if p.strip()]

        def _ctx(no: int, radius: int = 1) -> str:
            """取第 no 段及其前后 radius 段，拼成带段号的上下文片段。"""
            segs = []
            for k in range(max(1, no - radius), min(len(paras), no + radius) + 1):
                mark = "→" if k == no else " "
                segs.append(f"{mark}[段{k}] {paras[k - 1][:200]}")
            return "\n".join(segs)

        bundle: list[str] = []
        for i, d in enumerate(res["dup"][:8], 1):
            bundle.append(
                f"── 对白语义重复 #{i}（第{d['a_no']}段 ≈ 第{d['b_no']}段，相似度{d['ratio']}）──\n"
                f"【前一处】\n{_ctx(d['a_no'])}\n【后一处】\n{_ctx(d['b_no'])}"
            )
        for f in res["freq"][:8]:
            pos = "、".join(str(p) for p in f["paras"][:4])
            bundle.append(
                f"── 旁白高频词「{f['word']}」×{f['count']}（出现于第 {pos} 段）──\n"
                + "\n".join(_ctx(p) for p in f["paras"][:3])
            )
        context_block = "\n\n".join(bundle)

        prompt = (
            "你是小说编辑，下面是某章『口水词小哨兵』的检查结果与问题片段上下文。\n"
            "请判断哪些值得修改：真正造成阅读疲劳的语义重复对白、过密的旁白高频词；\n"
            "轻微或正常的重复不要动。\n\n"
            "输出一个 JSON 数组（不要输出任何其他文字），每项：\n"
            "{\"find\": \"正文里能唯一找到的连续原文片段\", \"replace\": \"替换后的文本\"}\n\n"
            "要求：\n"
            "1. find 必须是正文中逐字存在的连续片段，能在全文唯一定位（建议 6~60 字）；\n"
            "   找不到唯一片段就少写几条，不要编造。\n"
            "2. 高频词：只把连续密集出现的其中 1~2 处换成同义表达，不要全部替换、不要删句。\n"
            "3. 对白重复：改写靠后一处，措辞变化但语义与人物语气不变。\n"
            "4. 不删改剧情、设定、人名；不动对白外的叙述结构。\n"
            "5. 最多 15 条。\n\n"
            f"【检查结果与上下文】\n{context_block}"
        )
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON 数组，不要输出任何其他文字。")
        raw = self._clean(raw)
        edits = self._parse_edit_json(raw)
        if not edits:
            return full_text, "哨兵检查到问题，但自动修订未能生成可靠编辑指令，已保留原文。"
        new_text, applied, skipped = self._apply_edits(full_text, edits)
        if applied == 0:
            return full_text, "哨兵检查到问题，但编辑指令未能命中正文，已保留原文。"
        note = f"哨兵已做一轮自动修订：应用 {applied} 处、跳过 {skipped} 处。"
        return new_text, note

    def _read_target_text(self, sandbox: Sandbox, work: str, chapter: int) -> tuple[str, Optional[Path], str]:
        """确定局部修订的目标正文与文件路径。
        返回 (全文, 目标文件路径, 定位说明)；找不到返回 ("", None, 原因)。"""
        if work:
            ch = chapter
            if not (ch and ch > 0):
                ch = sandbox.latest_chapter_no(work)
            if ch and ch > 0 and sandbox.chapter_exists(work, ch):
                d = sandbox.chapters_dir(work)
                files = sorted(d.glob(f"ch{int(ch):02d}*.txt"))
                if files:
                    try:
                        text = files[0].read_text(encoding="utf-8")
                        if text.strip():
                            return text, files[0], f"《{work}》第{ch}章"
                    except Exception:
                        pass
            drafts = sorted(sandbox.drafts_dir(work).glob("*.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if drafts:
                try:
                    text = drafts[0].read_text(encoding="utf-8")
                    if text.strip():
                        return text, drafts[0], f"《{work}》草稿（尚未定章）"
                except Exception:
                    pass
            return "", None, f"《{work}》还没有可修订的正文"
        p = sandbox.latest_output_path()
        if p and p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                if text.strip():
                    return text, p, "最近一篇产出"
            except Exception:
                pass
        return "", None, "还没有写过可修订的内容"

    async def _revise_text(self, sandbox: Sandbox, instruction: str, full_text: str) -> tuple[str, str]:
        """正文局部修订核心：把用户要求 + 全文交给 LLM，输出编辑指令后由程序精确应用。
        只执行一轮；仅替换能在正文中唯一定位的片段。返回 (修订后文本, 修订说明)。"""
        text_for_llm = full_text
        truncated = False
        if len(full_text) > 24000:
            text_for_llm = full_text[:24000]
            truncated = True
        prompt = build_revise_text_prompt(instruction, text_for_llm)
        if truncated:
            prompt += "\n\n（注：原文过长已截断前 24000 字符；若要修改截断范围外的内容，请分次进行）"
        raw = await self._llm_chat(prompt, system_prompt="你只输出 JSON 数组，不要输出任何其他文字。")
        raw = self._clean(raw)
        edits = self._parse_edit_json(raw)
        if not edits:
            return full_text, "未能生成可靠的编辑指令，已保留原文。"
        new_text, applied, skipped = self._apply_edits(full_text, edits)
        if applied == 0:
            return full_text, f"编辑指令未能命中正文（跳过 {skipped} 处），已保留原文。"
        return new_text, f"局部修订完成：应用 {applied} 处、跳过 {skipped} 处。"

    async def _run_revise(self, sandbox: Sandbox, instruction: str, work: str, chapter: int) -> str:
        """mode=revise 同步执行：读取目标正文 → LLM 编辑指令 → 程序精确应用 → 覆盖落盘。"""
        full_text, target_path, where = self._read_target_text(sandbox, work, chapter)
        if not full_text:
            return where
        revised, note = await self._revise_text(sandbox, instruction, full_text)
        if revised == full_text:
            return f"{where}：{note}"
        try:
            target_path.write_text(revised.rstrip() + "\n", encoding="utf-8")
        except Exception as e:
            logger.error(f"局部修订落盘失败: {e}")
            return f"{where}：修订完成但落盘失败（{e}）。\n{note}"
        changes: list[str] = []
        sm = difflib.SequenceMatcher(None, full_text.splitlines(), revised.splitlines())
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            old_part = " / ".join(full_text.splitlines()[i1:i2])[:80]
            new_part = " / ".join(revised.splitlines()[j1:j2])[:80]
            changes.append(f"· {old_part}  →  {new_part}")
            if len(changes) >= 5:
                break
        lines = [f"【局部修订♪】{where}已按你的要求改好啦。", note]
        if changes:
            lines.append("改动摘要：")
            lines.extend(changes)
            if len(changes) >= 5:
                lines.append("…（还有更多改动，详见文件）")
        lines.append(f"已保存：{target_path}")
        return "\n".join(lines)

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
            if not nm or c.get("confidence", 0) < 0.6 or c.get("mention_count", 0) < 3:
                continue
            if sandbox.load_character(nm, work):
                continue
            # 已有卡（含别名）查重：防止同一角色换称呼被重复提炼
            duplicate = False
            for card_name in sandbox.list_work_characters(work):
                card = sandbox.load_character(card_name, work)
                if not card:
                    continue
                basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
                known = [str(basic.get("name") or "")] + [str(a) for a in (basic.get("aliases") or [])]
                if any(nm == k or (k and nm in k) for k in known if k):
                    duplicate = True
                    break
            if duplicate:
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
