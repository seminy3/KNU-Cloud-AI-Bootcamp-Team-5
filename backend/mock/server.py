"""로컬 Mock WebSocket 서버 — 프론트(role 4) 개발용.

백엔드를 AWS에 배포하기 전에, 프론트가 WebSocket 계약(docs/WEBSOCKET_API.md)대로
동작을 붙여볼 수 있도록 고정/모의 응답을 돌려준다. 실제 SageMaker·Bedrock·Polly는
호출하지 않는다(아키텍처 문서의 "Mock 모델 먼저" 전략).

실행:
    python -m pip install websockets
    python backend/mock/server.py
    # ws://localhost:8765 로 접속

라우팅 로직(handle_message)은 의존성 없이 단위 테스트할 수 있게 분리했다.
서버 구동부(websockets)는 아래 __main__ 에서만 쓴다.
"""
import json

# 세션별 누적 단어 버퍼(실서버의 DynamoDB 버퍼를 메모리로 흉내)
_buffers: dict[str, list[str]] = {}

# recognize 가 순환하며 돌려줄 모의 단어들
_MOCK_WORDS = ["나", "병원", "가다", "아프다", "머리"]

# 데모용 고정 문장(누적 단어와 무관하게 자연스러운 예시를 보여 준다)
_MOCK_SENTENCE = "저 병원에 가려고요"
_MOCK_AUDIO_URL = "https://example.invalid/mock-audio.mp3"


def _top_k(word: str):
    """1위가 word 인 그럴듯한 후보 목록을 만든다."""
    others = [w for w in _MOCK_WORDS if w != word][:2]
    cands = [{"name": word, "prob": 0.62}]
    cands += [{"name": w, "prob": round(0.1 - i * 0.03, 3)} for i, w in enumerate(others)]
    return cands


def handle_message(raw, buffers=None):
    """계약(docs/WEBSOCKET_API.md)대로 요청 1건을 처리해 응답 dict 를 돌려준다.

    buffers 를 주입하면(테스트) 세션 상태를 격리할 수 있다. 없으면 모듈 전역을 쓴다.
    """
    buffers = _buffers if buffers is None else buffers
    try:
        body = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (ValueError, TypeError):
        return {"type": "error", "stage": "recognize",
                "message": "JSON 파싱 실패", "retryable": True}

    action = body.get("action")
    session_id = body.get("sessionId", "default")

    if action == "recognize":
        if not body.get("keypoints"):
            return {"type": "error", "stage": "recognize",
                    "message": "keypoints가 없습니다.", "retryable": True}
        # 누적 개수에 따라 단어를 순환 선택(결정적이라 테스트·데모가 예측 가능)
        buf = buffers.setdefault(session_id, [])
        word = _MOCK_WORDS[len(buf) % len(_MOCK_WORDS)]
        buf.append(word)
        return {"type": "word", "word": word, "candidates": _top_k(word),
                "words": list(buf), "count": len(buf)}

    if action == "finalize":
        words = [w for w in (body.get("words") or []) if w]
        if not words:
            words = list(buffers.get(session_id, []))
        if not words:
            return {"type": "error", "stage": "sentence",
                    "message": "변환할 단어가 없습니다.", "retryable": True}
        buffers[session_id] = []  # 버퍼 비움(실서버와 동일)
        return {"type": "speech", "words": words, "sentence": _MOCK_SENTENCE,
                "audioUrl": _MOCK_AUDIO_URL, "needsConfirm": False}

    if action == "doctorUtterance":
        text = (body.get("text") or "").strip()
        if not text:
            return {"type": "error", "stage": "transcript",
                    "message": "저장할 텍스트가 없습니다.", "retryable": True}
        return {"type": "transcript", "text": text}

    return {"type": "error", "stage": "recognize",
            "message": f"알 수 없는 action: {action}", "retryable": False}


async def _ws_handler(websocket):
    """websockets 연결 핸들러. 받은 메시지마다 handle_message 응답을 돌려준다."""
    async for raw in websocket:
        resp = handle_message(raw)
        await websocket.send(json.dumps(resp, ensure_ascii=False))


def main(host="localhost", port=8765):
    import asyncio

    import websockets

    async def run():
        async with websockets.serve(_ws_handler, host, port):
            print(f"Mock WebSocket 서버 실행 중: ws://{host}:{port}")
            print("docs/WEBSOCKET_API.md 계약대로 모의 응답을 돌려줍니다. Ctrl+C로 종료.")
            await asyncio.Future()  # 영원히 대기

    asyncio.run(run())


if __name__ == "__main__":
    main()
