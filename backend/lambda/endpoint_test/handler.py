"""SageMaker 엔드포인트(sign-word-model) 연결 시험용 Lambda.

ENDPOINT_TEST.md의 테스트 코드를 저장소 파일로 추출한 것이다.
콘솔 Lambda 테스트와 CDK 배포가 같은 파일을 쓴다.

용도는 세 가지 확인이다 (ENDPOINT_TEST.md 참고).
  1. 컨테이너가 모델을 정상으로 읽고 응답하는지
  2. 응답 1위가 정답과 같은지
  3. 콜드/웜 응답 시간(elapsed_ms)

event: test_1.json 등 요청 본문을 그대로 받는다.
  {"top_k": 5, "keypoints": [[[x, y], ... 49개], ... 30프레임]}

return(정상):
  {"elapsed_ms": 1234, "result": {"ok": true, "top_k": [...]}}
return(오류): 콘솔에서 원인을 바로 보도록 예외 타입/메시지를 담아 돌려준다.
  {"elapsed_ms": 1234, "error": "AccessDeniedException", "message": "..."}

환경 변수
  ENDPOINT_NAME   호출할 엔드포인트 이름 (기본 sign-word-model)
  AWS_REGION      리전. Lambda 런타임이 자동 주입하므로 보통 설정 불필요 (기본 ap-northeast-2)

elapsed_ms는 Lambda에서 엔드포인트까지 왕복한 시간만 잰다.
프론트와 API Gateway 구간은 들어 있지 않다.
"""
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

# AWS_REGION은 Lambda 런타임이 자동으로 넣어 준다. 콘솔 밖(로컬)에서는 없을 수 있어 기본값을 둔다.
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "sign-word-model")

rt = boto3.client("sagemaker-runtime", region_name=REGION)


def lambda_handler(event, context=None):
    t = time.time()
    try:
        r = rt.invoke_endpoint(
            EndpointName=ENDPOINT_NAME,
            ContentType="application/json",
            Body=json.dumps(event),
        )
        body = json.loads(r["Body"].read())
        return {"elapsed_ms": round((time.time() - t) * 1000), "result": body}
    except ClientError as e:
        # AccessDeniedException, ValidationError, ModelError 등. 콘솔에서 원인을 바로 본다.
        return {
            "elapsed_ms": round((time.time() - t) * 1000),
            "error": e.response.get("Error", {}).get("Code", "ClientError"),
            "message": e.response.get("Error", {}).get("Message", str(e)),
        }
    except Exception as e:  # noqa: BLE001 - 시험용이라 어떤 예외든 그대로 보여 준다
        return {
            "elapsed_ms": round((time.time() - t) * 1000),
            "error": type(e).__name__,
            "message": str(e),
        }
