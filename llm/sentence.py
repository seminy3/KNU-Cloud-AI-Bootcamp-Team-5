"""수어 인식 단어 나열 → 의료진에게 보여 줄 한국어 문장 (Bedrock Claude).

Lambda에서도 그대로 쓰도록 anthropic SDK는 generate() 안에서만 import한다.
    pip install -U "anthropic[bedrock]"

    from sentence import load_labels, generate
    labels = load_labels("labels.csv")
    generate(["어제밤", "머리", "아프다"], labels,
             history=[{"speaker": "의사", "text": "언제부터 아프셨어요?"}])
    # → {"sentence": "어젯밤부터 머리가 아파요.", "ambiguous": False}
"""
import csv
import json
from pathlib import Path

# 실습 계정에서는 bedrock-mantle 엔드포인트가 404라 bedrock-runtime(InvokeModel) + global 추론 프로필을 쓴다
DEFAULT_MODEL = "global.anthropic.claude-opus-5-5"
DEFAULT_REGION = "ap-northeast-2"  # 서울
LABELS_CSV = Path(__file__).resolve().parent.parent / "data" / "labels.csv"

# 표제어만으로는 뜻이 모호하거나 대체 동작으로 넣은 클래스
WORD_NOTES = {
    "상처/다치다": "다치다 또는 상처 (같은 동작)",
    "어제밤": "어제 밤 / 어젯밤",
    "맞다": "네, 맞아요 (긍정 대답으로 자주 쓰임)",
    "아니다": "아니요, 아니에요 (부정 대답)",
    "가루약": "약 전반 (약 수어 대신 쓰는 동작)",
    "응급실": "응급, 급하다 (응급실 수어가 없어 긴급 동작으로 대체)",
    "전": "전에, 예전에",
    "좋아지다": "나아지다, 회복되다",
    "부러지다": "부러지다, 골절",
    "많다": "많다, 많이",
    "나": "나 → 문장에서는 '저'",
    "너": "상대를 가리킴. 의료진에게 말할 때는 '선생님'",
    "엄마": "엄마, 어머니",
    "1~10": "숫자. 맥락에 따라 날짜 수, 횟수, 통증 강도(0~10점), 나이 등",
}

SYSTEM_TEMPLATE = """당신은 병원 진료실의 한국수어 통역 보조입니다.
농인 환자의 수어를 인식 모델이 단어(표제어) 나열로 바꿔 보내면, 이를 의료진 화면에 띄울 자연스러운 한국어 문장 한 개로 옮깁니다.

## 입력
- 단어 나열: 환자가 수어로 말한 순서대로의 표제어. 한국수어는 조사·어미가 없고 어순이 한국어와 다를 수 있습니다.
- 이전 대화(있을 때): 의료진의 질문과 환자의 앞선 발화. 단어만으로 빠진 주어·시점·대상을 채울 때 근거로 씁니다.

## 문장 만드는 규칙
1. 화자는 환자 본인입니다. 1인칭('저')으로, 의료진에게 하는 공손한 해요체로 씁니다.
2. 단어 나열에 있는 뜻만 옮깁니다. 증상·부위·시점·횟수·정도를 새로 지어내지 않습니다. 이전 대화에서 분명히 이어지는 내용(질문의 대상 등)만 보충할 수 있습니다.
3. 의료적으로 중요한 단어(부위, 증상, 시점, 숫자, 부정)는 빠짐없이 반영합니다. 특히 '아니다', '없다'는 문장의 뜻을 뒤집으니 놓치지 않습니다.
4. '언제', '어디', '무엇', '왜', '어떻게'가 있으면 의료진에게 묻는 질문 문장일 수 있습니다.
5. 같은 단어가 연달아 나오면 인식 중복일 수 있으니 한 번으로 봅니다. 단, '많다', '조금', '계속' 같은 정도 표현은 강조로 볼 수 있습니다.
6. 단어가 하나뿐이면 그 단어를 살린 짧은 문장으로 씁니다(예: '화장실' → '화장실에 가고 싶어요.'). 이전 대화의 질문에 대한 대답이라면 그 질문에 맞춰 답합니다.
7. 단어끼리 뜻이 충돌하거나, 단어만으로는 뜻을 하나로 정하기 어려우면 가장 그럴듯한 문장을 쓰고 ambiguous를 true로 합니다. 의료진이 환자에게 다시 확인하라는 표시입니다. 이때도 sentence는 환자가 하는 말이어야 하며, "확인이 필요해요"처럼 상황을 설명하는 말을 sentence에 쓰지 않습니다. 모호하다는 사실은 ambiguous로만 알립니다.
8. 문장은 한 문장, 40자 안팎으로 짧게 씁니다. 설명이나 따옴표를 붙이지 않습니다.

## 인식 가능한 단어 ({n_words}개)
{vocab}

## 뜻 참고
{notes}
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentence": {"type": "string"},
        "ambiguous": {"type": "boolean"},
    },
    "required": ["sentence", "ambiguous"],
    "additionalProperties": False,
}


def load_labels(path=LABELS_CSV):
    with open(path, encoding="utf-8-sig") as f:
        return [r["label"] for r in csv.DictReader(f)]


def build_system(labels):
    notes = "\n".join(f"- {k}: {v}" for k, v in WORD_NOTES.items())
    return SYSTEM_TEMPLATE.format(n_words=len(labels), vocab=", ".join(labels), notes=notes)


def build_user(words, history=None):
    lines = []
    if history:
        lines.append("이전 대화:")
        lines += [f"- {h['speaker']}: {h['text']}" for h in history]
        lines.append("")
    lines.append("단어 나열: " + " / ".join(words))
    return "\n".join(lines)


def build_request(words, labels, history=None, model=DEFAULT_MODEL):
    unknown = [w for w in words if w not in labels]
    if unknown:
        raise ValueError(f"labels.csv에 없는 단어: {unknown}")
    return {
        "model": model,
        "max_tokens": 1024,
        # 178개 단어 목록이 들어간 system은 매 요청 같으므로 캐시
        "system": [{"type": "text", "text": build_system(labels), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_user(words, history)}],
        # 짧은 변환 작업이라 지연을 줄이려 effort low
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }


def make_client(region=DEFAULT_REGION, **kwargs):
    from anthropic import AnthropicBedrock

    return AnthropicBedrock(aws_region=region, **kwargs)


def generate(words, labels, history=None, client=None, model=DEFAULT_MODEL):
    """반환: {"sentence", "ambiguous", "usage"}. 거절·잘림은 RuntimeError."""
    client = client or make_client()
    msg = client.messages.create(**build_request(words, labels, history, model))
    if msg.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={msg.stop_reason}")
    text = next(b.text for b in msg.content if b.type == "text")
    out = json.loads(text)
    out["usage"] = {
        "input": msg.usage.input_tokens,
        "output": msg.usage.output_tokens,
        "cache_read": msg.usage.cache_read_input_tokens or 0,
    }
    return out
