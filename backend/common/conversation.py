"""대화 기록(DynamoDB) 공용 모듈.

흐름 ①의 finalize Lambda가 환자 발화를 저장하고, 직전 대화(history)를 조회한다.
흐름 ②에서 의료진 발화도 같은 테이블에 쌓으면 history로 함께 쓸 수 있다.

테이블 스키마
  PK  sessionId (S)
  SK  ts        (N, epoch millis)
  속성 role("patient"|"doctor"), sentence(S), words(L, 선택), needsConfirm(BOOL, 선택)

history 포맷은 llm/sentence.py가 기대하는 [{"speaker","text"}, ...] 에 맞춘다.
  role="patient" → speaker="환자", role="doctor" → speaker="의사"

사용:
    from conversation import ConversationStore
    store = ConversationStore(table_name="signmed-conversation", region="ap-northeast-2")
    store.put_turn("abc123", role="patient", sentence="저 병원에 가려고요", words=["나","병원","가다"])
    hist = store.recent_history("abc123", limit=6)
    # → [{"speaker": "의사", "text": "..."}, {"speaker": "환자", "text": "..."}]
"""
import os
import time

DEFAULT_REGION = "ap-northeast-2"
SPEAKER = {"patient": "환자", "doctor": "의사"}

# 진행 중 단어 버퍼 아이템의 SK. 일반 대화 기록 ts는 epoch millis(양수 13자리)라
# 0과 충돌하지 않는다. role="buffer"로 표시해 history 조회에서 제외한다.
BUFFER_TS = 0
BUFFER_ROLE = "buffer"


class ConversationError(Exception):
    """대화 기록 저장/조회 실패."""


def _table(table_name, region):
    import boto3

    return boto3.resource("dynamodb", region_name=region).Table(table_name)


class ConversationStore:
    def __init__(self, table_name=None, region=None, table=None):
        self.table_name = table_name or os.environ.get("TABLE_NAME")
        self.region = region or os.environ.get("AWS_REGION", DEFAULT_REGION)
        # table 리소스를 주입하면 boto3 없이 테스트 가능
        self._table = table

    @property
    def table(self):
        if self._table is None:
            if not self.table_name:
                raise ConversationError("TABLE_NAME이 설정되지 않음")
            self._table = _table(self.table_name, self.region)
        return self._table

    def put_turn(self, session_id, role, sentence, words=None,
                 needs_confirm=False, ts=None):
        """대화 한 턴을 저장한다. 저장 실패는 ConversationError.

        대화 기록 저장이 흐름을 막지 않도록, 호출 측이 이 예외를 잡아
        best-effort로 처리할 수 있다.
        """
        from botocore.exceptions import ClientError

        if role not in SPEAKER:
            raise ConversationError(f"알 수 없는 role: {role}")
        ts = ts if ts is not None else int(time.time() * 1000)
        item = {
            "sessionId": session_id,
            "ts": ts,
            "role": role,
            "sentence": sentence,
            "needsConfirm": bool(needs_confirm),
        }
        if words:
            item["words"] = list(words)
        try:
            self.table.put_item(Item=item)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise ConversationError(f"대화 기록 저장 실패: {code}") from e
        return item

    def recent_history(self, session_id, limit=6):
        """최근 turn을 시간순(오래된 → 최신)으로 [{"speaker","text"}] 로 돌려준다.

        llm/sentence.py의 history 포맷에 맞춘다. 조회 실패 시 빈 리스트를
        돌려주어 문장 생성이 history 없이도 진행되게 한다(best-effort).
        """
        from boto3.dynamodb.conditions import Key
        from botocore.exceptions import ClientError

        try:
            # 최신 limit개를 내림차순으로 받고, 돌려줄 때 시간순으로 뒤집는다
            resp = self.table.query(
                KeyConditionExpression=Key("sessionId").eq(session_id),
                ScanIndexForward=False,
                Limit=limit,
            )
        except ClientError:
            return []
        items = resp.get("Items", [])
        # 진행 중 단어 버퍼(role=buffer)는 대화 기록이 아니므로 제외
        items = [it for it in items if it.get("role") != BUFFER_ROLE]
        items = sorted(items, key=lambda it: it.get("ts", 0))  # 오래된 → 최신
        return [
            {"speaker": SPEAKER.get(it.get("role"), it.get("role")),
             "text": it.get("sentence", "")}
            for it in items
        ]

    # ── 진행 중 단어 버퍼 ────────────────────────────────────
    # recognize 가 인식한 단어를 세션별로 쌓고, finalize 가 꺼내 문장을 만든 뒤 비운다.
    # 단어 누적을 서버가 맡는 구조(Q1=b)를 위한 것이다.

    def append_word(self, session_id, word):
        """버퍼에 단어 하나를 덧붙이고, 덧붙인 뒤의 전체 단어 리스트를 돌려준다.

        DynamoDB list_append 로 원자적으로 추가한다. 실패는 ConversationError.
        """
        from botocore.exceptions import ClientError

        try:
            resp = self.table.update_item(
                Key={"sessionId": session_id, "ts": BUFFER_TS},
                UpdateExpression=(
                    "SET #r = :role, "
                    "words = list_append(if_not_exists(words, :empty), :w)"
                ),
                ExpressionAttributeNames={"#r": "role"},
                ExpressionAttributeValues={
                    ":role": BUFFER_ROLE,
                    ":empty": [],
                    ":w": [word],
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise ConversationError(f"단어 버퍼 추가 실패: {code}") from e
        return list(resp.get("Attributes", {}).get("words", []))

    def get_buffer(self, session_id):
        """현재 버퍼의 단어 리스트. 없으면 빈 리스트. 조회 실패 시 빈 리스트(best-effort)."""
        from botocore.exceptions import ClientError

        try:
            resp = self.table.get_item(
                Key={"sessionId": session_id, "ts": BUFFER_TS}
            )
        except ClientError:
            return []
        return list((resp.get("Item") or {}).get("words", []))

    def clear_buffer(self, session_id):
        """버퍼 아이템을 삭제한다. 실패는 조용히 무시(best-effort)."""
        from botocore.exceptions import ClientError

        try:
            self.table.delete_item(
                Key={"sessionId": session_id, "ts": BUFFER_TS}
            )
        except ClientError:
            pass
