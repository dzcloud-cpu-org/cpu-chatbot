# openai_service.py
"""
OpenAI API 연동 모듈

Gemini와 동일한 인터페이스를 사용하기 위해
generate_structured_response()
generate_chat_response()

함수를 동일하게 제공한다.

환경변수
OPENAI_API_KEY 필요
"""

from __future__ import annotations

import json
import logging
import os
import re

from typing import Any, Dict, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 사용할 모델
OPENAI_MODEL = "gpt-4.1-mini"

_client: Optional[AsyncOpenAI] = None


class OpenAIAPIError(RuntimeError):
    pass


def _get_client() -> AsyncOpenAI:
    global _client

    if _client is not None:
        return _client

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise OpenAIAPIError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다."
        )

    _client = AsyncOpenAI(
        api_key=api_key
    )

    return _client


def _extract_json(text: str) -> Dict[str, Any]:
    """
    GPT 응답에서 JSON만 추출
    """

    cleaned = text.strip()

    cleaned = re.sub(r"```json", "", cleaned)
    cleaned = re.sub(r"```", "", cleaned)

    cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("JSON 형식을 찾을 수 없습니다.")

    cleaned = cleaned[start:end + 1]

    try:
        return json.loads(cleaned)

    except json.JSONDecodeError as e:

        logger.error(
            "OpenAI JSON 파싱 실패 | %s",
            e
        )

        logger.error(cleaned)

        raise
        
# JSON 생성 (1단계)
async def generate_structured_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
) -> Dict[str, Any]:

    client = _get_client()

    try:

        response = await client.chat.completions.create(

            model=OPENAI_MODEL,

            temperature=temperature,

            response_format={
                "type": "json_object"
            },

            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )

        print("=" * 80)
        print("현재 사용하는 모델 :", OPENAI_MODEL)
        print("=" * 80)

    except Exception as e:

        logger.error(
            "OpenAI 호출 실패 | %s",
            e
        )

        raise OpenAIAPIError(str(e))

    text = response.choices[0].message.content

    if not text:

        raise OpenAIAPIError(
            "OpenAI가 빈 응답을 반환했습니다."
        )

    return _extract_json(text)
    
# 챗봇 답변 생성 (2단계)
async def generate_chat_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
) -> str:

    client = _get_client()

    try:

        response = await client.chat.completions.create(

            model=OPENAI_MODEL,

            temperature=temperature,

            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )

    except Exception as e:

        logger.error(
            "OpenAI 호출 실패 | %s",
            e
        )

        raise OpenAIAPIError(str(e))

    text = response.choices[0].message.content

    if not text:

        raise OpenAIAPIError(
            "OpenAI가 빈 응답을 반환했습니다."
        )

    return text