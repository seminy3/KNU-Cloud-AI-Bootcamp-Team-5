"""recognize Lambda (흐름 ① B1): keypoints → 단어 1개 인식 + 서버 누적.

WebSocket action "recognize"를 받아 SageMaker(sign-word-model)로 단어를 인식하고,
그 단어를 세션별 버퍼(DynamoDB)에 쌓은 뒤, 1위 단어·후보·현재 누적 현황을 돌려준다.
단어 누적은 서버가 맡는다(Q1=b). 프론트는 문장 끝에서 finalize만 호출하면 된다.

요청(브라우저 → 서버):
  {"action":"recognize","sessionId":"abc123","keypoints":[(30,49,2)]}
응답(서버 → 브라우저):
  {"type":"word","word":"병원","candidates":[{"name":"병원","prob":0.62}, ...],
   "words":["나","병원"],"count":2}
오류:
  {"type":"error","stage":"recognize","message":"...","retryable":true}

배포 패키지에는 이 파일과 함께 backend/common/ 이 포함된다.
환경 변수: ENDPOINT_NAME (기본 sign-word-model), TABLE_NAME(대화/버퍼 테이블)
"""
import logging
import math
import os
import sys

# 배포 패키지 안에서 common 모듈을 찾도록 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.dirname(__file__))

from conversation import ConversationError, ConversationStore  # noqa: E402
from sagemaker_client import RecognizeError, SageMakerClient  # noqa: E402
from ws import connection_id, make_poster, parse_body, send  # noqa: E402

log = logging.getLogger()
log.setLevel(logging.INFO)

TOP_K = int(os.environ.get("TOP_K", 5))
FRAMES, POINTS = 30, 49
# 정규화 좌표는 어깨 너비 기준이라 보통 ±5 안이다. 이보다 크면 잘못 만든 입력으로 본다
MAX_ABS = 100.0


def validate_keypoints(kp):
    """(30, 49, 2) 유한한 숫자인지 본다. 문제가 있으면 이유(str), 없으면 None.

    잘못된 입력이 엔드포인트에 가면 모델 워커가 죽고 다시 뜨면서 다음 호출이 느려진다.
    """
    if not isinstance(kp, list) or len(kp) != FRAMES:
        return f"프레임 수 {len(kp) if isinstance(kp, list) else type(kp).__name__}"
    nonzero = False
    for frame in kp:
        if not isinstance(frame, list) or len(frame) != POINTS:
            return "프레임당 지점 수"
        for point in frame:
            if (not isinstance(point, list) or len(point) != 2
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               and math.isfinite(v) and abs(v) <= MAX_ABS for v in point)):
                return "지점 값"
            nonzero = nonzero or point[0] != 0 or point[1] != 0
    if not nonzero:
        return "모든 지점이 0"
    return None

# 콜드 스타트 때 한 번만 만든다
_recognizer = None
_store = None


def recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = SageMakerClient()
    return _recognizer


def store():
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


def _handle(body):
    """비즈니스 로직만 분리(테스트 쉽게). 보낼 payload(dict)를 돌려준다."""
    keypoints = body.get("keypoints")
    if not keypoints:
        return {"type": "error", "stage": "recognize",
                "message": "keypoints가 없습니다.", "retryable": True}
    problem = validate_keypoints(keypoints)
    if problem:
        log.warning("입력 거부: %s", problem)
        return {"type": "error", "stage": "recognize",
                "message": "동작 데이터가 올바르지 않아요. 다시 시도해 주세요.", "retryable": True}
    try:
        out = recognizer().recognize(keypoints, top_k=TOP_K)
    except RecognizeError as e:
        log.warning("인식 실패: %s", e)
        return {"type": "error", "stage": "recognize",
                "message": "수어 인식에 실패했어요. 다시 시도해 주세요.", "retryable": True}
    if out["word"] is None:
        return {"type": "error", "stage": "recognize",
                "message": "인식된 단어가 없어요. 다시 시도해 주세요.", "retryable": True}

    word = out["word"]
    # 웹캠 시험 결과를 모으기 위한 로그(단어와 확률만, 좌표는 남기지 않는다)
    log.info("recognized session=%s top=%s", body.get("sessionId"),
             [(c.get("name"), round(float(c.get("prob") or 0), 4)) for c in out["candidates"]])
    # 인식한 단어를 세션 버퍼에 쌓는다. 버퍼 실패는 인식 자체를 막지 않도록
    # best-effort: 저장이 안 돼도 방금 단어는 돌려준다(words는 최소한 그 단어).
    session_id = body.get("sessionId")
    words = [word]
    if session_id:
        try:
            words = store().append_word(session_id, word)
        except ConversationError:
            log.warning("단어 버퍼 저장 실패(무시), word=%s", word)

    return {"type": "word", "word": word, "candidates": out["candidates"],
            "words": words, "count": len(words)}


def lambda_handler(event, context=None):
    cid = connection_id(event)
    body = parse_body(event)
    payload = _handle(body)
    try:
        poster = make_poster(event)
        send(poster, cid, payload)
    except Exception:  # noqa: BLE001 - push 실패해도 Lambda는 200으로 끝낸다
        log.exception("연결로 응답 전송 실패 cid=%s", cid)
    # WebSocket 라우트는 2xx를 돌려주면 된다
    return {"statusCode": 200}
