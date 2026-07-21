# services/ai/claude_service.py
from __future__ import annotations

import json
import logging
import os
import re

from typing import Any, Dict, Optional

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-3-5-haiku-latest"

_client: Optional[AsyncAnthropic] = None


class ClaudeAPIError(RuntimeError):
    pass


def _get_client() -> AsyncAnthropic:
    global _client

    if _client is not None:
        return _client

    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ClaudeAPIError(
            "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다."
        )

    _client = AsyncAnthropic(api_key=api_key)

    return _client


def _extract_json(text: str) -> Dict[str, Any]:

    cleaned = text.strip()

    cleaned = re.sub(r"```json", "", cleaned)
    cleaned = re.sub(r"```", "", cleaned)

    cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("JSON 형식을 찾을 수 없습니다.")

    cleaned = cleaned[start:end + 1]

    return json.loads(cleaned)


async def generate_structured_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
) -> Dict[str, Any]:

    client = _get_client()

    try:

        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            temperature=temperature,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        )

        print("=" * 80)
        print("현재 사용하는 모델 :", CLAUDE_MODEL)
        print("=" * 80)

    except Exception as e:
        logger.error("Claude 호출 실패 | %s", e)
        raise ClaudeAPIError(str(e))

    text = response.content[0].text

    return _extract_json(text)


async def generate_chat_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
) -> str:

    client = _get_client()

    try:

        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            temperature=temperature,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        )

    except Exception as e:
        logger.error("Claude 호출 실패 | %s", e)
        raise ClaudeAPIError(str(e))

    return response.content[0].text