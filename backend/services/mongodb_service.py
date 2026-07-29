"""
backend/services/mongodb_service.py

Service 계층 — Repository(mongo_repository.py)를 호출해 "Prompt Context"를
만든다. ChatbotService는 MongoDB 쿼리 방법(regex, $and/$or 등)을 몰라도 되고,
이 서비스가 만든 (search_condition, context) 튜플만 알면 된다.
"""

from __future__ import annotations

from backend.repository.mongo_repository import search_popups


def build_context(question: str) -> tuple[dict, list[dict]]:
    """
    질문 → (search_condition, context) 반환.
    context는 Lambda에게 그대로 전달되는 list[dict] (title/location/period/source_url 등).
    """
    return search_popups(question)
