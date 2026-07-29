"""
lambda/guardrail/output_guardrail.py

OpenAI가 생성한 답변을 API Gateway로 돌려주기 전 마지막 검사/정제.

검사 항목:
- 허위 정보 제거 / MongoDB Context 외 내용 생성 방지
  → 완전한 hallucination 탐지는 정규식으로 불가능하므로, 실무적으로 검증
    가능한 대리 지표(proxy)인 "URL/PII가 context에 실제로 존재하는지"로
    판별한다. context에 없는 URL/PII는 지어냈을 가능성이 높으므로 제거/마스킹한다.
- 금지어 필터링
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FALLBACK_MESSAGE = "죄송합니다.\n\n답변을 생성하는 중 문제가 발생했습니다. 다시 질문해주세요."

_PROFANITY_WORDS = ["씨발", "개새끼", "병신", "지랄", "미친놈", "미친년"]
_BANNED_WORDS = ["도박", "대출광고", "성인광고", "불법복제"]

_PHONE_PATTERN = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
_RRN_PATTERN = re.compile(r"\d{6}-?[1-4]\d{6}")
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_PATTERN = re.compile(r"https?://[^\s]+")


@dataclass
class OutputGuardrailResult:
    answer: str
    blocked: bool = False
    flags: list[str] = field(default_factory=list)


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _strip_ungrounded_urls(answer: str, context: str) -> tuple[str, bool]:
    found_ungrounded = False

    def _replace(match: "re.Match[str]") -> str:
        nonlocal found_ungrounded
        url = match.group(0)
        if url in context or url.rstrip(".,)") in context:
            return url
        found_ungrounded = True
        return ""

    cleaned = _URL_PATTERN.sub(_replace, answer)
    return cleaned, found_ungrounded


def _mask_ungrounded_pii(answer: str, context: str) -> tuple[str, bool]:
    masked = False

    def _mask_if_ungrounded(pattern: "re.Pattern[str]", label: str, text: str) -> str:
        nonlocal masked

        def _replace(match: "re.Match[str]") -> str:
            nonlocal masked
            value = match.group(0)
            if value in context:
                return value
            masked = True
            return f"[{label} 비공개]"

        return pattern.sub(_replace, text)

    result = answer
    result = _mask_if_ungrounded(_RRN_PATTERN, "주민등록번호", result)
    result = _mask_if_ungrounded(_PHONE_PATTERN, "전화번호", result)
    result = _mask_if_ungrounded(_EMAIL_PATTERN, "이메일", result)
    return result, masked


def check_output(answer: str, context: str) -> OutputGuardrailResult:
    """context: FastAPI가 보낸 MongoDB Context 문자열(JSON) — 근거 판정 기준."""
    flags: list[str] = []

    if _contains_any(answer, _PROFANITY_WORDS):
        return OutputGuardrailResult(answer=FALLBACK_MESSAGE, blocked=True, flags=["profanity"])

    if _contains_any(answer, _BANNED_WORDS):
        return OutputGuardrailResult(answer=FALLBACK_MESSAGE, blocked=True, flags=["banned_word"])

    cleaned, had_ungrounded_url = _strip_ungrounded_urls(answer, context)
    if had_ungrounded_url:
        flags.append("ungrounded_url_removed")

    cleaned, had_pii = _mask_ungrounded_pii(cleaned, context)
    if had_pii:
        flags.append("pii_masked")

    return OutputGuardrailResult(answer=cleaned, blocked=False, flags=flags)
