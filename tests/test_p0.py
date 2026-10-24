"""P0 纯函数单测：python tests/test_p0.py（无需 pytest / astrbot）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llmerrors import LLMCallError, classify_llm_error
from core.textclean import deep_clean_sections, line_is_meta
from core.outlineops import merge_outline_batch, missing_chapters

def test_textclean():
    src = "\n".join([
        "## 网游之夜·第3章 出林第一课（2/3）",
        "正文第一段。",
        "",
        "【STORY_END】",
        "（注：这里节奏放慢）",
        "以下为正文：",
        "第3章 出林第一课",
        "她开口：“第三章的内容她背得滚瓜烂熟”",
        "第三章的内容她背得滚瓜烂熟，一个字都没漏。",
        "正文最后一段。",
    ])
    out = deep_clean_sections(src)
    assert "（2/3）" not in out
    assert "STORY_END" not in out
    assert "（注：" not in out
    assert "以下为正文" not in out
    assert "第3章 出林第一课\n" not in out  # 独立标题行被剥
    assert "她背得滚瓜烂熟" in out          # 带引号/标点的叙述不误伤
    assert "一个字都没漏" in out            # 紧跟「第三章」的叙述不误伤
    assert out.startswith("正文第一段") and out.endswith("正文最后一段。")
    assert line_is_meta("## 某章 标题（1/2）")
    assert not line_is_meta("她说：“走吧（1/2）。”")  # 引号保护

def test_classify():
    assert classify_llm_error(TimeoutError()).kind == LLMCallError.TIMEOUT
    assert classify_llm_error(RuntimeError("HTTP 429 too many requests")).kind == LLMCallError.RATE_LIMIT
    assert classify_llm_error(RuntimeError("maximum context length is 8192 tokens")).kind == LLMCallError.CONTEXT_OVERFLOW
    assert classify_llm_error(ValueError("boom")).kind == LLMCallError.PROVIDER

def test_outline_merge():
    cur = {"meta": {"title": "T", "premise": "P"}, "chapters": [{"ch": 1, "summary": "a"}]}
    b2 = {"chapters": [{"ch": 3, "summary": "c"}, {"ch": 2, "summary": "b"}]}
    m = merge_outline_batch(cur, b2)
    assert [c["ch"] for c in m["chapters"]] == [1, 2, 3]
    m2 = merge_outline_batch(m, {"chapters": [{"ch": 2, "summary": "b2"}]})
    assert [c["summary"] for c in m2["chapters"] if c["ch"] == 2] == ["b2"]  # 同号覆盖
    assert m2["meta"]["premise"] == "P"                                     # meta 不丢
    assert missing_chapters(m) == []
    assert missing_chapters({"chapters": [{"ch": 1}, {"ch": 4}]}) == [2, 3]
    assert missing_chapters({"chapters": []}) == []

if __name__ == "__main__":
    test_textclean(); test_classify(); test_outline_merge()
    print("test_p0: ALL PASS")
