# S3 + CloudFront 배포 전달

## 선택한 방식

GitHub Actions에서 테스트와 빌드를 실행하고 dist를 다운로드 가능한 artifact로 제공합니다. 백엔드 담당자가 결과물을 받아 S3에 배포합니다. AWS 배포 권한이나 버킷 설정 없이 시작할 수 있습니다. 현재 워크플로는 S3에 직접 업로드하지 않습니다.

## 최초 설정

GitHub 저장소 Settings → Secrets and variables → Actions → Variables에 다음 값을 등록합니다.

- VITE_WS_URL — 실제 wss:// API Gateway 주소
- VITE_COGNITO_IDENTITY_POOL_ID — ap-northeast-2:... Identity Pool ID
- VITE_POSE_MODEL_URL — Pose task 모델 URL 또는 배포 파일 경로
- VITE_HAND_MODEL_URL — Hand task 모델 URL 또는 배포 파일 경로

이 값들은 브라우저에서 읽는 공개 설정입니다. AWS 장기 키나 토큰은 등록하지 않습니다. Cognito Identity Pool에는 게스트 인증 및 Transcribe Streaming 권한이 필요합니다.

Vite는 빌드 시 설정을 JavaScript에 넣습니다. 따라서 ID와 WebSocket URL을 먼저 받아 빌드하거나, 값을 받은 후 다시 빌드해야 합니다. S3에 .env를 올리는 것으로 설정이 변경되지 않습니다.

## 실행과 다운로드

frontend 브랜치의 frontend 폴더 또는 워크플로 변경을 push하면 자동 실행합니다. PR에서는 테스트와 빌드만 수행합니다. 배포 artifact는 실제 WebSocket URL과 Identity Pool ID를 설정한 push 또는 수동 실행에서 생성합니다.

GitHub → Actions → Frontend build → 성공한 실행 → Artifacts → signmed-frontend-dist-커밋번호를 다운로드합니다. 압축을 풀면 index.html, assets, pcm-worklet.js, mediapipe 등이 있습니다. 압축 해제된 내용물을 S3 배포 루트에 올립니다. ZIP 파일 자체나 dist라는 상위 폴더를 올리는 방식은 사용하지 않습니다.

workflow_dispatch의 수동 실행 버튼은 기본 브랜치에 워크플로가 등록된 이후 사용할 수 있습니다. 그 전에는 frontend 브랜치 push로 실행합니다.

## CloudFront 담당자 확인

- HTTPS로 서비스하고 기본 루트 객체를 index.html로 설정합니다.
- React Router의 /roles, /session, /patient, /doctor 직접 접근과 새로고침을 지원해야 합니다. 해당 페이지 경로를 index.html로 rewrite하는 CloudFront Function을 권장합니다. 403/404 응답을 index.html과 200으로 대체하는 방식도 가능하지만, 누락된 모델·WASM·JSON까지 HTML로 바뀌지 않도록 주의합니다.
- index.html은 짧은 캐시 또는 재검증을 사용하고 배포 후 캐시를 갱신합니다. public의 파일은 해시 없는 이름이므로 필요 시 /* 무효화를 수행합니다.
- JavaScript, application/wasm, application/json의 Content-Type을 확인합니다.
- 현재 배포는 도메인 루트(/)를 전제로 합니다.
- 카메라 및 마이크는 HTTPS에서 실제 기기로 확인합니다.

## 기능 완료 여부

CI 성공은 코드 검사와 정적 빌드 성공을 의미합니다. 49포인트 INPUT_SPEC, 모델 파일, 실제 AWS 음성 및 수어 추론, 기기 간 메시지 전달, 브라우저 시각 검수는 별도 검증 대상입니다. 현재 제공된 API에는 기기 간 전달 기능이 없습니다.

S3 자동 업로드까지 확장하려면 팀원이 버킷명, CloudFront distribution ID, GitHub OIDC용 IAM 역할을 제공해야 합니다. 그 단계에서 승인된 브랜치만 배포하도록 CI/CD를 확장합니다.
