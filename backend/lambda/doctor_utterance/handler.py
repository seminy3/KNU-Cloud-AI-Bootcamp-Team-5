"""doctor_utterance Lambda (흐름 ② 백엔드): 의사 발화 텍스트 저장.

흐름 ②(음성 → 자막)에서 음성→텍스트 변환은 브라우저가 Cognito 임시 자격증명으로
Transcribe Streaming에 직접 연결해 수행한다(아키텍처 문서 방식). 백엔드는 변환된
의사 발화 텍스트를 받아 대화 기록(DynamoDB)에 role="doctor"로 저장한다.
이 기록은 흐름 ①의 history로 쓰여 환자 발화 문장 생성의 맥락이 된다.

요청(브라우저 → 서버):
  {"action":"doctorUtterance","sessionId":"abc123","text":"어디가 아프세요?"}
응답(서버 → 브라우저):
  {"type":"transcript","text":"어디가 아프세요?"}
오류:
  {"type":"error","stage":"transcript","message":"...","retryable":true}

수어 영상 변환은 범위 밖(설계 변경: 음성→수어는 자막만). 이 Lambda는 저장과
확인 응답만 한다.

배포 패키지에는 이 파일과 함께 backend/common/ 이 포함된다.
환경 변수: TABLE_NAME (대화 기록 테이블)
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.dirname(__file__))

from conversation import ConversationError, ConversationStore  # noqa: E402
from peers import relay  # noqa: E402
from ws import connection_id, make_poster, parse_body, send  # noqa: E402

log = logging.getLogger()
log.setLevel(logging.INFO)

_store = None


def store():
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


def _handle(body):
    """비즈니스 로직. 보낼 payload(dict) 반환."""
    session_id = body.get("sessionId")
    text = (body.get("text") or "").strip()
    if not text:
        return {"type": "error", "stage": "transcript",
                "message": "저장할 텍스트가 없습니다.", "retryable": True}

    # 의사 발화를 대화 기록에 저장(best-effort). 저장 실패해도 자막 표시는 막지 않는다.
    if session_id:
        try:
            store().put_turn(session_id, role="doctor", sentence=text)
        except ConversationError:
            log.warning("의사 발화 저장 실패(무시), text=%s", text)

    return {"type": "transcript", "text": text}


def lambda_handler(event, context=None):
    cid = connection_id(event)
    body = parse_body(event)
    payload = _handle(body)
    try:
        poster = make_poster(event)
        send(poster, cid, payload)
    except Exception:  # noqa: BLE001
        log.exception("연결로 응답 전송 실패 cid=%s", cid)
    # 같은 대화 코드의 환자 화면으로 의료진 자막을 전달한다
    if payload.get("type") == "transcript":
        relay(event, lambda: store().table, body.get("sessionId"),
              {"type": "relay", "role": "doctor", "text": payload["text"]}, exclude=cid)
    return {"statusCode": 200}
