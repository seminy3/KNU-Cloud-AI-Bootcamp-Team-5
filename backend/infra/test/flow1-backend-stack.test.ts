import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { Flow1BackendStack } from "../lib/flow1-backend-stack";

function synth(): Template {
  const app = new cdk.App({
    context: { endpointName: "sign-word-model", skipLayerInstall: true },
  });
  const stack = new Flow1BackendStack(app, "Flow1Test", {
    env: { account: "379995600109", region: "ap-northeast-2" },
  });
  return Template.fromStack(stack);
}

describe("Flow1BackendStack", () => {
  test("WebSocket API가 action 기반 라우팅으로 생성된다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::ApiGatewayV2::Api", {
      ProtocolType: "WEBSOCKET",
      RouteSelectionExpression: "$request.body.action",
    });
  });

  test("recognize와 finalize 라우트가 있다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "recognize" });
    t.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "finalize" });
  });

  test("Lambda 5개가 Python 3.12로 생성된다", () => {
    const t = synth();
    // recognize + finalize + doctorUtterance + join + $disconnect
    const fns = t.findResources("AWS::Lambda::Function", {
      Properties: { Runtime: "python3.12" },
    });
    expect(Object.keys(fns).length).toBe(5);
  });

  test("WebSocket 스테이지에 호출 제한이 있다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::ApiGatewayV2::Stage", {
      DefaultRouteSettings: { ThrottlingRateLimit: 10, ThrottlingBurstLimit: 20 },
    });
  });

  test("두 화면 연결용 join, $disconnect 라우트가 있다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "join" });
    t.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "$disconnect" });
  });

  test("doctorUtterance 라우트가 있다 (흐름 ②)", () => {
    const t = synth();
    t.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "doctorUtterance",
    });
  });

  test("Cognito Identity Pool이 비인증 접속을 허용한다 (흐름 ②)", () => {
    const t = synth();
    t.resourceCountIs("AWS::Cognito::IdentityPool", 1);
    t.hasResourceProperties("AWS::Cognito::IdentityPool", {
      AllowUnauthenticatedIdentities: true,
    });
  });

  test("게스트 역할은 Transcribe 스트리밍 권한을 가진다 (흐름 ②)", () => {
    const t = synth();
    const policies = t.findResources("AWS::IAM::Policy");
    const json = JSON.stringify(policies);
    expect(json).toContain("transcribe:StartStreamTranscriptionWebSocket");
  });

  test("recognize는 SageMaker InvokeEndpoint를 엔드포인트 ARN으로만 허용", () => {
    const t = synth();
    t.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "sagemaker:InvokeEndpoint",
            Effect: "Allow",
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

  test("finalize는 Bedrock InvokeModel 권한을 가진다", () => {
    const t = synth();
    const policies = t.findResources("AWS::IAM::Policy");
    const json = JSON.stringify(policies);
    expect(json).toContain("bedrock:InvokeModel");
    expect(json).toContain("polly:SynthesizeSpeech");
  });

  test("S3 버킷과 DynamoDB 테이블이 생성된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::S3::Bucket", 1);
    t.resourceCountIs("AWS::DynamoDB::Table", 1);
    t.hasResourceProperties("AWS::DynamoDB::Table", {
      KeySchema: Match.arrayWith([
        { AttributeName: "sessionId", KeyType: "HASH" },
        { AttributeName: "ts", KeyType: "RANGE" },
      ]),
      BillingMode: "PAY_PER_REQUEST",
    });
  });

  test("S3 버킷은 퍼블릭 접근을 막는다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  test("finalize 환경변수에 버킷/테이블/보이스가 설정된다", () => {
    const t = synth();
    const fns = t.findResources("AWS::Lambda::Function", {
      Properties: {
        Environment: { Variables: { POLLY_VOICE: "Seoyeon" } },
      },
    });
    expect(Object.keys(fns).length).toBe(1);
  });

  test("recognize Lambda에 ENDPOINT_NAME과 TABLE_NAME이 모두 설정된다", () => {
    const t = synth();
    // recognize 는 단어 버퍼를 DynamoDB에 쌓으므로 TABLE_NAME 이 있어야 한다.
    // (이게 없어서 버퍼 저장이 조용히 실패하고 finalize가 빈 버퍼를 읽던 버그 회귀 방지)
    const fns = t.findResources("AWS::Lambda::Function", {
      Properties: {
        Environment: {
          Variables: Match.objectLike({ ENDPOINT_NAME: "sign-word-model" }),
        },
      },
    });
    // ENDPOINT_NAME 을 가진 함수는 recognize 하나. 그 함수에 TABLE_NAME 도 있어야 한다.
    const recognize = Object.values(fns).find(
      (f: any) => f.Properties.Environment.Variables.ENDPOINT_NAME !== undefined
    ) as any;
    expect(recognize).toBeDefined();
    expect(recognize.Properties.Environment.Variables.TABLE_NAME).toBeDefined();
  });

  test("DynamoDB 테이블에 세 Lambda(recognize/finalize/doctor)가 읽기·쓰기 권한을 가진다", () => {
    const t = synth();
    // dynamodb:PutItem 등을 허용하는 IAM 정책 statement 가 3개 함수 역할에 있어야 한다.
    const policies = t.findResources("AWS::IAM::Policy");
    const json = JSON.stringify(policies);
    const putItemCount = (json.match(/dynamodb:PutItem/g) || []).length;
    // grantReadWriteData 는 함수마다 정책을 만들므로 최소 3개 함수분
    expect(putItemCount).toBeGreaterThanOrEqual(3);
  });

  test("anthropic SDK 레이어가 생성되고 Python 3.12와 호환된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::Lambda::LayerVersion", 1);
    t.hasResourceProperties("AWS::Lambda::LayerVersion", {
      CompatibleRuntimes: Match.arrayWith(["python3.12"]),
    });
  });

  test("finalize Lambda에 레이어가 연결된다", () => {
    const t = synth();
    // POLLY_VOICE 로 finalize 함수를 특정하고, Layers 가 비어있지 않은지 확인
    const fns = t.findResources("AWS::Lambda::Function", {
      Properties: {
        Environment: { Variables: { POLLY_VOICE: "Seoyeon" } },
      },
    });
    const finalize = Object.values(fns)[0] as any;
    expect(Array.isArray(finalize.Properties.Layers)).toBe(true);
    expect(finalize.Properties.Layers.length).toBe(1);
  });
});
