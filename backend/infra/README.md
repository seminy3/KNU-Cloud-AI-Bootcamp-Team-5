# infra — CDK 앱 (role 3 백엔드/인프라)

AWS CDK(TypeScript) 앱이다. 스택 다섯 개가 있다: 엔드포인트 연결 시험(`SignMedEndpointTestStack`), 흐름 ①·② 백엔드(`SignMedFlow1BackendStack`), CI/CD OIDC(`SignMedCicdOidcStack`), 모니터링(`SignMedMonitoringStack`), 웹앱 호스팅(`SignMedWebappHostingStack`).

## 구성

| 파일 | 내용 |
|---|---|
| `bin/app.ts` | CDK 앱 진입점. 리전 `ap-northeast-2` 고정, 계정 ID는 배포 자격증명에서 자동(`CDK_DEFAULT_ACCOUNT`). 스택 5개 선언. |
| `lib/endpoint-test-stack.ts` | `EndpointTestStack`. 엔드포인트 시험 Lambda + 최소 권한 역할. |
| `lib/flow1-backend-stack.ts` | `Flow1BackendStack`. WebSocket API + recognize/finalize/doctorUtterance Lambda + S3 + DynamoDB + Cognito. |
| `lib/cicd-oidc-stack.ts` | `CicdOidcStack`. GitHub Actions OIDC Provider + 배포 역할. |
| `lib/monitoring-stack.ts` | `MonitoringStack`. CloudWatch 대시보드·알람 + AWS Budgets. |
| `lib/webapp-hosting-stack.ts` | `WebappHostingStack`. 웹앱(React) 정적 호스팅 S3 + CloudFront(OAC, HTTPS, SPA fallback). |
| `test/*.test.ts` | CDK assertions 테스트. |

### SignMedEndpointTestStack

- **Lambda** `sign-word-model-test` — Python 3.12, 타임아웃 30초, 메모리 256MB, 환경변수 `ENDPOINT_NAME=sign-word-model`. 코드는 [`../lambda/endpoint_test/handler.py`](../lambda/endpoint_test/handler.py).
- **IAM** — `sagemaker:InvokeEndpoint`를 `endpoint/sign-word-model` ARN으로만 허용.
- **LogGroup** 보존 1주. **출력** `FunctionName`, `EndpointArn`.

### SignMedFlow1BackendStack (흐름 ①·②)

- **WebSocket API** — 라우트 `recognize`, `finalize`, `doctorUtterance`(`$request.body.action` 기반). 스테이지 `dev`.
- **recognize Lambda** (흐름 ①) — keypoints → SageMaker, 단어 서버 누적. env `ENDPOINT_NAME`, `TABLE_NAME`.
- **finalize Lambda** (흐름 ①) — 누적 단어 → Bedrock → Polly → S3 → DynamoDB. env `AUDIO_BUCKET`/`TABLE_NAME`/`POLLY_VOICE`/`MODEL_ID`/`BEDROCK_REGION`/`LABELS_PATH`/`HISTORY_TURNS`.
- **doctorUtterance Lambda** (흐름 ②) — 의사 발화 텍스트 → DynamoDB `role=doctor` 저장. env `TABLE_NAME`.
- **S3** 음성 버킷(퍼블릭 차단, 1일 만료). **DynamoDB** 대화 기록/단어 버퍼(PK `sessionId`, SK `ts`, on-demand).
- **Cognito Identity Pool** (흐름 ②) — 비인증 게스트 역할에 `transcribe:StartStreamTranscription(WebSocket)`만 부여. 브라우저가 Transcribe에 직접 접속.
- **IAM 최소 권한** — recognize: `sagemaker:InvokeEndpoint`(엔드포인트 ARN). finalize: `bedrock:InvokeModel`, `polly:SynthesizeSpeech`, 해당 S3/DDB. 세 Lambda 모두: `execute-api:ManageConnections`.
- **출력** `WebSocketUrl`, `AudioBucketName`, `ConversationTableName`, `IdentityPoolId`.
- **패키징** — 로컬 번들링으로 `common/`(finalize는 `llm/`·`data/labels.csv`도)을 자산에 동봉. Docker 불필요. `anthropic[bedrock]` SDK는 **Lambda Layer**(`AnthropicLayer`)로 finalize에 붙는다. 레이어는 배포용 synth/deploy 때 pip로 Lambda 런타임 휠을 설치하고, 테스트·빠른 synth에서는 `--context skipLayerInstall=true`(테스트는 자동)로 건너뛴다.

### SignMedCicdOidcStack (CI/CD)

