import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { CicdOidcStack } from "../lib/cicd-oidc-stack";

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

  test("배포 역할 신뢰정책이 기본 repo로 한정된다", () => {
    const t = synth();
    const roles = t.findResources("AWS::IAM::Role");
    const json = JSON.stringify(roles);
    expect(json).toContain("repo:seminy3/KNU-Cloud-AI-Bootcamp-Team-5:*");
    expect(json).toContain("sts.amazonaws.com");
  });

  test("context로 repo를 바꿀 수 있다", () => {
    const t = synth({ githubRepo: "other/repo" });
    const json = JSON.stringify(t.findResources("AWS::IAM::Role"));
    expect(json).toContain("repo:other/repo:*");
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
});
