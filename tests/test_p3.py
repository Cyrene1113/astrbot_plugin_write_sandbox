"""P3 单测：python tests/test_p3.py（无需 pytest / astrbot）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.inject import assemble_injection, pick_to_archive
from core import Sandbox


def test_assemble():
    parts = [(2, "记忆", "M" * 3000), (0, "大纲", "O" * 2000), (1, "角色", "C" * 3000)]
    out = assemble_injection(parts, 6000)
    assert "OOOO" in out.replace("O", "O") and "大纲" not in out  # 只拼接正文，不含名
    total = len(out)
    assert total <= 6100, total
    assert all(x in out for x in ("O",))  # 高优先级保留
    assert "因注入预算省略" in out        # 低优先级被丢并标注
    out_full = assemble_injection(parts, 0)
    assert "M" * 100 in out_full and "C" * 100 in out_full  # budget=0 不截断
    assert assemble_injection([], 100) == ""
    one = assemble_injection([(1, "A", "X" * 500)], 100)
    assert one.startswith("X") and "截断" in one


def test_pick():
    assert pick_to_archive(list(range(1, 9)), keep=5, threshold=8) == []   # 8 章不归档
    assert pick_to_archive(list(range(1, 12)), keep=5, threshold=8) == [1, 2, 3, 4, 5, 6]
    assert pick_to_archive([], 5, 8) == []


def test_sandbox_p3():
    with tempfile.TemporaryDirectory() as td:
        sb = Sandbox(Path(td))
        sb.set_active_work("测作")
        # 大纲 + beats
        sb.save_outline("测作", {"meta": {}, "chapters": [
            {"ch": 1, "summary": "s1", "plot_points": ["a", "b"], "characters": ["汐", "昔涟"]},
            {"ch": 2, "summary": "s2", "plot_points": ["c"]},
        ]})
        beats = [{"scene": "茶室", "goal_chars": 900, "turn": "摊牌", "carry": "接上章结尾"},
                 {"scene": "雨夜街头", "goal_chars": 1100, "turn": "决裂", "carry": ""}]
        sb.set_chapter_beats("测作", 1, beats)
        assert sb.get_chapter_beats("测作", 1) == beats
        assert sb.get_chapter_beats("测作", 2) == []
        assert sb.chapter_character_names("测作", 1) == ["汐", "昔涟"]
        # 摘要分层
        for i in range(1, 11):
            sb.upsert_chapter_summary("测作", i, f"第{i}章摘要" * 20)
        pick = sb.pick_to_archive("测作")
        assert pick == [1, 2, 3, 4, 5]
        sb.save_arc_summary("测作", pick[0], pick[-1], "前五章弧线：起承转合。")
        sb.mark_summaries_archived("测作", pick)
        mem = sb.render_story_memory("测作")
        assert "弧线" in mem and "前五章弧线" in mem
        assert "第1章摘要" not in mem and "第10章摘要" in mem  # 归档的不再全量出现
        # focus_chars + world 截断
        sb.save_story_state("测作", {"meta": {"title": "测作", "current_chapter": 10},
                                     "world": {"设定A": "长" * 500},
                                     "characters": [{"name": "汐", "状态": "在茶室"},
                                                    {"name": "路人甲", "状态": "路过"}],
                                     "foreshadowing": [], "unresolved": []})
        mem2 = sb.render_story_memory("测作", focus_chars=["汐"])
        assert "在茶室" in mem2 and "汐" in mem2
        assert "路人甲" not in mem2.split("角色：汐")[0]  # 非焦点角色不抢焦点位
        assert "长" * 500 not in mem2  # world 被截断


if __name__ == "__main__":
    test_assemble(); test_pick(); test_sandbox_p3()
    print("test_p3: ALL PASS")
