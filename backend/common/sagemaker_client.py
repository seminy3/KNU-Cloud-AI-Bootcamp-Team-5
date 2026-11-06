"""SageMaker 엔드포인트(sign-word-model) 호출 공용 모듈.

흐름 ①의 recognize Lambda가 쓴다. endpoint_test/handler.py의 호출 로직을
재사용 가능한 함수로 정리한 것이다.

요청/응답 규격은 ENDPOINT.md 참고.
  요청:  {"top_k": 5, "keypoints": [(30,49,2)]}
  응답:  {"ok": true, "frames_in", "frames_used",
          "top_k": [{"label","name","label_id","word_id","prob"}, ...]}

사용:
    from sagemaker_client import SageMakerClient
    client = SageMakerClient(endpoint_name="sign-word-model", region="ap-northeast-2")
    out = client.recognize(keypoints, top_k=5)
    # out = {"word": "병원", "candidates": [{"name","prob"}...], "raw": {...}}
"""
import json
import os

DEFAULT_ENDPOINT = "sign-word-model"
DEFAULT_REGION = "ap-northeast-2"


class RecognizeError(Exception):
    """엔드포인트 호출/응답 처리 실패. 호출 측이 사용자 안내로 바꾼다."""


def _build_client(region):
    import boto3

    return boto3.client("sagemaker-runtime", region_name=region)


class SageMakerClient:
    def __init__(self, endpoint_name=None, region=None, client=None):
        self.endpoint_name = endpoint_name or os.environ.get(
            "ENDPOINT_NAME", DEFAULT_ENDPOINT
        )
        self.region = region or os.environ.get("AWS_REGION", DEFAULT_REGION)
        # client 를 주입하면 테스트에서 boto3 없이 쓴다
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_client(self.region)
        return self._client

    def invoke(self, keypoints, top_k=5):
        """엔드포인트를 호출하고 원시 응답(dict)을 돌려준다."""
        from botocore.exceptions import ClientError

        payload = {"top_k": top_k, "keypoints": keypoints}
        try:
            r = self.client.invoke_endpoint(
                EndpointName=self.endpoint_name,
                ContentType="application/json",
                Body=json.dumps(payload),
            )
            return json.loads(r["Body"].read())
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise RecognizeError(f"엔드포인트 호출 실패: {code}") from e

    def recognize(self, keypoints, top_k=5):
        """keypoints → 1위 단어 + 후보 목록.

        반환: {"word": str|None, "candidates": [{"name","prob"}...], "raw": dict}
        """
        raw = self.invoke(keypoints, top_k=top_k)
        if not raw.get("ok", True):
            # 원시 frames 를 보냈을 때 쓸 수 있는 프레임이 모자라면 ok=false (ENDPOINT.md)
            raise RecognizeError(f"인식 실패: {raw.get('reason', 'ok=false')}")

        tk = raw.get("top_k") or []
        candidates = [
            {"name": c.get("name"), "prob": c.get("prob")} for c in tk
        ]
        word = candidates[0]["name"] if candidates else None
        return {"word": word, "candidates": candidates, "raw": raw}
