"""
backend/services/redis_service.py

Redis는 FastAPI에서만 사용한다. K8s 환경에서는 REDIS_HOST가 보통
`<redis-service-name>.<namespace>.svc.cluster.local` 형태의 클러스터 내부
Service DNS를 가리킨다(REDIS_PORT/REDIS_PASSWORD는 Service/Secret 값 그대로).

# Redis Key 설계: 질문 원문 vs SHA-256 해시

옵션 A) 질문 원문을 그대로 키로 사용
    장점: `KEYS`/`SCAN`으로 봤을 때 어떤 질문인지 바로 보여 디버깅이 쉽다.
    단점: 한글/이모지/개행 등으로 키 길이가 가변적이고, 조사/띄어쓰기/대소문자가
      다르면 의미가 같아도 다른 키로 취급되어 히트율이 떨어지며, 질문 원문이
      Redis 키(및 로그/모니터링 도구)에 그대로 노출된다.

옵션 B) 정규화 후 SHA-256 해시 — **채택**
    장점: 항상 고정 64자 키, 특수문자 이슈 없음. `normalize_question()`(공백
      정리+소문자화) 후 해시하므로 표현이 살짝 달라도 동일 키로 캐시 재사용률이
      더 높다. 원문이 키에 남지 않아 노출 관점에서도 안전.
    단점: 해시만 봐서는 질문 내용을 알 수 없어, 디버깅을 위해 로그에 question과
      cache_key를 함께 남긴다.

**운영 환경 추천**: 정규화 + SHA-256 해시. 지금은 "정규화 후 완전 일치"만 캐시
히트로 인정하는 exact-match 캐시이며, 히트율이 낮다고 판단되면 질문 임베딩
기반 semantic cache(코사인 유사도 임계값 이상이면 히트)로 확장할 수 있다.

# TTL / 캐시 무효화

기본 REDIS_TTL_SECONDS=3600(1시간) — 팝업 운영기간/예약 정보가 하루 단위로
바뀔 수 있어 너무 길면 종료된 팝업을 운영 중이라 안내하는 사고가 날 수 있고,
너무 짧으면 캐시 효과가 없다. 캐시 무효화는 기본적으로 TTL 자연 만료에
맡기고, 데이터 갱신 배치가 특정 캐시를 능동적으로 지우고 싶다면 발급된 키를
별도 Redis Set(`chatbot:keys:all`)에 등록해두었다가 순회 삭제하는 방식을
얹을 수 있다(현재는 키 네임스페이스만 분리해두어 확장 여지를 남김).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Optional

import redis.asyncio as redis
from redis.exceptions import RedisError

from backend.config.settings import settings
from backend.utils.logger import get_logger

log = get_logger(__name__)

_CACHE_KEY_PREFIX = "chatbot:cache:"

# K8s Pod가 재시작되지 않는 한 재사용되는 프로세스 전역 커넥션 풀.
_client: Optional[redis.Redis] = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
            decode_responses=True,
        )
    return _client


def normalize_question(question: str) -> str:
    text = question.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def build_cache_key(question: str) -> str:
    normalized = normalize_question(question)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{_CACHE_KEY_PREFIX}{digest}"


async def get_cached_response(cache_key: str) -> Optional[dict]:
    """Redis 장애 시 예외를 올리지 않고 None(캐시 미스로 취급)을 반환한다 (fail-open).

    redis-py에 socket_connect_timeout/socket_timeout을 넘겨도, 대상 포트에
    아무 프로세스도 없는 상황(Redis가 아직 배포되지 않은 지금 같은 경우)에서
    실제로 20초 넘게 걸리는 것을 로컬에서 확인했다(플랫폼/커넥션 재시도 로직에
    따라 클라이언트 자체 타임아웃이 그대로 지켜지지 않을 수 있음). 캐시 계층
    장애가 응답 전체를 몇 초씩 지연시켜서는 안 되므로, asyncio.wait_for로
    상한선을 한 번 더 강제한다.
    """
    if not settings.CACHE_ENABLED:
        return None

    try:
        client = _get_client()
        raw = await asyncio.wait_for(client.get(cache_key), timeout=settings.REDIS_TIMEOUT_SECONDS)
    except (RedisError, asyncio.TimeoutError) as e:
        log.warning("redis_get_failed", extra={"extra_fields": {"event": "redis_get_failed", "error": str(e)}})
        return None

    if raw is None:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("redis_cache_corrupted", extra={"extra_fields": {"cache_key": cache_key}})
        return None


async def save_response(cache_key: str, response: dict, ttl_seconds: Optional[int] = None) -> bool:
    """Redis 장애 시 예외를 올리지 않고 False를 반환한다 (fail-open)."""
    if not settings.CACHE_ENABLED:
        return False

    ttl = ttl_seconds if ttl_seconds is not None else settings.REDIS_TTL_SECONDS

    try:
        client = _get_client()
        await asyncio.wait_for(
            client.set(cache_key, json.dumps(response, ensure_ascii=False), ex=ttl),
            timeout=settings.REDIS_TIMEOUT_SECONDS,
        )
        return True
    except (RedisError, asyncio.TimeoutError) as e:
        log.warning("redis_set_failed", extra={"extra_fields": {"event": "redis_set_failed", "error": str(e)}})
        return False


def _reset_client_for_test(client: Optional[redis.Redis] = None) -> None:
    global _client
    _client = client
