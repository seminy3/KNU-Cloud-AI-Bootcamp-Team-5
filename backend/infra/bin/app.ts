#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { EndpointTestStack } from "../lib/endpoint-test-stack";
import { Flow1BackendStack } from "../lib/flow1-backend-stack";
import { CicdOidcStack } from "../lib/cicd-oidc-stack";
import { MonitoringStack } from "../lib/monitoring-stack";
import { WebappHostingStack } from "../lib/webapp-hosting-stack";

const app = new cdk.App();

// 리전은 서울 고정. 계정 ID는 배포 시점의 자격증명에서 자동으로 채워진다
// (CDK_DEFAULT_ACCOUNT). 자격증명이 없으면 synth 시 account가 미지정이어도
// 템플릿 생성에는 문제가 없다.
const region = "ap-northeast-2";
const account = process.env.CDK_DEFAULT_ACCOUNT;

new EndpointTestStack(app, "SignMedEndpointTestStack", {
  env: { account, region },
  description:
    "SageMaker 엔드포인트 sign-word-model 연결 시험용 Lambda와 최소 권한 역할",
});

new Flow1BackendStack(app, "SignMedFlow1BackendStack", {
  env: { account, region },
  description:
    "흐름 ①·② 백엔드: WebSocket API + recognize/finalize/doctorUtterance Lambda + S3 + DynamoDB + Cognito",
});

new MonitoringStack(app, "SignMedMonitoringStack", {
  env: { account, region },
  description:
    "모니터링: CloudWatch 대시보드·알람 + AWS Budgets 월 예산 경보",
});

const webapp = new WebappHostingStack(app, "SignMedWebappHostingStack", {
  env: { account, region },
  description:
    "웹앱(React) 정적 호스팅: S3 + CloudFront(OAC, HTTPS, SPA fallback)",
});

new CicdOidcStack(app, "SignMedCicdOidcStack", {
  env: { account, region },
  // 프론트 자동 배포 역할이 웹앱 버킷·배포에 권한을 받는다
  webappBucket: webapp.bucket,
  webappDistribution: webapp.distribution,
  description:
    "CI/CD: GitHub Actions OIDC Provider + cdk deploy용 배포 역할",
});
