"""P2 单测：python tests/test_p2.py（无需 pytest / astrbot）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.prosecheck import check_prose, detect_sublimation, pronoun_stats
from core.constraintops import overlay_constraints, render_layered
from core import Sandbox


def test_pronoun():
    s = pronoun_stats("他看她。她看他。" * 50)  # 200 字 200 次
    assert s["he"] == 100 and s["she"] == 100 and s["you"] == 0 and s["per_kilo"] == 500.0
    s2 = pronoun_stats("你看他，他看你。" * 40)  # 320 字，你×80 他×80
    assert s2["you"] == 80 and s2["he"] == 80 and s2["per_kilo"] == 500.0
    assert pronoun_stats("短")["total_chars"] < 100


def test_sublimation():
    prose = "\n\n".join([
        "她把伞收起来，抖了抖水，走进屋里把灯打开。",
        "雨停了。仿佛整个世界都安静了下来。",
        "「你回来了。」他说。",
        "命运的齿轮开始转动。",
        "他数着台阶上楼，一层，两层，到门口摸出钥匙，插进锁孔，转了半圈。",
    ])
    hits = detect_sublimation(prose)
    tails = " ".join(h["tail"] for h in hits)
    assert "世界都安静" in tails and "命运的齿轮" in tails
    assert len(hits) == 2  # 正常叙述两句不误伤
    rep = check_prose(prose)
    assert rep["pronoun"]["total_chars"] > 0 and len(rep["sublimation"]) == 2


def test_overlay():
    g = {"length": "800~1500字", "scale": "露骨", "rhythm": "慢热", "banned_words": ["x"]}
    w = {"rhythm": "单场只推进一个自然节点", "scale": ""}
    ov = overlay_constraints(g, w)
    assert ov["rhythm"] == ("单场只推进一个自然节点", "work")
    assert ov["scale"] == ("露骨", "global")       # 空串不覆盖
    assert ov["banned_words"] == (["x"], "global")  # 未在作品级设置
    text = render_layered(ov)
    assert "（作品级）" in text and "慢热" not in text


def test_sandbox_layers():
    with tempfile.TemporaryDirectory() as td:
        sb = Sandbox(Path(td))
        sb.set_active_work("测试书")
        sb.save_work_constraints("测试书", {"rhythm": "快节奏", "length": ""})
        text = sb.render_layered_constraints("测试书")
        assert "快节奏（作品级）" in text
        assert "perspective" in text  # 全局键透传
        loaded = sb.load_work_constraints("测试书")
        assert loaded["rhythm"] == "快节奏" and "length" not in loaded  # 空值不入库
        few = sb.render_style_fewshot()
        assert few == ""  # 无风格卡时空串，不报错


if __name__ == "__main__":
    test_pronoun(); test_sublimation(); test_overlay(); test_sandbox_layers()
    print("test_p2: ALL PASS")
