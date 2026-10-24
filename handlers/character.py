"""人设卡域 Mixin：sandbox_character/sandbox_extract_characters + 校验器 + 字段编辑说明书。"""
from __future__ import annotations

import json
import re
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

from ..prompts import build_character_guide, build_character_prompt
from .base import _pack_sources


class CharacterMixin:
    """人设卡：统一格式、双存储、说明书模式收单、信息边界铁律。"""

    @filter.llm_tool(name="sandbox_character")
    async def sandbox_character(
        self,
        event: AstrMessageEvent,
        action: str = "create",
        description: str = "",
        name: str = "",
        scope: str = "work",
        work: str = "",
        text: str = "",
        merge_from: str = "",
    ) -> str:
        """生成/查看/删除统一格式的人设卡（全局或当前作品存储）。

        当用户说「给配角也设个卡」「按这个格式做张人设卡」「调出这个人设卡」
        「看看有哪些人设卡」时调用。生成后写正文时，会按大纲章节节点的出场角色自动注入人设卡。

        Args:
            action(string): create=返回人设卡编写说明书（默认，主模型按指南写好 JSON 后用 save 提交）；save=收下主模型写好的完整人设卡 JSON（放 text 参数），结构校验后落盘；list=列出可用卡；view=查看指定卡；update=按描述做字段级更新（只改动点名字段，其余原样保留，纯代码不走模型）；delete=删除指定卡；audit=复核当前作品人物卡（只查是否放入了无关的卡：旧卡残留/夹带无关人物）；merge=合并重复卡（name=保留卡，merge_from=被并入卡）。
            description(string): create 时一句话角色描述，如「夜总会老板，表面和气，背地里是情报贩子，右眼角有疤」；update 时写字段指令，如「删除称呼方式；口头禅改为 不必。」。
            name(string): view/update/delete/save 时必填，角色名。
            scope(string): global=存全局（跨作品可调）；work=存当前作品（默认，仅本作品可见）。
            work(string): 可选，scope=work 时指定作品；留空沿用当前激活作品。
            text(string): save 时必填，完整人设卡 JSON（按 action=create 返回的说明书编写）。
            merge_from(string): merge 时必填，被并入的卡名（如 水瓶座小姐（汐））；name 传保留的卡。
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

        if action == "merge":
            if not name.strip() or not merge_from.strip():
                return "merge 需要 name=保留的卡 与 merge_from=被并入的卡（如 name=汐, merge_from=水瓶座小姐（汐））。"
            if name.strip() == merge_from.strip():
                return "两张卡不能同名。"
            primary = sandbox.load_character(name.strip(), active)
            secondary = sandbox.load_character(merge_from.strip(), active)
            if not primary or not secondary:
                return (
                    f"两张卡都要存在才能合并（{name.strip()}={'有' if primary else '无'}，"
                    f"{merge_from.strip()}={'有' if secondary else '无'}）。"
                )
            from ..core.cardops import merge_cards, normalize_card
            primary, _ = normalize_card(primary)
            secondary, _ = normalize_card(secondary)
            merged = merge_cards(primary, secondary)
            scope_saved = "work" if (merged.get("meta") or {}).get("scope") == "work" else "global"
            sandbox.save_character(merged, scope_saved, active)
            sandbox.delete_character(merge_from.strip(), scope_saved, active)
            # 大纲出场名单同步替换（源名 → 保留名）
            replaced = sandbox.rename_in_outline(active, merge_from.strip(), name.strip())
            note = f"；大纲出场名单已替换 {replaced} 处" if replaced else ""
            return (
                f"已把「{merge_from.strip()}」并入「{name.strip()}」♪（别名吸收、空位互补{note}）\n\n"
                + sandbox.render_character(merged)
            )

        if action == "audit":
            if not active:
                return "复核人物卡需要先激活一个作品（给 work 参数或先激活）。"
            return sandbox.render_character_audit(active)

        # update：纯代码字段合并，不进模型、不读设定书，只动点名字段
        if action == "update":
            if not name.strip():
                return "update 需要指定角色名。"
            if not description.strip():
                return "update 需要写明要改哪些字段，例如「删除称呼方式；口头禅改为 不必。」"
            old = sandbox.load_character(name.strip(), active)
            if old is None:
                return f"找不到角色「{name.strip()}」的旧卡，无法走纯代码更新。"
            ok, card, changes = sandbox.apply_character_edit(old, description)
            if not ok:
                return (
                    "更新未执行，人家没认出要改哪些字段：\n- " + "；".join(changes)
                    + "\n\n" + self._build_card_update_guide()
                )
            basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
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
            return (
                "人设卡已更新♪（纯代码字段合并，未走模型、未参考设定书）\n变更：\n- "
                + "\n- ".join(changes)
                + "\n\n"
                + sandbox.render_character(card)
                + extra
            )

        if action == "save":
            if not text.strip():
                return "save 需要把人设卡 JSON 放进 text 参数（照着 action=create 返回的说明书编写）。"
            card: Optional[dict] = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    card = parsed
            except Exception:
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                        if isinstance(parsed, dict):
                            card = parsed
                    except Exception:
                        pass
            if card is None:
                return "save 收到的人设卡不是合法 JSON 对象，请对照说明书重新整理后提交。"
            basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
            if not basic.get("name"):
                basic["name"] = name.strip() or "未命名"
            card["basic"] = basic
            from ..core.cardops import validate_card_v2
            ok, problems = validate_card_v2(card)
            if not ok:
                return (
                    "人设卡校验未通过，打回重写：\n- " + "\n- ".join(problems)
                    + "\n\n（对照 action=create 返回的说明书修正后，重新 save）"
                )
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

        # create：按说明书模式，返回人设卡编写指南，由主模型亲手写卡后走 save 落盘
        if not description.strip():
            return "生成人设卡需要角色描述，例如「夜总会老板，表面和气，背地里是情报贩子」。"
        reference = sandbox.render_story_memory(active)[:1500] if active else ""
        guide = build_character_guide(description=description, reference=reference)
        return (
            "【人设卡说明书】人家先把编写指南给你，你照这个格式亲手写好完整的人设卡 JSON，"
            "然后调用 sandbox_character 的 action=save（把 JSON 放进 text 参数，name 传角色名）落盘。\n\n"
            + guide
        )

    @filter.llm_tool(name="sandbox_extract_characters")
    async def sandbox_extract_characters(
        self,
        event: AstrMessageEvent,
        source: str = "recent",
        text: str = "",
        scope: str = "work",
        work: str = "",
        min_confidence: str = "0.6",
    ) -> str:
        """从文本/素材/作品章节中统计角色戏份，出现次数与置信度达标的自动提炼成人设卡并保存。

        当用户说「把这几章的角色提成人设卡」「从这段文字里提取角色」「给戏份多的角色建卡」时调用。
        判断机制：模型评估每个角色的出现次数/涉及段落/置信度，confidence≥阈值（默认0.6）且
        出现≥3次才建卡，单章一次性龙套不建；已有同名卡不会覆盖，避免破坏手工卡。
        结果会标明：新建 / 已有跳过 / 龙套未建。

        Args:
            source(string): recent=最近产出（默认）；library=素材库全部；work=当前作品全部章节；manual=手动传 text。
            text(string): source=manual 时必填，要分析的原文。
            scope(string): global=存全局；work=存当前作品（默认）。
            work(string): 可选，scope=work 时指定作品。
            min_confidence(string): 提卡最低置信度 0~1，默认 0.6。
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
            if c.get("confidence", 0) >= threshold and c.get("mention_count", 0) >= 3
        ]
        # 龙套未建：有戏份但未达提卡门槛（出现不足 3 次或置信不足）的角色，只报不建
        passed_names = {str(c.get("name") or "").strip() for c in passed}
        weak = [
            str(c.get("name") or "").strip()
            for c in chars
            if str(c.get("name") or "").strip()
            and str(c.get("name") or "").strip() not in passed_names
            and (c.get("mention_count", 0) or 0) > 0
        ]
        if not passed:
            tail = f"（另有 {len(weak)} 个一次性龙套未建卡：{'、'.join(weak[:8])}{'…' if len(weak) > 8 else ''}）" if weak else ""
            return "这一段里没有够格立卡的角色（出现≥3次且置信度≥0.6 才建）。" + tail

        reference = sandbox.render_story_memory(active)[:1500] if active else ""
        created: list[str] = []
        skipped: list[str] = []
        suspect_cards: list[str] = []
        for c in passed:
            nm = str(c.get("name") or "").strip() or "未命名"
            if sandbox.find_character_by_identity(nm, active):
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
        if weak:
            lines.append("龙套未建（出现不足/置信不足）：" + "、".join(weak[:10]) + ("…" if len(weak) > 10 else ""))
        if suspect_cards:
            lines.append("⚠️ 以下新卡疑似夹带名单外人物关联（未出场/未提到）：" + "；".join(suspect_cards))
        lines.append("卡库现有：" + ("、".join(sandbox.list_characters(active)) or "（空）"))
        return "\n".join(lines)

    @staticmethod
    def _validate_character_card(card: dict) -> tuple[bool, list[str]]:
        """（deprecated：save 已改用 cardops.validate_card_v2，本方法留作旧路径安全网）
        人设卡结构校验：段必须是对象、列表字段必须是数组、relations 每项必须有 name。
        返回 (是否通过, 问题列表)。"""
        problems: list[str] = []
        if not isinstance(card, dict):
            return False, ["卡片必须是 JSON 对象"]
        for section in ("basic", "background", "story", "speech"):
            v = card.get(section)
            if v is not None and not isinstance(v, dict):
                problems.append(f"「{section}」应为对象")
        list_fields = {
            "basic": ("aliases", "quirks"),
            "background": ("secrets", "relations"),
            "story": ("plot_hooks",),
            "speech": ("catchphrases",),
        }
        for section, keys in list_fields.items():
            sec = card.get(section)
            if not isinstance(sec, dict):
                continue
            for k in keys:
                v = sec.get(k)
                if v is not None and not isinstance(v, list):
                    problems.append(f"「{section}.{k}」应为数组")
        bg = card.get("background")
        if isinstance(bg, dict):
            relations = bg.get("relations")
            if isinstance(relations, list):
                for i, r in enumerate(relations, start=1):
                    if not isinstance(r, dict) or not str(r.get("name") or "").strip():
                        problems.append(
                            f"relations 第 {i} 项缺少 name（应为 {{\"name\": \"与谁\", \"relation\": \"关系\", \"note\": \"现状\"}}）"
                        )
        return len(problems) == 0, problems

    @staticmethod
    def _build_card_update_guide() -> str:
        """人设卡字段编辑说明书：update 识别不了自然语言时，指引主模型如何组织指令或改用 save 提交。"""
        return (
            "【字段编辑说明书】update 是纯代码字段合并，只认严格指令，建议这样组织：\n"
            "可用字段：称呼方式、说话腔调/腔调、口头禅、外貌锚点、示例台词、语言节奏、别名、性别、年龄、身份、外貌、性格、"
            "习惯小动作/小动作、出身来历/出身、核心动机/动机、秘密/软肋、关系、定位、人物弧线、"
            "剧情钩子、涩涩参与度/尺度。\n"
            "指令写法：\n"
            "- 「X 改为 Y」→ 覆盖该字段\n"
            "- 「删除 X」→ 清空该字段\n"
            "- 「把性格改成……」「口头禅改为……，再加一句……」→ 用原话描述目标值\n"
            "如果用户要求比较自然语言化（如「性格改得更阴郁些」），直接按 action=create 的说明书"
            "重写完整卡片，用 action=save 提交（会覆盖同名卡）。"
        )
