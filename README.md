# SignMed AI

병원에서 청각장애인 환자와 의료진이 서로 다른 기기로 대화하도록 돕는 통역 서비스다. 환자의 수어는 문장과 음성으로, 의료진의 말은 자막으로 바꿔 상대 화면에 보낸다.

KNU 클라우드 AI 부트캠프 5조 프로젝트다.

## 하는 일

| 방향 | 흐름 |
|---|---|
| 환자 → 의료진 | 웹캠 → 브라우저 MediaPipe(손·포즈 좌표) → 단어 인식(SageMaker) → 단어를 문장으로(Bedrock) → 음성 합성(Polly) → 의료진 화면에 문장과 음성 |
| 의료진 → 환자 | 마이크 → 음성 인식(Transcribe, 브라우저 직결) 또는 직접 입력 → 환자 화면에 자막 |

- 환자 화면: 손을 들면 녹화하고 내리면 단어를 인식한다. 틀린 단어는 후보 5개 중에서 고르거나 지울 수 있다. 단어를 모아 **문장 확정하기**를 누르면 문장이 된다.
- 의료진 화면: 음성 인식 결과가 입력창에 채워지고, 고쳐서 보낼 수 있다. 글로 바로 보내도 된다.
- 두 화면은 같은 대화 코드로 연결된다. 상단에 상대방 접속 여부가 보인다.

## 구조

```
브라우저(환자) ─┐                     ┌─ recognize ── SageMaker 서버리스 (단어 178개 분류)
브라우저(의료진)─┼─ API Gateway ───────┼─ finalize ─── Bedrock(문장) → Polly(음성) → S3(mp3, presigned URL)
               │  WebSocket          ├─ doctorUtterance, join, $disconnect
               │                     └─ DynamoDB (대화 기록·단어 버퍼·연결 목록, 24시간 만료)
               ├─ Cognito 게스트 자격증명 → Transcribe Streaming (ko-KR)
               └─ CloudFront → S3 (웹앱)
```

- 인프라는 CDK(TypeScript)로 만든다: `backend/infra/`
- 리전은 서울(ap-northeast-2)

## 저장소 구성

| 경로 | 내용 |
|---|---|
| [`frontend/`](frontend/) | React + TypeScript + Vite 웹앱, MediaPipe 변환기, 단어 구간 자동 자르기, Mock 서버 |
| [`backend/lambda/`](backend/lambda/) | WebSocket 액션별 Lambda (`recognize`, `finalize`, `doctorUtterance`, `join`, `disconnect`) |
| [`backend/common/`](backend/common/) | SageMaker 호출, 대화 기록, 음성 합성, 연결 목록·전달 |
| [`backend/infra/`](backend/infra/) | CDK 스택 (백엔드, 웹앱 호스팅, CI/CD OIDC, 모니터링) |
| [`llm/`](llm/) | 단어 → 문장 프롬프트와 생성 코드 |
| [`data/labels.csv`](data/labels.csv) | 인식 단어 178개 (AI Hub 86개 + 국립국어원 수어사전 92개) |
| [`docs/WEBSOCKET_API.md`](docs/WEBSOCKET_API.md) | 프론트 ↔ 백엔드 메시지 규격 |
| [`docs/DEMO_SCENARIO.md`](docs/DEMO_SCENARIO.md) | 시연 순서와 준비 사항 |
| [`frontend/docs/ml/`](frontend/docs/ml/) | 모델 입력 규격과 ML 쪽 검증 기록 |

AI Hub와 수어사전 원본·좌표 데이터는 저장소에 올리지 않는다(`.gitignore`).

## 로컬에서 실행

Node.js 22.12 이상이 필요하다.

```bash
cd frontend
npm install
cp .env.example .env.local    # 실제 백엔드에 붙이려면 VITE_WS_URL, VITE_COGNITO_IDENTITY_POOL_ID를 채운다
npm run mock                  # 백엔드 없이 개발할 때. ws://localhost:8765
npm run dev                   # http://localhost:5173
```

카메라·마이크는 `localhost` 또는 HTTPS에서만 동작한다.

테스트:

```bash
cd frontend && npm test                       # 변환기·응답 규격·구간 자르기
python -m pytest backend/tests/ -q            # Lambda·공용 모듈 (requirements-dev.txt 필요)
cd backend/infra && npx jest                  # CDK 스택
```

## 배포

```bash
cd backend/infra && npm ci
npx cdk deploy SignMedFlow1BackendStack       # WebSocket API, Lambda, DynamoDB, Cognito
npx cdk deploy SignMedWebappHostingStack SignMedCicdOidcStack
```

- 백엔드 출력값 `WebSocketUrl`, `IdentityPoolId`를 저장소 변수 `VITE_WS_URL`, `VITE_COGNITO_IDENTITY_POOL_ID`에 넣는다.
- 웹앱 출력값과 `FrontendDeployRoleArn`을 `WEBAPP_BUCKET`, `WEBAPP_DISTRIBUTION_ID`, `FRONTEND_DEPLOY_ROLE_ARN`에 넣으면 main에 프론트 변경이 합쳐질 때 자동으로 배포된다.
- 실제 주소는 저장소가 Public이고 로그인이 없어 여기에 적지 않는다. 팀 Discord로 공유한다.
- SageMaker 엔드포인트 `sign-word-model`은 CDK 밖에서 만들었다(ML 담당).

## 측정한 것 (2026-11-08, 서울 리전)

| 항목 | 결과 |
|---|---|
| 단어 인식 | 이후 호출 약 0.2초, 첫 호출 약 12초 |
| 문장 + 음성 | 이후 호출 약 2초, 첫 호출 약 11.5초 |
| 음성 인식 | Cognito 게스트 → Transcribe로 한국어 문장 인식 확인 (합성 음성) |
| 두 화면 전달 | 접속 상태, 환자 문장·음성, 의료진 자막, 끊김 알림 확인 |

첫 호출이 느리므로 시연 직전에 한 번씩 호출해 깨워 둔다.

## 한계

- **새로운 사람에 대한 인식 정확도는 아직 확인하지 못했다.** 배포 모델은 평가용 수어자까지 학습에 썼다. 웹캠 앞의 새 사람으로 잰 결과는 아직 없다.
- 단어 구간 자동 자르기의 기준값(손목 높이, 대기 시간)은 실제 웹캠으로 조정하지 않은 초기값이다.
- 어휘는 178개다. 수어사전 단어 92개는 수어자 1명 영상으로만 학습했다.
- 로그인이 없다. 대화 코드를 아는 사람은 그 대화에 들어올 수 있다. 호출 제한(초당 10회)과 대화 기록 24시간 만료로 위험을 줄였다.

## 역할

| 역할 | 범위 |
|---|---|
| ML | 수어 인식 모델 학습·평가, SageMaker 배포, 모델 입력 규격 |
| 데이터 + LLM, PM | 데이터 수집·전처리, 좌표 추출, Bedrock 문장 생성, 일정 관리 |
| 백엔드 + 인프라 | WebSocket·Lambda, Transcribe·Polly, CDK, CI/CD, 모니터링 |
| 프론트엔드 | 웹 UI, 브라우저 MediaPipe, 자막·음성 재생 |
