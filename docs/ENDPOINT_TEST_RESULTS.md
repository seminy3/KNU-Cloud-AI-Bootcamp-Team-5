# 엔드포인트 연결 시험 결과 기록

SageMaker Serverless 엔드포인트 `sign-word-model`을 실제로 호출해 (1) 모델 로드·추론, (2) 1위 라벨이 로컬과 같은지, (3) 콜드/웜 응답 시간을 확인하고 그 결과를 여기에 적는다.

규격·요청/응답 형식은 [`../ENDPOINT.md`](../ENDPOINT.md), 원래 시험 절차와 오류표는 [`../ENDPOINT_TEST.md`](../ENDPOINT_TEST.md)에 있다. 이 문서는 **실행 방법 요약 + 결과를 채우는 칸**이다.

- 엔드포인트: `sign-word-model`
- 리전: `ap-northeast-2`
- 테스트 요청 파일: 저장소 루트의 `test_1.json`, `test_2.json`, `test_3.json`
- 테스트 핸들러 코드: [`../backend/lambda/endpoint_test/handler.py`](../backend/lambda/endpoint_test/handler.py)

## 로컬 기대값 (비교 기준)

최종 모델이 로컬에서 낸 결과다. 엔드포인트의 1위 이름이 이와 다르면 모델 로드나 전처리에 문제가 있다는 뜻이고, 확률은 소수 셋째 자리까지 거의 같아야 한다.

| 파일 | 정답 | 로컬 1위 확률 |
|---|---|---|
| `test_1.json` | 아프다 | 0.603 |
| `test_2.json` | 설사 | 0.547 |
| `test_3.json` | 상처/다치다 | 0.607 |

## 실행 방법

두 가지 길 중 접근 권한에 맞는 것을 고른다. 둘 다 같은 핸들러 코드를 쓰므로 결과는 같아야 한다.

### 길 A. 콘솔에서 직접 (AWS 계정 콘솔 접근만 있으면 됨, CLI 자격증명 불필요)

1. Lambda 콘솔에서 서울 리전(`ap-northeast-2`)에 함수를 새로 만든다. 런타임 **Python 3.12**.
2. 구성 → 일반 구성에서 제한 시간을 **30초**로 올린다. (기본 3초면 콜드 스타트에서 먼저 끊긴다.)
3. 환경 변수 `ENDPOINT_NAME` = `sign-word-model` 을 추가한다.
4. 실행 역할에 이 엔드포인트에 대한 `sagemaker:InvokeEndpoint` 권한을 준다. (아래 "권한" 참고)
5. 코드에 [`../backend/lambda/endpoint_test/handler.py`](../backend/lambda/endpoint_test/handler.py) 내용을 붙여 넣고 배포한다. 핸들러 설정은 `handler.lambda_handler`.
6. 테스트 탭에서 새 이벤트를 만들고 `test_1.json` 내용을 그대로 붙여 넣어 실행한다. 이 호출이 콜드 스타트다.
7. 결과의 `elapsed_ms`와 `result.top_k[0]`을 아래 표에 적는다.
8. 같은 이벤트를 바로 한 번 더 실행해 웜 `elapsed_ms`를 적는다.
9. `test_2.json`, `test_3.json`도 실행해 1위가 정답과 같은지 본다.

> 함수 응답은 핸들러가 감싼 형태다. 정상이면 `{"elapsed_ms": ..., "result": {...}}`, 오류면 `{"elapsed_ms": ..., "error": "...", "message": "..."}`. `result.top_k[0].name` 이 1위 이름이다.

### 길 B. CDK로 배포 (자격증명 또는 CI/CD가 있을 때)

`backend/infra`에 테스트 Lambda와 최소 권한 역할이 코드로 있다. 배포 방법은 [`../backend/infra/README.md`](../backend/infra/README.md) 참고.

```
cd backend/infra
npm install
npm run build
npx cdk deploy SignMedEndpointTestStack
```

배포되면 출력(`FunctionName`)에 나온 함수를 콘솔 테스트 탭에서 길 A의 6~9단계처럼 실행한다. (권한·환경변수는 스택이 이미 설정해 둔다.)

### 길 C. 로컬 스크립트로 자동 검증 (CLI 자격증명이 있을 때)

