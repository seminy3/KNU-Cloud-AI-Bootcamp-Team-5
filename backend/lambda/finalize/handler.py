"""finalize Lambda (흐름 ① B2): 누적 단어 → 문장 → 음성 → 저장 → 응답.

WebSocket action "finalize"를 받는다. 프론트가 recognize로 모은 단어 목록을
보내면, Bedrock으로 문장을 만들고 Polly로 음성을 합성해 S3 URL로 돌려준다.
대화 기록(DynamoDB)에 환자 발화를 저장한다.

요청(브라우저 → 서버):
  {"action":"finalize","sessionId":"abc123","words":["나","병원","가다"]}
응답(서버 → 브라우저):
  {"type":"speech","words":[...],"sentence":"...","audioUrl":"https://...","needsConfirm":false}
오류:
  {"type":"error","stage":"sentence|speech","message":"...","retryable":true}

부분 성공: 문장 생성이 fallback이면 sentence는 채우되 needsConfirm=true.
음성 합성이 실패하면 문장/자막은 돌려주고 audioUrl=null, needsConfirm=true로 둔다
(화면이 멈추지 않게. 자막만으로도 전달은 된다).

배포 패키지에는 이 파일, backend/common/, llm/(sentence.py, handler.py),
data/labels.csv 가 포함된다. anthropic[bedrock] SDK 는 Lambda Layer로 제공된다
(CDK AnthropicLayer).

환경 변수
  AUDIO_BUCKET    음성 mp3 S3 버킷
  TABLE_NAME      대화 기록 DynamoDB 테이블
  POLLY_VOICE     기본 Seoyeon
  MODEL_ID        Bedrock 모델 (llm 기본값 사용 가능)
  HISTORY_TURNS   history 조회 개수 (기본 6)
"""
import logging
import os
import sys

# 배포 패키지 안에서 common/ 과 llm/ 을 찾도록 경로 추가
_here = os.path.dirname(__file__)
for sub in ("common", "llm", ""):
    sys.path.insert(0, os.path.join(_here, sub))

from conversation import ConversationError, ConversationStore  # noqa: E402
from speech import SpeechError, SpeechSynthesizer  # noqa: E402
from ws import connection_id, make_poster, parse_body, send  # noqa: E402

log = logging.getLogger()
log.setLevel(logging.INFO)

HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", 6))

# 콜드 스타트에 한 번만
_store = None
_synth = None
_generate = None


def store():
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


def synth():
    global _synth
    if _synth is None:
        _synth = SpeechSynthesizer()
    return _synth


def generate_sentence(words, history):
    """llm 패키지의 문장 생성을 호출한다. {sentence, ambiguous, fallback} 반환.

    llm/handler.py의 lambda_handler가 labels 필터·fallback까지 처리하므로 재사용.
    import는 콜드 스타트 비용을 늦추려 함수 안에서.
    """
    global _generate
    if _generate is None:
        from handler import lambda_handler as llm_handler  # llm/handler.py
        _generate = llm_handler
    return _generate({"words": words, "history": history})


def _handle(body):
    """비즈니스 로직. 보낼 payload(dict) 반환. 대화기록 저장은 best-effort.

    단어 출처(Q1=b 서버 누적):
      - body 에 words 가 오면 그걸 쓴다(하위호환/프론트 누적 방식도 지원).
      - 없으면 서버가 recognize 때 쌓아 둔 세션 버퍼에서 읽는다.
    문장 생성에 성공하면 버퍼를 비운다(다음 문장을 위해).
    """
    session_id = body.get("sessionId")
    words = [w for w in (body.get("words") or []) if w]
    if not words and session_id:
        # 서버 누적 버퍼에서 가져온다
        words = [w for w in store().get_buffer(session_id) if w]
    if not words:
        return {"type": "error", "stage": "sentence",
                "message": "변환할 단어가 없습니다.", "retryable": True}

    # 1) history 조회 (실패해도 빈 리스트)
    history = []
    if session_id:
        try:
            history = store().recent_history(session_id, limit=HISTORY_TURNS)
        except ConversationError:
            log.warning("history 조회 실패, 빈 history로 진행")

    # 2) 문장 생성 (llm)
    try:
        out = generate_sentence(words, history)
    except Exception:  # noqa: BLE001 - llm 내부 예외도 여기서 사용자 안내로
        log.exception("문장 생성 실패 words=%s", words)
        return {"type": "error", "stage": "sentence",
                "message": "문장 변환에 실패했어요. 다시 시도해 주세요.", "retryable": True}

    sentence = out.get("sentence", "")
    need_confirm = bool(out.get("ambiguous") or out.get("fallback"))

    # 3) 음성 합성 (실패해도 자막은 전달)
    audio_url = None
    if sentence and session_id:
        try:
            audio_url = synth().synthesize_to_url(sentence, session_id)
        except SpeechError:
            log.exception("음성 합성 실패, 자막만 전달 sentence=%s", sentence)
            need_confirm = True

    # 4) 대화 기록 저장 (best-effort)
    if session_id and sentence:
        try:
            store().put_turn(session_id, role="patient", sentence=sentence,
                             words=words, needs_confirm=need_confirm)
        except ConversationError:
            log.warning("대화 기록 저장 실패(무시)")

    # 5) 서버 누적 버퍼를 비운다. 문장을 만들었으니 다음 문장을 새로 쌓는다.
    #    body 로 words 가 직접 온 경우에도, 세션 버퍼가 남아 있으면 정리한다.
    if session_id:
        store().clear_buffer(session_id)

    return {"type": "speech", "words": words, "sentence": sentence,
            "audioUrl": audio_url, "needsConfirm": need_confirm}


def lambda_handler(event, context=None):
    cid = connection_id(event)
    body = parse_body(event)
    payload = _handle(body)
    try:
        poster = make_poster(event)
        send(poster, cid, payload)
    except Exception:  # noqa: BLE001
        log.exception("연결로 응답 전송 실패 cid=%s", cid)
    return {"statusCode": 200}
