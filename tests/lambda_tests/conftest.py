"""
tests/lambda_tests/conftest.py

왜 lambda/ 테스트가 tests/backend/와 분리된 별도 서브디렉터리에 있는가:

저장소 루트에는 이미 `prompts/`(search_condition.py가 쓰는 임베딩/키워드
프롬프트 패키지)가 있고, 이번 요청으로 `lambda/prompts/`(Lambda의 System
Prompt 패키지)도 새로 생겼다. 두 패키지 모두 같은 이름 `prompts`를 최상위
모듈로 사용하므로, 만약 `backend`와 `lambda`용 테스트를 같은 pytest 프로세스
(같은 sys.modules 캐시)에서 함께 수집하면 어느 한쪽의 `prompts`가 다른 쪽을
가려버려 ImportError가 난다.

실제 배포 환경에서는 이 충돌이 전혀 발생하지 않는다 — backend(Docker 이미지)와
lambda(AWS SAM CodeUri: lambda/)는애초에 서로 다른 프로세스/컨테이너에서
실행되는 완전히 독립된 배포 단위이기 때문이다. 그래서 로컬 테스트도 그 경계를
그대로 반영해 별도 pytest 프로세스로 나눠 실행한다.

    pytest tests/backend -v        # backend/ 유닛 테스트
    pytest tests/lambda_tests -v   # lambda/ 유닛 테스트 (별도 프로세스)

이 conftest.py는 `tests/lambda_tests`를 대상으로 pytest를 실행했을 때만
로드되므로, backend 테스트 프로세스에는 이 sys.path 조작이 전혀 영향을 주지
않는다.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LAMBDA_DIR = os.path.join(_REPO_ROOT, "lambda")

if _LAMBDA_DIR not in sys.path:
    sys.path.append(_LAMBDA_DIR)
