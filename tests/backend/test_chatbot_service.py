from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from backend.services import redis_service
from backend.services.chatbot_service import ChatbotService
from backend.utils.exceptions import LambdaTimeoutError, MongoUnavailableError

QUESTION = "성수에 있는 캐릭터 팝업 알려줘"
SEARCH_CONDITION = {"intent": "recommend", "keywords": ["캐릭터"], "location": "성수"}
CONTEXT = [
    {"title": "성수 캐릭터 팝업", "location": "성수", "period": "2026-07-01 ~ 2026-08-01", "source_url": "https://example.com/1"}
]


@pytest.fixture(autouse=True)
def fake_redis_client():
    fake = fakeredis.FakeAsyncRedis(decode_responses=True)
    redis_service._reset_client_for_test(fake)
    yield fake
    redis_service._reset_client_for_test(None)


@pytest.mark.asyncio
async def test_cache_hit_skips_mongo_and_lambda():
    service = ChatbotService()
    cache_key = redis_service.build_cache_key(QUESTION)
    cached_response = {
        "question": QUESTION,
        "intent": "recommend",
        "search_condition": SEARCH_CONDITION,
        "results_count": 1,
        "answer": "캐시된 답변입니다.",
        "sources": [],
        "cached": False,
    }
    await redis_service.save_response(cache_key, cached_response)

    with patch("backend.services.chatbot_service.build_context") as mock_mongo, \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda:
        result = await service.chatbot_service(QUESTION)

    mock_mongo.assert_not_called()
    mock_lambda.assert_not_called()
    assert result["answer"] == "캐시된 답변입니다."
    assert result["cached"] is True


@pytest.mark.asyncio
async def test_off_topic_question_blocked_without_calling_mongo_or_lambda():
    service = ChatbotService()

    with patch("backend.services.chatbot_service.build_context") as mock_mongo, \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda:
        result = await service.chatbot_service("오늘 점심 뭐 먹지?")

    mock_mongo.assert_not_called()
    mock_lambda.assert_not_called()
    assert "팝업스토어 관련 질문만" in result["answer"]


@pytest.mark.asyncio
async def test_empty_mongo_result_returns_no_result_message_without_calling_lambda():
    service = ChatbotService()

    with patch("backend.services.chatbot_service.build_context", return_value=(SEARCH_CONDITION, [])) as mock_mongo, \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda:
        result = await service.chatbot_service(QUESTION)

    mock_mongo.assert_called_once()
    mock_lambda.assert_not_called()
    assert result["answer"] == "관련 정보를 찾을 수 없습니다."


@pytest.mark.asyncio
async def test_full_pipeline_calls_lambda_and_caches_result():
    service = ChatbotService()

    with patch("backend.services.chatbot_service.build_context", return_value=(SEARCH_CONDITION, CONTEXT)), \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda:
        mock_lambda.return_value = "성수 캐릭터 팝업\n위치: 성수\n상세페이지: https://example.com/1"

        result = await service.chatbot_service(QUESTION)

    mock_lambda.assert_awaited_once()
    sent_question, sent_context = mock_lambda.call_args.args
    assert sent_question == QUESTION
    assert "성수 캐릭터 팝업" in sent_context  # Context가 JSON 문자열로 전달됨
    assert "성수 캐릭터 팝업" in result["answer"]
    assert result["cached"] is False

    # 동일 질문을 다시 요청하면 캐시가 히트되어 Mongo/Lambda가 다시 호출되지 않아야 한다.
    with patch("backend.services.chatbot_service.build_context") as mock_mongo_again, \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda_again:
        second_result = await service.chatbot_service(QUESTION)

    mock_mongo_again.assert_not_called()
    mock_lambda_again.assert_not_called()
    assert second_result["cached"] is True
    assert second_result["answer"] == result["answer"]


@pytest.mark.asyncio
async def test_mongo_failure_returns_friendly_message():
    service = ChatbotService()

    with patch(
        "backend.services.chatbot_service.build_context",
        side_effect=MongoUnavailableError("connection refused"),
    ), patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock) as mock_lambda:
        result = await service.chatbot_service(QUESTION)

    mock_lambda.assert_not_called()
    assert "오류가 발생했습니다" in result["answer"]


@pytest.mark.asyncio
async def test_lambda_timeout_returns_friendly_message():
    service = ChatbotService()

    with patch("backend.services.chatbot_service.build_context", return_value=(SEARCH_CONDITION, CONTEXT)), \
         patch("backend.services.lambda_client.invoke_chat", new_callable=AsyncMock, side_effect=LambdaTimeoutError("timeout")):
        result = await service.chatbot_service(QUESTION)

    assert "답변을 생성하는 중 오류가 발생했습니다" in result["answer"]
