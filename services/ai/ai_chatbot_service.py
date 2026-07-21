# services/ai_chatbot_service.py

"""
AI 검색형 챗봇 서비스

처리 흐름
==========================================================
사용자 질문
    │
    ▼
0) Guardrail          - 팝업과 무관한 질문 즉시 차단 (AI/Mongo 호출 절약)
    │
    ▼
1) 검색 조건 생성      - search_condition.build_search_condition()
                         (임베딩 유사도 + 규칙 기반, AI 호출 없음)
                         자연어 질문 → 검색 조건(dict) 생성
    │
    ▼
2) MongoDB 검색        - services/mongo_service.search_popup_by_condition()
                         (search_condition의 date_filter를 서버에서
                          실제 날짜 범위로 변환하여 쿼리에 반영)
    │
    ▼
3) 결과 정제            - 중복 제거 + 상위 N개 제한
                       + mongo_service.build_popup_info_list()로
                         질문과 관련된 필드만 남긴 JSON으로 축소
    │
    ▼
4) AI 호출 (1회뿐)     - CHATBOT_RESPONSE_PROMPT
                         검색 결과(JSON)만 근거로 자연어 답변 생성
    │
    ▼
최종 응답 (question, intent, search_condition, results, answer)
==========================================================

※ 예전에는 1)번 검색 조건 생성에도 AI를 호출해서 질문당 AI 호출이 2회였다.
   지금은 1)번이 임베딩 유사도 + 규칙 기반으로 바뀌면서 AI 호출이 없고,
   AI는 4)번 최종 답변 생성 1회만 호출한다 (토큰/비용 절반 이하로 감소).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from prompts.chatbot_prompts import (
    CHATBOT_RESPONSE_PROMPT,
    build_chatbot_answer_user_prompt,
)

# AI는 이제 최종 답변 생성(4단계) 1곳에서만 호출한다.
from services.ai.ai_factory import (
    generate_chat_response,
    AI_PROVIDER,
)

from services.mongo_service import build_popup_info_list, search_popup_by_condition
from search_condition import build_search_condition
from utils.guardrail import is_popup_question

# 검색 결과 중 AI에게 실제로 전달할 최대 개수.
# 너무 많이 전달하면 입력 토큰이 늘고 답변 품질이 떨어지므로 상위 N개만 사용한다.
MAX_RESULTS_FOR_AI = 3

NO_RESULT_MESSAGE = "죄송합니다.\n\n조건에 맞는 팝업스토어를 찾지 못했습니다."
NOT_POPUP_QUESTION_MESSAGE = "죄송합니다.\n\n저는 팝업스토어 관련 질문만 답변할 수 있습니다."


# =============================================================================
# 1단계: 검색 조건 생성 (AI 호출 없음)
# =============================================================================

async def _build_search_condition(user_question: str) -> Dict[str, Any]:
    """
    build_search_condition()은 임베딩 계산(CPU 연산)이 포함되어 있어
    이벤트 루프를 막지 않도록 asyncio.to_thread로 별도 스레드에서 실행한다.
    """
    return await asyncio.to_thread(build_search_condition, user_question)


# =============================================================================
# 2단계: 검색 결과 중복 제거
# =============================================================================

def _deduplicate(results: List[dict]) -> List[dict]:
    """
    source_url을 기준으로 중복 팝업을 제거한다.
    (동일 팝업이 여러 키워드 조건에 동시에 매칭되어 중복 조회될 수 있음)
    """

    seen = set()
    unique_results = []

    for popup in results:
        popup_id = popup.get("source_url")
        if popup_id not in seen:
            seen.add(popup_id)
            unique_results.append(popup)

    return unique_results


# =============================================================================
# 3단계: 최종 답변 생성 (AI는 여기서 1회만 호출)
# =============================================================================

async def _build_final_answer(
    user_question: str,
    search_condition: Dict[str, Any],
    popup_info_list: List[dict],
) -> str:
    """
    AI 모델에게 CHATBOT_RESPONSE_PROMPT(system) + 사용자 질문/의도/검색결과(user)를
    전달하여 최종 자연어 답변을 생성한다.

    popup_info_list는 build_popup_info_list()가 만든, 질문과 관련된 필드만
    남긴 결과라서 build_chatbot_answer_user_prompt() 내부에서 그대로
    JSON으로 직렬화되어 전달된다.
    """

    user_prompt = build_chatbot_answer_user_prompt(
        user_question=user_question,
        search_condition=search_condition,
        popup_info=popup_info_list,
    )

    answer = await generate_chat_response(
        system_prompt=CHATBOT_RESPONSE_PROMPT,
        user_prompt=user_prompt,
        temperature=0.4,
    )

    return answer


# =============================================================================
# 전체 파이프라인 (라우터에서 호출하는 진입점)
# =============================================================================

async def generate_ai_search_response(user_question: str) -> Dict[str, Any]:
    """
    AI 검색형 챗봇의 전체 파이프라인을 실행한다.

    반환값에는 최종 답변(answer)뿐 아니라
    1단계에서 생성된 search_condition, 검색된 결과 개수도 함께 담아
    디버깅/프론트엔드 표시에 활용할 수 있게 했다.
    """

    total_start = time.perf_counter()

    # -------------------------------------------------------
    # 0) Guardrail
    #
    # 팝업스토어와 무관한 질문은 검색/AI를
    # 아예 호출하지 않고 즉시 차단한다. (응답 속도 + 비용 절감)
    # -------------------------------------------------------
    if not is_popup_question(user_question):
        print("[Guardrail] 팝업 관련 질문이 아님")
        return {
            "question": user_question,
            "intent": None,
            "search_condition": None,
            "results_count": 0,
            "answer": NOT_POPUP_QUESTION_MESSAGE,
            "sources": [],
        }

    # -------------------------------------------------------
    # 1) 검색 조건 생성 (임베딩 유사도 + 규칙 기반, AI 호출 없음)
    # -------------------------------------------------------
    condition_start = time.perf_counter()

    try:
        search_condition = await _build_search_condition(user_question)
    except Exception as e:
        print(f"[1단계 실패] 검색 조건 생성 오류: {e}")
        return {
            "question": user_question,
            "intent": None,
            "search_condition": None,
            "results_count": 0,
            "answer": "죄송합니다.\n\n질문을 분석하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            "sources": [],
        }

    print(f"[TIME] 검색 조건 생성(임베딩+규칙, AI 호출 없음) : {time.perf_counter() - condition_start:.4f}초")
    print("생성된 검색 조건 :", search_condition)

    # -------------------------------------------------------
    # 2) MongoDB 검색
    #
    # search_condition의 date_filter 값("today"/"this_week"/null)을
    # 실제 날짜 범위로 변환하는 작업은 mongo_service.py에서 수행한다.
    # -------------------------------------------------------
    mongo_start = time.perf_counter()

    search_results = search_popup_by_condition(search_condition, limit=5)

    print(f"[TIME] MongoDB 검색 : {time.perf_counter() - mongo_start:.4f}초")

    # 중복 제거 + 상위 N개만 사용
    search_results = _deduplicate(search_results)
    search_results = search_results[:MAX_RESULTS_FOR_AI]

    # 질문(intent/keywords)과 관련된 필드만 남긴 JSON으로 축소
    # (주소/카테고리/전체 content/웨이팅 등은 실제로 물어본 경우에만 포함됨)
    popup_info_list = build_popup_info_list(search_results, search_condition)

    print("=" * 80)
    print(f"[{AI_PROVIDER.upper()}] 최종 답변 생성 단계")
    print("검색 조건 :", search_condition)
    print("popup_info(JSON, AI 전달용) :", popup_info_list)
    print("사용자 질문 :", user_question)
    print("검색 결과 개수 :", len(search_results))
    for idx, popup in enumerate(search_results, start=1):
        print(f"[{idx}] {popup.get('title')}")
    print("=" * 80)

    # -------------------------------------------------------
    # 검색 결과가 없으면 AI 호출 없이 즉시 응답한다.
    # (CHATBOT_RESPONSE_PROMPT 규칙 7번과 동일한 문구를
    #  코드 레벨에서 먼저 처리해 불필요한 AI 호출을 줄인다.)
    # -------------------------------------------------------
    if not search_results:
        return {
            "question": user_question,
            "intent": search_condition.get("intent"),
            "search_condition": search_condition,
            "results_count": 0,
            "answer": NO_RESULT_MESSAGE,
            "sources": [],
        }

    # source_url은 build_popup_info_list()가 항상 포함시키는 기본 필드이므로
    # 여기서 그대로 꺼내 프론트엔드가 바로 <a href> 링크를 만들 수 있게 제공한다.
    # (AI가 답변 텍스트 안에 적은 URL을 프론트에서 정규식으로 다시 파싱할 필요가 없어짐)
    sources = [
        {"title": p.get("title"), "url": p.get("source_url")}
        for p in popup_info_list
        if p.get("source_url")
    ]

    # -------------------------------------------------------
    # 3) 최종 답변 생성 (AI는 파이프라인 전체에서 여기 1번만 호출된다)
    # -------------------------------------------------------
    answer_start = time.perf_counter()

    try:
        answer = await _build_final_answer(user_question, search_condition, popup_info_list)
    except Exception as e:
        print(f"[답변 생성 실패] {e}")
        return {
            "question": user_question,
            "intent": search_condition.get("intent"),
            "search_condition": search_condition,
            "results_count": len(search_results),
            "answer": "죄송합니다.\n\n답변을 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            "sources": sources,
        }

    print(f"[TIME] 답변 생성(AI 1회 호출) : {time.perf_counter() - answer_start:.4f}초")
    print(f"[TIME] 전체 처리 : {time.perf_counter() - total_start:.4f}초")

    return {
        "question": user_question,
        "intent": search_condition.get("intent"),
        "search_condition": search_condition,
        "results_count": len(search_results),
        "answer": answer,
        "sources": sources,
    }
