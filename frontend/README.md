# SignMed AI

React · TypeScript · Vite 기반의 한국어 통역 프론트엔드입니다.

## 실행

Node.js 22.12 이상을 사용합니다.

```sh
npm install
cp .env.example .env.local
npm run mock
# 별도 터미널
npm run dev
```

Windows에서는 `Copy-Item .env.example .env.local`을 사용합니다.
브라우저에서 http://localhost:5173 을 열고 역할 선택 → 새 대화 만들기 → 통역 화면 열기로 이동합니다.

```sh
npm run build
npm test
```

## 구현 구조

- `src/main.tsx` — 다섯 페이지, React Router, 자막 및 음성 재생
- `src/SessionContext.tsx` — 세션 ID 유지, 서버 단어 목록, 요청 잠금, 대화 기록
- `src/services/websocket.ts` — 재연결, 계약 검증, 전송 크기 검사
- `src/Camera.tsx` — 카메라, MediaPipe 포즈·손 추적, 캔버스, 30 유효 프레임 수집
- `src/services/keypointNormalizer.ts` — 명시적 모델 입력 매핑과 픽셀 기반 어깨 정규화
- `src/services/transcribe.ts` 및 `public/pcm-worklet.js` — Cognito 임시 자격증명, 한국어 Transcribe, 100ms 단위 PCM16 오디오
- `src/services/peer.ts` — 상대방 메시지 전달 인터페이스, 선택적인 같은 브라우저 탭 Mock
- `mock/server.mjs` — 제공된 문서에서 새로 작성한 계약 확인용 서버

## 현재 제한 및 설정

기존 Python Mock 서버 소스는 제공되지 않아 Node.js Mock을 새로 작성했습니다. 실제 모델을 호출하지 않으며 요청한 연결에만 응답합니다. 더미 음성 URL 재생 실패는 예상되는 동작입니다.

`VITE_WS_URL`을 배포된 WebSocket URL로 변경하면 기존 세 액션을 사용합니다. 대화 내용과 음성 URL은 메모리에만 유지하고, 세션 ID만 sessionStorage에 저장합니다. 요청 처리 여부가 불확실하면 자동 재전송을 차단합니다.

카메라 미리보기는 브라우저 권한으로 사용할 수 있습니다. 관절 검출에는 `VITE_POSE_MODEL_URL`, `VITE_HAND_MODEL_URL`에 공식 MediaPipe task 파일 경로가 필요합니다. WASM 파일은 public/mediapipe/wasm에 포함되어 있습니다.

학습용 49포인트 순서와 샘플링 규격이 제공되지 않아 실제 모델 인식 전송은 활성화되지 않습니다. `public/input-spec.json`에 ML 팀이 확인한 InputSpec을 설정해야 합니다. `src/services/keypointNormalizer.ts`의 InputSpec 타입을 따르며 points에는 정확히 49개 source/index 쌍이 필요합니다. shoulders는 포즈의 두 어깨 인덱스, sampleIntervalMs는 학습 규격에 맞는 간격입니다. approved는 검토 완료 후 true로 설정합니다. 현재 지원하는 누락 정책은 reject-frame이며, 학습 파이프라인의 정책이 다르면 변환기를 수정해야 합니다. 좌우 손 라벨과 미러링도 ML 팀의 검증이 필요합니다.

음성 인식은 `VITE_COGNITO_IDENTITY_POOL_ID` 및 게스트 역할의 Transcribe Streaming 권한이 필요합니다. 실제 AWS 인증 및 실시간 마이크 인식은 배포 설정으로 검증해야 합니다. 최종 발화는 사용자가 중지 후 확인 및 기록 버튼을 눌렀을 때만 저장합니다. 중간 자막은 저장하지 않습니다.

`VITE_MOCK_PEER=true`는 동일한 사이트를 연 같은 브라우저 탭 사이의 전달만 테스트합니다. 두 탭에서 같은 대화 코드를 입력하세요. 다른 브라우저나 기기 사이의 전달 및 상대방 접속 여부는 지원되지 않습니다.

## 백엔드·ML 협의

1. INPUT_SPEC.md 원본과 실제 입력 예제, 포인트 순서, 좌우 손 정의, 누락 처리, 샘플링 간격을 제공해야 합니다.
2. 세션 생성·참여·종료, 역할 등록, 상대방 연결 상태, 재접속 및 기기 간 문장 전달 계약이 필요합니다. 현재 존재하지 않는 액션은 호출하지 않습니다.
3. requestId와 중복 제거, 서버 단어 버퍼 조회 및 초기화 API가 필요합니다. 현 계약은 응답 유실 후 상태를 복구할 수 없습니다.
4. API Gateway WebSocket 프레임 한도는 32KB입니다. 제공된 34~64KB 좌표 JSON과 충돌할 수 있어 현재 32KB 초과는 차단합니다. 좌표 정밀도 합의 또는 서버가 지원하는 분할/재조립 계약이 필요합니다.
5. Cognito Identity Pool ID, 게스트 IAM 역할, 실제 WebSocket 배포 URL, 허용 origin, 음성 URL 만료 정보를 확인해야 합니다.

## 참고

- 디자인 https://www.figma.com/design/NQk3cpFBTqX9MZckChAYcN
- Transcribe 오디오 형식 https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html
- MediaPipe https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker/web_js
- WebSocket 한도 https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-execution-service-websocket-limits-table.html
