"""素材学习域 Mixin：sandbox_learn 风格卡提炼 + sandbox_favorite 收藏闭环。"""
from __future__ import annotations

import json
import re
import time
from astrbot.api.event import AstrMessageEvent, filter

from ..core import LIBRARY_DIR_NAME
from ..prompts import build_learn_prompt
from .base import _pack_sources


class LearnMixin:
    """素材学习：从 library/favorites 提炼风格卡，收藏高权重样本。"""

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
