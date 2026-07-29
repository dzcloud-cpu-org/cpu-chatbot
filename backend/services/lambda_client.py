"""
backend/services/lambda_client.py

FastAPI가 API Gateway를 거쳐 Lambda(AI 계층)를 호출하는 HTTP 클라이언트.

    response = requests.post(
        API_GATEWAY_URL,
        json={"question": question, "context": context},
        headers={"x-api-key": API_KEY},
        timeout=30,
    )

요청받은 예시는 동기(sync) `requests`를 쓰지만, 이 프로젝트의 FastAPI 엔드포인트
(`backend/api/chat_router.py`)는 전부 `async def`다. 동기 `requests.post()`를
async 함수 안에서 그대로 호출하면 호출 스레드가 블로킹되어 이벤트 루프 전체가
그 요청이 끝날 때까지 다른 요청을 처리하지 못한다(동시 처리량 저하). 그래서
동일한 파라미터(URL/JSON/헤더/타임아웃)를 그대로 유지하되, 논블로킹으로 동작하는
`httpx.AsyncClient`로 구현했다 — 요청 예시와 결과적으로 동일한 HTTP 호출을
만들지만, 이벤트 루프를 막지 않는다.

# 인증 방식: API Gateway API Key (x-api-key 헤더)

FastAPI가 K8s(온프레미스 포함)에서 실행되고 Lambda는 AWS에 있으므로, 매 요청
AWS 자격증명으로 서명해야 하는 IAM SigV4보다 헤더 하나(x-api-key)만 실으면
되는 API Key + Usage Plan 방식이 훨씬 단순하다. 더 강한 인증이 필요해지면
`_build_headers()`만 SigV4 서명 로직으로 교체하면 된다.
"""

from __future__ import annotations

import httpx

from backend.config.settings import settings
from backend.utils.exceptions import LambdaInvocationError, LambdaTimeoutError
from backend.utils.logger import get_logger, timed

log = get_logger(__name__)


def _build_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if settings.LAMBDA_API_KEY:
        headers["x-api-key"] = settings.LAMBDA_API_KEY
    return headers


async def invoke_chat(question: str, context: str) -> str:
    """
    API Gateway의 POST /chat(Lambda 프록시)을 호출해 답변을 받아온다.

    요청 바디: {"question": str, "context": str}
    응답 바디(정상): {"answer": str}
    응답 바디(Lambda 측 오류, 4xx/5xx): {"error": {"code": str, "message": str}}

    LAMBDA_API_URL이 아직 설정되지 않은 경우(Kubernetes 이전 작업 중이라 실제
    API Gateway URL이 없는 지금 같은 상황) 네트워크 호출 자체를 시도하지 않고
    바로 LambdaInvocationError를 던져 chatbot_service.py가 친화적 메시지로
    응답하게 한다 — 나중에 LAMBDA_API_URL/LAMBDA_API_KEY 값만 채우면 이 함수는
    코드 변경 없이 그대로 실제 API Gateway를 호출한다.
    """
    if not settings.LAMBDA_API_URL:
        raise LambdaInvocationError("LAMBDA_API_URL이 설정되지 않았습니다.")

    payload = {"question": question, "context": context}

    try:
        with timed(log, "lambda_invoke", question=question):
            async with httpx.AsyncClient(timeout=settings.LAMBDA_TIMEOUT_SECONDS) as client:
                response = await client.post(settings.LAMBDA_API_URL, json=payload, headers=_build_headers())
    except httpx.TimeoutException as e:
        raise LambdaTimeoutError(
            f"Lambda 응답이 {settings.LAMBDA_TIMEOUT_SECONDS}초 내에 도착하지 않았습니다."
        ) from e
    except httpx.HTTPError as e:
        raise LambdaInvocationError(f"Lambda 호출 중 네트워크 오류가 발생했습니다: {e}") from e

    if response.status_code >= 400:
        raise LambdaInvocationError(
            f"Lambda가 오류를 반환했습니다 (status={response.status_code}): {response.text[:300]}"
        )

    try:
        body = response.json()
    except ValueError as e:
        raise LambdaInvocationError("Lambda 응답을 JSON으로 파싱할 수 없습니다.") from e

    answer = body.get("answer")
    if not answer:
        raise LambdaInvocationError("Lambda 응답에 answer 필드가 없습니다.")

    return answer
