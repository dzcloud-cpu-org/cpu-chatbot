"""
backend/utils/logger.py

FastAPI가 K8s Pod로 뜨므로 stdout으로 나가는 로그는 컨테이너 로그 드라이버 →
(예: Fluent Bit/CloudWatch Agent, 클러스터 구성에 따라 다름)로 수집된다. 어떤
수집기를 쓰든 "한 줄 JSON"이 필드 기반 검색에 유리하므로 형태는 그대로 유지한다.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from backend.config.settings import settings


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.handlers = [handler]

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    plain logging.Logger를 그대로 반환한다.

    주의: logging.LoggerAdapter(logger, {})로 감싸면 안 된다 — LoggerAdapter의
    기본 process()가 kwargs["extra"]를 self.extra(빈 dict)로 덮어써버려서,
    이 파일의 log_event()/timed()가 넘기는 extra_fields가 전부 무시된다
    (구조화 로그 필드가 하나도 안 남는 조용한 버그였다).
    """
    _configure_once()
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info(event, extra={"extra_fields": {"event": event, **fields}})


@contextmanager
def timed(logger: logging.Logger, event: str, **fields: Any) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(logger, event, latency_ms=latency_ms, **fields)
