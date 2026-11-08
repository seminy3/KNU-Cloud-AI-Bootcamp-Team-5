"""recognize Lambda 단위 테스트. SageMaker 클라이언트와 WS poster를 모킹."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "common"))


def _load_recognize():
    """recognize/handler.py 를 고유 이름으로 로드한다.

    endpoint_test/finalize 의 handler.py 와 모듈명이 겹치지 않게 한다.
    """
    path = BACKEND / "lambda" / "recognize" / "handler.py"
    spec = importlib.util.spec_from_file_location("recognize_handler", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recognize_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


recognize_handler = _load_recognize()

# 올바른 (30, 49, 2) 입력. 검증을 통과해야 엔드포인트까지 간다
VALID_KP = [[[0.1, 0.2]] * 49 for _ in range(30)]
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


class FakePoster:
    def __init__(self):
        self.sent = []

    def post_to_connection(self, ConnectionId=None, Data=None):
        self.sent.append((ConnectionId, json.loads(Data.decode("utf-8"))))


def _resp(*names_probs, ok=True):
    tk = [{"name": n, "label": n, "label_id": i, "prob": p}
          for i, (n, p) in enumerate(names_probs)]
    return {"ok": ok, "top_k": tk}


class FakeStore:
    """세션별 단어 버퍼를 메모리로 흉내 낸다."""

    def __init__(self, exc=None):
        self._buf = {}
        self._exc = exc

    def append_word(self, session_id, word):
        if self._exc:
            raise self._exc
        self._buf.setdefault(session_id, []).append(word)
        return list(self._buf[session_id])


@pytest.fixture(autouse=True)
def reset_recognizer():
    recognize_handler._recognizer = None
    recognize_handler._store = FakeStore()  # 기본은 동작하는 버퍼
    yield
    recognize_handler._recognizer = None
    recognize_handler._store = None


def _inject(response=None, exc=None):
    recognize_handler._recognizer = SageMakerClient(
        endpoint_name="sign-word-model", client=FakeRuntime(response, exc)
    )


def _event(body):
    return {
        "requestContext": {"connectionId": "C1", "domainName": "x.execute-api",
                           "stage": "dev"},
        "body": json.dumps(body),
    }


def test_정상_word_응답(monkeypatch):
    _inject(_resp(("병원", 0.62), ("의사", 0.1)))
    poster = FakePoster()
    monkeypatch.setattr(recognize_handler, "make_poster", lambda e: poster)

    out = recognize_handler.lambda_handler(_event(
        {"action": "recognize", "sessionId": "abc", "keypoints": VALID_KP}))

    assert out["statusCode"] == 200
    cid, payload = poster.sent[0]
    assert cid == "C1"
    assert payload["type"] == "word"
    assert payload["word"] == "병원"
    assert payload["candidates"][0]["name"] == "병원"
    # 서버 누적: 첫 단어라 words=["병원"], count=1
    assert payload["words"] == ["병원"]
    assert payload["count"] == 1


def test_서버_누적_여러단어():
    recognize_handler._store = FakeStore()
    _inject(_resp(("나", 0.5)))
    out1 = recognize_handler._handle({"sessionId": "abc", "keypoints": VALID_KP})
    assert out1["words"] == ["나"] and out1["count"] == 1

    _inject(_resp(("병원", 0.6)))
    out2 = recognize_handler._handle({"sessionId": "abc", "keypoints": VALID_KP})
    assert out2["words"] == ["나", "병원"] and out2["count"] == 2


def test_버퍼_저장실패해도_단어는_반환():
    from conversation import ConversationError
    recognize_handler._store = FakeStore(exc=ConversationError("ddb down"))
    _inject(_resp(("병원", 0.62)))
    out = recognize_handler._handle({"sessionId": "abc", "keypoints": VALID_KP})
    # 버퍼 저장이 실패해도 방금 인식한 단어는 돌려준다(best-effort)
    assert out["type"] == "word"
    assert out["word"] == "병원"
    assert out["words"] == ["병원"]


def test_sessionId_없으면_누적없이_단어만():
    _inject(_resp(("병원", 0.62)))
    out = recognize_handler._handle({"keypoints": VALID_KP})
    assert out["type"] == "word"
    assert out["words"] == ["병원"]  # 세션 없으면 방금 단어만


def test_keypoints_없으면_error():
    out = recognize_handler._handle({"action": "recognize", "sessionId": "abc"})
    assert out["type"] == "error"
    assert out["stage"] == "recognize"


def test_RecognizeError면_error_payload():
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                      "InvokeEndpoint")
    _inject(exc=err)
    out = recognize_handler._handle({"keypoints": VALID_KP})
    assert out["type"] == "error"
    assert out["retryable"] is True


def test_인식단어_없으면_error():
    _inject(_resp())  # top_k 비어 있음 → word None
    out = recognize_handler._handle({"keypoints": VALID_KP})
    assert out["type"] == "error"


def test_push_실패해도_200(monkeypatch):
    _inject(_resp(("병원", 0.62)))

    def boom(_e):
        raise RuntimeError("gone")

    monkeypatch.setattr(recognize_handler, "make_poster", boom)
    out = recognize_handler.lambda_handler(_event({"keypoints": VALID_KP}))
    assert out["statusCode"] == 200  # push 실패해도 Lambda는 정상 종료


@pytest.mark.parametrize("kp,why", [
    ([[[0.0, 0.0]] * 49] * 3, "프레임 수"),
    ([[[0.1, 0.2]] * 48] * 30, "지점 수"),
    ([[[0.1, float("nan")]] * 49] * 30, "NaN"),
    ([[[0.1, "x"]] * 49] * 30, "문자"),
    ([[[0.1, 1e6]] * 49] * 30, "너무 큰 값"),
    ([[[0.0, 0.0]] * 49] * 30, "모두 0"),
])
def test_잘못된_좌표는_엔드포인트로_보내지_않는다(kp, why):
    rt = FakeRuntime(_resp(("병원", 0.6)))
    recognize_handler._recognizer = SageMakerClient(endpoint_name="sign-word-model", client=rt)
    out = recognize_handler._handle({"sessionId": "abc", "keypoints": kp})
    assert out["type"] == "error" and out["stage"] == "recognize", why
    assert rt.calls == [], why


def test_호출_시간초과도_error_payload():
    from botocore.exceptions import ReadTimeoutError
    _inject(exc=ReadTimeoutError(endpoint_url="https://runtime.sagemaker"))
    out = recognize_handler._handle({"keypoints": VALID_KP})
    assert out["type"] == "error"
    assert out["retryable"] is True
