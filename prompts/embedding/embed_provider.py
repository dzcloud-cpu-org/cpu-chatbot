"""
embeddings/embed_provider.py

임베딩 계산을 한 곳에 모아둔 모듈.
build_embeddings.py(태그 임베딩 생성, 최초 1회)와
search_condition.py(사용자 질문 임베딩, 매 요청마다 1개)에서 공통으로 사용한다.

환경변수 EMBEDDING_PROVIDER로 "gemini" / "openai" / "local" 중 선택 (기본값: local).

- "local": sentence-transformers로 로컬에서 직접 계산. API 호출/비용이 전혀 없다.
           발표 데모 비용을 최소화하려면 이 옵션을 권장.
- "gemini" / "openai": API 임베딩. 로컬 모델을 못 쓰는 환경(리소스 제한 등)일 때 대체용.
"""
import os

EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local")
LOCAL_EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL", "jhgan/ko-sroberta-multitask"
)

_local_model = None  # 최초 호출 시 1번만 로드해서 재사용 (매번 로드하면 느림)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """문자열 리스트 → 임베딩 벡터 리스트. 순서를 그대로 유지해서 반환한다."""
    if EMBEDDING_PROVIDER == "local":
        return _embed_local(texts)
    if EMBEDDING_PROVIDER == "openai":
        return _embed_openai(texts)
    return _embed_gemini(texts)


def _embed_local(texts: list[str]) -> list[list[float]]:
    global _local_model

    from sentence_transformers import SentenceTransformer

    if _local_model is None:
        _local_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)

    vectors = _local_model.encode(texts, normalize_embeddings=True)
    return vectors.tolist()


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    vectors = []
    for text in texts:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
        )
        vectors.append(result["embedding"])
    return vectors


def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 없습니다."
        )

    client = OpenAI(api_key=api_key)

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )

    return [item.embedding for item in response.data]


EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local")

print(f"EMBEDDING_PROVIDER = {EMBEDDING_PROVIDER}")