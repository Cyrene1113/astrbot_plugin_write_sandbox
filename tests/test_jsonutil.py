"""jsonutil 宽松 JSON 解析单测：python tests/test_jsonutil.py（无需 pytest / astrbot）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.jsonutil import (
    extract_balanced, parse_json_loose, parse_json_object, repair_truncated,
)


def test_clean():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('  \n{"a": 1}\n  ') == {"a": 1}


def test_fenced():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```\n希望符合要求。') == {"a": 1}
    assert parse_json_object('好的，以下是更新后的设定书：\n```json\n{"meta": {"title": "X"}}\n```\n如需调整请说。') == {"meta": {"title": "X"}}


def test_prose_around():
    raw = '根据旧设定书与新章节，更新如下：{"world": {"A": "B"}, "characters": []} 以上。'
    assert parse_json_object(raw) == {"world": {"A": "B"}, "characters": []}


def test_braces_in_strings():
    raw = '{"note": "面板显示{HP:100}，注意转义 \\" 引号", "n": 1}'
    assert parse_json_object(raw)["note"] == "面板显示{HP:100}，注意转义 \" 引号"


def test_trailing_comma():
    assert parse_json_object('{"a": [1, 2,], "b": {"c": 3,},}') == {"a": [1, 2], "b": {"c": 3}}


def test_truncated_salvage():
    # 模拟输出被 max_tokens 截断：完整成员保住、最后一条不完整记录救回已完成的键
    raw = '{"meta": {"title": "测"}, "world": {"A": "B"}, "characters": [{"name": "汐", "状态": "在茶室"'
    out = parse_json_object(raw)
    assert out is not None
    assert out["meta"]["title"] == "测" and out["world"] == {"A": "B"}
    assert out["characters"] == [{"name": "汐"}]
    # 截断发生在嵌套对象内部：整个不完整成员被丢弃，前面的成员保住
    raw2 = '{"meta": {"title": "测"}, "world": {"A": "B", "C": {"deep": "v"'
    out2 = parse_json_object(raw2)
    assert out2 is not None and out2["world"] == {"A": "B"}
    # 截断发生在字符串中间
    raw3 = '{"meta": {"title": "测作"}, "unresolved": ["线索一", "线索'
    out3 = parse_json_object(raw3)
    assert out3 is not None and out3["unresolved"] == ["线索一"]


def test_repair_direct():
    assert repair_truncated('{"a": 1, "b": [1,') is not None
    assert extract_balanced('前言 {"a": {"b": 1}} 后言 {"c": 2}') == '{"a": {"b": 1}}'


def test_garbage_returns_none():
    assert parse_json_object("") is None
    assert parse_json_object("模型拒绝输出任何结构化内容。") is None
    assert parse_json_loose("[]") == []  # list 也放行（parse_json_loose）
    assert parse_json_object("[]") is None


def test_object_helper():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('[1, 2]') is None


if __name__ == "__main__":
    test_clean(); test_fenced(); test_prose_around(); test_braces_in_strings()
    test_trailing_comma(); test_truncated_salvage(); test_repair_direct()
    test_garbage_returns_none(); test_object_helper()
    print("test_jsonutil: ALL PASS")
