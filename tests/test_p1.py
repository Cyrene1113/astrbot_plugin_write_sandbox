"""P1 纯函数 + Sandbox 级单测：python tests/test_p1.py（无需 pytest / astrbot）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cardops import (
    detect_meta_sentences, identity_names, merge_cards, normalize_card,
    scrub_meta_lines, validate_card_v2,
)
from core import Sandbox


def _v1_card():
    return {
        "basic": {"name": "汐", "aliases": ["汐小姐"], "personality": ["冷静", "内敛"],
                  "appearance": "白发蓝紫异色瞳", "quirks": ["泡茶讲究"]},
        "background": {"origin": "魔女", "secrets": ["习惯保留秘密"],
                       "relations": [{"name": "德谬歌", "relation": "挚友", "note": "第8章后正式认识"}]},
        "story": {"role": "配角", "arc": "第8章夜测试凡尘之坠后淡出主线",
                  "plot_hooks": ["契约从何而来"], "scale_role": "中"},
        "speech": {"tone": "温和", "catchphrases": ["茶要烧到正好"]},
        "meta": {"scope": "work", "schema_version": "1.0"},
    }


def test_normalize():
    card, moved = normalize_card(_v1_card())
    assert "arc" not in card["story"] and "plot_hooks" not in card["story"]
    assert moved["arc"].startswith("第8章") and moved["plot_hooks"] == ["契约从何而来"]
    assert card["meta"]["schema_version"] == "2.0"
    assert card["meta"]["canonical_id"] == "汐"
    assert card["persona"]["sample_dialogue"] == []


def test_validate():
    card, _ = normalize_card(_v1_card())
    ok, problems = validate_card_v2(card)  # 配角档：anchors 0 < 1 → 打回
    assert not ok and any("外貌锚点" in p for p in problems)
    card["persona"]["appearance_anchors"] = ["白发", "蓝紫异色瞳", "月白长袍"]
    card["persona"]["sample_dialogue"] = ["「茶要烧到正好。」"]
    ok, problems = validate_card_v2(card)
    assert ok, problems
    # 主角档加码：性格不足 3 条、台词不足 2 句
    card["story"]["role"] = "主角"
    card["basic"]["personality"] = ["冷静", "内敛", "克制"]
    ok, problems = validate_card_v2(card)
    assert not ok and any("示例台词" in p for p in problems)
    # 回配角档：台词空但口头禅有 → 回退档放行
    card["story"]["role"] = "配角"
    card["persona"]["sample_dialogue"] = []
    ok, problems = validate_card_v2(card)
    assert ok, problems
    # 剧情字段回流 → 拒绝
    card["story"]["plot_hooks"] = ["x"]
    ok, _ = validate_card_v2(card)
    assert not ok


def test_identity_merge():
    a = {"basic": {"name": "水瓶座小姐（汐）", "aliases": ["水瓶座小姐"],
                   "appearance": "白发蓝紫异色瞳", "personality": ["冷静"]},
         "meta": {"canonical_id": "水瓶座小姐（汐）"}}
    b = _v1_card()
    b, _ = normalize_card(b)
    assert "汐小姐" in identity_names(b)
    m = merge_cards(b, a)
    assert "水瓶座小姐（汐）" in m["basic"]["aliases"]
    assert m["basic"]["appearance"]  # 空位被补
    assert m["meta"]["canonical_id"] == "汐"


def test_meta_detect():
    prose = "\n".join([
        "正文开始，她推开茶室的门。",
        "（第8章后两人正式认识，此为伏笔）",
        "「主线任务：寻找鉴定师」——面板亮起。",
        "",
        "这也是作者埋下的悬念。",
        "",
        "她端起茶杯。",
        "他想起前文里她说过的那句话，第3章的雨夜。",
    ])
    hits = detect_meta_sentences(prose)
    autos = [h for h in hits if h["kind"] == "auto"]
    suspects = [h for h in hits if h["kind"] == "suspect"]
    assert any("伏笔" in h["line"] for h in autos)          # 独立注记行 → auto
    assert any("作者" in h["line"] for h in autos)           # 独立成段的作者语言 → auto
    assert all("主线任务" not in h["line"] for h in autos)   # 引号内游戏面板不 auto
    assert any("第3章" in h["line"] for h in suspects)       # 叙述内嵌 → suspect
    out, removed = scrub_meta_lines(prose)
    assert removed == 2 and "伏笔" not in out and "主线任务" in out and "作者" not in out


def test_render_purpose():
    with tempfile.TemporaryDirectory() as td:
        sb = Sandbox(Path(td))
        card, _ = normalize_card(_v1_card())
        card["persona"]["appearance_anchors"] = ["白发", "蓝紫异色瞳"]
        card["persona"]["sample_dialogue"] = ["「茶要烧到正好。」"]
        sb.save_character(card, "work", "测试书")
        full = sb.render_character(card)                 # 默认 full：管理员视角
        write = sb.render_character(card, purpose="write")  # 写作注入
        assert "秘密" in full and "秘密" not in write     # secrets 不进写作
        assert "第8章后正式认识" in full and "第8章" not in write  # note 裁剪
        assert "「茶要烧到正好。」" in write              # 台词注入
        found = sb.find_character_by_identity("汐小姐", "测试书")
        assert found is not None and found["basic"]["name"] == "汐"


if __name__ == "__main__":
    test_normalize(); test_validate(); test_identity_merge()
    test_meta_detect(); test_render_purpose()
    print("test_p1: ALL PASS")
