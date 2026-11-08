# backend — 백엔드 / 인프라 (role 3)

수어 ↔ 음성 번역 서비스의 백엔드·인프라다. 지금 단계의 목표는 **SageMaker Serverless 엔드포인트 `sign-word-model`을 실제로 호출해 검증**하는 것이다(권한 문제로 미뤄졌던 연결 시험 뚫기). 이후 WebSocket API · Bedrock · Polly · Transcribe · DynamoDB(대화 기록) · CI/CD · 모니터링이 여기에 추가된다.

> 설계 변경(MVP): 음성 → 수어 방향은 수어 영상 클립 재생 없이 **자막으로만** 보여 준다. 따라서 수어 영상 어순 변환(Bedrock)·단어→영상 매핑 DB는 범위에서 제외된다. 대화 기록 저장용 DynamoDB는 그대로 유지한다(흐름 ①에서 history로 쓰인다).

## 디렉터리

```
backend/
  infra/                        # CDK(TypeScript) 앱
    lib/endpoint-test-stack.ts    # 엔드포인트 시험 Lambda + 최소 권한 역할
    lib/flow1-backend-stack.ts    # 흐름 ① : WebSocket API + recognize/finalize + S3 + DynamoDB
    test/                         # CDK assertions 테스트
    README.md                     # synth / deploy 방법
  common/                       # Lambda 공용 모듈 (배포 시 각 Lambda에 동봉)
    sagemaker_client.py           # SageMaker InvokeEndpoint 래퍼
    speech.py                     # Polly 합성 + S3 저장 + presigned URL
    conversation.py               # DynamoDB 대화 기록 put/query
    ws.py                         # WebSocket @connections push 유틸
  lambda/
    endpoint_test/handler.py      # 콘솔/CDK 공용 SageMaker 시험 핸들러
    recognize/handler.py          # 흐름 ① : keypoints → 단어 1개 인식
    finalize/handler.py           # 흐름 ① : 단어열 → 문장 → 음성 → 저장
    doctor_utterance/handler.py   # 흐름 ② : 의사 발화 텍스트 → 대화 기록 저장
  layers/
    anthropic/requirements.txt    # finalize용 anthropic[bedrock] Lambda Layer 정의
  mock/
    server.py                     # 프론트 개발용 로컬 Mock WebSocket 서버
  scripts/
    invoke_endpoint.py            # 로컬 자동 검증 스크립트
  tests/                        # 파이썬 단위 테스트
  requirements-dev.txt          # 테스트/스크립트용 파이썬 의존성
```

관련 문서: 엔드포인트 규격 [`../ENDPOINT.md`](../ENDPOINT.md) · 원래 시험 절차/오류표 [`../ENDPOINT_TEST.md`](../ENDPOINT_TEST.md) · 결과 기록 [`../docs/ENDPOINT_TEST_RESULTS.md`](../docs/ENDPOINT_TEST_RESULTS.md).

## 엔드포인트 검증: 세 가지 길

같은 핸들러(`lambda/endpoint_test/handler.py`)를 쓰므로 어느 길이든 결과는 같아야 한다. 가진 접근 권한에 맞게 고른다.

| 길 | 필요한 것 | 방법 |
|---|---|---|
| A. 콘솔에서 직접 | AWS 콘솔 접근 (CLI 자격증명 불필요) | Lambda 함수를 콘솔에서 만들고 핸들러 코드 붙여넣기 → 테스트 탭에서 `test_N.json` 실행 |
| B. CDK로 배포 | 배포 자격증명 또는 CI/CD | `cd infra && npx cdk deploy` → 생성된 Lambda를 콘솔에서 실행 |
| C. 로컬 스크립트 | CLI 자격증명 | `python scripts/invoke_endpoint.py` 로 자동 판정 |

절차 상세는 [`../docs/ENDPOINT_TEST_RESULTS.md`](../docs/ENDPOINT_TEST_RESULTS.md).

## 프론트 협업 (계약 + Mock)

- **WebSocket API 계약**: [`../docs/WEBSOCKET_API.md`](../docs/WEBSOCKET_API.md) — recognize/finalize/doctorUtterance의 요청·응답·에러 스키마를 한곳에 고정. 프론트(role 4)와 공유하는 약속.
- **로컬 Mock 서버**: [`mock/README.md`](mock/README.md) — 배포 전에 프론트가 계약대로 붙여볼 수 있는 고정 응답 WebSocket 서버(`ws://localhost:8765`). `python backend/mock/server.py`.

## 흐름 ① 백엔드 (수어 → 음성·자막)

브라우저가 좌표를 보내면 단어를 인식하고, 모인 단어를 문장·음성으로 바꿔 돌려주는 경로. WebSocket **두 액션**으로 나뉜다. 전체 메시지 규격은 [`../docs/WEBSOCKET_API.md`](../docs/WEBSOCKET_API.md).

| 액션 | Lambda | 하는 일 | 응답 |
|---|---|---|---|
| `recognize` | recognize | keypoints → SageMaker(`sign-word-model`) → 단어 1개, 세션 버퍼에 누적 | `{type:"word", word, candidates, words, count}` |
| `finalize` | finalize | 누적 단어열 → Bedrock 문장 → Polly 음성 → S3 → DynamoDB, 버퍼 비움 | `{type:"speech", words, sentence, audioUrl, needsConfirm}` |

