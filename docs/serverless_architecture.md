# 팝업스토어 AI 챗봇 — FastAPI(K8s) + Lambda(AI 전용) 아키텍처

`backend/`(FastAPI, Kubernetes Pod)와 `lambda/`(AWS Lambda, API Gateway 뒤의
순수 AI 계층) 두 개의 독립 배포 트리로 구성된 아키텍처. Redis/MongoDB 관련
코드는 전부 `backend/`에만 있고, Lambda는 그중 어느 것도 알지 못한다 — Input
Guardrail → Prompt 생성 → OpenAI 호출 → Output Guardrail만 수행한다.

## 1. 전체 아키텍처

```
Client
    │
Istio Ingress Gateway (Kubernetes)
    │
FastAPI Backend (Kubernetes Pod)  ← backend/
    │
    ├─ ① Redis GET                      (backend/services/redis_service.py)
    │      Cache Hit  → 캐시 응답 반환 (MongoDB/Lambda 미호출)
    │      Cache Miss ↓
    │
    │      (0: 오프토픽 차단 — 다이어그램엔 없지만 비용 절감을 위한 추가 단계.
    │       9번 항목 참고)
    │
    ├─ ② MongoDB 조회                    (backend/services/mongodb_service.py
    │      결과 없음 → "관련 정보를 찾을 수      → backend/repository/mongo_repository.py
    │      없습니다" 즉시 응답 (Lambda 미호출)    → search_condition.py, services/mongo_service.py 재사용)
    │
    ├─ ③ Prompt Context 생성              (backend/prompts/context_formatter.py)
    │      MongoDB 결과(list[dict]) → JSON 문자열
    │
    ├─ ④ AWS API Gateway 호출              (backend/services/lambda_client.py)
    │        │                            POST {"question": ..., "context": "..."}
    │        ▼
    │      ⑤ AWS Lambda(Chatbot, Python 3.12)  ← lambda/
    │        ├─ Input Guardrail    (guardrail/input_guardrail.py)
    │        ├─ Prompt 생성         (prompts/system_prompt.py)
    │        ├─ OpenAI GPT 호출     (llm/client.py — 기존 openai_service.py 재사용)
    │        └─ Output Guardrail   (guardrail/output_guardrail.py)
    │
    ├─ ⑥ Lambda 응답 수신                  {"answer": "..."}
    │
    ├─ ⑦ Redis SET (TTL)                  (backend/services/redis_service.py)
    │
    └─ ⑧ 사용자 응답 반환
```

## 2. 폴더 구조

```
backend/                         # FastAPI — Docker 이미지로 K8s Pod 배포 (기존 Dockerfile 그대로 사용)
  api/
    chat_router.py                 # POST /chat (Client 대상)
    error_handlers.py              # 표준 JSON 에러 응답
  services/
    chatbot_service.py             # 오케스트레이션 (①~⑧, check_topic 포함)
    redis_service.py                # Redis get/set/키생성, fail-open
    mongodb_service.py              # repository를 감싸는 서비스 레이어 (build_context)
    lambda_client.py                  # API Gateway POST 호출 ({"question", "context"} → {"answer"})
  repository/mongo_repository.py    # search_condition.py + services/mongo_service.py 재사용
  prompts/context_formatter.py       # MongoDB 결과 → Context 문자열 ("③ Prompt Context 생성")
  models/schemas.py                  # ChatRequest/ChatResponse/에러 envelope
  config/settings.py                  # REDIS_HOST/PORT/PASSWORD, LAMBDA_API_URL, LAMBDA_API_KEY ...
  utils/{logger.py,exceptions.py}

lambda/                           # AWS Lambda(Python 3.12) — 독립 배포 zip (CodeUri: lambda/)
  lambda_handler.py                 # handler(event, context) — Input Validation(Pydantic) + 오케스트레이션
  guardrail/
    input_guardrail.py                # Prompt Injection / 시스템 프롬프트 변경 시도 / 욕설 / 비정상 입력 / 오프토픽
    output_guardrail.py                # 허위정보(미근거 URL/PII) 제거 / 금지어
  llm/client.py                       # 기존 services/ai/openai_service.py 호출 스타일 그대로 재사용
  prompts/system_prompt.py             # System Prompt + User Prompt 조립 ("Prompt 생성"은 Lambda 책임)
  requirements.txt                     # openai, pydantic만 (pymongo/redis/fastapi/requests 없음)

template.yaml                     # AWS SAM (CodeUri: lambda/, Handler: lambda_handler.handler), API Key + Usage Plan

tests/
  backend/                          # pytest tests/backend -v
    test_redis_service.py             # fakeredis
    test_chatbot_service.py           # mongodb_service/lambda_client mock
  lambda_tests/                      # pytest tests/lambda_tests -v (별도 프로세스로 실행 — 9번 항목 참고)
    conftest.py                        # lambda/ 를 sys.path에 추가
    test_input_guardrail.py
    test_output_guardrail.py
    test_lambda_handler.py
```

