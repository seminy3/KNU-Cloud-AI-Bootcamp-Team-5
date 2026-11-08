# 검증 결과

- TypeScript 정적 검사 통과 (`node node_modules/typescript/bin/tsc -b`)
- 실제 로컬 WebSocket 서버 왕복 테스트 통과 (`node mock/smoke.mjs`)
- 확인한 흐름은 recognize 두 번, 서버 누적 words, finalize, 버퍼 초기화 후 에러, doctorUtterance, 잘못된 좌표 에러입니다.
- 의존성 취약점 검사 0건 (설치 시점)
- Vitest 테스트 7개 작성. 실행 환경에서 esbuild 하위 프로세스 생성이 `spawn EPERM`으로 차단되어 테스트 실행은 완료하지 못했습니다.
- 프로덕션 빌드도 같은 실행 제한으로 완료하지 못했습니다.
- 브라우저 렌더링, 반응형 시각 검사, 실제 카메라·마이크, AWS 인증 및 추론은 검증하지 못했습니다.
- Figma의 이전 제작 결과를 바탕으로 화면을 구현했으며 아이콘은 임시 문자 아이콘입니다. 원본 에셋 반영과 시각 검수가 추가로 필요합니다.

일반 로컬 터미널에서 `npm run build`, `npm test`를 실행해 확인하세요. `npm run mock`을 실행한 상태에서 `node mock/smoke.mjs`로 실제 WebSocket 테스트를 반복할 수 있습니다.
