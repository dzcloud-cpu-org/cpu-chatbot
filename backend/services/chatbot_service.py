"""
backend/services/chatbot_service.py

목표 아키텍처를 그대로 구현하는 오케스트레이션 계층.

    Client → Istio Ingress Gateway → FastAPI POST /chat (backend/api/chat_router.py)
        │
        ▼
    ChatbotService.chatbot_service(question)
        │
        ├── 0) check_topic()     서비스와 무관한 질문(오프토픽)이면 즉시 차단
        │      (다이어그램에는 없지만, MongoDB/Lambda/OpenAI 호출을 전부 아끼는
        │       추가 최적화 — utils/guardrail.py의 기존 화이트리스트 재사용)
        ├── ① check_cache()      Redis GET
        │      Cache Hit  → 즉시 응답 반환 (MongoDB/Lambda 미호출)
        │      Cache Miss ↓
        ├── ② search_mongodb()   MongoDB 조회 (RAG)
        │      결과 없음 → "관련 정보를 찾을 수 없습니다" 즉시 응답 (Lambda 미호출)
        ├── ③ build_context()    MongoDB 결과를 Prompt Context 문자열로 생성
        ├── ④ call_lambda()      AWS API Gateway 호출 → ⑤ Lambda(Chatbot)
        │      (Input Guardrail → OpenAI GPT → Output Guardrail은 Lambda 내부에서 수행)
        ├── ⑥ Lambda 응답 수신
        ├── ⑦ save_cache()       Redis SET (TTL)
        └── ⑧ return_response()  사용자 응답 반환

Redis/MongoDB 관련 코드는 전부 이 계층(과 그 아래 redis_service.py/
mongodb_service.py/repository)에만 있고, Lambda는 이 중 어느 것도 알지 못한다.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.prompts.context_formatter import build_context_text
from backend.services import lambda_client
from backend.services.mongodb_service import build_context
from backend.services.redis_service import build_cache_key, get_cached_response, save_response
from backend.utils.exceptions import ChatbotError
from backend.utils.logger import get_logger, log_event, timed
from utils.guardrail import is_popup_question

log = get_logger(__name__)

NO_RESULT_MESSAGE = "관련 정보를 찾을 수 없습니다."
NOT_POPUP_QUESTION_MESSAGE = "죄송합니다.\n\n저는 팝업스토어 관련 질문만 답변할 수 있습니다."


class ChatbotService:
    async def check_cache(self, cache_key: str) -> Optional[dict]:
        return await get_cached_response(cache_key)

    def check_topic(self, question: str) -> bool:
        """서비스(팝업스토어)와 관련된 질문인지 확인한다. 기존 utils/guardrail.py의
        화이트리스트를 그대로 재사용한다(레거시 /chatbot/search 엔드포인트와 동일한 판단 기준)."""
        return is_popup_question(question)

    def search_mongodb(self, question: str) -> tuple[dict, list[dict]]:
        return build_context(question)

    def build_prompt_context(self, popup_info_list: list[dict]) -> str:
        return build_context_text(popup_info_list)

    async def call_lambda(self, question: str, context: str) -> str:
        return await lambda_client.invoke_chat(question, context)

    async def save_cache(self, cache_key: str, response: dict) -> None:
        saved = await save_response(cache_key, response)
        log_event(log, "cache_save", cache_key=cache_key, saved=saved)

    def return_response(
        self,
        question: str,
        answer: str,
        *,
        intent: Optional[str] = None,
        search_condition: Optional[dict] = None,
        context: Optional[list[dict]] = None,
        cached: bool = False,
    ) -> dict[str, Any]:
        context = context or []
        sources = [
            {"title": p.get("title"), "url": p.get("source_url")}
            for p in context
            if p.get("source_url")
        ]
        return {
            "question": question,
            "intent": intent,
            "search_condition": search_condition,
            "results_count": len(context),
            "answer": answer,
            "sources": sources,
            "cached": cached,
        }

    async def chatbot_service(self, question: str) -> dict[str, Any]:
        cache_key = build_cache_key(question)

        # ① Redis 조회 — Cache Hit이면 MongoDB/Lambda를 전혀 호출하지 않는다.
        with timed(log, "check_cache", question=question):
            cached = await self.check_cache(cache_key)

        if cached is not None:
            log_event(log, "cache_hit", question=question, cache_key=cache_key)
            return {**cached, "cached": True}

        log_event(log, "cache_miss", question=question, cache_key=cache_key)

        # 0) 오프토픽 차단 — MongoDB/Lambda/OpenAI를 전혀 호출하지 않는다.
        if not self.check_topic(question):
            log_event(log, "off_topic_blocked", question=question)
            return self.return_response(question, NOT_POPUP_QUESTION_MESSAGE)

        # ② MongoDB 조회 (RAG)
        try:
            with timed(log, "search_mongodb", question=question):
                search_condition, popup_info_list = self.search_mongodb(question)
        except ChatbotError as e:
            log.error("mongo_search_failed", extra={"extra_fields": {"error": e.message}})
            return self.return_response(question, "죄송합니다.\n\n정보를 조회하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

        log_event(
            log,
            "mongo_search_complete",
            question=question,
            results_count=len(popup_info_list),
            search_condition=search_condition,
        )

        if not popup_info_list:
            response = self.return_response(
                question, NO_RESULT_MESSAGE, intent=search_condition.get("intent"), search_condition=search_condition
            )
            # 결과 없음도 캐시해서 동일 질문 반복 시 Mongo 재조회를 피한다.
            await self.save_cache(cache_key, response)
            return response

        # ③ Prompt Context 생성 (MongoDB 결과 → 문자열)
        intent = search_condition.get("intent", "recommend")
        context_text = self.build_prompt_context(popup_info_list)

        # ④⑤⑥ API Gateway를 통해 Lambda 호출
        #      (Input Guardrail → Prompt 생성 → OpenAI → Output Guardrail은 Lambda 내부에서 수행)
        try:
            answer = await self.call_lambda(question, context_text)
        except ChatbotError as e:
            log.error("lambda_call_failed", extra={"extra_fields": {"error": e.message, "code": e.code}})
            return self.return_response(
                question,
                "죄송합니다.\n\n답변을 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                intent=intent,
                search_condition=search_condition,
                context=popup_info_list,
            )

        response = self.return_response(
            question, answer, intent=intent, search_condition=search_condition, context=popup_info_list
        )

        # ⑦ Redis 저장 (TTL)
        await self.save_cache(cache_key, response)

        # ⑧ 사용자 응답 반환
        return response
