import fakeredis
import pytest

from backend.services import redis_service


@pytest.fixture(autouse=True)
def fake_redis_client():
    fake = fakeredis.FakeAsyncRedis(decode_responses=True)
    redis_service._reset_client_for_test(fake)
    yield fake
    redis_service._reset_client_for_test(None)


def test_build_cache_key_is_deterministic_and_normalizes():
    key_a = redis_service.build_cache_key("서울에 있는 팝업 알려줘")
    key_b = redis_service.build_cache_key("  서울에 있는 팝업 알려줘  ")
    key_c = redis_service.build_cache_key("서울에 있는 팝업 알려줘 ")
    assert key_a == key_b == key_c
    assert key_a.startswith("chatbot:cache:")


def test_build_cache_key_differs_for_different_questions():
    assert redis_service.build_cache_key("성수 팝업") != redis_service.build_cache_key("홍대 팝업")


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    cache_key = redis_service.build_cache_key("아직 캐시에 없는 질문")
    assert await redis_service.get_cached_response(cache_key) is None


@pytest.mark.asyncio
async def test_save_then_get_round_trip():
    cache_key = redis_service.build_cache_key("성수 팝업 알려줘")
    response = {"question": "성수 팝업 알려줘", "answer": "성수 팝업입니다.", "cached": False}

    saved = await redis_service.save_response(cache_key, response, ttl_seconds=60)
    assert saved is True

    cached = await redis_service.get_cached_response(cache_key)
    assert cached == response


@pytest.mark.asyncio
async def test_ttl_is_applied(fake_redis_client):
    cache_key = redis_service.build_cache_key("TTL 확인용 질문")
    await redis_service.save_response(cache_key, {"answer": "ok"}, ttl_seconds=60)

    ttl = await fake_redis_client.ttl(cache_key)
    assert 0 < ttl <= 60


@pytest.mark.asyncio
async def test_get_returns_none_on_corrupted_json(fake_redis_client):
    cache_key = redis_service.build_cache_key("손상된 캐시 질문")
    await fake_redis_client.set(cache_key, "{이건 유효한 JSON이 아님")

    assert await redis_service.get_cached_response(cache_key) is None
