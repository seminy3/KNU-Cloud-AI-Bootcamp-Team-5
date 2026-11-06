"""doctor_utterance Lambda 단위 테스트. ConversationStore/WS poster 모킹."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "common"))


def _load():
    path = BACKEND / "lambda" / "doctor_utterance" / "handler.py"
    spec = importlib.util.spec_from_file_location("doctor_utterance_handler", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["doctor_utterance_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


dh = _load()


class FakeStore:
    def __init__(self, exc=None):
        self.puts = []
        self._exc = exc

    def put_turn(self, session_id, role, sentence, words=None, needs_confirm=False):
        if self._exc:
            raise self._exc
        self.puts.append({"sessionId": session_id, "role": role, "sentence": sentence})


@pytest.fixture(autouse=True)
def reset():
    dh._store = FakeStore()
    yield
    dh._store = None


def test_의사발화_저장_후_transcript_응답():
    store = FakeStore()
    dh._store = store
    out = dh._handle({"sessionId": "abc", "text": "어디가 아프세요?"})
    assert out == {"type": "transcript", "text": "어디가 아프세요?"}
    assert store.puts[0] == {"sessionId": "abc", "role": "doctor",
                             "sentence": "어디가 아프세요?"}


def test_빈_텍스트면_error():
    out = dh._handle({"sessionId": "abc", "text": "   "})
    assert out["type"] == "error"
    assert out["stage"] == "transcript"


def test_text_없어도_error():
    out = dh._handle({"sessionId": "abc"})
    assert out["type"] == "error"


def test_저장_실패해도_transcript_반환():
    from conversation import ConversationError
    dh._store = FakeStore(exc=ConversationError("ddb down"))
    out = dh._handle({"sessionId": "abc", "text": "안녕하세요"})
    # 저장이 실패해도 자막은 돌려준다(best-effort)
    assert out["type"] == "transcript"
    assert out["text"] == "안녕하세요"


def test_sessionId_없으면_저장안함_하지만_응답():
    store = FakeStore()
    dh._store = store
    out = dh._handle({"text": "질문입니다"})
    assert out["type"] == "transcript"
    assert store.puts == []  # 세션 없으면 저장 생략


def test_lambda_handler_push(monkeypatch):
    sent = []

    class P:
        def post_to_connection(self, ConnectionId=None, Data=None):
            sent.append((ConnectionId, json.loads(Data.decode("utf-8"))))

    monkeypatch.setattr(dh, "make_poster", lambda e: P())
    event = {"requestContext": {"connectionId": "C3", "domainName": "d", "stage": "dev"},
             "body": json.dumps({"sessionId": "abc", "text": "맥박 재볼게요"})}
    out = dh.lambda_handler(event)
    assert out["statusCode"] == 200
    assert sent[0][0] == "C3"
    assert sent[0][1]["type"] == "transcript"