**단어 누적은 서버가 한다.** recognize가 인식한 단어를 `sessionId`별로 DynamoDB 버퍼에 쌓고, finalize가 그 버퍼를 읽어 문장을 만든 뒤 비운다. 프론트는 문장 끝에서 `finalize`만 호출하면 되고, `words`를 직접 보낼 필요가 없다(보내면 그 값을 우선 사용 — 하위호환).

메시지 계약 예:
```jsonc
// 브라우저 → 서버 (단어 인식, 여러 번)
{ "action": "recognize", "sessionId": "abc123", "keypoints": [ /* (30,49,2) */ ] }
// 서버 → 브라우저 : word 는 방금 인식한 단어, words/count 는 현재까지 누적 현황
{ "type": "word", "word": "병원", "candidates": [ {"name":"병원","prob":0.62}, ... ],
  "words": ["나","병원"], "count": 2 }

// 브라우저 → 서버 (문장 확정, 한 번). words 생략 가능(서버 버퍼 사용)
{ "action": "finalize", "sessionId": "abc123" }
// 서버 → 브라우저
{ "type": "speech", "words": ["나","병원","가다"],
  "sentence": "저 병원에 가려고요", "audioUrl": "https://...", "needsConfirm": false }
```

- 서버 버퍼: 기존 `ConversationTable`에 `sessionId` + SK `ts=0`(role=`buffer`) 아이템으로 저장. 대화 기록(ts=epoch millis)과 섞이지 않고, history 조회에서도 제외된다.
- `needsConfirm`: 문장 생성이 애매하거나(fallback/ambiguous) 음성 합성이 실패하면 `true`. 프론트는 "확인 필요" 표시.
- 음성 합성이 실패해도 자막(`sentence`)은 돌려준다(`audioUrl` = null). 화면이 멈추지 않게.
- 재사용: `finalize`는 role 2의 `llm/`(sentence.py, handler.py)과 `data/labels.csv`를 배포 패키지에 동봉해 호출한다.

인프라는 `infra/lib/flow1-backend-stack.ts`(`SignMedFlow1BackendStack`). WebSocket API + Lambda 3개(recognize/finalize/doctorUtterance) + S3(음성, 1일 만료) + DynamoDB(대화 기록) + Cognito(흐름 ②) + 최소 권한 IAM. 배포·synth는 [`infra/README.md`](infra/README.md) 참고.

> Lambda 패키징: CDK가 로컬 번들링으로 `common/`(과 finalize는 `llm/`·`labels.csv`)을 각 Lambda 자산에 모은다. Docker 불필요. finalize가 Bedrock 호출에 쓰는 `anthropic[bedrock]` SDK는 Lambda Layer(`backend/layers/anthropic/`)로 제공된다. 배포용 synth/deploy 때 pip로 Lambda 런타임용 휠을 설치해 레이어를 만들고, 테스트·빠른 synth에서는 설치를 건너뛴다(context `skipLayerInstall`).

## 흐름 ② 백엔드 (음성 → 자막)

의료진이 말하면 농인이 자막으로 보는 경로. **음성 → 텍스트 변환은 브라우저가 Transcribe Streaming에 직접 연결**해 수행한다(아키텍처 문서 방식). 백엔드는 두 가지만 담당한다.

1. **Cognito Identity Pool** — 브라우저가 로그인 없이 임시 자격증명을 받아 Transcribe에 직접 접속하게 한다. 게스트(비인증) 역할에 Transcribe 스트리밍 권한만 부여(최소 권한).
2. **doctorUtterance 액션** — 브라우저가 변환한 의사 발화 텍스트를 받아 대화 기록(DynamoDB `role="doctor"`)에 저장한다. 이 기록은 흐름 ①의 `history`로 쓰여 환자 문장 생성의 맥락이 된다.

> 설계 변경(MVP): 음성 → 수어 영상 변환은 범위 밖. 자막만 표시한다.

메시지 계약 예:
```jsonc
// 브라우저 → 서버 (Transcribe로 변환한 의사 발화를 전달)
{ "action": "doctorUtterance", "sessionId": "abc123", "text": "어디가 아프세요?" }
// 서버 → 브라우저 (저장 확인)
{ "type": "transcript", "text": "어디가 아프세요?" }
```

- 저장 실패해도 자막 표시는 막지 않는다(best-effort). `sessionId`가 없으면 저장은 생략하고 확인만 돌려준다.
- 브라우저 쪽 Transcribe 연결에는 배포 출력 `IdentityPoolId`와 리전(`ap-northeast-2`)이 필요하다. 프론트(role 4)에 전달한다.

## CI/CD (GitHub Actions + OIDC)

`.github/workflows/ci.yml`:

- **verify** (모든 push·PR) — pytest + CDK build/jest + `cdk synth --all`. **자격증명 불필요**라 지금 바로 동작한다.
- **deploy** (main push 또는 수동) — OIDC로 AWS 역할을 맡아 `cdk deploy --all`. 액세스 키를 쓰지 않는다(아키텍처 문서 방침). GitHub 시크릿 `AWS_DEPLOY_ROLE_ARN`이 없으면 자동으로 건너뛴다.