기존 `routers/`, `services/mongo_service.py`, `services/ai/*`, `prompts/*`,
`utils/*`, `search_condition.py`(레거시 `/chatbot/search` 스택)는 그대로
재사용되며 수정하지 않았다. `main.py`는 `backend.api.chat_router`를 바라보도록
import만 바뀌었다.

## 3. FastAPI ↔ Lambda 통신 (API Gateway 경유)

**요청** (`backend/services/lambda_client.py` → API Gateway `POST /chat`):
```json
{
  "question": "성수에서 하는 팝업 알려줘",
  "context": "[{\"title\": \"...\", \"location\": \"성수\", \"source_url\": \"https://...\"}]"
}
```
**응답**(정상): `{"answer": "..."}`.
**응답**(Lambda 측 오류): HTTP 4xx/5xx + `{"error": {"code": "...", "message": "..."}}`.

### 동기 `requests.post` 예시 vs 실제 구현(`httpx.AsyncClient`)

요청받은 참고 예시는 다음과 같다.
```python
response = requests.post(
    API_GATEWAY_URL,
    json={"question": question, "context": context},
    headers={"x-api-key": API_KEY},
    timeout=30,
)
```
이 프로젝트의 FastAPI 엔드포인트는 전부 `async def`다. 동기 `requests.post()`를
async 함수 안에서 그대로 부르면 그 요청이 끝날 때까지 이벤트 루프 전체가
막혀 동시 처리량이 떨어진다. 그래서 URL/JSON/헤더/타임아웃은 예시와 동일하게
유지하되, `httpx.AsyncClient`로 논블로킹 호출을 구현했다
(`backend/services/lambda_client.py::invoke_chat`). 결과적으로 만들어지는
HTTP 요청은 동일하다.

### 인증 방식: API Gateway API Key (x-api-key 헤더)

FastAPI가 K8s(온프레미스 포함)에서 실행되고 Lambda는 AWS에 있으므로, 매 요청
AWS 자격증명으로 서명해야 하는 IAM SigV4보다 헤더 하나(x-api-key)만 실으면
되는 API Key + Usage Plan 방식이 단순하다. `template.yaml`에
`AWS::ApiGateway::ApiKey`/`UsagePlan`을 포함해뒀다.

## 4. 환경 변수

| 대상 | 변수 | 기본값(로컬) | 설명 |
|---|---|---|---|
| Backend | `REDIS_HOST` | `localhost` | K8s에서는 Redis Service DNS |
| Backend | `REDIS_PORT` | `6379` | |
| Backend | `REDIS_PASSWORD` | (빈 값) | K8s Secret으로 주입 예정 |
| Backend | `MONGODB_URI` | `mongodb://192.168.0.154:27017` | 기존 `services/mongo_service.py`가 그대로 읽음(변경 없음) |
| Backend | `LAMBDA_API_URL` | (빈 값) | API Gateway invoke URL. 비어있으면 `LambdaInvocationError`로 fail-safe 처리 |
| Backend | `LAMBDA_API_KEY` | (빈 값) | x-api-key 헤더 값 |
| Lambda | `OPENAI_API_KEY` | (필수) | 코드에 값이 없고 Lambda 환경변수로만 주입 |

지금은(Kubernetes/API Gateway 이전 작업 중) `REDIS_HOST`가 기본값(로컬)이고
`LAMBDA_API_URL`이 비어있지만, 코드는 이 값들을 환경변수로만 읽으므로 나중에
실제 값으로 바꾸기만 하면 연결된다(코드 변경 불필요) — 9번 항목의 실제 검증
결과가 이를 증명한다.

