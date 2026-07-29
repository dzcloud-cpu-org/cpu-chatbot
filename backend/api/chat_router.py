"""
backend/api/chat_router.py

Client가 직접 부르는 엔드포인트. 내부적으로 API Gateway 뒤의 Lambda를 호출하지만
클라이언트 입장에서는 여전히 FastAPI의 POST /chat 하나만 알면 된다.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.models.schemas import ChatRequest, success_response
from backend.services.chatbot_service import ChatbotService

router = APIRouter(tags=["Chat"])

_service = ChatbotService()


@router.post("/chat")
async def chat(request: ChatRequest) -> dict:
    result = await _service.chatbot_service(request.question)
    return success_response(result)
