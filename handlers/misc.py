"""杂项域 Mixin：sandbox_set/status/style_combo/work/cleanup/reset 六个薄壳工具。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from astrbot.api.event import AstrMessageEvent, filter

from ..core import (
    CORE_DIMS,
    CORE_LABELS,
    DEFAULT_CONSTRAINTS,
    DEFAULT_STYLE_CARD,
)
from ..core.textclean import deep_clean_sections, line_is_meta


class MiscMixin:
    """设置/状态/组合卡/作品工作区/清理/重置等薄壳工具。"""

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

    @filter.llm_tool(name="sandbox_work")
    async def sandbox_work(
        self,
        event: AstrMessageEvent,
        action: str = "info",
        name: str = "",
        key: str = "",
        value: str = "",
    ) -> str:
        """管理长篇作品工作区（每篇作品独立维护逐章摘要 + 设定书记忆）。

        当用户提到「作品/长篇/章节/设定书」，或说「新建一篇《XX》」「继续写《XX》」
        「看看现在写到哪了」时调用。

        Args:
            action(string): info=查看当前作品与记忆概况；switch=切换/新建作品(name 必填)；list=列出所有作品；config=调整作品写作配置(key/value 必填，如 关闭摘要注入 inject_memory=false)。
            name(string): switch 时必填，作品名；config 时可选（不填用当前激活作品）。
            key(string): config 时必填，配置项：inject_memory(是否注入前文摘要+设定书)、inject_characters(是否注入人设卡+选角)、auto_extract(每章写完是否自动提炼人设卡)、chapter_link(章节接续模式 auto/continuous/break)。
            value(string): config 时必填，新值：inject_*/auto_extract 用 true/false；chapter_link 用 auto/continuous/break。
        """
        sandbox = self._get_sandbox(event)
        if not sandbox:
            return "找不到沙盒（无法识别发送者）。"

        if action == "config":
            target = name.strip() or sandbox.get_active_work() or ""
            if not target:
                return "调整配置需要先指定作品（name 参数）或先激活一个作品。"
            if not key.strip():
                return "config 需要指定配置项 key：inject_memory / inject_characters / auto_extract / chapter_link。"
            k = key.strip().lower()
            cfg = sandbox.load_story_config(target)
            if k not in cfg:
                return f"未知配置项「{k}」。可用：inject_memory、inject_characters、auto_extract、chapter_link。"
            v = value.strip().lower()
            if k == "chapter_link":
                if v not in ("auto", "continuous", "break"):
                    return "chapter_link 取值：auto（自动）/ continuous（连续叙事贴上一章结尾）/ break（断章收尾只给氛围引子）。"
                cfg[k] = v
            else:
                if v not in ("true", "false", "1", "0", "开", "关", "是", "否"):
                    return f"{k} 需要 true/false（或 开/关）。"
                cfg[k] = v in ("true", "1", "开", "是")
            sandbox.save_story_config(target, cfg)
            label = {
                "inject_memory": "前文摘要+设定书注入",
                "inject_characters": "人设卡+选角注入",
                "auto_extract": "每章自动提炼人设卡",
                "chapter_link": "章节接续模式",
            }.get(k, k)
            return f"《{target}》配置已更新：{label} → {cfg[k]}"

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
        cfg = sandbox.load_story_config(active)
        cfg_lines = [
            f"注入前文摘要+设定书：{'开' if cfg.get('inject_memory', True) else '关'}",
            f"注入人设卡+选角：{'开' if cfg.get('inject_characters', True) else '关'}",
            f"每章自动提炼人设卡：{'开' if cfg.get('auto_extract', False) else '关'}",
            f"章节接续模式：{cfg.get('chapter_link', 'auto')}",
        ]
        return f"【当前作品《{active}】\n{memory}\n\n【写作配置】\n" + "\n".join(cfg_lines)

    @filter.llm_tool(name="sandbox_cleanup")
    async def sandbox_cleanup(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        days: int = 30,
        targets: str = "",
    ) -> str:
        """列出/清理沙盒里的旧废稿（短篇输出 + 作品章节），或清洗元信息残留。

        当用户说「看看有哪些旧稿」「清理一下旧文件」「把很久没动的稿子列出来」时调用。
        先 action=list 列出超过 days 天未修改的文件（带编号），用户点名编号后
        再 action=delete 删除（targets 传编号，如「1,3」）。
        用户说「清洗旧稿的残留标题/元信息」时用 action=scrub：先列清单，用户确认后执行。

        Args:
            action(string): list=列出旧文件（默认）；delete=按编号删除；scrub=扫描并清洗章节/产出文件里的分节标题等元信息残留（先列清单，targets 选编号执行）。
            days(int): 多少天以上算旧稿，默认 30。
            targets(string): delete/scrub 时要操作的编号，逗号分隔，如「1,3,5」；scrub 传「全部」清洗所有命中文件。
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
        if action == "scrub":
            files: list[tuple[str, Path]] = []
            for p in sorted(sandbox.outputs_dir.glob("*.txt")):
                files.append((f"短篇:{p.name}", p))
            for w in sandbox.list_works():
                for p in sorted(sandbox.chapters_dir(w).glob("*.txt")):
                    if p.is_file():
                        files.append((f"《{w}》:{p.name}", p))
            hits: list[tuple[str, Path, list[str]]] = []
            for label, p in files:
                try:
                    original = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                cleaned = deep_clean_sections(original)
                if cleaned != original.strip():
                    bad = [ln.strip() for ln in original.split("\n")
                           if ln.strip() and line_is_meta(ln)]
                    if bad:  # 只有真正命中元信息行的文件才进清单（纯空行压缩不列）
                        hits.append((label, p, bad))
            if not hits:
                return "扫了一遍，没有发现分节标题/状态标记等元信息残留♪"
            if not targets.strip():
                lines = [f"发现 {len(hits)} 个文件有元信息残留（列出的行将被删除）："]
                for i, (label, _, bad) in enumerate(hits, 1):
                    lines.append(f"{i}. {label}（{len(bad)} 行，如：{bad[0][:50]}）")
                lines.append("确认清洗就说「scrub 全部」，或指定编号如 targets=1,3。")
                return "\n".join(lines)
            nums = [n.strip() for n in re.split(r"[,，、\s]+", targets) if n.strip()]
            do_all = any(n in ("all", "全部", "a") for n in nums)
            idx = {int(n) for n in nums if n.isdigit()}
            done = skipped = 0
            for i, (label, p, _) in enumerate(hits, 1):
                if not (do_all or i in idx):
                    continue
                try:
                    cleaned = deep_clean_sections(p.read_text(encoding="utf-8"))
                    # 备份名后缀放最后（ch01_xx.txt.bak_scrub）：*.txt / ch*.txt glob 不会匹配
                    bak = p.with_name(p.name + ".bak_scrub")
                    if not bak.exists():
                        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                    p.write_text(cleaned + "\n", encoding="utf-8")
                    done += 1
                except Exception:
                    skipped += 1
            return f"清洗完成：{done} 个文件（已留 .bak_scrub 备份），失败 {skipped} 个。"
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
