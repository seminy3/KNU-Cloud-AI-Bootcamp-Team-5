"""endpoint_test/handler.py 단위 테스트.

실제 AWS 호출 없이 boto3 sagemaker-runtime 클라이언트를 모킹해
요청 구성 / 응답 파싱 / elapsed_ms 계산 / 예외 처리를 확인한다.

    python -m pytest backend/tests/
"""
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# endpoint_test/handler.py 경로. 다른 Lambda의 handler.py와 모듈명이 겹치지 않도록
# 고유 이름("endpoint_test_handler")으로 파일에서 직접 로드한다.
HANDLER_PATH = (Path(__file__).resolve().parent.parent
                / "lambda" / "endpoint_test" / "handler.py")


@pytest.fixture
def handler(monkeypatch):
    """boto3.client를 모킹한 뒤 handler 모듈을 고유 이름으로 새로 로드한다.

    모듈 로드 시점에 boto3.client("sagemaker-runtime")가 호출되므로
    import 전에 패치한다. 각 테스트가 독립된 가짜 클라이언트를 받도록
    매번 새로 로드한다.
    """
    fake_client = mock.MagicMock()
    with mock.patch("boto3.client", return_value=fake_client):
        spec = importlib.util.spec_from_file_location(
            "endpoint_test_handler", HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod._fake_client = fake_client  # 테스트에서 접근용
    return mod


def _body(payload):
    """invoke_endpoint 응답의 Body(StreamingBody 유사 객체)를 흉내 낸다."""
    return {"Body": io.BytesIO(json.dumps(payload).encode("utf-8"))}


def test_정상_호출시_result와_elapsed_ms_반환(handler):
    expected = {"ok": True, "frames_in": 30, "frames_used": 30,
                "top_k": [{"name": "아프다", "label_id": 0, "prob": 0.603}]}
    handler._fake_client.invoke_endpoint.return_value = _body(expected)

    event = {"top_k": 5, "keypoints": [[[0.0, 0.0]]]}
    out = handler.lambda_handler(event)

    assert out["result"] == expected
    assert isinstance(out["elapsed_ms"], int)
    assert out["elapsed_ms"] >= 0
    assert "error" not in out


def test_invoke_endpoint_올바른_인자로_호출(handler):
    handler._fake_client.invoke_endpoint.return_value = _body({"ok": True})

    event = {"top_k": 5, "keypoints": [[[1.0, 2.0]]]}
    handler.lambda_handler(event)

    kwargs = handler._fake_client.invoke_endpoint.call_args.kwargs
    assert kwargs["EndpointName"] == "sign-word-model"
    assert kwargs["ContentType"] == "application/json"
    # Body는 event를 그대로 직렬화한 것이어야 한다
    assert json.loads(kwargs["Body"]) == event


def test_AccessDenied_같은_ClientError를_error로_반환(handler):
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
        "InvokeEndpoint",
    )
    handler._fake_client.invoke_endpoint.side_effect = err

    out = handler.lambda_handler({"top_k": 5, "keypoints": []})

    assert out["error"] == "AccessDeniedException"
    assert "not authorized" in out["message"]
    assert "result" not in out
    assert isinstance(out["elapsed_ms"], int)


def test_일반_예외도_타입과_메시지로_반환(handler):
    handler._fake_client.invoke_endpoint.side_effect = ValueError("boom")

    out = handler.lambda_handler({"top_k": 5, "keypoints": []})

    assert out["error"] == "ValueError"
    assert out["message"] == "boom"
    assert "result" not in out
