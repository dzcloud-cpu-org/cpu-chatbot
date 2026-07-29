"""
backend/prompts/context_formatter.py

아키텍처 다이어그램의 "③ Prompt Context 생성" 단계. MongoDB 검색 결과
(backend/services/mongodb_service.py가 만든 popup_info_list, list[dict])를
Lambda에 그대로 넘길 수 있는 문자열(context)로 변환한다.

주의: 최종 System Prompt(누가 어떻게 답할지) 조립은 더 이상 여기서 하지
않는다 — 이번 아키텍처에서는 그 책임이 Lambda(lambda/prompts/system_prompt.py)
로 넘어갔다. 이 파일은 순수하게 "MongoDB 결과 → 문자열" 변환만 담당한다.
"""

from __future__ import annotations

import json


def build_context_text(popup_info_list: list[dict]) -> str:
    """검색 결과 리스트를 JSON 문자열로 직렬화한다. 사람이 읽는 문장으로 미리
    가공하지 않는 이유는, JSON 그대로 넘겨야 Lambda의 Output Guardrail이
    "답변에 등장한 URL/PII가 context 안에도 있는지"를 substring 검사로 판별할
    수 있기 때문이다(정보 손실 없이 그대로 전달)."""
    return json.dumps(popup_info_list, ensure_ascii=False)
