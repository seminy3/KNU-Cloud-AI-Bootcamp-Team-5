import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { MonitoringStack } from "../lib/monitoring-stack";

function synth(context: Record<string, string> = {}): Template {
  const app = new cdk.App({ context });
  const stack = new MonitoringStack(app, "MonTest", {
    env: { account: "379995600109", region: "ap-northeast-2" },
  });
  return Template.fromStack(stack);
}

describe("MonitoringStack", () => {
  test("CloudWatch 대시보드가 생성된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::CloudWatch::Dashboard", 1);
  });

  test("기본 Lambda에 에러 알람이 생긴다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::CloudWatch::Alarm", {
      Namespace: "AWS/Lambda",
      MetricName: "Errors",
      ComparisonOperator: "GreaterThanOrEqualToThreshold",
      Threshold: 1,
    });
  });

  test("context로 여러 Lambda 함수명을 받으면 알람도 그만큼 생긴다", () => {
    const t = synth({ lambdaNames: "fnA,fnB,fnC" });
    // 각 함수당 에러 알람 1개 → 3개
    t.resourceCountIs("AWS::CloudWatch::Alarm", 3);
  });

  test("AWS Budgets 월 예산이 생성된다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::Budgets::Budget", {
      Budget: Match.objectLike({
        BudgetType: "COST",
        TimeUnit: "MONTHLY",
      }),
    });
  });

  test("context budgetUsd가 예산 금액에 반영된다", () => {
    const t = synth({ budgetUsd: "25" });
    t.hasResourceProperties("AWS::Budgets::Budget", {
      Budget: Match.objectLike({
        BudgetLimit: { Amount: 25, Unit: "USD" },
      }),
    });
  });

  test("alertEmail이 있으면 예산 알림 구독이 생긴다", () => {
    const t = synth({ alertEmail: "team@example.com", budgetUsd: "10" });
    const budgets = t.findResources("AWS::Budgets::Budget");
    const json = JSON.stringify(budgets);
    expect(json).toContain("team@example.com");
    expect(json).toContain("FORECASTED");
  });
});
