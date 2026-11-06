"""sagemaker_client 단위 테스트. 실제 AWS 없이 가짜 클라이언트 주입."""
import io
import json
import sys
from pathlib import Path

import pytest

COMMON_DIR = Path(__file__).resolve().parent.parent / "common"
sys.path.insert(0, str(COMMON_DIR))

from sagemaker_client import RecognizeError, SageMakerClient  # noqa: E402


class FakeRuntime:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def invoke_endpoint(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return {"Body": io.BytesIO(json.dumps(self._response).encode("utf-8"))}


def _resp(*names_probs, ok=True):
    tk = [{"name": n, "label": n, "label_id": i, "prob": p}
          for i, (n, p) in enumerate(names_probs)]
    r = {"ok": ok, "frames_in": 30, "frames_used": 30, "top_k": tk}
    return r


def test_recognize_1위_단어와_후보목록():
    rt = FakeRuntime(_resp(("병원", 0.62), ("의사", 0.1), ("치료", 0.05)))
    c = SageMakerClient(endpoint_name="sign-word-model", region="ap-northeast-2", client=rt)
    out = c.recognize([[[0.0, 0.0]]], top_k=3)
    assert out["word"] == "병원"
    assert out["candidates"][0] == {"name": "병원", "prob": 0.62}
    assert len(out["candidates"]) == 3


def test_invoke_올바른_인자():
    rt = FakeRuntime(_resp(("병원", 0.62)))
    c = SageMakerClient(endpoint_name="sign-word-model", region="ap-northeast-2", client=rt)
    c.recognize([[[1.0, 2.0]]], top_k=5)
    kw = rt.calls[0]
    assert kw["EndpointName"] == "sign-word-model"
    assert kw["ContentType"] == "application/json"
    body = json.loads(kw["Body"])
    assert body["top_k"] == 5
    assert body["keypoints"] == [[[1.0, 2.0]]]


def test_ok_false면_RecognizeError():
    rt = FakeRuntime({"ok": False, "reason": "쓸 수 있는 프레임 부족"})
    c = SageMakerClient(client=rt)
    with pytest.raises(RecognizeError):
        c.recognize([[[0.0, 0.0]]])


def test_ClientError면_RecognizeError():
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeEndpoint"
    )
    rt = FakeRuntime(exc=err)
    c = SageMakerClient(client=rt)
    with pytest.raises(RecognizeError):
        c.recognize([[[0.0, 0.0]]])


def test_빈_top_k면_word_None():
    rt = FakeRuntime(_resp())  # top_k 비어 있음
    c = SageMakerClient(client=rt)
    out = c.recognize([[[0.0, 0.0]]])
    assert out["word"] is None
    assert out["candidates"] == []
