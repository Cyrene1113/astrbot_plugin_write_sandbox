"""写作沙盒 · characters 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .constants import CHARACTERS_DIR_NAME, CHARACTER_SCHEMA_VERSION, _LOCK, _safe_title


class CharactersMixin:
    """characters 域方法。"""

    _CARD_FIELD_LABELS: dict[str, tuple[str, str]] = {
        "称呼方式": ("speech", "address_style"),
        "说话腔调": ("speech", "tone"),
        "腔调": ("speech", "tone"),
        "口头禅": ("speech", "catchphrases"),
        "别名": ("basic", "aliases"),
        "性别": ("basic", "gender"),
        "年龄": ("basic", "age"),
        "身份": ("basic", "identity"),
        "外貌": ("basic", "appearance"),
        "性格": ("basic", "personality"),
        "习惯小动作": ("basic", "quirks"),
        "小动作": ("basic", "quirks"),
        "出身来历": ("background", "origin"),
        "出身": ("background", "origin"),
        "核心动机": ("background", "motivation"),
        "动机": ("background", "motivation"),
        "秘密": ("background", "secrets"),
        "软肋": ("background", "secrets"),
        "关系": ("background", "relations"),
        "定位": ("story", "role"),
        "人物弧线": ("story", "arc"),
        "剧情钩子": ("story", "plot_hooks"),
        "涩涩参与度": ("story", "scale_role"),
        "尺度": ("story", "scale_role"),
    }

    _NON_PERSON_TAIL = set("猫狗鸟鱼店馆楼屋山城镇村院厅房桥路树花月界泉谷川林峰崖湾洲原野漠岭岸河江湖海云霞霜雪风雨星日夜晨昏光影烟雾尘沙石岩玉珠花草树叶枝根藤果实籽瓣潮汐波浪溪涧瀑布潭泽")

    _RELATION_COMMON_WORDS = set(
        "家人朋友兄弟姊妹姐妹父母双亲母亲父亲妻子丈夫恋人情人敌人仇人同伴伙伴同事同学"
        "邻居路人故人旧识恩人仇家师父徒弟师尊弟子老师学生兄长妹妹弟弟哥哥姐姐叔伯姑舅"
        "亲戚长辈晚辈子女儿女乡亲邻里众人大家自己彼此对方谁某一切有些那些这些哪个哪位"
        "熟人新人旧人前人后人外人内人本人亲人挚友密友战友同僚同乡同门同窗心腹亲信下属"
        "上司老板掌柜伙计仆役丫鬟家丁护卫随从红颜蓝颜知己伯乐贵人对手劲敌宿敌情敌冤家"
        "对头死对头陌生人旁人家中老小一家老小孤老孤儿寡妇遗孀弃子继母继父养母养父义父"
        "义母干爹干娘结拜忘年交莫逆生死交患难交泛泛之交点头交一面之缘青梅竹马未婚妻未婚夫前任"
        "他们她们我们你们咱们人家自己彼此对方众人各位诸位"
    )

    _RELATION_PATTERNS = (
        re.compile(r"(?:与|和|跟|同)([\u4e00-\u9fff]{2,3}?)(?:有|是|为|成)?(?:的)?(?:关系|恩怨|旧情|旧怨|约定|婚约|交情|情谊|羁绊|缘分|师徒|主仆|兄妹|兄弟|姐妹|母女|父子|仇怨|过节|瓜葛|牵连|牵扯|情债|旧识)"),
        re.compile(r"(?:与|和|跟|同)([\u4e00-\u9fff]{2,3}?)(?:有|是|为|成)?(?:一起|重逢|相遇|相识|联手|结盟|结拜|决裂|反目|同居|共事|同行|结伴|成婚|成亲|对峙|交手|并肩)"),
    )

    def work_characters_dir(self, work: str) -> Path:
        d = self.work_dir(work) / CHARACTERS_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_characters(self, work: str = "") -> list[str]:
        """列出全局卡；work 非空时并列该作品卡（去重）。"""
        names = sorted(p.stem for p in self.characters_dir.glob("*.json"))
        if work:
            wd = self.work_dir(work) / CHARACTERS_DIR_NAME
            for p in sorted(wd.glob("*.json")):
                if p.stem not in names:
                    names.append(p.stem)
        return names

    def list_work_characters(self, work: str) -> list[str]:
        """只列该作品目录下的人设卡（隔离机制：作品检索/审计只认作品卡，全局卡不参与）。"""
        if not work:
            return []
        return sorted(p.stem for p in self.work_characters_dir(work).glob("*.json"))

    def _char_file(self, name: str, scope: str, work: str = "") -> Path:
        safe = f"{_safe_title(name, max_len=32)}.json"
        if scope == "work":
            if not work:
                work = self.get_active_work() or ""
            return self.work_characters_dir(work) / safe
        return self.characters_dir / safe

    def load_character(self, name: str, work: str = "", work_only: bool = False) -> Optional[dict]:
        """加载人设卡：作品卡优先，回退全局卡；work_only=True 时只加载作品卡（隔离机制）。"""
        name = name.strip()
        if not name:
            return None
        safe = f"{_safe_title(name, max_len=32)}.json"
        if work:
            wf = self.work_characters_dir(work) / safe
            if wf.exists():
                try:
                    data = json.loads(wf.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        if work_only:
            return None
        gf = self.characters_dir / safe
        if gf.exists():
            try:
                data = json.loads(gf.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def save_character(self, card: dict, scope: str, work: str = "") -> Path:
        """保存人设卡：scope=global 存全局目录，scope=work 存作品目录。"""
        basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        meta["scope"] = scope
        meta["schema_version"] = CHARACTER_SCHEMA_VERSION
        meta["created_at"] = meta.get("created_at") or datetime.now().isoformat(timespec="seconds")
        if scope == "work":
            meta["work"] = work or self.get_active_work() or ""
        else:
            meta.pop("work", None)
        card["meta"] = meta
        fname = _safe_title(str(basic.get("name") or "未命名"), max_len=32)
        path = (self.work_characters_dir(work) if scope == "work" else self.characters_dir) / f"{fname}.json"
        with _LOCK:
            path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def delete_character(self, name: str, scope: str, work: str = "") -> bool:
        path = self._char_file(name, scope, work)
        if path.exists():
            path.unlink()
            return True
        return False

    def _match_card_label(self, text: str) -> Optional[tuple[str, str]]:
        for label in sorted(self._CARD_FIELD_LABELS, key=len, reverse=True):
            if label in text:
                return self._CARD_FIELD_LABELS[label]
        return None

    def apply_character_edit(self, card: dict, description: str) -> tuple[bool, dict, list[str]]:
        """纯代码字段合并：按描述中的明确指令对旧卡做增删改，其余字段原样保留。

        支持写法：
        - 「删除X」「删掉X」「去掉X」→ 删除该字段
        - 「X改为Y」「把X改成Y」「X=Y」「X：Y」→ 设置该字段
        - 含「保留/不动/其余」的句子直接跳过
        列表类字段按 、，,/ 切分为数组。返回 (ok, 新卡, 变更说明)。
        """
        import copy as _copy
        card = _copy.deepcopy(card)
        changes: list[str] = []
        sentences = [s for s in re.split(r"[；;\n]", description) if s.strip()]
        for sent in sentences:
            s = sent.strip()
            m = re.match(r"^(删除|删掉|删去|去掉|移除|清掉)\s*(.*)$", s)
            if m:
                label = self._match_card_label(m.group(2).strip())
                if label:
                    sec, key = label
                    sub = card.get(sec)
                    if isinstance(sub, dict) and key in sub:
                        del sub[key]
                        changes.append(f"删除「{key}」")
                    continue
                changes.append(f"未识别字段，跳过：「{s}」")
                continue
            m = re.match(r"^(?:([^\s＝:=，,]+?)\s*(?:改为|改成|设为|设置为|设定为)\s*[＝:=：]?\s*(.*)$|([^\s＝:=，,]+?)\s*[＝:=：]\s*(.*)$)", s)
            if m:
                g1, g2, g3, g4 = m.groups()
                field = (g1 or g3 or "").strip()
                raw = (g2 or g4 or "").strip().strip("＝:=：")
                label = self._match_card_label(field)
                if label:
                    sec, key = label
                    sub = card.setdefault(sec, {})
                    if key == "relations":
                        sub[key] = []
                        changes.append(f"清空「{key}」（关系结构复杂，请用 view 核对后手工调整）")
                    elif key in ("aliases", "personality", "quirks", "secrets", "plot_hooks", "catchphrases"):
                        vals = [v.strip() for v in re.split(r"[、,，/]", raw) if v.strip()]
                        sub[key] = vals
                        changes.append(f"设置「{key}」={vals}")
                    else:
                        sub[key] = raw
                        changes.append(f"设置「{key}」={raw}")
                    continue
            if re.search(r"(保留|不动|维持|其余|其他)", s):
                continue
            changes.append(f"未识别指令，跳过：「{s}」")
        if not changes:
            return False, card, ["没有识别到任何可执行的字段指令，请用「删除X」「把X改为Y」的写法。"]
        return True, card, changes

    def render_character(self, card: dict) -> str:
        """渲染一张人设卡为文本。"""
        lines: list[str] = []
        basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
        meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
        name = basic.get("name") or meta.get("title") or "未命名"
        lines.append(f"【人设卡·{name}】")
        if basic.get("aliases"):
            lines.append(f"别名：{'、'.join(str(a) for a in basic['aliases'])}")
        for k, label in (("gender", "性别"), ("age", "年龄"), ("identity", "身份"), ("appearance", "外貌"), ("personality", "性格")):
            v = basic.get(k)
            if v:
                lines.append(f"{label}：{v}")
        quirks = basic.get("quirks") or []
        if isinstance(quirks, list) and quirks:
            lines.append("习惯小动作：" + "、".join(str(q) for q in quirks))
        bg = card.get("background") if isinstance(card.get("background"), dict) else {}
        if bg.get("origin"):
            lines.append(f"出身来历：{bg['origin']}")
        if bg.get("motivation"):
            lines.append(f"核心动机：{bg['motivation']}")
        secrets = bg.get("secrets") or []
        if isinstance(secrets, list) and secrets:
            lines.append("秘密/软肋：" + "、".join(str(s) for s in secrets))
        rels = bg.get("relations") or []
        if isinstance(rels, list) and rels:
            lines.append("关系：")
            for r in rels:
                if isinstance(r, dict):
                    parts = [str(r.get(k, "")) for k in ("name", "relation", "note") if r.get(k)]
                    if parts:
                        lines.append(f"  · {'｜'.join(parts)}")
        st = card.get("story") if isinstance(card.get("story"), dict) else {}
        if st.get("role"):
            lines.append(f"定位：{st['role']}")
        if st.get("arc"):
            lines.append(f"人物弧线：{st['arc']}")
        hooks = st.get("plot_hooks") or []
        if isinstance(hooks, list) and hooks:
            lines.append("剧情钩子：" + "、".join(str(h) for h in hooks))
        if st.get("scale_role"):
            lines.append(f"涩涩参与度：{st['scale_role']}")
        sp = card.get("speech") if isinstance(card.get("speech"), dict) else {}
        if sp.get("tone"):
            lines.append(f"说话腔调：{sp['tone']}")
        if sp.get("address_style"):
            lines.append(f"称呼方式：{sp['address_style']}")
        cps = sp.get("catchphrases") or []
        if isinstance(cps, list) and cps:
            lines.append("口头禅：" + "、".join(str(c) for c in cps))
        scope_label = "全局" if meta.get("scope") != "work" else f"作品《{meta.get('work', '')}》"
        lines.append(f"（存储：{scope_label}）")
        return "\n".join(lines)

    def render_global_card_pool(self, work: str, ch: int) -> str:
        """作品卡池摘要：一行一张（名字｜身份｜核心特征），供选角判断。

        隔离机制：作品写作只用本作品目录下的卡，全局卡不参与作品检索；
        需要全局角色时，先把全局卡复制修改成作品卡再使用。
        排除本章已出场（已由 render_work_characters 注入）的角色。
        """
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        exclude: set[str] = set()
        if cur and isinstance(cur.get("characters"), list):
            exclude |= {str(c).strip() for c in cur["characters"] if str(c).strip()}
        lines: list[str] = []
        for p in sorted(self.work_characters_dir(work).glob("*.json")):
            try:
                card = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(card, dict):
                continue
            basic = card.get("basic") if isinstance(card.get("basic"), dict) else {}
            name = str(basic.get("name") or p.stem).strip()
            if not name or name in exclude:
                continue
            identity = str(basic.get("identity") or "").strip()
            personality = str(basic.get("personality") or "").strip()
            st = card.get("story") if isinstance(card.get("story"), dict) else {}
            role = str(st.get("role") or "").strip()
            feat = personality[:40] or identity[:40] or role
            line = f"- {name}"
            if identity:
                line += f"｜{identity}"
            if feat:
                line += f"｜{feat}"
            lines.append(line)
        return "\n".join(lines) if lines else ""

    def render_work_characters(self, work: str, ch: int) -> str:
        """按大纲章节节点的「出场角色」加载人设卡（作品卡优先），拼成注入文本。"""
        outline = self.load_outline(work)
        chapters = outline.get("chapters") or []
        cur = next((it for it in chapters if isinstance(it, dict) and int(it.get("ch", 0)) == ch), None)
        if cur is None:
            candidates = [it for it in chapters if isinstance(it, dict)]
            if candidates:
                cur = min(candidates, key=lambda it: abs(int(it.get("ch", 0)) - ch))
        names: list[str] = []
        if cur and isinstance(cur.get("characters"), list):
            names = [str(c) for c in cur["characters"] if str(c).strip()]
        if not names:
            return ""
        lines = [f"【本章出场角色人设卡（《{work}》第{ch}章）】"]
        for n in names:
            card = self.load_character(n, work, work_only=True)
            if card:
                lines.append(self.render_character(card))
            else:
                lines.append(f"（角色「{n}」还没有人设卡，可按上下文自由处理）")
        return "\n".join(lines)

    def _looks_like_person_name(self, rn: str) -> bool:
        """判断一个关系对象名是否「像人名」：2~4字、无「的」字结构、不以常见物名词尾结尾。"""
        if not (2 <= len(rn) <= 4):
            return False
        if "的" in rn:
            return False
        if rn[-1] in self._NON_PERSON_TAIL:
            return False
        return True

    def _work_body_text(self, work: str, max_chars: int = 200000) -> str:
        """拼接作品全部章节正文（截断上限），用于「正文是否出现过该角色」的判定。"""
        parts: list[str] = []
        total = 0
        for p in sorted(self.chapters_dir(work).glob("*.txt")):
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if total + len(t) > max_chars:
                parts.append(t[: max_chars - total])
                break
            parts.append(t)
            total += len(t)
        return "\n".join(parts)

    def _card_all_text(self, card: dict) -> str:
        """把整张人设卡的所有字段文本拼起来，供整卡扫描。"""
        parts: list[str] = []

        def walk(v):
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                for it in v:
                    walk(it)
            elif isinstance(v, dict):
                for it in v.values():
                    walk(it)

        walk(card)
        return "\n".join(parts)

    def _card_foreign_names(self, card: dict, known: set[str], body_text: str) -> list[str]:
        """从整张卡（所有字段）里提取「像人名但不在 known ∪ 正文」的对象名。

        用于揪出临时人物卡里夹带的未出场/未提到人物关联。
        """
        if not isinstance(card, dict):
            return []
        candidates: set[str] = set()
        bg = card.get("background") if isinstance(card.get("background"), dict) else {}
        rels = bg.get("relations") or []
        if isinstance(rels, list):
            for r in rels:
                if isinstance(r, dict):
                    rn = str(r.get("name") or "").strip()
                    if rn and not rn.startswith("（"):
                        candidates.add(rn)
        text = self._card_all_text(card)
        for pat in self._RELATION_PATTERNS:
            for m in pat.finditer(text):
                nm = m.group(1).strip()
                if nm:
                    candidates.add(nm)
        suspects: list[str] = []
        for nm in sorted(candidates):
            if nm.endswith("们"):
                nm = nm[:-1]
            if not nm or nm in self._RELATION_COMMON_WORDS:
                continue
            if nm in known or nm in body_text:
                continue
            if self._looks_like_person_name(nm):
                suspects.append(nm)
        return suspects

    def check_card_foreign(self, card: dict, work: str) -> list[str]:
        """单张卡即时自检：卡里提到的、不在大纲名单/卡名/正文里的人物名。"""
        if not isinstance(card, dict) or not work:
            return []
        outline = self.load_outline(work)
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []
        known: set[str] = set()
        for c in meta.get("characters") or []:
            if isinstance(c, dict) and c.get("name"):
                known.add(str(c["name"]).strip())
            elif isinstance(c, str) and c.strip():
                known.add(c.strip())
        for it in chapters:
            if isinstance(it, dict) and isinstance(it.get("characters"), list):
                for c in it["characters"]:
                    if str(c).strip():
                        known.add(str(c).strip())
        known |= set(self.list_work_characters(work))
        body_text = self._work_body_text(work)
        return self._card_foreign_names(card, known, body_text)

    def audit_characters(self, work: str) -> dict[str, Any]:
        """复核当前作品的人设卡与大纲/正文的一致性。

        返回结构化报告：
        - stale: 有卡但大纲零出场**且正文零出现**的角色（无关卡混入候选）
        - foreign_relations: {卡名: [无关角色名]}，卡里 relations 引用了像人名但不在
          大纲/正文/卡名名单里的对象（疑似混入旧大纲或别的作品的角色）
        - chapter_chars: 大纲各章出场角色合集（用于对照）
        - meta_chars: 大纲 meta.characters 里的角色名
        """
        outline = self.load_outline(work)
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        chapters = outline.get("chapters") or []

        meta_chars: set[str] = set()
        for c in meta.get("characters") or []:
            if isinstance(c, dict) and c.get("name"):
                meta_chars.add(str(c["name"]).strip())
            elif isinstance(c, str) and c.strip():
                meta_chars.add(c.strip())

        chapter_chars: set[str] = set()
        for it in chapters:
            if isinstance(it, dict) and isinstance(it.get("characters"), list):
                for c in it["characters"]:
                    if str(c).strip():
                        chapter_chars.add(str(c).strip())

        all_roles = meta_chars | chapter_chars
        body_text = self._work_body_text(work)
        card_names = self.list_work_characters(work)
        stale: list[str] = []
        for n in card_names:
            if n not in all_roles and n not in body_text:
                stale.append(n)

        foreign_relations: dict[str, list[str]] = {}
        known = all_roles | set(card_names)
        for n in card_names:
            card = self.load_character(n, work)
            if not card:
                continue
            bg = card.get("background") if isinstance(card.get("background"), dict) else {}
            rels = bg.get("relations") or []
            if not isinstance(rels, list):
                continue
            suspects: list[str] = []
            for r in rels:
                if not isinstance(r, dict):
                    continue
                rn = str(r.get("name") or "").strip()
                if rn.startswith("（"):
                    continue
                if rn and rn not in known and rn not in body_text:
                    if self._looks_like_person_name(rn):
                        suspects.append(rn)
            if suspects:
                foreign_relations[n] = suspects

        foreign_mentions: dict[str, list[str]] = {}
        for n in card_names:
            card = self.load_character(n, work)
            if not card:
                continue
            suspects = self._card_foreign_names(card, known, body_text)
            if suspects:
                foreign_mentions[n] = suspects

        return {
            "stale": stale,
            "foreign_relations": foreign_relations,
            "foreign_mentions": foreign_mentions,
            "chapter_chars": sorted(chapter_chars),
            "meta_chars": sorted(meta_chars),
        }

    def render_character_audit(self, work: str) -> str:
        """把人物卡复核报告渲染成可读文本。"""
        rep = self.audit_characters(work)
        lines = [f"【《{work}》人物卡复核】"]
        stale = rep["stale"]
        foreign = rep["foreign_relations"]
        foreign_mentions = rep.get("foreign_mentions") or {}
        if stale:
            lines.append("⚠️ 疑似无关卡（有卡但大纲与正文零出现）：")
            lines.append("、".join(f"「{s}」" for s in stale))
        foreign_merged: dict[str, list[str]] = {}
        for card in sorted(set(foreign) | set(foreign_mentions)):
            merged = sorted(set(foreign.get(card, [])) | set(foreign_mentions.get(card, [])))
            if merged:
                foreign_merged[card] = merged
        if foreign_merged:
            lines.append("🔗 卡里引用了名单外的角色（疑似夹带未出场/未提到人物的关联）：")
            for card, names in foreign_merged.items():
                lines.append(f"  「{card}」→ {'、'.join(names)}")
        if not stale and not foreign_merged:
            lines.append("人设卡与大纲一致，没有发现无关卡混入♪")
        return "\n".join(lines)
