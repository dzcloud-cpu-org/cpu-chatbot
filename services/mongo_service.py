import os
import re
from datetime import date, timedelta

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

PROJECTION_BASIC = {
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

PROJECTION_EXTENDED = {
    **PROJECTION_BASIC,
    "opening_hours": 1,
    "additional_information": 1,
    "waiting_info": 1,
}


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _week_range(today: date) -> tuple[str, str]:
    """
    today가 속한 주(월요일~일요일)의 시작일/종료일을 문자열("YYYY-MM-DD")로 반환한다.

    예) today = 2025-01-16(목) → ("2025-01-13", "2025-01-19")
    """
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


# =============================================================================
# date_filter("today" / "this_week" / null) → MongoDB 날짜 조건 변환
#
# 1단계 Gemini(SEARCH_CONDITION_PROMPT)는 오늘이 정확히 며칠인지 알 수 없으므로
# "today" / "this_week" / null 중 하나로만 분류하고,
# 실제 날짜 범위 계산(오늘 날짜, 이번주 월~일 등)은 여기서 서버가 직접 수행한다.
# =============================================================================

def _base_conditions(date_filter: str | None = None) -> list[dict]:
    """
    운영 중(아직 종료되지 않은) 팝업만 남기는 공통 조건.

    - status가 "active"이고, end_date가 오늘 이후(아직 종료 전)인 것은 항상 조건에 포함한다.
    - date_filter가 없거나 "today"인 경우
      → "지금 진행 중"인 팝업만 남기도록 start_date <= 오늘 조건을 추가한다.
        (기존에는 end_date >= 오늘 조건만 있어서, 아직 시작하지 않은
         '오픈 예정' 팝업까지 검색에 포함되는 문제가 있었다.)
    - date_filter가 "this_week"인 경우
      → 이번주(월~일) 기간과 팝업의 운영기간(start_date~end_date)이
        하루라도 겹치면 포함되도록 overlap 조건으로 대체한다.
        (이번주에 아직 시작 전인 팝업, 이번주에 이미 시작해서
         계속 진행 중인 팝업을 모두 포함하기 위함)
    """
    today_str = _today()

    conditions: list[dict] = [
        {"status": "active"},
        {"end_date": {"$gte": today_str}},
    ]

    if date_filter == "this_week":
        _, week_end = _week_range(date.today())
        # 이번주 종료일보다 늦게 시작하는 팝업은 이번주와 겹치지 않으므로 제외.
        # (end_date >= 오늘 조건은 위에서 이미 적용되어 있어,
        #  이번주가 시작되기 전에 종료된 팝업은 자연히 걸러진다.)
        conditions.append({"start_date": {"$lte": week_end}})
    else:
        # date_filter가 "today"이거나 없는 경우(기본값) → 오늘 기준으로
        # 실제 운영 중인 팝업만 남긴다.
        conditions.append({"start_date": {"$lte": today_str}})

    return conditions


# =============================================================================
# 동의어 처리
#
# 사용자가 쓰는 표현과 실제로 팝업 데이터에 저장된 표현이 다를 수 있다.
# 예) 사용자는 "운영시간"이라 묻지만 데이터에는 "영업시간"으로만 적혀있는 경우
#
# 키워드가 아래 그룹에 속하면, 단일 키워드 대신
# "운영시간|영업시간|오픈시간" 같은 정규식 alternation으로 검색해서
# 표현이 달라도 매칭되도록 한다.
# =============================================================================

_SYNONYM_GROUPS: list[list[str]] = [
    ["운영시간", "영업시간", "오픈시간", "오픈 시간", "영업 시간"],
    ["예약", "사전예약", "네이버예약", "네이버 예약", "예약제"],
    ["웨이팅", "대기", "대기시간", "줄서기"],
    ["주차", "주차장", "발렛"],
    ["입장료", "관람료", "티켓", "입장권"],
]

# 빠른 조회를 위해 "키워드 → 동의어 그룹" 매핑을 미리 만들어둔다.
_SYNONYM_LOOKUP: dict[str, list[str]] = {
    word: group
    for group in _SYNONYM_GROUPS
    for word in group
}


def _expand_synonym(keyword: str) -> str:
    """
    키워드가 동의어 그룹에 속하면 "A|B|C" 형태의 정규식으로 확장하고,
    속하지 않으면 원래 키워드를 그대로 반환한다.
    """
    group = _SYNONYM_LOOKUP.get(keyword)
    if not group:
        return keyword

    return "|".join(re.escape(word) for word in group)


def _keyword_or_condition(keyword: str, fields: list[str]) -> dict:
    """지정된 필드 중 하나라도 keyword(동의어 포함)를 포함하면 매칭되는 $or 조건."""
    pattern = _expand_synonym(keyword)
    return {
        "$or": [
            {field: {"$regex": pattern, "$options": "i"}}
            for field in fields
        ]
    }


# 전체 필드 대상 검색 (title/region/address/category/content)
_FULL_SEARCH_FIELDS = ["title", "region", "address", "category", "content"]
# 제목/본문만 대상으로 하는 좁은 검색 (reservation/operation 등에서 사용)
_TITLE_CONTENT_FIELDS = ["title", "content"]


# =============================================================================
# 팝업 검색 함수 (키워드 리스트 기반)
#
# 여러 키워드를 AND 조건으로 검색한다.
# 예) ["서울", "캐릭터"] → "서울" 조건 만족 AND "캐릭터" 조건 만족
#
# 추후 RAG(Qdrant) 적용 시에도 동일한 검색 인터페이스 유지 가능
# =============================================================================

def search_popup(keywords: list[str], limit: int = 5) -> list[dict]:
    conditions = _base_conditions()
    conditions += [
        _keyword_or_condition(keyword, _FULL_SEARCH_FIELDS)
        for keyword in keywords
    ]

    query = {"$and": conditions}

    return list(collection.find(query, PROJECTION_BASIC).limit(limit))


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
#     "information": null,
#     "date_filter": "this_week"
# }
#
# intent, purpose, reservation, operation, information 값 자체는
# 현재 컬렉션(cpu_popga)에 별도 필드로 저장되어 있지 않으므로
# 검색 키워드로는 사용하지 않는다.
# (해당 값들은 2단계 Gemini가 최종 답변의 "질문 의도 판단"에만 사용한다.)
#
# date_filter는 예외적으로 실제 쿼리 조건(start_date/end_date)에 직접 반영한다.
# =============================================================================

def _build_keywords_from_condition(search_condition: dict) -> list[str]:
    """검색 조건(JSON) → MongoDB 검색용 키워드 리스트 변환 (중복/빈값 제거, 순서 유지)."""
    raw_values = [
        search_condition.get("popup_name"),
        search_condition.get("location"),
        search_condition.get("category"),
        *(search_condition.get("keywords") or []),
    ]

    keywords: list[str] = []
    for value in raw_values:
        if value and value not in keywords:
            keywords.append(value)

    return keywords


def _popup_name_or_keyword_conditions(search_condition: dict) -> list[dict]:
    """
    popup_name이 있으면 title 검색으로 좁히고,
    없으면 keywords를 title/content 대상으로 검색한다.
    reservation / operation intent에서 공통으로 사용.
    """
    popup_name = search_condition.get("popup_name")

    if popup_name:
        return [{"title": {"$regex": popup_name, "$options": "i"}}]

    keywords = search_condition.get("keywords") or []
    return [
        _keyword_or_condition(keyword, _TITLE_CONTENT_FIELDS)
        for keyword in keywords
    ]


def search_popup_by_condition(search_condition: dict, limit: int = 5) -> list[dict]:
    """
    1단계 Gemini가 생성한 검색 조건(JSON)을 받아
    MongoDB에서 조건에 맞는 팝업을 검색한다.

    - 운영 중(active)이고 종료되지 않은 팝업만 조회
    - date_filter("today"/"this_week"/null)에 따라 start_date/end_date 범위를
      실제 날짜로 변환하여 조건에 반영 (_base_conditions 참고)
    - intent별로 검색 필드를 분리해 불필요한 필드 검색을 최소화

    주의(예약/reservation intent 관련):
    현재 컬렉션에는 "실시간 예약 가능 여부"를 나타내는 필드가 없고,
    additional_information에 예약 관련 문구가 "등록되어 있는지" 여부만
    텍스트로 매칭할 수 있다. 즉 "지금 예약 가능한가요?" 같은 질문에는
    근본적으로 정확히 답할 수 없으며, 실시간 예약 가능 여부를 확인하려면
    크롤링/DB 설계 단계에서 별도 필드(예: reservation_available)를
    추가하는 작업이 선행되어야 한다.
    """
    intent = search_condition.get("intent")
    date_filter = search_condition.get("date_filter")
    conditions = _base_conditions(date_filter)

    if intent == "popup_info":
        popup_name = search_condition.get("popup_name")
        if popup_name:
            conditions.append({"title": {"$regex": popup_name, "$options": "i"}})

    elif intent == "location":
        location = search_condition.get("location")
        if location:
            conditions.append(_keyword_or_condition(location, ["region", "address"]))

    elif intent == "category":
        category = search_condition.get("category")
        if category:
            conditions.append({"category": {"$regex": category, "$options": "i"}})

    elif intent == "reservation":
        # 예약 관련 문구가 additional_information에 있는 팝업만 조회
        # (위 주의사항 참고: 텍스트 매칭 수준일 뿐 실시간 예약 가능 여부는 아님)
        #
        # 버그 수정: 기존에는 popup_name/keywords 조건만 적용되고
        # location/category 조건이 누락되어 있었다.
        # 예) "서울 예약 팝업 알려줘" → location="서울"이 무시되어
        #     서울이 아닌 다른 지역 팝업까지 검색되는 문제가 있었다.
        # → location(region/address)과 category 조건을 명시적으로 추가한다.
        location = search_condition.get("location")
        if location:
            conditions.append(_keyword_or_condition(location, ["region", "address"]))

        category = search_condition.get("category")
        if category:
            conditions.append({"category": {"$regex": category, "$options": "i"}})

        conditions += _popup_name_or_keyword_conditions(search_condition)
        conditions.append({
            "additional_information": {"$regex": "예약|사전예약|네이버", "$options": "i"}
        })

    elif intent == "operation":
        # opening_hours 필드가 존재하는 팝업만 조회
        conditions += _popup_name_or_keyword_conditions(search_condition)
        conditions.append({"opening_hours": {"$exists": True}})

    elif intent == "information":
        # 주소/추가 안내가 있는 팝업 조회
        conditions.append({
            "$or": [
                {"address_detail": {"$exists": True}},
                {"additional_information": {"$exists": True}},
            ]
        })

    else:
        # recommend / review 등 그 외 intent → 지역/카테고리/키워드 종합 검색
        location = search_condition.get("location")
        if location:
            conditions.append(_keyword_or_condition(location, ["region", "address"]))

        category = search_condition.get("category")
        if category:
            conditions.append({"category": {"$regex": category, "$options": "i"}})

        keywords = search_condition.get("keywords") or []
        conditions += [
            _keyword_or_condition(keyword, _TITLE_CONTENT_FIELDS)
            for keyword in keywords
        ]

    query = {"$and": conditions}

    return list(collection.find(query, PROJECTION_EXTENDED).limit(limit))