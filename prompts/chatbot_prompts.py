"""
prompts/chatbot_prompts.py

AI 검색형 챗봇 프롬프트 모음 (토큰 절약을 위해 축약한 버전)

전체 흐름
==========================================================
사용자 질문
    │
    ▼
search_condition.py (임베딩 유사도 + 규칙 기반, AI 호출 없음)
    - 자연어 질문 → MongoDB 검색 조건(dict) 변환
    │
    ▼
FastAPI → MongoDB 검색 → build_popup_info_list() (질문과 관련된 필드만 추출)
    │
    ▼
[AI 1회 호출] CHATBOT_RESPONSE_PROMPT
    - 검색 결과(JSON) 기반 자연어 답변 생성
    │
    ▼
최종 답변
==========================================================

※ 기존에는 검색 조건 생성에도 AI를 1회 호출해서(SEARCH_CONDITION_PROMPT)
   질문당 AI 호출이 2회였다. 지금은 search_condition.py가 그 역할을
   임베딩 유사도 + 규칙으로 대체하므로, AI는 최종 답변 생성 1회만 호출된다.
"""

# ==========================================================
# 검색 결과 기반 최종 답변 생성 프롬프트 (AI 호출은 이제 이것 하나뿐)
# ==========================================================
# 원본 대비 변경점:
# - 규칙 12개 + intent별 블록 7개 + "답변 형식" 중복 섹션을 하나로 압축
# - build_popup_info_list()가 이미 "질문과 관련된 필드만" 골라서 JSON으로
#   넘기므로, "이 필드가 있으면 이렇게 안내하라"는 케이스를 일일이 나열할
#   필요 없이 "JSON에 있는 필드만 언급"으로 대체
# ==========================================================

CHATBOT_RESPONSE_PROMPT = """
당신은 팝업스토어 챗봇입니다. 아래 제공되는 JSON 팝업 정보만 이용해 답변합니다.

# 규칙
1. JSON에 없는 팝업이나 정보는 절대 만들어내지 않습니다.
2. 질문 의도(intent)와 직접 관련된 정보만 간결하게 답합니다.
   불필요한 배경 설명이나 긴 소개 문장은 쓰지 않습니다.
3. JSON에 없는 필드는 언급하지 않습니다. 있는 정보만 안내합니다.
4. 상세 설명(content)은 있어도 1~2문장으로만 요약합니다.
5. 추천 이유는 intent가 recommend일 때만 작성합니다.
6. source_url이 있으면 아래 형식 그대로 안내합니다.
   (마크다운 링크 금지, URL은 수정/축약 없이 그대로)
   상세페이지: https://...
7. 검색 결과가 없으면 "죄송합니다. 조건에 맞는 팝업스토어를 찾지 못했습니다."
   라고만 답합니다.
8. date_filter(오늘/이번주)로 이미 필터링된 결과이므로 날짜를 재검증하지 않습니다.
9. 마크다운 문법(*, #, [](), 굵게 등)은 사용하지 않되, 여러 항목/팝업은
   줄바꿈으로 구분해 목록처럼 간결히 나열합니다.
10. 자연스럽고 친절한 한국어로 작성합니다.

# intent별 우선 정보 (JSON에 있는 것만)
popup_info   팝업명, 위치, 운영기간, 운영시간, 상세페이지
operation    운영기간, 운영시간 (없으면 "운영시간 정보가 등록되어 있지 않습니다.")
reservation  예약 정보 (없으면 "예약 관련 정보가 등록되어 있지 않습니다.")
information  주소, 추가 안내, 상세페이지
location/category  목록 형태로 안내
recommend    추천 이유 포함
"""

# ==========================================================
# User Prompt Builder
# ==========================================================

def build_chatbot_answer_user_prompt(
    user_question: str,
    search_condition: dict,
    popup_info,
) -> str:
    """
    최종 답변 생성용 User Prompt.

    popup_info는 mongo_service.build_popup_info_list()가 반환하는
    list[dict](질문과 관련된 필드만 추린 결과)를 그대로 받아 JSON으로 직렬화한다.
    기존처럼 사람이 읽기 좋은 긴 텍스트 블록을 미리 만들 필요가 없어져
    그만큼 입력 토큰이 줄어든다.
    """

    intent = search_condition.get("intent", "recommend")

    if isinstance(popup_info, (list, dict)):
        import json
        popup_info_text = json.dumps(popup_info, ensure_ascii=False)
    else:
        popup_info_text = popup_info

    return (
        f"질문: {user_question}\n"
        f"intent: {intent}\n"
        f"검색 결과(JSON): {popup_info_text}\n\n"
        "위 JSON에 있는 정보만 사용해 규칙에 따라 답변하세요."
    )
