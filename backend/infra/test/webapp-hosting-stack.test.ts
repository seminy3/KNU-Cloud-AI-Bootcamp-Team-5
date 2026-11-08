import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { WebappHostingStack } from "../lib/webapp-hosting-stack";

function synth(): Template {
  const app = new cdk.App();
  const stack = new WebappHostingStack(app, "WebappTest", {
    env: { account: "379995600109", region: "ap-northeast-2" },
  });
  return Template.fromStack(stack);
}

describe("WebappHostingStack", () => {
  test("정적 호스팅 S3 버킷이 퍼블릭 접근을 막는다", () => {
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

  test("CloudFront 배포가 생성된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::CloudFront::Distribution", 1);
  });

  test("HTTPS 리다이렉트 + 기본 루트가 index.html", () => {
    const t = synth();
    t.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        DefaultRootObject: "index.html",
        DefaultCacheBehavior: Match.objectLike({
          ViewerProtocolPolicy: "redirect-to-https",
        }),
      }),
    });
  });

  test("SPA fallback: 403/404를 index.html(200)로 돌린다", () => {
    const t = synth();
    t.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        CustomErrorResponses: Match.arrayWith([
          Match.objectLike({
            ErrorCode: 403,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
          }),
          Match.objectLike({
            ErrorCode: 404,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
          }),
        ]),
      }),
    });
  });

  test("OAC(Origin Access Control)가 생성된다", () => {
    const t = synth();
    t.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
  });

  test("접속 도메인과 배포 ID를 출력한다", () => {
    const t = synth();
    expect(Object.keys(t.findOutputs("DistributionDomainName")).length).toBe(1);
    expect(Object.keys(t.findOutputs("DistributionId")).length).toBe(1);
  });
});
