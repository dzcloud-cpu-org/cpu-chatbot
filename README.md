# 팝업스토어 AI 챗봇 (Chatbot-only PoC)

기존 "AI 팝업 플래너" 프로젝트에서 **챗봇 기능만 분리**한 경량 버전.
추천/일정 생성(Planner) 기능은 제거.

## 폴더 구조

```
.
├── main.py                        # FastAPI 진입점 (챗봇 라우터만 include)
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .env.example
├── prompts/
│   └── chatbot_prompts.py         # 검색조건 생성 / 최종답변 생성 프롬프트
├── routers/
│   └── chatbot.py                 # GET /chatbot/search, /chatbot/ai-search
├── services/
│   ├── gemini_service.py          # Gemini API 연동 (google-genai SDK)
│   ├── mongo_service.py           # MongoDB 검색 (레거시 + AI 검색조건 기반)
│   ├── chatbot_service.py         # 레거시 챗봇 (규칙 기반 키워드 추출)
│   └── ai_chatbot_service.py      # AI 검색형 챗봇 (2단계 Gemini 파이프라인)
└── utils/
    ├── guardrail.py               # 팝업 무관 질문 차단
    ├── keyword_utils.py           # 레거시 키워드 추출
    └── stopwords.py               # 레거시 불용어 사전
```

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 헬스체크 |
| GET | `/chatbot/search?user_question=` | 레거시: 규칙 기반 키워드 추출 → MongoDB → Gemini 답변(1회 호출) |
| GET | `/chatbot/ai-search?user_question=` | AI 검색형: Gemini가 검색조건(JSON) 생성 → MongoDB → Gemini 답변 생성 (2회 호출) |

## 실행 방법

```bash
# 1) 가상환경 생성 및 패키지 설치
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) 환경변수 설정
cp .env.example .env
# .env 파일에 GEMINI_API_KEY, MONGODB_URI 값 입력

# 3) 서버 실행
uvicorn main:app --reload --port 8000
```

## Docker 실행

```bash
docker build -t popup-chatbot .
docker run --env-file .env -p 7000:7000 popup-chatbot
```

## 원본 프로젝트에서 제거된 것

- `/api/v1/planner/recommend`, `/api/v1/planner/replan` 엔드포인트
- `models.py` (Planner 요청/응답 Pydantic 스키마)
- `prompts/planner_prompt.py`
- `services/retrieval.py`, `data/popups_mock.json`
- `venv/`, `.venv/`, `test.py`, `check_models.py` 등 개발용 잔재 파일
- 인코딩이 깨져 있던(UTF-16) `requirements.txt` → UTF-8로 재저장