배포 역할은 `SignMedCicdOidcStack`이 만든다. 한 번만 준비하면 된다:

1. 자격증명으로 `npx cdk deploy SignMedCicdOidcStack` (OIDC Provider + 배포 역할 생성)
2. 출력 `DeployRoleArn`을 GitHub 저장소 → Settings → Secrets → `AWS_DEPLOY_ROLE_ARN`에 등록
3. 이후 main에 머지하면 Actions가 OIDC로 자동 배포

## 모니터링 (CloudWatch + Budgets)

`SignMedMonitoringStack`:

- CloudWatch 대시보드 `signmed-backend` — Lambda 호출/에러/지연(p95)
- Lambda 에러 알람(Errors ≥ 1)
- AWS Budgets 월 예산 경보 — 예산 초과 전 이메일 알림(비용 관리)

배포 예: `npx cdk deploy SignMedMonitoringStack --context lambdaNames="recognize함수명,finalize함수명" --context budgetUsd=20 --context alertEmail=team@example.com` (함수명은 flow1 배포 후 실제 이름으로).

## 지금 할 수 있는 것 (자격증명 없이)

```
# 1) 파이썬 단위 테스트 (핸들러 + 검증 스크립트 로직)
python -m venv .venv            # 저장소 루트에서
.venv\Scripts\python -m pip install -r backend/requirements-dev.txt
.venv\Scripts\python -m pytest backend/tests/

# 2) CDK 템플릿 생성 + 스택 테스트
cd backend/infra
npm install
npm run build
npx cdk synth        # 자격증명 불필요
npx jest
```

## 현재 상태 체크리스트

완료 (자격증명 없이, 단위테스트 + synth 검증됨)
- [x] SageMaker 시험 핸들러 + 단위 테스트
- [x] 엔드포인트 시험 CDK 스택 + assertions 테스트, `cdk synth` 성공
- [x] 콘솔 수동 테스트 가이드 + 결과 기록 문서
- [x] 로컬 자동 검증 스크립트 + 단위 테스트
- [x] 흐름 ① 공용 모듈(SageMaker/speech/conversation/ws) + 단위 테스트
- [x] 흐름 ① recognize/finalize Lambda(서버 단어 누적) + 단위 테스트
- [x] 흐름 ② doctorUtterance Lambda(의사 발화 저장) + 단위 테스트
- [x] 흐름 ①·② CDK 스택(WebSocket API 3라우트 + S3 + DynamoDB + Cognito + 최소 권한) + assertions, `cdk synth` 성공
- [x] CI/CD GitHub Actions 워크플로(verify는 자격증명 없이 동작) + OIDC 배포 역할 CDK 스택 + assertions
- [x] 모니터링 CDK 스택(CloudWatch 대시보드·알람 + AWS Budgets) + assertions
- [x] finalize용 anthropic[bedrock] Lambda Layer(배포 시 설치, 테스트는 skip) + assertions
- [x] 웹앱 호스팅 CDK 스택(S3 + CloudFront OAC/HTTPS/SPA fallback) + assertions

완료 (실제 AWS 호출로 검증)
- [x] `scripts/invoke_endpoint.py`로 `test_1~3` 실제 호출 — 세 케이스 1위 일치 + 확률 로컬과 일치. 결과는 [`../docs/ENDPOINT_TEST_RESULTS.md`](../docs/ENDPOINT_TEST_RESULTS.md)
- [x] 콜드 스타트 실측 — 첫 호출 9154ms, 웜 67~105ms. "3초 이내"는 워밍업 필요

대기 (배포 시점/비용 팀 상의 후)
- [ ] `cdk deploy`로 다섯 스택 배포 (엔드포인트 시험 · 흐름 ①·② · OIDC · 모니터링 · 웹앱 호스팅) — 과금 시작, 팀 예산 합의 후
- [ ] 웹앱: 프론트 빌드 산출물을 `s3 sync`로 업로드(또는 CI), CloudFront 무효화 — 프론트(role 4) 산출물 준비 후
- [ ] OIDC 역할 ARN을 GitHub 시크릿 `AWS_DEPLOY_ROLE_ARN`에 등록 → Actions 자동 배포 활성화
- [ ] 흐름 ① : WebSocket 실연결 e2e 테스트
- [ ] 흐름 ② : 브라우저 Transcribe 직결 e2e(IdentityPoolId 전달), 프론트와 메시지 계약 확정
- [ ] 모니터링: 배포 후 실제 Lambda 함수명으로 `lambdaNames` 지정, 예산/이메일 설정

## 자격증명은 누가?

이 프로젝트의 AWS 계정(추정 `379995600109`)에 접근할 IAM 자격증명은 계정 운영 담당(인프라)이 발급한다. 본인이 관리 권한이 있으면 IAM 사용자/키 또는 SSO로 직접 만들고, 아니면 담당자에게 요청한다. **액세스 키는 저장소에 커밋하지 않는다.**
