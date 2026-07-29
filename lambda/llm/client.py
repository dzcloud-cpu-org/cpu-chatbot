"""
lambda/llm/client.py

기존 services/ai/openai_service.py::generate_chat_response()를 그대로
재사용한다 — AsyncOpenAI 싱글턴, system_prompt/user_prompt 2개 인자,
client.chat.completions.create(...) 호출 방식 전부 동일하다. 이번 아키텍처는
Prompt 생성이 다시 Lambda 책임이라(lambda/prompts/system_prompt.py),
system_prompt와 user_prompt를 분리해서 받는 기존 시그니처를 그대로 유지할 수
있었다 — 이 파일이 기존 코드와 정말 거의 동일한 이유다.

이 Lambda는 별도 zip으로 독립 배포되므로(requirements.txt에 openai/pydantic만
있음) 상위 저장소의 services/ai/openai_service.py를 import하지 않고 동일한
코드를 여기 그대로 옮겨왔다.

OPENAI_API_KEY는 코드에 작성하지 않고 Lambda 환경변수로만 주입한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# --- 기존 services/ai/openai_service.py와 동일한 부분 ---

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

_client: Optional[AsyncOpenAI] = None


class OpenAICallError(RuntimeError):
    pass


class OpenAITimeoutError(OpenAICallError):
    pass


def _get_client() -> AsyncOpenAI:
    global _client

    if _client is not None:
        return _client

    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise OpenAICallError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    _client = AsyncOpenAI(api_key=api_key)

    return _client


OPENAI_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "8"))


async def generate_chat_response(system_prompt: str, user_prompt: str, temperature: float = 0.4) -> str:
    """기존 openai_service.generate_chat_response()와 동일한 시그니처/호출 방식.
    Lambda 하드 타임아웃보다 먼저 우리가 타임아웃을 감지하도록 asyncio.wait_for만 추가했다."""

    client = _get_client()

    async def _call() -> str:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        text = response.choices[0].message.content

        if not text:
            raise OpenAICallError("OpenAI가 빈 응답을 반환했습니다.")

        return text

    try:
        return await asyncio.wait_for(_call(), timeout=OPENAI_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise OpenAITimeoutError(
            f"OpenAI 응답이 {OPENAI_TIMEOUT_SECONDS}초 내에 도착하지 않았습니다."
        ) from e
    except OpenAICallError:
        raise
    except Exception as e:
        logger.error("OpenAI 호출 실패 | %s", e)
        raise OpenAICallError(f"OpenAI 호출 중 오류가 발생했습니다: {e}") from e
