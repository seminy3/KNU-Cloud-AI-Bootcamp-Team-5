"""conversation 모듈 단위 테스트. DynamoDB Table 리소스를 모킹."""
import sys
from pathlib import Path

import pytest

COMMON_DIR = Path(__file__).resolve().parent.parent / "common"
sys.path.insert(0, str(COMMON_DIR))

from conversation import ConversationError, ConversationStore  # noqa: E402


class FakeTable:
    def __init__(self, items=None, put_exc=None, query_exc=None,
                 update_exc=None):
        self._items = items or []
        self.put_calls = []
        self._put_exc = put_exc
        self._query_exc = query_exc
        self._update_exc = update_exc
        # 버퍼 저장소: (sessionId, ts) → item
        self._store = {}
        self.deleted = []

    def put_item(self, Item=None):
        if self._put_exc:
            raise self._put_exc
        self.put_calls.append(Item)

    def query(self, **kwargs):
        if self._query_exc:
            raise self._query_exc
        # ScanIndexForward=False → 내림차순으로 주는 척, Limit 적용
        ordered = sorted(self._items, key=lambda it: it["ts"], reverse=True)
        limit = kwargs.get("Limit")
        if limit:
            ordered = ordered[:limit]
        return {"Items": ordered}

    # ── 버퍼용: list_append(if_not_exists(...)) 동작을 흉내 ──
    def update_item(self, Key=None, UpdateExpression=None,
                    ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, ReturnValues=None):
        if self._update_exc:
            raise self._update_exc
        k = (Key["sessionId"], Key["ts"])
        item = self._store.get(k, {"sessionId": Key["sessionId"], "ts": Key["ts"]})
        item["role"] = ExpressionAttributeValues[":role"]
        existing = item.get("words", list(ExpressionAttributeValues[":empty"]))
        item["words"] = existing + list(ExpressionAttributeValues[":w"])
        self._store[k] = item
        return {"Attributes": item}

    def get_item(self, Key=None):
        k = (Key["sessionId"], Key["ts"])
        item = self._store.get(k)
        return {"Item": item} if item else {}

    def delete_item(self, Key=None):
        k = (Key["sessionId"], Key["ts"])
        self.deleted.append(k)
        self._store.pop(k, None)


def test_put_turn_item_구성():
    t = FakeTable()
    store = ConversationStore(table_name="tbl", table=t)
    item = store.put_turn("abc", role="patient",
                          sentence="저 병원에 가려고요",
                          words=["나", "병원", "가다"],
                          needs_confirm=True, ts=1000)
    assert item["sessionId"] == "abc"
    assert item["ts"] == 1000
    assert item["role"] == "patient"
    assert item["sentence"] == "저 병원에 가려고요"
    assert item["words"] == ["나", "병원", "가다"]
    assert item["needsConfirm"] is True
    assert t.put_calls[0] == item


def test_put_turn_words_없으면_속성_생략():
    t = FakeTable()
    store = ConversationStore(table_name="tbl", table=t)
    item = store.put_turn("abc", role="doctor", sentence="어디가 아프세요?", ts=1)
    assert "words" not in item


def test_put_turn_알수없는_role면_에러():
    store = ConversationStore(table_name="tbl", table=FakeTable())
    with pytest.raises(ConversationError):
        store.put_turn("abc", role="nurse", sentence="x")


def test_put_turn_ClientError면_ConversationError():
    from botocore.exceptions import ClientError

    err = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                 "Message": "slow"}}, "PutItem")
    store = ConversationStore(table_name="tbl", table=FakeTable(put_exc=err))
    with pytest.raises(ConversationError):
        store.put_turn("abc", role="patient", sentence="x")


def test_recent_history_시간순_speaker매핑():
    items = [
        {"sessionId": "abc", "ts": 3, "role": "patient", "sentence": "머리 아파요"},
        {"sessionId": "abc", "ts": 1, "role": "doctor", "sentence": "어디가 아프세요?"},
        {"sessionId": "abc", "ts": 2, "role": "patient", "sentence": "저요"},
    ]
    store = ConversationStore(table_name="tbl", table=FakeTable(items=items))
    hist = store.recent_history("abc", limit=6)
    # 오래된 → 최신 순, speaker 매핑
    assert hist == [
        {"speaker": "의사", "text": "어디가 아프세요?"},
        {"speaker": "환자", "text": "저요"},
        {"speaker": "환자", "text": "머리 아파요"},
    ]


def test_recent_history_limit_적용():
    items = [{"sessionId": "abc", "ts": i, "role": "patient", "sentence": str(i)}
             for i in range(10)]
    store = ConversationStore(table_name="tbl", table=FakeTable(items=items))
    hist = store.recent_history("abc", limit=3)
    assert len(hist) == 3
    # 최신 3개(ts 7,8,9)를 시간순으로
    assert [h["text"] for h in hist] == ["7", "8", "9"]


def test_recent_history_조회실패시_빈리스트():
    from botocore.exceptions import ClientError

    err = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no"}},
                      "Query")
    store = ConversationStore(table_name="tbl", table=FakeTable(query_exc=err))
    assert store.recent_history("abc") == []


def test_table_name_없으면_에러():
    store = ConversationStore(table_name=None)
    with pytest.raises(ConversationError):
        _ = store.table


def test_recent_history_버퍼_아이템_제외():
    items = [
        {"sessionId": "abc", "ts": 0, "role": "buffer", "words": ["나", "병원"]},
        {"sessionId": "abc", "ts": 5, "role": "patient", "sentence": "저 병원에 가려고요"},
    ]
    store = ConversationStore(table_name="tbl", table=FakeTable(items=items))
    hist = store.recent_history("abc")
    # 버퍼(ts=0, role=buffer)는 history에 포함되지 않는다
    assert hist == [{"speaker": "환자", "text": "저 병원에 가려고요"}]


def test_append_word_누적_리스트_반환():
    store = ConversationStore(table_name="tbl", table=FakeTable())
    assert store.append_word("abc", "나") == ["나"]
    assert store.append_word("abc", "병원") == ["나", "병원"]
    assert store.append_word("abc", "가다") == ["나", "병원", "가다"]


def test_get_buffer_누적_조회():
    t = FakeTable()
    store = ConversationStore(table_name="tbl", table=t)
    store.append_word("abc", "나")
    store.append_word("abc", "병원")
    assert store.get_buffer("abc") == ["나", "병원"]


def test_get_buffer_없으면_빈리스트():
    store = ConversationStore(table_name="tbl", table=FakeTable())
    assert store.get_buffer("none") == []


def test_clear_buffer_후_빈리스트():
    t = FakeTable()
    store = ConversationStore(table_name="tbl", table=t)
    store.append_word("abc", "나")
    store.clear_buffer("abc")
    assert store.get_buffer("abc") == []
    assert ("abc", 0) in t.deleted


def test_버퍼는_세션별로_분리():
    t = FakeTable()
    store = ConversationStore(table_name="tbl", table=t)
    store.append_word("s1", "나")
    store.append_word("s2", "병원")
    assert store.get_buffer("s1") == ["나"]
    assert store.get_buffer("s2") == ["병원"]


def test_append_word_ClientError면_ConversationError():
    from botocore.exceptions import ClientError

    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}},
                      "UpdateItem")
    store = ConversationStore(table_name="tbl", table=FakeTable(update_exc=err))
    with pytest.raises(ConversationError):
        store.append_word("abc", "나")
