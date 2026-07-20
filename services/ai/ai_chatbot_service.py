# services/ai_chatbot_service.py

"""
AI 검색형 챗봇 서비스 (2단계 Gemini 파이프라인)

기존 chatbot_service.py는
"규칙 기반 키워드 추출(utils/keyword_utils.py) → MongoDB 검색 → Gemini 답변 생성(1회 호출)"
구조였다.

이 서비스는 규칙 기반 키워드 추출 대신
AI 모델에게 "질문 분석"까지 맡기는 방식으로 확장한다.

처리 흐름
==========================================================
사용자 질문
    │
    ▼
0) Guardrail          - 팝업과 무관한 질문 즉시 차단 (Gemini/Mongo 호출 절약)
    │
    ▼
1) AI 1단계 호출   - SEARCH_CONDITION_PROMPT
                         자연어 질문 → 검색 조건(JSON) 생성
                         (date_filter 판단을 돕기 위해 오늘 날짜를 함께 전달)
    │
    ▼
2) MongoDB 검색        - services/mongo_service.search_popup_by_condition()
                         (search_condition의 date_filter를 서버에서
                          실제 날짜 범위로 변환하여 쿼리에 반영)
    │
    ▼
3) 결과 정제            - 중복 제거 + 상위 N개 제한
    │
    ▼
4) AI 2단계 호출   - CHATBOT_RESPONSE_PROMPT
                         검색 결과만 근거로 자연어 답변 생성
    │
    ▼
최종 응답 (question, intent, search_condition, results, answer)
==========================================================

각 단계를 별도 함수로 분리하여
어디서 병목/오류가 발생하는지 로그로 바로 확인할 수 있게 했다.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Dict, List

from prompts.chatbot_prompts import (
    CHATBOT_RESPONSE_PROMPT,
    SEARCH_CONDITION_PROMPT,
    build_chatbot_answer_user_prompt,
    build_search_condition_user_prompt,
)
# Gemini
#from services.gemini_service import (
#    GeminiAPIError,
#    generate_chat_response,
#    generate_structured_response,
#)

# Openai
from services.ai.ai_factory import (
    generate_chat_response,
    generate_structured_response,
    AI_PROVIDER
)

from services.mongo_service import search_popup_by_condition
from utils.guardrail import is_popup_question

# 검색 결과 중 AI(2단계)에게 실제로 전달할 최대 개수.
# 너무 많이 전달하면 입력 토큰이 늘고 답변 품질이 떨어지므로 상위 N개만 사용한다.
MAX_RESULTS_FOR_AI = 3

NO_RESULT_MESSAGE = "죄송합니다.\n\n조건에 맞는 팝업스토어를 찾지 못했습니다."
NOT_POPUP_QUESTION_MESSAGE = "죄송합니다.\n\n저는 팝업스토어 관련 질문만 답변할 수 있습니다."


# =============================================================================
# 1단계: 검색 조건 생성
# =============================================================================

async def _build_search_condition(user_question: str) -> Dict[str, Any]:
    """
    AI 모델에게 SEARCH_CONDITION_PROMPT(system) + 사용자 질문(user)을 전달하여
    MongoDB 검색 조건(JSON)을 생성한다.

    AI는 학습 시점 이후의 "현재 날짜"를 알 수 없으므로,
    "오늘"/"이번주" 같은 표현을 정확히 분류할 수 있도록
    서버에서 계산한 오늘 날짜(date.today())를 User Prompt에 함께 넣어준다.

    다만 AI가 직접 날짜 산술(예: 이번주 월~일 계산)을 하지는 않으며,
    date_filter를 "today" / "this_week" / null 중 하나로 분류하는 역할만 한다.
    실제 날짜 범위 계산 및 쿼리 변환은 mongo_service.py에서 서버가 처리한다.

    generate_structured_response()는 response_mime_type="application/json"으로
    호출되므로, 반환값은 이미 dict(JSON)로 파싱되어 있다.
    """

    today_str = date.today().strftime("%Y-%m-%d")

    user_prompt = build_search_condition_user_prompt(
        user_question=user_question,
        today_str=today_str,
    )

    search_condition = await generate_structured_response(
        system_prompt=SEARCH_CONDITION_PROMPT,
        user_prompt=user_prompt,
        # 검색 조건 생성은 "정답이 정해진" 작업에 가까우므로
        # 창의성보다 일관성이 중요하다 → temperature를 낮게 설정
        temperature=0.1,
    )

    return search_condition


# =============================================================================
# 2단계: MongoDB 검색 결과 → AI 프롬프트용 텍스트 변환
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


def _build_popup_info_text(results: List[dict]) -> str:
    """
    MongoDB 검색 결과를 AI(2단계)에게 전달할 텍스트로 변환한다.

    개선사항
    --------------------------------------------------------------------
    1. 운영시간(opening_hours) 추가
    2. 예약/추가 안내(additional_information) 추가
    3. 웨이팅 정보(waiting_info) 추가
    4. 상세 페이지 URL(source_url) 추가
    5. List/Dict 형태의 데이터를 사람이 읽기 쉬운 문자열로 변환
    --------------------------------------------------------------------
    """

    popup_info = ""

    for idx, popup in enumerate(results, start=1):

        # --------------------------------------------------
        # 운영시간(List → 문자열)
        # --------------------------------------------------
        opening_hours = popup.get("opening_hours") or []

        if opening_hours:
            opening_hours = "\n".join(opening_hours)
        else:
            opening_hours = "정보 없음"

        # --------------------------------------------------
        # 예약/추가 안내
        # --------------------------------------------------
        additional_information = (
            popup.get("additional_information")
            or "정보 없음"
        )

        # --------------------------------------------------
        # 웨이팅 정보(Dict → 문자열)
        # --------------------------------------------------
        waiting_info = popup.get("waiting_info") or {}

        waiting_message = waiting_info.get(
            "waiting_zone",
            "정보 없음"
        )

        # --------------------------------------------------
        # 상세 페이지 URL
        # --------------------------------------------------
        source_url = popup.get(
            "source_url",
            "정보 없음"
        )

        # --------------------------------------------------
        # AI 전달용 텍스트 생성
        # --------------------------------------------------
        popup_info += f"""
        [{idx}]

        팝업명 :
        {popup.get("title", "정보 없음")}

        위치 :
        {popup.get("region", "정보 없음")}

        주소 :
        {popup.get("address", "정보 없음")}

        기간 :
        {popup.get("start_date", "정보 없음")}
        ~
        {popup.get("end_date", "정보 없음")}

        카테고리 :
        {popup.get("category", "정보 없음")}

        운영시간 :
        {opening_hours}

        예약/안내 :
        {additional_information}

        웨이팅 :
        {waiting_message}

        상세 :
        {popup.get("content", "정보 없음")}

        상세페이지 :
        {source_url}

        ------------------------
        """

    return popup_info


# =============================================================================
# 3단계: 최종 답변 생성
# =============================================================================

async def _build_final_answer(
    user_question: str,
    search_condition: Dict[str, Any],
    results: List[dict],
) -> str:
    """
    AI 모델에게 CHATBOT_RESPONSE_PROMPT(system) + 사용자 질문/의도/검색결과(user)를
    전달하여 최종 자연어 답변을 생성한다.
    """

    popup_info = _build_popup_info_text(results)

    user_prompt = build_chatbot_answer_user_prompt(
        user_question=user_question,
        search_condition=search_condition,
        popup_info=popup_info,
    )

    answer = await generate_chat_response(
        system_prompt=CHATBOT_RESPONSE_PROMPT,
        user_prompt=user_prompt,
        # 답변 생성은 자연스러운 문장 표현이 중요하므로
        # 검색 조건 생성 단계보다 temperature를 조금 높게 설정
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
    # 팝업스토어와 무관한 질문은 AI(1단계/2단계)와 MongoDB를
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
        }

    # -------------------------------------------------------
    # 1) 검색 조건 생성 (AI 1단계)
    #
    # date_filter("today" / "this_week" / null)를 정확히 판단할 수 있도록
    # _build_search_condition() 내부에서 오늘 날짜를 함께 전달한다.
    # -------------------------------------------------------
    condition_start = time.perf_counter()

    try:
        search_condition = await _build_search_condition(user_question)
    except Exception as e:
        # 1단계 AI 호출 자체가 실패하면 검색을 진행할 수 없으므로
        # 사용자에게는 안내 메시지만 반환하고, 원인은 로그로 남긴다.
        print(f"[1단계 실패] 검색 조건 생성 오류: {e}")
        return {
            "question": user_question,
            "intent": None,
            "search_condition": None,
            "results_count": 0,
            "answer": "죄송합니다.\n\n질문을 분석하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        }

    print(f"[TIME] 검색 조건 생성(1단계 AI) : {time.perf_counter() - condition_start:.4f}초")
    print("생성된 검색 조건 :", search_condition)

    # -------------------------------------------------------
    # 2) MongoDB 검색
    #
    # search_condition의 date_filter 값("today"/"this_week"/null)을
    # 실제 날짜 범위로 변환하는 작업은 mongo_service.py에서 수행한다.
    # (AI는 분류만 하고, 날짜 산술은 서버가 담당)
    # -------------------------------------------------------
    mongo_start = time.perf_counter()

    search_results = search_popup_by_condition(search_condition, limit=5)

    print(f"[TIME] MongoDB 검색 : {time.perf_counter() - mongo_start:.4f}초")

    # 중복 제거 + 상위 N개만 사용
    search_results = _deduplicate(search_results)
    search_results = search_results[:MAX_RESULTS_FOR_AI]

    popup_info = _build_popup_info_text(search_results)

    print("=" * 80)
    print("AI Search condition")
    print(f"f{AI_PROVIDER.upper()} Search condition")  # AI 호출할 때마다 자동으로 해당 AI로 바뀐다.
    print(search_condition)
    print("popup_info")
    print(popup_info)
    print("사용자 질문 :", user_question)
    print("검색 조건 :", search_condition)
    print("검색 결과 개수 :", len(search_results))
    for idx, popup in enumerate(search_results, start=1):
        print(f"[{idx}] {popup.get('title')}")
    print("=" * 80)

    # -------------------------------------------------------
    # 검색 결과가 없으면 2단계 AI 호출 없이 즉시 응답한다.
    # (CHATBOT_RESPONSE_PROMPT 규칙 10번과 동일한 문구를
    #  코드 레벨에서 먼저 처리해 불필요한 AI 호출을 줄인다.)
    # -------------------------------------------------------
    if not search_results:
        return {
            "question": user_question,
            "intent": search_condition.get("intent"),
            "search_condition": search_condition,
            "results_count": 0,
            "answer": NO_RESULT_MESSAGE,
        }

    # -------------------------------------------------------
    # 3) 최종 답변 생성 (AI 2단계)
    # -------------------------------------------------------
    answer_start = time.perf_counter()

    try:
        answer = await _build_final_answer(user_question, search_condition, search_results)
    except Exception as e:
        print(f"[2단계 실패] 답변 생성 오류: {e}")
        return {
            "question": user_question,
            "intent": search_condition.get("intent"),
            "search_condition": search_condition,
            "results_count": len(search_results),
            "answer": "죄송합니다.\n\n답변을 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        }

    print(f"[TIME] 답변 생성(2단계 AI) : {time.perf_counter() - answer_start:.4f}초")
    print(f"[TIME] 전체 처리 : {time.perf_counter() - total_start:.4f}초")

    return {
        "question": user_question,
        "intent": search_condition.get("intent"),
        "search_condition": search_condition,
        "results_count": len(search_results),
        "answer": answer,
    }