- **GitHub OIDC Provider** `token.actions.githubusercontent.com` (clientId `sts.amazonaws.com`).
- **배포 역할** `signmed-github-deploy` — GitHub Actions가 OIDC로 맡는다. 신뢰 범위는 `repo:seminy3/KNU-Cloud-AI-Bootcamp-Team-5:*`(context `githubRepo`로 변경). CDK 부트스트랩 역할(`cdk-*`) AssumeRole + CloudFormation 권한.
- **출력** `DeployRoleArn` — 이 값을 GitHub 저장소 시크릿 `AWS_DEPLOY_ROLE_ARN`에 넣으면 `.github/workflows/ci.yml`의 deploy job이 액세스 키 없이 배포한다.

### SignMedMonitoringStack (모니터링)

- **CloudWatch 대시보드** `signmed-backend` — Lambda별 호출/에러/지연(p95) 위젯.
- **에러 알람** — Lambda별 Errors ≥ 1. `--context lambdaNames="fnA,fnB"`로 실제 함수명 지정(기본은 엔드포인트 시험 Lambda).
- **AWS Budgets** — 월 예산 경보(`--context budgetUsd=20`, 기본 10 USD). `--context alertEmail=you@ex.com`을 주면 80% 실제/100% 예상 도달 시 이메일 알림.

### SignMedWebappHostingStack (웹앱 호스팅)

- **S3** 정적 호스팅 버킷(퍼블릭 차단). **CloudFront** 배포 — OAC로만 버킷 접근, HTTPS 강제, SPA fallback(403/404 → `/index.html` 200).
- 프론트 빌드 산출물은 이 스택이 올리지 않는다. 배포 후 업로드:
  ```
  aws s3 sync <빌드폴더> s3://<WebappBucketName> --delete
  aws cloudfront create-invalidation --distribution-id <DistributionId> --paths "/*"
  ```
- 음성 mp3는 이 CloudFront가 아니라 presigned S3 URL로 전달한다(흐름 ①). 여기 CloudFront는 웹앱 전용.
- **출력** `WebappBucketName`, `DistributionDomainName`(접속: `https://<이 값>`), `DistributionId`.

## 사전 준비

```
cd backend/infra
npm install
```

## 자격증명이 없을 때 (현재 상태): synth까지

AWS 자격증명 없이도 템플릿 생성·검토와 테스트는 가능하다.

```
npm run build          # TypeScript 컴파일
npx cdk synth          # CloudFormation 템플릿 생성 (cdk.out/)
npx jest               # 스택 assertions 테스트
```

`cdk synth`는 로컬에서 템플릿만 만들어 보는 것이라 자격증명이 필요 없다. 생성된 템플릿은 `cdk.out/`에서 볼 수 있다. **배포(`cdk deploy`)만 자격증명이 필요하다.** 즉 지금은 "코드 완성 · 배포 대기" 상태다.

## 자격증명이 생기면: 배포

```
# 1) 자격증명 설정 (예: 환경변수 또는 aws configure)
#    키는 절대 저장소에 커밋하지 않는다.

# 2) 계정에 CDK를 처음 쓰는 경우 한 번만 부트스트랩
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2

# 3) 배포 (스택 선택)
npx cdk deploy SignMedEndpointTestStack     # 엔드포인트 시험
npx cdk deploy SignMedFlow1BackendStack     # 흐름 ① 백엔드
npx cdk deploy --all                        # 둘 다
```

엔드포인트 시험 스택 배포 후 출력 `FunctionName`의 Lambda를 콘솔 테스트 탭에서 `test_1~3.json`으로 실행한다. 절차와 결과 기록은 [`../../docs/ENDPOINT_TEST_RESULTS.md`](../../docs/ENDPOINT_TEST_RESULTS.md).

흐름 ① 스택 배포 후 출력 `WebSocketUrl`을 프론트에 전달한다. finalize Lambda가 Bedrock 호출에 쓰는 `anthropic[bedrock]` SDK는 `AnthropicLayer`로 자동 포함된다(배포 시 pip 설치). 배포 머신에 `pip`이 있어야 레이어 설치가 로컬에서 되고, 없으면 Docker 번들링으로 폴백한다.

> CI/CD(GitHub Actions)로 배포할 때는 액세스 키 대신 OIDC로 AWS에 연결한다(아키텍처 문서 방침). 설정은 `SignMedCicdOidcStack` 참고.

## 참고

- 계정 ID는 하드코딩하지 않는다. 배포 시 자격증명의 계정이 쓰이고, ARN은 `arn:aws:sagemaker:ap-northeast-2:<account>:endpoint/sign-word-model` 형태로 자동 구성된다.
- 엔드포인트 이름을 바꾸려면 `cdk.json`의 `context.endpointName` 또는 `--context endpointName=...`로 넘긴다.
