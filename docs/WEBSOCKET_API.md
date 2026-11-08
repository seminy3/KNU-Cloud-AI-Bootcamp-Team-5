# WebSocket API 계약 (프론트 ↔ 백엔드)

수어 ↔ 음성 번역 서비스의 실시간 통신 규격이다. 프론트(role 4)와 백엔드(role 3)가 함께 쓰는 약속이라 여기서 고정한다. 값·형식이 바뀌면 이 문서를 먼저 고친다.

- 프로토콜: API Gateway WebSocket
- 연결 URL: 배포 출력 `WebSocketUrl` (예: `wss://xxxx.execute-api.ap-northeast-2.amazonaws.com/dev`)
- 라우팅 키: 메시지 본문의 `action` 필드(`$request.body.action`)
- 리전: `ap-northeast-2`
- 모든 메시지는 JSON. 문자열 인코딩은 UTF-8.

## 공통 규칙

- `sessionId`: 대화 한 세션을 식별하는 문자열. **프론트가 생성**하고 모든 메시지에 동일하게 넣는다(한 대화 동안 고정). 서버는 이 값으로 단어 버퍼와 대화 기록을 묶는다.
- 서버 → 브라우저 응답은 모두 `type` 필드로 종류를 구분한다: `word` | `speech` | `transcript` | `error`.
- 에러는 공통 포맷을 쓴다(맨 아래 "에러" 참고).
- 액션을 보낸 WebSocket 연결로 응답이 push 된다.

---

## 흐름 ① 수어 → 음성·자막

### 1) `recognize` — 단어 인식 (여러 번 호출)

브라우저가 MediaPipe로 뽑은 한 단어 분량의 좌표(30프레임)를 보낸다. 서버가 SageMaker로 단어를 인식하고, **세션 버퍼에 누적**한 뒤, 방금 단어와 현재까지의 누적 현황을 돌려준다.

요청:
```jsonc
{
  "action": "recognize",
  "sessionId": "abc123",
  "keypoints": [ /* (30, 49, 2) — 30프레임 × 49포인트 × (x,y) */ ]
}
```

응답(`type: "word"`):
```jsonc
{
  "type": "word",
  "word": "병원",                       // 방금 인식한 1위 단어
  "candidates": [                        // top_k 후보(순위순)
    { "name": "병원", "prob": 0.62 },
    { "name": "의사", "prob": 0.10 }
  ],
  "words": ["나", "병원"],               // 이 세션에 지금까지 누적된 단어들
  "count": 2
}
```

- `keypoints` 규격: 모양 `(30, 49, 2)`, NaN 없음, 어깨 중심 원점·어깨너비=1로 정규화. 상세는 [`../ENDPOINT.md`](../ENDPOINT.md).
- `prob`는 label smoothing 때문에 맞아도 낮게 나온다(0.04~0.73). 확신도보다 **순위**로 쓰는 편이 안전하다.
- 단어 누적은 서버가 한다. 프론트는 `recognize`를 반복 호출만 하면 되고, 누적 리스트는 응답 `words`로 확인한다.

### 2) `finalize` — 문장 확정 (문장 끝에서 한 번)

프론트가 "문장 끝"이라고 판단하면(버튼 또는 "손 내리면 끝") 호출한다. 서버가 누적 단어를 Bedrock으로 문장화하고 Polly로 음성을 만들어 돌려준다. 처리 후 세션 버퍼를 비운다.

요청:
```jsonc
{
  "action": "finalize",
  "sessionId": "abc123"
  // "words": ["나","병원","가다"]   // (선택) 직접 보내면 서버 버퍼 대신 이 값을 쓴다
}
```

응답(`type: "speech"`):
```jsonc
{
  "type": "speech",
  "words": ["나", "병원", "가다"],
  "sentence": "저 병원에 가려고요",
  "audioUrl": "https://.../audio/abc123/169....mp3",  // 음성 mp3 (presigned, 만료 5분). 실패 시 null
  "needsConfirm": false                                // true면 프론트가 "확인 필요" 표시
}
```

