"""大纲域 Mixin：sandbox_outline 说明书模式 + 校验器 + 章节差异审查。"""
from __future__ import annotations

import json
import re
from typing import Optional

from astrbot.api.event import AstrMessageEvent, filter

from ..core.outlineops import merge_outline_batch, missing_chapters
from ..prompts import build_outline_guide, build_outline_revise_guide


class OutlineMixin:
    """大纲：草稿先行、说明书模式收单、结构校验、未点名章节程序级保障。"""

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
        大纲章节多（>4 章）时分批提交：先 action=save_batch 存 meta+前 3 章，之后每批 3 章继续 save_batch，齐了再 confirm。
        action=discard 丢弃草稿；action=view 优先展示待确认草稿（带提示），无草稿才展示正式大纲。
        action=note 给某章挂灵感便签（ch + text，写该章时自动注入）；action=clearnote 清空某章便签。
        action=link 设置章节接续模式（link_mode=continuous 贴上一章结尾 / break 只给氛围引子 / auto 自动）。

        Args:
            instruction(string): 大纲要求，如「十章，涩涩密度前期暧昧后期露骨，结局留白」。
            action(string): write=返回大纲编写说明书（默认，主模型按指南写好 JSON 后用 save 提交）；save=收下主模型写好的大纲 JSON（放 text 参数），做结构校验+差异审查后存为待确认草稿；save_batch=分批收单（每批 meta+≤3 章，防单次输出超时）；revise=返回大纲修订说明书（只改点名部分，不推倒重来）；confirm=确认草稿落盘；discard=丢弃草稿；view=查看；note=挂灵感便签；clearnote=清空便签；link=设置章节接续。
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
            pending = sandbox.load_pending_outline(active)
            if pending is None:
                return "当前没有待确认的大纲草稿，先写一份再确认。"
            ok, problems = self._validate_outline(pending, strict=True)
            if not ok:
                return (
                    "草稿还有结构问题，先修正再确认：\n- " + "\n- ".join(problems)
                    + "\n\n（缺章/乱号用 action=save_batch 补齐或覆盖同号章）"
                )
            if sandbox.confirm_outline(active):
                return f"大纲已确认落盘《{active}》♪\n\n" + sandbox.render_outline(active)
            return "大纲落盘失败，请重试 confirm。"
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
                return f"《{active}》还没有正式大纲，先写一份再微调（用 action=write 拿说明书）。"
            if not instruction.strip():
                return "微调大纲需要说明改哪里，例如「把第3章改成雨夜重逢，删掉陆岑的戏份，新增一个女管家角色」。"
            guide = build_outline_revise_guide(
                instruction=instruction,
                current_outline=json.dumps(current, ensure_ascii=False, indent=2),
                reference=sandbox.render_story_memory(active)[:1500],
            )
            return (
                "【大纲修订说明书】人家把现有大纲和修订规则给你：只改用户点名的部分，其余节点原样保留。"
                "改好后调用 sandbox_outline 的 action=save（把修订后的完整大纲 JSON 放进 text 参数）提交，"
                "人家会做程序级差异审查。\n\n"
                + guide
            )
        if action == "save_batch":
            if not text.strip():
                return "save_batch 需要把本批 JSON 放进 text 参数：{\"meta\": {...}, \"chapters\": [...]}（首批带 meta，之后每批 ≤3 章）。"
            try:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError
            except Exception:
                m = re.search(r"\{.*\}", text, re.S)
                try:
                    parsed = json.loads(m.group(0)) if m else None
                    if not isinstance(parsed, dict):
                        raise ValueError
                except Exception:
                    return "save_batch 收到的不是合法 JSON 对象，请整理后重提。"
            cur = sandbox.load_pending_outline(active) or {"meta": {}, "chapters": []}
            merged = merge_outline_batch(cur, parsed)
            if not merged["meta"].get("title"):
                merged["meta"]["title"] = active
            ok, problems = self._validate_outline(merged, strict=False)
            if not ok:
                return "本批合并后结构有问题，未入库：\n- " + "\n- ".join(problems) + "\n请修正后重新 save_batch。"
            sandbox.save_pending_outline(active, merged)
            chs = [c["ch"] for c in merged["chapters"]]
            missing = missing_chapters(merged)
            lines = [f"已收下本批。《{active}》草稿累计 {len(chs)} 章（{chs[0]}~{chs[-1]}）。" if chs
                     else f"已收下 meta。《{active}》草稿还没有章节节点。"]
            if missing:
                lines.append(f"还缺：第 {'、'.join(str(c) for c in missing)} 章——请用 save_batch 补齐这些章号。")
            else:
                lines.append("章号连续。写满用户要求的章数后，请让用户过目草稿，用户确认后调用 action=confirm（会做完整连续性校验）。")
            preview = sandbox.render_pending_outline(active).split("\n")
            lines.append("当前草稿预览：\n" + "\n".join(preview[:15]) + ("\n…" if len(preview) > 15 else ""))
            return "\n".join(lines)
        if action == "save":
            if not text.strip():
                return "save 需要把写好的大纲 JSON 放进 text 参数（照着 action=write 返回的说明书编写）。"
            outline: Optional[dict] = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    outline = parsed
            except Exception:
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                        if isinstance(parsed, dict):
                            outline = parsed
                    except Exception:
                        pass
            if outline is None:
                return "save 收到的大纲不是合法 JSON 对象，请对照说明书重新整理后提交。"
            meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
            meta["title"] = meta.get("title") or active
            outline["meta"] = meta
            if not isinstance(outline.get("chapters"), list):
                outline["chapters"] = []
            ok, problems = self._validate_outline(outline)
            if not ok:
                return (
                    "大纲校验未通过，打回重写：\n- " + "\n- ".join(problems)
                    + "\n\n（对照 action=write 返回的说明书修正后，重新 save）"
                )
            diff_lines: list[str] = []
            old = sandbox.load_outline(active)
            if old.get("chapters"):
                diff_lines = self._diff_outline_chapters(old, outline)
            sandbox.save_pending_outline(active, outline)
            n = len(outline["chapters"])
            head = (
                f"《{active}》大纲草稿已收下（共 {n} 个章节节点），"
                "先审核一下，确认后人家再落盘生效：\n\n"
                + sandbox.render_pending_outline(active)
            )
            if diff_lines:
                head += "\n\n【程序差异审查】与正式大纲相比，本次改动涉及：\n- " + "\n- ".join(diff_lines[:10])
            return head
        if not instruction.strip():
            return "写大纲需要说明要求，例如「十章，暧昧转露骨，主角身世是核心悬念」。"
        guide = build_outline_guide(
            instruction=instruction,
            reference=sandbox.render_story_memory(active)[:1500],
        )
        return (
            "【大纲说明书】人家先把编写指南给你，你照这个格式亲手写好大纲 JSON，"
            "然后调用 sandbox_outline 的 action=save（把 JSON 放进 text 参数）存为待确认草稿。\n\n"
            + guide
        )

    @staticmethod
    def _validate_outline(outline: dict, strict: bool = True) -> tuple[bool, list[str]]:
        """大纲结构校验：meta/chapters 结构、ch 从 1 连续递增、字段类型。
        strict=False 为分批中途校验：允许暂无章节节点、章号暂不连续（重复/类型问题仍照查）。
        返回 (是否通过, 问题列表)。"""
        problems: list[str] = []
        if not isinstance(outline, dict):
            return False, ["大纲必须是 JSON 对象"]
        if not isinstance(outline.get("meta"), dict):
            problems.append("meta 必须是对象（含 title/premise/theme 等）")
        chapters = outline.get("chapters")
        if not isinstance(chapters, list):
            return False, problems + ["chapters 必须是数组"]
        if not chapters and strict:
            problems.append("chapters 不能为空数组，至少要有章节节点")
        seen: set[int] = set()
        for i, node in enumerate(chapters, start=1):
            if not isinstance(node, dict):
                problems.append(f"第 {i} 个章节节点不是对象")
                continue
            ch = node.get("ch")
            if not isinstance(ch, int):
                problems.append(f"第 {i} 个章节节点缺 ch（必须是数字，从 1 连续递增）")
                continue
            if ch in seen:
                problems.append(f"章号重复：ch={ch}")
            seen.add(ch)
            if strict and ch != i:
                problems.append(f"章号不连续：第 {i} 个节点 ch={ch}（应为 {i}，需从 1 连续递增）")
            for key in ("summary", "title", "tone", "scale", "cliffhanger", "notes"):
                v = node.get(key)
                if v is not None and not isinstance(v, str):
                    problems.append(f"第 {ch} 章「{key}」应为字符串")
            for key in ("plot_points", "characters"):
                v = node.get(key)
                if v is not None and not isinstance(v, list):
                    problems.append(f"第 {ch} 章「{key}」应为数组")
        return len(problems) == 0, problems

    @staticmethod
    def _diff_outline_chapters(old: dict, new: dict) -> list[str]:
        """对比新旧大纲的章节节点，列出被新增/删除/改动的章（程序级差异审查，
        防止模型顺手改没让改的地方）。"""
        old_chs = {c.get("ch"): c for c in old.get("chapters") or [] if isinstance(c, dict)}
        new_chs = {c.get("ch"): c for c in new.get("chapters") or [] if isinstance(c, dict)}
        lines: list[str] = []
        for ch in sorted(set(new_chs) - set(old_chs)):
            lines.append(f"新增 第{ch}章「{(new_chs[ch].get('title') or '')[:30]}」")
        for ch in sorted(set(old_chs) - set(new_chs)):
            lines.append(f"删除 第{ch}章「{(old_chs[ch].get('title') or '')[:30]}」")
        for ch in sorted(set(old_chs) & set(new_chs)):
            if old_chs[ch] != new_chs[ch]:
                lines.append(f"改动 第{ch}章「{(new_chs[ch].get('title') or '')[:30]}」")
        return lines
