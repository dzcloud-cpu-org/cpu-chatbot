FROM python:3.12-slim

# 작업 디렉토리
WORKDIR /app

# requirements 먼저 복사 (레이어 캐시 활용)
COPY requirements.txt .

# 라이브러리 설치
RUN pip install --no-cache-dir -r requirements.txt

# 프로젝트 전체 복사 (.dockerignore에 명시된 파일/폴더는 제외됨 —
# lambda/, tests/, docs/ 등은 이 이미지에 필요 없어 제외했다)
COPY . .

# K8s SecurityContext(runAsNonRoot: true)와 짝을 맞추기 위해 non-root 사용자로 실행.
# UID/GID를 고정값(10001)으로 둬야 k8s/deployment.yaml의 securityContext와 일치한다.
RUN groupadd -g 10001 appuser && useradd -u 10001 -g appuser -M appuser
USER 10001

# FastAPI 포트
EXPOSE 7000

# K8s에서는 livenessProbe/readinessProbe가 이 역할을 대신하지만,
# docker run 단독 실행이나 docker-compose 로컬 개발 시에도 상태를 확인할 수 있도록 남겨둔다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7000/health', timeout=2)" || exit 1

# 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7000"]
