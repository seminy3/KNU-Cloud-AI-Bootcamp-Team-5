import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";

/**
 * 웹앱(React) 정적 호스팅 스택 — S3 + CloudFront.
 *
 * - 비공개 S3 버킷(퍼블릭 차단) + CloudFront OAC 로만 접근.
 * - HTTPS 강제(HTTP → HTTPS 리다이렉트).
 * - SPA 라우팅: 403/404 를 index.html(200)로 돌려 클라이언트 라우터가 처리.
 *
 * 프론트 빌드 산출물(예: dist/)은 이 스택이 올리지 않는다. 배포 후
 *   aws s3 sync <빌드폴더> s3://<BucketName> --delete
 *   aws cloudfront create-invalidation --distribution-id <id> --paths "/*"
 * 로 업로드한다(또는 CI 에서). 프론트 배포 방식은 role 4 와 협의.
 *
 * 음성 mp3 전송은 이 CloudFront 를 쓰지 않고 presigned S3 URL 을 그대로 쓴다
 * (Flow1BackendStack). 여기 CloudFront 는 웹앱 호스팅 전용이다.
 */
export class WebappHostingStack extends cdk.Stack {
  /** 프론트 자동 배포 역할(CicdOidcStack)이 업로드·캐시 무효화 권한을 받는 대상 */
  public readonly bucket: s3.IBucket;
  public readonly distribution: cloudfront.IDistribution;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const siteBucket = new s3.Bucket(this, "WebappBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const distribution = new cloudfront.Distribution(this, "WebappDistribution", {
      defaultBehavior: {
        // S3BucketOrigin.withOriginAccessControl: 버킷을 비공개로 두고 OAC 로만 접근
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: "index.html",
      // SPA: 라우팅 경로(파일 없음)를 index.html 로 돌려 클라이언트 라우터가 처리
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.minutes(5),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.minutes(5),
        },
      ],
      comment: "SignMed 웹앱 호스팅",
    });

    this.bucket = siteBucket;
    this.distribution = distribution;

    new cdk.CfnOutput(this, "WebappBucketName", {
      value: siteBucket.bucketName,
      description: "프론트 빌드 산출물을 s3 sync 로 올릴 버킷",
    });
    new cdk.CfnOutput(this, "DistributionDomainName", {
      value: distribution.distributionDomainName,
      description: "웹앱 접속 도메인 (https://<이 값>)",
    });
    new cdk.CfnOutput(this, "DistributionId", {
      value: distribution.distributionId,
      description: "배포 후 캐시 무효화에 쓰는 CloudFront Distribution ID",
    });
  }
}
