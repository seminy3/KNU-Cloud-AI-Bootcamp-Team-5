import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";

/**
 * SageMaker 엔드포인트 sign-word-model 연결 시험용 스택.
 *
 * - 테스트 Lambda (Python 3.12, 타임아웃 30초)
 * - 최소 권한 실행 역할: sagemaker:InvokeEndpoint 를 해당 엔드포인트 ARN 으로만 허용
 *
 * Lambda 코드는 backend/lambda/endpoint_test 를 자산으로 참조한다
 * (Task 1 에서 만든 handler.py).
 */
export class EndpointTestStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // context 로 바꿀 수 있게 하되 기본은 sign-word-model
    const endpointName =
      (this.node.tryGetContext("endpointName") as string) ?? "sign-word-model";

    // 특정 엔드포인트 ARN 으로만 호출 권한을 한정한다.
    // account/region 은 스택 환경에서 가져온다(서울 고정은 bin/app.ts 에서).
    const endpointArn = cdk.Stack.of(this).formatArn({
      service: "sagemaker",
      resource: "endpoint",
      resourceName: endpointName,
    });

    // 로그 그룹을 명시적으로 만들어 보존 기간을 1주로 둔다.
    // (deprecated 된 logRetention 옵션은 추가 custom-resource Lambda 를 만들어
    //  리소스가 늘어나므로 쓰지 않는다.)
    const logGroup = new logs.LogGroup(this, "EndpointTestLogGroup", {
      logGroupName: "/aws/lambda/sign-word-model-test",
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const fn = new lambda.Function(this, "EndpointTestFunction", {
      functionName: "sign-word-model-test",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.lambda_handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "..", "..", "lambda", "endpoint_test")
      ),
      // 콜드 스타트가 길 수 있어 30초. ENDPOINT_TEST.md 와 맞춤.
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        ENDPOINT_NAME: endpointName,
      },
      logGroup,
      description:
        "SageMaker sign-word-model 엔드포인트를 InvokeEndpoint 로 호출해 응답/지연을 확인하는 시험용 함수",
    });

    // 최소 권한: 이 엔드포인트에 대한 InvokeEndpoint 만.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "InvokeSignWordModelEndpoint",
        effect: iam.Effect.ALLOW,
        actions: ["sagemaker:InvokeEndpoint"],
        resources: [endpointArn],
      })
    );

    new cdk.CfnOutput(this, "FunctionName", {
      value: fn.functionName,
      description: "콘솔 테스트 탭에서 실행할 Lambda 함수 이름",
    });
    new cdk.CfnOutput(this, "EndpointArn", {
      value: endpointArn,
      description: "InvokeEndpoint 권한이 한정된 SageMaker 엔드포인트 ARN",
    });
  }
}
