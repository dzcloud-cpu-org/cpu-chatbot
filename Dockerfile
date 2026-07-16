FROM python:3.12-slim

# 작업 디렉토리
WORKDIR /app

# requirements 먼저 복사 (레이어 캐시 활용)
COPY requirements.txt .

# 라이브러리 설치
RUN pip install --no-cache-dir -r requirements.txt

# 프로젝트 전체 복사 (.dockerignore에 명시된 파일/폴더는 제외됨)
COPY . .

# FastAPI 포트
EXPOSE 7000

# 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7000"]
