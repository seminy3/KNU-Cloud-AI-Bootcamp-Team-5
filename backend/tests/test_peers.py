"""peers(연결 목록·전달), join/disconnect Lambda 단위 테스트. DynamoDB·WS는 가짜로 대신한다."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "common"))

import peers  # noqa: E402
from conversation import ConversationStore  # noqa: E402


class FakeTable:
    """put/get/delete/query만 흉내 낸다. query는 sessionId 일치 + ts < 0 조건만 지원."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        self.items[(Item["sessionId"], Item["ts"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["sessionId"], Key["ts"]))
        return {"Item": dict(item)} if item else {}

    def delete_item(self, Key):
        self.items.pop((Key["sessionId"], Key["ts"]), None)

    def query(self, KeyConditionExpression, **kwargs):
        cond = KeyConditionExpression
        if cond.expression_operator == "AND":
            eq, rng = cond._values
        else:
            eq, rng = cond, None
        sid = eq._values[1]
        items = [v for (s, _), v in self.items.items() if s == sid]
        if rng is not None:
            items = [v for v in items if v["ts"] < rng._values[1]]
        items.sort(key=lambda v: v["ts"], reverse=not kwargs.get("ScanIndexForward", True))
        if "Limit" in kwargs:
            items = items[: kwargs["Limit"]]
        return {"Items": items}


class Gone(Exception):
    response = {"Error": {"Code": "GoneException"}}


class FakePoster:
    def __init__(self, gone=()):
        self.sent = []
        self.gone = set(gone)

    def post_to_connection(self, ConnectionId, Data):
        if ConnectionId in self.gone:
            raise Gone()
        self.sent.append((ConnectionId, json.loads(Data)))


def _load(name):
    path = BACKEND / "lambda" / name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"{name}_handler", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{name}_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


def _event(cid, body=None):
    ev = {"requestContext": {"connectionId": cid}}
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def test_참여하면_역할_목록에_나온다():
    store = peers.PeerStore(FakeTable())
    store.join("session-1", "A", "patient")
    store.join("session-1", "B", "doctor")
    store.join("session-2", "C", "doctor")
    assert sorted(store.members("session-1")) == [("A", "patient"), ("B", "doctor")]
    assert peers.presence_payload(store.members("session-1")) == {
        "type": "presence", "roles": ["doctor", "patient"]}


def test_끊기면_목록에서_빠지고_대화_코드를_돌려준다():
    store = peers.PeerStore(FakeTable())
    store.join("session-1", "A", "patient")
    store.join("session-1", "B", "doctor")
    assert store.leave("A") == "session-1"
    assert store.members("session-1") == [("B", "doctor")]
    assert store.leave("없는연결") is None


def test_만료된_연결은_목록에서_뺀다():
    table = FakeTable()
    store = peers.PeerStore(table)
    store.join("session-1", "A", "patient")
    for item in table.items.values():
        item["ttl"] = int(time.time()) - 1
    assert store.members("session-1") == []


def test_전달은_보낸_연결을_빼고_끊긴_연결은_지운다():
    store = peers.PeerStore(FakeTable())
    for cid, role in [("A", "patient"), ("B", "doctor"), ("C", "doctor")]:
        store.join("session-1", cid, role)
    poster = FakePoster(gone={"C"})
    sent = peers.broadcast(poster, store, "session-1", {"type": "relay"}, exclude="A")
    assert sent == 1
    assert poster.sent == [("B", {"type": "relay"})]
    assert sorted(store.members("session-1")) == [("A", "patient"), ("B", "doctor")]


def test_relay는_실패해도_예외를_내지_않는다():
    def broken():
        raise RuntimeError("테이블 없음")
    assert peers.relay(_event("A"), broken, "session-1", {"type": "relay"}, exclude="A") == 0
    assert peers.relay(_event("A"), broken, None, {"type": "relay"}, exclude="A") == 0


def test_대화_기록_조회에서_연결_항목은_빠진다():
    table = FakeTable()
    conv = ConversationStore(table=table)
    peers.PeerStore(table).join("session-1", "A", "patient")
    table.put_item(Item={"sessionId": "session-1", "ts": 1000, "role": "doctor", "sentence": "어디가 아프세요?"})
    assert conv.recent_history("session-1") == [{"speaker": "의사", "text": "어디가 아프세요?"}]


@pytest.fixture
def join_mod(monkeypatch):
    mod = _load("join")
    table = FakeTable()
    mod._peers = peers.PeerStore(table)
    poster = FakePoster()
    monkeypatch.setattr(mod, "make_poster", lambda event: poster)
    return mod, poster


def test_join은_같은_대화의_모든_연결에_접속_상태를_보낸다(join_mod):
    mod, poster = join_mod
    mod.lambda_handler(_event("A", {"action": "join", "sessionId": "session-1", "role": "patient"}))
    mod.lambda_handler(_event("B", {"action": "join", "sessionId": "session-1", "role": "doctor"}))
    last = {"type": "presence", "roles": ["doctor", "patient"]}
    assert ("A", last) in poster.sent and ("B", last) in poster.sent
    assert poster.sent[0] == ("A", {"type": "presence", "roles": ["patient"]})


@pytest.mark.parametrize("body", [
    {"sessionId": "short", "role": "patient"},
    {"sessionId": "session-1", "role": "nurse"},
    {"role": "patient"},
])
def test_join_입력이_틀리면_오류(join_mod, body):
    mod, poster = join_mod
    mod.lambda_handler(_event("A", {"action": "join", **body}))
    assert poster.sent[0][1]["type"] == "error"
    assert poster.sent[0][1]["stage"] == "join"
    assert mod._peers.members("session-1") == []


def test_disconnect는_남은_연결에_접속_상태를_알린다(monkeypatch):
    mod = _load("disconnect")
    table = FakeTable()
    store = peers.PeerStore(table)
    store.join("session-1", "A", "patient")
    store.join("session-1", "B", "doctor")
    mod._peers = store
    poster = FakePoster()
    monkeypatch.setattr(mod, "make_poster", lambda event: poster)
    mod.lambda_handler(_event("A"))
    assert poster.sent == [("B", {"type": "presence", "roles": ["doctor"]})]


def test_대화_기록은_24시간_뒤_만료된다():
    table = FakeTable()
    item = ConversationStore(table=table).put_turn("session-1", role="doctor", sentence="어디가 아프세요?")
    left = item["ttl"] - time.time()
    assert 23.9 * 3600 < left <= 24 * 3600
