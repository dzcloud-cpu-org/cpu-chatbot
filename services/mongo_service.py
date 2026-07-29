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
# (이하 검색 로직은 기존과 동일 — 변경 없음)
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

    주의: DB 조회 시에는 (조건부 필드를 나중에 채울 수 있도록) 여전히
    PROJECTION_EXTENDED로 필요한 필드를 모두 가져온다. 토큰 절감은
    "DB에서 어떤 필드를 가져오느냐"가 아니라 "GPT에 무엇을 넘기느냐"에서
    이루어지므로, GPT로 보내기 직전에 build_popup_info_list()를 거친다.
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
        conditions += _popup_name_or_keyword_conditions(search_condition)
        conditions.append({"opening_hours": {"$exists": True}})

    elif intent == "information":
        conditions.append({
            "$or": [
                {"address_detail": {"$exists": True}},
                {"additional_information": {"$exists": True}},
            ]
        })

    else:
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


# =============================================================================
# [토큰 최적화] GPT 전달용 popup_info 최소화
#
# 기존에는 조회된 팝업 dict를 거의 그대로(주소/카테고리/content 전체/
# waiting_info/additional_information 포함) GPT에 넘겨서 입력 토큰이 컸다.
#
# 아래 build_popup_info()는
#   1) 항상 필요한 최소 필드(팝업명/위치/운영기간/운영시간/상세페이지 URL)만 담고,
#   2) 사용자가 실제로 물어본 항목(intent 또는 keywords에 해당 단어가 있는 경우)에
#      한해서만 주소/예약안내/웨이팅/카테고리/상세설명을 추가로 담으며,
#   3) 상세 설명(content)은 포함하더라도 앞부분만 잘라 붙인다(_truncate).
#
# 반환값은 dict(직렬화하면 바로 JSON)이므로, 기존처럼 긴 한글 라벨이 붙은
# 텍스트 블록을 만들 필요 없이 json.dumps(..., ensure_ascii=False)로
# 바로 프롬프트에 넣으면 된다.
# =============================================================================

def _truncate(text: str | None, max_len: int = 100) -> str:
    """긴 본문 텍스트를 max_len자로 잘라 "..."을 붙인다. GPT에 넘기는 토큰을 줄이기 위함."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "..."


def build_popup_info(popup: dict, search_condition: dict | None = None) -> dict:
    """
    MongoDB 조회 결과 1건을 GPT 전달용 최소 정보(dict)로 변환한다.

    기본 포함 필드: title, location(region), period(운영기간), opening_hours, source_url
    조건부 포함 필드: address / reservation_info / waiting_info / category / content
      → search_condition의 intent 또는 keywords에 해당 질문이 있을 때만 추가.
    """
    search_condition = search_condition or {}
    intent = search_condition.get("intent")
    keywords_text = " ".join(search_condition.get("keywords") or [])
    combined = f"{intent or ''} {keywords_text}"

    info: dict = {
        "title": popup.get("title"),
        "location": popup.get("region"),
        "period": f'{popup.get("start_date", "")} ~ {popup.get("end_date", "")}',
        "opening_hours": popup.get("opening_hours"),
        "source_url": popup.get("source_url"),
    }
 
    # 주소를 직접 물어본 경우에만 포함
    if intent == "information" or "주소" in combined:
        info["address"] = popup.get("address")

    # 예약 관련 질문일 때만 포함 (실제 데이터는 additional_information 텍스트 매칭 수준)
    if intent == "reservation" or any(w in combined for w in ("예약", "사전예약", "네이버예약")):
        info["reservation_info"] = popup.get("additional_information")

    # 웨이팅/대기 질문일 때만 포함
    if any(w in combined for w in ("웨이팅", "대기")):
        info["waiting_info"] = popup.get("waiting_info")

    # 카테고리를 직접 물어본 경우에만 포함
    if intent == "category" or "카테고리" in combined:
        info["category"] = popup.get("category")

    # 상세 설명(팝업 소개)을 물어본 경우에만, 그것도 앞부분만 잘라서 포함
    if intent == "popup_info" or any(w in combined for w in ("설명", "소개", "어떤 곳", "어떤곳")):
        info["content"] = _truncate(popup.get("content"))

    return info


def build_popup_info_list(popups: list[dict], search_condition: dict | None = None) -> list[dict]:
    """search_popup_by_condition() 등으로 조회한 결과 리스트를 GPT 전달용으로 일괄 변환."""
    return [build_popup_info(p, search_condition) for p in popups]