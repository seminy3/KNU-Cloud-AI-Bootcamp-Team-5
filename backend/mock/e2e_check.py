"""배포된 WebSocket 백엔드 e2e 빠른 검증.

실제 WebSocketUrl 에 붙어 recognize(실제 keypoints) → finalize 를 보내고
서버가 push 하는 응답을 출력한다. 검증 후 바로 끝낸다.

    python backend/mock/e2e_check.py wss://xxxx.execute-api.ap-northeast-2.amazonaws.com/dev
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION = "e2e-check"


async def main(url):
    # test_1.json 의 keypoints 를 재사용(실제 (30,49,2))
    kp = json.loads((REPO_ROOT / "test_1.json").read_text(encoding="utf-8"))["keypoints"]

    async with websockets.connect(url) as ws:
        # 1) recognize 한 번
        await ws.send(json.dumps({"action": "recognize", "sessionId": SESSION, "keypoints": kp}))
        r1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        print("recognize →", json.dumps(r1, ensure_ascii=False))

        # 2) finalize (서버 누적 버퍼 사용)
        await ws.send(json.dumps({"action": "finalize", "sessionId": SESSION}))
        r2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        print("finalize  →", json.dumps(r2, ensure_ascii=False))

        ok = r1.get("type") == "word" and r2.get("type") == "speech"
        print("\n결과:", "PASS" if ok else "확인 필요")
        if r2.get("type") == "speech":
            print("  문장:", r2.get("sentence"))
            print("  audioUrl:", (r2.get("audioUrl") or "")[:60], "...")
            print("  needsConfirm:", r2.get("needsConfirm"))
        return 0 if ok else 1


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if not url:
        print("사용법: python e2e_check.py <WebSocketUrl>")
        sys.exit(2)
    sys.exit(asyncio.run(main(url)))
