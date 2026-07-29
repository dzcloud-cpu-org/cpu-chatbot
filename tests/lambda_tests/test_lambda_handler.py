import json
from unittest.mock import AsyncMock, patch

import pytest

import lambda_handler
from guardrail.input_guardrail import NOT_POPUP_QUESTION_MESSAGE
from llm.client import OpenAITimeoutError

CONTEXT = '[{"title": "성수 캐릭터 팝업", "source_url": "https://example.com/1"}]'


def _event(body: dict) -> dict:
    return {"body": json.dumps(body, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_process_chat_request_blocks_off_topic_without_calling_llm():
    with patch("lambda_handler.generate_chat_response", new_callable=AsyncMock) as mock_llm:
        answer = await lambda_handler.process_chat_request("오늘 점심 뭐 먹지?", "")

    mock_llm.assert_not_called()
    assert answer == NOT_POPUP_QUESTION_MESSAGE


@pytest.mark.asyncio
async def test_process_chat_request_success():
    with patch("lambda_handler.generate_chat_response", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "성수 캐릭터 팝업\n상세페이지: https://example.com/1"
        answer = await lambda_handler.process_chat_request("성수 팝업 알려줘", CONTEXT)

    mock_llm.assert_awaited_once()
    assert "성수 캐릭터 팝업" in answer


def test_handler_returns_400_for_missing_question():
    response = lambda_handler.handler(_event({}), None)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_handler_returns_200_for_guardrail_blocked_question():
    with patch("lambda_handler.generate_chat_response", new_callable=AsyncMock) as mock_llm:
        response = lambda_handler.handler(_event({"question": "오늘 날씨 어때?", "context": ""}), None)

    mock_llm.assert_not_called()
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["answer"] == NOT_POPUP_QUESTION_MESSAGE


def test_handler_returns_200_with_answer_on_success():
    with patch("lambda_handler.generate_chat_response", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "성수 캐릭터 팝업\n상세페이지: https://example.com/1"
        response = lambda_handler.handler(_event({"question": "성수 팝업 알려줘", "context": CONTEXT}), None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "성수 캐릭터 팝업" in body["answer"]


def test_handler_returns_504_on_llm_timeout():
    with patch(
        "lambda_handler.generate_chat_response", new_callable=AsyncMock, side_effect=OpenAITimeoutError("timeout")
    ):
        response = lambda_handler.handler(_event({"question": "성수 팝업 알려줘", "context": CONTEXT}), None)

    assert response["statusCode"] == 504
    body = json.loads(response["body"])
    assert body["error"]["code"] == "LLM_TIMEOUT"
