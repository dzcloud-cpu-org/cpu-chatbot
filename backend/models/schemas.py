"""
backend/models/schemas.py

Client ↔ FastAPI 계약. FastAPI ↔ Lambda 계약은 별도로
backend/services/lambda_client.py(발신)와 lambda/models/schemas.py(수신)에서 다룬다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, description="사용자 질문")


class ChatSource(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None


class ChatData(BaseModel):
    question: str
    intent: Optional[str] = None
    search_condition: Optional[dict] = None
    results_count: int = 0
    answer: str
    sources: list[ChatSource] = []
    cached: bool = False


class ChatResponse(BaseModel):
    success: bool = True
    data: ChatData


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody


def success_response(data: dict) -> dict:
    return {"success": True, "data": data}


def error_response(code: str, message: str) -> dict:
    return {"success": False, "error": {"code": code, "message": message}}
