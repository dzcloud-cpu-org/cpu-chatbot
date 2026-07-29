"""
backend/utils/exceptions.py

Redis 장애는 RedisUnavailableError를 정의만 해두고 라우터까지 전파하지 않는다
(backend/services/redis_service.py 내부에서 흡수하는 fail-open 정책 — 캐시
장애가 챗봇 기능 전체를 막아서는 안 된다).

LambdaInvocationError/LambdaTimeoutError는 이제 이 프로젝트의 "LLM 오류"에
해당한다 — 실제 OpenAI 호출은 Lambda 안에서 일어나고, FastAPI 입장에서는
그 Lambda 호출 자체가 실패/지연하는 것으로 관측되기 때문이다.
"""

from __future__ import annotations


class ChatbotError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MongoUnavailableError(ChatbotError):
    code = "MONGO_UNAVAILABLE"
    http_status = 503


class RedisUnavailableError(ChatbotError):
    """캐시 계층 장애. redis_service.py 내부에서만 사용되고 라우터까지 전파되지 않는다."""

    code = "REDIS_UNAVAILABLE"
    http_status = 503


class LambdaInvocationError(ChatbotError):
    """API Gateway 호출이 실패(네트워크 오류, 4xx/5xx 등)한 경우."""

    code = "LAMBDA_INVOCATION_ERROR"
    http_status = 502


class LambdaTimeoutError(ChatbotError):
    code = "LAMBDA_TIMEOUT"
    http_status = 504