## 5. Redis 처리 (Key 설계 / TTL)

`backend/services/redis_service.py` 상단 docstring에 상세 정리:
- **정규화 + SHA-256 해시**를 키로 사용(질문 원문 그대로 쓰는 방식보다 고정
  길이·특수문자 이슈 없음·캐시 히트율·프라이버시 전부 유리).
- TTL 기본 3600초(1시간) — 팝업 운영기간/예약 정보가 하루 단위로 바뀔 수 있어
  너무 길면 위험, 너무 짧으면 캐시 효과가 없다.
- 무효화는 기본적으로 TTL 자연 만료. 필요하면 발급된 키를 Redis Set에
  등록해뒀다가 데이터 갱신 배치에서 순회 삭제하는 방식을 얹을 수 있다.

## 6. MongoDB 처리 / RAG

`backend/services/mongodb_service.py` → `backend/repository/mongo_repository.py`가
기존 `search_condition.py`(임베딩 유사도 + 규칙 기반 검색조건 생성, LLM 호출
없음) + `services/mongo_service.py`(MongoDB 쿼리)를 그대로 재사용한다.
`backend/prompts/context_formatter.py`가 결과를 JSON 문자열로 변환해 Lambda에
전달한다. Lambda의 `prompts/system_prompt.py`는 다음 정책을 명시한다.

- Context만 근거로 답변
- Context에 없는 내용은 추측하지 않음
- 근거가 없으면 "관련 정보를 찾을 수 없습니다."라고만 답변

## 7. Guardrail

**Input Guardrail** (`lambda/guardrail/input_guardrail.py`):
- Prompt Injection / 시스템 프롬프트 변경 시도 차단(정규식 패턴)
- 욕설 필터링
- 비정상 입력(과도하게 긴 입력, 500자 초과) 차단
- 서비스와 무관한 질문(오프토픽) 차단 — `question`이 별도 필드로 도착하므로
  화이트리스트 검사가 정확히 동작한다(FastAPI의 1차 차단에 이은 2차 방어선)

**Output Guardrail** (`lambda/guardrail/output_guardrail.py`):
- 금지어 필터링, 욕설 시 전체 답변 차단
- 허위 정보 제거 / Context 외 내용 생성 방지 — 완전한 hallucination 탐지는
  정규식으로 불가능하므로, 실무적 대리 지표인 "답변 속 URL/PII가 Context에
  실제로 존재하는지"로 판별해 미근거 URL 제거·PII 마스킹을 수행한다.

## 8. 예외 처리 매트릭스

| 상황 | 처리 |
|---|---|
| MongoDB 연결 실패 | `MongoUnavailableError`(503, backend) → 친화적 메시지, Lambda 미호출 |
| Redis 연결 실패 (GET/SET) | fail-open — 로그만 남기고 캐시 미스로 취급 |
| API Gateway/Lambda 호출 실패(네트워크, 4xx/5xx) | `LambdaInvocationError`(502, backend) → 친화적 메시지 |
| Lambda 응답 지연 | `LambdaTimeoutError`(504, backend, `LAMBDA_TIMEOUT_SECONDS`=30초 기준) |
| Lambda 내부 OpenAI 오류/타임아웃 | Lambda가 자체적으로 502/504 + `{"error":...}` 반환 → backend가 위 두 예외로 변환 |
| Input Validation 실패(Lambda, Pydantic) | 400 + `{"error": {"code": "VALIDATION_ERROR", ...}}` |
| MongoDB 결과 없음 | 예외 아님 — "관련 정보를 찾을 수 없습니다." 정상 응답, Lambda 미호출 |
| Guardrail 차단(오프토픽/Injection/욕설 등) | 예외 아님 — 200 + 안내 문구 `{"answer": "..."}` 반환 |

## 9. 기존 OpenAI 호출 코드 재사용 vs 변경 비교

### `lambda/llm/client.py` ↔ `services/ai/openai_service.py`

