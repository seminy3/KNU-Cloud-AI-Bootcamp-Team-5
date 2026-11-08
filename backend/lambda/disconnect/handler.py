"""$disconnect Lambda: 끊긴 연결을 목록에서 지우고 남은 연결에 접속 상태를 알린다.

$disconnect 의 응답은 브라우저로 가지 않는다(이미 끊겼다). 남은 연결로만 보낸다.
  {"type":"presence","roles":["patient"]}
환경 변수: TABLE_NAME
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.dirname(__file__))

from conversation import ConversationStore  # noqa: E402
from peers import PeerStore, broadcast, presence_payload  # noqa: E402
from ws import connection_id, make_poster  # noqa: E402

log = logging.getLogger()
log.setLevel(logging.INFO)

_peers = None


def peers():
    global _peers
    if _peers is None:
        _peers = PeerStore(ConversationStore().table)
    return _peers


def lambda_handler(event, context=None):
    cid = connection_id(event)
    try:
        store = peers()
        session_id = store.leave(cid)
        if session_id:
            members = store.members(session_id)
            broadcast(make_poster(event), store, session_id, presence_payload(members), members=members)
    except Exception:  # noqa: BLE001
        log.exception("연결 정리 실패 cid=%s", cid)
    return {"statusCode": 200}
