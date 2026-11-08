import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";

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
  /** 신뢰할 subject. 기본은 production 환경의 job만 (repo:owner/repo:environment:production) */
  readonly subjectClaim?: string;
  /** 주면 main 브랜치 push에서만 쓸 수 있는 프론트 배포 역할(업로드·캐시 무효화만)을 만든다 */
  readonly webappBucket?: s3.IBucket;
  readonly webappDistribution?: cloudfront.IDistribution;
}

export class CicdOidcStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: CicdOidcStackProps) {
    super(scope, id, props);

    const githubRepo =
      props?.githubRepo ??
      (this.node.tryGetContext("githubRepo") as string) ??
      "seminy3/KNU-Cloud-AI-Bootcamp-Team-5";
    // GitHub OIDC 토큰의 sub 앞부분. 이 저장소는 이름 대신 바뀌지 않는 ID를 함께 쓰는 형식
    // (repo:owner@ownerId/name@repoId)으로 보내므로 두 형식을 모두 받는다.
    // ID는 `gh api repos/<repo>/actions/oidc/customization/sub`의 sub_claim_prefix 에서 확인한다.
    const defaultRepo = "seminy3/KNU-Cloud-AI-Bootcamp-Team-5";
    const immutablePrefix =
      (this.node.tryGetContext("githubSubPrefix") as string | undefined) ??
      (githubRepo === defaultRepo
        ? "repo:seminy3@234939058/KNU-Cloud-AI-Bootcamp-Team-5@1411516550"
        : undefined);
    const subPrefixes = [`repo:${githubRepo}`, ...(immutablePrefix ? [immutablePrefix] : [])];
    // 모든 브랜치(repo:...:*)를 허용하면 아무 브랜치의 워크플로가 배포 권한을 얻으므로,
    // ci.yml deploy job이 쓰는 environment: production 으로 좁힌다
    const subject = props?.subjectClaim
      ? [props.subjectClaim]
      : subPrefixes.map((p) => `${p}:environment:production`);

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
    if (props?.webappBucket && props?.webappDistribution) {
      // 프론트 자동 배포(frontend-build.yml): main 브랜치 push에서만, 웹앱 버킷과 캐시 무효화만 허용
      const frontendRole = new iam.Role(this, "GitHubFrontendDeployRole", {
        roleName: "signmed-github-frontend-deploy",
        assumedBy: new iam.WebIdentityPrincipal(provider.openIdConnectProviderArn, {
          StringEquals: {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": subPrefixes.map((p) => `${p}:ref:refs/heads/main`),
          },
        }),
        description: "GitHub Actions OIDC frontend deploy (S3 sync + CloudFront invalidation)",
        maxSessionDuration: cdk.Duration.hours(1),
      });
      props.webappBucket.grantReadWrite(frontendRole);
      props.webappBucket.grantDelete(frontendRole);
      frontendRole.addToPolicy(
        new iam.PolicyStatement({
          sid: "InvalidateWebapp",
          actions: ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
          resources: [
            `arn:aws:cloudfront::${this.account}:distribution/${props.webappDistribution.distributionId}`,
          ],
        })
      );
      new cdk.CfnOutput(this, "FrontendDeployRoleArn", {
        value: frontendRole.roleArn,
        description: "GitHub 저장소 변수 FRONTEND_DEPLOY_ROLE_ARN 에 넣을 역할 ARN",
      });
    }

    new cdk.CfnOutput(this, "OidcProviderArn", {
      value: provider.openIdConnectProviderArn,
    });
  }
}
