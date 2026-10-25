"""离线端到端验证（P4 修复批次的逻辑项）：python tests/test_e2e_offline.py。

用 astrbot 桩 + 假 LLM 驱动真实 WriteMixin 全链路，覆盖交接文档 §5 末尾
「待真机复验」中所有逻辑类行为；真机只余模型质量与框架集成。
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.stub_astrbot import install_stub

install_stub()  # 必须先于 import handlers

# handlers 内部用 `..core` 相对导入，需把仓库根挂成带父名的包再导入
import types
_ROOT = Path(__file__).resolve().parent.parent
_PKG = types.ModuleType("wsroot")
_PKG.__path__ = [str(_ROOT)]
sys.modules["wsroot"] = _PKG

from astrbot.api.star import Star  # 桩

from wsroot.handlers import BaseMixin, CharacterMixin, LearnMixin, MiscMixin, OutlineMixin, WriteMixin

DATA_ENV = "ASTRBOT_DATA_DIR"


class Ctx:
    def __init__(self):
        self.sent = []

    def get_using_provider(self):
        return None

    async def send_message(self, session, chain):
        self.sent.append(chain)


class Ev:
    unified_msg_origin = "test:origin"

    def __init__(self, uid="u1"):
        self._uid = uid

    def get_sender_id(self):
        return self._uid


class Plugin(BaseMixin, WriteMixin, OutlineMixin, CharacterMixin, LearnMixin, MiscMixin, Star):
    pass


def make_plugin(data_dir: Path):
    os.environ[DATA_ENV] = str(data_dir)
    return Plugin(Ctx())


def install_fake_llm(plugin, routes):
    """routes: [(子串, 响应)]，响应可为 str 或 callable(prompt)->str。命中即返回。"""
    calls = []

    async def fake_chat_ex(prompt, system_prompt="", timeout=0.0):
        calls.append(prompt)
        for key, resp in routes:
            if key in prompt:
                text = resp(prompt) if callable(resp) else resp
                return text, None
        return "", None

    plugin._llm_chat_ex = fake_chat_ex
    return calls


BEATS_JSON = json.dumps({"beats": [
    {"scene": "茶室对峙", "goal_chars": 900, "turn": "摊牌", "carry": "从本章开头进入"},
    {"scene": "雨夜街头", "goal_chars": 1100, "turn": "决裂", "carry": "接上一拍"},
    {"scene": "天台收束", "goal_chars": 1000, "turn": "各自离开", "carry": "接上一拍"},
]}, ensure_ascii=False)

STATE_JSON = json.dumps({"meta": {"title": "测作"}, "world": {}, "characters": [],
                         "foreshadowing": [], "unresolved": []}, ensure_ascii=False)


def beat_section(prompt: str) -> str:
    if "第 1/3 拍" in prompt:
        return "第一拍正文，两人对峙。【STORY_END】"  # 模拟模型违规提前收章
    if "第 2/3 拍" in prompt:
        return "第二拍正文，雨夜决裂。"
    if "第 3/3 拍" in prompt:
        return "第三拍正文，天台收束。"
    return "收尾段，故事落幕。【STORY_END】"


WORK_ROUTES = [
    ("场景调度师", BEATS_JSON),
    ("【场景 beat】", beat_section),
    ("【分节写作】", lambda p: "收尾段，故事落幕。【STORY_END】"),
    ("是否已经自然收尾", "否"),
    ("剧情摘要员", "第一章摘要：三拍推进，决裂收束。"),
    ("设定书管理员", STATE_JSON),
    ("剧情连续性检查员", json.dumps({"diverge": False, "note": ""}, ensure_ascii=False)),
    ("小说编辑", "[]"),
]


async def test_beat_flow():
    """C1+C2 修复验证：beat 非末拍不收章、不询问收尾标记、违规 STORY_END 被忽略、beats 落盘大纲。"""
    with tempfile.TemporaryDirectory() as td:
        plugin = make_plugin(Path(td))
        ev = Ev()
        sb = plugin._get_sandbox(ev)
        sb.save_outline("测作", {"meta": {"title": "测作"}, "chapters": [
            {"ch": 1, "summary": "对峙与决裂", "plot_points": ["摊牌", "决裂", "收束"], "characters": []}
        ]})
        calls = install_fake_llm(plugin, WORK_ROUTES)
        await plugin.sandbox_write(ev, instruction="写第一章", mode="generate", work="测作", chapter=1)
        task = plugin._jobs["u1"]["task"]
        await task
        job = plugin._jobs["u1"]
        assert job["status"] == "done", job.get("error")

        # ①违规 STORY_END 没有收掉整章：4 段全部写完并落盘
        ch_files = list(sb.chapters_dir("测作").glob("ch01*.txt"))
        assert len(ch_files) == 1, ch_files
        full = ch_files[0].read_text(encoding="utf-8")
        for seg in ("第一拍正文", "第二拍正文", "第三拍正文", "收尾段"):
            assert seg in full, seg

        # ②beats 已落盘大纲（修复前静默丢失）
        beats = sb.get_chapter_beats("测作", 1)
        assert len(beats) == 3 and beats[0]["scene"] == "茶室对峙"

        # ③非末拍 prompt 不询问收尾标记；末拍与超出 beat 的节仍询问
        p1 = next(p for p in calls if "第 1/3 拍" in p)
        p2 = next(p for p in calls if "第 2/3 拍" in p)
        p3 = next(p for p in calls if "第 3/3 拍" in p)
        for p in (p1, p2):
            assert "不要输出任何剧情状态标记" in p, p[-200:]
            assert "STORY_END" not in p
        assert "【STORY_END】" in p3 and "【STORY_CONTINUE】" in p3

        # ④末拍未打标记 → 收尾判断兜底被调用；非末拍不调用
        assert sum(1 for p in calls if "是否已经自然收尾" in p) == 1

        # ⑤摘要与设定书照常更新
        assert sb.load_chapter_summaries("测作")[0]["summary"].startswith("第一章摘要")
        assert sb.load_story_state("测作")["meta"]["title"] == "测作"


async def test_short_resume():
    """C3 修复验证：短篇分节复用同一文件，失败后 resume 接回完整前文。"""
    with tempfile.TemporaryDirectory() as td:
        plugin = make_plugin(Path(td))
        plugin._SECTION_RETRY = 0  # 加速失败路径
        ev = Ev()
        from wsroot.core.llmerrors import LLMCallError

        def sec1(_p):
            return "第一节正文，雨夜开场。【STORY_CONTINUE】"

        def sec_fail(_p):
            raise RuntimeError("不应再走到成功路由")

        async def fail_chat_ex(prompt, system_prompt="", timeout=0.0):
            if "第 1/1 节" in prompt:
                return "第一节正文，雨夜开场。【STORY_CONTINUE】", None
            return "", LLMCallError(LLMCallError.TIMEOUT, "模拟第2节起超时")

        plugin._llm_chat_ex = fail_chat_ex
        await plugin.sandbox_write(ev, instruction="写一段雨夜短篇", mode="generate")
        task = plugin._jobs["u1"]["task"]
        await task
        job = plugin._jobs["u1"]
        assert job["status"] == "failed", job["status"]
        first_file = Path(job["file_path"])
        assert "第一节正文" in first_file.read_text(encoding="utf-8")

        # 模拟重启：新实例读同一数据目录
        plugin2 = make_plugin(Path(td))
        plugin2._SECTION_RETRY = 0
        calls2 = install_fake_llm(plugin2, [
            ("【分节写作】", lambda p: "第二节正文，雨渐大。【STORY_CONTINUE】" if "第 2/1 节" in p
                else "第三节正文，天亮收束。【STORY_END】"),
        ])
        res = await plugin2.sandbox_write(Ev(), resume="job1")
        assert "已恢复任务 job1" in res, res
        await plugin2._jobs["u1"]["task"]
        job2 = plugin2._jobs["u1"]
        assert job2["status"] == "done", job2.get("error")

        # ①仍只有一份产出文件（复用而非另开新文件）②三节齐全 ③resume 接的是原文件
        outputs = list((Path(os.environ[DATA_ENV]) / "plugin_data" /
                        "astrbot_plugin_write_sandbox" / "u1" / "outputs").glob("*.txt"))
        assert len(outputs) == 1, outputs
        full = outputs[0].read_text(encoding="utf-8")
        for seg in ("第一节正文", "第二节正文", "第三节正文"):
            assert seg in full, seg
        assert job2["file_path"] == str(first_file)


async def test_update_scope_and_merge_guard():
    """B1 踩坑 + C4 验证：update 按 scope 精确查卡；merge 跨 scope 拒绝。"""
    with tempfile.TemporaryDirectory() as td:
        plugin = make_plugin(Path(td))
        ev = Ev()
        sb = plugin._get_sandbox(ev)
        sb.save_outline("测作", {"meta": {}, "chapters": []})
        g_card = {"basic": {"name": "汐", "gender": "未设定"}, "meta": {}}
        w_card = {"basic": {"name": "汐", "gender": "男"}, "meta": {}}
        sb.save_character(json.loads(json.dumps(g_card)), "global")
        sb.save_character(json.loads(json.dumps(w_card)), "work", "测作")

        # scope=global：只改全局卡，作品卡不动
        res = await plugin.sandbox_character(ev, action="update", name="汐",
                                             scope="global", work="测作", description="性别改为 女")
        assert "已更新" in res or "人设卡已更新" in res, res
        assert sb.load_character("汐", "")["basic"]["gender"] == "女"
        assert sb.load_character("汐", "测作", work_only=True)["basic"]["gender"] == "男"

        # scope=work：只改作品卡
        await plugin.sandbox_character(ev, action="update", name="汐",
                                       scope="work", work="测作", description="性别改为 女")
        assert sb.load_character("汐", "测作", work_only=True)["basic"]["gender"] == "女"

        # scope 查不到时给出跨 scope 提示
        res = await plugin.sandbox_character(ev, action="update", name="不存在的人",
                                             scope="global", work="测作", description="性别改为 女")
        assert "找不到" in res

        # merge 跨 scope 拒绝
        sb.save_character({"basic": {"name": "路人甲"}, "meta": {}}, "global")
        res = await plugin.sandbox_character(ev, action="merge", name="汐", merge_from="路人甲",
                                             work="测作")
        assert "不在同一存储范围" in res, res
        assert sb.load_character("路人甲", "") is not None  # 未被误删


async def test_perspective_default():
    from wsroot.core.constants import DEFAULT_CONSTRAINTS
    p = DEFAULT_CONSTRAINTS["perspective"]
    assert "第二人称主视角" != p and "风格卡" in p


async def main():
    await test_beat_flow()
    await test_short_resume()
    await test_update_scope_and_merge_guard()
    await test_perspective_default()
    print("test_e2e_offline: ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
