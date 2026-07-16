import os

from pymongo import MongoClient


# =============================================================================
# MongoDB 연결
#
# 팝업스토어 데이터가 저장되어 있는 MongoDB 연결
#
# 기존에는 IP를 코드에 직접 하드코딩했지만,
# - 운영/개발 환경마다 접속 주소가 달라질 수 있고
# - IP가 코드/Git 이력에 그대로 노출되는 문제가 있어
# 환경변수(.env)의 MONGODB_URI 값을 읽어오도록 변경했다.
# (.env에 값이 없으면 기존 PoC용 고정 IP를 기본값으로 사용)
# =============================================================================

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://192.168.0.154:27017")

client = MongoClient(MONGODB_URI)


db = client["cpu_popup_db"]

collection = db["cpu_popga"]



# =============================================================================
# 팝업 검색 함수
#
# 사용자 질문에서 추출한 핵심 키워드를 받아
# MongoDB에서 조건에 맞는 팝업 데이터를 검색한다.
#
# 기존 방식:
# keyword 1개씩 검색
# 서울 OR 캐릭터 OR 현대
#
# 문제:
# 검색 범위가 넓어 관련 없는 팝업도 많이 조회됨
#
#
# 변경 방식:
# 여러 키워드를 AND 조건으로 검색
#
# 예)
# ["서울", "캐릭터"]
#
# 결과:
# 서울이라는 조건 만족
# AND
# 캐릭터라는 조건 만족
#
# => 더 정확한 검색 결과 반환
#
# 추후 RAG(Qdrant) 적용 시에도
# 동일한 검색 인터페이스 유지 가능
# =============================================================================


def search_popup(keywords: list[str]):


    # =====================================================
    # 키워드별 검색 조건 생성
    #
    # 각 키워드는 제목, 지역, 주소, 카테고리, 내용 중
    # 하나라도 포함되어야 한다.
    #
    # 예:
    #
    # keyword = "서울"
    #
    # title OR region OR address OR category OR content
    #
    # keyword = "캐릭터"
    #
    # title OR region OR address OR category OR content
    #
    # 두 조건은 AND로 연결
    # =====================================================

    query = {
        "$and": [
            {
                "$or": [
                    {
                        "title": {
                            "$regex": keyword,
                            "$options": "i"
                        }
                    },
                    {
                        "region": {
                            "$regex": keyword,
                            "$options": "i"
                        }
                    },
                    {
                        "address": {
                            "$regex": keyword,
                            "$options": "i"
                        }
                    },
                    {
                        "category": {
                            "$regex": keyword,
                            "$options": "i"
                        }
                    },
                    {
                        "content": {
                            "$regex": keyword,
                            "$options": "i"
                        }
                    }
                ]
            }
            for keyword in keywords
        ]
    }



    # =====================================================
    # Projection
    #
    # 필요한 데이터만 조회
    #
    # 이유:
    # - Gemini에게 전달할 데이터 크기 감소
    # - 응답 속도 개선
    # - 추후 Qdrant RAG 검색 결과와 동일한 구조 유지
    # =====================================================

    projection = {

        "_id": 0,

        "title": 1,

        "region": 1,

        "category": 1,

        "address": 1,

        "content": 1,

        "start_date": 1,

        "end_date": 1,

        "thumbnail_url": 1,

        "source_url": 1

    }



    # =====================================================
    # MongoDB 검색
    #
    # 최대 5개 반환
    # =====================================================

    result = list(
        collection.find(
            query,
            projection
        ).limit(5)
    )


    return result



# =============================================================================
# [AI 검색형 챗봇] 검색 조건(JSON) 기반 팝업 검색 함수
#
# search_popup()이 "이미 추출된 키워드 리스트"를 입력받는 것과 달리,
# 이 함수는 1단계 Gemini(SEARCH_CONDITION_PROMPT)가 생성한
# 검색 조건 JSON 전체를 입력받아 MongoDB 쿼리로 변환한다.
#
# 예시 입력(search_condition):
# {
#     "intent": "location",
#     "popup_name": null,
#     "location": "성수",
#     "category": "캐릭터",
#     "purpose": null,
#     "keywords": ["포토존", "감성"],
#     "reservation": null,
#     "operation": null,
#     "information": null
# }
#
# 처리 방식:
# 1) popup_name, location, category, keywords 값을
#    하나의 "검색 키워드 리스트"로 합친다.
#    (null / 빈 문자열은 제외 → "언급하지 않은 값은 검색 조건에 넣지 않는다")
# 2) 합쳐진 키워드는 search_popup()과 동일하게
#    "키워드별 OR 검색 + 키워드 간 AND 검색" 방식으로 쿼리를 만든다.
#    (예: location="성수", category="캐릭터"
#         → (title|region|address|category|content에 "성수" 포함)
#           AND
#           (title|region|address|category|content에 "캐릭터" 포함))
# 3) 키워드가 하나도 없으면(순수 잡담 등) 빈 리스트를 반환한다.
#    → 조건 없이 전체 데이터를 반환하면 Gemini가 관련 없는 팝업까지
#      답변에 사용할 위험이 있기 때문에, 이 경우는 검색 자체를 하지 않는다.
# =============================================================================


def _build_keywords_from_condition(search_condition: dict) -> list[str]:
    """
    검색 조건(JSON) → MongoDB 검색용 키워드 리스트 변환

    intent, purpose, reservation, operation, information은
    현재 컬렉션(cpu_popga)에 별도 필드로 저장되어 있지 않으므로
    검색 키워드로는 사용하지 않는다.
    (해당 값들은 2단계 Gemini가 최종 답변의 "질문 의도 판단"에만 사용한다.)
    """

    raw_values = [
        search_condition.get("popup_name"),
        search_condition.get("location"),
        search_condition.get("category"),
    ]

    # keywords는 리스트이므로 별도로 풀어서 합친다.
    raw_values.extend(search_condition.get("keywords") or [])

    # None, 빈 문자열, 중복 제거 (순서는 유지)
    keywords: list[str] = []
    for value in raw_values:
        if value and value not in keywords:
            keywords.append(value)

    return keywords


def search_popup_by_condition(search_condition: dict, limit: int = 5):
    """
    1단계 Gemini가 생성한 검색 조건(JSON)을 받아 MongoDB에서 팝업을 검색한다.

    반환값은 search_popup()과 동일한 필드 구조를 가지므로,
    chatbot_service 쪽에서 두 함수를 동일한 방식으로 사용할 수 있다.
    """

    keywords = _build_keywords_from_condition(search_condition)

    # 검색에 사용할 키워드가 하나도 없으면 빈 결과를 반환한다.
    if not keywords:
        return []

    # 키워드 조합 쿼리 생성 방식은 search_popup()과 동일하다.
    query = {
        "$and": [
            {
                "$or": [
                    {"title": {"$regex": keyword, "$options": "i"}},
                    {"region": {"$regex": keyword, "$options": "i"}},
                    {"address": {"$regex": keyword, "$options": "i"}},
                    {"category": {"$regex": keyword, "$options": "i"}},
                    {"content": {"$regex": keyword, "$options": "i"}},
                ]
            }
            for keyword in keywords
        ]
    }

    projection = {
        "_id": 0,
        "title": 1,
        "region": 1,
        "category": 1,
        "address": 1,
        "content": 1,
        "start_date": 1,
        "end_date": 1,
        "thumbnail_url": 1,
        "source_url": 1,
    }

    result = list(
        collection.find(query, projection).limit(limit)
    )

    return result