# SageMaker 엔드포인트 정리

수어 단어 분류 모델 엔드포인트의 설정, 호출 방법, 요청·응답 형식을 정리한다. 상세 규격은 `INPUT_SPEC.md`의 2·3항에 있다.

## 엔드포인트

| 항목 | 값 |
|---|---|
| 이름 | `
sign-word-model` |
| 리전 | `ap-northeast-2` |
| 방식 | SageMaker Serverless |
| 서버리스 설정 | 메모리 2048MB, 최대 동시 호출 2 (측정한 값이 아니라 시작값) |
| 컨테이너 이미지 | `763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-inference:2.6.0-cpu-py312` |
| 환경 변수 | `SAGEMAKER_PROGRAM=inference.py` |
| 모델 파일 | `s3://signmed-ml-379995600109/models/final_model.tar.gz` |
| 추론 코드 | 모델 tar 안의 `code/inference.py` |

## 호출

- API는 `sagemaker-runtime`의 `InvokeEndpoint`다.
- `Content-Type`은 `application/json`만 받는다. 다른 값이면 서버가 오류를 낸다.
- Lambda 실행 역할에 이 엔드포인트에 대한 `sagemaker:InvokeEndpoint` 권한이 필요하다.

```python
import boto3, json

rt = boto3.client('sagemaker-runtime', region_name='ap-northeast-2')
r = rt.invoke_endpoint(
    EndpointName='sign-word-model',
    ContentType='application/json',
    Body=json.dumps({'top_k': 5, 'keypoints': kp}),
)
result = json.loads(r['Body'].read())
```

## 요청

```json
{"top_k": 5, "keypoints": [ [ [x, y], ... 49개 ], ... 30프레임 ]}
```

- `keypoints`는 (30, 49, 2) 모양이고 NaN이 없어야 한다.
- 좌표는 두 어깨의 중점을 원점, 두 어깨 사이 거리를 척도로 정규화한 값이며, 둘 다 픽셀 단위로 계산한다. 규칙은 `INPUT_SPEC.md`의 0항에 있다.
- `top_k`는 받을 후보 개수이고 기본은 5다.
- `keypoints` 대신 원시 `frames`(최대 900개)를 보내면 서버가 같은 규칙으로 변환한다. 요청 형식은 `INPUT_SPEC.md`의 2항에 있다.
- `keypoints`는 약 34~64KB다. 서버리스 엔드포인트의 요청 한도는 4MB다.

## 응답

항상 `application/json`이다.

```json
{"ok": true, "frames_in": 30, "frames_used": 30,
 "top_k": [{"label": "화상", "name": "화상", "label_id": 53, "word_id": 71, "prob": 0.62}, ...]}
```

| 필드 | 설명 |
|---|---|
| `label`, `label_id` | 결과를 식별하는 값이다. `label_id`는 0~177이고 팀 데이터셋 `index.csv`의 `label_id`와 같다. |
| `name` | 화면에 보일 이름이다. |
| `word_id` | 참고용이다. 수어사전 전용 클래스에는 없다. 결과 처리에 쓰지 않는다. |
| `prob` | softmax 확률이다. |

- `prob`는 label smoothing 때문에 맞았을 때도 낮게 나온다(팀 val 중앙값 0.45, 범위 0.04~0.73). 확신도보다 후보 순위로 쓰는 편이 안전하다.
- 확률 임계값은 정해지지 않았다.

## 오류와 주의

- 모양이 (30, 49, 2)가 아니거나, NaN이 있거나, `keypoints`와 `frames`가 모두 없으면 서버가 요청을 거절한다. Lambda에서 처리해 사용자에게 다시 시도를 안내해야 한다. 호출 쪽에서 어떤 예외로 나오는지는 확인하지 않았다.
- 서버는 `keypoints`의 모양과 NaN만 검사하므로 전부 0인 입력도 결과를 돌려준다. 손이 없는 구간은 프론트에서 걸러 보내지 않아야 한다.
- `ok: false`는 원시 `frames`를 보냈는데 쓸 수 있는 프레임이 5개 미만일 때 돌아온다(`reason` 포함).

## 아직 확인하지 못한 것

- 서버리스 콜드 스타트 시간과 응답 3초 요구의 충족 여부
- 네트워크와 SageMaker 오버헤드를 포함한 응답 시간. 서버 안의 추론 시간은 클립당 3~5ms다.
- 이 이미지에서 모델이 정상 로드되는지. 엔드포인트가 InService가 된 뒤 실제로 호출해 봐야 안다.
- 서버리스 설정 값(메모리 2048MB, 동시 호출 2)이 충분한지
