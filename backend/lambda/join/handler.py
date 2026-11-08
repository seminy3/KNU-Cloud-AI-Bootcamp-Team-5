"""join Lambda: 화면이 대화 코드와 역할을 등록한다.

요청(브라우저 → 서버):
  {"action":"join","sessionId":"abc123","role":"patient"}
응답(같은 대화 코드의 모든 연결, 자기 자신 포함):
  {"type":"presence","roles":["doctor","patient"]}
오류:
  {"type":"error","stage":"join","message":"...","retryable":false}

재연결하면 연결 ID가 바뀌므로 브라우저는 연결될 때마다 join을 다시 보낸다.
환경 변수: TABLE_NAME
"""
import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.dirname(__file__))

from conversation import ConversationStore  # noqa: E402
from peers import ROLES, PeerStore, broadcast, presence_payload  # noqa: E402
from ws import connection_id, make_poster, parse_body, send  # noqa: E402

log = logging.getLogger()
log.setLevel(logging.INFO)

SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")

_peers = None


def peers():
    global _peers
    if _peers is None:
        _peers = PeerStore(ConversationStore().table)
    return _peers


def _error(message):
    return {"type": "error", "stage": "join", "message": message, "retryable": False}


def lambda_handler(event, context=None):
    cid = connection_id(event)
    body = parse_body(event)
    session_id = body.get("sessionId")
    role = body.get("role")
    poster = make_poster(event)
    if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
        send(poster, cid, _error("대화 코드를 확인해 주세요."))
        return {"statusCode": 200}
    if role not in ROLES:
        send(poster, cid, _error("역할을 확인해 주세요."))
        return {"statusCode": 200}
    try:
        store = peers()
        store.join(session_id, cid, role)
        members = store.members(session_id)
    except Exception:  # noqa: BLE001
        log.exception("대화 참여 실패 session=%s", session_id)
        send(poster, cid, {**_error("대화 연결에 실패했습니다. 다시 시도해 주세요."), "retryable": True})
        return {"statusCode": 200}
    # 자기 자신도 받도록 exclude 없이 보낸다
    broadcast(poster, store, session_id, presence_payload(members), members=members)
    return {"statusCode": 200}