| 항목 | 기존 (`services/ai/openai_service.py`) | 새 코드 (`lambda/llm/client.py`) | 재사용 여부 |
|---|---|---|---|
| `OPENAI_MODEL` 상수 | `"gpt-4.1-mini"` | 동일 | 그대로 재사용 |
| `AsyncOpenAI` 싱글턴 (`_get_client`) | 모듈 전역 + lazy init | 동일한 패턴 | 그대로 재사용 |
| 함수 시그니처 | `generate_chat_response(system_prompt, user_prompt, temperature)` | 동일 | **그대로 재사용** — 이번 아키텍처는 Prompt 생성이 다시 Lambda 책임이라 원래 시그니처를 바꿀 필요가 없었다 |
| 호출 방식 | `client.chat.completions.create(model=..., messages=[{"role":"system",...},{"role":"user",...}])` | 동일 | 그대로 재사용 |
| `OPENAI_API_KEY` 로딩 | `os.getenv("OPENAI_API_KEY")` | 동일 | 그대로 재사용 |
| 예외 타입 | `OpenAIAPIError` | `OpenAICallError` + `OpenAITimeoutError` 분리 | **변경** — Timeout을 502/504로 구분 응답하기 위해 |
| 타임아웃 처리 | 없음 | `asyncio.wait_for(OPENAI_TIMEOUT_SECONDS)` | **추가** |

이전 라운드(FastAPI가 `{"prompt": "..."}` 하나로 합쳐 보내던 버전)에서는
`system_prompt`/`user_prompt` 2개 인자를 1개로 합쳐야 해서 시그니처가
바뀌었었는데, 이번 요청대로 Prompt 생성을 다시 Lambda가 맡으면서 기존 함수
시그니처를 그대로 유지할 수 있게 됐다 — 재사용률이 가장 높아진 버전이다.

### FastAPI에서 호출하는 예제

```python
# backend/services/chatbot_service.py 발췌
from backend.prompts.context_formatter import build_context_text
from backend.services import lambda_client

context_text = build_context_text(popup_info_list)
answer = await lambda_client.invoke_chat(question, context_text)  # → API Gateway 호출, {"answer": ...} 반환
```

`lambda_client.invoke_chat()`이 실제로 만드는 요청은 다음과 동일하다:

```python
import httpx

async def call_chat_lambda(question: str, context: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://xxxxx.execute-api.ap-northeast-2.amazonaws.com/Prod/chat",
            json={"question": question, "context": context},
            headers={"x-api-key": "..."},
        )
    response.raise_for_status()
    return response.json()["answer"]
```

## 10. 테스트를 두 프로세스로 나눠 실행하는 이유

저장소 루트에 이미 `prompts/`(search_condition.py가 쓰는 임베딩 프롬프트
패키지)가 있고, 이번 요청으로 `lambda/prompts/`(Lambda의 System Prompt
패키지)도 생겼다. 둘 다 같은 이름 `prompts`를 최상위 모듈로 쓰기 때문에,
`backend`/`lambda` 테스트를 같은 pytest 프로세스(같은 `sys.modules` 캐시)에서
함께 수집하면 한쪽의 `prompts`가 다른 쪽을 가려버려 `ImportError`가 난다.

실제 배포 환경에서는 이 충돌이 전혀 발생하지 않는다 — `backend`(Docker
이미지)와 `lambda`(AWS SAM `CodeUri: lambda/`)는 애초에 서로 다른
프로세스/컨테이너에서 실행되는 독립 배포 단위이기 때문이다. 로컬 테스트도
그 경계를 그대로 반영해 두 개의 pytest 프로세스로 나눠 실행한다.

```
.venv/Scripts/python.exe -m pytest tests/backend -v        # 12개
.venv/Scripts/python.exe -m pytest tests/lambda_tests -v   # 18개
```

| 시나리오 | 테스트 |
|---|---|
| Cache Hit (Mongo/Lambda 미호출) | `tests/backend/test_chatbot_service.py::test_cache_hit_skips_mongo_and_lambda` |
| 오프토픽 차단 (Mongo/Lambda 미호출) | `test_off_topic_question_blocked_without_calling_mongo_or_lambda` |
| MongoDB 결과 없음 (Lambda 미호출) | `test_empty_mongo_result_returns_no_result_message_without_calling_lambda` |
| 전체 파이프라인 성공 + 캐시 저장 + 재요청 시 캐시 히트 | `test_full_pipeline_calls_lambda_and_caches_result` |
| MongoDB 장애 / Lambda 타임아웃 | `test_mongo_failure_returns_friendly_message` / `test_lambda_timeout_returns_friendly_message` |
| Redis 장애(fail-open)/TTL/손상 캐시 | `tests/backend/test_redis_service.py` |
| Prompt Injection / SQLi / 욕설 / 비정상 입력 / 오프토픽 | `tests/lambda_tests/test_input_guardrail.py` |
| 허위정보(미근거 URL) / PII / 금지어 | `tests/lambda_tests/test_output_guardrail.py` |
| Lambda handler 자체(Input Validation, 400/200/504) | `tests/lambda_tests/test_lambda_handler.py` |

