# Mock WebSocket 서버 (프론트 개발용)

백엔드를 AWS에 배포하기 전에, 프론트(role 4)가 WebSocket 계약대로 UI를 붙여볼 수 있도록 **고정/모의 응답**을 돌려주는 로컬 서버다. 실제 SageMaker·Bedrock·Polly는 호출하지 않는다.

계약은 [`../../docs/WEBSOCKET_API.md`](../../docs/WEBSOCKET_API.md)와 100% 동일한 형식으로 응답한다.

## 실행

```
python -m pip install websockets
python backend/mock/server.py
```

- 기본 주소: `ws://localhost:8765`
- 종료: Ctrl+C

## 동작

| 보내는 action | 모의 응답 |
|---|---|
| `recognize` | 세션별로 단어를 하나씩 누적(`나→병원→가다→아프다→머리` 순환). `{type:"word", word, candidates, words, count}` |
| `finalize` | 누적 단어(또는 body `words`)로 `{type:"speech", words, sentence:"저 병원에 가려고요", audioUrl, needsConfirm:false}`. 버퍼는 비움 |
| `doctorUtterance` | `{type:"transcript", text}` (받은 text 그대로) |
| 그 외/오류 | `{type:"error", stage, message, retryable}` |

- `sessionId`별로 단어 버퍼가 분리된다(실서버의 DynamoDB 버퍼를 메모리로 흉내).
- `audioUrl`은 재생되지 않는 더미 URL이다(모의). 음성 재생 UI 연결 확인용.

## 빠른 확인 (브라우저 콘솔 예시)

```js
const ws = new WebSocket("ws://localhost:8765");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.onopen = () => {
  ws.send(JSON.stringify({ action: "recognize", sessionId: "demo", keypoints: [[[0,0]]] }));
  ws.send(JSON.stringify({ action: "recognize", sessionId: "demo", keypoints: [[[0,0]]] }));
  ws.send(JSON.stringify({ action: "finalize", sessionId: "demo" }));
};
```

## 참고

- 라우팅 로직은 `server.py`의 `handle_message()`에 있고, `websockets` 없이 단위 테스트된다(`backend/tests/test_mock_server.py`).
- 실제 백엔드가 배포되면 접속 URL만 `wss://...`(배포 출력 `WebSocketUrl`)로 바꾸면 된다. 메시지 형식은 동일하다.
