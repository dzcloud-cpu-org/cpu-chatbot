"""
lambda/lambda_handler.py

AWS Lambda entrypoint (Python 3.12, AWS SAM Handler: lambda_handler.handler).

이 Lambda는 AI 처리만 담당한다 — Redis/MongoDB 연결이나 DB 조회는 전혀 하지
않는다. FastAPI가 API Gateway를 통해 넘겨준 {question, context}만으로
답변을 만든다.

    API Gateway → handler(event, context)
        │
        ▼
    Input Validation (Pydantic ChatRequest)
        │
        ▼
    process_chat_request()
        ├── Input Guardrail   (guardrail/input_guardrail.py)
        ├── Prompt 생성        (prompts/system_prompt.py)
        ├── OpenAI GPT 호출    (llm/client.py — 기존 services/ai/openai_service.py 재사용)
        └── Output Guardrail  (guardrail/output_guardrail.py)
        │
        ▼
    {"answer": "..."} (API Gateway 프록시 응답으로 감싸서 반환)

# "lambda" 디렉터리와 Python 예약어

Python에서 `lambda`는 예약어라 `import lambda.guardrail...`처럼 점(dot)으로
참조하는 일반적인 import 문은 SyntaxError가 난다. 디렉터리 이름 자체는
`lambda/`를 그대로 쓰되, 아래 import들은 이 디렉터리 자체가 배포 패키지
루트(AWS SAM `CodeUri: lambda/`)라는 전제로 `guardrail`, `llm`, `prompts`를
최상위 모듈처럼 참조한다. 로컬 pytest에서도 동일하게 동작하도록
`tests/conftest.py`가 `lambda/` 디렉터리를 `sys.path`에 추가한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from guardrail.input_guardrail import check_input
from guardrail.output_guardrail import check_output
from llm.client import OpenAICallError, OpenAITimeoutError, generate_chat_response
from prompts.system_prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ChatRequest(BaseModel):
    """POST /chat 요청 바디: {"question": "...", "context": "..."}"""

    question: str = Field(..., min_length=1)
    context: str = ""


async def process_chat_request(question: str, context: str) -> str:
    """event 파싱과 분리된 순수 로직 — 로컬 테스트에서 event/context 없이 직접 호출 가능."""

    # 1) Input Guardrail
    guard_result = check_input(question)
    if guard_result.blocked:
        logger.info("input_guardrail_blocked reason=%s", guard_result.reason)
        return guard_result.message or ""

    # 2) Prompt 생성
    user_prompt = build_user_prompt(question, context)

    # 3) OpenAI GPT 호출 (기존 호출 코드 재사용, lambda/llm/client.py 참고)
    raw_answer = await generate_chat_response(SYSTEM_PROMPT, user_prompt)

    # 4) Output Guardrail
    result = check_output(raw_answer, context)
    if result.flags:
        logger.info("output_guardrail_flagged flags=%s", result.flags)

    return result.answer


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event: dict, context: Any) -> dict:
    """API Gateway(REST API, Lambda 프록시 통합) entrypoint."""

    # --- Input Validation ---
    try:
        raw_body = event.get("body") or "{}"
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": {"code": "VALIDATION_ERROR", "message": "요청 본문을 JSON으로 파싱할 수 없습니다."}})

    try:
        request = ChatRequest(**payload)
    except ValidationError as e:
        return _response(400, {"error": {"code": "VALIDATION_ERROR", "message": str(e)}})

    # --- Guardrail + Prompt 생성 + OpenAI 호출 + Guardrail ---
    try:
        answer = asyncio.run(process_chat_request(request.question, request.context))
    except OpenAITimeoutError as e:
        logger.error("openai_timeout %s", e)
        return _response(504, {"error": {"code": "LLM_TIMEOUT", "message": str(e)}})
    except OpenAICallError as e:
        logger.error("openai_call_error %s", e)
        return _response(502, {"error": {"code": "LLM_ERROR", "message": str(e)}})
    except Exception as e:  # noqa: BLE001 - Lambda 최상위 핸들러는 모든 예외를 표준 응답으로 변환해야 한다.
        logger.error("unexpected_error %s", e)
        return _response(500, {"error": {"code": "INTERNAL_ERROR", "message": "예상치 못한 오류가 발생했습니다."}})

    return _response(200, {"answer": answer})
