"""
lambda/guardrail/input_guardrail.py

Input Guardrail. 이번 계약({"question": ..., "context": ...})에서는 raw
question이 별도 필드로 그대로 도착하므로(이전 버전처럼 System Prompt와
합쳐진 큰 문자열이 아님), 팝업 서비스 화이트리스트 검사가 다시 의미를 갖는다
— FastAPI(backend/services/chatbot_service.py::check_topic)가 이미 1차로
걸러내지만, Lambda가 API Gateway를 통해 직접/단독으로 호출되는 경우까지
대비한 2차 방어선이다.

검사 항목:
- Prompt Injection / 시스템 프롬프트 변경 시도
- 욕설 및 비정상 입력(과도하게 긴 입력)
- 서비스와 무관한 질문(오프토픽)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

BLOCKED_MESSAGE = "죄송합니다.\n\n해당 요청은 처리할 수 없습니다."
NOT_POPUP_QUESTION_MESSAGE = "죄송합니다.\n\n저는 팝업스토어 관련 질문만 답변할 수 있습니다."

MAX_QUESTION_LENGTH = 500

# 기존 utils/guardrail.py::POPUP_KEYWORDS와 동일한 목록 (독립 배포를 위해 자체 보유).
_POPUP_KEYWORDS = [
    "팝업", "팝업스토어", "전시", "브랜드", "굿즈", "캐릭터",
    "성수", "홍대", "강남", "잠실", "명동",
    "더현대", "현대백화점", "롯데월드몰", "아이파크몰",
    "예약", "행사", "스토어",
    "팝업명", "운영시간", "웨이팅", "URL", "운영", "주소", "위치", "기간",
]

_PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(the\s+)?previous\s+instructions",
    r"ignore\s+all\s+(previous|prior)\s+instructions",
    r"disregard\s+(the\s+)?(above|previous)",
    r"이전\s*(지시|명령|지침)\s*(을|를)?\s*(무시|잊)",
    r"지금까지의?\s*(지시|명령|지침).{0,5}(무시|취소)",
    r"시스템\s*프롬프트",
    r"system\s*prompt",
    r"너는\s*이제부터",
    r"you\s+are\s+now",
    r"act\s+as\s+(an?|the)",
    r"jailbreak",
    r"\bdan\b",
    r"관리자\s*권한",
    r"admin\s*(권한|모드|access)",
    r"sudo",
    r"root\s*권한",
    r"개발자\s*모드",
    r"developer\s*mode",
]

_SQL_INJECTION_PATTERNS = [
    r"union\s+select",
    r"drop\s+table",
    r"drop\s+database",
    r"insert\s+into",
    r"delete\s+from",
    r"or\s+1\s*=\s*1",
    r"'\s*or\s*'?1'?\s*=\s*'?1",
    r"--\s*$",
    r";\s*drop\b",
    r"xp_cmdshell",
]

_PROFANITY_WORDS = ["씨발", "개새끼", "병신", "지랄", "미친놈", "미친년"]

_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in _PROMPT_INJECTION_PATTERNS]
_COMPILED_SQLI = [re.compile(p, re.IGNORECASE) for p in _SQL_INJECTION_PATTERNS]


@dataclass
class GuardrailResult:
    blocked: bool
    reason: Optional[str] = None
    message: Optional[str] = None


def _is_popup_question(question: str) -> bool:
    return any(keyword in question for keyword in _POPUP_KEYWORDS)


def check_input(question: str) -> GuardrailResult:
    if len(question) > MAX_QUESTION_LENGTH:
        return GuardrailResult(blocked=True, reason="abnormal_input", message=BLOCKED_MESSAGE)

    for pattern in _COMPILED_INJECTION:
        if pattern.search(question):
            return GuardrailResult(blocked=True, reason="prompt_injection", message=BLOCKED_MESSAGE)

    for pattern in _COMPILED_SQLI:
        if pattern.search(question):
            return GuardrailResult(blocked=True, reason="sql_injection", message=BLOCKED_MESSAGE)

    if any(word in question for word in _PROFANITY_WORDS):
        return GuardrailResult(blocked=True, reason="profanity", message=BLOCKED_MESSAGE)

    if not _is_popup_question(question):
        return GuardrailResult(blocked=True, reason="off_topic", message=NOT_POPUP_QUESTION_MESSAGE)

    return GuardrailResult(blocked=False)
