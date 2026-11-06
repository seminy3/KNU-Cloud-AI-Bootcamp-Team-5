module.exports = {
  testEnvironment: "node",
  roots: ["<rootDir>/test"],
  testMatch: ["**/*.test.ts"],
  // 레이어 번들링의 pip install 을 테스트 중 건너뛰게 하는 플래그 세팅
  setupFiles: ["<rootDir>/test/jest.setup.js"],
  transform: {
    "^.+\\.tsx?$": "ts-jest",
  },
};