```
python backend/scripts/invoke_endpoint.py
```

`test_1~3.json`을 호출해 1위 라벨 일치와 확률 근사를 자동 판정하고 PASS/FAIL과 응답 시간을 출력한다. 자세한 내용은 [`../backend/README.md`](../backend/README.md).

## 권한 (가장 흔한 실패 원인)

Lambda 실행 역할에 아래 정책이 있어야 한다. 리소스는 `*` 대신 이 엔드포인트 ARN으로만 한정한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeSignWordModelEndpoint",
      "Effect": "Allow",
      "Action": "sagemaker:InvokeEndpoint",
      "Resource": "arn:aws:sagemaker:ap-northeast-2:<ACCOUNT_ID>:endpoint/sign-word-model"
    }
  ]
}
```

`<ACCOUNT_ID>`는 계정 ID `379995600109`다 (`aws sts get-caller-identity`로 확인됨). CDK(길 B)는 이 정책을 자동으로 만든다.

## 결과 기록

> 길 C(로컬 스크립트 `backend/scripts/invoke_endpoint.py`)로 실제 호출해 기록함. 세 케이스 모두 통과.

| 기록할 값 | test_1 (콜드/1회차) | test_1 (웜/2회차) | test_2 | test_3 |
|---|---|---|---|---|
| `elapsed_ms` | 9154 | — (연속 호출) | 105 | 67 |
| 1위 이름 | 아프다 | 아프다 | 설사 | 상처/다치다 |
| 1위 확률 | 0.603085 | 0.603085 | 0.546731 | 0.606508 |
| 로컬과 일치? | 예 (기대 0.603) | 예 | 예 (기대 0.547) | 예 (기대 0.607) |

- 실행 날짜: 2026-11-06
- 실행자: sunghyun (IAM user, 계정 379995600109)
- 실행 방법(A/B/C): C — 로컬 스크립트 `invoke_endpoint.py` (CLI 자격증명)
- 특이사항:
  - 세 케이스 모두 1위 라벨 일치 + 확률이 로컬 기대값과 소수 셋째 자리까지 일치 → 컨테이너의 모델 로드·전처리가 로컬과 동일하게 동작함 확인.
  - 첫 호출(콜드 스타트) 9154ms, 이후 웜 호출 67~105ms. 콜드가 길어 "동작 후 3초 이내" 목표를 맞추려면 시연 전 워밍업 호출이 필요(본 백엔드에서 대응).
  - test_1~3 은 스크립트가 순차 호출하므로 test_1 의 1회차가 콜드, test_2/test_3 은 이미 웜 상태의 값이다.

### 판정 기준

- 1위 이름이 정답(아프다 / 설사 / 상처/다치다)과 같으면 모델 로드·전처리 정상.
- 1위 확률이 로컬 기대값(0.603 / 0.547 / 0.607)과 소수 셋째 자리까지 거의 같으면 OK. 크게 다르면 전처리 또는 모델 버전 차이를 의심.
- 콜드 `elapsed_ms`가 크면(수 초) Serverless 콜드 스타트다. 시연 전 미리 호출해 깨워 두는 전략이 필요하다 (본 백엔드 작업에서 다룸).

## 오류가 나면

응답의 `error` 필드(또는 콘솔 로그)로 원인을 본다. 상세 대응은 [`../ENDPOINT_TEST.md`](../ENDPOINT_TEST.md)의 오류표를 참고한다.

| `error` / 증상 | 원인과 확인 |
|---|---|
| `AccessDeniedException` | 실행 역할에 `sagemaker:InvokeEndpoint` 권한이 없다. 위 "권한" 정책을 붙인다. |
| `ValidationError`, 엔드포인트 못 찾음 | 리전이나 `ENDPOINT_NAME`이 다르다. |
| `ModelError` (4xx·5xx) | 컨테이너가 요청 처리 중 실패. CloudWatch `/aws/sagemaker/Endpoints/sign-word-model` 로그에서 원인 확인. |
| 메모리 부족, 응답 없음 | Serverless 메모리 2048MB 부족 가능. 3072/4096MB로 올려 재시도(모델 담당과 상의). |
| 제한 시간 초과 | Lambda 타임아웃을 늘린다. 콜드 스타트가 길다는 뜻이면 그 시간을 기록한다. |
