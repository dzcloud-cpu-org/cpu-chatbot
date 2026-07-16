"""
AI 팝업 챗봇 - FastAPI PoC

실행:
    uvicorn main:app --reload --port 8000

엔드포인트:
    GET /health                     - 헬스체크
    GET /chatbot/search             - 챗봇 (레거시, 규칙 기반 키워드 추출)
    GET /chatbot/ai-search          - 챗봇 (AI 검색형, 2단계 Gemini 파이프라인)

※ 기존에 있던 팝업 추천/일정 생성 기능(/api/v1/planner/recommend, /api/v1/planner/replan)은
   챗봇 기능만 남기기로 하면서 제거했습니다.
   해당 기능이 다시 필요해지면 이전 버전의 main.py, models.py,
   prompts/planner_prompt.py, services/retrieval.py를 참고해 복원하면 됩니다.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

# routers.chatbot -> services.mongo_service는 모듈이 import되는 시점에
# 곧바로 os.environ에서 MONGODB_URI를 읽으므로, 반드시 라우터를 import하기 전에
# load_dotenv()를 먼저 호출해서 .env 값이 채워지도록 한다.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.chatbot import router as chatbot_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="AI 팝업 챗봇 API (PoC)",
    description="팝업스토어 정보 검색 AI 챗봇 백엔드",
    version="0.2.0",
)

# 개발 단계 PoC용 CORS 전체 허용 (운영 배포 시 origin 제한 필요)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY"))}


app.include_router(chatbot_router)
