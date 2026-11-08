import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as budgets from "aws-cdk-lib/aws-budgets";

/**
 * 모니터링 스택 — CloudWatch 대시보드·알람 + AWS Budgets 월 예산 경보.
 *
 * 흐름 ①·② Lambda(recognize/finalize/doctorUtterance)의 에러·지연을 지켜보고,
 * 월 예산을 넘으면 이메일로 알린다(아키텍처 문서: AWS Budgets 로 월 예산 알림).
 *
 * 함수 이름은 Flow1BackendStack 의 논리적 이름이 아니라 실제 함수명을 모르면
 * 메트릭을 못 거니, 알람은 "함수명 리스트" context 로 받는다. 기본은 흐름 Lambda 들.
 * context:
 *   --context lambdaNames="fnA,fnB"   (쉼표 구분 실제 함수명)
 *   --context budgetUsd=20            (월 예산 USD, 기본 10)
 *   --context alertEmail=you@ex.com   (예산 경보 수신 이메일)
 */
export interface MonitoringStackProps extends cdk.StackProps {
  readonly lambdaNames?: string[];
  readonly budgetUsd?: number;
  readonly alertEmail?: string;
}

export class MonitoringStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: MonitoringStackProps) {
    super(scope, id, props);

    const namesCtx = this.node.tryGetContext("lambdaNames") as string | undefined;
    const lambdaNames =
      props?.lambdaNames ??
      (namesCtx ? namesCtx.split(",").map((s) => s.trim()).filter(Boolean) : undefined) ??
      ["sign-word-model-test"]; // 기본: 적어도 엔드포인트 시험 Lambda. 배포 후 실제 함수명으로 교체 권장.

    const budgetCtx = this.node.tryGetContext("budgetUsd");
    const budgetUsd =
      props?.budgetUsd ?? (budgetCtx != null ? Number(budgetCtx) : 10);
    const alertEmail =
      props?.alertEmail ?? (this.node.tryGetContext("alertEmail") as string | undefined);

    const dashboard = new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: "signmed-backend",
    });

    // Lambda 별 에러·호출·지연 메트릭과 에러 알람
    for (const fnName of lambdaNames) {
      const dims = { FunctionName: fnName };
      const errors = new cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Errors",
        dimensionsMap: dims,
        statistic: "Sum",
        period: cdk.Duration.minutes(5),
      });
      const duration = new cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Duration",
        dimensionsMap: dims,
        statistic: "p95",
        period: cdk.Duration.minutes(5),
      });
      const invocations = new cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Invocations",
        dimensionsMap: dims,
        statistic: "Sum",
        period: cdk.Duration.minutes(5),
      });

      new cloudwatch.Alarm(this, `Errors-${sanitize(fnName)}`, {
        metric: errors,
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator:
          cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        alarmDescription: `${fnName} 에러 발생`,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });

      dashboard.addWidgets(
        new cloudwatch.GraphWidget({
          title: `${fnName} — 호출/에러`,
          left: [invocations, errors],
          width: 12,
        }),
        new cloudwatch.GraphWidget({
          title: `${fnName} — 지연(p95)`,
          left: [duration],
          width: 12,
        })
      );
    }

    // AWS Budgets 월 예산 경보. 이메일이 있으면 알림 구독을 건다.
    const subscribers = alertEmail
      ? [
          {
            subscriptionType: "EMAIL",
            address: alertEmail,
          },
        ]
      : undefined;

    new budgets.CfnBudget(this, "MonthlyBudget", {
      budget: {
        budgetName: "signmed-monthly",
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: budgetUsd, unit: "USD" },
      },
      notificationsWithSubscribers: subscribers
        ? [
            {
              notification: {
                notificationType: "ACTUAL",
                comparisonOperator: "GREATER_THAN",
                threshold: 80, // 예산의 80% 도달 시
                thresholdType: "PERCENTAGE",
              },
              subscribers,
            },
            {
              notification: {
                notificationType: "FORECASTED",
                comparisonOperator: "GREATER_THAN",
                threshold: 100, // 예상 지출이 예산 초과
                thresholdType: "PERCENTAGE",
              },
              subscribers,
            },
          ]
        : undefined,
    });

    new cdk.CfnOutput(this, "DashboardName", { value: "signmed-backend" });
  }
}

function sanitize(name: string): string {
  return name.replace(/[^A-Za-z0-9]/g, "");
}
