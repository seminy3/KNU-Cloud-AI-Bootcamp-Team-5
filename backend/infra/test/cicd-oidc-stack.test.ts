import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { CicdOidcStack } from "../lib/cicd-oidc-stack";
import { WebappHostingStack } from "../lib/webapp-hosting-stack";

function synth(context: Record<string, string> = {}): Template {
  const app = new cdk.App({ context });
  const stack = new CicdOidcStack(app, "OidcTest", {
    env: { account: "379995600109", region: "ap-northeast-2" },
  });
  return Template.fromStack(stack);
}

describe("CicdOidcStack", () => {
  test("GitHub OIDC Provider가 생성된다", () => {
    const t = synth();
    // CDK는 OIDC Provider를 custom resource로 만든다
    t.hasResourceProperties("Custom::AWSCDKOpenIdConnectProvider", {
      Url: "https://token.actions.githubusercontent.com",
      ClientIDList: ["sts.amazonaws.com"],
    });
  });

  test("배포 역할 신뢰정책이 기본 repo의 production 환경으로 한정된다", () => {
    const t = synth();
    const roles = t.findResources("AWS::IAM::Role");
    const json = JSON.stringify(roles);
    expect(json).toContain("repo:seminy3/KNU-Cloud-AI-Bootcamp-Team-5:environment:production");
    expect(json).not.toContain("KNU-Cloud-AI-Bootcamp-Team-5:*");
    expect(json).toContain("sts.amazonaws.com");
  });

  test("context로 repo를 바꿀 수 있다", () => {
    const t = synth({ githubRepo: "other/repo" });
    const json = JSON.stringify(t.findResources("AWS::IAM::Role"));
    expect(json).toContain("repo:other/repo:environment:production");
    expect(json).not.toContain("seminy3/KNU-Cloud-AI-Bootcamp-Team-5");
  });

  test("배포 역할이 CDK 부트스트랩 역할을 맡을 수 있다", () => {
    const t = synth();
    const json = JSON.stringify(t.findResources("AWS::IAM::Policy"));
    expect(json).toContain("sts:AssumeRole");
    expect(json).toContain(":role/cdk-*");
  });

  test("DeployRoleArn을 출력한다", () => {
    const t = synth();
    const outputs = t.findOutputs("DeployRoleArn");
    expect(Object.keys(outputs).length).toBe(1);
  });

  test("웹앱을 주면 main 브랜치 전용 프론트 배포 역할을 만든다", () => {
    const app = new cdk.App();
    const env = { account: "379995600109", region: "ap-northeast-2" };
    const web = new WebappHostingStack(app, "WebTest", { env });
    const stack = new CicdOidcStack(app, "OidcWithWeb", {
      env, webappBucket: web.bucket, webappDistribution: web.distribution,
    });
    const t = Template.fromStack(stack);
    const roles = JSON.stringify(t.findResources("AWS::IAM::Role"));
    expect(roles).toContain("repo:seminy3/KNU-Cloud-AI-Bootcamp-Team-5:ref:refs/heads/main");
    const policies = JSON.stringify(t.findResources("AWS::IAM::Policy"));
    expect(policies).toContain("cloudfront:CreateInvalidation");
    expect(policies).toContain("s3:PutObject");
    expect(Object.keys(t.findOutputs("FrontendDeployRoleArn")).length).toBe(1);
  });
});
