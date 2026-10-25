"""宽松 JSON 解析纯函数（交接 §5-9：设定书/大纲/人设卡等 LLM JSON 输出的健壮提取）。

不依赖 astrbot。设计取向：先无损（围栏剥离/平衡括号提取），再有损（尾逗号清理/截断修复）；
宁可返回 None 让上层走重试，也不抛异常。不修补引号内的内容（中文弯引号等是合法字符串值）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def strip_json_fences(text: str) -> str:
    """剥离 Markdown 代码块围栏（```json … ``` / 无语言标注 / 只有尾围栏）。"""
    t = text.strip()
    if "```" not in t:
        return t
    m = re.search(r"```(?:json|JSON)?[ \t]*\r?\n?(.*?)```", t, re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return re.sub(r"```[a-zA-Z]*\r?\n?", "", t).replace("```", "").strip()


def extract_balanced(text: str, open_ch: str = "{", close_ch: str = "}") -> Optional[str]:
    """字符串感知的平衡括号提取：取第一个完整的 {…}（引号内的括号/转义不计数）。
    比贪婪的 {.*} 强：前后有杂文、文本里有多个对象、对象后有解释文字时都能取对。"""
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def repair_truncated(text: str) -> Optional[str]:
    """截断修复：在最后一个「字符串外的安全逗号」（任意深度）处截断——逗号必然位于
    完整值之后，截断点之前的成员全部完好；丢弃悬挂的不完整键值，再补齐未闭合的
    字符串与括号。只救得了丢尾巴，救不了丢开头；完全无逗号可用返回 None。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    last_comma: Optional[int] = None
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        elif c == "," and depth >= 1:
            last_comma = i
    if last_comma is None:
        return None
    out = text[start:last_comma].rstrip().rstrip(",")
    stack: list[str] = []
    in_str = False
    esc = False
    for c in out:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            if stack:
                stack.pop()
    if in_str:
        out += '"'
    for b in reversed(stack):
        out += "}" if b == "{" else "]"
    return out


def _clean_trailing_commas(t: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", t)


def parse_json_loose(text: str) -> Optional[Any]:
    """宽松解析：直接 loads → 平衡提取 → 尾逗号清理 → 截断修复。全部失败返回 None。"""
    if not text or not text.strip():
        return None
    t = strip_json_fences(text)
    candidates: list[str] = [t]
    b = extract_balanced(t)
    if b:
        candidates.append(b)
        candidates.append(_clean_trailing_commas(b))
    candidates.append(_clean_trailing_commas(t))
    r = repair_truncated(t)
    if r:
        candidates.append(r)
        candidates.append(_clean_trailing_commas(r))
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


def parse_json_object(text: str) -> Optional[dict]:
    """parse_json_loose 且要求结果为 dict；否则 None。"""
    parsed = parse_json_loose(text)
    return parsed if isinstance(parsed, dict) else None
