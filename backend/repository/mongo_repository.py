"""
backend/repository/mongo_repository.py

기존 top-level services/mongo_service.py(search_popup_by_condition,
build_popup_info_list)와 search_condition.py(build_search_condition)를 그대로
재사용한다 — FastAPI는 이 전체 저장소(모노레포)를 통째로 컨테이너 이미지에
포함해 배포하므로(Dockerfile이 COPY . . 하고 있음) 이 top-level 모듈들을
그대로 import해도 문제 없다 (Lambda처럼 별도 zip으로 독립 배포되는 게 아님).
"""

from __future__ import annotations

from typing import Any

from pymongo.errors import PyMongoError

from backend.utils.exceptions import MongoUnavailableError
from search_condition import build_search_condition
from services.mongo_service import build_popup_info_list, search_popup_by_condition

MAX_RESULTS_FOR_LLM = 3


def _deduplicate(results: list[dict]) -> list[dict]:
    seen: set[Any] = set()
    unique: list[dict] = []
    for popup in results:
        popup_id = popup.get("source_url")
        if popup_id not in seen:
            seen.add(popup_id)
            unique.append(popup)
    return unique


def search_popups(question: str) -> tuple[dict, list[dict]]:
    """질문 → 검색조건 생성 → MongoDB 검색 → 중복제거/개수제한 → Lambda 전달용 JSON 축소."""
    search_condition = build_search_condition(question)

    try:
        raw_results = search_popup_by_condition(search_condition, limit=5)
    except PyMongoError as e:
        raise MongoUnavailableError(f"MongoDB 조회 중 오류가 발생했습니다: {e}") from e

    results = _deduplicate(raw_results)[:MAX_RESULTS_FOR_LLM]
    popup_info_list = build_popup_info_list(results, search_condition)

    return search_condition, popup_info_list
