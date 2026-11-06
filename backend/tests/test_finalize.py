"""finalize Lambda 단위 테스트. llm 문장생성/Polly/S3/DDB를 모두 모킹."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "common"))


def _load_finalize():
    """finalize/handler.py 를 'finalize_handler' 라는 이름으로 로드한다.

    recognize/handler.py 와 모듈명이 겹치지 않게 명시적으로 불러온다.
    """
    path = BACKEND / "lambda" / "finalize" / "handler.py"
    spec = importlib.util.spec_from_file_location("finalize_handler", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["finalize_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


fh = _load_finalize()


class FakeStore:
    def __init__(self, history=None, buffer=None):
        self._history = history or []
        self._buffer = list(buffer) if buffer else []
        self.puts = []
        self.cleared = []

    def recent_history(self, session_id, limit=6):
        return self._history

    def put_turn(self, session_id, role, sentence, words=None, needs_confirm=False):
        self.puts.append({"sessionId": session_id, "role": role,
                          "sentence": sentence, "words": words,
                          "needsConfirm": needs_confirm})

    def get_buffer(self, session_id):
        return list(self._buffer)

    def clear_buffer(self, session_id):
        self.cleared.append(session_id)
        self._buffer = []


class FakeSynth:
    def __init__(self, url="https://audio/x.mp3", exc=None):
        self._url = url
        self._exc = exc
        self.calls = []

    def synthesize_to_url(self, text, session_id, ts=None):
        self.calls.append((text, session_id))
        if self._exc:
            raise self._exc
        return self._url


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    fh._store = None
    fh._synth = None
    fh._generate = None
    yield
    fh._store = None
    fh._synth = None
    fh._generate = None


def _wire(monkeypatch, store, synth, gen):
    monkeypatch.setattr(fh, "store", lambda: store)
    monkeypatch.setattr(fh, "synth", lambda: synth)
    monkeypatch.setattr(fh, "generate_sentence", gen)


def test_정상_speech_응답(monkeypatch):
    store = FakeStore(history=[{"speaker": "의사", "text": "어디가 불편하세요?"}])
    synth = FakeSynth(url="https://audio/abc.mp3")
    gen = lambda w, h: {"sentence": "저 병원에 가려고요", "ambiguous": False,
                        "fallback": False}
    _wire(monkeypatch, store, synth, gen)

    out = fh._handle({"sessionId": "abc", "words": ["나", "병원", "가다"]})

    assert out["type"] == "speech"
    assert out["sentence"] == "저 병원에 가려고요"
    assert out["audioUrl"] == "https://audio/abc.mp3"
    assert out["needsConfirm"] is False
    # 대화 기록 저장됨
    assert store.puts[0]["role"] == "patient"
    assert store.puts[0]["sentence"] == "저 병원에 가려고요"
    # Polly에 문장이 전달됨
    assert synth.calls[0][0] == "저 병원에 가려고요"


def test_words_없으면_error(monkeypatch):
    _wire(monkeypatch, FakeStore(), FakeSynth(), lambda w, h: {})
    out = fh._handle({"sessionId": "abc", "words": []})
    assert out["type"] == "error"
    assert out["stage"] == "sentence"


def test_fallback이면_needsConfirm_true(monkeypatch):
    gen = lambda w, h: {"sentence": "나 병원 가다", "ambiguous": False,
                        "fallback": True}
    _wire(monkeypatch, FakeStore(), FakeSynth(), gen)
    out = fh._handle({"sessionId": "abc", "words": ["나", "병원"]})
    assert out["needsConfirm"] is True


def test_ambiguous면_needsConfirm_true(monkeypatch):
    gen = lambda w, h: {"sentence": "오늘 내일?", "ambiguous": True, "fallback": False}
    _wire(monkeypatch, FakeStore(), FakeSynth(), gen)
    out = fh._handle({"sessionId": "abc", "words": ["오늘", "내일"]})
    assert out["needsConfirm"] is True


def test_문장생성_예외면_error(monkeypatch):
    def boom(w, h):
        raise RuntimeError("bedrock down")

    _wire(monkeypatch, FakeStore(), FakeSynth(), boom)
    out = fh._handle({"sessionId": "abc", "words": ["나"]})
    assert out["type"] == "error"
    assert out["stage"] == "sentence"


def test_음성합성_실패해도_자막은_전달(monkeypatch):
    from speech import SpeechError
    synth = FakeSynth(exc=SpeechError("polly down"))
    gen = lambda w, h: {"sentence": "머리가 아파요", "ambiguous": False,
                        "fallback": False}
    _wire(monkeypatch, FakeStore(), synth, gen)

    out = fh._handle({"sessionId": "abc", "words": ["머리", "아프다"]})
    assert out["type"] == "speech"
    assert out["sentence"] == "머리가 아파요"   # 자막은 전달
    assert out["audioUrl"] is None             # 음성은 없음
    assert out["needsConfirm"] is True


def test_history_조회실패해도_진행(monkeypatch):
    from conversation import ConversationError

    class BadStore(FakeStore):
        def recent_history(self, session_id, limit=6):
            raise ConversationError("ddb down")

    gen = lambda w, h: {"sentence": "약 주세요", "ambiguous": False, "fallback": False}
    _wire(monkeypatch, BadStore(), FakeSynth(), gen)
    out = fh._handle({"sessionId": "abc", "words": ["가루약"]})
    assert out["type"] == "speech"  # history 없이도 진행


def test_lambda_handler_push(monkeypatch):
    gen = lambda w, h: {"sentence": "네 맞아요", "ambiguous": False, "fallback": False}
    _wire(monkeypatch, FakeStore(), FakeSynth(), gen)
    sent = []

    class P:
        def post_to_connection(self, ConnectionId=None, Data=None):
            sent.append((ConnectionId, json.loads(Data.decode("utf-8"))))

    monkeypatch.setattr(fh, "make_poster", lambda e: P())
    event = {"requestContext": {"connectionId": "C9", "domainName": "d", "stage": "dev"},
             "body": json.dumps({"sessionId": "abc", "words": ["맞다"]})}
    out = fh.lambda_handler(event)
    assert out["statusCode"] == 200
    assert sent[0][0] == "C9"
    assert sent[0][1]["type"] == "speech"


def test_words_없으면_서버버퍼에서_읽음(monkeypatch):
    # body 에 words 가 없고, 서버 버퍼에 쌓여 있는 경우
    store = FakeStore(buffer=["나", "병원", "가다"])
    gen = lambda w, h: {"sentence": "저 병원에 가려고요", "ambiguous": False,
                        "fallback": False}
    _wire(monkeypatch, store, FakeSynth(), gen)

    out = fh._handle({"sessionId": "abc"})  # words 없음

    assert out["type"] == "speech"
    assert out["words"] == ["나", "병원", "가다"]  # 버퍼에서 가져옴
    assert out["sentence"] == "저 병원에 가려고요"


def test_문장생성_성공하면_버퍼_클리어(monkeypatch):
    store = FakeStore(buffer=["나", "병원"])
    gen = lambda w, h: {"sentence": "저 병원에 가려고요", "ambiguous": False,
                        "fallback": False}
    _wire(monkeypatch, store, FakeSynth(), gen)

    fh._handle({"sessionId": "abc"})
    assert "abc" in store.cleared  # 처리 후 버퍼 비움


def test_버퍼도_비었으면_error(monkeypatch):
    store = FakeStore(buffer=[])  # 버퍼 비어 있음
    _wire(monkeypatch, store, FakeSynth(), lambda w, h: {})
    out = fh._handle({"sessionId": "abc"})  # words 없음
    assert out["type"] == "error"
    assert out["stage"] == "sentence"


def test_body_words_우선하고_버퍼도_클리어(monkeypatch):
    # body 로 words 가 오면 그걸 쓰되, 세션 버퍼도 정리한다(하위호환)
    store = FakeStore(buffer=["무시될", "단어"])
    gen = lambda w, h: {"sentence": "네 맞아요", "ambiguous": False, "fallback": False}
    _wire(monkeypatch, store, FakeSynth(), gen)

    out = fh._handle({"sessionId": "abc", "words": ["맞다"]})
    assert out["words"] == ["맞다"]      # body.words 우선
    assert "abc" in store.cleared        # 버퍼는 정리됨
