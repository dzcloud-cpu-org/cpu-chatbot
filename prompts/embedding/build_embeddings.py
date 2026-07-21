"""
embeddings/build_embeddings.py

태그 임베딩을 "한 번만" 생성해서 tag_embeddings.json으로 저장하는 스크립트.

주의: 서버 요청마다 실행하는 게 아니다.
      태그 목록(TAGS)을 추가/수정했을 때만 개발자가 수동으로 다시 실행한다.

사용법:
    python embeddings/build_embeddings.py
"""
import json
import os
from embed_provider import embed_texts

# =============================================================================
# 태그 목록
#
# 기존 mongodb.py의 _SYNONYM_GROUPS(동의어 사전)와
# SEARCH_CONDITION_PROMPT의 "키워드 변환 예시"에서 쓰던 표현들을 모았다.
# "예약 가능한", "친구랑 갈만한"처럼 사전에 없던 새 표현을 지원하고 싶으면
# 아래에 관련 태그만 추가하고 이 스크립트를 다시 실행하면 된다.
# =============================================================================
TAGS: list[str] = [
    "포토존", "사진",
    "굿즈", "MD",
    "가족", "아이", "체험",
    "데이트", "감성", "친구", "모임",
    "한산", "대기 적음",
    "예약", "사전예약",
    "웨이팅", "대기",
    "주차", "발렛",
    "입장료", "무료",
    "실내", "실외",
]

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "tag_embeddings.json")


def main() -> None:
    vectors = embed_texts(TAGS)
    data = dict(zip(TAGS, vectors))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"저장 완료: {OUTPUT_PATH} ({len(TAGS)}개 태그)")


if __name__ == "__main__":
    main()