"""
backend/config/settings.py

FastAPI(K8s Pod) 쪽 환경변수. MongoDB 접속 정보는 기존 services/mongo_service.py가
자체적으로 os.environ["MONGODB_URI"]를 읽으므로 여기서 중복 선언하지 않는다
(이미 잘 동작하는 코드를 그대로 재사용 — 아래 REDIS_HOST 등과 다르게 이 파일에서
다시 선언하지 않는 이유).

K8s 배포 시 실제 값 예시:
    REDIS_HOST=redis.default.svc.cluster.local   (Redis가 같은 클러스터의 Service)
    REDIS_PORT=6379
    REDIS_PASSWORD=<Secret에서 주입>
    MONGODB_URI=mongodb://mongodb.default.svc.cluster.local:27017
    LAMBDA_API_URL=https://xxxxx.execute-api.ap-northeast-2.amazonaws.com/Prod/chat
    LAMBDA_API_KEY=<API Gateway API Key>

지금은(Kubernetes 이전 작업 중) 위 실제 값이 없으므로 전부 로컬 기본값으로
두되, 이 파일 밖의 코드(redis_service.py, lambda_client.py)는 어떤 값이든
환경변수만 읽어서 그대로 쓰므로 나중에 값만 바꾸면 코드 변경 없이 연결된다.
"""

from __future__ import annotations

import os


def _get_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


class Settings:
    # --- Redis (K8s 내부 Service, 아직 미배포 — 로컬 기본값) ---
    REDIS_HOST: str = os.environ.get("REDIS_HOST", "localhost")
    REDIS_PORT: int = _get_int("REDIS_PORT", 6379)
    REDIS_PASSWORD: str = os.environ.get("REDIS_PASSWORD", "")
    REDIS_TTL_SECONDS: int = _get_int("REDIS_TTL_SECONDS", 3600)
    CACHE_ENABLED: bool = _get_bool("CACHE_ENABLED", True)
    REDIS_TIMEOUT_SECONDS: float = float(os.environ.get("REDIS_TIMEOUT_SECONDS", "1.5"))

    # --- Lambda (API Gateway 경유 호출, 아직 미배포 — URL/Key 비어있으면 fail-safe 처리) ---
    LAMBDA_API_URL: str = os.environ.get("LAMBDA_API_URL", "")
    LAMBDA_API_KEY: str = os.environ.get("LAMBDA_API_KEY", "")
    LAMBDA_TIMEOUT_SECONDS: float = float(os.environ.get("LAMBDA_TIMEOUT_SECONDS", "30"))

    # --- Logging ---
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


settings = Settings()
