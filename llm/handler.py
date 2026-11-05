"""Lambda: 수어 인식 단어 → 의료진 화면 문장.

백엔드 Lambda가 이 함수를 직접 import하거나, 이 파일을 별도 Lambda로 배포해 invoke한다.

event:  {"words": ["어제밤", "머리", "아프다"],
         "history": [{"speaker": "의사", "text": "어디가 불편하세요?"}, ...]}   # history는 선택
return: {"sentence": "어젯밤부터 머리가 아파요.", "ambiguous": false, "fallback": false}

- history는 최근 HISTORY_TURNS개만 쓴다 (DynamoDB 대화 기록을 시간순으로 넘기면 됨)
- Bedrock 호출이 실패하거나 늦으면 단어를 이어 붙인 문장을 fallback=true로 돌려준다.
  화면이 멈추지 않게 하려는 것으로, 프론트는 fallback이면 "확인 필요" 표시를 한다.

환경 변수
  MODEL_ID        기본 global.anthropic.claude-opus-5-5
  BEDROCK_REGION  기본 ap-northeast-2
  LABELS_PATH     labels.csv 위치 (배포 패키지에 같이 넣고 지정)
  TIMEOUT_SEC     Bedrock 요청 제한 시간, 기본 8
배포 패키지: handler.py, sentence.py, labels.csv + anthropic[bedrock] (레이어 또는 함께 설치)
"""
import logging
import os

from sentence import DEFAULT_MODEL, DEFAULT_REGION, LABELS_CSV, generate, load_labels, make_client

HISTORY_TURNS = 6
log = logging.getLogger()
log.setLevel(logging.INFO)

# 콜드 스타트 때 한 번만 만든다
LABELS = load_labels(os.environ.get("LABELS_PATH", LABELS_CSV))
MODEL_ID = os.environ.get("MODEL_ID", DEFAULT_MODEL)
_client = None


def client():
    global _client
    if _client is None:
        # 3초 목표라 재시도는 1번만
        _client = make_client(os.environ.get("BEDROCK_REGION", DEFAULT_REGION),
                              timeout=float(os.environ.get("TIMEOUT_SEC", 8)), max_retries=1)
    return _client


def fallback_sentence(words):
    return " ".join(w for i, w in enumerate(words) if i == 0 or w != words[i - 1])


def lambda_handler(event, context=None):
    words = [w for w in event.get("words", []) if w]
    if not words:
        return {"sentence": "", "ambiguous": False, "fallback": False}
    unknown = [w for w in words if w not in LABELS]
    if unknown:  # 인식 모델 라벨과 labels.csv가 어긋난 경우. 빼고 진행
        log.warning("labels.csv에 없는 단어 제외: %s", unknown)
        words = [w for w in words if w in LABELS]
        if not words:
            return {"sentence": "", "ambiguous": True, "fallback": True}
    history = event.get("history", [])[-HISTORY_TURNS:]
    try:
        out = generate(words, LABELS, history, client(), MODEL_ID)
        log.info("words=%s sentence=%s usage=%s", words, out["sentence"], out["usage"])
        return {"sentence": out["sentence"], "ambiguous": out["ambiguous"], "fallback": False}
    except Exception:
        log.exception("Bedrock 문장 생성 실패, words=%s", words)
        return {"sentence": fallback_sentence(words), "ambiguous": True, "fallback": True}
