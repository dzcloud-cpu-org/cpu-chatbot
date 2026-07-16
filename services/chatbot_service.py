from utils.keyword_utils import extract_keywords
from services.mongo_service import search_popup
from services.gemini_service import generate_chat_response
import time
from utils.guardrail import is_popup_question

# =============================================================================
# 챗봇 서비스
#
# 사용자 질문을 받아
# 1. 핵심 키워드 추출
# 2. MongoDB 검색
# 3. 검색 결과 정제
# 4. Gemini 전달
# 5. 자연어 답변 생성
#
# =============================================================================


async def generate_chat_response_service(user_question: str):
    """
    MongoDB 검색 결과를 기반으로 Gemini 답변을 생성한다.
    """

    # =====================================================
    # 응답 시간 측정 시작
    #
    # 전체 응답 시간과 각 처리 단계의 시간을 측정하여
    # 병목 구간(MongoDB, Gemini 등)을 확인한다.
    # =====================================================

    total_start = time.perf_counter()


    # =====================================================
    # Guardrail
    #
    # 팝업스토어와 관련 없는 질문은
    # MongoDB 및 Gemini를 호출하지 않고
    # 즉시 차단한다.
    # =====================================================

    if not is_popup_question(user_question):

        print("[Guardrail] 팝업 관련 질문이 아님")

        return (
            "죄송합니다.\n\n"
            "저는 팝업스토어 관련 질문만 답변할 수 있습니다."
        )


    # =====================================================
    # 사용자 질문 → 핵심 키워드 추출
    #
    # 예:
    # 서울에 빵 팝업 알려줘
    #
    # 결과:
    # ['서울', '빵']
    #
    # =====================================================

    # 키워드 추출 시간 측정
    keyword_start = time.time()

    keywords = extract_keywords(user_question)

    print(f"키워드 추출 시간 : {time.time() - keyword_start:.3f}초")

    print("추출된 키워드 :", keywords)



    # =====================================================
    # MongoDB 검색
    # =====================================================

    # 몽고DB 검색 시간 측정
    mongo_start = time.perf_counter()

    search_results = search_popup(keywords)

    mongo_end = time.perf_counter()

    print(f"[TIME] MongoDB 검색 : {mongo_end - mongo_start:.4f}초")


    # =====================================================
    # 중복 제거
    #
    # 동일 팝업이 여러 키워드 검색에 포함되는 경우 제거
    #
    # =====================================================

    unique_results = []

    seen = set()


    for popup in search_results:

        popup_id = popup.get("source_url")


        if popup_id not in seen:

            seen.add(popup_id)

            unique_results.append(popup)



    search_results = unique_results



    # =====================================================
    # Gemini 전달 데이터 개수 제한
    #
    # 검색 결과가 너무 많으면
    # - 입력 토큰 증가
    # - 답변 품질 저하
    #
    # 상위 3개만 전달
    #
    # =====================================================

    search_results = search_results[:3]


    # =====================================================
    # 검색 로그
    # =====================================================

    print("=" * 80)

    print("사용자 질문 :", user_question)

    print("추출된 키워드 :", keywords)

    print("검색 결과 개수 :", len(search_results))


    for idx, popup in enumerate(search_results, start=1):

        print(f"[{idx}] {popup.get('title')}")


    print("=" * 80)



    # =====================================================
    # 검색 결과 없음
    # =====================================================

    if not search_results:

        return "조건에 맞는 팝업스토어를 찾지 못했습니다."



    # =====================================================
    # Gemini 전달용 데이터 생성
    #
    # 필요한 정보만 전달
    #
    # =====================================================

    popup_info = ""


    for idx, popup in enumerate(search_results, start=1):

        popup_info += f"""
            [{idx}]

            팝업명 :
            {popup.get("title", "정보 없음")}

            위치 :
            {popup.get("region", "정보 없음")}

            기간 :
            {popup.get("start_date", "정보 없음")}
            ~
            {popup.get("end_date", "정보 없음")}

            카테고리 :
            {popup.get("category", "정보 없음")}


            ------------------------
            """



    # =====================================================
    # Gemini System Prompt
    #
    # 검색 결과 기반 답변 제한
    #
    # =====================================================

    system_prompt = """
        너는 팝업스토어 추천 AI이다.

        답변 규칙:

        1. 반드시 제공된 검색 결과 안의 정보만 사용한다.
        2. 검색 결과에 없는 내용은 추측하지 않는다.
        3. 사용자가 방문하기 쉽게 설명한다.
        4. 여러 개의 팝업이 있으면 비교해서 안내한다.

        답변 형식:

        - 팝업명
        - 위치
        - 기간
        - 추천 이유

        위 순서로 작성한다.
        """



    # =====================================================
    # Gemini User Prompt
    # =====================================================

    user_prompt = f"""
        사용자 질문:

        {user_question}


        검색된 팝업스토어 정보:

        {popup_info}


        위 데이터만 참고하여 사용자 질문에 답변해줘.
        """



    # =====================================================
    # Gemini 호출 시간 측정
    # =====================================================

    gemini_start = time.perf_counter()

    answer = await generate_chat_response(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    gemini_end = time.perf_counter()

    print(f"[TIME] Gemini 호출 : {gemini_end - gemini_start:.4f}초")

    # =====================================================
    # 전체 처리 시간
    # =====================================================

    total_end = time.perf_counter()

    print(f"[TIME] 전체 처리 : {total_end - total_start:.4f}초")

    return answer