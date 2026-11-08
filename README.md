# KNU-Cloud-AI-Bootcamp-Team-5

수어를 음성·자막으로, 음성을 자막으로 바꿔 주는 AWS 기반 실시간 양방향 통역 서비스.

> 설계 변경(MVP): 음성 → 수어 방향은 수어 영상 클립 재생 없이 **자막으로만** 보여 준다. 이에 따라 수어 영상 어순 변환·영상 클립 매핑 DB는 범위에서 제외됐다.

## 역할 구성

| 역할 | 범위 | 디렉터리 |
|---|---|---|
| 1 · ML | 수어 인식 모델 학습·평가, SageMaker 배포 | (모델 저장소) |
| 2 · 데이터 + LLM | 데이터 전처리, 좌표 추출, Bedrock 프롬프트 | [`llm/`](llm/), [`data/`](data/) |
| 3 · 백엔드 + 인프라 | API·WebSocket·Lambda, Transcribe/Polly, CDK, CI/CD, 모니터링 | [`backend/`](backend/) |
| 4 · 프론트엔드 | 웹 UI, 브라우저 MediaPipe, 자막·음성 재생 | (프론트 저장소) |

## 백엔드 / 인프라 (role 3)

진행 상황:
- **SageMaker 엔드포인트 연결 시험**: 권한 문제로 못 했던 호출 테스트를 자격증명 유무와 관계없이 할 수 있게 테스트 Lambda(CDK) + 콘솔 가이드 + 로컬 검증 스크립트 준비 완료.
- **흐름 ① 백엔드(수어 → 음성·자막)**: WebSocket 두 액션(`recognize`/`finalize`) 구조로 SageMaker → Bedrock → Polly → S3 경로 구현. 단어 누적은 서버가 담당. 공용 모듈 + Lambda + CDK 스택(WebSocket API·S3·DynamoDB·최소 권한 IAM).
- **흐름 ② 백엔드(음성 → 자막)**: 브라우저가 Cognito 임시 자격증명으로 Transcribe에 직접 연결(아키텍처 문서 방식). 백엔드는 Cognito Identity Pool과 의사 발화 저장 액션(`doctorUtterance`)을 제공해 흐름 ①의 대화 맥락과 연결. 수어 영상 변환은 범위 밖(자막만).
- **CI/CD**: GitHub Actions 워크플로(검증은 자격증명 없이 동작, 배포는 OIDC). 액세스 키를 GitHub에 올리지 않는다.
- **모니터링**: CloudWatch 대시보드·Lambda 에러 알람 + AWS Budgets 월 예산 경보.

모두 자격증명 없이 단위테스트(`pytest`)와 `cdk synth`까지 검증했고, 실제 배포/호출만 자격증명 대기 상태다.

- 백엔드 개요와 실행 방법: [`backend/README.md`](backend/README.md)
- CDK 앱: [`backend/infra/README.md`](backend/infra/README.md)
- WebSocket API 계약 (프론트 공유): [`docs/WEBSOCKET_API.md`](docs/WEBSOCKET_API.md)
- 프론트 개발용 Mock 서버: [`backend/mock/README.md`](backend/mock/README.md)
- 엔드포인트 규격: [`ENDPOINT.md`](ENDPOINT.md) · 시험 절차/오류표: [`ENDPOINT_TEST.md`](ENDPOINT_TEST.md)
- 시험 결과 기록: [`docs/ENDPOINT_TEST_RESULTS.md`](docs/ENDPOINT_TEST_RESULTS.md)

## 데이터 + LLM (role 2)

- 단어 라벨: [`data/labels.csv`](data/labels.csv)
- Bedrock 문장 생성 Lambda: [`llm/`](llm/)
