"""LLM 调用错误分类（P0-T1）：统一错误类别，供上层决定重试/降级策略。

独立模块、不依赖 astrbot，可被 tests/test_p0.py 直接导入。
"""
from __future__ import annotations

import asyncio


class LLMCallError(Exception):
    TIMEOUT = "timeout"                    # 超时（流式空闲超时 / 总超时）
    RATE_LIMIT = "rate_limit"              # 限流 / 429 / 配额
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文超限（重试无意义，应裁剪注入）
    PROVIDER = "provider_error"            # 其他供应商侧错误
    EMPTY = "empty_response"               # 调用成功但内容为空

    _KIND_CN = {
        TIMEOUT: "超时",
        RATE_LIMIT: "限流",
        CONTEXT_OVERFLOW: "上下文超限",
        PROVIDER: "供应商错误",
        EMPTY: "空响应",
    }

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    def __str__(self) -> str:
        head = self._KIND_CN.get(self.kind, self.kind)
        return f"{head}（{self.kind}）" + (f": {self.detail[:200]}" if self.detail else "")


def classify_llm_error(exc: BaseException, detail: str = "") -> LLMCallError:
    """把任意异常映射为 LLMCallError；detail 可覆盖异常文本。"""
    msg = detail or f"{type(exc).__name__}: {exc}"
    low = msg.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) \
            or "timeout" in low or "timed out" in low or "超时" in msg:
        return LLMCallError(LLMCallError.TIMEOUT, msg)
    if ("429" in low or "too many requests" in low or "quota" in low
            or "rate limit" in low or "rate_limit" in low or "限流" in msg):
        return LLMCallError(LLMCallError.RATE_LIMIT, msg)
    overflow_kw = ("context length", "maximum context", "context window",
                   "token limit", "max_tokens", "too long", "请求过长",
                   "上下文超限", "payload too large")
    if any(k in low or k in msg for k in overflow_kw):
        return LLMCallError(LLMCallError.CONTEXT_OVERFLOW, msg)
    return LLMCallError(LLMCallError.PROVIDER, msg)
