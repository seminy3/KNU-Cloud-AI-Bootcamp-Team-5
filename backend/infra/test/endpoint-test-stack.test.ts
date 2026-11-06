import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { EndpointTestStack } from "../lib/endpoint-test-stack";

function synth(): Template {
  const app = new cdk.App({ context: { endpointName: "sign-word-model" } });
  const stack = new EndpointTestStack(app, "TestStack", {
    env: { account: "379995600109", region: "ap-northeast-2" },
  });
  return Template.fromStack(stack);
}

describe("EndpointTestStack", () => {
  test("Python 3.12 Lambda가 30초 타임아웃으로 생성된다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::Lambda::Function", {
      Runtime: "python3.12",
      Timeout: 30,
      Handler: "handler.lambda_handler",
      Environment: {
        Variables: { ENDPOINT_NAME: "sign-word-model" },
      },
    });
  });

  test("IAM 정책이 sagemaker:InvokeEndpoint를 특정 엔드포인트 ARN으로만 한정한다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "sagemaker:InvokeEndpoint",
            Effect: "Allow",
            // ARN 은 Fn::Join 으로 조각나 있다(Partition 이 Ref).
            // 조각 배열 안에 엔드포인트를 가리키는 꼬리 문자열이 있는지 확인한다.
            Resource: {
              "Fn::Join": [
                "",
                Match.arrayWith([
                  ":sagemaker:ap-northeast-2:379995600109:endpoint/sign-word-model",
                ]),
              ],
            },
          }),
        ]),
      },
    });
  });

  test("와일드카드(*) 리소스로 InvokeEndpoint 권한을 주지 않는다", () => {
    const t = synth();
    const policies = t.findResources("AWS::IAM::Policy");
    // InvokeEndpoint 를 허용하는 statement 의 Resource 가 "*" 가 아니어야 한다
    for (const policy of Object.values(policies)) {
      const statements = policy.Properties.PolicyDocument.Statement as any[];
      for (const s of statements) {
        const actions = Array.isArray(s.Action) ? s.Action : [s.Action];
        if (actions.includes("sagemaker:InvokeEndpoint")) {
          expect(s.Resource).not.toBe("*");
        }
      }
    }
  });

  test("Lambda 함수가 정확히 하나 생성된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::Lambda::Function", 1);
  });
});
