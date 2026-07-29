"""
backend/api/error_handlers.py

ChatbotError 계열(MongoUnavailableError/LambdaInvocationError/LambdaTimeoutError)을
표준 JSON 에러 응답으로 변환한다. MongoDB 결과 없음은 예외가 아니라 정상 200
응답으로 처리하므로(chatbot_service.py 참고) 여기서 다루지 않는다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.models.schemas import error_response
from backend.utils.exceptions import ChatbotError
from backend.utils.logger import get_logger

log = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ChatbotError)
    async def _handle_chatbot_error(request: Request, exc: ChatbotError) -> JSONResponse:
        log.error(
            "chatbot_error",
            extra={"extra_fields": {"code": exc.code, "message": exc.message, "path": request.url.path}},
        )
        return JSONResponse(status_code=exc.http_status, content=error_response(exc.code, exc.message))

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unexpected_error",
            extra={"extra_fields": {"error": str(exc), "path": request.url.path}},
            exc_info=exc,
        )
        return JSONResponse(status_code=500, content=error_response("INTERNAL_ERROR", "예상치 못한 오류가 발생했습니다."))
