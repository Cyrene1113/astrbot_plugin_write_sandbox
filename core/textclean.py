"""完稿深层清洗（P0-T7）：剥离正文段落级的元信息残留。

独立模块、不依赖 astrbot。只清洗「整行命中」的元信息：
分节标题（## xxx（2/3））、独立章节标题行、剧情状态标记、括号注记、引导语。
规则取向：宁可漏判、不可误伤对白与叙述。
"""
from __future__ import annotations

import re

# ① 分节标题行：任意内容 + 末尾 (n/m) 计数，可带 markdown 井号
_RE_SECTION_HEADER = re.compile(
    r"^\s*#{0,6}\s*[^\n]{0,60}?[（(]\s*\d+\s*/\s*\d+\s*[)）]\s*$"
)
# ② 独立章节标题行：第N章 后必须紧跟一个分隔符（空格/·/：/-/—/】）。
#    中文行文不用空格，「第三章的内容…」这类叙述在「章」后紧跟汉字，不会命中。
_RE_CHAPTER_TITLE = re.compile(
    r"^\s*#{0,6}\s*(?:第[0-9零一二两三四五六七八九十百千]+\s*章|Chapter\s*\d+)"
    r"[\s·:：\-—】][^\n]{1,30}$"
)
# ③ 剧情状态标记残留（任意位置整行）
_RE_STORY_MARK = re.compile(r"^\s*【?\s*(?:STORY_END|STORY_CONTINUE)\s*】?\s*$", re.I)
# ④ 独立括号注记行（不限前 8 行）
_RE_NOTE_LINE = re.compile(r"^[（(]\s*(?:注|说明|备注|提示|小记)[：:][^）)]{0,60}[）)]\s*$")
# ⑤ 引导语整行
_RE_LEADIN_LINE = re.compile(
    r"^\s*(?:好的|好|嗯|那么|ok|OK)?[，,:：\s]*"
    r"(?:以下为正文|以下是正文|正文如下|正文开始|正文内容如下|以下内容为正文)[：:，,。\s]?\s*$"
)

_SENTENCE_END = ("。", "！", "？", "…", "!", "?", "”", "」", "』")
_QUOTE_CHARS = ("\"", "“", "”", "「", "『", "《")


def line_is_meta(line: str, isolated: bool = True) -> bool:
    """判断单行是否为元信息残留（供 scrub 清单展示复用）。
    isolated=该行独立成段（前后是空行/文件边缘）时才认章节标题。"""
    s = line.strip()
    if not s:
        return False
    if _strong_meta(s):
        return True
    if isolated and _RE_CHAPTER_TITLE.match(s):
        if not any(q in s for q in _QUOTE_CHARS) and s[-1] not in _SENTENCE_END:
            return True
    return False


def deep_clean_sections(text: str) -> str:
    """逐行剥离元信息残留并压缩多余空行。每节捕获时调用。

    两遍法：第一遍剥强模式（无需上下文）；第二遍在幸存行里剥「独立标题行」
    （上一行为空或行首即视为独立，避免被同批剥掉的引导语干扰孤立判定）。
    """
    if not text:
        return text
    # 第一遍：强模式（分节标题 / 状态标记 / 注记 / 引导语）
    kept = [ln for ln in text.split("\n") if not _strong_meta(ln)]
    # 第二遍：独立章节标题行
    out_lines: list[str] = []
    for i, ln in enumerate(kept):
        if ln.strip() and _RE_CHAPTER_TITLE.match(ln.strip()):
            prev_blank = (i == 0) or (not kept[i - 1].strip())
            s = ln.strip()
            if prev_blank and not any(q in s for q in _QUOTE_CHARS) and s[-1] not in _SENTENCE_END:
                continue
        out_lines.append(ln)
    out = "\n".join(out_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _strong_meta(line: str) -> bool:
    """强元信息模式：整行命中即剥，不需要孤立性佐证。"""
    s = line.strip()
    if not s:
        return False
    if _RE_STORY_MARK.match(s) or _RE_NOTE_LINE.match(s) or _RE_LEADIN_LINE.match(s):
        return True
    if _RE_SECTION_HEADER.match(s):
        # 带 # 前缀必是标题；无 # 的（n/m）结尾行要求不含引号（防误伤对白）
        return s.startswith("#") or not any(q in s for q in _QUOTE_CHARS)
    return False
