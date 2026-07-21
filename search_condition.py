"""
search_condition.py

기존 1단계 Gemini(SEARCH_CONDITION_PROMPT)가 하던 역할을
"임베딩 유사도(keywords) + 규칙 기반(intent, date_filter)"으로 대체한다.

이렇게 하면:
- keywords: 태그 임베딩과 질문 임베딩의 코사인 유사도로 추출
            → "친구랑 갈만한", "예약 가능한"처럼 사전 문자열과 다르게 표현해도
              의미가 가까운 태그(감성/친구/예약 등)를 잡아낼 수 있다.
- intent / date_filter: 정규식/키워드 매칭 (LLM 불필요, 비용 0원)

결과 스키마는 기존 SEARCH_CONDITION_PROMPT의 출력 형식과 동일하게 맞춰서
search_popup_by_condition()에 그대로 넣을 수 있도록 했다.

LLM(Gemini/GPT)은 더 이상 검색 조건 생성에 호출되지 않고,
파이프라인 전체에서 CHATBOT_RESPONSE_PROMPT(최종 답변 생성) 1회만 호출된다.
"""
import json
import os

from prompts.embedding.embed_provider import embed_texts

_TAG_EMBEDDINGS_PATH = os.path.join(
    os.path.dirname(__file__), "prompts", "embedding", "tag_embeddings.json"
)

with open(_TAG_EMBEDDINGS_PATH, "r", encoding="utf-8") as f:
    _TAG_EMBEDDINGS: dict[str, list[float]] = json.load(f)

if not _TAG_EMBEDDINGS:
    # tag_embeddings.json이 비어있으면(태그 임베딩을 아직 생성하지 않았으면)
    # keywords가 항상 빈 배열로만 나온다. 서버는 죽지 않지만 검색 품질이
    # 크게 떨어지니, 한 번 아래 스크립트를 실행해서 실제 임베딩을 채워야 한다.
    #   python prompts/embedding/build_embeddings.py
    print(
        "[경고] prompts/embedding/tag_embeddings.json이 비어 있습니다. "
        "python prompts/embedding/build_embeddings.py 를 먼저 실행하세요."
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _top_similar_tags(
    question_vector: list[float], top_k: int = 3, threshold: float = 0.5
) -> list[str]:
    """
    threshold(기본 0.5)보다 유사도가 낮은 태그는 버린다.
    → 관련 없는 질문인데 억지로 태그가 붙는 것을 방지.
    실제 데이터로 튜닝해가며 threshold를 조정하는 것을 권장한다.
    """
    scored = [
        (tag, _cosine_similarity(question_vector, vector))
        for tag, vector in _TAG_EMBEDDINGS.items()
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [tag for tag, score in scored[:top_k] if score >= threshold]


# --- 지역명 사전 (utils/guardrail.py의 POPUP_KEYWORDS 중 지역/몰 이름과 동일하게 유지) ---
# DB의 region 목록이 늘어나면 여기에도 추가한다.
_LOCATIONS: list[str] = [
    "성수", "홍대", "강남", "잠실", "명동",
    "더현대", "현대백화점", "롯데월드몰", "아이파크몰",
]


def _extract_location(question: str) -> str | None:
    for loc in _LOCATIONS:
        if loc in question:
            return loc
    return None


def _extract_keywords(question: str) -> list[str]:
    remove_words = {
        "팝업",
        "팝업스토어",
        "스토어",
        "알려줘",
        "알려",
        "찾아줘",
        "찾아",
        "추천",
        "해주세요",
        "해줘",
        "가능",
        "가능한",
        "열리는",
        "진행하는",
        "하는",
        "있는",
    }

    keywords = []

    for word in question.split():
        word = word.strip(".,!?")

        # 긴 조사부터 제거
        for suffix in [
            "에서",
            "으로",
            "에게",
            "부터",
            "까지",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
        ]:
            if word.endswith(suffix):
                word = word[:-len(suffix)]
                break

        if not word:
            continue

        if word in remove_words:
            continue

        keywords.append(word)

    return keywords


# --- intent 규칙 (LLM 없이 키워드 매칭) ---
# 순서가 중요: 위에서부터 먼저 매칭되는 것을 채택한다.
_INTENT_RULES: list[tuple[str, list[str]]] = [
    ("reservation", ["예약", "사전예약", "네이버예약", "예약 가능"]),
    ("operation", ["운영시간", "영업시간", "몇시", "언제 열", "언제 문"]),
    ("information", ["주소", "위치가 어디", "어디에 있", "어디야"]),
    ("category", ["카테고리", "장르"]),
    ("location", ["근처", "동네", "지역"]),
]


def _classify_intent(question: str) -> str:
    for intent, keywords in _INTENT_RULES:
        if any(kw in question for kw in keywords):
            return intent
    return "recommend"


def _extract_date_filter(question: str) -> str | None:
    if any(kw in question for kw in ("이번주", "이번 주", "이번 주말")):
        return "this_week"
    if any(kw in question for kw in ("오늘", "지금")):
        return "today"
    return None


def build_search_condition(question: str) -> dict:
    """
    LLM 호출 없이 질문 → 검색 조건(JSON) 생성.

    주의:
    - popup_name / category는 여기서는 채우지 않는다. 특정 팝업 이름은 DB에
      등록된 title 목록과 대조해야 정확하고, category는 아직 사전이 없어서다.
      데이터가 늘어나면 _LOCATIONS와 같은 방식으로 사전을 추가하면 된다.
    """
    question_vector = embed_texts([question])[0]

    # 임베딩 기반 의미 키워드 추출
    semantic_keywords = _top_similar_tags(question_vector)

    # 사용자가 직접 입력한 명시적 키워드 추출
    explicit_keywords = _extract_keywords(question)

    # 두 결과 병합 + 중복 제거
    keywords = list(set(
        semantic_keywords + explicit_keywords
    ))

    return {
        "intent": _classify_intent(question),
        "popup_name": None,
        "location": _extract_location(question),
        "category": None,
        "purpose": None,
        "keywords": keywords,
        "reservation": None,
        "operation": None,
        "information": None,
        "date_filter": _extract_date_filter(question),
    }