- `needsConfirm`: 문장이 애매하거나(모델이 ambiguous/fallback 판단) 음성 합성이 실패하면 `true`.
- `audioUrl`이 `null`이어도 `sentence`(자막)는 항상 돌려준다 — 음성 합성이 실패해도 화면은 진행.
- `finalize` 후 서버 버퍼는 비워지므로, 다음 문장은 `recognize`부터 새로 쌓인다.

---

## 흐름 ② 음성 → 자막

### 음성 → 텍스트는 브라우저가 직접

의료진 음성의 텍스트 변환은 **브라우저가 Amazon Transcribe Streaming에 직접 연결**해서 한다(백엔드 경유 안 함). 이때 Cognito 임시 자격증명이 필요하다.

- 배포 출력 `IdentityPoolId` + 리전 `ap-northeast-2`로 Cognito Identity Pool에서 게스트(비인증) 자격증명을 받는다.
- 그 자격증명으로 Transcribe Streaming(한국어 `ko-KR`)에 직접 연결해 실시간 텍스트를 받는다.
- 수어 영상 변환은 범위 밖(MVP): 의료진 발화는 **자막으로만** 표시한다.

### `doctorUtterance` — 의사 발화 저장 (선택)

브라우저가 Transcribe로 얻은 텍스트를 백엔드에 전달해 대화 기록에 남긴다. 이 기록은 흐름 ①의 문장 생성에서 맥락(history)으로 쓰인다.

요청:
```jsonc
{ "action": "doctorUtterance", "sessionId": "abc123", "text": "어디가 아프세요?" }
```

응답(`type: "transcript"`):
```jsonc
{ "type": "transcript", "text": "어디가 아프세요?" }
```

- 저장은 best-effort다. 실패해도 자막 표시를 막지 않는다.
- `sessionId`가 없으면 저장은 생략하고 확인 응답만 온다.

---

## 에러

모든 실패는 공통 포맷으로 온다.

```jsonc
{
  "type": "error",
  "stage": "recognize" | "sentence" | "speech" | "transcript",
  "message": "사람이 읽을 안내 문구",
  "retryable": true
}
```

| stage | 언제 | 프론트 권장 처리 |
|---|---|---|
| `recognize` | keypoints 누락/인식 실패/단어 없음 | 다시 동작하도록 안내, 재시도 |
| `sentence` | 변환할 단어 없음/문장 생성 실패 | 단어를 다시 모으게 안내 |
| `speech` | 음성 합성 실패 | 자막만 표시(참고: speech 응답의 audioUrl=null + needsConfirm=true로 오는 경우가 일반적) |
| `transcript` | 저장할 텍스트 없음 | 무시 가능 |

- `retryable: true`면 같은 요청을 다시 보내도 된다.

---

## 요약 (프론트 체크리스트)

- [ ] `sessionId`를 대화 시작 시 1개 생성해 모든 메시지에 동일하게 사용
- [ ] 수어: `recognize` 반복 → 문장 끝에서 `finalize` 한 번. 응답 `words`로 누적 확인
- [ ] `finalize` 응답의 `sentence`는 자막, `audioUrl`은 음성(있으면 재생), `needsConfirm`이면 "확인 필요"
- [ ] 음성: `IdentityPoolId`로 Cognito 게스트 자격증명 → Transcribe 직접 연결(ko-KR) → 자막 표시. 필요 시 `doctorUtterance`로 기록
- [ ] 모든 응답은 `type`으로 분기, `type:"error"`는 공통 처리

> 배포 전 개발용으로 로컬 Mock WebSocket 서버가 있다: [`../backend/mock/README.md`](../backend/mock/README.md). 이 계약대로 고정 응답을 돌려주므로 백엔드 배포를 기다리지 않고 프론트를 붙여볼 수 있다.
