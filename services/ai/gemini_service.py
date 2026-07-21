# gemini_service.py

"""
Gemini API 연동 모듈 (google-genai SDK 기반)

기존에는 REST API를 httpx로 직접 호출했지만, 아래 두 가지 이유로 공식
google-genai SDK로 전면 교체한다.

1. AI Studio에서 신규 발급되는 키가 전부 AQ...(Auth Key) 형식으로 바뀌었고,
   raw REST 호출은 인증 헤더/쿼리 처리를 직접 신경 써야 했지만
   공식 SDK는 이 인증 방식 변화를 내부적으로 알아서 처리해준다.

2. SDK가 모델 목록·에러 코드 등 API 변경 사항을 라이브러리 업데이트만으로
   따라가므로, Gemini 쪽 스펙이 또 바뀌어도 이 파일을 직접 고칠 일이 줄어든다.

환경변수 GEMINI_API_KEY 필요 (AQ... Auth Key를 그대로 넣으면 된다).
실패 시 logger.error로 에러 코드/메시지를 콘솔에 그대로 남긴다 (디버깅용).
"""


from __future__ import annotations

import json
import logging
import os
import re

from typing import Any, Dict, Optional

from google import genai
from google.genai import errors, types


logger = logging.getLogger(__name__)


GEMINI_MODEL = "gemini-flash-latest"


# genai.Client는 내부적으로 커넥션을 관리하므로 요청마다 새로 만들지 않고 재사용한다.

_client: Optional[genai.Client] = None



class GeminiAPIError(RuntimeError):
    pass



def _get_client() -> genai.Client:
    global _client

    if _client is not None:
        return _client


    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise GeminiAPIError(
            "GEMINI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일/ 확인하세요."
        )


    _client = genai.Client(api_key=api_key)

    return _client



def _extract_json(text: str) -> Dict[str, Any]:
    """
    모델 응답에서 순수 JSON만 추출한다.

    response_mime_type="application/json"을 지정해도
    코드블록(```json)이나 불필요한 텍스트가 포함될 수 있기 때문에
    안전하게 JSON만 추출한다.
    """

    cleaned = text.strip()


    # Markdown 코드블록 제거
    cleaned = re.sub(r"```json", "", cleaned)
    cleaned = re.sub(r"```", "", cleaned)

    cleaned = cleaned.strip()


    # JSON 객체 부분 추출
    start = cleaned.find("{")
    end = cleaned.rfind("}")


    if start == -1 or end == -1:
        raise ValueError("JSON 형식을 찾을 수 없습니다.")


    cleaned = cleaned[start:end + 1]


    try:
        return json.loads(cleaned)


    except json.JSONDecodeError as e:
        logger.error(
            "Gemini 응답 JSON 파싱 실패 | error=%s | raw_text=%r",
            e,
            cleaned,
        )
        raise



# =============================================================================
# 추천 API용 Gemini 호출
# =============================================================================

# 추천 일정 생성 기능에서 사용하는 함수.
#
# response_mime_type="application/json"을 지정하여
# Gemini가 JSON 형태로 응답하도록 한다.
#
# 반환값:
#
# {
#     "schedule": [...],
#     "route_summary": "..."
# }
#
# main.py의 추천 API(/planner/recommend, /planner/replan 등)에서 사용한다.

# =============================================================================


async def generate_structured_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4
) -> Dict[str, Any]:
    """
    Gemini에 system+user 프롬프트를 보내고,
    JSON 객체로 파싱된 응답을 반환한다.
    """


    client = _get_client()


    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=temperature,

        # Gemini가 JSON 형태로 응답하도록 설정
        response_mime_type="application/json",

        # 추가하면 Gemini가 깨진 JSON을 거의 생성하지 않는다.
        # response_schema=RecommendResponse,
    )


    try:

        # 주의: 이전에는 같은 config/user_prompt로 generate_content를 두 번 호출하고 있었다.
        # (실수로 복사되어 남아있던 코드 — Gemini를 쓸 때마다 매번 API 호출 비용이 2배로 나가는 원인이었다.)
        # 호출은 반드시 한 번만 한다.
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=config,
        )

        print("=" * 80)
        print("현재 사용하는 모델 :", GEMINI_MODEL)
        print("=" * 80)

    except errors.APIError as e:

        # SDK가 표준화해서 던져주는 에러
        # HTTP status에 해당하는 code와 message를 그대로 확인 가능

        logger.error(
            "Gemini API 호출 실패 | code=%s | message=%s",
            getattr(e, "code", "?"),
            getattr(e, "message", str(e)),
        )


        raise GeminiAPIError(
            f"Gemini API 호출 실패 (code={getattr(e, 'code', '?')}): "
            f"{str(getattr(e, 'message', str(e)))[:500]}"
        )


    except Exception as e:

        logger.error(
            "Gemini 호출 중 예상치 못한 오류 | error=%s",
            e
        )

        raise GeminiAPIError(
            f"Gemini 호출 중 예상치 못한 오류: {e}"
        )



    text = response.text



    if not text:

        # 안전 필터 등에 의해 정상 응답이지만 텍스트가 없는 경우

        logger.error(
            "Gemini가 빈 응답을 반환했습니다 | response=%s",
            response,
        )

        raise GeminiAPIError(
            f"Gemini가 빈 응답을 반환했습니다: {response}"
        )



    return _extract_json(text)





# =============================================================================
# 챗봇용 Gemini 호출
# =============================================================================

# MongoDB에서 검색한 팝업 데이터를 기반으로
# 사용자에게 자연스럽게 설명하는 챗봇 전용 함수.
#
# 추천 API와 달리 JSON이 아닌 일반 텍스트(Text)를 반환한다.
#
# response_mime_type를 지정하지 않으면
# Gemini가 자연어 문장으로 응답한다.
#
# routers/chatbot.py에서 사용한다.

# =============================================================================


async def generate_chat_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
) -> str:
    """
    Gemini에 system+user 프롬프트를 보내고,
    자연어(Text) 형태의 응답을 반환한다.
    """


    client = _get_client()


    config = types.GenerateContentConfig(

        system_instruction=system_prompt,

        temperature=temperature,

        # response_mime_type를 지정하지 않으면
        # 일반 텍스트(Text) 응답을 반환한다.

    )


    try:

        response = await client.aio.models.generate_content(

            model=GEMINI_MODEL,

            contents=user_prompt,

            config=config,

        )


        print("=" * 80)
        print(repr(response.text))
        # repr(response.text)는 \n \r \t 까지 보여준다.
        # Gemini 응답 디버깅용
        print("=" * 80)



    except errors.APIError as e:


        logger.error(
            "Gemini API 호출 실패 | code=%s | message=%s",
            getattr(e, "code", "?"),
            getattr(e, "message", str(e)),
        )


        raise GeminiAPIError(
            f"Gemini API 호출 실패 (code={getattr(e, 'code', '?')}): "
            f"{str(getattr(e, 'message', str(e)))[:500]}"
        )



    except Exception as e:


        logger.error(
            "Gemini 호출 중 예상치 못한 오류 | error=%s",
            e
        )


        raise GeminiAPIError(
            f"Gemini 호출 중 예상치 못한 오류: {e}"
        )



    text = response.text



    if not text:

        logger.error(
            "Gemini가 빈 응답을 반환했습니다 | response=%s",
            response,
        )


        raise GeminiAPIError(
            f"Gemini가 빈 응답을 반환했습니다: {response}"
        )



    # 챗봇은 JSON이 아닌 자연어(Text)를 그대로 반환한다.

    return text