# chatbot.py
# # 서비스 호출 (URL만 받는다.)

from fastapi import APIRouter
from services.chatbot_service import generate_chat_response_service
from services.ai_chatbot_service import generate_ai_search_response


router = APIRouter(
    prefix="/chatbot",
    tags=["Chatbot"]
)


# =============================================================================
# 챗봇 검색 API
# =============================================================================
# 사용자의 자연어 질문을 받아
# chatbot_service에서 MongoDB 검색 + Gemini 답변 생성을 수행한다.
#
# 요청 예시:
# GET /chatbot/search?user_question=홍대에 있는 캐릭터 팝업 추천해줘
#
# 처리 흐름:
#
# 사용자 질문
#       ↓
# chatbot.py (API 요청 받기)
#       ↓
# chatbot_service.py
#       ↓
# MongoDB 팝업 데이터 검색
#       ↓
# Gemini에게 검색 결과 전달
#       ↓
# 자연어 답변 반환
#
# =============================================================================


@router.get("/search")
async def search(user_question: str):

    """
    [레거시] 규칙 기반 키워드 추출(utils/keyword_utils.py) 방식 챗봇.
    사용자 질문을 받아 챗봇 서비스를 호출한다.
    """

    answer = await generate_chat_response_service(user_question)

    print("응답: ", answer)

    return {
        "question": user_question,
        "answer": answer,
    }


# =============================================================================
# AI 검색형 챗봇 API (2단계 Gemini 파이프라인)
# =============================================================================
# /search와의 차이점:
#   /search    : 불용어 제거 등 "규칙 기반"으로 키워드를 추출 (utils/keyword_utils.py)
#   /ai-search : Gemini가 질문의 intent/지역/카테고리/키워드까지 직접 분석
#                (services/ai_chatbot_service.py, prompts/chatbot_prompts.py)
#
# 요청 예시:
# GET /chatbot/ai-search?user_question=성수동에서 사진 찍기 좋은 캐릭터 팝업 알려줘
#
# 처리 흐름:
#
# 사용자 질문
#       ↓
# chatbot.py (API 요청 받기)
#       ↓
# ai_chatbot_service.py
#       ↓
# Gemini 1단계 : 검색 조건(JSON) 생성 (SEARCH_CONDITION_PROMPT)
#       ↓
# MongoDB 팝업 데이터 검색 (mongo_service.search_popup_by_condition)
#       ↓
# Gemini 2단계 : 검색 결과 기반 자연어 답변 생성 (CHATBOT_RESPONSE_PROMPT)
#       ↓
# 최종 응답 반환 (질문 의도 / 검색 조건 / 결과 개수 / 답변)
#
# =============================================================================


@router.get("/ai-search")
async def ai_search(user_question: str):

    """
    사용자 질문을 받아 AI 검색형 챗봇 파이프라인(2단계 Gemini)을 호출한다.
    """

    result = await generate_ai_search_response(user_question)

    print("AI 검색 응답: ", result)

    return result