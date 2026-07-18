# chatbot.py
# # 서비스 호출 (URL만 받는다.)
from fastapi import APIRouter
from services.ai_chatbot_service import generate_ai_search_response

router = APIRouter(
    prefix="/chatbot",
    tags=["Chatbot"]
)

# =============================================================================
# 챗봇 검색 API (AI 검색형, 2단계 Gemini 파이프라인)
# =============================================================================
# 사용자의 자연어 질문을 받아
# ai_chatbot_service에서 Gemini 검색조건 분석 + MongoDB 검색 + Gemini 답변 생성을 수행한다.
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
@router.get("/search")
async def search(user_question: str):
    """
    AI 검색형 챗봇(2단계 Gemini)
    프론트에서는 기존 /chatbot/search만 호출하면 된다.
    """
    result = await generate_ai_search_response(user_question)
    print("AI 검색 응답:", result)
    return result