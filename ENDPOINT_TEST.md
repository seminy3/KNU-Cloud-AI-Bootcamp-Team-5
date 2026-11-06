# 엔드포인트 호출 시험

PC의 CLI 자격 증명 없이 AWS 콘솔의 Lambda 테스트 기능으로 서버리스 엔드포인트를 호출해 본다. 이 시험으로 확인하는 것은 세 가지다.

1. 컨테이너가 모델을 정상으로 읽고 응답하는지
2. 응답의 1위가 정답과 같은지 (모델과 전처리가 로컬과 같게 동작하는지)
3. 첫 호출(콜드 스타트)과 이후 호출의 시간

## 시험 요청

`test_1.json`, `test_2.json`, `test_3.json`은 팀 val 클립 하나씩을 `keypoints` 요청(소수 6자리, 약 30KB)으로 만든 것이다. 이 클립들은 최종 모델이 로컬에서 맞힌 것이고, 최종 모델은 val 수어자도 학습에 포함했으므로 정확도 평가가 아니라 연결 시험용이다.

| 파일 | 정답 | 로컬 결과 (1위 확률) | 2·3위 |
|---|---|---|---|
| `test_1.json` | 아프다 | 아프다 0.603 | 생리통 0.004, 9 0.004 |
| `test_2.json` | 설사 | 설사 0.547 | 콧물 0.005, 모르다 0.005 |
| `test_3.json` | 상처/다치다 | 상처/다치다 0.607 | 가렵다 0.006, 내일 0.004 |

엔드포인트의 확률은 소수 셋째 자리까지 로컬과 거의 같아야 한다. 1위 이름이 다르면 모델 로드나 전처리에 문제가 있다는 뜻이다.

## Lambda 만들기

1. Lambda 콘솔에서 서울 리전(`ap-northeast-2`)에 함수를 새로 만든다. 런타임은 Python 3.12다.
2. 구성의 일반 구성에서 제한 시간을 30초로 올린다. 기본값 3초면 콜드 스타트에서 먼저 끊긴다.
3. 환경 변수 `ENDPOINT_NAME`에 엔드포인트 이름을 넣는다.
4. 실행 역할에 이 엔드포인트에 대한 `sagemaker:InvokeEndpoint` 권한을 준다.
5. 코드는 아래를 붙여 넣고 배포한다.

```python
import json
import os
import time

import boto3

rt = boto3.client('sagemaker-runtime', region_name='ap-northeast-2')


def lambda_handler(event, context):
    t = time.time()
    r = rt.invoke_endpoint(
        EndpointName=os.environ['ENDPOINT_NAME'],
        ContentType='application/json',
        Body=json.dumps(event),
    )
    body = json.loads(r['Body'].read())
    return {'elapsed_ms': round((time.time() - t) * 1000), 'result': body}
```

## 시험 순서

1. 테스트 탭에서 새 이벤트를 만들고 `test_1.json`의 내용을 그대로 붙여 넣어 실행한다. 이 호출이 콜드 스타트다. 오래 쉬었다면 수 초가 걸릴 수 있다.
2. 결과의 `elapsed_ms`와 `result.top_k[0]`을 기록한다.
3. 같은 이벤트를 바로 한 번 더 실행해 `elapsed_ms`를 기록한다. 이 값이 콜드 스타트가 아닌 호출 시간이다.
4. `test_2.json`, `test_3.json`도 실행해 1위가 정답과 같은지 본다.

| 기록할 값 | 1회차 (콜드) | 2회차 | test_2 | test_3 |
|---|---|---|---|---|
| `elapsed_ms` | | | | |
| 1위 이름 | | | | |
| 1위 확률 | | | | |

`elapsed_ms`는 Lambda에서 엔드포인트까지 왕복한 시간만 잰다. 프론트와 API Gateway 구간은 들어 있지 않다.

## 오류가 나면

| 증상 | 가능한 원인과 확인 |
|---|---|
| `AccessDeniedException` | Lambda 실행 역할에 `sagemaker:InvokeEndpoint` 권한이 없다. |
| `ValidationError`, 엔드포인트를 찾지 못함 | 리전이나 `ENDPOINT_NAME`이 다르다. |
| `ModelError` (상태 코드 4xx·5xx) | 컨테이너가 요청을 처리하다 실패했다. CloudWatch의 `/aws/sagemaker/Endpoints/<엔드포인트 이름>` 로그 그룹에서 원인을 본다. |
| 메모리 부족 로그, 응답 없음 | 서버리스 메모리 2048MB가 부족할 수 있다. 3072MB나 4096MB로 올려 다시 시도한다. |
| Lambda 제한 시간 초과 | 제한 시간을 늘린다. 콜드 스타트가 길다는 뜻이면 그 시간을 기록한다. |

Lambda를 만들 권한이 없으면 Lambda를 만들 수 있는 팀원에게 이 코드와 시험 요청 파일을 넘긴다.
