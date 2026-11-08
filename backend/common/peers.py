"""같은 대화 코드에 접속한 연결 목록과, 그 연결들로의 전달.

환자 화면과 의료진 화면이 서로 다른 기기에서 같은 대화 코드로 접속하면,
한쪽에서 생긴 문장·자막을 다른 쪽으로 보낸다.

대화 기록 테이블을 같이 쓴다(PK sessionId, SK ts).
  연결 항목    sessionId=<대화 코드>, ts=음수(연결 ID 해시), role="conn", connectionId, peerRole
  역방향 항목  sessionId="conn#<연결 ID>", ts=0, role="conn", session=<대화 코드>
역방향 항목은 $disconnect 때 연결 ID만으로 대화 코드를 찾기 위한 것이다.
두 항목 모두 ttl을 걸어 끊김 이벤트를 놓쳐도 남지 않게 한다(WebSocket 연결은 최대 2시간).

서버 → 브라우저 메시지
  {"type":"presence","roles":["doctor","patient"]}             접속한 역할 목록
  {"type":"relay","role":"patient","text":"...","audioUrl":..., "needsConfirm":false}
  {"type":"relay","role":"doctor","text":"..."}
"""
import logging
import time
import zlib

from ws import send

log = logging.getLogger()

ROLES = ("patient", "doctor")
CONN_ROLE = "conn"
LOOKUP_PREFIX = "conn#"
TTL_SECONDS = 3 * 3600


def _conn_ts(connection_id):
    # 대화 기록(양수 ts), 단어 버퍼(ts=0)와 겹치지 않게 음수를 쓴다
    return -(zlib.crc32(connection_id.encode("utf-8")) + 1)


class PeerStore:
    def __init__(self, table):
        self.table = table

    def join(self, session_id, connection_id, role):
        ttl = int(time.time()) + TTL_SECONDS
        self.table.put_item(Item={
            "sessionId": session_id, "ts": _conn_ts(connection_id), "role": CONN_ROLE,
            "connectionId": connection_id, "peerRole": role, "ttl": ttl,
        })
        self.table.put_item(Item={
            "sessionId": LOOKUP_PREFIX + connection_id, "ts": 0, "role": CONN_ROLE,
            "session": session_id, "ttl": ttl,
        })

    def remove(self, session_id, connection_id):
        self.table.delete_item(Key={"sessionId": session_id, "ts": _conn_ts(connection_id)})

    def leave(self, connection_id):
        """연결을 지우고 그 연결이 있던 대화 코드를 돌려준다. 없으면 None."""
        key = {"sessionId": LOOKUP_PREFIX + connection_id, "ts": 0}
        item = self.table.get_item(Key=key).get("Item")
        if not item:
            return None
        session_id = item.get("session")
        self.table.delete_item(Key=key)
        if session_id:
            self.remove(session_id, connection_id)
        return session_id

    def members(self, session_id):
        """[(연결 ID, 역할)]. 만료 시간이 지난 항목은 뺀다(TTL 삭제는 늦게 일어난다)."""
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=Key("sessionId").eq(session_id) & Key("ts").lt(0)
        )
        now = int(time.time())
        return [
            (it["connectionId"], it.get("peerRole"))
            for it in resp.get("Items", [])
            if it.get("role") == CONN_ROLE and int(it.get("ttl", now + 1)) > now
        ]


def presence_payload(members):
    return {"type": "presence", "roles": sorted({r for _, r in members if r in ROLES})}


def _is_gone(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code") == "GoneException"


def broadcast(poster, peers, session_id, payload, exclude=None, members=None):
    """같은 대화 코드의 연결로 payload를 보낸다. 끊긴 연결은 목록에서 지운다.

    보낸 연결 수를 돌려준다. 전달 실패가 원래 요청을 막지 않도록 예외를 밖으로 내지 않는다.
    """
    sent = 0
    for cid, _ in (members if members is not None else peers.members(session_id)):
        if cid == exclude:
            continue
        try:
            send(poster, cid, payload)
            sent += 1
        except Exception as e:  # noqa: BLE001
            if _is_gone(e):
                try:
                    peers.remove(session_id, cid)
                except Exception:  # noqa: BLE001
                    log.warning("끊긴 연결 삭제 실패 cid=%s", cid)
            else:
                log.warning("상대 연결로 전달 실패 cid=%s: %s", cid, e)
    return sent


def relay(event, get_table, session_id, payload, exclude):
    """핸들러에서 한 줄로 쓰는 전달. get_table은 테이블을 돌려주는 함수. 실패는 로그만 남긴다."""
    if not session_id:
        return 0
    try:
        from ws import make_poster

        return broadcast(make_poster(event), PeerStore(get_table()), session_id, payload, exclude=exclude)
    except Exception:  # noqa: BLE001
        log.exception("상대 화면 전달 실패 session=%s", session_id)
        return 0