## 11. 로컬 end-to-end 통합 테스트 결과 (Kubernetes/API Gateway 배포 전)

단위 테스트(mock)와 별개로, Docker/WSL 없이 세 가지를 로컬에 띄워 실제 코드
경로로 전체 흐름을 검증했다.

- `fakeredis.TcpFakeServer` → 실제 Redis 프로토콜(RESP)로 응답하는 서버를
  `localhost:6379`에 (Redis 미배포 상태를 대신)
- **API Gateway/Lambda를 mock으로 대체하지 않고**, `lambda/lambda_handler.py`의
  `handler(event, context)`를 그대로 호출하는 소형 HTTP 서버를 `localhost:9091`에
  (아직 배포되지 않은 API Gateway 자리만 대신하고, 그 뒤의 로직은 100% 실제 코드)
- FastAPI(`main:app`)를 `REDIS_HOST=localhost`, `LAMBDA_API_URL=http://127.0.0.1:9091/chat`로 기동

이 상태로 실제 MongoDB(`192.168.0.154`)를 대상으로 4가지 시나리오를 확인했다.

| 시나리오 | 요청 | 결과 |
|---|---|---|
| 오프토픽 차단 | "오늘 점심 뭐 먹지?" | `check_topic()`에서 즉시 차단, MongoDB/Lambda 미호출, 2.16초(Redis 조회 포함) |
| Cache Miss → 전체 파이프라인 | "성수에서 하는 팝업 알려줘" (1차) | MongoDB 3건 검색 → Context 생성 → 로컬 Lambda 서버(실제 `lambda_handler.handler`) 호출 → 실제 OpenAI가 3건 모두 근거로 답변 생성 → Redis 저장(`saved: true`). **8.93초**, `"cached": false` |
| Cache Hit | 동일 질문 (2차) | MongoDB/Lambda 전부 미호출, 캐시된 답변 그대로 반환. **0.06초**, `"cached": true`, Lambda 호출 횟수 그대로(1회 유지 — 재호출 안 됨) |
| MongoDB 결과 없음 | "존재하지않는화성팝업스토어알려줘" | MongoDB는 조회하되 결과 0건 → "관련 정보를 찾을 수 없습니다" 즉시 반환, Lambda 미호출. 0.55초 |

로그를 확인한 결과 `redis_get_failed`/`redis_set_failed` 경고가 전혀 없었다
(진짜 Redis 프로토콜 서버에 정상적으로 GET/SET이 성공했다는 뜻). `REDIS_HOST`/
`LAMBDA_API_URL` 등 환경변수 값만 실제 K8s Service/API Gateway 값으로 바꾸면
이 결과가 그대로 재현된다(코드 변경 불필요) — 이번 통합 테스트가 그 근거다.

**검증 중 발견/수정한 이슈**: `REDIS_HOST`가 가리키는 포트에 아무 프로세스도
없는 상태(Redis 미배포)에서 `redis.asyncio.Redis`가 `socket_connect_timeout`
설정(1.5초)을 지키지 않고 20초 넘게 걸리는 것을 확인했다(플랫폼/커넥션 재시도
로직에 따라 클라이언트 자체 타임아웃이 그대로 지켜지지 않을 수 있음). 캐시
계층 장애가 응답 전체를 지연시키면 fail-open의 의미가 없으므로,
`backend/services/redis_service.py`의 `get_cached_response`/`save_response`에
`asyncio.wait_for(REDIS_TIMEOUT_SECONDS)` 상한을 한 번 더 강제해 실제로
~1.6초 안에 실패하고 넘어가는 것을 재확인했다.
