import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";

/**
 * CI/CD용 GitHub OIDC 연동 스택.
 *
 * GitHub Actions 가 액세스 키 없이 AWS 에 연결하도록 OIDC Identity Provider 와
 * 배포용 IAM 역할을 만든다(아키텍처 문서 방침: 키는 GitHub 에 올리지 않는다).
 *
 * 배포 후 역할 ARN(출력 DeployRoleArn)을 GitHub 저장소 시크릿
 * AWS_DEPLOY_ROLE_ARN 에 넣으면 .github/workflows/ci.yml 의 deploy job 이 동작한다.
 *
 * 신뢰 범위는 repo(기본 seminy3/KNU-Cloud-AI-Bootcamp-Team-5)로 한정한다.
 * context 로 바꿀 수 있다: --context githubRepo=owner/repo
 */
export interface CicdOidcStackProps extends cdk.StackProps {
  /** owner/repo 형식 (기본 seminy3/KNU-Cloud-AI-Bootcamp-Team-5) */
  readonly githubRepo?: string;
  /** 신뢰할 브랜치/ref 조건의 subject. 기본은 모든 ref (repo:owner/repo:*) */
  readonly subjectClaim?: string;
}

export class CicdOidcStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: CicdOidcStackProps) {
    super(scope, id, props);

    const githubRepo =
      props?.githubRepo ??
      (this.node.tryGetContext("githubRepo") as string) ??
      "seminy3/KNU-Cloud-AI-Bootcamp-Team-5";
    const subject = props?.subjectClaim ?? `repo:${githubRepo}:*`;

    // GitHub Actions OIDC Provider.
    const provider = new iam.OpenIdConnectProvider(this, "GitHubOidcProvider", {
      url: "https://token.actions.githubusercontent.com",
      clientIds: ["sts.amazonaws.com"],
    });

    // Actions 워크플로가 AssumeRoleWithWebIdentity 로 맡을 배포 역할.
    // sub 가 이 repo 의 것이고 aud 가 sts.amazonaws.com 일 때만 허용.
    const deployRole = new iam.Role(this, "GitHubDeployRole", {
      roleName: "signmed-github-deploy",
      assumedBy: new iam.WebIdentityPrincipal(
        provider.openIdConnectProviderArn,
        {
          StringEquals: {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          },
          StringLike: {
            "token.actions.githubusercontent.com:sub": subject,
          },
        }
      ),
      // IAM Role description 은 ASCII 만 허용(CloudFormation 제약)되어 영문으로 둔다.
      description: "GitHub Actions OIDC deploy role (cdk deploy)",
      maxSessionDuration: cdk.Duration.hours(1),
    });

    // CDK 부트스트랩이 만든 역할을 맡을 수 있게 한다(cdk deploy 가 이 방식으로 동작).
    // 최소화하려면 특정 서비스 권한으로 좁힐 수 있으나, CDK 배포는 부트스트랩
    // 역할 체인에 의존하므로 sts:AssumeRole(cdk-*)과 CloudFormation 권한을 준다.
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AssumeCdkBootstrapRoles",
        actions: ["sts:AssumeRole"],
        resources: [`arn:aws:iam::${this.account}:role/cdk-*`],
      })
    );
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "CloudFormationAccess",
        actions: [
          "cloudformation:*",
        ],
        resources: ["*"],
      })
    );

    new cdk.CfnOutput(this, "DeployRoleArn", {
      value: deployRole.roleArn,
      description:
        "GitHub 저장소 시크릿 AWS_DEPLOY_ROLE_ARN 에 넣을 배포 역할 ARN",
    });
    new cdk.CfnOutput(this, "OidcProviderArn", {
      value: provider.openIdConnectProviderArn,
    });
  }
}
